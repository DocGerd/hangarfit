"""Tests for the self-contained offline HTML assembler (hangarfit.viewer)."""

from __future__ import annotations

import json
import re
from importlib import resources

from hangarfit import scene, viewer
from hangarfit.loader import load_layout
from hangarfit.towplanner import plan_fill

LAYOUT = "tests/fixtures/valid_left_side_nesting.yaml"


def _html(tmp_path) -> str:
    lay = load_layout(LAYOUT)
    sc = scene.build_scene(lay, moves_plan=plan_fill(lay))
    out = tmp_path / "v.html"
    viewer.render_viewer(sc, out)
    return out.read_text(encoding="utf-8")


def test_html_is_self_contained_and_offline(tmp_path):
    html = _html(tmp_path)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert 'type="importmap"' in html
    assert 'type="application/json" id="scene"' in html
    # Three.js source is embedded as a data: URL, not network-referenced.
    assert "data:text/javascript;base64," in html
    # Coarse net: no remote references anywhere in the artifact.
    assert "http://" not in html and "https://" not in html


def test_offline_targets_real_network_triggers(tmp_path):
    # The coarse "no http://" check passes partly because the vendored JS is
    # base64-encoded (its license URLs don't leak). Assert the properties that
    # actually decide whether the browser fetches: every import-map target is a
    # data: URL, and there is no src=/href= attribute pointing at a remote URL.
    html = _html(tmp_path)
    im = re.search(r'<script type="importmap">(.*?)</script>', html, re.S)
    assert im is not None
    imports = json.loads(im.group(1))["imports"]
    assert imports  # non-empty
    assert all(v.startswith("data:") for v in imports.values())
    # No fetchable attribute references (the artifact has no <link>/<img>/<script src>).
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?:', html)


def test_embed_json_neutralizes_hostile_script_close():
    # The escape exists for exactly this: a string value containing </script>.
    # Plane ids flow from user YAML straight into the inlined <script> JSON.
    hostile = {"id": "</script><img src=x onerror=alert(1)>", "n": 1}
    out = viewer._embed_json(hostile)
    assert "</script" not in out.lower()  # no element breakout
    assert "<" not in out  # every '<' escaped
    assert json.loads(out) == hostile  # …and the original value still round-trips


def test_scene_json_round_trips(tmp_path):
    html = _html(tmp_path)
    m = re.search(r'<script type="application/json" id="scene">(.*?)</script>', html, re.S)
    assert m is not None
    # Embedded JSON escapes '<' as < to prevent a </script> breakout; JSON
    # parsing decodes it back, so the document round-trips to the scene dict.
    assert json.loads(m.group(1))["schema"] == "hangarfit.scene/v1"


def test_embedded_scene_has_no_raw_angle_bracket(tmp_path):
    html = _html(tmp_path)
    m = re.search(r'id="scene">(.*?)</script>', html, re.S)
    assert m is not None
    assert "<" not in m.group(1)  # all '<' escaped — no markup breakout


def test_html_embeds_viewer_js(tmp_path):
    html = _html(tmp_path)
    assert "OrbitControls" in html and "Matrix4" in html
    assert "three/addons/controls/OrbitControls.js" in html


def test_static_scene_renders(tmp_path):
    # A layout with no MovesPlan → static scene, still a valid HTML artifact.
    lay = load_layout(LAYOUT)
    sc = scene.build_scene(lay)  # no moves_plan
    out = tmp_path / "static.html"
    viewer.render_viewer(sc, out)
    assert out.read_text(encoding="utf-8").lstrip().startswith("<!DOCTYPE html>")


def test_assets_are_packaged():
    assert resources.files("hangarfit._viewer_assets").joinpath("viewer.js").is_file()
    assert resources.files("hangarfit._viewer_assets.three").joinpath("three.module.js").is_file()
    assert resources.files("hangarfit._viewer_assets.three").joinpath("OrbitControls.js").is_file()


def test_inlined_viewer_js_has_no_script_close():
    # viewer.js is inlined RAW into a <script type="module"> (not escaped), so a
    # literal </script> in the JS source would break out of the element. Guard
    # against a future edit introducing one.
    src = (
        resources.files("hangarfit._viewer_assets")
        .joinpath("viewer.js")
        .read_text(encoding="utf-8")
    )
    assert "</script" not in src.lower()
