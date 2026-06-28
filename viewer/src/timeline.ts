// ── timeline state machine (hidden → animating → parked) ─────────────────────
import type * as THREE from 'three';
import { affineMatrix } from './affine.ts';
import { byId } from './dom.ts';
import type { Affine, SceneV2, SegmentData } from './scene-contract.ts';

/** Pose + visibility of a plane at time `t`. PURE given `(segByPlane, finals, pid, t)`
 * — no THREE, no DOM (node-tested in #440). A static plane (no leg) renders at its
 * parked pose, or hides if a malformed scene is missing its final pose (the anchor
 * self-check already banners that).
 *
 * #865 Rung D: `segByPlane` maps a plane to its LIST of legs (one entry today). A
 * move-aside body (#667 Rung E) has a staging leg then a final leg: it animates each
 * leg, and in a GAP between legs it rests at the staging pose (the just-completed
 * leg's last sample), NOT hidden. Single-leg input is byte-identical to before. */
export function affineAt(
  segByPlane: Record<string, SegmentData[]>,
  finals: Record<string, Affine>,
  pid: string,
  t: number,
): { vis: boolean; aff: Affine | null } {
  const segs = segByPlane[pid];
  if (!segs || segs.length === 0) {
    const aff = finals[pid];
    return aff ? { vis: true, aff } : { vis: false, aff: null };
  }
  if (t < segs[0].start_s) return { vis: false, aff: null };          // not entered yet
  const last = segs[segs.length - 1];
  if (t >= last.end_s) return { vis: true, aff: finals[pid] };        // parked at final pose
  // Legs are in execution order. Find the one covering t; if t falls in a gap
  // BETWEEN legs, the body waits at the most-recently-completed leg's last sample.
  let rest: Affine | null = null;
  for (const seg of segs) {
    if (t < seg.start_s) break;                                       // gap before this leg → use `rest`
    if (t < seg.end_s) {
      const frac = (t - seg.start_s) / (seg.end_s - seg.start_s);
      const i = Math.round(frac * (seg.samples.length - 1));
      return { vis: true, aff: seg.samples[i] };
    }
    rest = seg.samples[seg.samples.length - 1];                       // past this leg → staging pose candidate
  }
  return { vis: true, aff: rest };
}

/** Pure: pose + visibility of EVERY animated body — planes ∪ placed-routed
 * ground-object movers (#651) — at time `t`. A mover reuses the SAME
 * hidden→sample→parked state machine as a plane via `affineAt`; the only
 * difference is its resting pose lives on its own block (`final_pose`), not in
 * `scene.final_poses` (aircraft-only), so each GO gets a single-entry finals
 * map. A body with no segment (a static plane, a fixed obstacle, or a deferred
 * path=None mover) shows at its own final pose. No THREE, no DOM — node-tested. */
export function framePoses(
  scene: SceneV2,
  segByPlane: Record<string, SegmentData[]>,
  t: number,
): Record<string, { vis: boolean; aff: Affine | null }> {
  const out: Record<string, { vis: boolean; aff: Affine | null }> = {};
  for (const p of scene.planes) {
    out[p.id] = affineAt(segByPlane, scene.final_poses, p.id, t);
  }
  for (const go of scene.ground_objects) {
    out[go.id] = affineAt(segByPlane, { [go.id]: go.final_pose }, go.id, t);
  }
  return out;
}

export interface Timeline {
  total: number;
  hasAnim: boolean;
  segs: SegmentData[];
  applyTime: (t: number) => void;
}

/** Wire the timeline to the built plane + ground-object Groups and the
 * `#active`/`#clock` readouts. `applyTime(t)` is the per-frame impure edge around
 * the pure `framePoses`. Movers (#651) animate from the same `segments` list —
 * `goGroups` defaults to `{}` so a GO-free scene is unchanged. */
export function createTimeline(
  scene: SceneV2,
  groups: Record<string, THREE.Group>,
  goGroups: Record<string, THREE.Group> = {},
): Timeline {
  const TL = scene.timeline;
  const SEGS = TL.segments;
  const TOTAL = TL.total_s;
  const hasAnim = TOTAL > 0 && SEGS.length > 0;
  // #865 Rung D: group legs per plane (don't overwrite) — a move-aside body has
  // more than one. Single-leg bodies (every body today) yield single-element lists.
  const segByPlane: Record<string, SegmentData[]> = {};
  for (const s of SEGS) (segByPlane[s.plane_id] ??= []).push(s);

  const active = byId('active');
  const clock = byId('clock');

  const applyTime = (t: number): void => {
    const poses = framePoses(scene, segByPlane, t);
    const drive = (id: string, g: THREE.Group | undefined): void => {
      if (!g) return;
      const { vis, aff } = poses[id];
      g.visible = vis;
      if (vis && aff) {
        g.matrix.copy(affineMatrix(aff));
        g.matrixWorldNeedsUpdate = true;
      }
    };
    for (const p of scene.planes) drive(p.id, groups[p.id]);
    for (const go of scene.ground_objects) drive(go.id, goGroups[go.id]);

    const cur = SEGS.find((s) => t >= s.start_s && t < s.end_s);
    active.textContent = cur ? 'towing: ' + cur.plane_id : '';
    clock.textContent = t.toFixed(1) + 's';
  };

  return { total: TOTAL, hasAnim, segs: SEGS, applyTime };
}
