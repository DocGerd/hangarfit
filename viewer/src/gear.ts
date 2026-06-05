// ── gear / cart render constants (#399) ──────────────────────────────────────
// Render *sizes* live here in the viewer layer, never in fleet.yaml (the canonical
// data carries only plane-local wheel POSITIONS, ADR-0013). Values mirror the 2D
// path's constants in visualize.py so 2D and 3D read alike; the remaining three
// (WHEEL_WIDTH_M, LEG_WIDTH_M, CART_PALLET_HEIGHT_M) have no 2D analogue — the
// top-down PNG has no z and draws no gear leg — and are glyph extents chosen to
// read at fuselage scale.
import * as THREE from 'three';
import type { BrandTokens } from './brand-contract.ts';
import type { PlaneData } from './scene-contract.ts';

export const WHEEL_RADIUS_M = 0.18; // visualize._WHEEL_RADIUS_M
export const WHEEL_WIDTH_M = 0.12; // tyre width — 3D-only glyph depth
export const LEG_WIDTH_M = 0.06; // thin gear-leg strut up to the belly — 3D-only glyph
export const CART_PALLET_HALF_EXTENT_M = 0.4; // visualize._CART_PALLET_HALF_EXTENT_M
export const CART_PALLET_HEIGHT_M = 0.12; // dolly deck thickness — 3D-only glyph depth

export interface GearMaterials {
  gearMat: THREE.MeshStandardMaterial;
  palletMat: THREE.MeshStandardMaterial;
}

/** The shared wheel/leg + pallet-deck materials, built ONCE from BRAND (the
 * original created these at module scope; BRAND only exists at runtime, so the
 * single-instance sharing moves into this factory). DoubleSide: the plane Group
 * matrix may be det −1. */
export function makeGearMaterials(BRAND: BrandTokens): GearMaterials {
  const WHEEL_COLOR = new THREE.Color(BRAND.wheel); // brand.WHEEL_COLOR (= visualize._WHEEL_COLOR)
  const CART_DECK_COLOR = new THREE.Color(BRAND.cartDeck); // brand.CART_DECK_COLOR
  return {
    gearMat: new THREE.MeshStandardMaterial({
      color: WHEEL_COLOR, side: THREE.DoubleSide, roughness: 0.6, metalness: 0.1,
    }),
    palletMat: new THREE.MeshStandardMaterial({
      color: CART_DECK_COLOR, side: THREE.DoubleSide, roughness: 0.9, metalness: 0.0,
    }),
  };
}

// Draw gear (a wheel at each point + a short leg up to the belly where there is
// clearance) and, when carted, a pallet deck under each wheel, all parented to the
// plane's affine Group `g` so they inherit the verified plane-local→world transform
// and animate along the tow path for free. Wheel world positions are oracle-checked
// in checkAnchors().
export function addGear(g: THREE.Group, p: PlaneData, mats: GearMaterials): void {
  // Belly = lowest box bottom; the leg rises from the wheel top to it. A plane
  // with no boxes falls back to a wheel-diameter belly so a stub leg still renders.
  let belly = Infinity;
  for (const b of p.boxes) belly = Math.min(belly, b.cz - b.height_m / 2);
  if (!isFinite(belly)) belly = 2 * WHEEL_RADIUS_M;
  const deck = p.on_carts ? CART_PALLET_HEIGHT_M : 0;
  for (const [u, v] of p.wheels) {
    // Cylinder's default axis is +Y = local v (lateral), so the disc lies in the
    // forward/up (u,w) plane — a wheel that rolls forward. No rotation needed.
    const wheelZ = deck + WHEEL_RADIUS_M;
    const wheel = new THREE.Mesh(
      new THREE.CylinderGeometry(WHEEL_RADIUS_M, WHEEL_RADIUS_M, WHEEL_WIDTH_M, 16),
      mats.gearMat,
    );
    wheel.position.set(u, v, wheelZ);
    wheel.castShadow = true;
    g.add(wheel);

    const wheelTop = wheelZ + WHEEL_RADIUS_M;
    const legH = belly - wheelTop;
    if (legH > 0.01) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(LEG_WIDTH_M, LEG_WIDTH_M, legH), mats.gearMat);
      leg.position.set(u, v, wheelTop + legH / 2);
      leg.castShadow = true;
      g.add(leg);
    }
    if (p.on_carts) {
      const pallet = new THREE.Mesh(
        new THREE.BoxGeometry(2 * CART_PALLET_HALF_EXTENT_M, 2 * CART_PALLET_HALF_EXTENT_M, deck),
        mats.palletMat,
      );
      pallet.position.set(u, v, deck / 2);
      pallet.castShadow = true;
      pallet.receiveShadow = true;
      g.add(pallet);
    }
  }
}
