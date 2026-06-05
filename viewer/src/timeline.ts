// ── timeline state machine (hidden → animating → parked) ─────────────────────
import type * as THREE from 'three';
import { affineMatrix } from './affine';
import { byId } from './dom';
import type { Affine, SceneV1, SegmentData } from './scene-contract';

/** Pose + visibility of a plane at time `t`. PURE given `(segByPlane, finals, pid, t)`
 * — no THREE, no DOM (node-tested in #440). A static plane (no segment) renders at
 * its parked pose, or hides if a malformed scene is missing its final pose (the
 * anchor self-check already banners that). */
export function affineAt(
  segByPlane: Record<string, SegmentData>,
  finals: Record<string, Affine>,
  pid: string,
  t: number,
): { vis: boolean; aff: Affine | null } {
  const seg = segByPlane[pid];
  if (!seg) {
    const aff = finals[pid];
    return aff ? { vis: true, aff } : { vis: false, aff: null };
  }
  if (t < seg.start_s) return { vis: false, aff: null };        // not entered yet
  if (t >= seg.end_s) return { vis: true, aff: finals[pid] };
  const frac = (t - seg.start_s) / (seg.end_s - seg.start_s);
  const i = Math.round(frac * (seg.samples.length - 1));
  return { vis: true, aff: seg.samples[i] };
}

export interface Timeline {
  total: number;
  hasAnim: boolean;
  segs: SegmentData[];
  applyTime: (t: number) => void;
}

/** Wire the timeline to the built plane Groups + the `#active`/`#clock` readouts.
 * `applyTime(t)` is the per-frame impure edge around the pure `affineAt`. */
export function createTimeline(scene: SceneV1, groups: Record<string, THREE.Group>): Timeline {
  const TL = scene.timeline;
  const SEGS = TL.segments;
  const TOTAL = TL.total_s;
  const hasAnim = TOTAL > 0 && SEGS.length > 0;
  const segByPlane: Record<string, SegmentData> = {};
  for (const s of SEGS) segByPlane[s.plane_id] = s;
  const finals = scene.final_poses;

  const active = byId('active');
  const clock = byId('clock');

  const applyTime = (t: number): void => {
    for (const p of scene.planes) {
      const { vis, aff } = affineAt(segByPlane, finals, p.id, t);
      const g = groups[p.id];
      g.visible = vis;
      if (vis && aff) {
        g.matrix.copy(affineMatrix(aff));
        g.matrixWorldNeedsUpdate = true;
      }
    }
    const cur = SEGS.find((s) => t >= s.start_s && t < s.end_s);
    active.textContent = cur ? 'towing: ' + cur.plane_id : '';
    clock.textContent = t.toFixed(1) + 's';
  };

  return { total: TOTAL, hasAnim, segs: SEGS, applyTime };
}
