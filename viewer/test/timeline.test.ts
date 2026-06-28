// node --test units for the timeline state machine (#440). affineAt() is pure
// given (segByPlane, finals, pid, t) — the hidden → animating → parked
// transitions of scene-v2-schema.md, untestable from pytest.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { affineAt, framePoses } from '../src/timeline.ts';
import type { Affine, SceneV2, SegmentData } from '../src/scene-contract.ts';

const FINAL: Affine = [1, 0, 9, 0, 1, 0];
const A0: Affine = [1, 0, 0, 0, 1, 0];
const A1: Affine = [1, 0, 5, 0, 1, 0];
const A2: Affine = [1, 0, 9, 0, 1, 0];
const seg: SegmentData = { plane_id: 'p', start_s: 2, end_s: 6, samples: [A0, A1, A2] };
// #865 Rung D: segByPlane maps a plane to its LIST of legs (one entry today).
const segByPlane: Record<string, SegmentData[]> = { p: [seg] };
const finals: Record<string, Affine> = { p: FINAL, q: FINAL };

test('static plane (no segment) → shown at its final pose', () => {
  assert.deepEqual(affineAt({}, finals, 'q', 3), { vis: true, aff: FINAL });
});

test('static plane missing its final pose → hidden (the self-check banners it)', () => {
  assert.deepEqual(affineAt({}, {}, 'q', 3), { vis: false, aff: null });
});

test('before start_s → hidden (not entered yet)', () => {
  assert.deepEqual(affineAt(segByPlane, finals, 'p', 1.9), { vis: false, aff: null });
});

test('at / after end_s → parked at the final pose', () => {
  assert.deepEqual(affineAt(segByPlane, finals, 'p', 6), { vis: true, aff: FINAL });
  assert.deepEqual(affineAt(segByPlane, finals, 'p', 99), { vis: true, aff: FINAL });
});

test('mid-animation → sample chosen by the rounded fraction', () => {
  // n=3 samples; frac=(t−2)/(6−2); i=round(frac·2)
  assert.deepEqual(affineAt(segByPlane, finals, 'p', 2), { vis: true, aff: A0 }); // frac 0 → i0
  assert.deepEqual(affineAt(segByPlane, finals, 'p', 4), { vis: true, aff: A1 }); // frac .5 → i1
  assert.deepEqual(affineAt(segByPlane, finals, 'p', 5.9), { vis: true, aff: A2 }); // frac .975 → round 2
});

// ── multi-leg body (#865 Rung D): staging leg, wait, then final leg ───────────
// A move-aside body (#667 Rung E) drives to a staging pose (leg 0), waits there
// while another body routes past, then drives to its final slot (leg 1). Between
// the legs the body rests at the staging pose (leg 0's last sample), NOT hidden.
const L0a: Affine = [1, 0, 2, 0, 1, 0]; // staging leg, first sample
const L0b: Affine = [1, 0, 3, 0, 1, 0]; // staging leg, last sample (the staging pose)
const L1a: Affine = [1, 0, 8, 0, 1, 0]; // final leg, first sample
const L1b: Affine = [1, 0, 9, 0, 1, 0]; // final leg, last sample
const leg0: SegmentData = { plane_id: 'm', start_s: 0, end_s: 4, samples: [L0a, L0b], leg_index: 0 };
const leg1: SegmentData = { plane_id: 'm', start_s: 6, end_s: 10, samples: [L1a, L1b], leg_index: 1 };
const multiLeg: Record<string, SegmentData[]> = { m: [leg0, leg1] };
const mFinals: Record<string, Affine> = { m: FINAL };

test('multi-leg: hidden → leg0 → waits at staging between legs → leg1 → parked', () => {
  assert.deepEqual(affineAt(multiLeg, mFinals, 'm', -1), { vis: false, aff: null }); // before leg0
  assert.deepEqual(affineAt(multiLeg, mFinals, 'm', 0), { vis: true, aff: L0a }); // leg0 start
  assert.deepEqual(affineAt(multiLeg, mFinals, 'm', 5), { vis: true, aff: L0b }); // gap → staging pose
  assert.deepEqual(affineAt(multiLeg, mFinals, 'm', 6), { vis: true, aff: L1a }); // leg1 start
  assert.deepEqual(affineAt(multiLeg, mFinals, 'm', 99), { vis: true, aff: FINAL }); // after leg1 → parked
});

// ── framePoses: planes ∪ ground-object movers, one state machine (#651) ───────
// A mover reuses the SAME hidden→sample→parked transitions as a plane, but its
// resting pose lives on its own block (final_pose), not in scene.final_poses.

const CADDY_FINAL: Affine = [1, 0, 7, 0, 1, 0];
const FUEL_FINAL: Affine = [1, 0, 3, 0, 1, 0];

function sceneWithMover(): SceneV2 {
  // Only the fields framePoses reads (planes/ground_objects/final_poses); a
  // partial cast keeps the fixture honest about what the unit depends on.
  return {
    planes: [{ id: 'p' }],
    ground_objects: [
      { id: 'caddy', final_pose: CADDY_FINAL },
      { id: 'fuel', final_pose: FUEL_FINAL },
    ],
    final_poses: { p: FINAL },
    timeline: { total_s: 0, segments: [] },
  } as unknown as SceneV2;
}

test('framePoses animates a routed mover (hidden → sample → parked); plane unaffected', () => {
  const moverSeg: SegmentData = { plane_id: 'caddy', start_s: 4, end_s: 8, samples: [A0, A1, A2] };
  const seg2: Record<string, SegmentData[]> = { caddy: [moverSeg] };
  const sc = sceneWithMover();
  assert.deepEqual(framePoses(sc, seg2, 0).caddy, { vis: false, aff: null }); // not entered
  assert.deepEqual(framePoses(sc, seg2, 6).caddy, { vis: true, aff: A1 }); // mid-animation
  assert.deepEqual(framePoses(sc, seg2, 99).caddy, { vis: true, aff: CADDY_FINAL }); // parked
  assert.deepEqual(framePoses(sc, seg2, 99).p, { vis: true, aff: FINAL }); // plane still parked
});

test('framePoses keeps a segment-less ground object (obstacle / deferred mover) at its final pose', () => {
  const sc = sceneWithMover();
  assert.deepEqual(framePoses(sc, {}, 5).fuel, { vis: true, aff: FUEL_FINAL });
  assert.deepEqual(framePoses(sc, {}, 5).caddy, { vis: true, aff: CADDY_FINAL });
});
