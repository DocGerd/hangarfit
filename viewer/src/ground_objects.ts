// ── ground objects: placed non-aircraft bodies (#606) ───────────────────────
//
// A thin sibling of addPlanes for the Stage-A ground objects: the fixed obstacle
// (a keep-out, e.g. the Maul fuel trailer) and the placed/routed movers (the VW
// Caddy + glider trailers). Each is built ONCE in plane-local coords via the
// shared boxMesh, then parked at its static final_pose affine — there is no
// timeline yet (mover animation + the Caddy egress lane are deferred follow-ups,
// the egress oracle exports no corridor geometry). The viewer does NO transform
// math: final_pose is the det-−1 affine computed in Python (ADR-0002/ADR-0017),
// and checkAnchors() cross-checks each body's world corners against go_anchors.
//
// Colour is read from each block's `color` (brand-resolved per class in
// scene.py, the plane colour-map idiom) — nothing hard-coded here (#419).
import * as THREE from 'three';
import { affineMatrix } from './affine.ts';
import { byId } from './dom.ts';
import { boxMesh } from './planes.ts';
import type { SceneV2 } from './scene-contract.ts';

export interface GroundObjectsBundle {
  /** Per-id static Groups, returned for parity with addPlanes (disposal/inspect). */
  groups: Record<string, THREE.Group>;
}

/** Build each ground object's affine Group (boxes only — no gear/nose), park it at
 * its static final_pose, add it to the scene, and append a legend chip. Returns
 * the groups. A GO-free scene builds nothing and is inert (no legend rows). */
export function addGroundObjects(scene: THREE.Scene, SCENE: SceneV2): GroundObjectsBundle {
  const groups: Record<string, THREE.Group> = {};
  const legend = byId('legend');

  for (const go of SCENE.ground_objects) {
    const g = new THREE.Group();
    g.matrixAutoUpdate = false; // static body: set the matrix once from the affine
    const colour = new THREE.Color(go.color);
    for (const b of go.boxes) g.add(boxMesh(b, colour));
    g.matrix.copy(affineMatrix(go.final_pose));
    g.matrixWorldNeedsUpdate = true;
    groups[go.id] = g;
    scene.add(g);

    // Legend chip, built with safe DOM methods (ids come from user YAML — no
    // innerHTML). The class is shown so obstacle vs mover is unambiguous; the
    // hard-door (egress) Caddy is flagged with a ⮕ door cue.
    const sw = document.createElement('span');
    sw.className = 'sw';
    const dot = document.createElement('i');
    dot.style.background = go.color;
    sw.appendChild(dot);
    const klass = go.object_class === 'fixed_obstacle' ? 'obstacle' : 'mover';
    const tag = go.hard_door_mover ? `${go.id} (${klass} ⮕ door)` : `${go.id} (${klass})`;
    sw.appendChild(document.createTextNode(tag));
    legend.appendChild(sw);
  }
  return { groups };
}
