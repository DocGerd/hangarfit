// src/main.ts
import * as THREE11 from "three";

// src/dom.ts
function byId(id) {
  const el = document.getElementById(id);
  if (el === null) throw new Error(`viewer: missing #${id} element`);
  return el;
}
function banner(msg) {
  const b = byId("banner");
  b.hidden = false;
  b.textContent = msg;
}
function clearBanner() {
  const b = byId("banner");
  b.hidden = true;
  b.textContent = "";
}
function disableControl(id) {
  byId(id).disabled = true;
}
function enableControl(id) {
  byId(id).disabled = false;
}

// src/renderer.ts
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
function createRenderer(canvas, H, BRAND2) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  } catch (e) {
    banner("WebGL is unavailable in this browser: " + e.message);
    throw e;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BRAND2.sceneBg);
  const cam = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 2e3);
  cam.up.set(0, 0, 1);
  const controls = new OrbitControls(cam, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  const span = Math.max(H.width_m, H.length_m);
  const home = () => {
    cam.position.set(H.width_m * 0.5, -H.length_m * 0.55, span * 0.95);
    controls.target.set(H.width_m / 2, H.length_m / 2, 0.5);
    controls.update();
  };
  home();
  scene.add(new THREE.HemisphereLight(
    new THREE.Color(BRAND2.hemisphereSky),
    new THREE.Color(BRAND2.hemisphereGround),
    BRAND2.hemisphereIntensity
  ));
  const sun = new THREE.DirectionalLight(new THREE.Color(BRAND2.sun), BRAND2.sunIntensity);
  sun.position.set(H.width_m * 0.35, -H.length_m * 0.15, span * 1.1);
  sun.target.position.set(H.width_m / 2, H.length_m / 2, 0);
  scene.add(sun.target);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.normalBias = 0.04;
  const sc = sun.shadow.camera;
  sc.left = -span;
  sc.right = span;
  sc.top = span;
  sc.bottom = -span;
  sc.near = 0.5;
  sc.far = span * 3.5;
  sc.updateProjectionMatrix();
  scene.add(sun);
  const fill = new THREE.DirectionalLight(new THREE.Color(BRAND2.fill), BRAND2.fillIntensity);
  fill.position.set(H.width_m * 0.7, H.length_m * 1.2, span * 0.6);
  scene.add(fill);
  return { renderer, scene, cam, controls, span, home };
}

// src/hangar.ts
import * as THREE2 from "three";
var WALL_H = 3;
function addHangar(scene, H, BRAND2, span) {
  const wallMeshes = [];
  const notches = H.structural_notches ?? [];
  const floorMat = new THREE2.MeshStandardMaterial({
    color: new THREE2.Color(BRAND2.floor),
    roughness: 1,
    side: THREE2.DoubleSide
  });
  let floor;
  if (notches.length === 0) {
    floor = new THREE2.Mesh(new THREE2.PlaneGeometry(H.width_m, H.length_m), floorMat);
    floor.position.set(H.width_m / 2, H.length_m / 2, 0);
  } else {
    const shape = new THREE2.Shape();
    shape.moveTo(0, 0);
    shape.lineTo(H.width_m, 0);
    shape.lineTo(H.width_m, H.length_m);
    shape.lineTo(0, H.length_m);
    shape.closePath();
    for (const n of notches) {
      const hole = new THREE2.Path();
      hole.moveTo(n.x_min_m, n.y_min_m);
      hole.lineTo(n.x_max_m, n.y_min_m);
      hole.lineTo(n.x_max_m, n.y_max_m);
      hole.lineTo(n.x_min_m, n.y_max_m);
      hole.closePath();
      shape.holes.push(hole);
    }
    floor = new THREE2.Mesh(new THREE2.ShapeGeometry(shape), floorMat);
  }
  floor.receiveShadow = true;
  scene.add(floor);
  const grid = new THREE2.GridHelper(
    span,
    Math.round(span),
    new THREE2.Color(BRAND2.gridMajor),
    new THREE2.Color(BRAND2.gridMinor)
  );
  grid.rotation.x = Math.PI / 2;
  grid.position.set(H.width_m / 2, H.length_m / 2, 3e-3);
  scene.add(grid);
  const t = 0.08;
  const addWall = (sx, sy, x, y) => {
    const m = new THREE2.MeshStandardMaterial({
      color: new THREE2.Color(BRAND2.walls),
      transparent: true,
      opacity: BRAND2.wallsOpacity,
      side: THREE2.DoubleSide
    });
    const mesh = new THREE2.Mesh(new THREE2.BoxGeometry(sx, sy, WALL_H), m);
    mesh.position.set(x, y, WALL_H / 2);
    scene.add(mesh);
    wallMeshes.push(mesh);
  };
  addWall(t, H.length_m, 0, H.length_m / 2);
  addWall(t, H.length_m, H.width_m, H.length_m / 2);
  addWall(H.width_m, t, H.width_m / 2, H.length_m);
  const dl = H.door.center_x_m - H.door.width_m / 2;
  const dr = H.door.center_x_m + H.door.width_m / 2;
  if (dl > 1e-6) addWall(dl, t, dl / 2, 0);
  if (dr < H.width_m - 1e-6) addWall(H.width_m - dr, t, (dr + H.width_m) / 2, 0);
  const eps = 1e-6;
  for (const n of notches) {
    const cx = (n.x_min_m + n.x_max_m) / 2;
    const cy = (n.y_min_m + n.y_max_m) / 2;
    const w = n.x_max_m - n.x_min_m;
    const l = n.y_max_m - n.y_min_m;
    if (n.x_min_m > eps) addWall(t, l, n.x_min_m, cy);
    if (n.x_max_m < H.width_m - eps) addWall(t, l, n.x_max_m, cy);
    if (n.y_min_m > eps) addWall(w, t, cx, n.y_min_m);
    if (n.y_max_m < H.length_m - eps) addWall(w, t, cx, n.y_max_m);
  }
  const bay = H.maintenance_bay;
  if (bay && bay.closed) {
    const bm = new THREE2.Mesh(
      new THREE2.BoxGeometry(bay.width_m, bay.depth_m, WALL_H),
      new THREE2.MeshStandardMaterial({
        color: new THREE2.Color(BRAND2.bay),
        transparent: true,
        opacity: BRAND2.bayOpacity
      })
    );
    bm.position.set(bay.center_x_m, H.length_m - bay.depth_m / 2, WALL_H / 2);
    scene.add(bm);
  }
  return wallMeshes;
}

// src/planes.ts
import * as THREE5 from "three";

// src/gear.ts
import * as THREE3 from "three";
var WHEEL_RADIUS_M = 0.18;
var WHEEL_WIDTH_M = 0.12;
var LEG_WIDTH_M = 0.06;
var CART_PALLET_HALF_EXTENT_M = 0.4;
var CART_PALLET_HEIGHT_M = 0.12;
function makeGearMaterials(BRAND2) {
  const WHEEL_COLOR = new THREE3.Color(BRAND2.wheel);
  const CART_DECK_COLOR = new THREE3.Color(BRAND2.cartDeck);
  return {
    gearMat: new THREE3.MeshStandardMaterial({
      color: WHEEL_COLOR,
      side: THREE3.DoubleSide,
      roughness: 0.6,
      metalness: 0.1
    }),
    palletMat: new THREE3.MeshStandardMaterial({
      color: CART_DECK_COLOR,
      side: THREE3.DoubleSide,
      roughness: 0.9,
      metalness: 0
    })
  };
}
function addGear(g, p, mats) {
  let belly = Infinity;
  for (const b of p.boxes) belly = Math.min(belly, b.cz - b.height_m / 2);
  if (!isFinite(belly)) belly = 2 * WHEEL_RADIUS_M;
  const deck = p.on_carts ? CART_PALLET_HEIGHT_M : 0;
  for (const [u, v] of p.wheels) {
    const wheelZ = deck + WHEEL_RADIUS_M;
    const wheel = new THREE3.Mesh(
      new THREE3.CylinderGeometry(WHEEL_RADIUS_M, WHEEL_RADIUS_M, WHEEL_WIDTH_M, 16),
      mats.gearMat
    );
    wheel.position.set(u, v, wheelZ);
    wheel.castShadow = true;
    g.add(wheel);
    const wheelTop = wheelZ + WHEEL_RADIUS_M;
    const legH = belly - wheelTop;
    if (legH > 0.01) {
      const leg = new THREE3.Mesh(new THREE3.BoxGeometry(LEG_WIDTH_M, LEG_WIDTH_M, legH), mats.gearMat);
      leg.position.set(u, v, wheelTop + legH / 2);
      leg.castShadow = true;
      g.add(leg);
    }
    if (p.on_carts) {
      const pallet = new THREE3.Mesh(
        new THREE3.BoxGeometry(2 * CART_PALLET_HALF_EXTENT_M, 2 * CART_PALLET_HALF_EXTENT_M, deck),
        mats.palletMat
      );
      pallet.position.set(u, v, deck / 2);
      pallet.castShadow = true;
      pallet.receiveShadow = true;
      g.add(pallet);
    }
  }
}

// src/labels.ts
import * as THREE4 from "three";
function makeLabel(text, BRAND2, conflicted = false) {
  const shown = conflicted ? text + " ⚠ conflict" : text;
  const fontPx = 64, padX = 14, padY = 8;
  const fontStack = "px ui-monospace, 'SF Mono', Menlo, monospace";
  const measure = document.createElement("canvas").getContext("2d");
  measure.font = fontPx + fontStack;
  const tw = Math.ceil(measure.measureText(shown).width);
  const canvas = document.createElement("canvas");
  canvas.width = tw + padX * 2;
  canvas.height = fontPx + padY * 2;
  const ctx = canvas.getContext("2d");
  ctx.font = fontPx + fontStack;
  ctx.textBaseline = "middle";
  ctx.fillStyle = conflicted ? BRAND2.labelConflictChip : BRAND2.labelChipBg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = BRAND2.labelText;
  ctx.fillText(shown, padX, canvas.height / 2);
  const tex = new THREE4.CanvasTexture(canvas);
  tex.anisotropy = 4;
  const sprite = new THREE4.Sprite(
    new THREE4.SpriteMaterial({ map: tex, transparent: true, depthTest: false })
  );
  const hWorld = 0.9;
  sprite.scale.set(hWorld * canvas.width / canvas.height, hWorld, 1);
  sprite.renderOrder = 999;
  return sprite;
}
function addLabelAndNose(g, p, colour, conflicted, BRAND2, labelMeshes, noseMeshes) {
  let maxTop = 0, sx = 0, sy = 0, noseX = -Infinity, noseZ = 0;
  for (const b of p.boxes) {
    maxTop = Math.max(maxTop, b.cz + b.height_m / 2);
    sx += b.cx;
    sy += b.cy;
    noseX = Math.max(noseX, b.cx + b.length_m / 2);
    if (b.kind === "fuselage_front") noseZ = b.cz;
  }
  const n = p.boxes.length || 1;
  if (!isFinite(noseX)) noseX = 0;
  if (noseZ === 0) noseZ = maxTop * 0.5;
  const label = makeLabel(p.id, BRAND2, conflicted);
  label.position.set(sx / n, sy / n, maxTop + 1);
  g.add(label);
  labelMeshes.push(label);
  const noseLen = 0.7, noseR = 0.28;
  const nose = new THREE4.Mesh(
    new THREE4.ConeGeometry(noseR, noseLen, 14),
    new THREE4.MeshStandardMaterial({
      color: colour,
      emissive: colour.clone().multiplyScalar(0.25),
      side: THREE4.DoubleSide,
      roughness: 0.5,
      metalness: 0.1
    })
  );
  nose.rotation.z = -Math.PI / 2;
  nose.position.set(noseX + noseLen / 2, 0, noseZ);
  nose.castShadow = true;
  g.add(nose);
  noseMeshes.push(nose);
}

// src/planes.ts
function boxMaterial(b, colour) {
  const base = { color: colour, side: THREE5.DoubleSide, roughness: 0.7, metalness: 0.05 };
  if (b.kind === "wing") {
    return new THREE5.MeshStandardMaterial({ ...base, transparent: true, opacity: 0.5 });
  }
  if (b.kind === "strut") {
    return new THREE5.MeshStandardMaterial({ ...base, roughness: 0.35, metalness: 0.85 });
  }
  if (b.kind === "fuselage_front") {
    return new THREE5.MeshStandardMaterial({ ...base, color: colour.clone().multiplyScalar(0.55) });
  }
  return new THREE5.MeshStandardMaterial(base);
}
function boxMesh(b, colour) {
  let mesh;
  if (b.vertices !== null) {
    const shape = new THREE5.Shape();
    const vs = b.vertices;
    shape.moveTo(vs[0][0], vs[0][1]);
    for (let i = 1; i < vs.length; i++) shape.lineTo(vs[i][0], vs[i][1]);
    shape.closePath();
    mesh = new THREE5.Mesh(
      new THREE5.ExtrudeGeometry(shape, { depth: b.height_m, bevelEnabled: false }),
      boxMaterial(b, colour)
    );
    mesh.position.z = b.z_band[0];
  } else {
    mesh = new THREE5.Mesh(
      new THREE5.BoxGeometry(b.length_m, b.width_m, b.height_m),
      boxMaterial(b, colour)
    );
    mesh.position.set(b.cx, b.cy, b.cz);
    mesh.rotation.z = THREE5.MathUtils.degToRad(b.angle_deg);
  }
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}
function addPlanes(scene, SCENE, BRAND2) {
  const CONFLICT = BRAND2.conflict;
  const groups = {};
  const labelMeshes = [];
  const noseMeshes = [];
  const legend = byId("legend");
  const gearMats = makeGearMaterials(BRAND2);
  for (const p of SCENE.planes) {
    const g = new THREE5.Group();
    g.matrixAutoUpdate = false;
    const conflicted = SCENE.conflicts.includes(p.id);
    const colour = new THREE5.Color(conflicted ? CONFLICT : p.color);
    for (const b of p.boxes) g.add(boxMesh(b, colour));
    addGear(g, p, gearMats);
    addLabelAndNose(g, p, colour, conflicted, BRAND2, labelMeshes, noseMeshes);
    groups[p.id] = g;
    scene.add(g);
    const sw = document.createElement("span");
    sw.className = "sw";
    const dot = document.createElement("i");
    dot.style.background = conflicted ? BRAND2.conflict : p.color;
    sw.appendChild(dot);
    sw.appendChild(document.createTextNode(p.id));
    legend.appendChild(sw);
  }
  return { groups, labelMeshes, noseMeshes };
}

// src/ground_objects.ts
import * as THREE7 from "three";

// src/affine.ts
import * as THREE6 from "three";
function affineMatrix(aff) {
  const [a, b, tx, c, d, ty] = aff;
  const m = new THREE6.Matrix4();
  m.set(
    a,
    b,
    0,
    tx,
    c,
    d,
    0,
    ty,
    0,
    0,
    1,
    0,
    0,
    0,
    0,
    1
  );
  return m;
}
function applyAffine(aff, u, v) {
  const [a, b, tx, c, d, ty] = aff;
  return [a * u + b * v + tx, c * u + d * v + ty];
}

// src/ground_objects.ts
function addGroundObjects(scene, SCENE) {
  const groups = {};
  const legend = byId("legend");
  for (const go of SCENE.ground_objects) {
    const g = new THREE7.Group();
    g.matrixAutoUpdate = false;
    const colour = new THREE7.Color(go.color);
    for (const b of go.boxes) g.add(boxMesh(b, colour));
    g.matrix.copy(affineMatrix(go.final_pose));
    g.matrixWorldNeedsUpdate = true;
    groups[go.id] = g;
    scene.add(g);
    const sw = document.createElement("span");
    sw.className = "sw";
    const dot = document.createElement("i");
    dot.style.background = go.color;
    sw.appendChild(dot);
    const klass = go.object_class === "fixed_obstacle" ? "obstacle" : "mover";
    const tag = go.hard_door_mover ? `${go.id} (${klass} ⮕ door)` : `${go.id} (${klass})`;
    sw.appendChild(document.createTextNode(tag));
    legend.appendChild(sw);
  }
  return { groups };
}

// src/paths.ts
import * as THREE8 from "three";
var TX = 2;
var TY = 5;
function pathPoints(seg) {
  return seg.samples.map((s) => [s[TX], s[TY]]);
}
function addTowPaths(scene, SCENE, BRAND2) {
  const Z_OFFSET = 0.02;
  const segsByPlane = {};
  for (const s of SCENE.timeline.segments) (segsByPlane[s.plane_id] ??= []).push(s);
  const lines = [];
  for (const p of SCENE.planes) {
    const segs = segsByPlane[p.id];
    if (!segs) continue;
    const conflicted = SCENE.conflicts.includes(p.id);
    const colour = new THREE8.Color(conflicted ? BRAND2.conflict : p.color);
    for (const seg of segs) {
      const pts = pathPoints(seg);
      if (pts.length < 2) continue;
      const geom = new THREE8.BufferGeometry().setFromPoints(
        pts.map(([x, y]) => new THREE8.Vector3(x, y, Z_OFFSET))
      );
      const line = new THREE8.Line(geom, new THREE8.LineBasicMaterial({ color: colour }));
      scene.add(line);
      lines.push(line);
    }
  }
  const setVisible = (on) => {
    for (const l of lines) l.visible = on;
  };
  return { lines, setVisible };
}

// src/egress.ts
import * as THREE9 from "three";
function addEgressLanes(scene, SCENE, BRAND2) {
  const Z_OFFSET = 0.025;
  const colour = new THREE9.Color(BRAND2.egressLane);
  const lines = [];
  for (const moverId of Object.keys(SCENE.egress_lanes).sort()) {
    const pts = SCENE.egress_lanes[moverId];
    if (pts.length < 2) continue;
    const geom = new THREE9.BufferGeometry().setFromPoints(
      pts.map(([x, y]) => new THREE9.Vector3(x, y, Z_OFFSET))
    );
    const line = new THREE9.Line(
      geom,
      new THREE9.LineDashedMaterial({
        color: colour,
        dashSize: 0.6,
        gapSize: 0.3,
        transparent: true,
        opacity: 0.9
      })
    );
    line.computeLineDistances();
    scene.add(line);
    lines.push(line);
  }
  return { lines };
}

// src/anchors.ts
import * as THREE10 from "three";
function partCornersLocal(b) {
  if (b.vertices !== null) return b.vertices;
  const h = THREE10.MathUtils.degToRad(b.angle_deg);
  const cs = Math.cos(h), sn = Math.sin(h);
  const hl = b.length_m / 2, hw = b.width_m / 2;
  const corners = [[hl, -hw], [hl, hw], [-hl, hw], [-hl, -hw]];
  return corners.map(
    ([lx, ly]) => [b.cx + lx * cs - ly * sn, b.cy + lx * sn + ly * cs]
  );
}
function compareBoxesToOracle(aff, boxes, want, label) {
  let maxErr = 0;
  if (!aff || !want) return { structural: "missing affine/anchors for " + label, maxErr };
  if (want.length !== boxes.length) {
    return { structural: "anchor/box count mismatch for " + label, maxErr };
  }
  for (let bi = 0; bi < boxes.length; bi++) {
    const corners = partCornersLocal(boxes[bi]);
    if (want[bi].length !== corners.length) {
      return { structural: "anchor/vertex count mismatch for " + label, maxErr };
    }
    corners.forEach(([u, v], ci) => {
      const [wx, wy] = applyAffine(aff, u, v);
      maxErr = Math.max(maxErr, Math.abs(wx - want[bi][ci][0]), Math.abs(wy - want[bi][ci][1]));
    });
  }
  return { structural: "", maxErr };
}
function checkAnchors(scene) {
  let maxErr = 0;
  for (const p of scene.planes) {
    const aff = scene.final_poses[p.id];
    const r = compareBoxesToOracle(aff, p.boxes, scene.anchors[p.id], p.id);
    if (r.structural) return { structural: r.structural, maxErr };
    maxErr = Math.max(maxErr, r.maxErr);
    const gw = scene.gear_anchors[p.id];
    if (!aff || !gw || !p.wheels) {
      return { structural: "missing gear anchors/wheels for " + p.id, maxErr };
    }
    if (gw.length !== p.wheels.length) {
      return { structural: "gear anchor/wheel count mismatch for " + p.id, maxErr };
    }
    p.wheels.forEach(([u, v], wi) => {
      const [wx, wy] = applyAffine(aff, u, v);
      maxErr = Math.max(maxErr, Math.abs(wx - gw[wi][0]), Math.abs(wy - gw[wi][1]));
    });
  }
  for (const go of scene.ground_objects) {
    const r = compareBoxesToOracle(go.final_pose, go.boxes, scene.go_anchors[go.id], go.id);
    if (r.structural) return { structural: r.structural, maxErr };
    maxErr = Math.max(maxErr, r.maxErr);
  }
  return { structural: "", maxErr };
}

// src/timeline.ts
function affineAt(segByPlane, finals, pid, t) {
  const segs = segByPlane[pid];
  if (!segs || segs.length === 0) {
    const aff = finals[pid];
    return aff ? { vis: true, aff } : { vis: false, aff: null };
  }
  if (t < segs[0].start_s) return { vis: false, aff: null };
  const last = segs[segs.length - 1];
  if (t >= last.end_s) return { vis: true, aff: finals[pid] };
  let rest = null;
  for (const seg of segs) {
    if (t < seg.start_s) break;
    if (t < seg.end_s) {
      const frac = (t - seg.start_s) / (seg.end_s - seg.start_s);
      const i = Math.round(frac * (seg.samples.length - 1));
      return { vis: true, aff: seg.samples[i] };
    }
    rest = seg.samples[seg.samples.length - 1];
  }
  return { vis: true, aff: rest };
}
function framePoses(scene, segByPlane, t) {
  const out = {};
  for (const p of scene.planes) {
    out[p.id] = affineAt(segByPlane, scene.final_poses, p.id, t);
  }
  for (const go of scene.ground_objects) {
    out[go.id] = affineAt(segByPlane, { [go.id]: go.final_pose }, go.id, t);
  }
  return out;
}
function createTimeline(scene, groups, goGroups = {}) {
  const TL = scene.timeline;
  const SEGS = TL.segments;
  const TOTAL = TL.total_s;
  const hasAnim = TOTAL > 0 && SEGS.length > 0;
  const segByPlane = {};
  for (const s of SEGS) (segByPlane[s.plane_id] ??= []).push(s);
  const active = byId("active");
  const clock = byId("clock");
  const applyTime = (t) => {
    const poses = framePoses(scene, segByPlane, t);
    const drive = (id, g) => {
      if (!g) return;
      const { vis, aff } = poses[id];
      g.visible = vis;
      if (vis && aff) {
        g.matrix.copy(affineMatrix(aff));
        g.matrixWorldNeedsUpdate = true;
      }
    };
    for (const p of scene.planes) drive(p.id, groups[p.id]);
    for (const go of scene.ground_objects) drive(go.id, goGroups[go.id]);
    const cur = SEGS.find((s) => t >= s.start_s && t < s.end_s);
    active.textContent = cur ? "towing: " + cur.plane_id : "";
    clock.textContent = t.toFixed(1) + "s";
  };
  return { total: TOTAL, hasAnim, segs: SEGS, applyTime };
}

// src/hud.ts
var ANIM_CONTROLS = ["play", "prev", "next", "scrub", "speed"];
function startHud(deps) {
  const { home, controls, renderer, scene, cam } = deps;
  let active = deps.timeline;
  let t = 0;
  let playing = false;
  let speed = 1;
  const scrub = byId("scrub");
  const playBtn = byId("play");
  const speedSel = byId("speed");
  speedSel.addEventListener("change", () => {
    speed = parseFloat(speedSel.value);
  });
  const applyAnimEnabled = () => {
    for (const id of ANIM_CONTROLS) (active.hasAnim ? enableControl : disableControl)(id);
  };
  applyAnimEnabled();
  scrub.addEventListener("input", () => {
    t = Number(scrub.value) / 1e3 * active.total;
    playing = false;
    playBtn.textContent = "▶";
    active.applyTime(t);
  });
  playBtn.addEventListener("click", () => {
    if (!active.hasAnim) return;
    playing = !playing;
    playBtn.textContent = playing ? "❚❚" : "▶";
    if (t >= active.total) t = 0;
  });
  const stepTo = (dir) => {
    const bounds = [0, ...active.segs.map((s) => s.end_s)];
    let i = bounds.findIndex((b) => b > t + 1e-6);
    if (i < 0) i = bounds.length - 1;
    t = dir > 0 ? bounds[Math.min(i, bounds.length - 1)] : bounds[Math.max(0, i - 2)];
    playing = false;
    playBtn.textContent = "▶";
    if (active.hasAnim) scrub.value = String(t / active.total * 1e3);
    active.applyTime(t);
  };
  byId("next").addEventListener("click", () => stepTo(1));
  byId("prev").addEventListener("click", () => stepTo(-1));
  byId("reset").addEventListener("click", home);
  window.addEventListener("resize", () => {
    cam.aspect = window.innerWidth / window.innerHeight;
    cam.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });
  let last = performance.now();
  const loop = (now) => {
    requestAnimationFrame(loop);
    const dt = (now - last) / 1e3;
    last = now;
    if (playing && active.hasAnim) {
      t += dt * speed;
      if (t >= active.total) {
        t = active.total;
        playing = false;
        playBtn.textContent = "▶";
      }
      scrub.value = String(t / active.total * 1e3);
    }
    active.applyTime(t);
    controls.update();
    renderer.render(scene, cam);
  };
  active.applyTime(0);
  requestAnimationFrame(loop);
  return {
    setActiveTimeline(timeline) {
      active = timeline;
      t = 0;
      playing = false;
      playBtn.textContent = "▶";
      scrub.value = "0";
      applyAnimEnabled();
      active.applyTime(0);
    }
  };
}

// src/compare.ts
function wrapIndex(current, n, dir) {
  if (n <= 0) return 0;
  return ((current + dir) % n + n) % n;
}
function clampIndex(i, n) {
  if (n <= 0) return 0;
  if (!Number.isFinite(i)) return 0;
  return Math.min(Math.max(Math.trunc(i), 0), n - 1);
}
function fmtGap(min_gap_m) {
  return min_gap_m == null ? "n/a" : min_gap_m.toFixed(2) + " m";
}
function optionLabels(solutions) {
  return solutions.map((s) => `${s.label} — gap ${fmtGap(s.summary.min_gap_m)}`);
}
function formatSummary(sol) {
  const s = sol.summary;
  const parts = [`min gap ${fmtGap(s.min_gap_m)}`];
  if (s.planes_moved_vs_first > 0) {
    parts.push(`${s.planes_moved_vs_first} moved vs #1 (avg ${s.mean_shift_m.toFixed(1)} m)`);
  }
  parts.push(s.routable ? "tow-routable" : "not tow-routable");
  return parts.join(" · ");
}
function foundLabel(m) {
  if (m.count_found < m.count_requested) {
    return `Found ${m.count_found} of ${m.count_requested} requested`;
  }
  return `${m.count_found} solution${m.count_found === 1 ? "" : "s"}`;
}

// src/main.ts
function setReadouts(scene) {
  byId("placeholder").hidden = !scene.placeholder;
  const r = scene.readouts;
  const fmtM = (v) => v == null ? "n/a" : v.toFixed(2) + " m";
  byId("readouts").textContent = r ? "gap " + fmtM(r.min_gap_m) + " · wing-over-tail " + fmtM(r.min_wing_over_tail_clearance_m) : "";
}
function buildWorld(scene, data, brand) {
  byId("legend").textContent = "";
  const group = new THREE11.Group();
  scene.add(group);
  const { groups, labelMeshes, noseMeshes } = addPlanes(group, data, brand);
  const { groups: goGroups } = addGroundObjects(group, data);
  const { setVisible: setPathsVisible } = addTowPaths(group, data, brand);
  addEgressLanes(group, data, brand);
  try {
    const { structural, maxErr } = checkAnchors(data);
    if (structural) {
      banner("TRANSFORM CHECK FAILED (" + structural + ") — do not trust this render.");
    } else if (maxErr > 1e-6) {
      banner(
        "TRANSFORM CHECK FAILED (maxErr=" + maxErr.toExponential(2) + "): viewer affine disagrees with the Python oracle — do not trust this render."
      );
    }
  } catch (e) {
    banner("TRANSFORM CHECK ERRORED: " + e.message + " — do not trust this render.");
  }
  const timeline = createTimeline(data, groups, goGroups);
  return { group, labelMeshes, noseMeshes, setPathsVisible, timeline };
}
function wireToggles(wallMeshes, getWorld) {
  const wallsToggle = byId("walls");
  wallsToggle.addEventListener("change", () => {
    for (const m of wallMeshes) m.visible = wallsToggle.checked;
  });
  const labelsToggle = byId("labels");
  labelsToggle.addEventListener("change", () => {
    const on = labelsToggle.checked;
    const w = getWorld();
    for (const m of w.labelMeshes) m.visible = on;
    for (const m of w.noseMeshes) m.visible = on;
  });
  const pathsToggle = byId("paths");
  pathsToggle.addEventListener("change", () => getWorld().setPathsVisible(pathsToggle.checked));
}
function applyToggleState(world) {
  const on = byId("labels").checked;
  for (const m of world.labelMeshes) m.visible = on;
  for (const m of world.noseMeshes) m.visible = on;
  world.setPathsVisible(byId("paths").checked);
}
function disposeWorld(group) {
  group.traverse((obj) => {
    const m = obj;
    if (m.geometry) m.geometry.dispose();
    const mat = m.material;
    const mats = Array.isArray(mat) ? mat : mat ? [mat] : [];
    for (const one of mats) {
      const tex = one.map;
      if (tex) tex.dispose();
      one.dispose();
    }
  });
}
function setupStage(hangar, brand) {
  const canvas = byId("c");
  const r = createRenderer(canvas, hangar, brand);
  const wallMeshes = addHangar(r.scene, hangar, brand, r.span);
  return { ...r, wallMeshes };
}
function bootSingle(data, brand) {
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
    cam: stage.cam
  });
}
function startCompare(manifest, brand) {
  const solutions = manifest.solutions;
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
    cam: stage.cam
  });
  const select = byId("compare");
  for (const label of optionLabels(solutions)) {
    const opt = document.createElement("option");
    opt.textContent = label;
    select.appendChild(opt);
  }
  const metrics = byId("compare-metrics");
  const showMetrics = () => {
    metrics.textContent = foundLabel(manifest) + " · " + formatSummary(solutions[current]);
  };
  const mount = (k) => {
    const next = clampIndex(k, solutions.length);
    if (next === current) return;
    current = next;
    select.selectedIndex = current;
    clearBanner();
    stage.scene.remove(world.group);
    disposeWorld(world.group);
    world = buildWorld(stage.scene, solutions[current].scene, brand);
    applyToggleState(world);
    setReadouts(solutions[current].scene);
    hud.setActiveTimeline(world.timeline);
    showMetrics();
  };
  select.addEventListener("change", () => mount(select.selectedIndex));
  window.addEventListener("keydown", (e) => {
    if (e.target === select) return;
    if (e.key === "ArrowRight") mount(wrapIndex(current, solutions.length, 1));
    else if (e.key === "ArrowLeft") mount(wrapIndex(current, solutions.length, -1));
  });
  select.selectedIndex = 0;
  showMetrics();
}
var BRAND = JSON.parse(byId("brand").textContent ?? "null");
var solutionsEl = document.getElementById("solutions");
if (solutionsEl) {
  startCompare(JSON.parse(solutionsEl.textContent ?? "null"), BRAND);
} else {
  bootSingle(JSON.parse(byId("scene").textContent ?? "null"), BRAND);
}
