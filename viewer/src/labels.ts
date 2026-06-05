// ── identity cues (#400): billboarded id label + nose cone ───────────────────
// HUD-toggleable. A billboarded id label and a nose cone at the plane-local +x
// tip show which plane is which and which way it faces.
import * as THREE from 'three';
import type { BrandTokens } from './brand-contract.ts';
import type { PlaneData } from './scene-contract.ts';

export function makeLabel(text: string, BRAND: BrandTokens, conflicted = false): THREE.Sprite {
  // Plane ids are machine output → mono (brand). A conflicted plane carries the
  // non-colour "never hue alone" cue 3D can't hatch: a " ⚠ conflict" suffix and
  // the conflict-ink chip instead of the surface glass (BRAND.md §3).
  const shown = conflicted ? text + ' ⚠ conflict' : text;
  const fontPx = 64, padX = 14, padY = 8;
  const fontStack = "px ui-monospace, 'SF Mono', Menlo, monospace";
  const measure = document.createElement('canvas').getContext('2d')!;
  measure.font = fontPx + fontStack;
  const tw = Math.ceil(measure.measureText(shown).width);
  const canvas = document.createElement('canvas');
  canvas.width = tw + padX * 2;
  canvas.height = fontPx + padY * 2;
  const ctx = canvas.getContext('2d')!;
  ctx.font = fontPx + fontStack;
  ctx.textBaseline = 'middle';
  ctx.fillStyle = conflicted ? BRAND.labelConflictChip : BRAND.labelChipBg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = BRAND.labelText;
  ctx.fillText(shown, padX, canvas.height / 2); // SAFE: canvas fillText, never innerHTML (ids are user YAML)
  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 4;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }),
  );
  const hWorld = 0.9; // label height in world metres
  sprite.scale.set((hWorld * canvas.width) / canvas.height, hWorld, 1);
  sprite.renderOrder = 999; // float above geometry (depthTest off)
  return sprite;
}

/** Add the id label sprite + nose cone to a plane Group, pushing them onto the
 * shared toggle arrays so the HUD "labels" switch can hide them all at once. */
export function addLabelAndNose(
  g: THREE.Group,
  p: PlaneData,
  colour: THREE.Color,
  conflicted: boolean,
  BRAND: BrandTokens,
  labelMeshes: THREE.Sprite[],
  noseMeshes: THREE.Mesh[],
): void {
  let maxTop = 0, sx = 0, sy = 0, noseX = -Infinity, noseZ = 0;
  for (const b of p.boxes) {
    maxTop = Math.max(maxTop, b.cz + b.height_m / 2);
    sx += b.cx;
    sy += b.cy;
    noseX = Math.max(noseX, b.cx + b.length_m / 2);
    if (b.kind === 'fuselage_front') noseZ = b.cz;
  }
  const n = p.boxes.length || 1;
  if (!isFinite(noseX)) noseX = 0;
  if (noseZ === 0) noseZ = maxTop * 0.5;

  const label = makeLabel(p.id, BRAND, conflicted);
  label.position.set(sx / n, sy / n, maxTop + 1.0); // above the plane, in plane-local
  g.add(label);
  labelMeshes.push(label);

  const noseLen = 0.7, noseR = 0.28;
  const nose = new THREE.Mesh(
    new THREE.ConeGeometry(noseR, noseLen, 14),
    new THREE.MeshStandardMaterial({
      color: colour, emissive: colour.clone().multiplyScalar(0.25),
      side: THREE.DoubleSide, roughness: 0.5, metalness: 0.1,
    }),
  );
  nose.rotation.z = -Math.PI / 2; // default +Y tip → +x (plane-local nose)
  nose.position.set(noseX + noseLen / 2, 0, noseZ);
  nose.castShadow = true;
  g.add(nose);
  noseMeshes.push(nose);
}
