// hangarfit 3D viewer — entry / orchestration (esbuild bundles this + the sibling
// modules into the committed src/hangarfit/_viewer_assets/viewer.js, ADR-0020).
//
// A thin consumer of the hangarfit.scene/v2 contract: it performs NO transform
// math. Every plane-local→world placement arrives as a 2x3 affine [a,b,tx,c,d,ty]
// computed in Python from geometry.local_to_world (the determinant-−1 transform,
// ADR-0002/ADR-0017). The viewer drops each affine into a THREE.Matrix4 and
// assigns it to a statically-built plane group. All colours/opacities are read
// from the injected BRAND blob (Python brand.py) — never hard-coded here (#419).
//
// Two boot modes: a single scene (the `#scene` blob → bootSingle) or, when a
// `#solutions` compare manifest is present (#666), N alternatives the user switches
// between (startCompare). Single mode is behaviour-identical to the pre-#666 viewer.
import * as THREE from 'three';
import { banner, byId, clearBanner } from './dom.ts';
import { createRenderer } from './renderer.ts';
import { addHangar } from './hangar.ts';
import { addPlanes } from './planes.ts';
import { addGroundObjects } from './ground_objects.ts';
import { addTowPaths } from './paths.ts';
import { addEgressLanes } from './egress.ts';
import { checkAnchors } from './anchors.ts';
import { createTimeline, type Timeline } from './timeline.ts';
import { startHud } from './hud.ts';
import { wrapIndex, clampIndex, optionLabels, formatSummary, foundLabel } from './compare.ts';
import type { BrandTokens } from './brand-contract.ts';
import type { CompareManifest, SceneV2 } from './scene-contract.ts';

// One solution's dynamic content: the meshes (under one toggleable Group) + its
// timeline + the label/nose/paths handles the HUD toggles drive.
interface World {
  group: THREE.Group;
  labelMeshes: THREE.Sprite[];
  noseMeshes: THREE.Mesh[];
  setPathsVisible: (on: boolean) => void;
  timeline: Timeline;
}

// #401 honesty banner + actionable readouts, for the ACTIVE scene. Static banner
// text lives in the HTML; we only toggle visibility and fill the numbers (no user
// HTML — set via textContent). Handles both directions so a compare switch onto a
// measured/valid solution clears a prior placeholder/readout.
function setReadouts(scene: SceneV2): void {
  byId('placeholder').hidden = !scene.placeholder;
  const r = scene.readouts;
  const fmtM = (v: number | null): string => (v == null ? 'n/a' : v.toFixed(2) + ' m');
  byId('readouts').textContent = r
    ? 'gap ' + fmtM(r.min_gap_m) + ' · wing-over-tail ' + fmtM(r.min_wing_over_tail_clearance_m)
    : '';
}

// Build one solution's dynamic world under a fresh Group parented to `scene`. The
// hangar shell is built ONCE by the caller (shared across alternatives); everything
// that differs per solution — planes, ground objects, tow paths, egress lanes, the
// timeline — lives here so a compare switch is one `scene.remove(world.group)`.
function buildWorld(scene: THREE.Scene, data: SceneV2, brand: BrandTokens): World {
  byId('legend').textContent = ''; // legend is rebuilt per world (addPlanes/addGroundObjects fill it)
  const group = new THREE.Group();
  scene.add(group);

  const { groups, labelMeshes, noseMeshes } = addPlanes(group, data, brand);
  const { groups: goGroups } = addGroundObjects(group, data);
  const { setVisible: setPathsVisible } = addTowPaths(group, data, brand);
  addEgressLanes(group, data, brand);

  // ── load-time anchor self-check ──────────────────────────────────────────────
  // Must FAIL LOUD (banner), never throw — a throw here aborts module evaluation
  // and blanks the page, the opposite of the ADR-0017 fail-loud contract. The pure
  // compare lives in anchors.ts; this edge banners structural problems / a >1e-6
  // mismatch, and wraps everything so any unforeseen error still surfaces as a banner.
  try {
    const { structural, maxErr } = checkAnchors(data);
    if (structural) {
      banner('TRANSFORM CHECK FAILED (' + structural + ') — do not trust this render.');
    } else if (maxErr > 1e-6) {
      banner(
        'TRANSFORM CHECK FAILED (maxErr=' + maxErr.toExponential(2) +
        '): viewer affine disagrees with the Python oracle — do not trust this render.',
      );
    }
  } catch (e) {
    banner('TRANSFORM CHECK ERRORED: ' + (e as Error).message + ' — do not trust this render.');
  }

  // Movers (#651) animate from the same timeline as planes, so their Groups are passed
  // alongside the plane Groups (both returned per-id by the add* builders above).
  const timeline = createTimeline(data, groups, goGroups);
  return { group, labelMeshes, noseMeshes, setPathsVisible, timeline };
}

// Wire the HUD visibility toggles. `walls` drives the shared hangar; `labels`/`paths`
// drive whatever world `getWorld()` returns (the active solution in compare mode).
function wireToggles(wallMeshes: THREE.Object3D[], getWorld: () => World): void {
  const wallsToggle = byId<HTMLInputElement>('walls');
  wallsToggle.addEventListener('change', () => {
    for (const m of wallMeshes) m.visible = wallsToggle.checked;
  });
  const labelsToggle = byId<HTMLInputElement>('labels');
  labelsToggle.addEventListener('change', () => {
    const on = labelsToggle.checked;
    const w = getWorld();
    for (const m of w.labelMeshes) m.visible = on;
    for (const m of w.noseMeshes) m.visible = on;
  });
  const pathsToggle = byId<HTMLInputElement>('paths');
  pathsToggle.addEventListener('change', () => getWorld().setPathsVisible(pathsToggle.checked));
}

// Re-apply the current toggle states to a freshly-mounted world, so a compare switch
// respects the user's current label/path checkbox state (a new world builds visible).
function applyToggleState(world: World): void {
  const on = byId<HTMLInputElement>('labels').checked;
  for (const m of world.labelMeshes) m.visible = on;
  for (const m of world.noseMeshes) m.visible = on;
  world.setPathsVisible(byId<HTMLInputElement>('paths').checked);
}

// Free a removed world's GPU resources (geometry/material/texture) so repeated
// compare switches don't leak.
function disposeWorld(group: THREE.Object3D): void {
  group.traverse((obj) => {
    const m = obj as THREE.Mesh;
    if (m.geometry) m.geometry.dispose();
    const mat = m.material as THREE.Material | THREE.Material[] | undefined;
    const mats = Array.isArray(mat) ? mat : mat ? [mat] : [];
    for (const one of mats) {
      const tex = (one as THREE.MeshBasicMaterial).map;
      if (tex) tex.dispose();
      one.dispose();
    }
  });
}

function setupStage(
  hangar: SceneV2['hangar'],
  brand: BrandTokens,
): ReturnType<typeof createRenderer> & { wallMeshes: THREE.Object3D[] } {
  const canvas = byId<HTMLCanvasElement>('c');
  const r = createRenderer(canvas, hangar, brand);
  const wallMeshes = addHangar(r.scene, hangar, brand, r.span);
  return { ...r, wallMeshes };
}

function bootSingle(data: SceneV2, brand: BrandTokens): void {
  setReadouts(data);
  const stage = setupStage(data.hangar, brand);
  const world = buildWorld(stage.scene, data, brand);
  wireToggles(stage.wallMeshes, () => world);
  startHud({
    timeline: world.timeline,
    home: stage.home,
    controls: stage.controls,
    renderer: stage.renderer,
    scene: stage.scene,
    cam: stage.cam,
  });
}

function startCompare(manifest: CompareManifest, brand: BrandTokens): void {
  const solutions = manifest.solutions;
  // Alternatives of one scenario share the hangar: build the shell from solution #1.
  const stage = setupStage(solutions[0].scene.hangar, brand);
  let current = 0;
  let world = buildWorld(stage.scene, solutions[0].scene, brand);
  setReadouts(solutions[0].scene);
  wireToggles(stage.wallMeshes, () => world);
  const hud = startHud({
    timeline: world.timeline,
    home: stage.home,
    controls: stage.controls,
    renderer: stage.renderer,
    scene: stage.scene,
    cam: stage.cam,
  });

  // Compare control: fill the <select>, wire change + ←/→ keys, show per-solution metrics.
  const select = byId<HTMLSelectElement>('compare');
  for (const label of optionLabels(solutions)) {
    const opt = document.createElement('option');
    opt.textContent = label;
    select.appendChild(opt);
  }
  const metrics = byId('compare-metrics');
  const showMetrics = (): void => {
    metrics.textContent = foundLabel(manifest) + ' · ' + formatSummary(solutions[current]);
  };

  const mount = (k: number): void => {
    const next = clampIndex(k, solutions.length);
    if (next === current) return;
    current = next;
    select.selectedIndex = current;
    clearBanner(); // drop any prior solution's transform warning before re-checking
    stage.scene.remove(world.group);
    disposeWorld(world.group);
    world = buildWorld(stage.scene, solutions[current].scene, brand);
    applyToggleState(world); // honour the user's current label/path checkbox state
    setReadouts(solutions[current].scene);
    hud.setActiveTimeline(world.timeline); // re-point the render loop, rewind to t=0
    showMetrics();
  };

  select.addEventListener('change', () => mount(select.selectedIndex));
  window.addEventListener('keydown', (e) => {
    // When the <select> is focused the browser already steps it on ←/→ (firing
    // `change` → mount); skip the window handler so a keypress advances once.
    if (e.target === select) return;
    if (e.key === 'ArrowRight') mount(wrapIndex(current, solutions.length, 1));
    else if (e.key === 'ArrowLeft') mount(wrapIndex(current, solutions.length, -1));
  });
  select.selectedIndex = 0;
  showMetrics();
}

const BRAND = JSON.parse(byId('brand').textContent ?? 'null') as BrandTokens;
const solutionsEl = document.getElementById('solutions');
if (solutionsEl) {
  startCompare(JSON.parse(solutionsEl.textContent ?? 'null') as CompareManifest, BRAND);
} else {
  bootSingle(JSON.parse(byId('scene').textContent ?? 'null') as SceneV2, BRAND);
}
