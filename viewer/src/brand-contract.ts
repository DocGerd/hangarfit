// Typed mirror of the BRAND token blob (Python `brand.py`, ADR-0019 / #419),
// injected by `viewer.py` as `<script id="brand">`. Colours arrive as
// `#RRGGBB` strings (straight into `new THREE.Color(str)`); opacities and
// intensities are plain numbers. Do NOT hard-code `0x` colour literals in the
// viewer — every colour is a brand token (#419).
//
// The complete mirror of `brand.viewer_brand_tokens()`; a Python key-set parity
// test in `tests/test_scene.py` fails if `brand.py` and these keys drift apart
// (the same desync guard as `scene-contract.ts`).

export interface BrandTokens {
  sceneBg: string;

  hemisphereSky: string;
  hemisphereGround: string;
  hemisphereIntensity: number;

  sun: string;
  sunIntensity: number;
  fill: string;
  fillIntensity: number;

  floor: string;
  gridMajor: string;
  gridMinor: string;

  walls: string;
  wallsOpacity: number;
  bay: string;
  bayOpacity: number;

  wheel: string;
  cartDeck: string;

  conflict: string;
  labelConflictChip: string;
  labelChipBg: string;
  labelText: string;
}
