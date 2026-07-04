// viewer/src/interaction/editor.ts — the impure raycaster-selection edge (#442
// Chunk 2) plus the intent-capture controls + Scenario-YAML export (Chunk 3).
// Mounted only when `view --edit` injects an `#editor-context` blob (main.ts
// gates the call); dormant otherwise. Per the interaction/README's one hard
// rule, this module may import `three` but must NEVER import `affine.ts` or
// `anchors.ts` — it never re-derives geometry, only reads the already-built
// plane Groups the Python-owned transform placed.
import * as THREE from 'three';
import {
  initialIntent,
  toggleSelection,
  isSelected,
  setPriority,
  pinAtCurrent,
  unpin,
  setPinField,
  setOnCarts,
  addToDoorOrder,
  removeFromDoorOrder,
  moveInDoorOrder,
  toggleGroundObject,
  setCartModeOverride,
} from './selection.ts';
import { intentToScenarioYaml } from './export.ts';
import { byId } from '../dom.ts';
import { focusAwareHex } from './highlight.ts';
import type { Intent, EditorContext } from './intent-contract.ts';

export interface EditorHandle {
  getIntent(): Intent;
  // Detach every listener this mount added and remove the DOM it injected, so a
  // #445 serve re-mount (after a Calculate world-swap) cannot double-fire or
  // orphan the injected pin-fields div.
  dispose(): void;
}

export function mountEditor(opts: {
  groups: Record<string, THREE.Group>;
  renderer: THREE.WebGLRenderer;
  cam: THREE.Camera;
  ctx: EditorContext;
  // #445: seed a re-mount with the user's current intent (default: derived fresh
  // from ctx) so a Calculate swap preserves their selection/priorities/pins.
  initialIntent?: Intent;
}): EditorHandle {
  let intent = opts.initialIntent ?? initialIntent(opts.ctx);
  // #445: listeners on persistent elements (the canvas + HUD controls) are added
  // with this signal so dispose() detaches them cleanly on a re-mount. Listeners
  // on the door/palette list rows are self-cleaning — the next render clears
  // their container — so they are left off the signal.
  const ac = new AbortController();
  const sig = { signal: ac.signal };
  // The last-selected plane. The HUD controls (priority/pin/on_carts) always
  // act on this plane; deselecting it (or nothing being selected) disables them.
  let focusedId: string | null = null;
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const idByObject = new Map<THREE.Object3D, string>();
  // Per-mesh highlight targets: clone each emissive material so the editor's highlight
  // mutation is isolated (gear/pallet materials are SHARED across planes upstream — mutating
  // the shared instance would bleed the highlight onto every plane). Capture the original
  // emissive so a selected plane restores its real look instead of being forced to black.
  const targets: { id: string; mat: THREE.MeshStandardMaterial; orig: number }[] = [];
  for (const [id, g] of Object.entries(opts.groups)) {
    g.traverse((o) => {
      idByObject.set(o, id);
      const mesh = o as THREE.Mesh;
      const m = mesh.material;
      if (m && !Array.isArray(m) && (m as THREE.MeshStandardMaterial).emissive) {
        const cloned = (m as THREE.MeshStandardMaterial).clone();
        mesh.material = cloned;
        targets.push({ id, mat: cloned, orig: cloned.emissive.getHex() });
      }
    });
  }
  const el = opts.renderer.domElement;

  function pick(ev: PointerEvent): string | null {
    const r = el.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, opts.cam);
    const hits = ray.intersectObjects(Object.values(opts.groups), true);
    for (const h of hits) {
      const id = idByObject.get(h.object);
      if (id) return id;
    }
    return null;
  }

  // Only a near-stationary pointerdown->up counts as a selection click, so orbiting
  // the camera (a drag) never toggles selection.
  let downX = 0;
  let downY = 0;
  el.addEventListener(
    'pointerdown',
    (ev: PointerEvent) => {
      downX = ev.clientX;
      downY = ev.clientY;
    },
    sig,
  );
  el.addEventListener(
    'pointerup',
    (ev: PointerEvent) => {
      if (Math.hypot(ev.clientX - downX, ev.clientY - downY) > 6) return; // a drag, not a click
      const id = pick(ev);
      if (!id) return;
      // #904: click = FOCUS only. No membership toggle, so a plane's priority/pin/
      // cart-mode/door-rank are never dropped by focusing it. Fleet add/remove lives
      // on the #910 palette checkboxes. focusedId is the stable, membership-independent
      // handle #911's TransformControls gizmo will attach to.
      focusedId = id;
      applyHighlight(); // repaint the focus highlight onto the newly focused plane
      syncControls(); // control enablement reflects the focused plane (gated on isSelected)
      renderDoorOrder(); // the ＋rank button enablement reads focus + membership
    },
    sig,
  );

  function applyHighlight(): void {
    // Three states over the emissive channel (#904): the FOCUSED plane glows blue
    // (which plane the panel edits — independent of membership); an unfocused plane
    // shows its membership hue (original emissive if in the fleet, amber if excluded).
    for (const t of targets) {
      t.mat.emissive.setHex(focusAwareHex(isSelected(intent, t.id), t.id === focusedId, t.orig));
    }
  }

  function renderReadout(): void {
    const readout = document.getElementById('sel-readout');
    if (readout) readout.textContent = `selected: ${[...intent.selectedPlaneIds].sort().join(', ')}`;
  }

  // --- Intent-capture controls (priority / pin / on_carts) + export --------
  const prio = byId<HTMLInputElement>('prio');
  const pinToggle = byId<HTMLInputElement>('pin-toggle');
  const cartsToggle = byId<HTMLInputElement>('carts-toggle');
  const exportBtn = byId<HTMLButtonElement>('export');
  const rankAdd = byId<HTMLButtonElement>('rank-add');
  const doorList = byId<HTMLOListElement>('door-order-list');
  const paletteList = byId<HTMLUListElement>('palette-list');
  const cartMode = byId<HTMLSelectElement>('cart-mode');
  // #909 cart-mode override: the plane's base mode (from the catalog) and the
  // effective mode (override, else base). Aircraft catalog entries carry
  // movementMode; a palette-added plane is overridable too (the override is a
  // fleet-level exception, independent of placement).
  const baseMode = (id: string): string | undefined => opts.ctx.catalog?.[id]?.movementMode;
  const effMode = (id: string): string | undefined => intent.cartModeOverrides[id] ?? baseMode(id);
  // Show which wall — and where along it — the door is, from the editor-context
  // door edge (#907). The door is the front wall (y=0, the coordinate
  // convention); center_x_m ± width_m/2 is a 1-D span along it (pure display
  // arithmetic — no geometry transform, so ADR-0002 is untouched).
  const doorHint = byId<HTMLSpanElement>('door-hint');
  const door = opts.ctx.door;
  if (door) {
    const half = door.width_m / 2;
    doorHint.textContent = `door: front wall, x ${(door.center_x_m - half).toFixed(1)}–${(door.center_x_m + half).toFixed(1)} m`;
  }

  // Dynamic x/y/heading pin fields — `_HUD_EDIT` (viewer.py) does not carry
  // these, so build them here and hide them until the focused plane is pinned.
  const pinFields = document.createElement('div');
  pinFields.id = 'pin-fields';
  pinFields.hidden = true;
  function mkPinInput(label: string, field: 'x' | 'y' | 'heading'): HTMLInputElement {
    const wrap = document.createElement('label');
    wrap.textContent = `${label} `;
    const inp = document.createElement('input');
    inp.type = 'number';
    inp.step = '0.1';
    inp.addEventListener('input', () => {
      if (focusedId && inp.value !== '') intent = setPinField(intent, focusedId, field, Number(inp.value));
    });
    wrap.appendChild(inp);
    pinFields.appendChild(wrap);
    return inp;
  }
  const xIn = mkPinInput('x_m', 'x');
  const yIn = mkPinInput('y_m', 'y');
  const hIn = mkPinInput('heading_deg', 'heading');
  exportBtn.parentElement?.insertBefore(pinFields, exportBtn);

  function syncControls(): void {
    const id = focusedId;
    const active = id !== null && isSelected(intent, id);
    // A palette-added plane (#910) has no currentPose, so "pin at current pose"
    // is meaningless for it — keep priority (pose-free) live but disable the pin
    // toggle rather than leave it enabled-but-inert (pinAtCurrent no-ops there).
    const hasPose = id !== null && id in opts.ctx.currentPoses;
    prio.disabled = !active;
    pinToggle.disabled = !active || !hasPose;
    // #909 cart-mode override select: available for any focused selected aircraft
    // (the override is fleet-level, independent of a pin). Show the effective
    // mode; gate the radius-needing options (a non-always_cart mode requires a
    // turn radius) so the editor can't author a load-invalid override.
    if (!active || id === null || baseMode(id) === undefined) {
      cartMode.disabled = true;
    } else {
      cartMode.disabled = false;
      cartMode.value = effMode(id) ?? '';
      const canRadius = opts.ctx.catalog?.[id]?.hasTurnRadius ?? false;
      for (const opt of Array.from(cartMode.options)) {
        opt.disabled = opt.value !== 'always_cart' && !canRadius;
      }
    }
    if (!active || id === null) {
      prio.value = '';
      pinToggle.checked = false;
      pinFields.hidden = true;
      cartsToggle.disabled = true;
      cartsToggle.checked = false;
    } else {
      const p = intent.priorities[id];
      prio.value = p !== undefined ? String(p) : '';
      const mp = intent.mustPositions[id];
      pinToggle.checked = mp !== undefined;
      pinFields.hidden = mp === undefined;
      if (mp) {
        xIn.value = String(mp.x);
        yIn.value = String(mp.y);
        hIn.value = String(mp.heading);
        // The on-carts toggle reflects the EFFECTIVE mode: free only when
        // cart_eligible; a locked/overridden-locked mode forces on_carts.
        const mode = effMode(id);
        if (mode === 'cart_eligible') {
          cartsToggle.disabled = false;
          cartsToggle.checked = mp.onCarts;
        } else {
          cartsToggle.disabled = true;
          cartsToggle.checked = mode === 'always_cart';
        }
      } else {
        cartsToggle.disabled = true;
        cartsToggle.checked = false;
      }
    }
    exportBtn.disabled = intent.selectedPlaneIds.length === 0;
  }

  prio.addEventListener(
    'input',
    () => {
      if (!focusedId) return;
      intent = setPriority(intent, focusedId, prio.value === '' ? null : Math.max(0, Number(prio.value)));
    },
    sig,
  );
  pinToggle.addEventListener(
    'change',
    () => {
      if (!focusedId) return;
      intent = pinToggle.checked ? pinAtCurrent(intent, focusedId, opts.ctx) : unpin(intent, focusedId);
      syncControls();
    },
    sig,
  );
  cartsToggle.addEventListener(
    'change',
    () => {
      if (!focusedId) return;
      intent = setOnCarts(intent, focusedId, cartsToggle.checked);
    },
    sig,
  );
  cartMode.addEventListener(
    'change',
    () => {
      const id = focusedId;
      if (id === null) return;
      // Picking the plane's base mode clears the override (byte path unchanged);
      // any other mode sets it.
      const m = cartMode.value;
      intent = setCartModeOverride(intent, id, m === baseMode(id) ? null : m);
      // Keep a pin's on_carts consistent with the (possibly new) forced mode, so
      // the exported scenario is never a contradiction the loader would reject.
      if (intent.mustPositions[id]) {
        if (m === 'always_cart') intent = setOnCarts(intent, id, true);
        else if (m === 'always_own_gear') intent = setOnCarts(intent, id, false);
      }
      syncControls();
    },
    sig,
  );
  exportBtn.addEventListener(
    'click',
    () => {
      const yaml = intentToScenarioYaml(intent, opts.ctx);
      const blob = new Blob([yaml], { type: 'text/yaml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'scenario.edited.yaml';
      a.click();
      URL.revokeObjectURL(a.href);
    },
    sig,
  );

  // --- Door-proximity ranking list (#907) ---------------------------------
  // Rebuild the ordered list from intent.doorOrder. Each <li> is drag-reorderable
  // (HTML5 DnD → moveInDoorOrder by index) and carries a × to unrank. The "＋ rank
  // selected" button appends the focused plane; it enables only when the focused
  // plane is selected and not already ranked.
  function renderDoorOrder(): void {
    doorList.textContent = '';
    intent.doorOrder.forEach((id, index) => {
      const li = document.createElement('li');
      li.draggable = true;
      const label = document.createElement('span');
      label.textContent = id;
      const rm = document.createElement('button');
      rm.type = 'button';
      rm.textContent = '×';
      rm.title = `unrank ${id}`;
      rm.addEventListener('click', () => {
        intent = removeFromDoorOrder(intent, id);
        renderDoorOrder();
      });
      li.append(label, rm);
      li.addEventListener('dragstart', (ev) => ev.dataTransfer?.setData('text/plain', String(index)));
      li.addEventListener('dragover', (ev) => ev.preventDefault()); // permit a drop here
      li.addEventListener('drop', (ev) => {
        ev.preventDefault();
        const from = Number(ev.dataTransfer?.getData('text/plain'));
        if (Number.isInteger(from)) {
          intent = moveInDoorOrder(intent, from, index);
          renderDoorOrder();
        }
      });
      doorList.appendChild(li);
    });
    rankAdd.disabled = !(
      focusedId !== null && isSelected(intent, focusedId) && !intent.doorOrder.includes(focusedId)
    );
  }

  rankAdd.addEventListener(
    'click',
    () => {
      if (!focusedId) return;
      intent = addToDoorOrder(intent, focusedId);
      renderDoorOrder();
    },
    sig,
  );

  // --- Catalog palette (#910): add aircraft / movers from an empty hangar ----
  // Rebuild the list from the editor-context `catalog`. Each row is a checkbox:
  // an aircraft toggles the selection (→ fleet_in); a mover toggles a
  // ground_objects entry the solver places. A fixed_obstacle is shown disabled —
  // it needs an authored pose the offline editor can't produce (drag/serve).
  // Sorted by id for a stable list. An aircraft added here has no rendered Group
  // and no currentPose, so it can't be highlighted or pinned offline (only
  // soft-constrained — priority and door-order) — that "see the placed result"
  // gap closes with the serve epic.
  const KIND_LABEL: Record<string, string> = {
    aircraft: 'aircraft',
    placed_routed_mover: 'mover',
    fixed_obstacle: 'fixed',
  };
  function renderPalette(): void {
    const catalog = opts.ctx.catalog ?? {};
    paletteList.textContent = '';
    for (const id of Object.keys(catalog).sort()) {
      const { name, kind } = catalog[id];
      const isAircraft = kind === 'aircraft';
      const isMover = kind === 'placed_routed_mover';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = isAircraft ? isSelected(intent, id) : intent.groundObjectIds.includes(id);
      box.disabled = !(isAircraft || isMover); // a fixed obstacle needs a pose
      box.addEventListener('change', () => {
        if (isAircraft) {
          // #904: the palette toggles fleet membership ONLY; it must not move focus
          // (focus is the canvas click's job — moving it here would yank a future
          // gizmo target). The focused plane's controls still refresh below in case
          // its own membership was the thing toggled.
          intent = toggleSelection(intent, id);
          applyHighlight();
          renderReadout();
          syncControls();
          renderDoorOrder();
        } else if (isMover) {
          intent = toggleGroundObject(intent, id);
        }
        renderPalette();
      });
      const badge = document.createElement('span');
      badge.className = 'kind';
      badge.textContent = KIND_LABEL[kind] ?? kind;
      const label = document.createElement('label');
      label.append(box, document.createTextNode(` ${name} `), badge);
      const li = document.createElement('li');
      li.appendChild(label);
      paletteList.appendChild(li);
    }
  }

  applyHighlight();
  renderReadout();
  syncControls();
  renderDoorOrder();
  renderPalette();
  return {
    getIntent: () => intent,
    // #445: abort the persistent-element listeners and remove the injected
    // pin-fields div so a re-mount after a Calculate swap starts clean.
    dispose: () => {
      ac.abort();
      pinFields.remove();
    },
  };
}
