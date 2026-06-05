// ── affine → Matrix4 (z-row identity; det may be −1, that's intentional) ─────
//
// The ONLY arithmetic the viewer does on poses. `applyAffine` is pure (the
// oracle compare in anchors.ts and the #440 node tests use it); `affineMatrix`
// drops the Python-computed 2x3 affine into a THREE.Matrix4. The viewer never
// DERIVES an affine — Python owns the determinant-−1 transform (ADR-0002/0017).
import * as THREE from 'three';
import type { Affine } from './scene-contract.ts';

/** 2x3 affine `[a,b,tx,c,d,ty]` → THREE.Matrix4 mapping local (u,v,w,1) → world. */
export function affineMatrix(aff: Affine): THREE.Matrix4 {
  const [a, b, tx, c, d, ty] = aff;
  const m = new THREE.Matrix4();
  // row-major: maps local (u,v,w,1) → world (a·u+b·v+tx, c·u+d·v+ty, w).
  m.set(
    a, b, 0, tx,
    c, d, 0, ty,
    0, 0, 1, 0,
    0, 0, 0, 1,
  );
  return m;
}

/** Apply a 2x3 affine to a local (u,v), returning world (x,y). PURE. */
export function applyAffine(aff: Affine, u: number, v: number): [number, number] {
  const [a, b, tx, c, d, ty] = aff;
  return [a * u + b * v + tx, c * u + d * v + ty];
}
