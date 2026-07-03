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
} from './selection.ts';
import { intentToScenarioYaml } from './export.ts';
import { byId } from '../dom.ts';
import type { Intent, EditorContext } from './intent-contract.ts';

export interface EditorHandle {
  getIntent(): Intent;
}

export function mountEditor(opts: {
  groups: Record<string, THREE.Group>;
  renderer: THREE.WebGLRenderer;
  cam: THREE.Camera;
  ctx: EditorContext;
}): EditorHandle {
  let intent = initialIntent(opts.ctx);
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
  el.addEventListener('pointerdown', (ev: PointerEvent) => {
    downX = ev.clientX;
    downY = ev.clientY;
  });
  el.addEventListener('pointerup', (ev: PointerEvent) => {
    if (Math.hypot(ev.clientX - downX, ev.clientY - downY) > 6) return; // a drag, not a click
    const id = pick(ev);
    if (!id) return;
    intent = toggleSelection(intent, id);
    focusedId = isSelected(intent, id) ? id : null;
    applyHighlight();
    renderReadout();
    syncControls();
    renderDoorOrder(); // deselect un-ranks; focus change updates the add-button state
  });

  function applyHighlight(): void {
    // Selected planes keep their original emissive; deselected planes glow amber ("excluded").
    for (const t of targets) {
      t.mat.emissive.setHex(isSelected(intent, t.id) ? t.orig : 0x552200);
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
    prio.disabled = !active;
    pinToggle.disabled = !active;
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
        cartsToggle.disabled = !opts.ctx.cartEligible[id];
        cartsToggle.checked = mp.onCarts;
      } else {
        cartsToggle.disabled = true;
        cartsToggle.checked = false;
      }
    }
    exportBtn.disabled = intent.selectedPlaneIds.length === 0;
  }

  prio.addEventListener('input', () => {
    if (!focusedId) return;
    intent = setPriority(intent, focusedId, prio.value === '' ? null : Math.max(0, Number(prio.value)));
  });
  pinToggle.addEventListener('change', () => {
    if (!focusedId) return;
    intent = pinToggle.checked ? pinAtCurrent(intent, focusedId, opts.ctx) : unpin(intent, focusedId);
    syncControls();
  });
  cartsToggle.addEventListener('change', () => {
    if (!focusedId) return;
    intent = setOnCarts(intent, focusedId, cartsToggle.checked);
  });
  exportBtn.addEventListener('click', () => {
    const yaml = intentToScenarioYaml(intent, opts.ctx);
    const blob = new Blob([yaml], { type: 'text/yaml' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'scenario.edited.yaml';
    a.click();
    URL.revokeObjectURL(a.href);
  });

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

  rankAdd.addEventListener('click', () => {
    if (!focusedId) return;
    intent = addToDoorOrder(intent, focusedId);
    renderDoorOrder();
  });

  applyHighlight();
  renderReadout();
  syncControls();
  renderDoorOrder();
  return { getIntent: () => intent };
}
