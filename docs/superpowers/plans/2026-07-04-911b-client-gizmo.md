# #911 PR B — client drag-to-fix gizmo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the client half of drag-to-fix: focus a plane, click "fix position", drag it on the hangar floor and set its heading with a `TransformControls` gizmo; on drop the world pose round-trips through PR A's solve-free `POST /convert` into a normal editable pin, and the existing Calculate re-solves.

**Architecture:** A new `viewer/src/interaction/manipulate.ts` owns the gizmo mechanics (one reusable proxy `Object3D` + one `TransformControls`, live-translation follow, drag-end→`/convert`→callback, the `heldByEditor` render-loop gate). `editor.ts` *uses* it: it injects the "fix position" button, gates enablement in `syncControls()`, and turns a converted pose into a pin via a new pure `pinAtPose` (`selection.ts`). `TransformControls` is vendored like `OrbitControls` (offline `data:` import-map + esbuild external). All heading↔yaw math stays in Python (PR A's `world_yaw_rad` seed forward, `/convert` back) — the browser never authors the determinant-−1 transform (ADR-0002).

**Tech Stack:** TypeScript (viewer, esbuild bundle → committed `viewer.js`), three.js r160 (`TransformControls` addon), Python 3.12 (`viewer.py` import-map only), `node --test` for pure units, headless swiftshader Chrome for the smoke.

## Global Constraints

- **ADR-0002/0029:** No JS-authored transform inverse. The plane mesh is **never rotated** in JS; live position mirroring is **pure translation** (`matrix.setPosition`, preserving the Python-owned det-−1 linear part). All heading↔yaw math is Python: the `world_yaw_rad` seed (PR A, `build_editor_context`) forward, `math_rad_to_compass` in `/convert` back. `manipulate.ts` imports **neither** `affine.ts` nor `anchors.ts`.
- **ADR-0003:** `/convert` does no solving (PR A, already merged); no determinism surface changes here.
- **ADR-0017 / scene-schema-guard:** `build_scene` and scene/v2 are **untouched**. This PR touches `viewer.py` **only** in `_assemble_html`'s import-map (a reviewed offline-HTML byte change) — **no `_HUD_EDIT` edit** (the "fix position" button is client-injected, like Calculate).
- **ADR-0020 / viewer-build-drift:** after any `viewer/src/*.ts` change, rebuild `src/hangarfit/_viewer_assets/viewer.js` (`npm --prefix viewer/ run build`) and commit it so the drift guard passes at branch HEAD.
- **Vendoring (ADR-0017):** `TransformControls.js` is vendored raw into `src/hangarfit/_viewer_assets/three/`, pinned to `three@0.160.0` via jsDelivr, SHA-256 recorded in `VENDOR.md`. Do **not** use `getHelper()` (r169+); r160 adds the control to the scene directly.
- **three version lockstep:** `three` (vendored) = `three` devDep (`0.160.0`) = `@types/three` (`0.160.0`). Do not bump.
- **Offline dormancy:** with no serve backend the drag flow must not mount — no `TransformControls` constructed, no button. The offline single-file export's **behavior** is unchanged; its **bytes** change only by the `TransformControls` import-map entry + the rebuilt `viewer.js` (both inert offline).
- **Delivery:** PR B off `develop` (PR A #926 merged). PR body: `Closes #911`. Its own CHANGELOG `[Unreleased]` entry. Review arc: `code-reviewer` + `scene-schema-guard` (rebuilt `viewer.js` + `viewer.py`).

---

## File Structure

- **New:** `src/hangarfit/_viewer_assets/three/TransformControls.js` — vendored r160 addon (raw).
- **New:** `viewer/src/interaction/manipulate.ts` — `createManipulator(...)`: gizmo mechanics only (no intent, no focus, no DOM button).
- **Modify:** `src/hangarfit/_viewer_assets/three/VENDOR.md` — third source/hash row.
- **Modify:** `src/hangarfit/viewer.py` — `_assemble_html`: one asset read + one import-map entry for `TransformControls`.
- **Modify:** `viewer/esbuild.config.mjs` — add `TransformControls` addon to `external`.
- **Modify:** `viewer/src/interaction/selection.ts` — new pure `pinAtPose`.
- **Modify:** `viewer/src/timeline.ts` — one-line `heldByEditor` hold-gate in `drive`.
- **Modify:** `viewer/src/interaction/editor.ts` — optional `scene`/`orbit`/`onEdit` opts; create + use the manipulator; inject the "fix position" button; enablement in `syncControls`; `onConverted`→`pinAtPose`.
- **Modify:** `viewer/src/interaction/calculate.ts` — `mountCalculate` returns `{ markUnsolved }`; a `● unsolved` marker cleared on a successful solve.
- **Modify:** `viewer/src/main.ts` — parse serve-config before mounting the editor; thread `scene`/`orbit`/`onEdit` into `mountEditor` (and the `reRender` re-mount) only when served; wire the marker.
- **Modify:** `src/hangarfit/_viewer_assets/viewer.js` — rebuilt bundle (Task 5).
- **Modify:** `tests/test_viewer.py` — import-map entry assertion + `--edit`/offline HTML byte updates.
- **Modify/New:** `viewer/test/*.test.ts` — `pinAtPose`, hold-gate, `markUnsolved` pure units.
- **Modify:** `CHANGELOG.md` — one `[Unreleased]` entry (Task 6).

**Recon reference (verbatim current code for every anchor):** `/tmp/claude-1000/-home-pkuhn-hangarfit/46869df9-a28d-4a9d-9a1d-8960d3de0014/scratchpad/prb-integration-reference.md` — implementers are handed the relevant excerpt in their task brief; they need not re-derive line numbers.

---

### Task 1: Vendor `TransformControls` + import-map + esbuild external

**Files:**
- Create: `src/hangarfit/_viewer_assets/three/TransformControls.js`
- Modify: `src/hangarfit/_viewer_assets/three/VENDOR.md`
- Modify: `src/hangarfit/viewer.py` (`_assemble_html`, ~lines 101–110)
- Modify: `viewer/esbuild.config.mjs` (line 35, `external`)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Produces: the offline import-map now resolves `three/addons/controls/TransformControls.js`; Task 4's `manipulate.ts` may `import { TransformControls } from 'three/addons/controls/TransformControls.js'` and it resolves both offline (data: URL) and under esbuild (external).

- [ ] **Step 1: Fetch + pin the vendored addon**

Run (from repo root):
```bash
V=0.160.0
DST=src/hangarfit/_viewer_assets/three
curl -fsSL "https://cdn.jsdelivr.net/npm/three@${V}/examples/jsm/controls/TransformControls.js" -o "$DST/TransformControls.js"
sha256sum "$DST/TransformControls.js"
```
Record the printed SHA-256 for Step 2. Sanity-check the file is a real ES module (`grep -c "export" "$DST/TransformControls.js"` > 0, imports `from 'three'`).

- [ ] **Step 2: Extend `VENDOR.md`**

Add a `TransformControls.js` row to the **Sources** table and a line to the **SHA-256** block (use the Step-1 hash), and add it to the **Refresh procedure** `curl`/`sha256sum` commands. Mirror the existing `OrbitControls.js` row exactly:

Sources table — add:
```markdown
| `TransformControls.js` | https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/TransformControls.js |
```
SHA-256 block — add the line `<hash>  TransformControls.js`. Refresh procedure — add the matching `curl … TransformControls.js … -o "$DST/TransformControls.js"` line and extend the `sha256sum` args. Also add one sentence to the note under the Sources table: `TransformControls.js` likewise imports the bare `'three'` specifier, resolved by the same import-map.

- [ ] **Step 3: Write the failing test (import-map entry present)**

Add to `tests/test_viewer.py` (near the existing edit/offline HTML tests):
```python
def test_edit_html_registers_transformcontrols_importmap(tmp_path: Path) -> None:
    # #911 PR B: the vendored TransformControls addon must be in the offline
    # import-map so the (dormant-offline) drag bundle resolves its import.
    scenario = _write_minimal_scenario(tmp_path)  # reuse the helper the other edit tests use
    html = _render_edit_html(scenario)             # reuse the existing edit-HTML render helper
    assert "three/addons/controls/TransformControls.js" in html
    assert "three/addons/controls/OrbitControls.js" in html  # regression: still there
```
If `tests/test_viewer.py` has no `_write_minimal_scenario`/`_render_edit_html` helpers with those exact names, use whatever the file's existing edit-HTML tests use to produce the `--edit` HTML string (grep the file for `editor-context` / `render_edit` to find them) — the assertion (`"three/addons/controls/TransformControls.js" in html`) is the point.

- [ ] **Step 4: Run it — verify it FAILS**

Run: `pytest tests/test_viewer.py::test_edit_html_registers_transformcontrols_importmap -v`
Expected: FAIL (`TransformControls.js` not yet in the import-map).

- [ ] **Step 5: Add the import-map entry in `viewer.py`**

In `_assemble_html` (`src/hangarfit/viewer.py`), beside the existing OrbitControls lines:
```python
    three_src = _asset_text(_THREE, "three.module.js")
    orbit_src = _asset_text(_THREE, "OrbitControls.js")
    transform_src = _asset_text(_THREE, "TransformControls.js")   # #911 PR B
    viewer_js = _asset_text(_ASSETS, "viewer.js")

    import_map = {
        "imports": {
            "three": _data_url(three_src),
            "three/addons/controls/OrbitControls.js": _data_url(orbit_src),
            "three/addons/controls/TransformControls.js": _data_url(transform_src),  # #911 PR B
        }
    }
```

- [ ] **Step 6: Add the esbuild external entry**

`viewer/esbuild.config.mjs` line 35:
```js
  external: ["three", "three/addons/controls/OrbitControls.js", "three/addons/controls/TransformControls.js"],
```

- [ ] **Step 7: Run the test + the byte-identity suite**

Run: `pytest tests/test_viewer.py -v`
Expected: the new test PASSES. Any `--edit`/offline HTML **byte-identity** test now fails on the added import-map entry — this is the deliberate, reviewed byte change (§5 of the spec). Update those golden bytes/asserts to include the `TransformControls` entry (the offline export gains only this import-map line; the button is client-injected, so no HTML button bytes are added). Re-run until green.

- [ ] **Step 8: ruff/mypy + commit**

Run: `ruff check src/hangarfit/viewer.py tests/test_viewer.py && ruff format --check src/hangarfit/viewer.py tests/test_viewer.py && mypy src/hangarfit/`
Expected: clean.
```bash
git add src/hangarfit/_viewer_assets/three/TransformControls.js src/hangarfit/_viewer_assets/three/VENDOR.md src/hangarfit/viewer.py viewer/esbuild.config.mjs tests/test_viewer.py
git commit -m "feat(viewer): vendor TransformControls + register offline import-map (#911)"
```
(No `viewer.js` change here — no TS was touched; the esbuild `external` entry is inert until Task 4 imports the addon.)

---

### Task 2: `pinAtPose` (selection) + render-loop hold-gate (timeline)

**Files:**
- Modify: `viewer/src/interaction/selection.ts` (beside `pinAtCurrent`, lines 63–68)
- Modify: `viewer/src/timeline.ts` (`drive`, inside `applyTime`, lines ~97–105)
- Test: `viewer/test/selection.test.ts` (existing), `viewer/test/timeline-hold.test.ts` (new)

**Interfaces:**
- Produces: `pinAtPose(intent: Intent, id: string, pose: { x_m: number; y_m: number; heading_deg: number }, onCarts: boolean): Intent` — Task 4's `editor.ts` calls it in `onConverted`. Hold-gate: any plane `Group` with `userData.heldByEditor === true` is skipped by `applyTime`, so Task 3/4's manipulator can own that plane's matrix.

- [ ] **Step 1: Write the failing `pinAtPose` test**

Add to `viewer/test/selection.test.ts` (mirror the existing `pinAtCurrent` test's imports/fixtures):
```ts
test('pinAtPose sets mustPositions from a converted pose, carrying onCarts', () => {
  const base: Intent = { selectedPlaneIds: ['a'], priorities: {}, mustPositions: {}, doorOrder: [], groundObjectIds: [], cartModeOverrides: {} };
  const out = pinAtPose(base, 'a', { x_m: 3.5, y_m: -2.0, heading_deg: 275 }, true);
  assert.deepStrictEqual(out.mustPositions.a, { x: 3.5, y: -2.0, heading: 275, onCarts: true });
  assert.deepStrictEqual(base.mustPositions, {}); // immutability: input untouched
});
```
Import `pinAtPose` from `../src/interaction/selection.ts` alongside the existing selection imports.

- [ ] **Step 2: Run it — verify it FAILS**

Run: `npm --prefix viewer/ run test`
Expected: FAIL (`pinAtPose` is not exported).

- [ ] **Step 3: Implement `pinAtPose`**

In `viewer/src/interaction/selection.ts`, right after `pinAtCurrent`:
```ts
// #911 PR B: a sibling of pinAtCurrent that sources the pin from a Python-converted
// dragged pose (POST /convert) instead of currentPoses. onCarts is not part of the
// pose conversion — the caller carries it from the plane's existing pin or currentPose.
export function pinAtPose(
  intent: Intent,
  id: string,
  pose: { x_m: number; y_m: number; heading_deg: number },
  onCarts: boolean,
): Intent {
  const mp: MustPosition = { x: pose.x_m, y: pose.y_m, heading: pose.heading_deg, onCarts };
  return { ...intent, mustPositions: { ...intent.mustPositions, [id]: mp } };
}
```

- [ ] **Step 4: Run the selection test — verify PASS**

Run: `npm --prefix viewer/ run test`
Expected: the new test PASSES.

- [ ] **Step 5: Write the failing hold-gate test**

New file `viewer/test/timeline-hold.test.ts`. It builds a minimal single-plane `SceneV2` with **no** animation (`timeline.total_s = 0`, `segments: []`) so `framePoses` returns the plane's static scene pose, constructs two real `THREE.Group`s, marks one held, calls `applyTime(0)`, and asserts the held group's matrix was **not** overwritten while the unheld one was.
```ts
import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createTimeline } from '../src/timeline.ts';
import type { SceneV2 } from '../src/scene-contract.ts';

function miniScene(): SceneV2 {
  // Minimal valid scene: one plane at a known affine, no ground objects, no animation.
  // (Fill every field createTimeline/framePoses reads; copy the shape from an existing
  // viewer/test scene fixture if one exists — grep viewer/test for `timeline:` — else
  // inline the smallest object that satisfies scene-contract.ts's SceneV2.)
  return /* … minimal SceneV2 literal, plane id 'p', identity-ish affine, timeline {total_s:0, segments:[]} … */ {} as SceneV2;
}

test('applyTime skips a Group flagged heldByEditor', () => {
  const scene = miniScene();
  const held = new THREE.Group();
  const free = new THREE.Group();
  held.userData.heldByEditor = true;
  held.matrixAutoUpdate = false;
  free.matrixAutoUpdate = false;
  const before = held.matrix.clone();
  const tl = createTimeline(scene, { p: held });   // drive 'p' via the held group
  tl.applyTime(0);
  assert.ok(held.matrix.equals(before), 'held group matrix must be untouched');

  const tl2 = createTimeline(scene, { p: free });
  tl2.applyTime(0);
  assert.ok(!free.matrix.equals(before), 'unheld group matrix must be driven');
});
```
If assembling a full `SceneV2` literal proves large, reuse an existing scene fixture from `viewer/test/` (grep for `scene-contract` / `SceneV2` imports in the test dir); the load-bearing assertion is *held ⇒ matrix untouched*, *free ⇒ matrix set*.

- [ ] **Step 6: Run it — verify it FAILS**

Run: `npm --prefix viewer/ run test`
Expected: FAIL (both groups currently get driven; the held assertion fails).

- [ ] **Step 7: Add the hold-gate**

In `viewer/src/timeline.ts`, first line inside the `drive` closure:
```ts
    const drive = (id: string, g: THREE.Group | undefined): void => {
      if (!g) return;
      if (g.userData.heldByEditor) return; // #911 PR B: the editor's gizmo owns this plane's matrix
      const { vis, aff } = poses[id];
```

- [ ] **Step 8: Run the tests — verify PASS**

Run: `npm --prefix viewer/ run test && npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint`
Expected: all green.

- [ ] **Step 9: Commit**
```bash
git add viewer/src/interaction/selection.ts viewer/src/timeline.ts viewer/test/selection.test.ts viewer/test/timeline-hold.test.ts
git commit -m "feat(viewer): pinAtPose + heldByEditor render-loop hold-gate (#911)"
```
(No `viewer.js` rebuild yet — deferred to Task 5 so the bundle is rebuilt once after all TS lands.)

---

### Task 3: `manipulate.ts` — the gizmo mechanics

**Files:**
- Create: `viewer/src/interaction/manipulate.ts`

**Interfaces:**
- Consumes: `pinAtPose`? no — the manipulator is intent-free. It consumes `ConvertResponse`/`convertRequestInit` (serve-contract), `banner` (dom), `heldByEditor` (Task 2), `TransformControls` (Task 1 import-map/external).
- Produces:
  ```ts
  export interface ManipulatorHandle {
    arm(id: string): void;      // attach the gizmo to plane id's proxy, seeded from ctx
    disarm(): void;             // detach the gizmo (leaves heldByEditor as-is until reRender)
    armedId(): string | null;
    dispose(): void;            // remove the gizmo + all listeners
  }
  export function createManipulator(opts: {
    scene: THREE.Scene;
    groups: Record<string, THREE.Group>;
    cam: THREE.Camera;
    renderer: THREE.WebGLRenderer;
    orbit: OrbitControls;
    ctx: EditorContext;
    onConverted: (id: string, pose: ConvertResponse) => void;
  }): ManipulatorHandle
  ```
  Task 4's `editor.ts` calls `createManipulator` and drives `arm`/`disarm`.

**Note on testing:** `TransformControls` needs a live camera + `renderer.domElement` (WebGL), so this module is **not** unit-tested in `node --test` — consistent with the raycaster/`pick` precedent (untested in `editor.ts`). Its verification here is `tsc --noEmit` + `eslint`; behavior is covered by the Task 5 headless smoke + review. No failing-test-first cycle for this task.

- [ ] **Step 1: Write `manipulate.ts`**
```ts
// viewer/src/interaction/manipulate.ts — #911 PR B drag-to-fix gizmo mechanics.
// Intent-free and focus-free: editor.ts owns which plane is armed and turns a
// converted pose into a pin. Python owns the determinant-−1 inverse (ADR-0002):
// this module mirrors ONLY translation to the mesh (identity, safe) and never
// rotates it; heading is shown on the gizmo's yaw ring and read back through
// POST /convert. It imports neither affine.ts nor anchors.ts.
import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import type { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { banner } from '../dom.ts';
import { convertRequestInit } from '../serve-contract.ts';
import type { ConvertResponse } from '../serve-contract.ts';
import type { EditorContext } from './intent-contract.ts';

export interface ManipulatorHandle {
  arm(id: string): void;
  disarm(): void;
  armedId(): string | null;
  dispose(): void;
}

export function createManipulator(opts: {
  scene: THREE.Scene;
  groups: Record<string, THREE.Group>;
  cam: THREE.Camera;
  renderer: THREE.WebGLRenderer;
  orbit: OrbitControls;
  ctx: EditorContext;
  onConverted: (id: string, pose: ConvertResponse) => void;
}): ManipulatorHandle {
  const proxy = new THREE.Object3D(); // a reusable anchor — NEVER a plane Group
  opts.scene.add(proxy);
  const control = new TransformControls(opts.cam, opts.renderer.domElement);
  control.setSpace('local');
  opts.scene.add(control); // r160: add the control itself (no getHelper())
  let armed: string | null = null;

  // Z-up config applied per mode: translate shows XY only; rotate shows the yaw Z-ring.
  function setMode(mode: 'translate' | 'rotate'): void {
    control.setMode(mode);
    control.showX = mode === 'translate';
    control.showY = mode === 'translate';
    control.showZ = mode === 'rotate';
  }
  setMode('translate');

  // Suspend OrbitControls while dragging so the camera doesn't fight the gizmo.
  const onDraggingChanged = (e: { value: boolean }): void => {
    opts.orbit.enabled = !e.value;
    if (!e.value && armed) void convertOnDrop(armed); // drag just ENDED
  };
  control.addEventListener('dragging-changed', onDraggingChanged as (e: THREE.Event) => void);

  // Live translation follow (translate mode only): mirror ONLY position to the mesh,
  // preserving its Python-owned det-−1 linear part. Heading is never mirrored.
  const onObjectChange = (): void => {
    if (armed === null || control.getMode() !== 'translate') return;
    const g = opts.groups[armed];
    if (!g) return;
    const z0 = g.matrix.elements[14]; // keep the plane's layer z
    g.matrix.setPosition(proxy.position.x, proxy.position.y, z0);
    g.matrixWorldNeedsUpdate = true;
  };
  control.addEventListener('objectChange', onObjectChange);

  // Keyboard mode toggle while armed: t = translate (move), r = rotate (heading).
  const onKey = (ev: KeyboardEvent): void => {
    if (armed === null) return;
    if (ev.key === 't') setMode('translate');
    else if (ev.key === 'r') setMode('rotate');
  };
  window.addEventListener('keydown', onKey);

  async function convertOnDrop(id: string): Promise<void> {
    try {
      const req = { x: proxy.position.x, y: proxy.position.y, world_yaw_rad: proxy.rotation.z };
      const resp = await fetch('/convert', convertRequestInit(req));
      if (!resp.ok) {
        let msg = String(resp.status);
        try { msg = (JSON.parse(await resp.text()) as { error?: string }).error ?? msg; } catch { /* keep status */ }
        banner('Fix position failed: ' + msg);
        return;
      }
      opts.onConverted(id, (await resp.json()) as ConvertResponse);
    } catch (e) {
      banner('Fix position failed: ' + (e as Error).message);
    }
  }

  return {
    arm(id: string): void {
      const c = opts.ctx.currentPoses[id];
      if (!c) return; // a palette-added plane has no pose to seed from
      const z0 = opts.groups[id]?.matrix.elements[14] ?? 0;
      proxy.position.set(c.x_m, c.y_m, z0);
      proxy.rotation.set(0, 0, c.world_yaw_rad); // Python seed — no JS trig (ADR-0002)
      setMode('translate');
      control.attach(proxy);
      const g = opts.groups[id];
      if (g) g.userData.heldByEditor = true; // stop the timeline driving this plane
      armed = id;
    },
    disarm(): void {
      control.detach();
      armed = null; // leaves heldByEditor set — cleared naturally when reRender rebuilds Groups
    },
    armedId: () => armed,
    dispose(): void {
      control.removeEventListener('dragging-changed', onDraggingChanged as (e: THREE.Event) => void);
      control.removeEventListener('objectChange', onObjectChange);
      window.removeEventListener('keydown', onKey);
      control.detach();
      control.dispose();
      opts.scene.remove(control);
      opts.scene.remove(proxy);
      armed = null;
    },
  };
}
```

- [ ] **Step 2: Typecheck + lint**

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint`
Expected: clean. (Nothing imports `manipulate.ts` yet, so `npm run build` won't bundle it — that starts in Task 4. `tsc --noEmit` still type-checks the file standalone.)
- If `TransformControls`'s `getMode`/`setSpace`/`showX` typings differ under `@types/three@0.160`, adjust to the real r160 surface (do **not** add `@ts-expect-error` to paper over a real API mismatch — fix the call). Verify the addon import path types resolve exactly as `OrbitControls` does.

- [ ] **Step 3: Commit**
```bash
git add viewer/src/interaction/manipulate.ts
git commit -m "feat(viewer): manipulate.ts — TransformControls drag-to-fix gizmo core (#911)"
```

---

### Task 4: Wire the gizmo into `editor.ts` + `calculate.ts` unsolved marker

**Files:**
- Modify: `viewer/src/interaction/editor.ts` (opts interface ~29–46; `syncControls` 183–238; the pin-fields injection block ~162–181 as a mirror; dispose 403–412)
- Modify: `viewer/src/interaction/calculate.ts` (return `{ markUnsolved }`; a `● unsolved` marker)
- Test: `viewer/test/calculate.test.ts` or an existing viewer test for `markUnsolved`

**Interfaces:**
- Consumes: `createManipulator`/`ManipulatorHandle` (Task 3), `pinAtPose` (Task 2), `ConvertResponse` (serve-contract).
- Produces: `mountEditor` opts gains **optional** `scene?: THREE.Scene`, `orbit?: OrbitControls`, `onEdit?: () => void`; `EditorHandle` is unchanged externally. `mountCalculate` now returns `{ markUnsolved(): void }`. Task 5's `main.ts` supplies the new opts + consumes the return.

- [ ] **Step 1: Write the failing `markUnsolved` test**

`viewer/test/calculate.test.ts` (new, or add to an existing serve/calculate test). `mountCalculate` touches the DOM (`document`), so the test needs a minimal DOM — if `viewer/test` has no jsdom harness, keep this a **pure** check on the returned marker function against a stubbed button element rather than a full mount. Simplest faithful unit:
```ts
// Verify mountCalculate returns a markUnsolved() that flags the button, and that
// a successful solve clears it. Requires a DOM; if viewer/test has no DOM harness,
// SKIP this unit and rely on the Task-5 headless smoke (note it in the report).
```
If there is no DOM harness in `viewer/test/` (grep for `jsdom`/`document` in the dir), do **not** invent one — omit this unit, implement Steps 2–4, and record in the task report that `markUnsolved` is covered by the headless smoke (Task 5) + review, consistent with the untested-DOM precedent.

- [ ] **Step 2: `calculate.ts` — return a marker, clear on success**

Change `mountCalculate`'s signature to return `{ markUnsolved(): void }`, add a visible `● unsolved` marker, and clear it after a successful `reRender`:
```ts
export function mountCalculate(opts: {
  getIntent: () => Intent;
  ctx: EditorContext;
  reRender: (resp: SolveResponse) => void;
}): { markUnsolved(): void } {
  const btn = document.createElement('button');
  btn.id = 'calculate';
  btn.type = 'button';
  btn.textContent = 'Calculate';
  const exportBtn = byId<HTMLButtonElement>('export');
  exportBtn.parentElement?.insertBefore(btn, exportBtn);

  const markUnsolved = (): void => { btn.classList.add('unsolved'); btn.textContent = 'Calculate ●'; };
  const clearUnsolved = (): void => { btn.classList.remove('unsolved'); btn.textContent = 'Calculate'; };

  async function run(): Promise<void> {
    btn.disabled = true;
    clearBanner();
    try {
      const yaml = intentToScenarioYaml(opts.getIntent(), opts.ctx);
      const resp = await fetch('/solve', solveRequestInit(yaml));
      if (!resp.ok) {
        let msg = `${resp.status}`;
        try { msg = (JSON.parse(await resp.text()) as { error?: string }).error ?? msg; } catch { /* keep status */ }
        banner('Calculate failed: ' + msg);
        return;
      }
      opts.reRender((await resp.json()) as SolveResponse);
      clearUnsolved(); // a successful solve reflects the current intent
    } catch (e) {
      banner('Calculate failed: ' + (e as Error).message);
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener('click', () => void run());
  return { markUnsolved };
}
```

- [ ] **Step 3: `editor.ts` — optional opts + manipulator + fix-position button**

3a. Extend the opts interface (keep new fields **optional** so `main.ts`'s existing call still type-checks until Task 5):
```ts
export function mountEditor(opts: {
  groups: Record<string, THREE.Group>;
  renderer: THREE.WebGLRenderer;
  cam: THREE.Camera;
  ctx: EditorContext;
  initialIntent?: Intent;
  // #911 PR B: present only when served (drag needs the /convert round-trip). When
  // both scene & orbit are supplied the gizmo + "fix position" button mount; absent
  // (offline export) the drag flow stays fully dormant (no TransformControls built).
  scene?: THREE.Scene;
  orbit?: OrbitControls;
  onEdit?: () => void; // notify the host a convert changed the intent (marks Calculate unsolved)
}): EditorHandle {
```

3b. After `syncControls`/`focusedId` are defined and the manipulator's deps exist, create the manipulator + button (only when served). Add imports at the top: `import { createManipulator } from './manipulate.ts'; import type { ManipulatorHandle } from './manipulate.ts'; import { pinAtPose } from './selection.ts';` (and `OrbitControls` type if referenced).
```ts
  // #911 PR B: drag-to-fix. Mounts iff served (scene & orbit provided).
  let manip: ManipulatorHandle | null = null;
  let fixBtn: HTMLButtonElement | null = null;
  if (opts.scene && opts.orbit) {
    manip = createManipulator({
      scene: opts.scene,
      groups: opts.groups,
      cam: opts.cam,
      renderer: opts.renderer,
      orbit: opts.orbit,
      ctx: opts.ctx,
      onConverted: (id, pose) => {
        // Carry onCarts from the existing pin, else the plane's current pose.
        const onCarts = intent.mustPositions[id]?.onCarts ?? opts.ctx.currentPoses[id]?.on_carts ?? false;
        intent = pinAtPose(intent, id, pose, onCarts);
        focusedId = id;
        syncControls();       // populate the x/y/heading pin fields (editable)
        opts.onEdit?.();      // flag Calculate "unsolved"
      },
    });
    fixBtn = document.createElement('button');
    fixBtn.id = 'fix-position';
    fixBtn.type = 'button';
    fixBtn.textContent = 'Fix position';
    exportBtn.parentElement?.insertBefore(fixBtn, exportBtn);
    fixBtn.addEventListener(
      'click',
      () => {
        if (!manip || focusedId === null) return;
        if (manip.armedId() === focusedId) manip.disarm();
        else manip.arm(focusedId);
      },
      sig,
    );
  }
```

3c. In `syncControls`, gate the fix-position button exactly like `pinToggle` (enabled only for a focused, selected plane that has a `currentPose`). Add near the `pinToggle.disabled` line:
```ts
    if (fixBtn) fixBtn.disabled = !active || !hasPose;
```

3d. In `dispose()`, tear the manipulator + button down:
```ts
    dispose: () => {
      ac.abort();
      pinFields.remove();
      manip?.dispose();     // #911 PR B
      fixBtn?.remove();     // #911 PR B
    },
```

- [ ] **Step 4: Typecheck + lint + existing tests**

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint && npm --prefix viewer/ run test`
Expected: clean (the optional opts keep `main.ts`'s current `mountEditor` call valid; `mountCalculate`'s new return is ignored by the current `main.ts` call — still valid). If the `markUnsolved` unit was written in Step 1, it passes.

- [ ] **Step 5: Commit**
```bash
git add viewer/src/interaction/editor.ts viewer/src/interaction/calculate.ts
# + the calculate test if one was added
git commit -m "feat(viewer): wire drag gizmo + fix-position button into the editor; Calculate unsolved marker (#911)"
```

---

### Task 5: `main.ts` wiring + rebuild `viewer.js` + headless smoke

**Files:**
- Modify: `viewer/src/main.ts` (`bootSingle`, lines 150–211)
- Modify: `src/hangarfit/_viewer_assets/viewer.js` (rebuilt bundle)

**Interfaces:**
- Consumes: the Task 4 `mountEditor` opts (`scene`/`orbit`/`onEdit`) + `mountCalculate`'s `{ markUnsolved }`.

- [ ] **Step 1: Thread serve-only opts + the marker into `bootSingle`**

Restructure the `if (ctxEl?.textContent)` block so the serve-config is parsed **before** `mountEditor`, and pass `scene`/`orbit`/`onEdit` only when served; wire the marker to `mountCalculate`'s return. Replace the editor/Calculate mount region (`main.ts` ~171–207):
```ts
  const ctxEl = document.getElementById('editor-context');
  if (ctxEl?.textContent) {
    const ctx = JSON.parse(ctxEl.textContent) as EditorContext;
    const serveCfg = parseServeConfig(document.getElementById('serve-config')?.textContent);

    // #911 PR B: the drag gizmo + "fix position" button mount only when served
    // (drag needs the /convert round-trip). markUnsolved() flags Calculate after a
    // drag-convert; it's assigned once Calculate mounts (below), so the closure is
    // safe to pass into the editor first.
    let markUnsolved: () => void = () => {};
    const editHostOpts = serveCfg
      ? { scene: stage.scene, orbit: stage.controls, onEdit: () => markUnsolved() }
      : {};
    let editor = mountEditor({ groups: world.groups, renderer: stage.renderer, cam: stage.cam, ctx, ...editHostOpts });

    if (serveCfg) {
      const calc = mountCalculate({
        getIntent: () => editor.getIntent(),
        ctx,
        reRender: (resp) => {
          const preserved = editor.getIntent();
          clearBanner();
          const nextWorld = buildWorld(stage.scene, resp.scene, brand);
          const nextEditor = mountEditor({
            groups: nextWorld.groups,
            renderer: stage.renderer,
            cam: stage.cam,
            ctx: resp.editorContext,
            initialIntent: preserved,
            scene: stage.scene,
            orbit: stage.controls,
            onEdit: () => markUnsolved(),
          });
          editor.dispose();
          stage.scene.remove(world.group);
          disposeWorld(world.group);
          world = nextWorld;
          editor = nextEditor;
          applyToggleState(world);
          setReadouts(resp.scene);
          hud.setActiveTimeline(world.timeline);
        },
      });
      markUnsolved = calc.markUnsolved;
    }
  }
```
Keep `world`/`editor` as the existing mutable `let`s. (The `mountEditor` call now spreads `editHostOpts`; confirm `stage.controls` is the `OrbitControls` instance per `RendererBundle` and `stage.scene` the `THREE.Scene`.)

- [ ] **Step 2: Typecheck + lint + unit tests**

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint && npm --prefix viewer/ run test`
Expected: clean/green.

- [ ] **Step 3: Rebuild the committed bundle**

Run: `npm --prefix viewer/ run build`
Expected: `src/hangarfit/_viewer_assets/viewer.js` is regenerated (now includes `manipulate.ts` + the wiring; `three/addons/controls/TransformControls.js` stays **external**, resolved at runtime by the Task-1 import-map). Confirm the import stayed external (not inlined):
```bash
grep -c "TransformControls" src/hangarfit/_viewer_assets/viewer.js   # references, but the addon source is NOT inlined
```

- [ ] **Step 4: Headless smoke (swiftshader)**

Render a served-style `--edit` page and confirm it mounts without a transform-mismatch banner. Because the gizmo only mounts when a `#serve-config` blob is present, drive it through `hangarfit serve` OR render an edit HTML that includes a serve-config blob. Minimal serve-based smoke:
```bash
# start serve in the background, screenshot the editor, confirm no TRANSFORM banner
hangarfit serve tests/fixtures/scenario_minimal.yaml --port 8766 --no-open &
SERVE_PID=$!
sleep 2
google-chrome --headless=new --use-gl=angle --use-angle=swiftshader \
  --enable-unsafe-swiftshader --virtual-time-budget=8000 \
  --dump-dom "http://127.0.0.1:8766/" > /tmp/edit-dom.html 2>/dev/null
kill $SERVE_PID
grep -q "fix-position" /tmp/edit-dom.html && echo "OK: fix-position button present"
grep -qi "TRANSFORM CHECK FAILED" /tmp/edit-dom.html && echo "FAIL: transform banner" || echo "OK: no transform banner"
```
Expected: `fix-position` button present, no transform banner. (Driving an actual drag headlessly is out of scope — the pose math is covered by PR A's Python `/convert` tests + Task 2's `pinAtPose` unit.) If `serve` lacks a `--no-open` flag or the DOM dump can't reach it, fall back to opening the `serve` `GET /` HTML via `--screenshot` per the CLAUDE.md viewer recipe; the assertions (button present, no banner) are the point.

- [ ] **Step 5: Commit the bundle + wiring**
```bash
git add viewer/src/main.ts src/hangarfit/_viewer_assets/viewer.js
git commit -m "feat(viewer): mount drag gizmo when served + rebuild bundle (#911)"
```

---

### Task 6: CHANGELOG + PR + review arc

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: CHANGELOG entry**

Add under `## [Unreleased]` → `### Added` (after the PR-A `drag-to-fix backend` entry):
```markdown
- **Editor drag-to-fix placement** — with `hangarfit serve`, the `--edit` viewer gained a **Fix position** button: focus a plane, drag it on the hangar floor and set its heading with a gizmo, and on drop the world pose round-trips through the solve-free `POST /convert` (Python owns the determinant-−1 inverse, ADR-0002) into a normal, editable pin — then **Calculate** re-solves with it pinned. The plane mesh slides live during the drag (translation only; heading shows on the gizmo, never rotating the reflected mesh in JS). Offline exports are unchanged in behavior — the drag flow is dormant without a serve backend. Completes #911 (client half; the `/convert` backend shipped in PR A). (#911)
```

- [ ] **Step 2: Push + open the draft PR**
```bash
git push -u origin feature/911b-client-gizmo
```
Then `gh pr create --draft --base develop` with a body (via `--body-file`) containing `Closes #911`, the "PR B of two — client half" framing, the ADR-0002/0003/0017 invariants, the vendoring note, and the testing summary. Set assignee `DocGerd` + labels `enhancement`,`area:viewer` + milestone via `gh api` (see CLAUDE.md — `gh pr edit` is unreliable here).

- [ ] **Step 3: Review arc**

Final whole-branch review package (`review-package MERGE_BASE HEAD`, `MERGE_BASE = git merge-base develop HEAD`), dispatched to:
- `scene-schema-guard` — rebuilt `viewer.js`, `viewer.py` import-map change, no scene/v2 drift, editor-context untouched. **Empirically rebuild `viewer.js` and diff byte-identical.**
- `code-reviewer` — the whole client change.
- (`comment-analyzer` if the docs/comments footprint is non-trivial.)

Convert findings to inline threads on the reviewed commit (anchor **before** pushing fixes so they auto-outdate), fix Critical/Important (record Minors in the ledger), reply + resolve every thread, post a review-evidence summary. Flip out of draft (`gh pr ready`) when the arc is clean. **Do not merge — the user is the sole merger.**

- [ ] **Step 4: Commit CHANGELOG**
```bash
git add CHANGELOG.md
git commit -m "docs(changelog): #911 PR B editor drag-to-fix (#911)"
```
(Push with the branch; the CHANGELOG may also be folded into Step 2's initial push if authored first.)

---

## Self-Review (author checklist — done)

1. **Spec coverage:** §3.1 `/convert` (PR A, done) · §3.2 `world_yaw_rad` seed (PR A, done) · §3.3 vendor TransformControls → **T1** · §3.4 manipulator → **T3** (as `createManipulator` used by `editor.ts`, not a standalone `main.ts` `mountManipulate` — a deliberate refinement: `focusedId` is `editor.ts`-private and the button's enablement must track focus+selection, which only `syncControls` sees; behavior/ADR-compliance/data-flow are identical) · §3.5 hold-gate → **T2** · §3.6 `pinAtPose` + `onConverted` → **T2**/**T4** · §3.7 fix-position button + unsolved marker → **T4** · §3.8 serve-contract types (PR A, done) · §6 tests → each task + T5 smoke · §8 PR B delivery → **T6**.
2. **Placeholder scan:** the only intentionally-open spots are the `miniScene()` `SceneV2` literal (T2.5) and the DOM-harness check (T4.1) — both give the implementer a concrete fallback (reuse an existing fixture / omit-and-note) rather than a vague "TBD".
3. **Type consistency:** `ManipulatorHandle`/`createManipulator` (T3) match their use in T4; `mountEditor`'s new optional opts (T4) match the T5 call; `mountCalculate`'s `{ markUnsolved }` return (T4) matches T5's `calc.markUnsolved`. `pinAtPose` signature identical in T2 (def) and T4 (use). `ConvertResponse`/`convertRequestInit` are PR-A types (confirmed present).
4. **Deviation flagged for review:** the §3.4 module-shape refinement (above) — the reviewer should adjudicate if they prefer the literal spec shape.
