// node --test units for the pure affine math (#440). This is coverage pytest
// cannot reach: the only arithmetic the viewer does on Python-emitted poses.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { affineMatrix, applyAffine } from '../src/affine.ts';
import type { Affine } from '../src/scene-contract.ts';

test('applyAffine maps local (u,v) → world (x,y)', () => {
  const aff: Affine = [2, 0, 1, 0, 3, 5]; // [a,b,tx,c,d,ty]
  // x = a·u + b·v + tx = 2·1 + 0·1 + 1 = 3 ; y = c·u + d·v + ty = 0·1 + 3·1 + 5 = 8
  assert.deepEqual(applyAffine(aff, 1, 1), [3, 8]);
  const id: Affine = [1, 0, 0, 0, 1, 0];
  assert.deepEqual(applyAffine(id, 4, -2), [4, -2]);
});

test('applyAffine: translation is f(0,0)', () => {
  const aff: Affine = [0.5, -0.5, 2, 0.5, 0.5, -1];
  assert.deepEqual(applyAffine(aff, 0, 0), [2, -1]);
});

test('affineMatrix drops the 2x3 into a THREE.Matrix4 with z-row identity', () => {
  const m = affineMatrix([2, 3, 7, 5, 11, 13]);
  // THREE.Matrix4.elements is COLUMN-major; the row-major set() above transposes.
  assert.deepEqual(Array.from(m.elements), [
    2, 5, 0, 0, // column 0
    3, 11, 0, 0, // column 1
    0, 0, 1, 0, // column 2 (z-row identity → box height passes through)
    7, 13, 0, 1, // column 3 (translation)
  ]);
});

test('a heading affine is a determinant-−1 reflection (ADR-0002), preserved by affineMatrix', () => {
  const h = (30 * Math.PI) / 180;
  // geometry.local_to_world linear part: [[sin, cos],[cos, −sin]] → det = −1.
  const aff: Affine = [Math.sin(h), Math.cos(h), 4, Math.cos(h), -Math.sin(h), 9];
  const [a, b, , c, d] = aff;
  assert.ok(Math.abs(a * d - b * c - -1) < 1e-12);
  // affineMatrix must carry the same −1 (elements col-major: a=e0, c=e1, b=e4, d=e5).
  const e = affineMatrix(aff).elements;
  const det2 = e[0] * e[5] - e[4] * e[1];
  assert.ok(Math.abs(det2 - -1) < 1e-12);
});
