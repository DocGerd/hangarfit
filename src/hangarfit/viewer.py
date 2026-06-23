"""Assemble a self-contained, offline 3D viewer HTML from a scene/v2 dict.

The whole viewer is **one HTML file** — the scene JSON and the vendored
Three.js are inlined, so a double-clicked ``file://`` page needs zero network.
ES modules cannot be ``fetch``-ed from ``file://`` (CORS), so the import-map
maps ``three`` / its OrbitControls addon to ``data:`` URLs of the vendored
sources; the scene is inlined as a JSON ``<script>`` (no ``fetch``). See
ADR-0017.
"""

from __future__ import annotations

import base64
import json
from importlib import resources
from pathlib import Path

from hangarfit import brand, metrics

_ASSETS = "hangarfit._viewer_assets"
_THREE = "hangarfit._viewer_assets.three"


def _asset_text(pkg: str, name: str) -> str:
    return resources.files(pkg).joinpath(name).read_text(encoding="utf-8")


def _data_url(js_source: str) -> str:
    """A ``data:`` URL of an ES-module source — resolvable from a ``file://``
    import-map with no network (base64 so ``<`` / quotes can't break the HTML)."""
    b64 = base64.b64encode(js_source.encode("utf-8")).decode("ascii")
    return f"data:text/javascript;base64,{b64}"


def _embed_json(obj: dict) -> str:
    """Compact JSON safe to inline inside a ``<script>`` element: ``<`` is
    escaped to ``\\u003c`` so a value can never produce a ``</script>``
    breakout (the canonical safe-embedding technique).

    ``allow_nan=False`` makes a non-finite value (``inf``/``nan``) raise here at
    the producer rather than serialize to a bare ``Infinity``/``NaN`` token that
    the viewer's ``JSON.parse`` would choke on — fail loud, not a blank page."""
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")


def _embed_brand() -> str:
    """The canonical ``BRAND`` JSON blob injected into the HTML for ``viewer.js``
    to read (#419). Serialized with ``sort_keys=True`` + compact separators so the
    byte sequence is stable across renders (determinism), then ``<``-escaped the
    same way :func:`_embed_json` escapes the scene blob — the values are
    Python-authored brand tokens, but the escape keeps a future hostile token from
    breaking out of the ``<script>`` element. This blob is SEPARATE from the
    ``scene`` blob: the ``scene/v2`` schema is unchanged (ADR-0017)."""
    tokens = brand.viewer_brand_tokens()
    return json.dumps(tokens, sort_keys=True, separators=(",", ":"), allow_nan=False).replace(
        "<", "\\u003c"
    )


# The viewer-compare container (#666). This is a viewer-HTML-level wrapper layered
# OVER N independent ``hangarfit.scene/v2`` docs — it is deliberately NOT part of
# the scene/v2 schema, so ``scene.build_scene`` (and its byte-determinism + the
# scene-contract.ts key-parity guard) stay untouched. The viewer reads it from a
# separate ``<script id="solutions">`` blob instead of the single-mode ``#scene``.
_COMPARE_SCHEMA = "hangarfit.viewer-compare/v1"


def _assemble_html(*, extra_head: str, hud_html: str, data_scripts: str) -> str:
    """Assemble the shared self-contained viewer HTML skeleton.

    ``data_scripts`` is the JSON ``<script>`` payload that the bundle consumes —
    the single ``#scene`` blob, or the multi ``#solutions`` compare manifest. The
    BRAND blob, importmap, canvas/HUD shell, and inlined bundle are common to both
    modes; ``extra_head`` is an optional extra ``<head>`` fragment (empty in single
    mode, so single output is template-identical)."""
    three_src = _asset_text(_THREE, "three.module.js")
    orbit_src = _asset_text(_THREE, "OrbitControls.js")
    viewer_js = _asset_text(_ASSETS, "viewer.js")

    import_map = {
        "imports": {
            "three": _data_url(three_src),
            "three/addons/controls/OrbitControls.js": _data_url(orbit_src),
        }
    }
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>hangarfit — 3D viewer</title>\n"
        f"<style>{_CSS}</style>\n"
        f"{extra_head}"
        f'<script type="importmap">{json.dumps(import_map)}</script>\n'
        "</head><body>\n"
        '<div id="app"><canvas id="c"></canvas></div>\n'
        '<div id="banner" hidden></div>\n'
        # #401 honesty banner: static text (not user data), shown by viewer.js
        # when scene.placeholder is true. Same wording as the 2D PNG.
        f'<div id="placeholder" hidden>{metrics.PLACEHOLDER_BANNER}</div>\n'
        f'<div id="hud">{hud_html}</div>\n'
        # The canonical BRAND token blob (#419), separate from the scene blob so
        # the scene/v2 schema stays unchanged. viewer.js reads its colours from
        # here instead of hard-coding 0x literals.
        f'<script type="application/json" id="brand">{_embed_brand()}</script>\n'
        f"{data_scripts}"
        f'<script type="module">{viewer_js}</script>\n'
        "</body></html>\n"
    )


def render_viewer(scene: dict, output_path: Path | str) -> None:
    """Write a single self-contained, offline HTML viewer for ``scene`` to
    ``output_path``. ``scene`` is a ``hangarfit.scene/v2`` dict from
    :func:`hangarfit.scene.build_scene`."""
    data = f'<script type="application/json" id="scene">{_embed_json(scene)}</script>\n'
    html = _assemble_html(extra_head="", hud_html=_HUD, data_scripts=data)
    Path(output_path).write_text(html, encoding="utf-8")


def render_compare_viewer(
    solutions: list[dict],
    output_path: Path | str,
    *,
    count_requested: int,
) -> None:
    """Write a self-contained, offline 3D viewer carrying N solver alternatives
    for side-by-side comparison (#666).

    Each ``solutions`` entry is a dict ``{"label": str, "scene": <scene/v2 dict>,
    "summary": {...}}`` where ``scene`` comes from :func:`hangarfit.scene.build_scene`
    (so every alternative's scene bytes are byte-identical to a standalone render —
    the multi-scene container is purely additive, ADR-0003) and ``summary`` carries
    the per-solution compare metrics (min gap, planes-moved-vs-#1, routability).

    ``count_requested`` is the ``--alternatives N`` the user asked for; when fewer
    diverse solutions exist the manifest records both so the viewer can label
    "Found n of N" (mirroring ``solve``)."""
    if not solutions:
        raise ValueError("render_compare_viewer requires at least one solution")
    manifest = {
        "schema": _COMPARE_SCHEMA,
        "count_requested": count_requested,
        "count_found": len(solutions),
        "solutions": list(solutions),
    }
    data = f'<script type="application/json" id="solutions">{_embed_json(manifest)}</script>\n'
    html = _assemble_html(
        extra_head=f"<style>{_CSS_COMPARE}</style>\n",
        hud_html=_HUD_COMPARE,
        data_scripts=data,
    )
    Path(output_path).write_text(html, encoding="utf-8")


# DocGerdSoft dark-surface brand (BRAND.md §4). Form controls don't inherit
# font-family, so the Geist stack is set explicitly on the text-bearing controls
# (the HUD buttons + the speed select); machine output (clock, readouts, ids) is
# mono per brand. Every colour/font below is sourced from :mod:`hangarfit.brand`
# (#419) so the CSS can't drift from the 3D scene tokens; the byte output is
# unchanged.
_CSS = (
    f"html,body{{margin:0;height:100%;background:{brand.PAPER};color:{brand.INK};"
    f"font:13px {brand.FONT_SANS};overflow:hidden}}"
    "#c{display:block;width:100vw;height:100vh}"
    "#hud{position:fixed;left:0;right:0;bottom:0;padding:10px 14px;"
    f"background:{brand.HUD_GLASS};border-top:1px solid {brand.HAIRLINE};"
    "display:flex;gap:10px;align-items:center;flex-wrap:wrap}"
    f"#hud button{{cursor:pointer;background:{brand.BUTTON_BG};color:{brand.INK};"
    f"border:1px solid {brand.BUTTON_BORDER};"
    f"font:500 13px {brand.FONT_SANS};"
    "border-radius:6px;padding:5px 10px}#hud button:disabled{opacity:.4;cursor:default}"
    f"#hud select{{font:500 13px {brand.FONT_SANS}}}"
    f"#hud button:focus,#scrub:focus{{outline:2px solid {brand.FOCUS_RING};outline-offset:2px}}"
    f"#scrub{{flex:1;min-width:160px;accent-color:{brand.SCRUBBER_ACCENT}}}"
    f"#banner{{position:fixed;top:0;left:0;right:0;padding:10px;"
    f"background:{brand.ERROR_BANNER_BG};color:{brand.ERROR_BANNER_TEXT};"
    "text-align:center;z-index:9;font-weight:600}"
    f"#placeholder{{position:fixed;top:0;left:0;right:0;padding:7px;"
    f"background:{brand.PLACEHOLDER_BANNER_BG};"
    f"color:{brand.PLACEHOLDER_BANNER_TEXT};"
    "text-align:center;z-index:8;font-weight:700;letter-spacing:.02em}"
    f"#clock,#active,#readouts,.sw{{font-family:{brand.FONT_MONO};"
    f'font-feature-settings:"tnum" 1,"zero" 1}}'
    f"#readouts{{color:{brand.READOUTS_TEXT};font-variant-numeric:tabular-nums}}"
    "#legend{display:flex;gap:8px;flex-wrap:wrap}"
    ".sw{display:inline-flex;align-items:center;gap:4px}"
    ".sw i{width:11px;height:11px;border-radius:2px;display:inline-block}"
)
_HUD = (
    '<button id="play">▶</button>'
    '<button id="prev">◀ plane</button><button id="next">plane ▶</button>'
    '<input id="scrub" type="range" min="0" max="1000" value="0">'
    '<select id="speed" title="playback speed">'
    '<option value="0.5">0.5×</option>'
    '<option value="1" selected>1×</option>'
    '<option value="2">2×</option>'
    "</select>"
    '<span id="clock">0.0s</span><span id="active"></span>'
    '<button id="reset">reset view</button>'
    '<label><input id="walls" type="checkbox" checked> walls</label>'
    '<label><input id="labels" type="checkbox" checked> labels</label>'
    # #505 floor tow-path overlay toggle. Default ON: the 3D analogue of the 2D
    # `solve --render-paths` overlay; drawing the route makes the apron slide-in
    # (ty<0) and the in-hangar maneuvering legible. Inert on a static scene.
    '<label><input id="paths" type="checkbox" checked> paths</label>'
    '<span id="readouts"></span>'
    '<span id="legend"></span>'
)
# #666 compare HUD: a solution switcher (dropdown, also ←/→ keys) prepended to the
# standard HUD, plus a per-solution metrics readout. The <select> ships empty —
# viewer.js fills its options from the #solutions manifest at load (the labels are
# built by the pure, node-tested compare.ts). Only emitted by render_compare_viewer,
# so single-mode HUD bytes are unchanged.
_HUD_COMPARE = (
    '<label id="cmp-label">solution '
    '<select id="compare" title="compare alternatives (←/→ keys)"></select></label>'
    '<span id="compare-metrics"></span>' + _HUD
)
# Compare-only chrome, emitted as an extra <style> by render_compare_viewer so the
# single-mode <style> block (and its byte output) is untouched. Sourced from the
# same brand tokens as _CSS (#419) — no drift.
_CSS_COMPARE = (
    f"#cmp-label{{font:500 13px {brand.FONT_SANS}}}"
    f"#compare-metrics{{font-family:{brand.FONT_MONO};"
    f'font-feature-settings:"tnum" 1,"zero" 1;color:{brand.READOUTS_TEXT}}}'
)
