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
function disableControl(id) {
  byId(id).disabled = true;
}

// src/renderer.ts
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
function createRenderer(canvas2, H2, BRAND2) {
  let renderer2;
  try {
    renderer2 = new THREE.WebGLRenderer({ canvas: canvas2, antialias: true });
  } catch (e) {
    banner("WebGL is unavailable in this browser: " + e.message);
    throw e;
  }
  renderer2.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer2.setSize(window.innerWidth, window.innerHeight);
  renderer2.shadowMap.enabled = true;
  renderer2.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene2 = new THREE.Scene();
  scene2.background = new THREE.Color(BRAND2.sceneBg);
  const cam2 = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 2e3);
  cam2.up.set(0, 0, 1);
  const controls2 = new OrbitControls(cam2, renderer2.domElement);
  controls2.enableDamping = true;
  controls2.dampingFactor = 0.08;
  const span2 = Math.max(H2.width_m, H2.length_m);
  const home2 = () => {
    cam2.position.set(H2.width_m * 0.5, -H2.length_m * 0.55, span2 * 0.95);
    controls2.target.set(H2.width_m / 2, H2.length_m / 2, 0.5);
    controls2.update();
  };
  home2();
  scene2.add(new THREE.HemisphereLight(
    new THREE.Color(BRAND2.hemisphereSky),
    new THREE.Color(BRAND2.hemisphereGround),
    BRAND2.hemisphereIntensity
  ));
  const sun = new THREE.DirectionalLight(new THREE.Color(BRAND2.sun), BRAND2.sunIntensity);
  sun.position.set(H2.width_m * 0.35, -H2.length_m * 0.15, span2 * 1.1);
  sun.target.position.set(H2.width_m / 2, H2.length_m / 2, 0);
  scene2.add(sun.target);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.normalBias = 0.04;
  const sc = sun.shadow.camera;
  sc.left = -span2;
  sc.right = span2;
  sc.top = span2;
  sc.bottom = -span2;
  sc.near = 0.5;
  sc.far = span2 * 3.5;
  sc.updateProjectionMatrix();
  scene2.add(sun);
  const fill = new THREE.DirectionalLight(new THREE.Color(BRAND2.fill), BRAND2.fillIntensity);
  fill.position.set(H2.width_m * 0.7, H2.length_m * 1.2, span2 * 0.6);
  scene2.add(fill);
  return { renderer: renderer2, scene: scene2, cam: cam2, controls: controls2, span: span2, home: home2 };
}

// src/hangar.ts
import * as THREE2 from "three";
var WALL_H = 3;
function addHangar(scene2, H2, BRAND2, span2) {
  const wallMeshes2 = [];
  const notches = H2.structural_notches ?? [];
  const floorMat = new THREE2.MeshStandardMaterial({
    color: new THREE2.Color(BRAND2.floor),
    roughness: 1,
    side: THREE2.DoubleSide
  });
  let floor;
  if (notches.length === 0) {
    floor = new THREE2.Mesh(new THREE2.PlaneGeometry(H2.width_m, H2.length_m), floorMat);
    floor.position.set(H2.width_m / 2, H2.length_m / 2, 0);
  } else {
    const shape = new THREE2.Shape();
    shape.moveTo(0, 0);
    shape.lineTo(H2.width_m, 0);
    shape.lineTo(H2.width_m, H2.length_m);
    shape.lineTo(0, H2.length_m);
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
  scene2.add(floor);
  const grid = new THREE2.GridHelper(
    span2,
    Math.round(span2),
    new THREE2.Color(BRAND2.gridMajor),
    new THREE2.Color(BRAND2.gridMinor)
  );
  grid.rotation.x = Math.PI / 2;
  grid.position.set(H2.width_m / 2, H2.length_m / 2, 3e-3);
  scene2.add(grid);
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
    scene2.add(mesh);
    wallMeshes2.push(mesh);
  };
  addWall(t, H2.length_m, 0, H2.length_m / 2);
  addWall(t, H2.length_m, H2.width_m, H2.length_m / 2);
  addWall(H2.width_m, t, H2.width_m / 2, H2.length_m);
  const dl = H2.door.center_x_m - H2.door.width_m / 2;
  const dr = H2.door.center_x_m + H2.door.width_m / 2;
  if (dl > 1e-6) addWall(dl, t, dl / 2, 0);
  if (dr < H2.width_m - 1e-6) addWall(H2.width_m - dr, t, (dr + H2.width_m) / 2, 0);
  const eps = 1e-6;
  for (const n of notches) {
    const cx = (n.x_min_m + n.x_max_m) / 2;
    const cy = (n.y_min_m + n.y_max_m) / 2;
    const w = n.x_max_m - n.x_min_m;
    const l = n.y_max_m - n.y_min_m;
    if (n.x_min_m > eps) addWall(t, l, n.x_min_m, cy);
    if (n.x_max_m < H2.width_m - eps) addWall(t, l, n.x_max_m, cy);
    if (n.y_min_m > eps) addWall(w, t, cx, n.y_min_m);
    if (n.y_max_m < H2.length_m - eps) addWall(w, t, cx, n.y_max_m);
  }
  const bay = H2.maintenance_bay;
  if (bay && bay.closed) {
    const bm = new THREE2.Mesh(
      new THREE2.BoxGeometry(bay.width_m, bay.depth_m, WALL_H),
      new THREE2.MeshStandardMaterial({
        color: new THREE2.Color(BRAND2.bay),
        transparent: true,
        opacity: BRAND2.bayOpacity
      })
    );
    bm.position.set(bay.center_x_m, H2.length_m - bay.depth_m / 2, WALL_H / 2);
    scene2.add(bm);
  }
  return wallMeshes2;
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
  const canvas2 = document.createElement("canvas");
  canvas2.width = tw + padX * 2;
  canvas2.height = fontPx + padY * 2;
  const ctx = canvas2.getContext("2d");
  ctx.font = fontPx + fontStack;
  ctx.textBaseline = "middle";
  ctx.fillStyle = conflicted ? BRAND2.labelConflictChip : BRAND2.labelChipBg;
  ctx.fillRect(0, 0, canvas2.width, canvas2.height);
  ctx.fillStyle = BRAND2.labelText;
  ctx.fillText(shown, padX, canvas2.height / 2);
  const tex = new THREE4.CanvasTexture(canvas2);
  tex.anisotropy = 4;
  const sprite = new THREE4.Sprite(
    new THREE4.SpriteMaterial({ map: tex, transparent: true, depthTest: false })
  );
  const hWorld = 0.9;
  sprite.scale.set(hWorld * canvas2.width / canvas2.height, hWorld, 1);
  sprite.renderOrder = 999;
  return sprite;
}
function addLabelAndNose(g, p, colour, conflicted, BRAND2, labelMeshes2, noseMeshes2) {
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
  labelMeshes2.push(label);
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
  noseMeshes2.push(nose);
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
function addPlanes(scene2, SCENE2, BRAND2) {
  const CONFLICT = BRAND2.conflict;
  const groups2 = {};
  const labelMeshes2 = [];
  const noseMeshes2 = [];
  const legend = byId("legend");
  const gearMats = makeGearMaterials(BRAND2);
  for (const p of SCENE2.planes) {
    const g = new THREE5.Group();
    g.matrixAutoUpdate = false;
    const conflicted = SCENE2.conflicts.includes(p.id);
    const colour = new THREE5.Color(conflicted ? CONFLICT : p.color);
    for (const b of p.boxes) g.add(boxMesh(b, colour));
    addGear(g, p, gearMats);
    addLabelAndNose(g, p, colour, conflicted, BRAND2, labelMeshes2, noseMeshes2);
    groups2[p.id] = g;
    scene2.add(g);
    const sw = document.createElement("span");
    sw.className = "sw";
    const dot = document.createElement("i");
    dot.style.background = conflicted ? BRAND2.conflict : p.color;
    sw.appendChild(dot);
    sw.appendChild(document.createTextNode(p.id));
    legend.appendChild(sw);
  }
  return { groups: groups2, labelMeshes: labelMeshes2, noseMeshes: noseMeshes2 };
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
function addGroundObjects(scene2, SCENE2) {
  const groups2 = {};
  const legend = byId("legend");
  for (const go of SCENE2.ground_objects) {
    const g = new THREE7.Group();
    g.matrixAutoUpdate = false;
    const colour = new THREE7.Color(go.color);
    for (const b of go.boxes) g.add(boxMesh(b, colour));
    g.matrix.copy(affineMatrix(go.final_pose));
    g.matrixWorldNeedsUpdate = true;
    groups2[go.id] = g;
    scene2.add(g);
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
  return { groups: groups2 };
}

// src/paths.ts
import * as THREE8 from "three";
var TX = 2;
var TY = 5;
function pathPoints(seg) {
  return seg.samples.map((s) => [s[TX], s[TY]]);
}
function addTowPaths(scene2, SCENE2, BRAND2) {
  const Z_OFFSET = 0.02;
  const segByPlane = {};
  for (const s of SCENE2.timeline.segments) segByPlane[s.plane_id] = s;
  const lines = [];
  for (const p of SCENE2.planes) {
    const seg = segByPlane[p.id];
    if (!seg) continue;
    const pts = pathPoints(seg);
    if (pts.length < 2) continue;
    const conflicted = SCENE2.conflicts.includes(p.id);
    const colour = new THREE8.Color(conflicted ? BRAND2.conflict : p.color);
    const geom = new THREE8.BufferGeometry().setFromPoints(
      pts.map(([x, y]) => new THREE8.Vector3(x, y, Z_OFFSET))
    );
    const line = new THREE8.Line(geom, new THREE8.LineBasicMaterial({ color: colour }));
    scene2.add(line);
    lines.push(line);
  }
  const setVisible = (on) => {
    for (const l of lines) l.visible = on;
  };
  return { lines, setVisible };
}

// src/anchors.ts
import * as THREE9 from "three";
function partCornersLocal(b) {
  if (b.vertices !== null) return b.vertices;
  const h = THREE9.MathUtils.degToRad(b.angle_deg);
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
function checkAnchors(scene2) {
  let maxErr = 0;
  for (const p of scene2.planes) {
    const aff = scene2.final_poses[p.id];
    const r = compareBoxesToOracle(aff, p.boxes, scene2.anchors[p.id], p.id);
    if (r.structural) return { structural: r.structural, maxErr };
    maxErr = Math.max(maxErr, r.maxErr);
    const gw = scene2.gear_anchors[p.id];
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
  for (const go of scene2.ground_objects) {
    const r = compareBoxesToOracle(go.final_pose, go.boxes, scene2.go_anchors[go.id], go.id);
    if (r.structural) return { structural: r.structural, maxErr };
    maxErr = Math.max(maxErr, r.maxErr);
  }
  return { structural: "", maxErr };
}

// src/timeline.ts
function affineAt(segByPlane, finals, pid, t) {
  const seg = segByPlane[pid];
  if (!seg) {
    const aff = finals[pid];
    return aff ? { vis: true, aff } : { vis: false, aff: null };
  }
  if (t < seg.start_s) return { vis: false, aff: null };
  if (t >= seg.end_s) return { vis: true, aff: finals[pid] };
  const frac = (t - seg.start_s) / (seg.end_s - seg.start_s);
  const i = Math.round(frac * (seg.samples.length - 1));
  return { vis: true, aff: seg.samples[i] };
}
function framePoses(scene2, segByPlane, t) {
  const out = {};
  for (const p of scene2.planes) {
    out[p.id] = affineAt(segByPlane, scene2.final_poses, p.id, t);
  }
  for (const go of scene2.ground_objects) {
    out[go.id] = affineAt(segByPlane, { [go.id]: go.final_pose }, go.id, t);
  }
  return out;
}
function createTimeline(scene2, groups2, goGroups2 = {}) {
  const TL = scene2.timeline;
  const SEGS = TL.segments;
  const TOTAL = TL.total_s;
  const hasAnim = TOTAL > 0 && SEGS.length > 0;
  const segByPlane = {};
  for (const s of SEGS) segByPlane[s.plane_id] = s;
  const active = byId("active");
  const clock = byId("clock");
  const applyTime = (t) => {
    const poses = framePoses(scene2, segByPlane, t);
    const drive = (id, g) => {
      if (!g) return;
      const { vis, aff } = poses[id];
      g.visible = vis;
      if (vis && aff) {
        g.matrix.copy(affineMatrix(aff));
        g.matrixWorldNeedsUpdate = true;
      }
    };
    for (const p of scene2.planes) drive(p.id, groups2[p.id]);
    for (const go of scene2.ground_objects) drive(go.id, goGroups2[go.id]);
    const cur = SEGS.find((s) => t >= s.start_s && t < s.end_s);
    active.textContent = cur ? "towing: " + cur.plane_id : "";
    clock.textContent = t.toFixed(1) + "s";
  };
  return { total: TOTAL, hasAnim, segs: SEGS, applyTime };
}

// src/hud.ts
function startHud(deps) {
  const { timeline: timeline2, home: home2, controls: controls2, renderer: renderer2, scene: scene2, cam: cam2 } = deps;
  const { total: TOTAL, hasAnim, segs: SEGS, applyTime } = timeline2;
  let t = 0;
  let playing = false;
  let speed = 1;
  const scrub = byId("scrub");
  const playBtn = byId("play");
  const speedSel = byId("speed");
  speedSel.addEventListener("change", () => {
    speed = parseFloat(speedSel.value);
  });
  if (!hasAnim) {
    ["play", "prev", "next", "scrub", "speed"].forEach(disableControl);
  }
  scrub.addEventListener("input", () => {
    t = Number(scrub.value) / 1e3 * TOTAL;
    playing = false;
    playBtn.textContent = "▶";
    applyTime(t);
  });
  playBtn.addEventListener("click", () => {
    if (!hasAnim) return;
    playing = !playing;
    playBtn.textContent = playing ? "❚❚" : "▶";
    if (t >= TOTAL) t = 0;
  });
  const stepTo = (dir) => {
    const bounds = [0, ...SEGS.map((s) => s.end_s)];
    let i = bounds.findIndex((b) => b > t + 1e-6);
    if (i < 0) i = bounds.length - 1;
    t = dir > 0 ? bounds[Math.min(i, bounds.length - 1)] : bounds[Math.max(0, i - 2)];
    playing = false;
    playBtn.textContent = "▶";
    if (hasAnim) scrub.value = String(t / TOTAL * 1e3);
    applyTime(t);
  };
  byId("next").addEventListener("click", () => stepTo(1));
  byId("prev").addEventListener("click", () => stepTo(-1));
  byId("reset").addEventListener("click", home2);
  window.addEventListener("resize", () => {
    cam2.aspect = window.innerWidth / window.innerHeight;
    cam2.updateProjectionMatrix();
    renderer2.setSize(window.innerWidth, window.innerHeight);
  });
  let last = performance.now();
  const loop = (now) => {
    requestAnimationFrame(loop);
    const dt = (now - last) / 1e3;
    last = now;
    if (playing && hasAnim) {
      t += dt * speed;
      if (t >= TOTAL) {
        t = TOTAL;
        playing = false;
        playBtn.textContent = "▶";
      }
      scrub.value = String(t / TOTAL * 1e3);
    }
    applyTime(t);
    controls2.update();
    renderer2.render(scene2, cam2);
  };
  applyTime(0);
  requestAnimationFrame(loop);
}

// src/main.ts
var SCENE = JSON.parse(byId("scene").textContent ?? "null");
var BRAND = JSON.parse(byId("brand").textContent ?? "null");
var H = SCENE.hangar;
if (SCENE.placeholder) byId("placeholder").hidden = false;
if (SCENE.readouts) {
  const fmtM = (v) => v == null ? "n/a" : v.toFixed(2) + " m";
  byId("readouts").textContent = "gap " + fmtM(SCENE.readouts.min_gap_m) + " · wing-over-tail " + fmtM(SCENE.readouts.min_wing_over_tail_clearance_m);
}
var canvas = byId("c");
var { renderer, scene, cam, controls, span, home } = createRenderer(canvas, H, BRAND);
var wallMeshes = addHangar(scene, H, BRAND, span);
var wallsToggle = byId("walls");
wallsToggle.addEventListener("change", () => {
  for (const m of wallMeshes) m.visible = wallsToggle.checked;
});
var { groups, labelMeshes, noseMeshes } = addPlanes(scene, SCENE, BRAND);
var labelsToggle = byId("labels");
labelsToggle.addEventListener("change", () => {
  const on = labelsToggle.checked;
  for (const m of labelMeshes) m.visible = on;
  for (const m of noseMeshes) m.visible = on;
});
var { groups: goGroups } = addGroundObjects(scene, SCENE);
var { setVisible: setPathsVisible } = addTowPaths(scene, SCENE, BRAND);
var pathsToggle = byId("paths");
pathsToggle.addEventListener("change", () => setPathsVisible(pathsToggle.checked));
try {
  const { structural, maxErr } = checkAnchors(SCENE);
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
var timeline = createTimeline(SCENE, groups, goGroups);
startHud({ timeline, home, controls, renderer, scene, cam });
