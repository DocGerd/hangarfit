// ── floor tow-path polylines (#505): the 3D analogue of the 2D --render-paths ─
//
// Draw each placed plane's full tow route as a coloured line on the hangar floor
// (z ≈ 0), one colour per plane — the 3D counterpart of `solve --render-paths`
// (#192/#193). NO scene/v2 change (ADR-0017): the polyline is already implicit
// in the timeline. Each `segments[].samples` entry is a `[a,b,tx,c,d,ty]` affine
// whose translation `(tx, ty) = (sample[2], sample[5])` is exactly the
// plane-local origin's ground track, door (or apron, ty<0) → parked slot. So we
// derive the floor line straight from the samples Python already emits.
//
// The line uses the plane's own `p.color` — the same hue as its boxes, its nose
// cone, and its legend swatch (PLANES_DARK, the dark-surface expression of the
// 2D PLANES fill, brand-parity by sorted-id index, ADR-0017/#415) — so the
// "blue plane has a blue route" reading is self-consistent in the viewer.
import * as THREE from 'three';
import type { BrandTokens } from './brand-contract.ts';
import type { SceneV2, SegmentData } from './scene-contract.ts';

const TX = 2; // index of tx in a [a,b,tx,c,d,ty] affine
const TY = 5; // index of ty

/** The floor ground track of one tow segment: `(tx, ty)` of each sample, in
 * order (door/apron → slot). PURE — no THREE, no DOM (node-tested in #505). A
 * segment with an apron lead-in carries its `ty < 0` sample(s) verbatim, so the
 * slide-in from outside the door is part of the drawn line (#412 / ADR-0021). */
export function pathPoints(seg: SegmentData): [number, number][] {
  return seg.samples.map((s) => [s[TX], s[TY]]);
}

export interface TowPaths {
  /** The Line objects, so the caller can dispose / inspect them if needed. */
  lines: THREE.Line[];
  /** Show/hide every floor path at once (the HUD `paths` toggle). */
  setVisible: (on: boolean) => void;
}

/** Build one floor polyline per tow segment, coloured by the plane's own hue,
 * and add them to `scene`. A plane with no segment (static / un-routable scene)
 * gets no line — there is no route to draw. Lines sit a hair above the floor
 * (z = `Z_OFFSET`) so they don't z-fight the floor plane or the grid, and below
 * the parked planes' bellies. Returns the lines + a visibility toggle. */
export function addTowPaths(scene: THREE.Scene, SCENE: SceneV2, BRAND: BrandTokens): TowPaths {
  const Z_OFFSET = 0.02; // just above the floor (grid sits at 0.003); under any belly
  const segByPlane: Record<string, SegmentData> = {};
  for (const s of SCENE.timeline.segments) segByPlane[s.plane_id] = s;

  const lines: THREE.Line[] = [];
  for (const p of SCENE.planes) {
    const seg = segByPlane[p.id];
    if (!seg) continue; // static plane: no tow route to draw
    const pts = pathPoints(seg);
    if (pts.length < 2) continue; // a single point is not a line
    const conflicted = SCENE.conflicts.includes(p.id);
    const colour = new THREE.Color(conflicted ? BRAND.conflict : p.color);
    const geom = new THREE.BufferGeometry().setFromPoints(
      pts.map(([x, y]) => new THREE.Vector3(x, y, Z_OFFSET)),
    );
    const line = new THREE.Line(geom, new THREE.LineBasicMaterial({ color: colour }));
    scene.add(line);
    lines.push(line);
  }

  const setVisible = (on: boolean): void => {
    for (const l of lines) l.visible = on;
  };
  return { lines, setVisible };
}
