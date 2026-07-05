"""Integration tests for the `hangarfit serve` loopback backend (#445)."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from hangarfit import server

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "scenario_minimal.yaml"


@pytest.fixture
def live_server() -> Iterator[int]:
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


def test_get_root_serves_inlined_edit_viewer(live_server: int) -> None:
    c = _conn(live_server)
    c.request("GET", "/")
    resp = c.getresponse()
    body = resp.read().decode("utf-8")
    assert resp.status == 200
    assert "text/html" in resp.getheader("Content-Type", "")
    assert 'id="scene"' in body and 'id="editor-context"' in body and 'id="serve-config"' in body


def test_post_solve_returns_scene_and_refreshed_editor_context(live_server: int) -> None:
    yaml_body = _FIXTURE.read_text(encoding="utf-8")
    c = _conn(live_server)
    c.request("POST", "/solve", body=yaml_body.encode("utf-8"))
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    # {scene, editorContext}: the scene is a valid scene/v2 doc; the refreshed
    # editor-context lets the client re-base "pin at current pose" on the new poses.
    scene = doc["scene"]
    assert scene["schema"].startswith("hangarfit.scene/")
    assert "hangar" in scene and "planes" in scene
    ctx = doc["editorContext"]
    assert ctx["schema"] == "hangarfit.editor-context/v1"
    assert ctx["currentPoses"]  # non-empty: the solved layout's poses


def test_post_solve_malformed_content_length_is_400(live_server: int) -> None:
    # Malformed request FRAMING (a non-integer Content-Length) must be an actionable
    # 400, never a dropped connection with no HTTP response.
    c = _conn(live_server)
    c.putrequest("POST", "/solve")
    c.putheader("Content-Length", "not-a-number")
    c.endheaders()
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 400
    assert "error" in doc


def test_non_loopback_host_is_rejected(live_server: int) -> None:
    c = _conn(live_server)
    c.request("GET", "/", headers={"Host": "evil.example.com"})
    resp = c.getresponse()
    resp.read()
    assert resp.status == 403


def test_unknown_path_404s(live_server: int) -> None:
    c = _conn(live_server)
    c.request("GET", "/nope")
    resp = c.getresponse()
    resp.read()
    assert resp.status == 404


def test_malformed_yaml_body_is_4xx_not_500(live_server: int) -> None:
    c = _conn(live_server)
    c.request("POST", "/solve", body=b"fleet_in: [does_not_exist_plane]\n")
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert 400 <= resp.status < 500
    assert "error" in doc  # actionable JSON, no stack trace


def test_post_convert_returns_pin_for_a_dragged_pose(live_server: int) -> None:
    import math

    # heading 30° → world_yaw_rad = radians(90 - 30); /convert must invert it back.
    yaw = math.radians(90.0 - 30.0)
    c = _conn(live_server)
    c.request(
        "POST",
        "/convert",
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
        "POST",
        "/convert",
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
        "POST",
        "/convert",
        body=json.dumps({"x": 1.0, "y": 2.0}).encode("utf-8"),  # no world_yaw_rad
        headers={"Content-Type": "application/json"},
    )
    resp = c.getresponse()
    assert resp.status == 400


def test_post_convert_internal_bug_is_500(
    live_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Guard the 400/500 separation (commit 7a91139): a genuine bug in the COMPUTE path
    # (here math_rad_to_compass) must surface as a logged 500, NOT be silently
    # mislabeled a 400 "bad convert request". This is the exact property that
    # regressed once; a refactor moving the compute back inside the parse `try` would
    # reintroduce the silent-failure bug, and this test would catch it.
    # (The 500 path intentionally logs a traceback to stderr — expected, not noise.)
    import math

    def _boom(*a: object, **k: object) -> float:
        raise ValueError("simulated internal bug in the compute path")

    monkeypatch.setattr(server, "math_rad_to_compass", _boom)
    yaw = math.radians(90.0 - 30.0)
    c = _conn(live_server)
    c.request(
        "POST",
        "/convert",
        body=json.dumps({"x": 1.0, "y": 2.0, "world_yaw_rad": yaw}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 500
    assert doc == {"error": "internal error"}


def test_post_convert_non_finite_is_400(live_server: int) -> None:
    c = _conn(live_server)
    c.request(
        "POST",
        "/convert",
        body=b'{"x": 1.0, "y": 2.0, "world_yaw_rad": Infinity}',  # Python json accepts Infinity
        headers={"Content-Type": "application/json"},
    )
    resp = c.getresponse()
    assert resp.status == 400
