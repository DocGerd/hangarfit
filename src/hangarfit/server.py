"""Local loopback HTTP backend for the interactive editor (#445, ADR-0030).

``hangarfit serve <scenario>`` binds a stdlib http.server to 127.0.0.1 and exposes:

  * ``GET  /``      -> the inlined interactive-editor viewer (initial solved scene)
  * ``POST /solve`` -> body = an exported Scenario YAML; returns a scene/v2 JSON doc

Pure transport: solving stays in the one Python runtime, so the determinant-−1
transform (ADR-0002) and byte-identical determinism (ADR-0003) are untouched, and
the offline single-file export (ADR-0017) is unchanged. Loopback-only + a
Host-header allowlist (DNS-rebinding guard); YAML input safety is inherited from
the loader's ``yaml.safe_load`` + scenario-key allowlist.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
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
    """Everything the handler needs to render ``GET /`` and answer ``POST /solve``.

    ``fleet``/``hangar`` are NOT stored: the posted YAML re-emits their refs,
    resolved (relative to ``scenario_dir``) by the temp file the handler writes
    there — so a served solve is byte-identically ``hangarfit solve <exported.yaml>``.
    """

    scenario_dir: Path
    load_kwargs: dict[str, Any]  # max_carts, apron_depth (NOT in the posted YAML)
    solve_kwargs: dict[str, Any]  # budget_s, seed, search, alternatives, plan_paths
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
    import yaml

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
    raw = yaml.safe_load(scenario_path.read_text(encoding="utf-8")) or {}
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


def _solve_scene(seed: SeedContext, scenario_yaml: str) -> tuple[dict, dict]:
    """Write the posted YAML to a temp file in the seed dir (so relative
    ``fleet:``/``hangar:`` refs resolve identically), solve, and return
    ``(scene/v2, editor-context)``. Raises :class:`LoaderError` on an
    invalid/unsolvable scenario.

    The editor-context is refreshed from the *new* layout so the client can
    re-base "pin at current pose" on the solved poses — the browser must not
    derive them (that is the forbidden determinant-−1 inversion, ADR-0002).
    ``fleet``/``hangar`` refs are stable across solves (same scenario), so the
    seed's are reused."""
    fd, tmp_path = tempfile.mkstemp(suffix=".yaml", dir=seed.scenario_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(scenario_yaml)
        scenario = load_scenario(tmp_path, **seed.load_kwargs)
        result = solve(scenario, **seed.solve_kwargs)
        if not result.layouts:
            raise LoaderError(f"no valid layout found (status={result.status})")
        layout = result.layouts[0]
        scene = scene_mod.build_scene(layout)
        ctx = viewer.build_editor_context(
            fleet_ref=seed.initial_ctx["fleet"],
            hangar_ref=seed.initial_ctx["hangar"],
            maintenance_plane=layout.maintenance_plane,
            layout=layout,
        )
        return scene, ctx
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class _Handler(BaseHTTPRequestHandler):
    server_version = "hangarfit-serve/1"

    @property
    def _seed(self) -> SeedContext:
        return self.server.seed  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # keep the console quiet
        return

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        # drop :port, unwrap [::1], case-fold (a missing/empty Host fails closed)
        name = host.rsplit(":", 1)[0].strip("[]").lower()
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
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                raise ValueError("negative Content-Length")
            body = self.rfile.read(length).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as e:
            # Malformed request framing (bad Content-Length / non-UTF-8 body): an
            # actionable 400, never a dropped connection + stderr traceback.
            self._send_json(400, {"error": f"bad request body: {e}"})
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


def make_server(
    seed: SeedContext, *, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """Build (but do not start) a loopback ThreadingHTTPServer bound to ``seed``.

    Threading is safe: each request loads its own Scenario and solves — the solver
    holds no shared mutable state (ADR-0003)."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.seed = seed  # type: ignore[attr-defined]
    return httpd


def serve(
    scenario_path: Path | str, *, port: int = 8765, open_browser: bool = True, **kw: Any
) -> None:
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
