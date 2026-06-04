"""Assemble a self-contained, offline 3D viewer HTML from a scene/v1 dict.

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

from hangarfit import metrics

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
    breakout (the canonical safe-embedding technique)."""
    return json.dumps(obj, separators=(",", ":")).replace("<", "\\u003c")


def render_viewer(scene: dict, output_path: Path | str) -> None:
    """Write a single self-contained, offline HTML viewer for ``scene`` to
    ``output_path``. ``scene`` is a ``hangarfit.scene/v1`` dict from
    :func:`hangarfit.scene.build_scene`."""
    three_src = _asset_text(_THREE, "three.module.js")
    orbit_src = _asset_text(_THREE, "OrbitControls.js")
    viewer_js = _asset_text(_ASSETS, "viewer.js")

    import_map = {
        "imports": {
            "three": _data_url(three_src),
            "three/addons/controls/OrbitControls.js": _data_url(orbit_src),
        }
    }
    html = (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>hangarfit — 3D viewer</title>\n"
        f"<style>{_CSS}</style>\n"
        f'<script type="importmap">{json.dumps(import_map)}</script>\n'
        "</head><body>\n"
        '<div id="app"><canvas id="c"></canvas></div>\n'
        '<div id="banner" hidden></div>\n'
        # #401 honesty banner: static text (not user data), shown by viewer.js
        # when scene.placeholder is true. Same wording as the 2D PNG.
        f'<div id="placeholder" hidden>{metrics.PLACEHOLDER_BANNER}</div>\n'
        f'<div id="hud">{_HUD}</div>\n'
        f'<script type="application/json" id="scene">{_embed_json(scene)}</script>\n'
        f'<script type="module">{viewer_js}</script>\n'
        "</body></html>\n"
    )
    Path(output_path).write_text(html, encoding="utf-8")


_CSS = (
    "html,body{margin:0;height:100%;background:#0d0e10;color:#e8eaed;"
    "font:13px system-ui,sans-serif;overflow:hidden}"
    "#c{display:block;width:100vw;height:100vh}"
    "#hud{position:fixed;left:0;right:0;bottom:0;padding:10px 14px;"
    "background:rgba(18,20,24,.86);display:flex;gap:10px;align-items:center;flex-wrap:wrap}"
    "#hud button{cursor:pointer;background:#2a2d33;color:#e8eaed;border:1px solid #3b4046;"
    "border-radius:6px;padding:5px 10px}#hud button:disabled{opacity:.4;cursor:default}"
    "#scrub{flex:1;min-width:160px}"
    "#banner{position:fixed;top:0;left:0;right:0;padding:10px;background:#7a1f1f;color:#fff;"
    "text-align:center;z-index:9;font-weight:600}"
    "#placeholder{position:fixed;top:0;left:0;right:0;padding:7px;background:#b00020;"
    "color:#fff;text-align:center;z-index:8;font-weight:700;letter-spacing:.02em}"
    "#readouts{color:#aeb6c2;font-variant-numeric:tabular-nums}"
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
    '<span id="readouts"></span>'
    '<span id="legend"></span>'
)
