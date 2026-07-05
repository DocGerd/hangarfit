// viewer/src/interaction/manipulate.ts — #911 PR B drag-to-fix gizmo mechanics.
// Intent-free and focus-free: editor.ts owns which plane is armed and turns a
// converted pose into a pin. Python owns the determinant-−1 inverse (ADR-0002):
// this module mirrors ONLY translation to the mesh (identity, safe) and never
// rotates it; heading is shown on the gizmo's yaw ring and read back through
// POST /convert. It imports neither affine.ts nor anchors.ts.
import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import type { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { banner } from '../dom.ts';
import { convertRequestInit } from '../serve-contract.ts';
import type { ConvertResponse } from '../serve-contract.ts';
import type { EditorContext } from './intent-contract.ts';

export interface ManipulatorHandle {
  arm(id: string): void; // attach the gizmo to plane id's proxy, seeded from ctx
  disarm(): void; // detach the gizmo (leaves heldByEditor as-is until reRender)
  armedId(): string | null;
  dispose(): void; // remove the gizmo + all listeners
}

export function createManipulator(opts: {
  scene: THREE.Scene;
  groups: Record<string, THREE.Group>;
  cam: THREE.Camera;
  renderer: THREE.WebGLRenderer;
  orbit: OrbitControls;
  ctx: EditorContext;
  onConverted: (id: string, pose: ConvertResponse) => void;
}): ManipulatorHandle {
  const proxy = new THREE.Object3D(); // a reusable anchor — NEVER a plane Group
  opts.scene.add(proxy);
  const control = new TransformControls(opts.cam, opts.renderer.domElement);
  control.setSpace('local');
  opts.scene.add(control); // r160: TransformControls IS an Object3D (no getHelper())
  let armed: string | null = null;

  // Z-up config applied per mode: translate shows XY handles only; rotate shows
  // the yaw Z-ring only.
  function setMode(mode: 'translate' | 'rotate'): void {
    control.setMode(mode);
    control.showX = mode === 'translate';
    control.showY = mode === 'translate';
    control.showZ = mode === 'rotate';
  }
  setMode('translate');

  // Suspend OrbitControls while dragging so the camera doesn't fight the gizmo.
  // `dragging-changed`'s payload is typed `{ value: unknown }` by
  // TransformControlsEventMap (@types/three@0.160); the vendored
  // TransformControls.js always dispatches a real boolean there, so an
  // `=== true` comparison reads it honestly without a cast.
  const onDraggingChanged = (event: { value: unknown }): void => {
    const dragging = event.value === true;
    opts.orbit.enabled = !dragging;
    if (!dragging && armed) void convertOnDrop(armed); // drag just ENDED
  };
  control.addEventListener('dragging-changed', onDraggingChanged);

  // Live translation follow (translate mode only): mirror ONLY position to the mesh,
  // preserving its Python-owned det-−1 linear part. Heading is never mirrored.
  const onObjectChange = (): void => {
    if (armed === null || control.getMode() !== 'translate') return;
    const g = opts.groups[armed];
    if (!g) return;
    const z0 = g.matrix.elements[14]; // keep the plane's layer z
    g.matrix.setPosition(proxy.position.x, proxy.position.y, z0);
    g.matrixWorldNeedsUpdate = true;
  };
  control.addEventListener('objectChange', onObjectChange);

  // Keyboard mode toggle while armed: t = translate (move), r = rotate (heading).
  const onKey = (ev: KeyboardEvent): void => {
    if (armed === null) return;
    if (ev.key === 't') setMode('translate');
    else if (ev.key === 'r') setMode('rotate');
  };
  window.addEventListener('keydown', onKey);

  async function convertOnDrop(id: string): Promise<void> {
    try {
      const req = { x: proxy.position.x, y: proxy.position.y, world_yaw_rad: proxy.rotation.z };
      const resp = await fetch('/convert', convertRequestInit(req));
      if (!resp.ok) {
        let msg = String(resp.status);
        try {
          msg = (JSON.parse(await resp.text()) as { error?: string }).error ?? msg;
        } catch {
          /* keep status */
        }
        banner('Fix position failed: ' + msg);
        return;
      }
      opts.onConverted(id, (await resp.json()) as ConvertResponse);
    } catch (e) {
      banner('Fix position failed: ' + (e as Error).message);
    }
  }

  return {
    arm(id: string): void {
      const c = opts.ctx.currentPoses[id];
      if (!c) return; // a palette-added plane has no pose to seed from
      const z0 = opts.groups[id]?.matrix.elements[14] ?? 0;
      proxy.position.set(c.x_m, c.y_m, z0);
      proxy.rotation.set(0, 0, c.world_yaw_rad); // Python seed — no JS trig (ADR-0002)
      setMode('translate');
      control.attach(proxy);
      const g = opts.groups[id];
      if (g) g.userData.heldByEditor = true; // stop the timeline driving this plane
      armed = id;
    },
    disarm(): void {
      control.detach();
      armed = null; // leaves heldByEditor set — cleared naturally when reRender rebuilds Groups
    },
    armedId: () => armed,
    dispose(): void {
      control.removeEventListener('dragging-changed', onDraggingChanged);
      control.removeEventListener('objectChange', onObjectChange);
      window.removeEventListener('keydown', onKey);
      control.detach();
      control.dispose();
      opts.scene.remove(control);
      opts.scene.remove(proxy);
      armed = null;
    },
  };
}
