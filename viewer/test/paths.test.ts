// node --test units for the floor tow-path polyline derivation (#505). The
// 3D analogue of the 2D `solve --render-paths` overlay (#192/#193). The path is
// ALREADY implicit in the scene: each `timeline.segments[].samples` entry is a
// `[a,b,tx,c,d,ty]` affine whose translation `(tx, ty)` = `(sample[2],
// sample[5])` is exactly the plane-local origin's ground track. `pathPoints`
// derives the floor line from those samples with NO scene/v2 change (ADR-0017).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { addTowPaths, pathPoints } from '../src/paths.ts';
import type { Affine, PlaneData, SceneV2, SegmentData } from '../src/scene-contract.ts';
import type { BrandTokens } from '../src/brand-contract.ts';

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

// ── addTowPaths: the impure THREE-backed builder (one Line per routable plane).
// Mirrors anchors.test.ts: build a minimal SceneV2 + BRAND blob, run the builder
// against a real THREE.Scene (the test-only `three` devDep), and inspect the
// returned Line objects. Only `conflict` of BRAND is read by addTowPaths, but
// the full BrandTokens shape is supplied so the literal type-checks.
const BRAND: BrandTokens = {
  sceneBg: '#000000',
  hemisphereSky: '#000000',
  hemisphereGround: '#000000',
  hemisphereIntensity: 1,
  sun: '#000000',
  sunIntensity: 1,
  fill: '#000000',
  fillIntensity: 1,
  floor: '#000000',
  gridMajor: '#000000',
  gridMinor: '#000000',
  walls: '#000000',
  wallsOpacity: 1,
  bay: '#000000',
  bayOpacity: 1,
  wheel: '#000000',
  cartDeck: '#000000',
  egressLane: '#D6A23E',
  conflict: '#e6194b',
  labelConflictChip: '#000000',
  labelChipBg: '#000000',
  labelText: '#000000',
};

function plane(over: Partial<PlaneData> = {}): PlaneData {
  return { id: 'x', color: '#112233', boxes: [], wheels: [], on_carts: false, ...over };
}

// A two-sample (drawable) tow segment for `plane_id`; the linear part is inert.
function towSeg(plane_id: string): SegmentData {
  return { plane_id, start_s: 0, end_s: 2, samples: [[1, 0, 5, 0, 1, 0], [1, 0, 5, 0, 1, 6]] };
}

// Minimal SceneV2 for the builder: it only reads planes[], conflicts[] and
// timeline.segments[]. Everything else is filled with valid-shape placeholders.
function makeScene(planes: PlaneData[], conflicts: string[], segments: SegmentData[]): SceneV2 {
  return {
    schema: 'hangarfit.scene/v2',
    units: 'm',
    coordinate_note: '',
    hangar: {
      width_m: 10,
      length_m: 10,
      door: { center_x_m: 5, width_m: 4 },
      maintenance_bay: { center_x_m: 5, width_m: 2, depth_m: 2, closed: false, plane_id: null },
      structural_notches: [],
    },
    planes,
    ground_objects: [],
    conflicts,
    final_poses: {},
    anchors: {},
    gear_anchors: {},
    go_anchors: {},
    egress_lanes: {},
    timeline: { segments, total_s: 0 },
    placeholder: false,
    readouts: null,
  };
}

test('addTowPaths: one line per routable plane; conflicted line uses BRAND.conflict, others p.color; segment-less / single-sample planes skipped', () => {
  const clean = plane({ id: 'clean', color: '#1f77b4' }); // drawable, not conflicted
  const bad = plane({ id: 'bad', color: '#2ca02c' }); // drawable AND in conflicts[]
  const onePt = plane({ id: 'onept', color: '#ff7f0e' }); // single-sample → no line
  const static_ = plane({ id: 'static', color: '#9467bd' }); // no segment → no line
  const SCENE = makeScene(
    [clean, bad, onePt, static_],
    ['bad'],
    [
      towSeg('clean'),
      towSeg('bad'),
      { plane_id: 'onept', start_s: 0, end_s: 1, samples: [[1, 0, 5, 0, 1, 0]] },
      // 'static' has no segment at all
    ],
  );

  const scene = new THREE.Scene();
  const { lines, setVisible } = addTowPaths(scene, SCENE, BRAND);

  // Exactly the two drawable planes produce a line; the single-point and the
  // segment-less plane are skipped.
  assert.equal(lines.length, 2, 'only the two ≥2-sample planes get a line');
  assert.equal(scene.children.length, 2, 'each line is added to the scene');

  // The two lines come out in planes[] order: clean (index 0), bad (index 1).
  const cleanLine = lines[0];
  const badLine = lines[1];
  const colourOf = (l: THREE.Line): string =>
    '#' + (l.material as THREE.LineBasicMaterial).color.getHexString();

  // Conflicted plane → conflict ink, NOT its own colour.
  assert.equal(colourOf(badLine), BRAND.conflict.toLowerCase(), 'conflicted line uses BRAND.conflict');
  assert.notEqual(colourOf(badLine), bad.color.toLowerCase(), 'conflicted line is NOT the plane colour');

  // Non-conflicted plane → its own hue.
  assert.equal(colourOf(cleanLine), clean.color.toLowerCase(), 'non-conflicted line uses the plane colour');

  // The visibility toggle flips every line at once.
  assert.ok(lines.every((l) => l.visible), 'lines start visible');
  setVisible(false);
  assert.ok(lines.every((l) => l.visible === false), 'setVisible(false) hides every line');
  setVisible(true);
  assert.ok(lines.every((l) => l.visible === true), 'setVisible(true) shows every line again');
});
