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

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(H.width_m, H.length_m),
    new THREE.MeshStandardMaterial({
      color: new THREE.Color(BRAND.floor), roughness: 1, side: THREE.DoubleSide,
    }),
  );
  floor.position.set(H.width_m / 2, H.length_m / 2, 0); // PlaneGeometry lies in XY (normal +Z)
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
