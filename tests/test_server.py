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


def test_post_solve_returns_scene_v2(live_server: int) -> None:
    yaml_body = _FIXTURE.read_text(encoding="utf-8")
    c = _conn(live_server)
    c.request("POST", "/solve", body=yaml_body.encode("utf-8"))
    resp = c.getresponse()
    doc = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert doc["schema"].startswith("hangarfit.scene/")  # a valid scene/v2 doc
    assert "hangar" in doc and "planes" in doc


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
