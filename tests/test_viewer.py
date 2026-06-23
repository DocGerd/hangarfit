"""Tests for the self-contained offline HTML assembler (hangarfit.viewer)."""

from __future__ import annotations

import json
import re
from importlib import resources

from hangarfit import brand, scene, viewer
from hangarfit.loader import load_layout
from hangarfit.towplanner import plan_fill

LAYOUT = "tests/fixtures/valid_left_side_nesting.yaml"


def _html(tmp_path) -> str:
    lay = load_layout(LAYOUT)
    sc = scene.build_scene(lay, moves_plan=plan_fill(lay))
    out = tmp_path / "v.html"
    viewer.render_viewer(sc, out)
    return out.read_text(encoding="utf-8")


def _brand_blob(html: str) -> dict:
    """Parse the injected BRAND token blob (#419) out of the rendered HTML."""
    m = re.search(r'<script type="application/json" id="brand">(.*?)</script>', html, re.S)
    assert m is not None
    return json.loads(m.group(1))


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
    assert json.loads(m.group(1))["schema"] == "hangarfit.scene/v2"


def test_embedded_scene_has_no_raw_angle_bracket(tmp_path):
    html = _html(tmp_path)
    m = re.search(r'id="scene">(.*?)</script>', html, re.S)
    assert m is not None
    assert "<" not in m.group(1)  # all '<' escaped — no markup breakout


def test_html_embeds_viewer_js(tmp_path):
    html = _html(tmp_path)
    assert "OrbitControls" in html and "Matrix4" in html
    assert "three/addons/controls/OrbitControls.js" in html


def test_html_embeds_polish_features(tmp_path):
    # #400: contact shadows, a billboarded id-label sprite, and the labels/nose
    # HUD toggle must all reach the emitted artifact (viewer.js + HUD).
    html = _html(tmp_path)
    assert "shadowMap" in html  # contact shadows enabled on the renderer
    assert "CanvasTexture" in html  # billboarded id-label sprite (safe fillText)
    assert 'id="labels"' in html  # the HUD toggle for labels + nose arrows


def test_html_embeds_floor_tow_paths(tmp_path):
    # #505: the floor tow-path overlay (3D analogue of `solve --render-paths`)
    # must reach the artifact — the HUD `paths` toggle (default ON) and the
    # bundled line-builder. Pixels are checked by the headless screenshot; this
    # is the string-presence guard that the toggle + builder ship in the HTML.
    html = _html(tmp_path)
    assert 'id="paths" type="checkbox" checked' in html  # toggle, default ON
    assert "addTowPaths" in html  # the bundled floor-line builder reaches viewer.js


def test_html_embeds_honesty_banner_and_readouts(tmp_path):
    # #401: the placeholder banner + readouts wiring must reach the artifact, and
    # the scene JSON must carry the placeholder flag (shipped fleet is unmeasured).
    html = _html(tmp_path)
    assert "PLACEHOLDER DATA" in html
    assert 'id="placeholder"' in html
    assert 'id="readouts"' in html
    m = re.search(r'id="scene">(.*?)</script>', html, re.S)
    assert m is not None
    scene_json = json.loads(m.group(1))
    assert scene_json["placeholder"] is True
    assert scene_json["readouts"] is not None


def test_html_carries_brand_3d_tokens(tmp_path):
    # #415: the generated viewer embeds the DocGerdSoft dark-surface brand. These
    # are string-presence guards (pixels are checked by the headless screenshot);
    # every viewer.js literal is always in the inlined module text and every CSS
    # token always in the <style> block, so one fixture suffices.
    html = _html(tmp_path)
    # HUD chrome (viewer.py _CSS)
    assert "#D6A23E" in html  # placeholder/honesty banner -> warning amber
    assert "#14161A" in html  # ...with dark ink on the amber
    assert "#BC4438" in html  # error banner -> danger red
    assert "outline:2px solid #3FA3D6" in html  # accent focus ring
    assert "accent-color:#3FA3D6" in html  # branded range scrubber
    assert "#C2C7CD" in html  # readouts -> --graphite-strong dark
    assert '"Geist Mono"' in html  # machine-output mono stack
    # Scene shell + lights now live in the injected BRAND blob (#419): viewer.js
    # reads them at runtime instead of hard-coding 0x literals. Assert the token
    # values are present (case-insensitive hex, since THREE.Color is) rather than
    # the old 0x form.
    b = _brand_blob(html)
    assert b["floor"].lower() == "#15171a"  # floor -> --surface dark
    assert b["gridMinor"].lower() == "#202428"  # grid minor -> --hairline-2 dark
    assert b["bay"].lower() == "#7b63a3"  # maintenance bay -> maint violet
    assert b["wallsOpacity"] == 0.20  # walls -> lifted opacity
    assert b["fill"].lower() == "#cfe3f2"  # fill light -> pale accent tint
    # Never-hue-alone conflict cue (viewer.js)
    assert "⚠ conflict" in html  # non-colour label suffix the 3D box can't hatch
    assert "ui-monospace" in html  # mono label stack


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


def test_inlined_viewer_js_is_the_committed_bundle_verbatim(tmp_path):
    # #439: viewer.js is now an esbuild artifact built from viewer/src/*.ts. The
    # CI `viewer-build-drift` guard pins one half — viewer.js == esbuild(viewer/src);
    # this pins the other half — the assembler inlines the committed bundle RAW,
    # byte-for-byte, into the <script type="module">. Together they guarantee the
    # shipped HTML carries exactly the reviewed, drift-guarded bytes. (A full-HTML
    # golden hash is deliberately avoided: it would be brittle against every bundle
    # rebuild / brand / vendored-three bump; the two-call determinism test below
    # pins assembly stability instead.)
    html = _html(tmp_path)
    src = (
        resources.files("hangarfit._viewer_assets")
        .joinpath("viewer.js")
        .read_text(encoding="utf-8")
    )
    assert src in html


# ── #419: the injected canonical BRAND token blob ───────────────────────────


def test_brand_blob_is_present_canonical_and_round_trips(tmp_path):
    # The BRAND blob (#419) is its OWN <script id="brand">, separate from the
    # scene blob (scene/v2 unchanged). It must parse, be canonical (sorted keys +
    # compact separators) so the HTML is byte-stable, and be a flat dict of
    # str/number values.
    html = _html(tmp_path)
    m = re.search(r'<script type="application/json" id="brand">(.*?)</script>', html, re.S)
    assert m is not None
    raw = m.group(1)
    tokens = json.loads(raw)
    # Canonical: re-serializing with sort_keys + compact separators (then the same
    # '<'-escape the assembler applies) reproduces the embedded bytes exactly.
    canonical = json.dumps(tokens, sort_keys=True, separators=(",", ":"), allow_nan=False).replace(
        "<", "\\u003c"
    )
    assert raw == canonical
    assert tokens == brand.viewer_brand_tokens()
    assert all(isinstance(v, (str, int, float)) for v in tokens.values())


def test_brand_blob_has_no_raw_angle_bracket(tmp_path):
    # Same </script>-breakout guard as the scene blob: every '<' in the BRAND blob
    # is escaped to < so a future hostile token can't break out of the element.
    html = _html(tmp_path)
    m = re.search(r'id="brand">(.*?)</script>', html, re.S)
    assert m is not None
    assert "<" not in m.group(1)


def test_render_viewer_is_byte_identical_across_two_calls(tmp_path):
    # Render-only token centralization must not cost determinism: the same scene
    # renders to byte-identical HTML twice (the BRAND blob is sort_keys-stable).
    lay = load_layout(LAYOUT)
    sc1 = scene.build_scene(lay, moves_plan=plan_fill(lay))
    sc2 = scene.build_scene(lay, moves_plan=plan_fill(lay))
    out1 = tmp_path / "a.html"
    out2 = tmp_path / "b.html"
    viewer.render_viewer(sc1, out1)
    viewer.render_viewer(sc2, out2)
    assert out1.read_bytes() == out2.read_bytes()


# ── #666: the multi-solution compare viewer ─────────────────────────────────


def _solution(scene_dict: dict, label: str, **summary) -> dict:
    return {"label": label, "scene": scene_dict, "summary": summary}


def _compare_html(tmp_path, n: int = 2, *, count_requested: int | None = None) -> str:
    lay = load_layout(LAYOUT)
    sc = scene.build_scene(lay, moves_plan=plan_fill(lay))
    sols = [
        _solution(
            sc, "#1", min_gap_m=1.2, planes_moved_vs_first=0, mean_shift_m=0.0, routable=True
        ),
        _solution(
            sc, "#2", min_gap_m=0.9, planes_moved_vs_first=3, mean_shift_m=2.1, routable=False
        ),
    ][:n]
    out = tmp_path / "cmp.html"
    viewer.render_compare_viewer(sols, out, count_requested=count_requested or n)
    return out.read_text(encoding="utf-8")


def _solutions_blob(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="solutions">(.*?)</script>', html, re.S)
    assert m is not None
    return json.loads(m.group(1))


def test_compare_html_is_self_contained_and_offline(tmp_path):
    html = _compare_html(tmp_path)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert 'type="importmap"' in html
    assert 'type="application/json" id="solutions"' in html
    assert "data:text/javascript;base64," in html
    assert "http://" not in html and "https://" not in html


def test_compare_manifest_round_trips(tmp_path):
    manifest = _solutions_blob(_compare_html(tmp_path, n=2))
    assert manifest["schema"] == "hangarfit.viewer-compare/v1"
    assert manifest["count_requested"] == 2
    assert manifest["count_found"] == 2
    assert len(manifest["solutions"]) == 2
    # Each carried scene is a full, valid scene/v2 doc.
    assert manifest["solutions"][0]["scene"]["schema"] == "hangarfit.scene/v2"
    assert manifest["solutions"][1]["summary"]["planes_moved_vs_first"] == 3


def test_compare_per_solution_scene_bytes_match_standalone(tmp_path):
    # ADR-0003: a layout's scene/v2 emission is byte-identical whether rendered
    # alone or carried as compare alternative #k — the container is purely additive.
    lay = load_layout(LAYOUT)
    sc = scene.build_scene(lay, moves_plan=plan_fill(lay))
    html = _compare_html(tmp_path)
    assert viewer._embed_json(sc) in html  # the standalone scene bytes appear verbatim


def test_compare_uses_solutions_blob_not_single_scene(tmp_path):
    # Compare mode carries N scenes in #solutions and omits the single #scene blob;
    # the viewer branches on which blob is present.
    html = _compare_html(tmp_path)
    assert 'type="application/json" id="scene"' not in html


def test_compare_hud_carries_switcher_control(tmp_path):
    html = _compare_html(tmp_path)
    assert 'id="compare"' in html  # the solution <select>
    assert 'id="compare-metrics"' in html  # per-solution metrics readout


def test_compare_manifest_has_no_raw_angle_bracket(tmp_path):
    # Same </script>-breakout guard as the scene/BRAND blobs.
    m = re.search(r'id="solutions">(.*?)</script>', _compare_html(tmp_path), re.S)
    assert m is not None
    assert "<" not in m.group(1)


def test_compare_records_partial_found_vs_requested(tmp_path):
    # "If available": fewer diverse solutions than requested keeps both counts so
    # the viewer can label "Found n of N" (mirroring solve).
    manifest = _solutions_blob(_compare_html(tmp_path, n=1, count_requested=3))
    assert manifest["count_found"] == 1
    assert manifest["count_requested"] == 3


def test_compare_render_is_byte_identical_across_two_calls(tmp_path):
    lay = load_layout(LAYOUT)
    sc = scene.build_scene(lay, moves_plan=plan_fill(lay))
    sols = [
        _solution(sc, "#1", min_gap_m=1.2, planes_moved_vs_first=0, mean_shift_m=0.0, routable=True)
    ]
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    viewer.render_compare_viewer(sols, a, count_requested=1)
    viewer.render_compare_viewer(sols, b, count_requested=1)
    assert a.read_bytes() == b.read_bytes()


def test_compare_requires_at_least_one_solution(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="at least one solution"):
        viewer.render_compare_viewer([], tmp_path / "x.html", count_requested=3)


def test_compare_inlines_committed_bundle(tmp_path):
    # Same contract as single mode: the assembler inlines the committed bundle RAW.
    src = (
        resources.files("hangarfit._viewer_assets")
        .joinpath("viewer.js")
        .read_text(encoding="utf-8")
    )
    assert src in _compare_html(tmp_path)


def test_brand_module_exports():
    # The single token source (#419) exposes the palettes, status inks, and the
    # 3D viewer token object the BRAND blob is built from.
    assert len(brand.PLANES) == 9
    assert len(brand.PLANES_DARK) == 9
    assert set(brand.STATUS) == {"valid", "conflict", "maint", "wall"}
    tokens = brand.viewer_brand_tokens()
    # The 3D token keys viewer.js reads must all exist.
    for key in (
        "sceneBg",
        "floor",
        "gridMajor",
        "gridMinor",
        "walls",
        "wallsOpacity",
        "bay",
        "bayOpacity",
        "hemisphereSky",
        "hemisphereGround",
        "sun",
        "fill",
        "wheel",
        "cartDeck",
        "conflict",
        "labelText",
        "labelChipBg",
        "labelConflictChip",
    ):
        assert key in tokens
