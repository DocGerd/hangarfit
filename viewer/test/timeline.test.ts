// node --test units for the timeline state machine (#440). affineAt() is pure
// given (segByPlane, finals, pid, t) — the hidden → animating → parked
// transitions of scene-v1-schema.md, untestable from pytest.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { affineAt } from '../src/timeline.ts';
import type { Affine, SegmentData } from '../src/scene-contract.ts';

const FINAL: Affine = [1, 0, 9, 0, 1, 0];
const A0: Affine = [1, 0, 0, 0, 1, 0];
const A1: Affine = [1, 0, 5, 0, 1, 0];
const A2: Affine = [1, 0, 9, 0, 1, 0];
const seg: SegmentData = { plane_id: 'p', start_s: 2, end_s: 6, samples: [A0, A1, A2] };
const segByPlane: Record<string, SegmentData> = { p: seg };
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
