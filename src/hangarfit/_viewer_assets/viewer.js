// hangarfit 3D viewer — a thin consumer of the hangarfit.scene/v1 contract.
//
// It performs NO transform math: every plane-local→world placement arrives as a
// 2x3 affine [a,b,tx,c,d,ty] computed in Python from geometry.local_to_world
// (the determinant-−1 transform, ADR-0002/ADR-0017). The viewer drops each
// affine into a THREE.Matrix4 and assigns it to a statically-built plane group.
//
// World convention (matches hangarfit core): x = right along the door wall,
// y = deeper into the hangar, z = up. We make Three.js +Z-up so the affine's
// z-row is identity and box height runs along world up. Reflected matrices
// (det −1) render correctly because every material is DoubleSide.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const SCENE = JSON.parse(document.getElementById('scene').textContent);
const H = SCENE.hangar;

function banner(msg) {
  const b = document.getElementById('banner');
  b.hidden = false;
  b.textContent = msg;
}

// ── renderer / scene / camera ────────────────────────────────────────────────
const canvas = document.getElementById('c');
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
} catch (e) {
  banner('WebGL is unavailable in this browser: ' + e.message);
  throw e;
}
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d0e10);

const cam = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 2000);
cam.up.set(0, 0, 1); // +Z up — set BEFORE OrbitControls reads it.

const controls = new OrbitControls(cam, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;

const span = Math.max(H.width_m, H.length_m);
function home() {
  cam.position.set(H.width_m * 0.5, -H.length_m * 0.55, span * 0.95);
  controls.target.set(H.width_m / 2, H.length_m / 2, 0.5);
  controls.update();
}
home();

scene.add(new THREE.HemisphereLight(0xffffff, 0x202428, 1.15));
const sun = new THREE.DirectionalLight(0xffffff, 0.75);
sun.position.set(H.width_m * 0.3, -H.length_m * 0.2, span);
scene.add(sun);

// ── affine → Matrix4 (z-row identity; det may be −1, that's intentional) ─────
function affineMatrix(aff) {
  const [a, b, tx, c, d, ty] = aff;
  const m = new THREE.Matrix4();
  // row-major: maps local (u,v,w,1) → world (a·u+b·v+tx, c·u+d·v+ty, w).
  m.set(
    a, b, 0, tx,
    c, d, 0, ty,
    0, 0, 1, 0,
    0, 0, 0, 1,
  );
  return m;
}

// ── hangar: floor, grid, walls (split at door), maintenance bay ──────────────
const WALL_H = 3.0;
const wallMeshes = [];
function addHangar() {
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(H.width_m, H.length_m),
    new THREE.MeshStandardMaterial({ color: 0x16181c, roughness: 1, side: THREE.DoubleSide }),
  );
  floor.position.set(H.width_m / 2, H.length_m / 2, 0); // PlaneGeometry lies in XY (normal +Z)
  scene.add(floor);

  const grid = new THREE.GridHelper(span, Math.round(span), 0x3b4046, 0x23262b);
  grid.rotation.x = Math.PI / 2; // GridHelper is in XZ by default → rotate into XY
  grid.position.set(H.width_m / 2, H.length_m / 2, 0.003);
  scene.add(grid);

  const t = 0.08;
  const addWall = (sx, sy, x, y) => {
    const m = new THREE.MeshStandardMaterial({
      color: 0x4b5560, transparent: true, opacity: 0.16, side: THREE.DoubleSide,
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
      new THREE.MeshStandardMaterial({ color: 0x922b21, transparent: true, opacity: 0.34 }),
    );
    bm.position.set(bay.center_x_m, H.length_m - bay.depth_m / 2, WALL_H / 2);
    scene.add(bm);
  }
}
addHangar();
document.getElementById('walls').addEventListener('change', (e) => {
  wallMeshes.forEach((m) => { m.visible = e.target.checked; });
});

// ── planes: one Group of boxes each, built ONCE in plane-local coords ────────
const CONFLICT = 0xc8442c;
const groups = {};
const legend = document.getElementById('legend');
for (const p of SCENE.planes) {
  const g = new THREE.Group();
  g.matrixAutoUpdate = false; // we drive g.matrix per frame from the affine
  const conflicted = SCENE.conflicts.includes(p.id);
  const colour = new THREE.Color(conflicted ? CONFLICT : p.color);
  for (const b of p.boxes) {
    const isWing = b.kind === 'wing';
    const mat = new THREE.MeshStandardMaterial({
      color: colour,
      side: THREE.DoubleSide,           // reflected (det −1) group matrix → show both faces
      transparent: isWing,              // translucent wings reveal vertical stacking
      opacity: isWing ? 0.5 : 0.95,
      roughness: 0.7,
      metalness: 0.05,
    });
    // local X = u (forward/length), local Y = v (right/width), local Z = w (height).
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(b.length_m, b.width_m, b.height_m), mat);
    mesh.position.set(b.cx, b.cy, b.cz);
    mesh.rotation.z = THREE.MathUtils.degToRad(b.angle_deg); // CCW about local up, as oriented_rect
    g.add(mesh);
  }
  groups[p.id] = g;
  scene.add(g);

  // Build the legend chip with safe DOM methods (no innerHTML): plane ids come
  // from user YAML, so avoid any HTML-injection surface even on a local file.
  const sw = document.createElement('span');
  sw.className = 'sw';
  const dot = document.createElement('i');
  dot.style.background = conflicted ? '#c8442c' : p.color;
  sw.appendChild(dot);
  sw.appendChild(document.createTextNode(p.id));
  legend.appendChild(sw);
}

// ── load-time anchor self-check: recompute final world corners and compare ───
function boxCornersLocal(b) {
  const h = THREE.MathUtils.degToRad(b.angle_deg);
  const cs = Math.cos(h), sn = Math.sin(h);
  const hl = b.length_m / 2, hw = b.width_m / 2;
  // oriented_rect corner order, rotated CCW about (cx,cy): (+hl,-hw),(+hl,+hw),(-hl,+hw),(-hl,-hw)
  return [[hl, -hw], [hl, hw], [-hl, hw], [-hl, -hw]].map(
    ([lx, ly]) => [b.cx + lx * cs - ly * sn, b.cy + lx * sn + ly * cs],
  );
}
function applyAffine(aff, u, v) {
  const [a, b, tx, c, d, ty] = aff;
  return [a * u + b * v + tx, c * u + d * v + ty];
}
(function checkAnchors() {
  let maxErr = 0;
  for (const p of SCENE.planes) {
    const aff = SCENE.final_poses[p.id];
    const want = SCENE.anchors[p.id];
    if (!aff || !want) continue;
    p.boxes.forEach((b, bi) => {
      boxCornersLocal(b).forEach(([u, v], ci) => {
        const [wx, wy] = applyAffine(aff, u, v);
        maxErr = Math.max(maxErr, Math.abs(wx - want[bi][ci][0]), Math.abs(wy - want[bi][ci][1]));
      });
    });
  }
  if (maxErr > 1e-6) {
    banner(
      'TRANSFORM CHECK FAILED (maxErr=' + maxErr.toExponential(2) +
      '): viewer affine disagrees with the Python oracle — do not trust this render.',
    );
  }
})();

// ── timeline state machine (hidden → animating → parked) ─────────────────────
const TL = SCENE.timeline;
const SEGS = TL.segments;
const TOTAL = TL.total_s;
const hasAnim = TOTAL > 0 && SEGS.length > 0;
const segByPlane = {};
for (const s of SEGS) segByPlane[s.plane_id] = s;

function affineAt(pid, t) {
  const seg = segByPlane[pid];
  if (!seg) return { vis: true, aff: SCENE.final_poses[pid] }; // static plane
  if (t < seg.start_s) return { vis: false, aff: null };       // not entered yet
  if (t >= seg.end_s) return { vis: true, aff: SCENE.final_poses[pid] };
  const frac = (t - seg.start_s) / (seg.end_s - seg.start_s);
  const i = Math.round(frac * (seg.samples.length - 1));
  return { vis: true, aff: seg.samples[i] };
}

function applyTime(t) {
  for (const p of SCENE.planes) {
    const { vis, aff } = affineAt(p.id, t);
    const g = groups[p.id];
    g.visible = vis;
    if (vis && aff) {
      g.matrix.copy(affineMatrix(aff));
      g.matrixWorldNeedsUpdate = true;
    }
  }
  const cur = SEGS.find((s) => t >= s.start_s && t < s.end_s);
  document.getElementById('active').textContent = cur ? 'towing: ' + cur.plane_id : '';
  document.getElementById('clock').textContent = t.toFixed(1) + 's';
}

// ── HUD wiring ───────────────────────────────────────────────────────────────
let t = 0;
let playing = false;
const speed = 1;
const scrub = document.getElementById('scrub');
const playBtn = document.getElementById('play');

if (!hasAnim) {
  ['play', 'prev', 'next', 'scrub'].forEach((id) => { document.getElementById(id).disabled = true; });
}

scrub.addEventListener('input', () => {
  t = (scrub.value / 1000) * TOTAL;
  playing = false;
  playBtn.textContent = '▶';
  applyTime(t);
});
playBtn.addEventListener('click', () => {
  if (!hasAnim) return;
  playing = !playing;
  playBtn.textContent = playing ? '❚❚' : '▶';
  if (t >= TOTAL) t = 0;
});
function stepTo(dir) {
  const bounds = [0, ...SEGS.map((s) => s.end_s)];
  let i = bounds.findIndex((b) => b > t + 1e-6);
  if (i < 0) i = bounds.length - 1;
  t = dir > 0 ? bounds[Math.min(i, bounds.length - 1)] : bounds[Math.max(0, i - 2)];
  playing = false;
  playBtn.textContent = '▶';
  if (hasAnim) scrub.value = String((t / TOTAL) * 1000);
  applyTime(t);
}
document.getElementById('next').addEventListener('click', () => stepTo(1));
document.getElementById('prev').addEventListener('click', () => stepTo(-1));
document.getElementById('reset').addEventListener('click', home);

window.addEventListener('resize', () => {
  cam.aspect = window.innerWidth / window.innerHeight;
  cam.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// ── render loop ──────────────────────────────────────────────────────────────
let last = performance.now();
function loop(now) {
  requestAnimationFrame(loop);
  const dt = (now - last) / 1000;
  last = now;
  if (playing && hasAnim) {
    t += dt * speed;
    if (t >= TOTAL) {
      t = TOTAL;
      playing = false;
      playBtn.textContent = '▶';
    }
    scrub.value = String((t / TOTAL) * 1000);
  }
  applyTime(t);
  controls.update();
  renderer.render(scene, cam);
}
applyTime(0);
requestAnimationFrame(loop);
