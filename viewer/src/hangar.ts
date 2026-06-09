// ── hangar: floor, grid, walls (split at door), maintenance bay ──────────────
import * as THREE from 'three';
import type { BrandTokens } from './brand-contract.ts';
import type { HangarData } from './scene-contract.ts';

export const WALL_H = 3.0;

/** Build floor, grid, the four walls (front split at the door) and a closed
 * maintenance bay. Returns the wall meshes so main.ts can wire the walls toggle. */
export function addHangar(
  scene: THREE.Scene, H: HangarData, BRAND: BrandTokens, span: number,
): THREE.Mesh[] {
  const wallMeshes: THREE.Mesh[] = [];
  const notches = H.structural_notches ?? [];

  const floorMat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(BRAND.floor), roughness: 1, side: THREE.DoubleSide,
  });
  let floor: THREE.Mesh;
  if (notches.length === 0) {
    floor = new THREE.Mesh(new THREE.PlaneGeometry(H.width_m, H.length_m), floorMat);
    floor.position.set(H.width_m / 2, H.length_m / 2, 0); // PlaneGeometry lies in XY (normal +Z)
  } else {
    // L-shaped floor (ADR-0018): the bounding rectangle minus each notch, built
    // as a ShapeGeometry hole. ShapeGeometry already lies in XY in absolute
    // hangar coords, so no recentre is needed.
    const shape = new THREE.Shape();
    shape.moveTo(0, 0);
    shape.lineTo(H.width_m, 0);
    shape.lineTo(H.width_m, H.length_m);
    shape.lineTo(0, H.length_m);
    shape.closePath();
    for (const n of notches) {
      const hole = new THREE.Path();
      hole.moveTo(n.x_min_m, n.y_min_m);
      hole.lineTo(n.x_max_m, n.y_min_m);
      hole.lineTo(n.x_max_m, n.y_max_m);
      hole.lineTo(n.x_min_m, n.y_max_m);
      hole.closePath();
      shape.holes.push(hole);
    }
    floor = new THREE.Mesh(new THREE.ShapeGeometry(shape), floorMat);
  }
  floor.receiveShadow = true; // catches the planes' contact shadows (#400)
  scene.add(floor);

  const grid = new THREE.GridHelper(
    span, Math.round(span), new THREE.Color(BRAND.gridMajor), new THREE.Color(BRAND.gridMinor),
  );
  grid.rotation.x = Math.PI / 2; // GridHelper is in XZ by default → rotate into XY
  grid.position.set(H.width_m / 2, H.length_m / 2, 0.003);
  scene.add(grid);

  const t = 0.08;
  const addWall = (sx: number, sy: number, x: number, y: number): void => {
    const m = new THREE.MeshStandardMaterial({
      color: new THREE.Color(BRAND.walls), transparent: true, opacity: BRAND.wallsOpacity,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, WALL_H), m);
    mesh.position.set(x, y, WALL_H / 2);
    scene.add(mesh);
    wallMeshes.push(mesh);
  };
  addWall(t, H.length_m, 0, H.length_m / 2);             // left
  addWall(t, H.length_m, H.width_m, H.length_m / 2);     // right
  addWall(H.width_m, t, H.width_m / 2, H.length_m);      // back
  const dl = H.door.center_x_m - H.door.width_m / 2;
  const dr = H.door.center_x_m + H.door.width_m / 2;
  if (dl > 1e-6) addWall(dl, t, dl / 2, 0);              // front, left of door
  if (dr < H.width_m - 1e-6) addWall(H.width_m - dr, t, (dr + H.width_m) / 2, 0); // front, right

  // Interior notch walls (ADR-0018): the office-corner walls that face the
  // hangar floor. Skip any notch edge flush with the outer boundary — that edge
  // already has the outer wall (or is the building corner).
  const eps = 1e-6;
  for (const n of notches) {
    const cx = (n.x_min_m + n.x_max_m) / 2;
    const cy = (n.y_min_m + n.y_max_m) / 2;
    const w = n.x_max_m - n.x_min_m;
    const l = n.y_max_m - n.y_min_m;
    if (n.x_min_m > eps) addWall(t, l, n.x_min_m, cy);                  // left face
    if (n.x_max_m < H.width_m - eps) addWall(t, l, n.x_max_m, cy);      // right face
    if (n.y_min_m > eps) addWall(w, t, cx, n.y_min_m);                  // front face
    if (n.y_max_m < H.length_m - eps) addWall(w, t, cx, n.y_max_m);     // back face
  }

  const bay = H.maintenance_bay;
  if (bay && bay.closed) {
    const bm = new THREE.Mesh(
      new THREE.BoxGeometry(bay.width_m, bay.depth_m, WALL_H),
      new THREE.MeshStandardMaterial({
        color: new THREE.Color(BRAND.bay), transparent: true, opacity: BRAND.bayOpacity,
      }),
    );
    bm.position.set(bay.center_x_m, H.length_m - bay.depth_m / 2, WALL_H / 2);
    scene.add(bm);
  }
  return wallMeshes;
}
