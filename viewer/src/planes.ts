// ── planes: one Group of boxes each, built ONCE in plane-local coords ────────
import * as THREE from 'three';
import { byId } from './dom.ts';
import { addGear, makeGearMaterials } from './gear.ts';
import { addLabelAndNose } from './labels.ts';
import type { BrandTokens } from './brand-contract.ts';
import type { BoxData, SceneV2 } from './scene-contract.ts';

export interface PlanesBundle {
  groups: Record<string, THREE.Group>;
  labelMeshes: THREE.Sprite[];
  noseMeshes: THREE.Mesh[];
}

// Kind-based materials (#400): translucent wings reveal vertical stacking; thin
// metallic struts; a darker cockpit (fuselage_front) tint echoing the 2D render's
// cockpit shading (same intent, not a perceptual match — 3D darkens in linear
// space); everything else opaque body colour. DoubleSide for the det-−1 group.
export function boxMaterial(b: BoxData, colour: THREE.Color): THREE.MeshStandardMaterial {
  const base = { color: colour, side: THREE.DoubleSide, roughness: 0.7, metalness: 0.05 };
  if (b.kind === 'wing') {
    return new THREE.MeshStandardMaterial({ ...base, transparent: true, opacity: 0.5 });
  }
  if (b.kind === 'strut') {
    return new THREE.MeshStandardMaterial({ ...base, roughness: 0.35, metalness: 0.85 });
  }
  if (b.kind === 'fuselage_front') {
    return new THREE.MeshStandardMaterial({ ...base, color: colour.clone().multiplyScalar(0.55) });
  }
  return new THREE.MeshStandardMaterial(base);
}

/** Build every plane's affine Group (boxes + gear + label/nose), add it to the
 * scene, and build the legend chips with safe DOM methods. Returns the per-plane
 * groups (driven by the timeline) and the toggle arrays. */
export function addPlanes(scene: THREE.Scene, SCENE: SceneV2, BRAND: BrandTokens): PlanesBundle {
  const CONFLICT = BRAND.conflict; // '#RRGGBB' string → new THREE.Color(CONFLICT)
  const groups: Record<string, THREE.Group> = {};
  const labelMeshes: THREE.Sprite[] = [];
  const noseMeshes: THREE.Mesh[] = [];
  const legend = byId('legend');
  const gearMats = makeGearMaterials(BRAND);

  for (const p of SCENE.planes) {
    const g = new THREE.Group();
    g.matrixAutoUpdate = false; // we drive g.matrix per frame from the affine
    const conflicted = SCENE.conflicts.includes(p.id);
    const colour = new THREE.Color(conflicted ? CONFLICT : p.color);
    for (const b of p.boxes) {
      // local X = u (forward/length), local Y = v (right/width), local Z = w (height).
      let mesh: THREE.Mesh;
      if (b.vertices !== null) {
        // scene/v2 polygon footprint (#549): extrude the plane-local ring into a
        // prism. The ring already has (cx,cy,angle) folded in (it matches the
        // anchor oracle), so we apply NO position.xy / rotation here — only lift
        // the base to z_bottom. ExtrudeGeometry lays the Shape in XY and extrudes
        // +Z from 0..height_m, mirroring the box's [z_bottom, z_top] span. The
        // ShapeGeometry L-floor (hangar.ts) is the in-tree precedent (#530).
        const shape = new THREE.Shape();
        const vs = b.vertices;
        shape.moveTo(vs[0][0], vs[0][1]);
        for (let i = 1; i < vs.length; i++) shape.lineTo(vs[i][0], vs[i][1]);
        shape.closePath();
        mesh = new THREE.Mesh(
          new THREE.ExtrudeGeometry(shape, { depth: b.height_m, bevelEnabled: false }),
          boxMaterial(b, colour),
        );
        mesh.position.z = b.z_band[0];
      } else {
        mesh = new THREE.Mesh(
          new THREE.BoxGeometry(b.length_m, b.width_m, b.height_m),
          boxMaterial(b, colour),
        );
        mesh.position.set(b.cx, b.cy, b.cz);
        mesh.rotation.z = THREE.MathUtils.degToRad(b.angle_deg); // CCW about local up, as oriented_rect
      }
      mesh.castShadow = true;
      mesh.receiveShadow = true; // planes catch each other's shadows (vertical clearance)
      g.add(mesh);
    }
    addGear(g, p, gearMats); // wheels + legs (+ pallets when carted), same affine Group
    addLabelAndNose(g, p, colour, conflicted, BRAND, labelMeshes, noseMeshes); // id label + nose arrow
    groups[p.id] = g;
    scene.add(g);

    // Build the legend chip with safe DOM methods (no innerHTML): plane ids come
    // from user YAML, so avoid any HTML-injection surface even on a local file.
    const sw = document.createElement('span');
    sw.className = 'sw';
    const dot = document.createElement('i');
    dot.style.background = conflicted ? BRAND.conflict : p.color;
    sw.appendChild(dot);
    sw.appendChild(document.createTextNode(p.id));
    legend.appendChild(sw);
  }
  return { groups, labelMeshes, noseMeshes };
}
