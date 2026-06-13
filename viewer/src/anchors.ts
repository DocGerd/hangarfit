// ── load-time anchor self-check: recompute final world corners and compare ───
//
// This is the ONLY thing pinning the JS matrix path to the Python oracle, and
// the ADR-0017 backstop. It is PURE here — `checkAnchors(scene)` returns a
// result; the fail-loud `banner()` side-effect stays at the thin edge in
// main.ts, so the comparison is node-testable without a DOM (#440). The
// structural-vs-tolerance reporting (and the "never throw" wrapping) is
// preserved behaviour-identically by the caller.
import * as THREE from 'three';
import { applyAffine } from './affine.ts';
import type { Affine, BoxData, SceneV2 } from './scene-contract.ts';

/** Plane-local footprint corners of a box (scene/v2). A polygon part carries its
 * ring in `vertices`, already folded into plane-local (u,v) — returned verbatim
 * so the oracle just applies the affine, no transform math (ADR-0002). A scalar
 * part has `vertices: null` and its 4 corners are rebuilt from length/width/angle
 * in oriented_rect order, rotated CCW about (cx,cy):
 *  (+hl,-hw),(+hl,+hw),(-hl,+hw),(-hl,-hw). */
export function partCornersLocal(b: BoxData): [number, number][] {
  if (b.vertices !== null) return b.vertices;
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

/** Compare one body's recomputed world box corners (geometry + final affine) to
 * the Python oracle. `label` names the body in any structural message. Shared by
 * planes and ground objects (#606) — both ride the identical det-−1 box path. */
function compareBoxesToOracle(
  aff: Affine | undefined,
  boxes: BoxData[],
  want: number[][][] | undefined,
  label: string,
): AnchorCheckResult {
  let maxErr = 0;
  if (!aff || !want) return { structural: 'missing affine/anchors for ' + label, maxErr };
  if (want.length !== boxes.length) {
    return { structural: 'anchor/box count mismatch for ' + label, maxErr };
  }
  for (let bi = 0; bi < boxes.length; bi++) {
    const corners = partCornersLocal(boxes[bi]);
    // A polygon box of N verts against an oracle box of M (≠N) is a structural
    // breach — catch it before the per-corner loop indexes past the oracle.
    if (want[bi].length !== corners.length) {
      return { structural: 'anchor/vertex count mismatch for ' + label, maxErr };
    }
    corners.forEach(([u, v], ci) => {
      const [wx, wy] = applyAffine(aff, u, v);
      maxErr = Math.max(maxErr, Math.abs(wx - want[bi][ci][0]), Math.abs(wy - want[bi][ci][1]));
    });
  }
  return { structural: '', maxErr };
}

/**
 * Recompute each plane's world box corners + wheel positions, and each ground
 * object's box corners (#606), from geometry and the final affine, and compare to
 * the Python oracle (`anchors`, `gear_anchors`, `go_anchors`). PURE — no DOM, no
 * throw. main.ts banners on `structural` or `maxErr > 1e-6`.
 */
export function checkAnchors(scene: SceneV2): AnchorCheckResult {
  let maxErr = 0;
  for (const p of scene.planes) {
    const aff = scene.final_poses[p.id];
    const r = compareBoxesToOracle(aff, p.boxes, scene.anchors[p.id], p.id);
    if (r.structural) return { structural: r.structural, maxErr };
    maxErr = Math.max(maxErr, r.maxErr);
    // Gear oracle (#399): the wheels[] ride the same affine Group as the boxes,
    // so a wrong transform corrupts both — but viewer.js is not pytest-covered,
    // so we cross-check the wheel world positions against the Python oracle too.
    const gw = scene.gear_anchors[p.id];
    if (!aff || !gw || !p.wheels) {
      return { structural: 'missing gear anchors/wheels for ' + p.id, maxErr };
    }
    if (gw.length !== p.wheels.length) {
      return { structural: 'gear anchor/wheel count mismatch for ' + p.id, maxErr };
    }
    p.wheels.forEach(([u, v], wi) => {
      const [wx, wy] = applyAffine(aff, u, v);
      maxErr = Math.max(maxErr, Math.abs(wx - gw[wi][0]), Math.abs(wy - gw[wi][1]));
    });
  }
  // Ground objects (#606): box corners only (no gear), same det-−1 box path.
  for (const go of scene.ground_objects) {
    const r = compareBoxesToOracle(go.final_pose, go.boxes, scene.go_anchors[go.id], go.id);
    if (r.structural) return { structural: r.structural, maxErr };
    maxErr = Math.max(maxErr, r.maxErr);
  }
  return { structural: '', maxErr };
}
