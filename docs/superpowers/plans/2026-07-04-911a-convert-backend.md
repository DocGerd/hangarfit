# #911 PR A — Drag-to-fix Backend (`/convert` + gizmo seed) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Python/serve half of #911 drag-to-fix: a solve-free `POST /convert` that turns a dragged world pose into a scenario pin (Python owns the determinant-−1 inverse), and a Python-owned `world_yaw_rad` gizmo seed in the editor-context — so PR B's client gizmo does zero heading↔yaw trig.

**Architecture:** `POST /convert` (a sibling of `/solve` in `server.py`) is a pure geometry map — `x_m=x, y_m=y` (translation is identity) and `heading_deg = towplanner.math_rad_to_compass(world_yaw_rad)` (the existing tested involution) — that never calls the solver. `build_editor_context.currentPoses` gains `world_yaw_rad = towplanner.compass_to_math_rad(heading_deg)` (the forward companion). No client gizmo/vendoring here (that is PR B).

**Tech Stack:** Python 3.12 (stdlib `http.server`), pytest; TypeScript type-only edits (`intent-contract.ts`, `serve-contract.ts`) — no `viewer.js` runtime change.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-04-911-drag-to-fix-position-design.md` (§3.1, §3.2, §3.8, §6, §8 "PR A").
- **Branch:** `feature/911-convert-backend` (already created off `develop`; the spec is already committed there at `f79635f`).
- **ADR-0002/0029:** all heading↔yaw math stays in Python — `math_rad_to_compass` (inverse) + `compass_to_math_rad` (seed). No JS trig is added.
- **ADR-0003:** `/convert` does **no** solving — RNG-free, deterministic, never calls `solve`/`build_scene`.
- **`math_rad_to_compass` / `compass_to_math_rad`** live in `src/hangarfit/towplanner.py` (a tested mutual-inverse pair). Import them; do not reimplement.
- **`world_yaw_rad` is an editor-context field, NOT scene/v2** — `build_scene` and scene/v2 are untouched.
- **No client gizmo / `manipulate.ts` / TransformControls vendoring** — that is PR B.
- **Directory-aware commands:** `npm --prefix viewer/ run <script>`; absolute paths for pytest.
- **Delivery:** draft PR → review arc (`pr-review-toolkit:code-reviewer` + `pr-review-toolkit:silent-failure-hunter` for the new endpoint's error paths) → resolve → `gh pr ready`. Never merge from Claude.

---

## File Structure

- **Modify** `src/hangarfit/towplanner.py` — *(none; functions already exist)*; **Test** `tests/test_towplanner_dubins.py` — add the involution round-trip guard.
- **Modify** `src/hangarfit/viewer.py` — import `compass_to_math_rad`; add `world_yaw_rad` to `build_editor_context.currentPoses`.
- **Modify** `viewer/src/interaction/intent-contract.ts` — add `world_yaw_rad: number` to `CurrentPose`.
- **Modify** `viewer/test/selection.test.ts` (+ any other TS fixture building a `CurrentPose`) — add `world_yaw_rad` so `tsc` passes.
- **Test** `tests/test_scene.py` — nested `CurrentPose` key-parity + `world_yaw_rad` value test.
- **Modify** `src/hangarfit/server.py` — `import math`; import `math_rad_to_compass`; refactor `do_POST` into a path dispatch with a shared `_read_body`; add `_handle_convert` + `_convert_pose`.
- **Modify** `viewer/src/interaction/serve-contract.ts` — add `ConvertRequest`/`ConvertResponse`/`convertRequestInit`.
- **Test** `tests/test_server.py` — `/convert` happy path, bad-JSON 400, missing-field 400, non-finite 400, solve-free.
- **Modify** `CHANGELOG.md` — one `[Unreleased]` entry.

---

### Task 1: Involution round-trip guard (the math the whole feature rests on)

**Files:**
- Test: `tests/test_towplanner_dubins.py`

**Interfaces:**
- Consumes (existing): `compass_to_math_rad(heading_deg: float) -> float` (= `radians(90 - heading)`), `math_rad_to_compass(theta_rad: float) -> float` (= `(90 - degrees(theta)) % 360`) from `hangarfit.towplanner`.

- [ ] **Step 1: Add the failing test**

`tests/test_towplanner_dubins.py` already imports `compass_to_math_rad`. Extend that import line to also import `math_rad_to_compass`:

```python
from hangarfit.towplanner import Pose, _dubins_shortest, compass_to_math_rad, math_rad_to_compass, plan_dubins
```

Then add (near the existing `test_compass_to_math_rad_cardinals`):

```python
def test_compass_math_round_trips_are_mutual_inverses() -> None:
    # #911: the drag-to-fix round-trip is Python-owned (ADR-0002) — the editor-context
    # gizmo seed uses compass_to_math_rad and server.py's /convert uses
    # math_rad_to_compass. They MUST be mutual inverses across the heading range, or a
    # dragged plane pins to the wrong heading.
    for heading in (0.0, 30.0, 45.0, 90.0, 179.9, 270.0, 359.0):
        assert math_rad_to_compass(compass_to_math_rad(heading)) == pytest.approx(heading)
```

- [ ] **Step 2: Run it (expect PASS — both functions already exist and are inverses)**

Run: `pytest tests/test_towplanner_dubins.py::test_compass_math_round_trips_are_mutual_inverses -v`
Expected: PASS. (This is a *characterization* guard on existing behavior, not a RED→GREEN cycle — if it fails, STOP: the pair is not actually invertible and the whole feature premise is wrong.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_towplanner_dubins.py
git commit -m "test(towplanner): guard compass↔math heading involution (#911)

The drag-to-fix round-trip rests on math_rad_to_compass ∘ compass_to_math_rad
being identity; pin it before building /convert + the gizmo seed on it.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd"
```

---

### Task 2: `world_yaw_rad` gizmo seed (Python emit + TS type + parity)

**Files:**
- Modify: `src/hangarfit/viewer.py` (imports; `build_editor_context.currentPoses` ~157-165)
- Modify: `viewer/src/interaction/intent-contract.ts` (`CurrentPose`)
- Modify: `viewer/test/selection.test.ts` (+ any other TS fixture constructing a `CurrentPose`)
- Test: `tests/test_scene.py`

**Interfaces:**
- Produces: each `editorContext.currentPoses[id]` gains `world_yaw_rad: number` = `compass_to_math_rad(heading_deg)`. `CurrentPose` (TS) gains `world_yaw_rad: number`. PR B consumes this to seed the gizmo proxy.

- [ ] **Step 1: Write the failing tests** (in `tests/test_scene.py`, beside the existing `test_editor_context_ts_keys_match_build_editor_context` at line 532)

```python
def test_editor_context_current_pose_ts_keys_match():
    # #911: currentPoses entries mirror intent-contract.ts's CurrentPose. That is a
    # NESTED interface, so the top-level EditorContext parity test above does not
    # cover it — guard the pose key set so world_yaw_rad (the drag gizmo seed) can't
    # drift between Python and TS.
    from hangarfit import viewer

    lay = load_layout(LAYOUT)
    ctx = viewer.build_editor_context(
        fleet_ref="data/fleet.yaml",
        hangar_ref="data/hangar.yaml",
        maintenance_plane=lay.maintenance_plane,
        layout=lay,
    )
    pose = next(iter(ctx["currentPoses"].values()))
    assert _ts_interface_fields("interaction/intent-contract.ts", "CurrentPose") == set(pose)


def test_editor_context_current_pose_world_yaw_seed_value():
    # #911: world_yaw_rad = compass_to_math_rad(heading_deg) (= radians(90 - heading)),
    # the Python-owned forward transform the drag gizmo's clean proxy is seeded with
    # (the browser does no heading↔yaw trig, ADR-0002).
    from hangarfit import viewer
    from hangarfit.towplanner import compass_to_math_rad

    lay = load_layout(LAYOUT)
    ctx = viewer.build_editor_context(
        fleet_ref="data/fleet.yaml",
        hangar_ref="data/hangar.yaml",
        maintenance_plane=lay.maintenance_plane,
        layout=lay,
    )
    for pose in ctx["currentPoses"].values():
        assert pose["world_yaw_rad"] == pytest.approx(compass_to_math_rad(pose["heading_deg"]))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_scene.py::test_editor_context_current_pose_ts_keys_match tests/test_scene.py::test_editor_context_current_pose_world_yaw_seed_value -v`
Expected: FAIL — `world_yaw_rad` is not in `currentPoses` (value test `KeyError`) and the TS `CurrentPose` lacks it / Python lacks it (parity set mismatch).

- [ ] **Step 3: Emit `world_yaw_rad` in `build_editor_context`** (`src/hangarfit/viewer.py`)

Add the import (beside `from hangarfit.models import Layout`, viewer.py:19):

```python
from hangarfit.towplanner import compass_to_math_rad
```

In `build_editor_context`, extend the `currentPoses` comprehension (viewer.py:157-165):

```python
        "currentPoses": {
            p.plane_id: {
                "x_m": p.x_m,
                "y_m": p.y_m,
                "heading_deg": p.heading_deg,
                "on_carts": p.on_carts,
                # #911: the world-space yaw (radians, math convention) the drag
                # gizmo's clean PROXY is seeded with. Python-owned so the browser
                # does no heading↔yaw trig (ADR-0002); its inverse is server.py's
                # /convert. compass_to_math_rad(h) = radians(90 - h).
                "world_yaw_rad": compass_to_math_rad(p.heading_deg),
            }
            for p in layout.placements
        },
```

- [ ] **Step 4: Add the field to the TS `CurrentPose` interface** (`viewer/src/interaction/intent-contract.ts`)

```ts
export interface CurrentPose { x_m: number; y_m: number; heading_deg: number; on_carts: boolean; world_yaw_rad: number; }
```

- [ ] **Step 5: Fix TS fixtures that construct a `CurrentPose`**

`world_yaw_rad` is now required, so every TS object literal typed as `CurrentPose` must include it. Find them:

Run: `grep -rn "heading_deg" viewer/test viewer/src/interaction`
For each `currentPoses` fixture entry (at least `viewer/test/selection.test.ts`'s `CTX` — the `husky`/`ctsl` poses), add `world_yaw_rad: 0` (an arbitrary valid number; these fixtures don't exercise the gizmo). Example for `selection.test.ts`:

```ts
  currentPoses: {
    husky: { x_m: 2.1, y_m: 14.3, heading_deg: 0, on_carts: false, world_yaw_rad: 0 },
    ctsl: { x_m: 5.0, y_m: 3.0, heading_deg: 90, on_carts: false, world_yaw_rad: 0 },
  },
```

- [ ] **Step 6: Run the Python + TS checks**

Run: `pytest tests/test_scene.py -k "current_pose" -v`
Expected: PASS (both new tests).

Run: `npm --prefix viewer/ run typecheck`
Expected: no errors (confirms every `CurrentPose` fixture got the field).

Run: `npm --prefix viewer/ run test`
Expected: PASS (existing node units unaffected by the added optional-in-practice field).

- [ ] **Step 7: Confirm `viewer.js` is byte-identical (type-only change)**

`intent-contract.ts` is type-only (interfaces erase at build). Rebuild and confirm no bundle drift:

Run: `npm --prefix viewer/ run build && git status --short src/hangarfit/_viewer_assets/viewer.js`
Expected: **no output** (viewer.js unchanged). If it changed, STOP and report — a type-only edit must not alter the bundle.

- [ ] **Step 8: Confirm no unrelated Python test pinned the old pose shape**

Run: `pytest tests/test_viewer.py tests/test_scene.py -q`
Expected: PASS. (The `--edit` HTML's editor-context blob now carries `world_yaw_rad`; if a test asserted the exact old `currentPoses` shape/bytes, update it to include the new field — a deliberate, additive change. The `#scene` byte-identity and `viewer.js`-verbatim tests are unaffected.)

- [ ] **Step 9: Commit**

```bash
git add src/hangarfit/viewer.py viewer/src/interaction/intent-contract.ts viewer/test/selection.test.ts tests/test_scene.py
git commit -m "feat(viewer): Python-owned world_yaw_rad gizmo seed in editor-context (#911)

currentPoses carries world_yaw_rad = compass_to_math_rad(heading_deg) so PR B's
drag gizmo proxy is seeded without any JS heading↔yaw trig (ADR-0002). Nested
CurrentPose key-parity + value tests added; intent-contract.ts + fixtures updated.
Type-only TS change — viewer.js byte-identical.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd"
```

*(If `grep` in Step 5 surfaced more fixture files than `selection.test.ts`, add them to the `git add` list.)*

---

### Task 3: `POST /convert` endpoint (solve-free pose→pin)

**Files:**
- Modify: `src/hangarfit/server.py` (imports; `do_POST` ~186-212; new `_read_body`/`_handle_solve`/`_handle_convert`/`_convert_pose`)
- Modify: `viewer/src/interaction/serve-contract.ts`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes (existing): `math_rad_to_compass` (Task 1's math), `_send_json`, `_host_ok`, `self._seed`, `_solve_scene`.
- Produces: `POST /convert` — request JSON `{x, y, world_yaw_rad}` → response `{x_m, y_m, heading_deg}`. `_convert_pose(body: str) -> dict`. TS `ConvertRequest`/`ConvertResponse`/`convertRequestInit` (PR B consumes).

- [ ] **Step 1: Write the failing tests** (`tests/test_server.py`, after the existing `/solve` tests)

```python
def test_post_convert_returns_pin_for_a_dragged_pose(live_server: int) -> None:
    import math

    # heading 30° → world_yaw_rad = radians(90 - 30); /convert must invert it back.
    yaw = math.radians(90.0 - 30.0)
    c = _conn(live_server)
    c.request(
        "POST", "/convert",
        body=json.dumps({"x": 4.2, "y": 7.1, "world_yaw_rad": yaw}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert doc == pytest.approx({"x_m": 4.2, "y_m": 7.1, "heading_deg": 30.0})


def test_post_convert_is_solve_free(live_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    import math

    # /convert must never reach the solver: replace solve with a bomb; /convert still
    # 200s with the pure-geometry pin (proving it does no solving — ADR-0003).
    def _boom(*a: object, **k: object) -> object:
        raise RuntimeError("solve must not be called by /convert")

    monkeypatch.setattr(server, "solve", _boom)
    yaw = math.radians(90.0 - 45.0)
    c = _conn(live_server)
    c.request(
        "POST", "/convert",
        body=json.dumps({"x": 1.0, "y": 2.0, "world_yaw_rad": yaw}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert doc["heading_deg"] == pytest.approx(45.0)


def test_post_convert_malformed_json_is_400(live_server: int) -> None:
    c = _conn(live_server)
    c.request("POST", "/convert", body=b"{not json", headers={"Content-Type": "application/json"})
    resp = c.getresponse()
    assert resp.status == 400
    assert "error" in json.loads(resp.read().decode("utf-8"))


def test_post_convert_missing_field_is_400(live_server: int) -> None:
    c = _conn(live_server)
    c.request(
        "POST", "/convert",
        body=json.dumps({"x": 1.0, "y": 2.0}).encode("utf-8"),  # no world_yaw_rad
        headers={"Content-Type": "application/json"},
    )
    resp = c.getresponse()
    assert resp.status == 400


def test_post_convert_non_finite_is_400(live_server: int) -> None:
    c = _conn(live_server)
    c.request(
        "POST", "/convert",
        body=b'{"x": 1.0, "y": 2.0, "world_yaw_rad": Infinity}',  # Python json accepts Infinity
        headers={"Content-Type": "application/json"},
    )
    resp = c.getresponse()
    assert resp.status == 400
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_server.py -k convert -v`
Expected: FAIL — `/convert` is currently a 404 (the happy-path asserts 200; the 400 tests get 404).

- [ ] **Step 3: Implement the endpoint** (`src/hangarfit/server.py`)

Add `import math` (beside `import json`, server.py:19) and the towplanner import (beside the other `hangarfit` imports, server.py:30-34):

```python
from hangarfit.towplanner import math_rad_to_compass
```

Add the pure converter (module-level, beside `_solve_scene`):

```python
def _convert_pose(body: str) -> dict:
    """Convert a dragged WORLD floor pose to a scenario pin — SOLVE-FREE and
    RNG-free (#911). Position is identity (world XY == scenario x_m/y_m, the
    ``_pose_affine`` translation columns in ``scene.py``); heading is the tested
    compass↔math involution (``towplanner``). The browser never authors this
    inverse (ADR-0002). Raises ``ValueError``/``KeyError``/``TypeError`` on a
    malformed body (mapped to a 400 by the handler)."""
    data = json.loads(body)
    x = float(data["x"])
    y = float(data["y"])
    yaw = float(data["world_yaw_rad"])
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(yaw)):
        raise ValueError("x, y, world_yaw_rad must be finite")
    return {"x_m": x, "y_m": y, "heading_deg": math_rad_to_compass(yaw)}
```

Refactor `do_POST` (server.py:186-212) into a path dispatch with a shared body reader. Replace the whole method with:

```python
    def _read_body(self) -> str | None:
        """Read+decode the request body, or send a 400 and return None on bad framing."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                raise ValueError("negative Content-Length")
            return self.rfile.read(length).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as e:
            # Malformed request framing (bad Content-Length / non-UTF-8 body): an
            # actionable 400, never a dropped connection + stderr traceback.
            self._send_json(400, {"error": f"bad request body: {e}"})
            return None

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": "non-loopback Host rejected"})
            return
        if self.path == "/solve":
            self._handle_solve()
        elif self.path == "/convert":
            self._handle_convert()
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_solve(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            scene, ctx = _solve_scene(self._seed, body)
        except LoaderError as e:
            self._send_json(422, {"error": str(e)})
            return
        except Exception:  # unexpected: log the stack server-side, generic 500 out
            traceback.print_exc(file=sys.stderr)
            self._send_json(500, {"error": "internal error"})
            return
        self._send_json(200, {"scene": scene, "editorContext": ctx})

    def _handle_convert(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            pin = _convert_pose(body)
        except (ValueError, KeyError, TypeError) as e:
            # Malformed convert payload (bad JSON / missing / non-numeric / non-finite):
            # an actionable 400, never a dropped connection + stderr traceback.
            self._send_json(400, {"error": f"bad convert request: {e}"})
            return
        except Exception:  # defensive: unexpected -> log + generic 500
            traceback.print_exc(file=sys.stderr)
            self._send_json(500, {"error": "internal error"})
            return
        self._send_json(200, pin)
```

*(This preserves the existing `/solve` behavior byte-for-byte — the read+solve+errors move verbatim into `_read_body`+`_handle_solve`.)*

- [ ] **Step 4: Run the `/convert` tests + the existing `/solve` tests**

Run: `pytest tests/test_server.py -v`
Expected: PASS — the 5 new `/convert` tests and every existing `/solve`/`GET` test (the refactor is behavior-preserving).

- [ ] **Step 5: Add the TS serve-contract types** (`viewer/src/interaction/serve-contract.ts`)

Append (beside the existing `SolveResponse`/`solveRequestInit`):

```ts
// #911 drag-to-fix: POST /convert turns a dragged WORLD floor pose into a scenario
// pin. Python owns the determinant-−1 inverse (ADR-0002); the client never computes
// heading↔yaw. world_yaw_rad is read off the gizmo proxy's rotation.z.
export interface ConvertRequest { x: number; y: number; world_yaw_rad: number; }
export interface ConvertResponse { x_m: number; y_m: number; heading_deg: number; }
export function convertRequestInit(req: ConvertRequest): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req) };
}
```

- [ ] **Step 6: Typecheck + confirm no bundle drift**

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint`
Expected: no errors.

Run: `npm --prefix viewer/ run build && git status --short src/hangarfit/_viewer_assets/viewer.js`
Expected: **no output** — nothing in `main.ts`'s import graph uses `convertRequestInit` yet (PR B wires it), so esbuild does not bundle it and `viewer.js` is byte-identical.

- [ ] **Step 7: Ruff + mypy the Python change**

Run: `ruff check src/hangarfit/server.py tests/test_server.py && mypy src/hangarfit/`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/hangarfit/server.py viewer/src/interaction/serve-contract.ts tests/test_server.py
git commit -m "feat(serve): POST /convert — solve-free dragged-pose → scenario pin (#911)

A sibling of /solve that maps a raw world floor pose to {x_m,y_m,heading_deg}:
identity translation + the tested compass↔math heading involution, never touching
the solver (ADR-0003) — the Python-owned inverse of the world_yaw_rad gizmo seed
(ADR-0002). do_POST refactored into a path dispatch with a shared body reader;
/solve behavior unchanged. serve-contract.ts gains ConvertRequest/Response.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd"
```

---

### Task 4: CHANGELOG + draft PR + review arc

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the CHANGELOG entry**

Under `## [Unreleased]` → `### Added` (create it in the conventional Added/Changed/Fixed order if absent), add:

```markdown
- `hangarfit serve` gains a solve-free `POST /convert` endpoint that turns a dragged world pose into a scenario pin (Python owns the coordinate inverse), and the editor-context now carries a per-plane `world_yaw_rad` gizmo seed — the backend half of interactive drag-to-fix placement. (#911)
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): #911 PR A serve /convert + gizmo seed

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd"
```

- [ ] **Step 3: Push + open the draft PR** *(controller step)*

```bash
git push -u origin feature/911-convert-backend
```

Write the PR body to a scratch file (never inline a `gh` body — the git-guard hook), then:

```bash
gh pr create --draft --base develop \
  --title "feat(serve): #911 PR A — /convert endpoint + world_yaw_rad gizmo seed" \
  --body-file <scratch>/pr911a-body.md
```

Body: `Closes #911` is **NOT** used here (this is PR A of two — #911 closes with PR B). Use `Part of #911` / `Refs #911`. Summarize the `/convert` op + the seed + "PR B (client gizmo) follows". Set metadata via REST (`gh api -X POST .../issues/<n>/labels` = `enhancement`,`area:backend`; assignee `DocGerd`; no milestone).

- [ ] **Step 4: Review arc** *(controller step)*

Dispatch on the PR diff (read-only, `origin/feature/911-convert-backend`):
- `pr-review-toolkit:code-reviewer` — main pass.
- `pr-review-toolkit:silent-failure-hunter` — the new `/convert` error paths (400/500 mapping, the `_read_body` refactor, non-finite guard).
- `scene-schema-guard` — confirm `world_yaw_rad` is an editor-context (not scene/v2) change and `build_scene` is untouched; `viewer.js` byte-identical.

Convert findings to inline threads (batch if ≥5, anchored to the reviewed commit), fix, reply + `resolveReviewThread`. Re-review if non-trivial. Then `gh pr ready <n>` and tell the user it is clean and ready for final review. Never merge.

---

## Self-Review

**1. Spec coverage (§ vs task):**
- §3.1 `POST /convert` solve-free + math + errors → Task 3 (`_convert_pose`, `_handle_convert`, 5 tests incl. solve-free + non-finite). ✓
- §3.2 `world_yaw_rad` seed + `CurrentPose` type + parity → Task 2. ✓
- §3.8 `ConvertRequest`/`ConvertResponse`/`convertRequestInit` → Task 3 Step 5. ✓
- §6 tests: `/convert` happy+error+solve-free (Task 3), `world_yaw_rad` value + parity (Task 2), involution round-trip (Task 1), byte-drift checks (Task 2 Step 7, Task 3 Step 6). ✓
- §8 PR A delivery + review arc (code-reviewer + silent-failure-hunter) → Task 4. ✓
- Explicitly **out of scope** (PR B): TransformControls vendoring, `manipulate.ts`, hold-gate, `pinAtPose`, "fix position" button — none appear in a task. ✓

**2. Placeholder scan:** every code step shows full code; commands have expected output. `<n>`/`<scratch>` are runtime values (PR number, scratchpad path), not placeholders.

**3. Type consistency:** `world_yaw_rad` (Python key + TS `CurrentPose` field + fixtures) consistent across Task 2. `_convert_pose(body: str) -> dict` returns `{x_m, y_m, heading_deg}`, matching the TS `ConvertResponse` and the Task 3 happy-path assertion. `convertRequestInit(req: ConvertRequest)` matches `{x, y, world_yaw_rad}`, the request the happy-path test POSTs and `_convert_pose` reads.
