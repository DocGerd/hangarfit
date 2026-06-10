// node --test units for the anchor self-check (#440). checkAnchors() is the
// ADR-0017 cross-language backstop; the pure compare is testable here without a
// DOM (the fail-loud banner stays at the main.ts edge). Includes the
// structural-mismatch branches the headless render can't easily exercise.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { partCornersLocal, checkAnchors } from '../src/anchors.ts';
import { applyAffine } from '../src/affine.ts';
import type { Affine, BoxData, SceneV2 } from '../src/scene-contract.ts';

function box(over: Partial<BoxData> = {}): BoxData {
  return {
    kind: 'body', cx: 0, cy: 0, cz: 0.5, length_m: 2, width_m: 4, height_m: 1, angle_deg: 0,
    z_band: [0, 1], vertices: null, ...over,
  };
}

// scene/v2 hexagon footprint (plane-local u,v), to exercise the N-gon path.
const HEX: [number, number][] = [[1, 0], [0.4, 3], [-0.4, 3], [-1, 0], [-0.4, -3], [0.4, -3]];

test('partCornersLocal: scalar box (vertices null) → ±half-extent corners in oriented_rect order', () => {
  // hl=1, hw=2 ; order (+hl,−hw),(+hl,+hw),(−hl,+hw),(−hl,−hw)
  assert.deepEqual(partCornersLocal(box()), [[1, -2], [1, 2], [-1, 2], [-1, -2]]);
});

test('partCornersLocal: scalar box 90° CCW rotation about the box centre', () => {
  const c = partCornersLocal(box({ angle_deg: 90 }));
  // (hl,−hw)=(1,−2) rotated +90° about origin → (2, 1)
  assert.ok(Math.abs(c[0][0] - 2) < 1e-12 && Math.abs(c[0][1] - 1) < 1e-12);
});

test('partCornersLocal: polygon box → its plane-local vertices verbatim (no transform math)', () => {
  // A polygon part already carries the angle+offset-folded ring; the oracle just
  // applies the affine next, so partCornersLocal must return it untouched.
  assert.deepEqual(partCornersLocal(box({ vertices: HEX })), HEX);
});

// Build a scene whose anchors / gear_anchors are the TRUE oracle values for a
// given affine, so a faithful viewer compare yields maxErr 0.
function makeScene(aff: Affine, b: BoxData, wheel: [number, number]): SceneV2 {
  const corners = partCornersLocal(b).map(([u, v]) => applyAffine(aff, u, v));
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
    planes: [{ id: 'p', color: '#ffffff', boxes: [b], wheels: [wheel], on_carts: false }],
    conflicts: [],
    final_poses: { p: aff },
    anchors: { p: [corners] },
    gear_anchors: { p: [applyAffine(aff, wheel[0], wheel[1])] },
    timeline: { segments: [], total_s: 0 },
    placeholder: false,
    readouts: null,
  };
}

const AFF: Affine = [Math.sin(0.3), Math.cos(0.3), 4, Math.cos(0.3), -Math.sin(0.3), 9];

test('matching oracle → no structural error, maxErr ~0', () => {
  const r = checkAnchors(makeScene(AFF, box(), [0, 1]));
  assert.equal(r.structural, '');
  assert.ok(r.maxErr < 1e-9);
});

test('a corrupted corner pushes maxErr above the 1e-6 tolerance', () => {
  const s = makeScene(AFF, box(), [0, 1]);
  s.anchors.p[0][0][0] += 0.01; // corrupt one box-corner x
  const r = checkAnchors(s);
  assert.equal(r.structural, '');
  assert.ok(r.maxErr >= 0.01 - 1e-9);
});

test('anchor/box count mismatch → structural banner (compares before tolerance)', () => {
  const s = makeScene(AFF, box(), [0, 1]);
  s.anchors.p = []; // 0 anchor boxes vs 1 real box
  assert.match(checkAnchors(s).structural, /anchor\/box count mismatch/);
});

test('missing gear anchors → structural banner', () => {
  const s = makeScene(AFF, box(), [0, 1]);
  s.gear_anchors = {}; // gear oracle absent
  assert.match(checkAnchors(s).structural, /missing gear anchors\/wheels/);
});

test('missing final pose / anchors → structural banner', () => {
  const s = makeScene(AFF, box(), [0, 1]);
  s.anchors = {};
  assert.match(checkAnchors(s).structural, /missing affine\/anchors/);
});

test('polygon oracle matches viewer recompute → no structural error, maxErr ~0', () => {
  // The N-gon path runs the identical per-corner applyAffine compare, so a
  // faithful polygon oracle yields maxErr 0 exactly like a scalar box.
  const r = checkAnchors(makeScene(AFF, box({ vertices: HEX }), [0, 1]));
  assert.equal(r.structural, '');
  assert.ok(r.maxErr < 1e-9);
});

test('polygon anchor vertex-count mismatch → structural banner', () => {
  // A polygon box of N vertices against an oracle box of M (≠N) is a structural
  // breach the per-corner tolerance loop would otherwise index past.
  const s = makeScene(AFF, box({ vertices: HEX }), [0, 1]);
  s.anchors.p[0] = s.anchors.p[0].slice(0, 3); // 3 oracle corners vs 6 real vertices
  assert.match(checkAnchors(s).structural, /vertex count mismatch/);
});
