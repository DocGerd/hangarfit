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
// This file keeps viewer.js's exact init ORDER; the heavy lifting lives in the
// sibling section modules so the port is reviewable section-by-section.
import { banner, byId } from './dom.ts';
import { createRenderer } from './renderer.ts';
import { addHangar } from './hangar.ts';
import { addPlanes } from './planes.ts';
import { addGroundObjects } from './ground_objects.ts';
import { addTowPaths } from './paths.ts';
import { addEgressLanes } from './egress.ts';
import { checkAnchors } from './anchors.ts';
import { createTimeline } from './timeline.ts';
import { startHud } from './hud.ts';
import type { BrandTokens } from './brand-contract.ts';
import type { SceneV2 } from './scene-contract.ts';

const SCENE = JSON.parse(byId('scene').textContent ?? 'null') as SceneV2;
const BRAND = JSON.parse(byId('brand').textContent ?? 'null') as BrandTokens;
const H = SCENE.hangar;

// #401 honesty banner + actionable readouts. The banner text is static (set in
// the HTML); we only unhide it. Readouts are numbers from the scene — no user
// HTML, set via textContent.
if (SCENE.placeholder) byId('placeholder').hidden = false;
if (SCENE.readouts) {
  const fmtM = (v: number | null): string => (v == null ? 'n/a' : v.toFixed(2) + ' m');
  byId('readouts').textContent =
    'gap ' + fmtM(SCENE.readouts.min_gap_m) +
    ' · wing-over-tail ' + fmtM(SCENE.readouts.min_wing_over_tail_clearance_m);
}

// renderer / scene / camera / lights
const canvas = byId<HTMLCanvasElement>('c');
const { renderer, scene, cam, controls, span, home } = createRenderer(canvas, H, BRAND);

// hangar + walls toggle
const wallMeshes = addHangar(scene, H, BRAND, span);
const wallsToggle = byId<HTMLInputElement>('walls');
wallsToggle.addEventListener('change', () => {
  for (const m of wallMeshes) m.visible = wallsToggle.checked;
});

// planes (boxes + gear + label/nose) + labels toggle
const { groups, labelMeshes, noseMeshes } = addPlanes(scene, SCENE, BRAND);
// Labels + nose arrows share one HUD toggle (#400). They are children of each
// plane Group, so a hidden (not-yet-entered) plane hides its label too.
const labelsToggle = byId<HTMLInputElement>('labels');
labelsToggle.addEventListener('change', () => {
  const on = labelsToggle.checked;
  for (const m of labelMeshes) m.visible = on;
  for (const m of noseMeshes) m.visible = on;
});

// ground objects (#606): fixed obstacles + placed movers. Fixed obstacles are
// always-on floor bodies; placed-routed movers animate along their drive path via
// the timeline (#651) — so we keep their Groups to drive per frame. A GO-free
// scene builds nothing here.
const { groups: goGroups } = addGroundObjects(scene, SCENE);

// floor tow paths (#505) + paths toggle. One coloured floor line per plane's
// tow route (the 3D analogue of `solve --render-paths`), default ON so the route
// — and the apron slide-in (ty<0) — is legible at a glance. A static / un-routed
// scene draws no lines; the toggle stays harmlessly inert in that case.
const { setVisible: setPathsVisible } = addTowPaths(scene, SCENE, BRAND);
const pathsToggle = byId<HTMLInputElement>('paths');
pathsToggle.addEventListener('change', () => setPathsVisible(pathsToggle.checked));

// hard-door egress lane(s) (#652): the drive-out corridor a rescue vehicle (the
// VW Caddy) must keep clear, as a dashed amber floor line. A safety annotation —
// always on, no toggle. An egress-lane-free scene builds nothing here.
addEgressLanes(scene, SCENE, BRAND);

// ── load-time anchor self-check ──────────────────────────────────────────────
// Must FAIL LOUD (banner), never throw — a throw here aborts module evaluation
// and blanks the page with no signal, the opposite of the ADR-0017 fail-loud
// contract. The pure compare lives in anchors.ts; this edge banners structural
// problems / a >1e-6 mismatch, and wraps everything so any unforeseen error
// still surfaces as a banner.
try {
  const { structural, maxErr } = checkAnchors(SCENE);
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

// timeline + HUD wiring + render loop. Movers (#651) animate from the same
// timeline as planes, so their Groups are passed alongside the plane Groups.
const timeline = createTimeline(SCENE, groups, goGroups);
startHud({ timeline, home, controls, renderer, scene, cam });
