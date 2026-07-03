# `hangarfit serve` Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `hangarfit serve` subcommand exposing a loopback HTTP backend so the interactive editor can trigger the solve live (the **Calculate** button) instead of exporting a YAML file to re-run by hand.

**Architecture:** Pure transport. `serve` reuses the existing `load_scenario → solve → build_scene → build_editor_context → render_edit_viewer` pipeline verbatim; it adds a stdlib `http.server` bound to `127.0.0.1` and a client bootstrap that `fetch()`es a fresh scene. Python stays the sole solver/transform authority (ADR-0002); determinism (ADR-0003) is transport-neutral (one runtime); the offline single-file export (ADR-0017) is untouched.

**Tech Stack:** Python 3.12 stdlib (`http.server`, `webbrowser`, `tempfile`), existing `hangarfit` modules; TypeScript (esbuild bundle, `three` r160), `node:test`.

## Global Constraints

- **Python 3.12 only** (ADR-0009). Standard library only for the server — **no** flask/fastapi (matches the no-heavy-deps ethos).
- **Loopback-only:** the server binds `127.0.0.1`. No `--host` flag; a LAN/remote bind is a deliberate non-feature.
- **Determinism (ADR-0003):** `serve` adds no solver/geometry change. The **offline** `render_edit_viewer` output stays **byte-identical** — the `serve-config` blob only ever appears when `serve` renders, never in the file export.
- **Transform policy (ADR-0002/0029):** the browser never composes/inverts the determinant-−1 transform. It sends the exported Scenario YAML and consumes Python-emitted scene affines.
- **`scene/v2` seam (ADR-0017):** `build_scene` is unchanged. `scene-schema-guard` applies to any `viewer.py`/`scene.py`/contract touch. The `serve-config` blob is a viewer-HTML-level artifact, **not** a scene/v2 change (like `#solutions` and `#editor-context`).
- **Input safety:** the server relies on the loader's existing `yaml.safe_load` + `_ALLOWED_SCENARIO_KEYS` allowlist. No new deserialization path.
- **Viewer bundle:** after any `viewer/src/*.ts` edit, rebuild `src/hangarfit/_viewer_assets/viewer.js` and commit it in the same change (the `viewer-build-drift` CI guard, #438).
- **Commits:** frequent, per-step. End commit messages with the two trailer lines the repo uses (`Co-Authored-By:` / `Claude-Session:`).

---

## File Structure

**Python (create):**
- `src/hangarfit/server.py` — the HTTP backend: a `SeedContext` dataclass, `build_seed(...)` (initial load+solve+scene+ctx), `make_server(...)` (returns a `ThreadingHTTPServer`), the `_Handler` (`do_GET` `/`, `do_POST` `/solve`, Host-guard, JSON errors), and a blocking `serve(...)` entrypoint.

**Python (modify):**
- `src/hangarfit/viewer.py` — extract `build_edit_html(scene, context, *, serve_config=None) -> str` (string-returning); `render_edit_viewer` calls it. `serve_config` adds a `#serve-config` blob only when provided.
- `src/hangarfit/cli.py` — add the `serve` subparser, `cmd_serve`, and the `main()` dispatch arm.

**Python tests (create/modify):**
- `tests/test_server.py` (create) — threaded-server integration tests on an ephemeral port.
- `tests/test_viewer.py` (modify) — `build_edit_html` byte-identity + `serve-config` blob tests.
- `tests/test_cli.py` (modify) — `cmd_serve` arg wiring (monkeypatched `serve`).

**Client (create):**
- `viewer/src/serve-contract.ts` — `ServeConfig` interface + pure `parseServeConfig` / `solveRequestInit` helpers.
- `viewer/src/interaction/calculate.ts` — `mountCalculate(...)`: creates the Calculate button, POSTs, re-renders.
- `viewer/test/serve-contract.test.ts` — node units for the pure helpers.

**Client (modify):**
- `viewer/src/interaction/editor.ts` — optional `initialIntent` seed + `AbortController` `dispose()`; `EditorHandle` gains `dispose`.
- `viewer/src/main.ts` — `bootSingle` holds `world`/`editor`/`hud` mutably and wires `mountCalculate` under a `#serve-config` blob.
- `src/hangarfit/_viewer_assets/viewer.js` — rebuilt bundle (committed).

**Docs (create/modify):**
- `docs/adr/0030-hangarfit-serve-local-backend.md` (create).
- `CHANGELOG.md`, `CLAUDE.md`, `docs/architecture/05-building-block-view.md` (modify).

---

## Task 1: `build_edit_html` refactor + `serve-config` blob (viewer.py)

**Files:**
- Modify: `src/hangarfit/viewer.py:203-214` (`render_edit_viewer`) + a new `build_edit_html`
- Test: `tests/test_viewer.py`

**Interfaces:**
- Produces: `build_edit_html(scene: dict, context: dict, *, serve_config: dict | None = None) -> str` — returns the full edit-viewer HTML string. `render_edit_viewer(scene, context, output_path)` becomes a thin file-writing wrapper. When `serve_config` is `None` the byte output equals today's `render_edit_viewer`.
- The `serve-config` blob schema constant `_SERVE_CONFIG_SCHEMA = "hangarfit.serve-config/v1"`.

- [ ] **Step 1: Write the failing byte-identity + serve-config tests**

Add to `tests/test_viewer.py` (near the existing edit-viewer tests ~line 359):

```python
def test_build_edit_html_matches_render_edit_viewer_bytes(tmp_path):
    # build_edit_html (string) with no serve_config must be byte-identical to the
    # file render_edit_viewer writes — the offline path is unchanged (ADR-0003).
    sc = _scene_for_test()  # existing helper used by the edit tests below
    ctx = viewer.build_editor_context(
        fleet_ref="data/fleet.yaml",
        hangar_ref="data/hangar.yaml",
        maintenance_plane=None,
        layout=_layout_for_test(),
    )
    out = tmp_path / "edit.html"
    viewer.render_edit_viewer(sc, ctx, out)
    assert viewer.build_edit_html(sc, ctx) == out.read_text(encoding="utf-8")


def test_build_edit_html_serve_config_adds_blob_only_when_given():
    sc = _scene_for_test()
    ctx = viewer.build_editor_context(
        fleet_ref="data/fleet.yaml",
        hangar_ref="data/hangar.yaml",
        maintenance_plane=None,
        layout=_layout_for_test(),
    )
    without = viewer.build_edit_html(sc, ctx)
    assert 'id="serve-config"' not in without
    with_cfg = viewer.build_edit_html(sc, ctx, serve_config={"schema": "hangarfit.serve-config/v1"})
    assert 'id="serve-config"' in with_cfg
    # additive: the without-blob bytes are a prefix-preserving subset (scene/ctx unchanged)
    assert 'id="editor-context"' in with_cfg and 'id="scene"' in with_cfg
```

If the existing edit tests build `sc`/`layout` inline rather than via `_scene_for_test()`/`_layout_for_test()` helpers, reuse whatever fixture builders those tests already use (grep `def test_render_edit_viewer_keeps_scene_bytes` at `tests/test_viewer.py:359` and copy its scene/layout setup verbatim into the two new tests).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_viewer.py -k "build_edit_html" -v`
Expected: FAIL with `AttributeError: module 'hangarfit.viewer' has no attribute 'build_edit_html'`.

- [ ] **Step 3: Implement `build_edit_html` and rewire `render_edit_viewer`**

In `src/hangarfit/viewer.py`, add the schema constant next to `_EDITOR_CONTEXT_SCHEMA` (~line 74):

```python
# The serve-config blob (#445). Present ONLY when `hangarfit serve` renders the
# shell — never in the offline file export — so the client can tell it is running
# under the local backend and light up the (otherwise dormant) Calculate button.
# A viewer-HTML-level marker like #solutions / #editor-context, NOT a scene/v2 change.
_SERVE_CONFIG_SCHEMA = "hangarfit.serve-config/v1"
```

Replace `render_edit_viewer` (lines 203-214) with a `build_edit_html` + a thin wrapper:

```python
def build_edit_html(scene: dict, context: dict, *, serve_config: dict | None = None) -> str:
    """Return the interactive-editor viewer HTML as a string.

    The ``#scene`` bytes are byte-identical to :func:`render_viewer` (ADR-0003).
    ``serve_config`` — when given — appends a third ``#serve-config`` blob so the
    client knows it runs under ``hangarfit serve`` (#445); with ``serve_config=None``
    the output is byte-identical to the offline ``render_edit_viewer`` file."""
    data = (
        f'<script type="application/json" id="scene">{_embed_json(scene)}</script>\n'
        f'<script type="application/json" id="editor-context">{_embed_json(context)}</script>\n'
    )
    if serve_config is not None:
        data += f'<script type="application/json" id="serve-config">{_embed_json(serve_config)}</script>\n'
    return _assemble_html(extra_head="", hud_html=_HUD_EDIT, data_scripts=data)


def render_edit_viewer(scene: dict, context: dict, output_path: Path | str) -> None:
    """Like :func:`render_viewer`, plus an additive ``#editor-context`` blob and
    the edit HUD. Writes the byte-identical offline artifact (no ``serve-config``)."""
    Path(output_path).write_text(build_edit_html(scene, context), encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_viewer.py -k "build_edit_html or render_edit_viewer" -v`
Expected: PASS (new tests + the untouched existing `render_edit_viewer` tests).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/viewer.py tests/test_viewer.py
git commit -m "$(printf 'refactor(viewer): #445 extract build_edit_html + serve-config blob\n\nString-returning build_edit_html for the serve backend to send as an HTTP\nbody; offline render_edit_viewer stays byte-identical (serve_config=None).\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd')"
```

---

## Task 2: `server.py` — seed context + HTTP handler (`GET /`, `POST /solve`)

**Files:**
- Create: `src/hangarfit/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `hangarfit.viewer.build_edit_html`, `build_editor_context`; `hangarfit.loader.load_scenario`, `load_fleet`, `load_hangar`; `hangarfit.solver.solve`; `hangarfit.scene.build_scene`; `hangarfit.models.SearchConfig`.
- Produces:
  - `@dataclass(frozen=True) SeedContext` with `scenario_dir: Path`, `load_kwargs: dict`, `solve_kwargs: dict`, `initial_scene: dict`, `initial_ctx: dict`.
  - `build_seed(scenario_path, *, fleet=None, hangar=None, max_carts=None, apron_depth=None, seed=None, budget_s=30.0, spread=False, nose_out=True) -> SeedContext`
  - `make_server(seed: SeedContext, *, host="127.0.0.1", port=0) -> http.server.ThreadingHTTPServer` (attaches `httpd.seed`)
  - `serve(scenario_path, *, port=8765, open_browser=True, **build_seed_kwargs) -> None` (blocking)
  - `_solve_scene(seed: SeedContext, scenario_yaml: str) -> dict` — write temp file in `seed.scenario_dir`, `load_scenario` + `solve` + `build_scene`, return the scene dict; raises on no-layout.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_server.py`:

```python
"""Integration tests for the `hangarfit serve` loopback backend (#445)."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from hangarfit import server

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "scenario_minimal.yaml"


@pytest.fixture
def live_server():
    """Start make_server on an ephemeral port in a background thread; tear down."""
    seed = server.build_seed(_FIXTURE, budget_s=5.0, seed=0)
    httpd = server.make_server(seed, port=0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def _conn(port: int) -> http.client.HTTPConnection:
    return http.client.HTTPConnection("127.0.0.1", port, timeout=30)


def test_get_root_serves_inlined_edit_viewer(live_server):
    c = _conn(live_server)
    c.request("GET", "/")
    resp = c.getresponse()
    body = resp.read().decode("utf-8")
    assert resp.status == 200
    assert "text/html" in resp.getheader("Content-Type", "")
    assert 'id="scene"' in body and 'id="editor-context"' in body and 'id="serve-config"' in body


def test_post_solve_returns_scene_v2(live_server):
    yaml_body = _FIXTURE.read_text(encoding="utf-8")
    c = _conn(live_server)
    c.request("POST", "/solve", body=yaml_body.encode("utf-8"))
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert doc["schema"].startswith("hangarfit.scene/")  # a valid scene/v2 doc
    assert "hangar" in doc and "planes" in doc


def test_non_loopback_host_is_rejected(live_server):
    c = _conn(live_server)
    c.request("GET", "/", headers={"Host": "evil.example.com"})
    resp = c.getresponse()
    resp.read()
    assert resp.status == 403


def test_unknown_path_404s(live_server):
    c = _conn(live_server)
    c.request("GET", "/nope")
    resp = c.getresponse()
    resp.read()
    assert resp.status == 404


def test_malformed_yaml_body_is_4xx_not_500(live_server):
    c = _conn(live_server)
    c.request("POST", "/solve", body=b"fleet_in: [does_not_exist_plane]\n")
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert 400 <= resp.status < 500
    assert "error" in doc  # actionable JSON, no stack trace
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hangarfit.server'`.

- [ ] **Step 3: Implement `server.py`**

Create `src/hangarfit/server.py`:

```python
"""Local loopback HTTP backend for the interactive editor (#445, ADR-0030).

`hangarfit serve <scenario>` binds a stdlib http.server to 127.0.0.1 and exposes:

  GET  /        -> the inlined interactive-editor viewer (initial solved scene)
  POST /solve   -> body = an exported Scenario YAML; returns a scene/v2 JSON doc

Pure transport: solving stays in the one Python runtime, so the determinant-−1
transform (ADR-0002) and byte-identical determinism (ADR-0003) are untouched, and
the offline single-file export (ADR-0017) is unchanged. Loopback-only + a
Host-header allowlist (DNS-rebinding guard); YAML input safety is inherited from
the loader's yaml.safe_load + scenario-key allowlist.
"""

from __future__ import annotations

import json
import sys
import tempfile
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from hangarfit import scene as scene_mod
from hangarfit import viewer
from hangarfit.loader import LoaderError, load_fleet, load_hangar, load_scenario
from hangarfit.models import SearchConfig
from hangarfit.solver import solve

_SERVE_CONFIG = {"schema": viewer._SERVE_CONFIG_SCHEMA}
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class SeedContext:
    """Everything the handler needs to render `GET /` and answer `POST /solve`.

    fleet/hangar are NOT stored: the posted YAML re-emits their refs, resolved
    (relative to `scenario_dir`) by the temp-file the handler writes there — so a
    served solve is byte-identically `hangarfit solve <exported.yaml>`.
    """

    scenario_dir: Path
    load_kwargs: dict[str, Any]  # max_carts, apron_depth (NOT in the posted YAML)
    solve_kwargs: dict[str, Any]  # budget_s, seed, search, alternatives
    initial_scene: dict
    initial_ctx: dict


def build_seed(
    scenario_path: Path | str,
    *,
    fleet: str | None = None,
    hangar: str | None = None,
    max_carts: int | None = None,
    apron_depth: Any = None,
    seed: int | None = None,
    budget_s: float = 30.0,
    spread: bool = False,
    nose_out: bool = True,
) -> SeedContext:
    """Resolve the seed scenario, run the initial solve, and build the first
    scene + editor-context. Placement-only (no tow planning) — the editor is a
    placement surface and this keeps each Calculate snappy."""
    scenario_path = Path(scenario_path)
    fleet_obj = load_fleet(fleet) if fleet is not None else None
    hangar_obj = load_hangar(hangar, fleet=fleet_obj) if hangar is not None else None
    load_kwargs: dict[str, Any] = {"max_carts": max_carts, "apron_depth": apron_depth}
    solve_kwargs: dict[str, Any] = {
        "budget_s": budget_s,
        "seed": seed,
        "search": SearchConfig(spread=spread, nose_out=nose_out),
        "alternatives": 1,
        "plan_paths": False,
    }
    scenario = load_scenario(scenario_path, fleet=fleet_obj, hangar=hangar_obj, **load_kwargs)
    result = solve(scenario, **solve_kwargs)
    if not result.layouts:
        raise LoaderError(f"seed scenario has no valid layout (status={result.status})")
    layout = result.layouts[0]
    initial_scene = scene_mod.build_scene(layout)
    # Read the raw fleet/hangar refs the way the CLI's editor context does, so the
    # export re-emits them verbatim next to the seed scenario (temp-file resolution).
    import yaml as _yaml

    raw = _yaml.safe_load(scenario_path.read_text(encoding="utf-8")) or {}
    fleet_ref = fleet if fleet is not None else str(raw.get("fleet", ""))
    hangar_ref = hangar if hangar is not None else str(raw.get("hangar", ""))
    initial_ctx = viewer.build_editor_context(
        fleet_ref=fleet_ref,
        hangar_ref=hangar_ref,
        maintenance_plane=layout.maintenance_plane,
        layout=layout,
    )
    return SeedContext(
        scenario_dir=scenario_path.resolve().parent,
        load_kwargs=load_kwargs,
        solve_kwargs=solve_kwargs,
        initial_scene=initial_scene,
        initial_ctx=initial_ctx,
    )


def _solve_scene(seed: SeedContext, scenario_yaml: str) -> dict:
    """Write the posted YAML to a temp file in the seed dir (so relative
    fleet:/hangar: refs resolve identically), solve, and return a scene/v2 dict.
    Raises LoaderError on an invalid/unsolvable scenario."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".yaml", dir=seed.scenario_dir, delete=False
    )
    try:
        tmp.write(scenario_yaml)
        tmp.close()
        scenario = load_scenario(tmp.name, **seed.load_kwargs)
        result = solve(scenario, **seed.solve_kwargs)
        if not result.layouts:
            raise LoaderError(f"no valid layout found (status={result.status})")
        return scene_mod.build_scene(result.layouts[0])
    finally:
        Path(tmp.name).unlink(missing_ok=True)


class _Handler(BaseHTTPRequestHandler):
    server_version = "hangarfit-serve/1"

    @property
    def _seed(self) -> SeedContext:
        return self.server.seed  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # keep the console quiet
        return

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        name = host.rsplit(":", 1)[0].strip("[]")  # drop :port, unwrap [::1]
        return name in _LOOPBACK_HOSTS

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler contract)
        if not self._host_ok():
            self._send_json(403, {"error": "non-loopback Host rejected"})
            return
        if self.path != "/":
            self._send_json(404, {"error": "not found"})
            return
        html = viewer.build_edit_html(
            self._seed.initial_scene, self._seed.initial_ctx, serve_config=_SERVE_CONFIG
        )
        self._send_html(html)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._send_json(403, {"error": "non-loopback Host rejected"})
            return
        if self.path != "/solve":
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        try:
            scene = _solve_scene(self._seed, body)
        except LoaderError as e:
            self._send_json(422, {"error": str(e)})
            return
        except Exception as e:  # unexpected: log server-side, return generic 500
            print(f"serve: unexpected error on /solve: {e!r}", file=sys.stderr)
            self._send_json(500, {"error": "internal error"})
            return
        self._send_json(200, scene)


def make_server(
    seed: SeedContext, *, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """Build (but do not start) a loopback ThreadingHTTPServer bound to `seed`.
    Threading is safe: each request loads its own Scenario and solves — the solver
    holds no shared mutable state (ADR-0003)."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.seed = seed  # type: ignore[attr-defined]
    return httpd


def serve(scenario_path: Path | str, *, port: int = 8765, open_browser: bool = True, **kw: Any) -> None:
    """Blocking entrypoint: build the seed, start the server, optionally open a
    browser, and serve until interrupted."""
    seed = build_seed(scenario_path, **kw)
    httpd = make_server(seed, port=port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"hangarfit serve: {url}  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
```

> Note: `self.server` is typed `socketserver.BaseServer` in stdlib stubs, hence the `# type: ignore[attr-defined]` on `.seed`. If `mypy src/hangarfit/` flags the `ThreadingHTTPServer.seed` assignment, keep the ignore; do not widen the type.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_server.py -v`
Expected: PASS (all 5 tests). If `test_malformed_yaml_body_is_4xx_not_500` returns 500, the loader raised a non-`LoaderError` — check the message and, only if it is a genuinely-expected loader validation error type, add it to the `except` arm (do **not** broaden to bare `Exception` for the 4xx path).

- [ ] **Step 5: Typecheck + commit**

Run: `mypy src/hangarfit/` and `ruff check src/hangarfit/server.py`
Expected: clean (bar the documented `type: ignore`).

```bash
git add src/hangarfit/server.py tests/test_server.py
git commit -m "$(printf 'feat(server): #445 loopback HTTP backend (GET / + POST /solve)\n\nStdlib http.server on 127.0.0.1; reuses load_scenario->solve->build_scene\nverbatim. Host-header DNS-rebinding guard; temp-file-in-seed-dir resolution\nkeeps a served solve byte-identical to `solve <exported.yaml>`.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd')"
```

---

## Task 3: `serve` CLI subcommand + dispatch (cli.py)

**Files:**
- Modify: `src/hangarfit/cli.py` (add subparser after the `view` block ~line 542; add `cmd_serve` near `cmd_view`; add dispatch in `main` ~line 1559)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `hangarfit.server.serve`.
- Produces: `cmd_serve(args) -> int`; a `serve` subparser with positional `input` and flags `--fleet --hangar --max-carts --apron-depth --seed --budget --spread --no-nose-out --port --no-open`.

- [ ] **Step 1: Write the failing CLI test**

Add to `tests/test_cli.py`:

```python
def test_serve_cli_invokes_server_with_parsed_args(monkeypatch):
    from hangarfit import cli, server

    captured = {}

    def fake_serve(scenario_path, **kw):
        captured["path"] = str(scenario_path)
        captured.update(kw)

    monkeypatch.setattr(server, "serve", fake_serve)
    rc = cli.main(
        ["serve", "tests/fixtures/scenario_minimal.yaml", "--port", "9001", "--no-open", "--seed", "7"]
    )
    assert rc == 0
    assert captured["path"].endswith("scenario_minimal.yaml")
    assert captured["port"] == 9001
    assert captured["open_browser"] is False
    assert captured["seed"] == 7
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_cli.py -k serve_cli -v`
Expected: FAIL with `SystemExit: 2` (argparse: `invalid choice: 'serve'`).

- [ ] **Step 3: Add the subparser** (in `build_parser`, immediately after the `view` block ends at `src/hangarfit/cli.py:542`, before `return parser`)

```python
    serve = sub.add_parser(
        "serve",
        help="Run a local loopback backend so the --edit viewer can Calculate live (#445).",
    )
    serve.add_argument("input", help="Scenario YAML to seed the editor (solved on start).")
    serve.add_argument("--fleet", metavar="PATH", default=None, help="Override the fleet data file.")
    serve.add_argument("--hangar", metavar="PATH", default=None, help="Override the hangar data file.")
    serve.add_argument(
        "--max-carts", type=int, metavar="N", default=None, dest="max_carts",
        help="Override the hangar's spare-cart count for the cart_eligible pool.",
    )
    serve.add_argument(
        "--apron-depth", type=_apron_depth_arg, metavar="N|auto", default=None, dest="apron_depth",
        help="Staging-apron depth (m); 'auto' derives from the fleet (ADR-0021).",
    )
    serve.add_argument("--seed", type=int, default=None, metavar="S", help="RNG seed (default: entropy).")
    serve.add_argument(
        "--budget", type=float, default=30.0, metavar="SEC",
        help="Per-solve wall-clock budget in seconds (default: 30.0).",
    )
    serve.add_argument(
        "--spread", action="store_true",
        help="Keep the inter-plane spread post-pass ON (default OFF, as for view --solve).",
    )
    serve.add_argument(
        "--no-nose-out", action="store_false", dest="nose_out", default=True,
        help="Disable the nose-out parked-heading preference (#263).",
    )
    serve.add_argument("--port", type=int, default=8765, metavar="N", help="Loopback port (default: 8765).")
    serve.add_argument(
        "--no-open", action="store_false", dest="open_browser", default=True,
        help="Do not auto-open a browser; just print the URL.",
    )
```

- [ ] **Step 4: Add `cmd_serve`** (immediately after `cmd_view`, before `def main`, ~line 1549)

```python
def cmd_serve(args: argparse.Namespace) -> int:
    """Run the `serve` subcommand: a local loopback backend for the live editor (#445)."""
    from hangarfit import server

    try:
        server.serve(
            args.input,
            port=args.port,
            open_browser=args.open_browser,
            fleet=args.fleet,
            hangar=args.hangar,
            max_carts=args.max_carts,
            apron_depth=args.apron_depth,
            seed=args.seed,
            budget_s=args.budget,
            spread=args.spread,
            nose_out=args.nose_out,
        )
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except OSError as e:  # e.g. port already in use
        print(f"error: could not start server: {e}", file=sys.stderr)
        return 2
    return 0
```

- [ ] **Step 5: Add the dispatch arm** (in `main`, `src/hangarfit/cli.py` ~line 1559, after the `view` arm)

```python
    if args.cmd == "serve":
        return cmd_serve(args)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_cli.py -k serve_cli -v`
Expected: PASS.

- [ ] **Step 7: Typecheck + commit**

Run: `mypy src/hangarfit/` and `ruff check src/hangarfit/cli.py`
Expected: clean.

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "$(printf 'feat(cli): #445 hangarfit serve subcommand\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd')"
```

---

## Task 4: Client Calculate loop (serve-contract, editor dispose, calculate, main wiring, bundle)

**Files:**
- Create: `viewer/src/serve-contract.ts`, `viewer/src/interaction/calculate.ts`, `viewer/test/serve-contract.test.ts`
- Modify: `viewer/src/interaction/editor.ts`, `viewer/src/main.ts`
- Rebuild: `src/hangarfit/_viewer_assets/viewer.js`

**Interfaces:**
- Produces:
  - `serve-contract.ts`: `interface ServeConfig { schema: string }`; `parseServeConfig(text: string | null | undefined): ServeConfig | null`; `solveRequestInit(yaml: string): RequestInit`.
  - `calculate.ts`: `mountCalculate(opts: { getIntent: () => Intent; ctx: EditorContext; reRender: (scene: SceneV2) => void }): void`.
  - `editor.ts`: `EditorHandle` gains `dispose(): void`; `mountEditor` accepts `opts.initialIntent?: Intent`.

- [ ] **Step 1: Write the failing node unit for the pure serve helpers**

Create `viewer/test/serve-contract.test.ts`:

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseServeConfig, solveRequestInit } from '../src/serve-contract.ts';

test('parseServeConfig returns null when the blob is absent', () => {
  assert.equal(parseServeConfig(null), null);
  assert.equal(parseServeConfig(undefined), null);
  assert.equal(parseServeConfig(''), null);
});

test('parseServeConfig parses a present blob', () => {
  const cfg = parseServeConfig('{"schema":"hangarfit.serve-config/v1"}');
  assert.equal(cfg?.schema, 'hangarfit.serve-config/v1');
});

test('solveRequestInit posts the yaml body', () => {
  const init = solveRequestInit('fleet_in: [a]\n');
  assert.equal(init.method, 'POST');
  assert.equal(init.body, 'fleet_in: [a]\n');
  assert.match(String((init.headers as Record<string, string>)['Content-Type']), /yaml/);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix viewer/ run test`
Expected: FAIL — cannot resolve `../src/serve-contract.ts`.

- [ ] **Step 3: Implement `serve-contract.ts`**

Create `viewer/src/serve-contract.ts`:

```ts
// viewer/src/serve-contract.ts — the client side of the `hangarfit serve` seam (#445).
// A viewer-HTML-level marker blob (#serve-config), NOT part of scene/v2. Pure &
// node-tested; the DOM/fetch wiring lives in interaction/calculate.ts.
export interface ServeConfig {
  schema: string;
}

/** Parse the injected `#serve-config` blob; null when absent (offline export). */
export function parseServeConfig(text: string | null | undefined): ServeConfig | null {
  if (!text) return null;
  return JSON.parse(text) as ServeConfig;
}

/** The fetch init for POST /solve — a raw Scenario YAML body. */
export function solveRequestInit(yaml: string): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/x-yaml' }, body: yaml };
}
```

- [ ] **Step 4: Run the node unit to verify it passes**

Run: `npm --prefix viewer/ run test`
Expected: PASS (the 3 new tests + the existing suite).

- [ ] **Step 5: Add `dispose()` + `initialIntent` to `editor.ts`**

In `viewer/src/interaction/editor.ts`:

1. Extend the handle interface (line 28-30):

```ts
export interface EditorHandle {
  getIntent(): Intent;
  dispose(): void;
}
```

2. Accept an optional seed intent and create an `AbortController` (top of `mountEditor`, replacing line 38):

```ts
  let intent = opts.initialIntent ?? initialIntent(opts.ctx);
  const ac = new AbortController();
```

and widen the `opts` type to include `initialIntent?: Intent` (add to the `mountEditor` parameter object and import `Intent` — it is already imported).

3. Add `{ signal: ac.signal }` as the 3rd argument to **every** `el.addEventListener(...)` and every HUD-control `.addEventListener(...)` call in the function (the pointerdown/pointerup on `el`; `prio`, `pinToggle`, `cartsToggle`, `cartMode`, `exportBtn`, `rankAdd` listeners; and the per-`<li>` drag listeners in `renderDoorOrder` / per-row `box` listeners in `renderPalette`). Example for the two `el` listeners:

```ts
  el.addEventListener('pointerdown', (ev: PointerEvent) => { downX = ev.clientX; downY = ev.clientY; }, { signal: ac.signal });
  el.addEventListener('pointerup', (ev: PointerEvent) => { /* …unchanged body… */ }, { signal: ac.signal });
```

4. Return `dispose` (replace the final `return { getIntent: () => intent };` at line 351):

```ts
  return { getIntent: () => intent, dispose: () => ac.abort() };
```

> The per-`<li>`/`box` listeners are re-created on every `renderDoorOrder`/`renderPalette` call, but they live on elements that are replaced (door list) or on nodes inside the persistent palette `<ul>`; adding `{ signal: ac.signal }` to them ensures a `dispose()` cleanly detaches everything so a re-mount after Calculate cannot double-fire.

- [ ] **Step 6: Implement `calculate.ts`**

Create `viewer/src/interaction/calculate.ts`:

```ts
// viewer/src/interaction/calculate.ts — the serve-mode "Calculate" control (#445).
// Dormant offline (main.ts only mounts it when a #serve-config blob is present).
// Never composes geometry: it POSTs the exported Scenario YAML and hands the
// Python-computed scene straight to the caller's re-render (ADR-0002).
import { banner, byId, clearBanner } from '../dom.ts';
import { intentToScenarioYaml } from './export.ts';
import { solveRequestInit } from '../serve-contract.ts';
import type { Intent, EditorContext } from './intent-contract.ts';
import type { SceneV2 } from '../scene-contract.ts';

export function mountCalculate(opts: {
  getIntent: () => Intent;
  ctx: EditorContext;
  reRender: (scene: SceneV2) => void;
}): void {
  const btn = document.createElement('button');
  btn.id = 'calculate';
  btn.type = 'button';
  btn.textContent = 'Calculate';
  const exportBtn = byId<HTMLButtonElement>('export');
  exportBtn.parentElement?.insertBefore(btn, exportBtn);

  async function run(): Promise<void> {
    btn.disabled = true;
    clearBanner();
    try {
      const yaml = intentToScenarioYaml(opts.getIntent(), opts.ctx);
      const resp = await fetch('/solve', solveRequestInit(yaml));
      if (!resp.ok) {
        let msg = `${resp.status}`;
        try {
          msg = (JSON.parse(await resp.text()) as { error?: string }).error ?? msg;
        } catch {
          /* non-JSON body: keep the status code */
        }
        banner('Calculate failed: ' + msg);
        return;
      }
      opts.reRender((await resp.json()) as SceneV2);
    } catch (e) {
      banner('Calculate failed: ' + (e as Error).message);
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener('click', () => void run());
}
```

- [ ] **Step 7: Wire `bootSingle` in `main.ts`**

In `viewer/src/main.ts`:

1. Add imports (near line 26-29):

```ts
import { mountCalculate } from './interaction/calculate.ts';
import { parseServeConfig } from './serve-contract.ts';
```

2. Replace the body of `bootSingle` from the `const world = ...` line (151) through the editor-context block (170), making `world`/`hud`/`editor` mutable and adding the serve branch:

```ts
function bootSingle(data: SceneV2, brand: BrandTokens): void {
  setReadouts(data);
  const stage = setupStage(data.hangar, brand);
  let world = buildWorld(stage.scene, data, brand);
  wireToggles(stage.wallMeshes, () => world);
  const hud = startHud({
    timeline: world.timeline,
    home: stage.home,
    controls: stage.controls,
    renderer: stage.renderer,
    scene: stage.scene,
    cam: stage.cam,
  });

  const ctxEl = document.getElementById('editor-context');
  if (ctxEl?.textContent) {
    const ctx = JSON.parse(ctxEl.textContent) as EditorContext;
    let editor = mountEditor({ groups: world.groups, renderer: stage.renderer, cam: stage.cam, ctx });

    // #445 serve: a #serve-config blob (never in the offline export) lights up the
    // Calculate button. A successful solve swaps the world (mirroring the #666
    // compare mount) and re-mounts the editor on the new groups, preserving the
    // user's intent so they can iterate.
    const serveCfg = parseServeConfig(document.getElementById('serve-config')?.textContent);
    if (serveCfg) {
      mountCalculate({
        getIntent: () => editor.getIntent(),
        ctx,
        reRender: (next) => {
          const preserved = editor.getIntent();
          editor.dispose();
          clearBanner();
          stage.scene.remove(world.group);
          disposeWorld(world.group);
          world = buildWorld(stage.scene, next, brand);
          applyToggleState(world);
          setReadouts(next);
          hud.setActiveTimeline(world.timeline);
          editor = mountEditor({
            groups: world.groups,
            renderer: stage.renderer,
            cam: stage.cam,
            ctx,
            initialIntent: preserved,
          });
        },
      });
    }
  }
}
```

- [ ] **Step 8: Typecheck + lint + node tests**

Run: `npm --prefix viewer/ run typecheck && npm --prefix viewer/ run lint && npm --prefix viewer/ run test`
Expected: all clean/pass. Fix any `tsc` error (e.g. an `addEventListener` missing the `signal` option, or `EditorHandle.dispose` not implemented) before proceeding.

- [ ] **Step 9: Rebuild the committed bundle**

Run: `npm --prefix viewer/ run build`
Expected: regenerates `src/hangarfit/_viewer_assets/viewer.js`. Verify it changed and that the committed-bundle test still ties: `pytest tests/test_viewer.py -k "committed_bundle or inlined_viewer_js" -v` (PASS).

- [ ] **Step 10: Commit**

```bash
git add viewer/src/ viewer/test/ src/hangarfit/_viewer_assets/viewer.js
git commit -m "$(printf 'feat(viewer): #445 client Calculate loop (serve-mode)\n\nserve-contract.ts + calculate.ts POST the exported Scenario YAML to /solve\nand re-render the returned scene; editor gains AbortController dispose +\ninitialIntent so a swap re-mounts cleanly with intent preserved. Bundle rebuilt.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd')"
```

---

## Task 5: ADR-0030 + docs (CHANGELOG, CLAUDE.md, arc42)

**Files:**
- Create: `docs/adr/0030-hangarfit-serve-local-backend.md`
- Modify: `CHANGELOG.md`, `CLAUDE.md`, `docs/architecture/05-building-block-view.md`

- [ ] **Step 1: Write ADR-0030**

Create `docs/adr/0030-hangarfit-serve-local-backend.md` mirroring the ADR-0029 header format:

```markdown
# ADR-0030: `hangarfit serve` — a local loopback backend so the editor triggers the solve

- **Status:** Accepted
- **Date:** 2026-07-04
- **Deciders:** Patrick Kuhn (DocGerd)

> **Scope.** Records the deployment-model shift for the Stage-3 editor (#445):
> from a double-clicked **offline file** to an **additional** opt-in **local
> loopback server** that runs the *unchanged* Python solver and returns a
> `scene/v2` payload the viewer renders. The offline single-file export
> ([ADR-0017](0017-3d-viewer-architecture.md)) survives unchanged.

## Context

The `--edit` viewer (#442) captures intent and exports a `Scenario` YAML the user
re-runs on the CLI. Closing that loop in the browser ("Calculate") needs a solver
the page can call; a `file://` page cannot even `fetch` (CORS, ADR-0017).

## Decision

Add `hangarfit serve <scenario>`: a stdlib `http.server` bound to `127.0.0.1`
exposing `GET /` (the inlined editor) and `POST /solve` (Scenario YAML → `scene/v2`
JSON). It reuses `load_scenario → solve → build_scene` verbatim — pure transport.

- **No web framework** — stdlib only, matching the no-heavy-deps ethos.
- **Python stays the authority** — the determinant-−1 transform (ADR-0002) and
  determinism (ADR-0003) are untouched because solving never leaves the one runtime.
- **Loopback-only, no `--host`.** A LAN/remote bind is a deliberate non-feature.
- **Threat model:** a `Host`-header allowlist (`127.0.0.1`/`localhost`) is the
  DNS-rebinding guard; YAML input safety is inherited from the loader's
  `yaml.safe_load` + scenario-key allowlist; `/solve`'s blast radius is one
  CPU-bound solve, no shell, no writes beyond a temp file the server owns.

## Alternatives considered

- **In-browser Pyodide/WASM solve — rejected.** Re-opens the det-−1 trap (ADR-0002)
  and introduces a second determinism runtime (ADR-0003).
- **Desktop wrapper (Tauri / pywebview) — deferred.** Same "Python owns the solve"
  benefit, heavier tooling, nicer distribution; a future packaging option.

## Consequences

Unlocks the drag-to-fix (#911) and mover-pin (#912) follow-ups (Python owns the
trivial pose inverse). Reaffirms ADR-0002/0003/0017; supersedes none.
```

- [ ] **Step 2: Add the CHANGELOG entry** (under `## [Unreleased]` → `### Added` in `CHANGELOG.md`, after the #910 bullet)

```markdown
- **`hangarfit serve` — live editor backend** — a new `hangarfit serve <scenario>` subcommand starts a local, loopback-only (`127.0.0.1`) HTTP backend so the `--edit` viewer's new **Calculate** button re-solves and re-renders in the browser, instead of exporting a YAML to re-run by hand. Pure transport over the existing solver (`GET /` serves the editor; `POST /solve` takes an exported `Scenario` YAML and returns a `scene/v2` doc) — Python stays the solver/transform authority (ADR-0002) and determinism is unaffected (ADR-0003, one runtime). The offline single-file export is unchanged. Loopback bind + a `Host`-header guard; no web-framework dependency. (#445, ADR-0030)
```

- [ ] **Step 3: Add the `serve` command to CLAUDE.md** (in the "Useful commands" section, after the `view --edit` block)

```markdown
# #445 serve: local loopback backend so the --edit viewer's Calculate button
# re-solves live (ADR-0030). Reuses load_scenario->solve->build_scene verbatim
# (pure transport); binds 127.0.0.1 only (no --host), Host-header guarded. The
# offline single-file export is unchanged. GET / serves the editor; POST /solve
# takes an exported Scenario YAML and returns a scene/v2 doc. Ctrl-C to stop.
hangarfit serve tests/fixtures/scenario_minimal.yaml --port 8765
```

- [ ] **Step 4: Add `server` to the module map** (in `docs/architecture/05-building-block-view.md`, add a `server` row/subsection next to `viewer`/`scene`, one sentence: "the loopback HTTP backend for the live editor — `GET /` + `POST /solve` over the unchanged solve pipeline; ADR-0030"). Also add the module name to the CLAUDE.md §5 module-map inline list (the "(`cli`, `loader`, … `viewer`, …)" enumeration) if it enumerates modules.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0030-hangarfit-serve-local-backend.md CHANGELOG.md CLAUDE.md docs/architecture/05-building-block-view.md
git commit -m "$(printf 'docs: #445 ADR-0030 serve backend + CHANGELOG + module map\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01QrHcFVwhf2L3wNVQZffMMd')"
```

---

## Task 6: Full-suite green, manual smoke, draft PR

**Files:** none (verification + delivery)

- [ ] **Step 1: Run the safe local test gate**

Run: `make test`
Expected: green (the two-pass split: parallel bulk + serial canaries). This exercises `test_server.py`, `test_viewer.py`, `test_cli.py`.

- [ ] **Step 2: Lint + format + typecheck the whole change**

Run: `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`
Expected: clean.

- [ ] **Step 3: Manual end-to-end smoke (human-visible)**

Run: `hangarfit serve tests/fixtures/scenario_minimal.yaml --port 8765 --no-open`
Then in another shell verify the loop headlessly:

```bash
curl -s -X POST --data-binary @tests/fixtures/scenario_minimal.yaml http://127.0.0.1:8765/solve | python -c "import sys,json; d=json.load(sys.stdin); print('scene schema:', d['schema'])"
curl -s -o /dev/null -w "root %{http_code}\n" http://127.0.0.1:8765/
curl -s -o /dev/null -w "rebind %{http_code}\n" -H "Host: evil.com" http://127.0.0.1:8765/
```

Expected: `scene schema: hangarfit.scene/v2`, `root 200`, `rebind 403`. Ctrl-C the server.

- [ ] **Step 4 (stretch): headless viewer http smoke**

Start the server (`--no-open`), then:

```bash
google-chrome --headless=new --use-gl=angle --use-angle=swiftshader \
  --enable-unsafe-swiftshader --virtual-time-budget=8000 \
  --screenshot=/tmp/serve.png "http://127.0.0.1:8765/"
```

Expected: a rendered screenshot, no on-page TRANSFORM CHECK banner (the `checkAnchors` self-check passes over the served scene). Ctrl-C the server.

- [ ] **Step 5: Push + open the draft PR**

```bash
git push -u origin feature/445-serve-backend
gh pr create --draft --base develop \
  --title "feat: hangarfit serve — local backend for the live editor (#445)" \
  --body "$(printf 'Closes #445.\n\nStage-3 capability E: a loopback HTTP backend so the --edit viewer Calculates live. Pure transport over the unchanged solve pipeline; ADR-0030 records the deployment model. Drag (#911) and mover-pin (#912) remain follow-ups.\n\nSpec: docs/superpowers/specs/2026-07-04-445-hangarfit-serve-backend-design.md')"
```

Then set metadata (assignee/labels/milestone) via `gh api -X PATCH` as per the repo workflow.

- [ ] **Step 6: Review arc (per repo workflow — outside the plan's TDD loop)**

Run `/pr-review`: **code-reviewer** (main pass) + **scene-schema-guard** (viewer.py/serve-config touch) + **silent-failure-hunter** (the server's error paths). One inline thread per finding; fix + resolve; flip to ready when clean. **No `determinism-guard` needed** (no `solver.py`/`towplanner.py` change).

---

## Self-Review (against the spec)

**Spec coverage:**
- §2/§4.5 new `server.py`, `serve` CLI, reused pipeline → Tasks 2, 3. ✓
- §3 `GET /` (inlined edit HTML) + `POST /solve` (Scenario YAML → scene/v2) → Task 2. ✓
- §3 drop `GET /viewer.js` (inlined) + defer `POST /check` → not implemented, by design. ✓
- §3.1 temp-file-in-seed-dir resolution → `_solve_scene` (Task 2). ✓
- §4 CLI shape (port 8765, `--no-open`, loopback-only, passthrough solve flags) → Task 3. ✓
- §5 threat model (loopback bind, `ThreadingHTTPServer`, Host-header guard, inherited `safe_load`) → Task 2 (`_host_ok`, `make_server`). ✓
- §6 client serve branch + Calculate + reuse re-render path → Task 4. ✓
- §6 `viewer.js` rebuilt/committed → Task 4 Step 9. ✓
- §8 tests (Python integration incl. Host-403/404/malformed; node unit; stretch headless) → Tasks 2, 4, 6. ✓
- §9 ADR-0030 → Task 5. ✓
- §11 one PR closing #445, CHANGELOG entry, `scene-schema-guard` + `silent-failure-hunter`, no `determinism-guard` → Tasks 5, 6. ✓

**Placeholder scan:** no TBD/TODO; every code step shows real code; test steps show assertions. ✓

**Type consistency:** `SeedContext`/`build_seed`/`make_server`/`serve`/`_solve_scene` signatures match across Tasks 2–3; `EditorHandle.dispose` (Task 4 Step 5) is consumed in `main.ts` (Task 4 Step 7); `mountCalculate` param object matches its call site; `parseServeConfig`/`solveRequestInit` signatures match the node test and `main.ts`/`calculate.ts` imports; `_SERVE_CONFIG_SCHEMA` defined in Task 1 is read by `server.py` (`_SERVE_CONFIG`) in Task 2. ✓
