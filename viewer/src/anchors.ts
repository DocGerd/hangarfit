// ── load-time anchor self-check: recompute final world corners and compare ───
//
// This is the ONLY thing pinning the JS matrix path to the Python oracle, and
// the ADR-0017 backstop. It is PURE here — `checkAnchors(scene)` returns a
// result; the fail-loud `banner()` side-effect stays at the thin edge in
// main.ts, so the comparison is node-testable without a DOM (#440). The
// structural-vs-tolerance reporting (and the "never throw" wrapping) is
// preserved behaviour-identically by the caller.
import * as THREE from 'three';
import { applyAffine } from './affine';
import type { BoxData, SceneV1 } from './scene-contract';

/** oriented_rect corner order, rotated CCW about (cx,cy):
 *  (+hl,-hw),(+hl,+hw),(-hl,+hw),(-hl,-hw). */
export function boxCornersLocal(b: BoxData): [number, number][] {
  const h = THREE.MathUtils.degToRad(b.angle_deg);
  const cs = Math.cos(h), sn = Math.sin(h);
  const hl = b.length_m / 2, hw = b.width_m / 2;
  const corners: [number, number][] = [[hl, -hw], [hl, hw], [-hl, hw], [-hl, -hw]];
  return corners.map(
    ([lx, ly]) => [b.cx + lx * cs - ly * sn, b.cy + lx * sn + ly * cs] as [number, number],
  );
}

/** Result of the oracle compare. `structural` non-empty => a structural problem
 * (missing/mismatched data) found first; otherwise `maxErr` is the largest
 * |viewer − oracle| coordinate error over all box corners and wheels. */
export interface AnchorCheckResult {
  structural: string;
  maxErr: number;
}

/**
 * Recompute each plane's world box corners + wheel positions from geometry and
 * its final affine, and compare to the Python oracle (`anchors`, `gear_anchors`).
 * PURE — no DOM, no throw. main.ts banners on `structural` or `maxErr > 1e-6`.
 */
export function checkAnchors(scene: SceneV1): AnchorCheckResult {
  let maxErr = 0;
  let structural = '';
  for (const p of scene.planes) {
    const aff = scene.final_poses[p.id];
    const want = scene.anchors[p.id];
    if (!aff || !want) {
      structural = 'missing affine/anchors for ' + p.id;
      break;
    }
    if (want.length !== p.boxes.length) {
      structural = 'anchor/box count mismatch for ' + p.id;
      break;
    }
    p.boxes.forEach((b, bi) => {
      boxCornersLocal(b).forEach(([u, v], ci) => {
        const [wx, wy] = applyAffine(aff, u, v);
        maxErr = Math.max(maxErr, Math.abs(wx - want[bi][ci][0]), Math.abs(wy - want[bi][ci][1]));
      });
    });
    // Gear oracle (#399): the wheels[] ride the same affine Group as the boxes,
    // so a wrong transform corrupts both — but viewer.js is not pytest-covered,
    // so we cross-check the wheel world positions against the Python oracle too.
    const gw = scene.gear_anchors[p.id];
    if (!gw || !p.wheels) {
      structural = 'missing gear anchors/wheels for ' + p.id;
      break;
    }
    if (gw.length !== p.wheels.length) {
      structural = 'gear anchor/wheel count mismatch for ' + p.id;
      break;
    }
    p.wheels.forEach(([u, v], wi) => {
      const [wx, wy] = applyAffine(aff, u, v);
      maxErr = Math.max(maxErr, Math.abs(wx - gw[wi][0]), Math.abs(wy - gw[wi][1]));
    });
  }
  return { structural, maxErr };
}
