// ── floor egress-lane polylines (#652): the hard-door mover drive-out corridor ─
//
// Draw each hard-door mover's required drive-out corridor as a dashed amber line
// on the hangar floor — the 3D counterpart of the 2D egress decal. The corridor
// geometry arrives ready-made in `scene.egress_lanes` (sampled [x, y] world points
// the Python egress oracle now surfaces, #652 / `towplanner.egress_corridors`); the
// viewer does NO routing math. Amber = "keep clear", deliberately distinct from the
// per-plane tow-path hue (a route) and the conflict red (a violation). The colour is
// read from `BRAND.egressLane` — nothing hard-coded here (#419).
import * as THREE from 'three';
import type { BrandTokens } from './brand-contract.ts';
import type { SceneV2 } from './scene-contract.ts';

export interface EgressLanes {
  /** The Line objects, so the caller can dispose / inspect them if needed. */
  lines: THREE.Line[];
}

/** Build one dashed amber floor polyline per hard-door egress corridor and add
 * them to `scene`. A scene with no egress lanes builds nothing and is inert. Lines
 * sit a hair above the tow paths (z = 0.025 > the 0.02 tow-path offset) so the
 * keep-clear corridor reads on top of a route that overlaps it. Mover ids are
 * sorted for deterministic build order. Egress lanes are a safety annotation, so
 * they are always on (no HUD toggle). */
export function addEgressLanes(scene: THREE.Scene, SCENE: SceneV2, BRAND: BrandTokens): EgressLanes {
  const Z_OFFSET = 0.025; // just above the tow paths (0.02) so the corridor reads on top
  const colour = new THREE.Color(BRAND.egressLane);
  const lines: THREE.Line[] = [];
  for (const moverId of Object.keys(SCENE.egress_lanes).sort()) {
    const pts = SCENE.egress_lanes[moverId];
    if (pts.length < 2) continue; // a single point is not a line
    const geom = new THREE.BufferGeometry().setFromPoints(
      pts.map(([x, y]) => new THREE.Vector3(x, y, Z_OFFSET)),
    );
    const line = new THREE.Line(
      geom,
      new THREE.LineDashedMaterial({
        color: colour,
        dashSize: 0.6,
        gapSize: 0.3,
        transparent: true,
        opacity: 0.9,
      }),
    );
    line.computeLineDistances(); // required for LineDashedMaterial to render dashes
    scene.add(line);
    lines.push(line);
  }
  return { lines };
}
