// Typed mirror of the BRAND token blob (Python `brand.py`, ADR-0019 / #419),
// injected by `viewer.py` as `<script id="brand">`. Colours arrive as
// `#RRGGBB` strings (straight into `new THREE.Color(str)`); opacities and
// intensities are plain numbers. Do NOT hard-code `0x` colour literals in the
// viewer — every colour is a brand token (#419).
//
// LEAN for #439 (only the tokens the renderer reads); the full token parity
// test against `brand.py` lands in #440.

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
