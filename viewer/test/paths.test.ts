// node --test units for the floor tow-path polyline derivation (#505). The
// 3D analogue of the 2D `solve --render-paths` overlay (#192/#193). The path is
// ALREADY implicit in the scene: each `timeline.segments[].samples` entry is a
// `[a,b,tx,c,d,ty]` affine whose translation `(tx, ty)` = `(sample[2],
// sample[5])` is exactly the plane-local origin's ground track. `pathPoints`
// derives the floor line from those samples with NO scene/v1 change (ADR-0017).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pathPoints } from '../src/paths.ts';
import type { Affine, SegmentData } from '../src/scene-contract.ts';

// Three samples whose translations trace door (y=0) → mid → slot. The linear
// part is irrelevant to the ground track — only (tx, ty) at indices 2 and 5 are.
const A0: Affine = [1, 0, 9.0, 0, 1, 0.0];
const A1: Affine = [0, 1, 8.5, 1, 0, 6.2];
const A2: Affine = [-1, 0, 8.6, 0, -1, 12.4];
const seg: SegmentData = { plane_id: 'p', start_s: 0, end_s: 4, samples: [A0, A1, A2] };

test('pathPoints: one floor point per sample, taking (tx, ty) = (sample[2], sample[5])', () => {
  assert.deepEqual(pathPoints(seg), [[9.0, 0.0], [8.5, 6.2], [8.6, 12.4]]);
});

test('pathPoints: an apron lead-in (first sample ty < 0) is preserved verbatim', () => {
  // With an apron the planner seeds the path OUTSIDE the door (y<0); the floor
  // line must extend to ty<0 so the slide-in is visible (#412 / ADR-0021).
  const apronSeg: SegmentData = {
    plane_id: 'q',
    start_s: 0,
    end_s: 4,
    samples: [[1, 0, 8.5, 0, 1, -7.49], [1, 0, 8.5, 0, 1, 0], [1, 0, 8.5, 0, 1, 6.0]],
  };
  const pts = pathPoints(apronSeg);
  assert.deepEqual(pts[0], [8.5, -7.49]);
  assert.ok(pts[0][1] < 0, 'lead-in point is outside the door (ty<0)');
});

test('pathPoints: a single-sample segment yields a single point (no line drawn by caller)', () => {
  assert.deepEqual(pathPoints({ plane_id: 'p', start_s: 0, end_s: 1, samples: [A0] }), [[9.0, 0.0]]);
});

test('pathPoints: an empty-samples segment yields no points', () => {
  assert.deepEqual(pathPoints({ plane_id: 'p', start_s: 0, end_s: 1, samples: [] }), []);
});
