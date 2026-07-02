// viewer/src/interaction/editor.ts — the impure raycaster-selection edge (#442
// Chunk 2). Mounted only when `view --edit` injects an `#editor-context` blob
// (main.ts gates the call); dormant otherwise. Per the interaction/README's one
// hard rule, this module may import `three` but must NEVER import `affine.ts`
// or `anchors.ts` — it never re-derives geometry, only reads the already-built
// plane Groups the Python-owned transform placed.
import * as THREE from 'three';
import { initialIntent, toggleSelection, isSelected } from './selection.ts';
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
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const idByObject = new Map<THREE.Object3D, string>();
  for (const [id, g] of Object.entries(opts.groups)) g.traverse((o) => idByObject.set(o, id));
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
    applyHighlight();
    renderReadout();
  });

  function applyHighlight(): void {
    // Deselected planes glow amber ("excluded from fleet"); selected keep the default look.
    for (const [id, g] of Object.entries(opts.groups)) {
      const on = isSelected(intent, id);
      g.traverse((o) => {
        const mesh = o as THREE.Mesh;
        const mat = mesh.material as THREE.MeshStandardMaterial | undefined;
        if (mat && mat.emissive) mat.emissive.setHex(on ? 0x000000 : 0x552200);
      });
    }
  }

  function renderReadout(): void {
    const readout = document.getElementById('sel-readout');
    if (readout) readout.textContent = `selected: ${[...intent.selectedPlaneIds].sort().join(', ')}`;
  }

  applyHighlight();
  renderReadout();
  return { getIntent: () => intent };
}
