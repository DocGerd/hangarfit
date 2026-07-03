# Interactive placement editor → full-frontend redesign — design

**Status:** Accepted direction (*Full vision, sequenced*). Issues **#907–#912** are filed under
milestone v0.19.0 (#39) and the `later` track (see [§8](#8-decomposition-into-issues-filed)); the
remaining open items in [§10](#10-open-decisions) are *implementation* choices. This doc is the
umbrella spec for that issue set.

**Date:** 2026-07-03
**Author:** Claude (brainstorming session, grounded by four parallel code/web investigations)
**Epic:** #436 (TypeScript migration & modular, extendable viewer architecture)
**Related:** #442 (Stage-2 editor, shipped) · #445 (Stage-3 `hangarfit serve`, deferred tracking) ·
#444 (scene/v2 JSON-Schema single-source, deferred) · #904 (editor click-to-focus fast-follow) ·
#441 (Python `priority`) · #614 (`door_order`) · ADR-0002 · ADR-0017 · ADR-0025 · ADR-0029 · ADR-0003
**Filed issues:** #907 (C1) · #908 (C2) · #909 (B) · #910 (A1) · #911 (D) · #912 (A2) — under epic #436

---

## 1. Why this exists

The interactive placement editor that shipped in v0.18.0 (#442, `hangarfit view --solve --edit`) is
**Stage 2** of epic #436: an *intent-capture* surface over an **already-solved** layout. The user
deselects planes, sets priorities/pins on the solved geometry, and clicks **Export scenario YAML**;
that YAML is then re-run with `hangarfit solve` on the CLI. It was deliberately scoped to avoid a
backend and to avoid authoring the coordinate transform in JavaScript (ADR-0029).

The user's envisioned UX is materially larger and is, in effect, **Stage 3** — the deferred
`hangarfit serve` full frontend (#445). This doc captures that vision as a concrete design, grounds
each piece against the current code, and decomposes it into shippable work.

### 1.1 The envisioned UX (user's words, structured)

1. Show the hangar **empty**, like now.
2. A **palette/list of planes *and* ground objects** to choose which go in.
3. Per item, set **on-cart / own-gear**, overriding the default when it differs.
4. Set a **priority**: highest priority should be **closest to the door if possible**; priority is
   **exclusive/partial** — only one item may be "#1", others may have none (no shared ranks).
5. For items whose position the user wants to fix: click **"fix position"** and **drag them in the
   hangar** to the exact spot (and, implicitly, set heading).
6. Click **Calculate** and get the **results** in the browser.

---

## 2. Feasibility findings (grounding)

Four parallel investigations established the following. Citations are `file:line`.

### 2.1 Priority & the door (capability C)
There are **two unrelated "priority" mechanisms**:

- `PlaneConstraint.priority: float|None` (#441, `models.py:1179`) is a per-plane **spread-clearance
  weight** — `_priority_weight = 1.0 + priority` feeding the inter-plane repulsion energy
  (`solver.py:1254-1267`, `1323`). Ties allowed; **nothing to do with the door**. This is the field
  the current editor exposes.
- `Scenario.door_order: tuple[str,...]|None` (#614, `models.py:1240`) is a fleet-level **relative
  door-proximity ordering** ("first id parks nearest the door at `y=0`"). It already enforces exactly
  the user's two hard rules: **no duplicate ranks** (`models.py:1401-1403`) and **partial set**
  (unlisted planes are unranked). It is scored as a Kendall-tau inversion count
  (`solver.py:1390-1425`) and used **only as the primary selection tiebreak among already-found valid
  basins** (`solver.py:2106`) — it is *relative*, never absolute door-attraction, and it does **not
  steer the placement search** (absent from `_score`, `solver.py:1658-1674`).

**Verdict:** the user's ranked-priority idea maps onto `door_order`, **not** the numeric `priority`
field. "Rank-1 closest to the door" is **not** expressible today (all door logic is relative and
post-hoc). Achieving "rank-1 *actively pulled* to the door" needs **one new absolute door-distance
soft term** keyed on the rank-1 (or rank-weighted) body's `y_m`, ADR-0003-safe **iff** gated
inert-when-unset (the same gating `door_order` uses). The *insertion point* differs, though:
`door_order` is an integer post-hoc **selection** tiebreak that never steers placement, so the closer
analog for an *active* door pull is `_back_bias_energy` (`solver.py:~1326`), a float `exp` positional
bias — and a float term inherits the cross-machine libm caveat the spread energy already carries.
A length-1 `door_order` is currently a no-op, so the absolute term is what makes a single "#1" meaningful.

### 2.2 Palette / catalog & cart override (capabilities A, B)
- Today the editor's selectable universe = **solved planes only**: `initialIntent` =
  `Object.keys(ctx.currentPoses)` (`selection.ts:5`), built from `layout.placements`
  (`viewer.py:155-163`). No notion of an unplaced item.
- The full catalog **is** enumerable Python-side (`load_fleet`/`load_ground_objects` build
  `dict[str,Aircraft]` / `dict[str,GroundObject]` from `data/catalog/*.yaml` + `data/fleet.yaml`
  before the editor context is built), but is **not piped into the JS blob** —
  `build_editor_context` (`viewer.py:129-165`) emits, per placed plane, only pose + cart-eligibility
  (`currentPoses` + `cartEligible`), plus scalar `schema`/`fleet`/`hangar`/`maintenance` refs — but **no
  enumerated catalog of unplaced items**. **Gap to fill:** add a `catalog` field built from `layout.fleet` (full dict) +
  `layout.ground_objects`.
- Cart states come from `Aircraft.movement_mode ∈ {always_cart, always_own_gear, cart_eligible}`
  (`models.py:31,340`); `is_cart_eligible` (`models.py:390-392`) is what gates the toggle
  (`editor.ts:152`). The `cart_eligible` toggle already works. A scenario can set per-placement
  `on_carts` but **cannot** change `movement_mode`; only the **fleet manifest** can, via the
  `{ref, movement_mode}` override (`loader.py:161,164-200`). So overriding a *locked* plane's cart
  mode requires the export to emit a fleet-manifest override, which `export.ts` does not do today.
- Ground objects: `fixed_obstacle` position **is** scenario-expressible today (round-trippable,
  `loader.py:866-887`); `placed_routed_mover` (cars/trailers) **forbids an authored pose by design**
  (ADR-0025, `loader.py:889-893`) — the solver places them; only `region_preference` steers. So
  "drag a trailer to an exact spot" needs either treating it as a `fixed_obstacle` (loses routed
  status) or a **schema extension** letting a mover carry a pin (mirroring `constraints[id].pin`).

### 2.3 Drag-to-fix-position (capability D)
- The viewer pins **three r160** (`viewer/package.json` `"three":"0.160.0"`). **TransformControls**
  is the right primitive (DragControls doesn't rotate and drags in a camera-facing plane, not the
  floor):
  - Reposition: `setMode('translate')`, `showZ=false` → drag confined to the **XY** ground plane
    (this viewer is **Z-up**: `camera.up=(0,0,1)`, floor = world XY, yaw = rotation about Z; ADR-0017:104-107);
    it does the 2D-pointer→3D-floor raycast **internally** (no hand-rolled raycaster).
  - Heading: `setMode('rotate')`, `showX=false; showY=false` → single yaw (Z-axis) ring.
  - Coordinate `OrbitControls` via the official `'dragging-changed'` → `orbit.enabled = !value` hook.
  - r160 caveat: add the gizmo with `scene.add(control)` — `getHelper()` is r169+. (These three r160
    facts — the show-flag axes, the internal floor raycast, and `scene.add(control)` vs `getHelper()` —
    are pinned to `three` r160; re-verify against the pinned version when D/serve is built, since the CI
    skew-guard bumps `three`/`@types/three`/the test devDep in lockstep.)
- The pose→scenario math is **trivial and self-inverse**: `_pose_affine` emits `[s, c, x_m, c, -s,
  y_m]` with linear part `M=[[s,c],[c,-s]]`, `det=-1` (`scene.py:132-139`). `M` is a **reflection ⇒
  `M⁻¹=M`** (its own inverse). The viewer builds the scene in hangarfit meters (`ADR-0017:104-107`),
  so world == hangar coords: a plane dragged so its origin lands on floor hit `(wx,wy)` gives
  `x_m=wx, y_m=wy` directly; heading = `degrees(atan2(a,b))`. ~20 lines.
- **BUT** authoring that inverse in JS is forbidden by **policy**, not blocked by difficulty:
  ADR-0029:41-43 — "No `interaction/` module may compose, invert, or re-derive the
  determinant-−1 transform" (ADR-0002); the drag-specific failure mode + escape hatch are the separate
  passage at :128-133. Its failure mode is the signature sign-flip: a mirror-imaged
  render at non-axis-aligned headings, invisible to the Python `test_geometry` canary because it's in
  the one language CI can't exercise, and `checkAnchors()` only guards the **forward** transform.
- **Recorded escape hatch (ADR-0029:132-133):** return drag "behind a design that keeps the
  derivation on the Python side" — i.e. round-trip the dragged pose as **intent** through the server
  (#445); Python owns the (trivial) inverse and re-solves.

### 2.4 In-browser Calculate (capability E)
- A `file://` HTML **cannot** solve, and cannot even `fetch` (CORS on `file://`, ADR-0017:93-98 — the
  very reason the offline build inlines the scene). So Calculate **requires** `hangarfit serve`.
- The whole solve→scene chain already exists as a pure pipeline; `serve` only changes the transport
  from file to HTTP: `load_scenario` (`loader.py:669`) → `solve()` (`solver.py:59`) → `build_scene()`
  returning a JSON `scene/v2` dict (`scene.py:388`) → `json.dumps`. Net-new is a stdlib
  `http.server` bound to loopback + a viewer bootstrap that `fetch()`es the scene instead of reading
  the inlined `<script id="scene">` blob. The runtime scene-swap path already exists and is exercised
  (the #666 compare switcher re-runs `mount→buildWorld→checkAnchors`).

---

## 3. The architectural pivot

**Capabilities D and E are the same project: `hangarfit serve` (#445).** `serve` unlocks drag
(Python owns the inverse), Calculate (native), *and* the "empty → add → see result" flow — while
keeping Python the sole authority for the determinant-−1 transform (ADR-0002) and the determinism
contract (ADR-0003) untouched, because solving stays in one Python runtime. The offline single-file
export **survives** as the shareable/pure-view artifact; `serve` is an *additional* deployment mode.

`serve` needs its **own ADR** (it shifts the model from "double-clicked offline file" to "a local
server"). Alternatives to record and reject/defer there:
- **Desktop wrapper** (Tauri / pywebview) — same "Python owns the solve" benefit, heavier tooling,
  nicer distribution. A viable alternative packaging of the same idea.
- **In-browser Pyodide/WASM solve** — **rejected**: re-opens the det-−1 trap and introduces a second
  determinism runtime.
- Localhost security: loopback-only bind, no auth surface, no external origin.

Capabilities **B/C — and the *selection* core of A (A1) — do not need the backend** and can ship
incrementally on the offline editor; A's "see the placed result" and mover-drag (A2) do need it (E).

---

## 4. Component design, per capability

Each unit below has one purpose and a defined interface, consistent with the existing
`scene.py`↔`scene-contract.ts` seam and the `interaction/` module boundary.

### 4.1 (C) Ranked priority = closest-to-door — *backend-free*
- **Editor UI:** a drag-to-order list ("door proximity: #1 nearest → …"); items not in the list are
  unranked. Enforces uniqueness by construction (a list). Reuses the existing selection set.
- **Export:** emit `door_order: [id, …]` in the exported scenario (new key in `export.ts`, alongside
  `fleet_in`/`maintenance`/`constraints`). Uniqueness + placeable-membership are enforced in
  `Scenario.__post_init__` (`models.py:1401-1407`); partial sets are permitted by construction (no
  completeness check).
- **Solver (optional, for "actively pulled to door"):** add one **absolute door-distance soft term**
  keyed on the rank-weighted `y_m` — modelled on `_back_bias_energy` (`solver.py:~1326`, a float `exp`
  positional bias, *not* door_order's integer selection tiebreak), gated **inert-when-unset** so all
  existing plans stay byte-identical (ADR-0003; mind the cross-machine libm caveat for a float term).
  This touches the solver's scoring surface ⇒ **`determinism-guard` review required**. Ships as its own
  issue; the UI/export can land first and rely on the existing relative tiebreak until then.
- **Editor-context:** emit `door` geometry (already in scene/v2) so the UI can show which edge is the
  door.

### 4.2 (B) Per-item cart override — *backend-free*
- `cart_eligible` toggle already works. For a **locked** plane (`always_own_gear`/`always_cart`),
  add an explicit "override cart mode" affordance. The override lives in a fleet **manifest file**
  (`{ref: catalog/<id>.yaml, movement_mode: …}`, `loader.py:161`), **not** the scenario — the exported
  scenario only references a fleet by path. So this export must emit/modify a **second file** (a fleet
  manifest), not just the scenario. Keep the default path (no override) byte-identical.
- **Decision to confirm:** is overriding a *locked* plane's mode actually wanted, or only the
  `cart_eligible` on/off that already works? (The demo scenario only exercises the latter.)

### 4.3 (A) Empty hangar + palette (planes *and* objects)
- **Editor-context:** add a `catalog` field to `build_editor_context` (`viewer.py`), built from
  `layout.fleet` + `layout.ground_objects` (both already in-memory at the `cmd_view` call site):
  `{id: {name, kind, cartEligible, footprint/parts-for-ghost}}`.
- **Palette UI:** list catalog items; clicking adds one to the intent (`fleet_in` for aircraft; a
  ground-object entry for objects). Added items render as **ghosts** at a default pose until Calculate
  runs.
- **Backend dependency:** the *selection* is backend-free, but "add and **see the placed result**"
  needs Calculate (E). Without the backend, A degrades to "pick what's in `fleet_in`, then export".
- **Ground-object placement (interacts with D):** fixing a car/trailer at a chosen spot needs the
  mover-pin schema extension (§2.2) OR fixed-obstacle coercion. Recommend the schema extension: add an
  optional `pin` to a `placed_routed_mover` scenario entry, mirroring `constraints[id].pin`.

### 4.4 (D) Drag-to-fix-position — *needs backend*
- **Client:** `TransformControls` per §2.3 (translate w/ `showZ=false` for the Z-up floor; rotate w/
  the yaw Z-ring only, `showX=false; showY=false`; `dragging-changed`→orbit toggle; `scene.add(control)`
  for r160). New `interaction/manipulate.ts`.
- **Transform boundary:** the client **never** inverts the transform. On drag-end it sends the
  **pose as intent** (the dragged object's world position + yaw, as opaque numbers) to the server;
  Python maps them back to `(x_m,y_m,heading_deg)` using the tested `geometry`/`scene` code and
  re-renders. This honors ADR-0002 "by construction."
- **UX guard:** because Python re-solves, a dragged pose becomes a **pin** (must-position) so the
  solver respects it rather than moving it — matching the existing `pinAtCurrent` semantics.

### 4.5 (E) `hangarfit serve` — *the backend*
- **New `serve` CLI subcommand** + stdlib `http.server`/`BaseHTTPRequestHandler`, loopback-bound
  (no flask/fastapi dependency — matches the no-heavy-deps ethos).
- **Endpoints:** `GET /` + `GET /viewer.js` (serve shell + committed bundle); `POST /solve` (body =
  exported `Scenario` YAML → `load_scenario` → `solve` → `build_scene` → JSON `scene/v2`); optional
  `POST /check` (`collisions.check`) for validity-only.
- **Client bootstrap branch:** when running under `http://`, `fetch('/solve', …)` and feed the
  returned scene into the existing `mount→buildWorld→checkAnchors` path (proven by #666).
- **Invariants:** Python stays the solver/authority; det-−1 transform single-sourced; ADR-0003
  determinism unaffected (one runtime). Offline export path unchanged.

---

## 5. Data flow

```
Offline mode (survives):   scenario.yaml --CLI--> solve --> build_scene --> inlined <script id=scene> --> viewer
Serve mode (new):          [browser palette/drag/rank UI] --intent--> export.ts --Scenario YAML-->
                           POST /solve --> load_scenario --> solve --> build_scene --> scene/v2 JSON -->
                           fetch() --> mount/buildWorld/checkAnchors --> render (+ TransformControls attach)
```

The **intent object** (`{selectedPlaneIds, priorities, mustPositions, door_order, catalog additions}`)
is the single contract between UI and server, serialized by the existing `export.ts` path (extended).

---

## 6. Cross-cutting concerns

- **Determinism (ADR-0003):** the new absolute door term and any solver-touching change must be
  inert-when-unset and pass `determinism-guard`. `serve` itself is determinism-neutral (transport
  only).
- **Transform policy (ADR-0002/0029):** no JS-authored inverse. All pose↔scenario mapping stays in
  Python; the client sends/receives opaque numbers.
- **scene/v2 seam (ADR-0017):** additive-only; `serve` reuses `build_scene` unchanged →
  `scene-schema-guard` applies to any editor-context/scene change. The `catalog` addition is an
  editor-context (`hangarfit.editor-context/v1`) change, not a scene/v2 change.
- **Security:** loopback bind only; document the local-server threat model in the new ADR.
- **Offline artifact preserved:** the single-file export remains the shareable deliverable; `serve`
  is opt-in.

## 7. Testing strategy

- **C/B/A export:** extend `tests/test_viewer.py` byte-identity + `export.ts` node unit tests
  (`viewer/test/`); a loader round-trip test (`door_order`, manifest override, mover-pin) proving
  `export → load_scenario` validity.
- **Absolute door term:** solver unit + a determinism canary (inert-when-unset byte-identity).
- **D manipulate:** `viewer/test/` node units for the TransformControls config (mode/show-flags) and
  the intent payload shape; the *inverse correctness* is tested in Python (existing `test_geometry`
  45° canary already guards it).
- **E serve:** a Python integration test hitting `POST /solve` with a fixture scenario and asserting a
  valid `scene/v2` doc; a headless viewer smoke that `fetch`es and passes `checkAnchors` (swiftshader,
  as in the existing view smoke).

## 8. Decomposition into issues (filed)

Filed under epic #436; order reflects the **"Full vision, sequenced"** direction. Filed numbers:
C1 = #907, C2 = #908, B = #909, A1 = #910, D = #911, A2 = #912.

1. **C1 — Ranked-priority UI + `door_order` export** (backend-free). Editor list + `export.ts` +
   loader round-trip test.
2. **C2 — Absolute door-distance soft term** (solver; `determinism-guard`). Makes rank-1 actively
   seek the door. Can trail C1.
3. **B — Cart-mode override for locked planes** (fleet-manifest override in export). *Confirm demand.*
4. **A1 — Catalog field in editor-context + palette UI** (add planes/objects from an empty start).
5. **E — `hangarfit serve` epic** (+ its own ADR): CLI subcommand, loopback API, viewer fetch
   bootstrap. Unlocks D + Calculate + A's "see result".
6. **D — Drag-to-fix via TransformControls** (client) + pin-as-intent round-trip. Depends on E.
7. **A2 — Mover-pin schema extension** (let a `placed_routed_mover` carry a pin) so objects can be
   dragged to an exact spot. Depends on D+E.
8. **#904** — fold the existing click-to-focus fast-follow into the manipulate/selection rework.

The **"Serve backend first"** direction reorders to 5 → 6 → 4/7 → 1/2/3. The **"Backend-free only"**
direction ships 1,2,3, a selection-only 4, and explicitly defers 5/6/7. In **every** ordering, #904
(item 8) rides along with the manipulate/selection rework (D).

## 9. Non-goals / YAGNI

- In-browser (Pyodide/WASM) solving — rejected (det-−1 trap + second determinism runtime).
- Authoring the coordinate inverse in JS — rejected (ADR-0002).
- Replacing the offline export — it stays.
- Multi-user / remote server — loopback-only.

## 10. Open decisions

1. **Direction** (§3): Full-vision-sequenced *(recommended)* vs Serve-backend-first vs
   Backend-free-only. Governs whether the `serve` epic + drag are in scope now.
2. **Deliverable:** design spec + reviewed follow-up issues *(recommended)* vs issues-only vs
   spec + implementation plan.
3. **B scope:** override a *locked* plane's cart mode, or only the `cart_eligible` on/off that
   already works?
4. **A2 ground-object placement:** mover-pin schema extension *(recommended)* vs fixed-obstacle
   coercion vs leave objects solver-placed (no manual drag for cars/trailers).

---

*This spec is a draft for review. On approval it is committed via the normal issue-driven PR flow,
and §8 becomes the filed issue set.*
