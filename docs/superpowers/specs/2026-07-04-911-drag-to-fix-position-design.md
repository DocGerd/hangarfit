# #911 — Editor drag-to-fix-position (pose round-tripped as intent)

**Date:** 2026-07-04
**Issue:** #911 (epic #436; builds on #904 click-to-focus + #445 `hangarfit serve`)
**Status:** design approved; implementation pending
**Extends:** ADR-0030 (serve) with a second endpoint + an additive editor-context field. ADR-0002/0029 (Python owns the determinant-−1 transform) and ADR-0003 (deterministic solve) bind it. No new ADR needed.

---

## 1. Problem & goal

The user wants to click **"fix position"** on a focused plane and **drag** it on the hangar floor to an exact spot, **and set its heading**, then re-solve with that plane pinned. `#904` shipped the stable `focusedId` target; `#445` shipped the `serve` loopback backend. The blocker recorded in ADR-0002/0029: turning a dragged **world** pose into scenario `(x_m, y_m, heading_deg)` inverts the determinant-−1 transform, which must **not** be authored in JS (the sign-flip trap CI can't exercise). So Python must own the inverse; the browser sends a raw world pose.

**v1 scope (decided):** translate on the floor **and** set heading (yaw ring).

## 2. Decision — drop behavior: **pin-then-Calculate**

A drag-drop **converts** the world pose to a normal, editable **pin** (via a solve-free `serve` op); the user then presses the existing **Calculate** to re-solve. Chosen unanimously by a four-lens judge panel (UX / architecture / determinism / forward-compat) over "auto-solve on every drop":

| Lens | **pin-then-Calculate** | auto-solve-on-drop |
|---|---|---|
| UX & interaction feel | **5** | 2 |
| Architecture & serve surface | **5** | 3 |
| Determinism & performance | **5** | 2 |
| Forward-compat & scope-fit | **5** | 3 |

**Why:** a served solve is *global* — the ADR-0008 spread post-pass relocates every **unpinned** plane and `reRender` rebuilds the whole world (main.ts:183-207) — so auto-solving on each drop reshuffles planes 2–8 the instant plane 1 is dropped (placing against a moving board), at a full RR-MC (budget 30 s) per drop. Pin-then-Calculate matches the issue's literal acceptance criterion ("dragging a plane **and hitting Calculate** re-solves"), does **zero** solve on drop (RNG-free, sub-ms), reuses the entire existing pin→export→`/solve`→`reRender` path (zero parallel state), and surfaces "no valid layout" only at the explicit Calculate the user chose to press.

**Deferred fast-follow (not v1):** an opt-in "auto-recalculate on drop" toggle (default OFF) that simply programmatically clicks Calculate after `/convert` — "A now, one-line toggle later," never a fork to a bespoke solve-with-pin endpoint. Deferred because the acceptance criterion is the two-step and the auto-solve thrash needs multi-plane UX validation (and lands naturally with #912's mover-pin).

## 3. Architecture

### 3.1 New serve op — `POST /convert` (Python, `server.py`)

A stateless, **solve-free, RNG-free** sibling to `/solve` in `_Handler.do_POST` (server.py:186-212). **Do NOT route it through `_solve_scene`** — that would fire RR-MC + the spread pass and reintroduce the auto-solve cost/nondeterminism/thrash.

- **Route:** add a `self.path == "/convert"` branch alongside the existing `/solve` branch (the current `if self.path != "/solve": 404` becomes a small dispatch on `/solve` vs `/convert`). Host-header guard (`_host_ok`) unchanged.
- **Request body (JSON):** `{"x": float, "y": float, "world_yaw_rad": float}` — the raw world floor pose read off the manipulation proxy. (No `id` needed: the op is pure geometry; the client applies the result to `focusedId`.)
- **Handler** (`_convert_pose(body: str) -> dict`): the whole op is
  ```python
  x_m = x                                  # translation is identity (scene.py:139 _pose_affine tx=x_m)
  y_m = y                                  # ...ty=y_m
  heading_deg = towplanner.math_rad_to_compass(world_yaw_rad)   # (90 - deg(θ)) % 360, the tested involution
  ```
  Returns `{"x_m": x_m, "y_m": y_m, "heading_deg": heading_deg}`. It **never** calls `build_scene` / `build_editor_context` / `solve`, so it is **byte-path-neutral** — cannot drift scene/v2 or the serve-gated offline export.
- **Errors:** bad framing (Content-Length / non-UTF-8) → 400 (reuse the `/solve` pattern); malformed JSON / missing / non-numeric fields → 400 with an actionable message; unexpected → `traceback.print_exc(file=sys.stderr)` + generic 500. Non-finite floats (NaN/Inf) rejected as 400.
- `math_rad_to_compass` already lives in `src/hangarfit/towplanner.py:337-339` (a tested mutual inverse of `compass_to_math_rad`). This is the **only** heading math and it is in Python (ADR-0002).

### 3.2 Python-owned gizmo seed — `world_yaw_rad` in `build_editor_context` (`viewer.py`)

`currentPoses[id]` (viewer.py:157-165) gains one **additive** field:
```python
"world_yaw_rad": towplanner.compass_to_math_rad(p.heading_deg),   # radians(90 - heading): the forward companion of /convert
```
The client seeds the proxy from it (no JS trig). This is an **editor-context blob** change (`hangarfit.editor-context/v1`), **not** a scene/v2 change — so `build_scene` byte-identity and the scene-schema-guard key-parity net are untouched. Extend the `CurrentPose` interface in `viewer/src/interaction/intent-contract.ts` with `world_yaw_rad: number`. (The editor-context↔`intent-contract.ts` key-parity test added in #910 must be updated to include it.)

**Alignment (why a clean proxy matches the reflected plane):** the plane's local +u axis maps under the det-−1 transform to world direction `(sin h, cos h)`, math-angle `90° − h = compass_to_math_rad(h)`. A plain proxy rotation by that same angle points its +x the same way, so the yaw ring aligns with the plane's nose; the reflection only flips handedness (+v), which the ring ignores. Reading `proxy.rotation.z` back through `math_rad_to_compass` round-trips the heading exactly.

### 3.3 Vendor `TransformControls` (dev-only toolchain + offline asset)

`TransformControls` is not yet vendored (the offline HTML import-map registers only `three` + `OrbitControls` as `data:` URLs; esbuild marks only those `external`). Mirror exactly how `OrbitControls` was added:
- Fetch `three@0.160.0`'s `examples/jsm/controls/TransformControls.js`, pin + SHA-256, place under `src/hangarfit/_viewer_assets/three/` (document in `src/hangarfit/_viewer_assets/three/VENDOR.md`).
- Register it in `viewer.py`'s import-map (viewer.py:95-100) as a `data:` URL under `three/addons/controls/TransformControls.js`.
- Add `three/addons/controls/TransformControls.js` to esbuild's `external` list (esbuild.config.mjs:35) so the bundle keeps it external (like OrbitControls).
- Import path in TS: `three/addons/controls/TransformControls.js` (the r160 addon path; `getHelper()` is r169+ — do **not** use it).

### 3.4 The manipulator — `viewer/src/interaction/manipulate.ts` (new)

A new interaction module, mounted only in `--edit` **and** only when serve is available (a `#serve-config` blob is present — the drag-to-fix flow needs the `/convert` round-trip, so it ships **dormant** in the offline single-file export, consistent with the Calculate button).

- `mountManipulate({ groups, proxyHost, cam, renderer, orbit, ctx, getFocusedId, onConverted }): ManipulateHandle` — creates a `TransformControls` bound to a single reusable **proxy `Object3D`** (never a plane Group). Z-up config: translate mode shows XY only (`showZ = false`); a rotate mode shows the yaw Z-ring only (`showX = false; showY = false`). `control.addEventListener('dragging-changed', e => orbit.enabled = !e.value)` suspends OrbitControls during a drag. `scene.add(control)` (r160; not `getHelper()`).
- **Arming (`arm(id)`):** position the proxy at `ctx.currentPoses[id].x_m/.y_m` (floor z) and set `proxy.rotation.z = ctx.currentPoses[id].world_yaw_rad` (Python seed — no JS trig); attach the gizmo; set `groups[id].userData.heldByEditor = true` so the render loop stops overwriting that plane (see §3.5). **Disarm (`disarm()`):** detach the gizmo, hide it; leaves `heldByEditor` as-is on a converted plane (it stays held until the next `reRender` rebuilds Groups).
- **Live translation follow:** on the gizmo's `change` event during a translate drag, mirror **only translation** to the plane Group: `groups[id].matrix.setPosition(proxy.position.x, proxy.position.y, z0)` (preserves the Python-owned det-−1 linear part — this is identity translation, ADR-safe) + `matrixWorldNeedsUpdate = true`. **Heading is NOT mirrored to the mesh** (rotating the reflected mesh would author the transform — ADR-0002); the intended heading shows on the gizmo's yaw ring (optionally a thin nose-arrow helper on the proxy).
- **Drag-end (`dragging-changed`→false):** read `proxy.position.x/.y` + `proxy.rotation.z`, POST `{x, y, world_yaw_rad}` to `/convert`; on the response call `onConverted(id, {x_m, y_m, heading_deg})`. On a `/convert` error, `banner(...)` and leave the pin unchanged (the plane stays at its live-translated spot; the user can retry or Calculate).
- `dispose()`: abort listeners + remove the gizmo (mirrors `editor.ts` dispose, so a `reRender` re-mount is clean).

### 3.5 Render-loop hold gate (`viewer/src/timeline.ts`)

`applyTime`'s `drive(id, g)` (timeline.ts:97-105) copies each plane's affine to `g.matrix` **every frame**. Add one guard at the top of `drive`:
```ts
if (g?.userData.heldByEditor) return;   // #911: a plane the editor is dragging owns its own matrix
```
`manipulate.ts` owns the matrix of a held plane; on `reRender` fresh Groups are built (no flag set), so a solved plane resumes normal `applyTime` — no snap-back bookkeeping. (Held planes stay visible; the early return skips the visibility write too, which is correct for the static-layout edit mode.)

### 3.6 Wire the converted pose into the pin machinery (`selection.ts`, `editor.ts`)

- New pure helper `pinAtPose(intent, id, pose: {x_m, y_m, heading_deg}, onCarts): Intent` in `selection.ts` — a sibling of `pinAtCurrent` (selection.ts:63-68) that sources the pin from the converted pose instead of `currentPoses[id]`. `onCarts` carries from the plane's existing pin (`intent.mustPositions[id]?.onCarts ?? currentPoses[id].on_carts`) — it is not part of the pose conversion.
- `onConverted(id, pose)` (wired in `editor.ts`/`main.ts`): `intent = pinAtPose(intent, id, pose, onCarts)`, then `syncControls()` (the x/y/heading pin fields populate and stay editable via the unchanged `setPinField`, selection.ts:76-80) and mark Calculate "unsolved" (§3.7). Pressing Calculate exports `constraints[id].pin` via `export.ts:54-60` unchanged → `POST /solve` → `reRender`. **Zero parallel state** — a dropped pose is an ordinary editable pin.

### 3.7 Arming affordance + "unsolved changes" state (`_HUD_EDIT` in `viewer.py`, `calculate.ts`/`main.ts`)

- **"fix position" button** — **client-injected** into the `#editor` panel by the client (like the Calculate button, which `mountCalculate` creates in JS, *not* in `_HUD_EDIT`), so there is **no `_HUD_EDIT` change** and the offline edit HTML gains no button. It mounts only when serve is available (a `#serve-config` blob is present) and is enabled only for a focused, selected plane that has a `currentPose`. Clicking arms `manipulate.ts` for `focusedId` (attaches the gizmo); a second click / focus change disarms. (The existing "pin here" checkbox pins at the *current* pose; "fix position" arms the drag → pin at the *dragged* pose — both produce a normal `mustPosition`.)
- **Calculate "unsolved changes" marker:** when a drag-convert (or any pin edit) changes the intent since the last successful solve, add a visible marker to the Calculate button (e.g. a `● unsolved` class); clear it on a successful `reRender`. This is the UX-lens mitigation for pin-then-Calculate's deferred feedback (a user who drags and sees the mesh not rotate must discover the required Calculate step). Minimal: a boolean "dirty since last solve" toggled on convert/pin-edit and cleared in `reRender`.

### 3.8 Serve contract types (`viewer/src/interaction/serve-contract.ts`)

Add `ConvertRequest = { x: number; y: number; world_yaw_rad: number }`, `ConvertResponse = { x_m: number; y_m: number; heading_deg: number }`, and a `convertRequestInit(req)` helper (JSON body, `Content-Type: application/json`) beside the existing `solveRequestInit`.

## 4. Data flow (the full round-trip)

```
focus plane (#904) → click "fix position" → arm gizmo (proxy seeded from Python world_yaw_rad)
  → drag on floor: plane mesh slides live (translation only); yaw ring shows intended heading
  → drop: POST /convert {x, y, world_yaw_rad}  → Python: x_m=x, y_m=y, heading_deg=math_rad_to_compass(yaw)
  → onConverted → pinAtPose → x/y/heading pin fields populate; Calculate marked "unsolved"
  → user presses Calculate → export constraints[id].pin → POST /solve → reRender (whole world, plane at solved pose w/ Python heading)
```
The browser never computes heading↔yaw: forward via the `world_yaw_rad` seed (§3.2), inverse via `/convert` (§3.1).

## 5. ADR compliance

- **ADR-0002/0029:** no JS-authored transform inverse. Position mirroring is pure translation (identity); the plane mesh is never rotated in JS; all heading↔yaw math is Python (`compass_to_math_rad` seed + `math_rad_to_compass` in `/convert`). `manipulate.ts` imports neither `affine.ts` nor `anchors.ts`.
- **ADR-0003:** `/convert` does no solving (RNG-free, deterministic); the served `/solve` path is unchanged. Determinism contract untouched.
- **ADR-0030 (serve):** `/convert` is a natural second endpoint on the existing loopback server; same Host-header guard, same opt-in gating. The offline single-file export's **behavior** is unchanged — drag ships dormant (no `#serve-config` ⇒ neither the "fix position" button nor the gizmo mounts). Its **bytes** change only by the `TransformControls` import-map entry (§3.3, needed so the bundle resolves the addon even though it's never mounted offline) plus the rebuilt `viewer.js` — both inert offline.
- **ADR-0017 / scene-schema-guard:** `build_scene` and scene/v2 are untouched; `world_yaw_rad` is an editor-context field, not scene/v2.

## 6. Testing plan

- **Python (`tests/test_server.py`):** `POST /convert` happy path (a known `(x, y, world_yaw_rad)` → expected `(x_m, y_m, heading_deg)`, asserting the round-trip against `compass_to_math_rad`); framing 400 (bad Content-Length / non-UTF-8); malformed/missing/NaN field 400; unknown path still 404; **solve-free** assertion (a `/convert` call does not invoke the solver — e.g. patch/spy or assert timing/no scene in response).
- **Python (`tests/test_viewer.py`):** `build_editor_context` emits `world_yaw_rad = compass_to_math_rad(heading_deg)` per plane (a value check on a known pose); the editor-context↔`intent-contract.ts` key-parity test updated to include it; the `--edit` HTML byte-identity updated for the new `TransformControls` **import-map** entry (a deliberate, reviewed byte change — inert offline; the "fix position" button is client-injected, so it adds no HTML bytes).
- **Round-trip unit (Python):** `math_rad_to_compass(compass_to_math_rad(h)) == h` across a range (guards the involution the whole feature rests on) — extend `tests/test_towplanner.py` if not already covered.
- **Viewer units (`viewer/test`):** `pinAtPose` (sets `mustPositions[id]` from a pose; `onCarts` carry logic; immutability) as pure `node --test`; `convertRequestInit`/`serve-contract` shape. `manipulate.ts` gizmo/DOM wiring is not unit-tested (raycaster/WebGL, precedent-consistent) — covered by the headless smoke + review.
- **Headless smoke (swiftshader):** a `serve`-rendered `--edit` page mounts the gizmo without a transform-mismatch banner; `checkAnchors` stays hidden. (Driving an actual drag headlessly is out of scope; the pose math is covered by the Python + `pinAtPose` units.)
- **Manual test:** run `hangarfit serve <scenario>`; focus a plane, "fix position", drag + rotate, drop → pin fields populate; Calculate → plane lands ~at the dropped pose with the right heading; other planes may relocate (expected — global solve).
- **`viewer.js` rebuild** committed with the client change (viewer-build-drift guard).

## 7. Scope / non-goals

- **Deferred (fast-follow):** the opt-in "auto-recalculate on drop" toggle (§2).
- **Not #911:** #912 mover pin (the `/convert` op is pose-kind-agnostic and reuses verbatim; the mover *schema* is #912). No offline (non-serve) drag. No multi-select drag (one focused plane at a time). No live mesh **rotation** (Python-owned; shown on the gizmo).

## 8. Delivery (two sequential PRs)

The ADR-sensitive Python inverse is isolated into its own PR first, mirroring how #904 preceded #911:

- **PR A — backend:** `/convert` endpoint + `_convert_pose`, `world_yaw_rad` seed in `build_editor_context`, `serve-contract.ts` types + `convertRequestInit`, and all Python/parity tests. Independently mergeable and fully testable (no gizmo yet). Touches `server.py`, `viewer.py`, `serve-contract.ts`, `intent-contract.ts` (+ tests).
- **PR B — client:** vendor `TransformControls` (+ `viewer.py` import-map entry + `VENDOR.md`, the only `viewer.py` change — no `_HUD_EDIT` edit), `manipulate.ts`, the timeline hold-gate, `pinAtPose`, the client-injected "fix position" button + Calculate "unsolved" state, `main.ts`/`editor.ts` wiring, rebuilt `viewer.js`. Based off `develop` **after** PR A merges (consumes A's `/convert` + seed + types). Touches the viewer client + the vendored asset + `viewer.py` (import-map only).

Each PR carries its own CHANGELOG `[Unreleased]` entry and its own review arc (PR A: code-reviewer + silent-failure-hunter for the new endpoint; PR B: code-reviewer + scene-schema-guard for `viewer.js`/`viewer.py`).

## 9. Review guards this touches

- **PR A:** `silent-failure-hunter` (the new `/convert` error paths), `code-reviewer`. `scene-schema-guard` if `viewer.py` `build_editor_context` change is judged in-scope (it is an editor-context, not scene/v2, change — confirm parity test).
- **PR B:** `scene-schema-guard` (rebuilt `viewer.js`, `viewer.py` `_HUD_EDIT`), `code-reviewer`. The `viewer/src/*.ts`→`viewer.js` rebuild guard + `VENDOR.md` skew.
