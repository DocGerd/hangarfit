"""Top-down matplotlib renderer for hangarfit layouts.

Renders a :class:`Layout` to a PNG: hangar outline, door as a gap in the
front wall, maintenance bay shaded, each placed aircraft drawn as its
world :class:`Part` polygons (fuselage opaque, wing translucent so
overlapping wings show their stack, struts as thin lines). Aircraft are
color-keyed by ``wing_position``. If a :class:`CheckResult` is supplied,
the parts of conflicting planes are overdrawn in red.

The renderer's job is to give a human something to eyeball — visual
quality is intentionally not asserted in tests. The smoke tests verify
the function runs without exception and produces a non-empty PNG; pixel
content is reviewed by the user.

The ``Agg`` backend is forced at import time so the renderer runs
headless in CI / pytest without a display server. This must happen
*before* ``matplotlib.pyplot`` is imported anywhere else in the test
session, which is why the import order in this module is fixed.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

from .geometry import WorldPart, aircraft_parts_world  # noqa: E402
from .models import CheckResult, Layout, WingPosition  # noqa: E402

_WING_COLORS: dict[WingPosition, str] = {
    "high": "#3498db",  # blue
    "mid": "#e67e22",  # orange
    "low": "#f4d03f",  # yellow
}
# Fallback for any future ``wing_position`` value or unusual configurations
# (e.g., the Falke's monowheel-with-outriggers — drawn neutrally so it
# doesn't visually claim one of the three wing-height tiers).
_FALLBACK_COLOR = "#95a5a6"  # gray

_CONFLICT_COLOR = "#e74c3c"  # red

_FUSELAGE_ALPHA = 0.9
_WING_ALPHA = 0.4
_STRUT_LINEWIDTH = 1.2

_BAY_COLOR = "#fadbd8"  # very pale red — "this strip is reserved"
_BAY_ALPHA = 0.35
_HANGAR_EDGE = "#2c3e50"  # near-black
_DOOR_EDGE = "#bdc3c7"  # light gray — visually "open"


def render_layout(
    layout: Layout,
    output_path: Path | str,
    *,
    check_result: CheckResult | None = None,
    title: str | None = None,
    dpi: int = 100,
) -> None:
    """Render ``layout`` to a PNG at ``output_path``.

    If ``check_result`` is supplied and has any conflicts, the parts of
    each conflicting plane are overdrawn in red. The overlay is
    deliberately over-broad: ``Conflict`` only carries plane ids and a
    kind string, not specific part identifiers, so we highlight every
    part of every plane named in any conflict rather than guess which
    part-pair is the actual culprit. A future refinement could plumb
    part indices through ``Conflict.detail``.
    """
    fig, ax = plt.subplots(figsize=(10, 12))
    _draw_hangar(ax, layout)
    _draw_aircraft(ax, layout)
    if check_result is not None and not check_result.valid:
        _draw_conflict_overlay(ax, layout, check_result)
    _finalize_axes(ax, layout, title)
    fig.savefig(str(output_path), dpi=dpi, bbox_inches="tight")
    plt.close(fig)


def _draw_hangar(ax, layout: Layout) -> None:
    """Hangar rectangle with a gap in the front wall for the door and a
    shaded back strip for the maintenance bay."""
    hangar = layout.hangar
    door_left = hangar.door.center_x_m - hangar.door.width_m / 2
    door_right = hangar.door.center_x_m + hangar.door.width_m / 2

    # Maintenance bay first so it's underneath everything else.
    bay_start_y = hangar.length_m - hangar.maintenance_bay.depth_m
    ax.fill(
        [0, hangar.width_m, hangar.width_m, 0],
        [bay_start_y, bay_start_y, hangar.length_m, hangar.length_m],
        color=_BAY_COLOR,
        alpha=_BAY_ALPHA,
        zorder=0,
    )

    # Back, left, right walls — solid.
    ax.plot([0, hangar.width_m], [hangar.length_m, hangar.length_m], color=_HANGAR_EDGE, lw=2)
    ax.plot([0, 0], [0, hangar.length_m], color=_HANGAR_EDGE, lw=2)
    ax.plot([hangar.width_m, hangar.width_m], [0, hangar.length_m], color=_HANGAR_EDGE, lw=2)

    # Front wall split around the door — door rendered as a light/dashed
    # gap so it reads as "opening" not "wall I forgot to draw".
    ax.plot([0, door_left], [0, 0], color=_HANGAR_EDGE, lw=2)
    ax.plot([door_right, hangar.width_m], [0, 0], color=_HANGAR_EDGE, lw=2)
    ax.plot([door_left, door_right], [0, 0], color=_DOOR_EDGE, lw=1, linestyle=":")


def _draw_aircraft(ax, layout: Layout) -> None:
    """Draw each placed plane as its world parts, color-keyed by wing position."""
    for placement in layout.placements:
        aircraft = layout.fleet[placement.plane_id]
        color = _WING_COLORS.get(aircraft.wing_position, _FALLBACK_COLOR)
        world_parts = aircraft_parts_world(aircraft, placement)
        for part in world_parts:
            _draw_part(ax, part, color)
        _annotate_plane(ax, placement, aircraft.id)


def _draw_part(ax, part: WorldPart, color: str) -> None:
    """Render a single world part. Fuselage is opaque-ish, wing translucent
    (so stacked wings show their overlap visually), strut as a thin
    outlined polygon (struts are physically thin, no fill needed)."""
    coords = list(part.polygon.exterior.coords)[:-1]
    if part.kind == "fuselage":
        patch = MplPolygon(coords, closed=True, facecolor=color, edgecolor=_HANGAR_EDGE,
                           alpha=_FUSELAGE_ALPHA, lw=0.5, zorder=2)
    elif part.kind == "wing":
        patch = MplPolygon(coords, closed=True, facecolor=color, edgecolor=color,
                           alpha=_WING_ALPHA, lw=0.5, zorder=1)
    elif part.kind == "strut":
        patch = MplPolygon(coords, closed=True, facecolor="none", edgecolor=_HANGAR_EDGE,
                           lw=_STRUT_LINEWIDTH, zorder=3)
    else:
        patch = MplPolygon(coords, closed=True, facecolor=color, edgecolor=_HANGAR_EDGE,
                           alpha=_FUSELAGE_ALPHA, lw=0.5, zorder=2)
    ax.add_patch(patch)


def _annotate_plane(ax, placement, plane_id: str) -> None:
    """Plane id label + a short arrow showing the nose direction."""
    import math

    # Nose direction in world coords: at heading 0 the nose is +y;
    # at heading 90 the nose is +x. See CLAUDE.md for the full transform.
    h = math.radians(placement.heading_deg)
    nose_dx = math.sin(h)
    nose_dy = math.cos(h)
    arrow_length = 0.8
    ax.annotate(
        "",
        xy=(placement.x_m + nose_dx * arrow_length, placement.y_m + nose_dy * arrow_length),
        xytext=(placement.x_m, placement.y_m),
        arrowprops=dict(arrowstyle="->", color=_HANGAR_EDGE, lw=1),
        zorder=4,
    )
    ax.text(
        placement.x_m,
        placement.y_m - 0.2,
        plane_id,
        ha="center",
        va="top",
        fontsize=7,
        color=_HANGAR_EDGE,
        zorder=4,
    )


def _draw_conflict_overlay(ax, layout: Layout, check_result: CheckResult) -> None:
    """Redraw every part of every plane named in any conflict, in red,
    with a thicker edge so the conflict reads at a glance."""
    conflicting_planes: set[str] = set()
    for conflict in check_result.conflicts:
        conflicting_planes.update(conflict.planes)
    for placement in layout.placements:
        if placement.plane_id not in conflicting_planes:
            continue
        aircraft = layout.fleet[placement.plane_id]
        for part in aircraft_parts_world(aircraft, placement):
            coords = list(part.polygon.exterior.coords)[:-1]
            patch = MplPolygon(
                coords,
                closed=True,
                facecolor="none",
                edgecolor=_CONFLICT_COLOR,
                lw=2,
                zorder=5,
            )
            ax.add_patch(patch)


def _finalize_axes(ax, layout: Layout, title: str | None) -> None:
    """Equal-aspect axes, hangar-sized view, sensible labels, optional title."""
    hangar = layout.hangar
    ax.set_xlim(-1, hangar.width_m + 1)
    ax.set_ylim(-1, hangar.length_m + 1)
    ax.set_aspect("equal")
    ax.set_xlabel("x (m) — door wall")
    ax.set_ylabel("y (m) — deeper into hangar")
    ax.grid(True, alpha=0.2, zorder=0)
    if title is not None:
        ax.set_title(title)
