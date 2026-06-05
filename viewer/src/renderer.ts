// ── renderer / scene / camera / lights ───────────────────────────────────────
//
// World convention (matches hangarfit core): x = right along the door wall,
// y = deeper into the hangar, z = up. We make Three.js +Z-up so the affine's
// z-row is identity and box height runs along world up. Reflected matrices
// (det −1) render correctly because every material is DoubleSide.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { banner } from './dom';
import type { BrandTokens } from './brand-contract';
import type { HangarData } from './scene-contract';

export interface RendererBundle {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  cam: THREE.PerspectiveCamera;
  controls: OrbitControls;
  span: number;
  home: () => void;
}

export function createRenderer(
  canvas: HTMLCanvasElement, H: HangarData, BRAND: BrandTokens,
): RendererBundle {
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  } catch (e) {
    banner('WebGL is unavailable in this browser: ' + (e as Error).message);
    throw e;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true; // contact shadows (#400)
  renderer.shadowMap.type = THREE.PCFSoftShadowMap; // soft edges

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BRAND.sceneBg);

  const cam = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 2000);
  cam.up.set(0, 0, 1); // +Z up — set BEFORE OrbitControls reads it.

  const controls = new OrbitControls(cam, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  const span = Math.max(H.width_m, H.length_m);
  const home = (): void => {
    cam.position.set(H.width_m * 0.5, -H.length_m * 0.55, span * 0.95);
    controls.target.set(H.width_m / 2, H.length_m / 2, 0.5);
    controls.update();
  };
  home();

  // Lighting (#400). The key sun casts contact shadows so vertical clearance is
  // legible — a high wing's shadow falling across a neighbour's tail is the whole
  // reason the 3D viewer exists (ADR-0017). A soft fill from the opposite side keeps
  // shaded faces readable, and a (slightly lowered) hemisphere ambient lets shadows
  // darken without going black.
  scene.add(new THREE.HemisphereLight(
    new THREE.Color(BRAND.hemisphereSky), new THREE.Color(BRAND.hemisphereGround), BRAND.hemisphereIntensity,
  ));
  const sun = new THREE.DirectionalLight(new THREE.Color(BRAND.sun), BRAND.sunIntensity);
  sun.position.set(H.width_m * 0.35, -H.length_m * 0.15, span * 1.1);
  sun.target.position.set(H.width_m / 2, H.length_m / 2, 0); // aim at hangar centre
  scene.add(sun.target);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.normalBias = 0.04; // suppress acne on the det-−1 reflected boxes
  const sc = sun.shadow.camera; // ortho frustum sized to the hangar span
  sc.left = -span;
  sc.right = span;
  sc.top = span;
  sc.bottom = -span;
  sc.near = 0.5;
  sc.far = span * 3.5;
  sc.updateProjectionMatrix();
  scene.add(sun);
  // soft fill: pale tint of the horizon accent #3FA3D6, no shadow
  const fill = new THREE.DirectionalLight(new THREE.Color(BRAND.fill), BRAND.fillIntensity);
  fill.position.set(H.width_m * 0.7, H.length_m * 1.2, span * 0.6);
  scene.add(fill);

  return { renderer, scene, cam, controls, span, home };
}
