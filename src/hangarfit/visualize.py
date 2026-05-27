"""Top-down matplotlib renderer for hangarfit layouts.

Renders a :class:`Layout` to a PNG: hangar outline, door as a gap in the
front wall (at the *top* of the rendered image, matching the coordinate
diagram in ``docs/architecture/08-crosscutting-concepts.md``),
maintenance bay rendered conditionally on ``layout.maintenance_plane``
(see :func:`_draw_maintenance_bay` for the open/closed contract), each
placed aircraft drawn as its world :class:`Part` polygons (fuselage
opaque, wing translucent so overlapping wings show their stack, struts
as thin lines). Aircraft are color-keyed by ``wing_position``. If a
:class:`CheckResult` is supplied, the parts of conflicting planes are
overdrawn in red.

The renderer's job is to give a human something to eyeball — visual
quality is intentionally not asserted in tests. The smoke tests verify
the function produces a valid PNG; pixel content is reviewed by the user.

Backend caveat: ``matplotlib.use("Agg", force=True)`` is called at import
time so the renderer runs headless in CI / pytest without a display
server. ``force=True`` overrides a backend that was already selected by
another module (otherwise the call is silently a no-op). The module also
re-asserts the backend at render time as a defense in depth.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle as MplCircle  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

from .geometry import WorldPart, aircraft_parts_world, local_to_world  # noqa: E402
from .models import Aircraft, CheckResult, Layout, Placement, WingPosition  # noqa: E402

if TYPE_CHECKING:
    # Annotation-only import: the runtime code in _draw_tow_paths is duck-typed
    # (moves_plan.moves / move.path.sample()), so importing MovesPlan eagerly
    # would add a needless module dependency. towplanner does not import
    # visualize, so this is safe under TYPE_CHECKING either way.
    from .towplanner import MovesPlan

_WING_COLORS: dict[WingPosition, str] = {
    "high": "#3498db",  # blue
    "mid": "#e67e22",  # orange
    "low": "#f4d03f",  # yellow
}
# Defensive: ``WingPosition`` is closed at ``Literal["high", "mid", "low"]``
# and ``_WING_COLORS`` covers all three, so this fallback is unreachable
# today. It exists so that adding a new wing_position literal to the model
# doesn't blow up the renderer with KeyError before someone updates the map.
_FALLBACK_COLOR = "#95a5a6"  # gray

_CONFLICT_COLOR = "#e74c3c"  # red

# Tow-path overlay palette (#192). One colour per plane, cycled by sorted
# plane_id. Deliberately excludes the conflict red (#e74c3c) and the gray
# fallback so a path never blurs into a conflict highlight or a placeholder
# part. Eight entries cover the fleet (<=9 planes); a 9th plane reuses the
# first colour — acceptable since each path terminates at its annotated slot.
_TOW_PATH_COLORS = (
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#17becf",  # cyan
    "#bcbd22",  # olive
    "#e377c2",  # pink
)
_TOW_PATH_LINEWIDTH = 1.6

_FUSELAGE_ALPHA = 0.9  # near-opaque: two fuselages overlapping is always
# a conflict, no value in seeing through.
_WING_ALPHA = 0.4  # translucent so stacked wings (z-disjoint nesting,
# case 3 / case 7-8) show their plan-view overlap.
_STRUT_LINEWIDTH = 1.2  # struts are physically thin (~5 cm × ~1.3 m); a
# filled polygon would be near-invisible at hangar
# scale, so we draw an outline instead.

# Arrow length for the nose-direction marker, in meters. Hardcoded rather
# than scaling with hangar size: at the fleet's ~6-9 m fuselage scale,
# 0.8 m reads as a clear "direction hint" without overwhelming the plane.
# If hangars ever shrink below ~10 m on either axis this may need tuning.
_NOSE_ARROW_LENGTH_M = 0.8

# View padding around the hangar rectangle, in meters. Keeps a vertex
# landing *exactly* on a wall (e.g., the ``valid_wall_vertex`` fixture)
# visible rather than clipped flush against the figure edge.
_VIEW_PADDING_M = 1.0

# Closed-bay "wall" style — saturated red + slashed hatch, kept visually
# distinct from ``_CONFLICT_COLOR`` so the two reds don't blur in one image.
_BAY_WALL_FACE = "#922b21"
_BAY_WALL_EDGE = "#641e16"
_BAY_WALL_ALPHA = 0.55
_BAY_WALL_HATCH = "///"
_BAY_LABEL_COLOR = "#ffffff"
_HANGAR_EDGE = "#2c3e50"  # near-black
_DOOR_EDGE = "#bdc3c7"  # light gray — visually "open"

# ── Wheel / cart glyph constants ────────────────────────────────────────────
# All drawn at zorder=1.5, strictly between the wing/floor layer (zorder=1)
# and the fuselage patch (zorder=2), so the wheels peek out at the
# nose/tail/sides without obscuring aircraft body colours.
_GLYPH_ZORDER = 1.5  # between wings (1) and fuselage (2)
#
# COLOUR: neutral dark-gray that reads on the off-white floor, stays clear of
# the wing-position palette (#3498db/#e67e22/#f4d03f), the conflict-red
# (#e74c3c), and the tow-path colours. A second shade is used for the cart
# deck so the dolly rectangle is distinct from its wheel circles.
_WHEEL_COLOR = "#566573"  # dark slate-gray — individual wheel discs
_CART_DECK_COLOR = "#aab7b8"  # lighter gray — cart/dolly deck rectangle
_CART_DECK_ALPHA = 0.85

# Wheel disc radius in meters. Visually "a tyre" at ~6–9 m fuselage scale
# inside an 18–30 m hangar. Mirror the _NOSE_ARROW_LENGTH_M tuning idiom.
_WHEEL_RADIUS_M = 0.18

# Cart deck half-dimensions in meters. The deck rectangle is drawn under the
# fuselage centroid. Full width is 2 × 0.55 = 1.1 m, which is ~1.3× a typical
# 0.85 m fuselage width, so it reads as "something underneath"; length is
# shorter (~40% of a typical 6.5 m fuselage) so it is clearly not the fuselage.
_CART_DECK_HALF_LENGTH_M = 1.3
_CART_DECK_HALF_WIDTH_M = 0.55

# Fraction of fuselage forward half where nose-wheel / main-gear land.
# Nose wheel: near the fuselage nose tip. Main-gear (nosewheel config):
# placed at ~35% back from nose toward CG. Main-gear (tailwheel config):
# placed at ~40% forward from origin (CG). Tailwheel: at aft tip.
# These are intentionally approximate — real gear positions are unknown
# (fleet.yaml has measured:false everywhere) and these fractions give a
# visually convincing top-down silhouette.
_NOSE_GEAR_FRAC = 0.85  # fraction of forward half-fuselage for nose wheel
_MAIN_GEAR_FWD_FRAC = 0.30  # fraction of forward half for nosewheel mains
_MAIN_GEAR_TAILDRAGGER_FWD_FRAC = 0.45  # fraction of forward half for tailwheel mains
# Main-gear lateral offset as a multiple of fuselage half-width.
_MAIN_GEAR_LATERAL_FRAC = 1.6


def render_layout(
    layout: Layout,
    output_path: Path | str,
    *,
    check_result: CheckResult | None = None,
    moves_plan: MovesPlan | None = None,
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

    If ``moves_plan`` is supplied, each plane's tow path is overlaid as a
    polyline (one colour per plane) at the same z-tier as the conflict
    overlay — see :func:`_draw_tow_paths` (#192). ``check_result`` and
    ``moves_plan`` are independent: a layout can be rendered with neither,
    either, or both.

    The ``hangarfit solve --render-paths`` flag is the CLI entry point that
    plumbs each layout's bundled ``MovesPlan`` (from ``solve()``) through to
    this parameter (#193).
    """
    # Defense in depth: even with ``matplotlib.use("Agg", force=True)``
    # at module import, a misconfigured environment (interactive backend
    # forced via env var, pytest pulling in pyplot before this module)
    # could leave the wrong backend active. Failing loud here beats
    # producing a silently empty PNG.
    current = matplotlib.get_backend().lower()
    if current != "agg":
        raise RuntimeError(
            f"hangarfit.visualize requires the Agg backend; got {current!r}. "
            f"Set MPLBACKEND=Agg or ensure no other module switches the backend."
        )

    if check_result is not None:
        _validate_check_result_planes(layout, check_result)

    fig, ax = plt.subplots(figsize=(10, 12))
    try:
        _draw_hangar(ax, layout)
        _draw_aircraft(ax, layout)
        if not (check_result is None or check_result.valid):
            _draw_conflict_overlay(ax, layout, check_result)
        if moves_plan is not None:
            _draw_tow_paths(ax, moves_plan)
        _finalize_axes(ax, layout, title)
        fig.savefig(str(output_path), dpi=dpi, bbox_inches="tight")
    finally:
        # Always close the figure, even if savefig raises (bad path,
        # encoder error, full disk). Otherwise matplotlib accumulates
        # figures in memory and warns at ~20 — invisible to a single-shot
        # smoke test but lethal in a CLI batch or parametrized test loop.
        plt.close(fig)


def _validate_check_result_planes(layout: Layout, check_result: CheckResult) -> None:
    """Reject a ``CheckResult`` whose conflicts reference planes that
    aren't in ``layout.placements`` — that would only happen if the
    caller passed a result from a *different* layout, and silently
    rendering a clean PNG for an invalid result is the precise kind of
    "looks OK so the bug ships" failure this tool exists to prevent."""
    placed = {p.plane_id for p in layout.placements}
    conflicting = {pid for c in check_result.conflicts for pid in c.planes}
    unknown = conflicting - placed
    if unknown:
        raise ValueError(
            f"check_result references planes not placed in this layout: "
            f"{sorted(unknown)}. This usually means a CheckResult from a "
            f"different layout was supplied."
        )


def nose_direction(heading_deg: float) -> tuple[float, float]:
    """Return the unit ``(dx, dy)`` vector pointing along the plane's nose
    in world coords, for the given compass-style ``heading_deg``.

    Matches the plane-local-to-world transform in
    :func:`hangarfit.geometry.aircraft_parts_world` — at ``heading_deg = 0``
    the nose maps to world ``+y`` (``(0, 1)``); at ``heading_deg = 90`` it
    maps to world ``+x`` (``(1, 0)``). This is exactly the ``(sin h, cos h)``
    pair from the determinant-``-1`` transform documented in
    `ADR-0002 <../../docs/adr/0002-determinant-minus-one-transform.md>`_;
    a textbook CCW rotation would invert ``dx`` ↔ ``dy`` and the nose arrow
    would point the wrong way at non-axis-aligned headings.

    Extracted as a pure function so the regression-test for that wrong-
    handedness bug can run without rendering anything.
    """
    h = math.radians(heading_deg)
    return (math.sin(h), math.cos(h))


def _draw_hangar(ax: Any, layout: Layout) -> None:
    """Hangar rectangle with a gap in the front wall for the door and a
    conditional maintenance-bay overlay (closed-bay only)."""
    hangar = layout.hangar
    door_left = hangar.door.center_x_m - hangar.door.width_m / 2
    door_right = hangar.door.center_x_m + hangar.door.width_m / 2

    # Bay overlay first (zorder=0) so walls and aircraft layer on top.
    _draw_maintenance_bay(ax, layout)

    # Back, left, right walls — solid.
    ax.plot(
        [0, hangar.width_m],
        [hangar.length_m, hangar.length_m],
        color=_HANGAR_EDGE,
        lw=2,
    )
    ax.plot([0, 0], [0, hangar.length_m], color=_HANGAR_EDGE, lw=2)
    ax.plot([hangar.width_m, hangar.width_m], [0, hangar.length_m], color=_HANGAR_EDGE, lw=2)

    # Front wall split around the door — door rendered as a light/dashed
    # gap so it reads as "opening" not "wall I forgot to draw".
    ax.plot([0, door_left], [0, 0], color=_HANGAR_EDGE, lw=2)
    ax.plot([door_right, hangar.width_m], [0, 0], color=_HANGAR_EDGE, lw=2)
    ax.plot([door_left, door_right], [0, 0], color=_DOOR_EDGE, lw=1, linestyle=":")


def _draw_maintenance_bay(ax: Any, layout: Layout) -> None:
    """Render the maintenance bay if (and only if) it is closed.

    Open-bay (``layout.maintenance_plane is None``) is a no-op — the bay
    rectangle imposes no constraint. Closed-bay is drawn as a hatched red
    wall over the partial-width rectangle
    (``MaintenanceBay.center_x_m`` / ``width_m`` / ``depth_m``) with an
    ``IN MAINTENANCE: <plane_id>`` label centered inside. The occupant
    itself is not drawn — by Layout invariant it is absent from
    ``placements`` and the draw loop skips it without special-casing.
    """
    if layout.maintenance_plane is None:
        return

    hangar = layout.hangar
    bay = hangar.maintenance_bay
    bay_x_lo = bay.center_x_m - bay.width_m / 2
    bay_x_hi = bay.center_x_m + bay.width_m / 2
    bay_y_lo = hangar.length_m - bay.depth_m
    bay_y_hi = hangar.length_m

    patch = MplPolygon(
        [
            (bay_x_lo, bay_y_lo),
            (bay_x_hi, bay_y_lo),
            (bay_x_hi, bay_y_hi),
            (bay_x_lo, bay_y_hi),
        ],
        closed=True,
        facecolor=_BAY_WALL_FACE,
        edgecolor=_BAY_WALL_EDGE,
        alpha=_BAY_WALL_ALPHA,
        hatch=_BAY_WALL_HATCH,
        lw=1.5,
        zorder=0,
    )
    ax.add_patch(patch)

    ax.text(
        (bay_x_lo + bay_x_hi) / 2,
        (bay_y_lo + bay_y_hi) / 2,
        f"IN MAINTENANCE: {layout.maintenance_plane}",
        ha="center",
        va="center",
        fontsize=9,
        fontweight="bold",
        color=_BAY_LABEL_COLOR,
        zorder=4,
    )


def _draw_aircraft(ax: Any, layout: Layout) -> None:
    """Draw each placed plane as its world parts, color-keyed by wing position."""
    for placement in layout.placements:
        aircraft = layout.fleet[placement.plane_id]
        color = _WING_COLORS.get(aircraft.wing_position, _FALLBACK_COLOR)
        world_parts = aircraft_parts_world(aircraft, placement)
        # Gear/cart glyph drawn before parts so the fuselage patch (zorder=2)
        # sits on top — wheels peek out at nose/tail/sides.
        _draw_gear_glyph(ax, placement, aircraft)
        for part in world_parts:
            _draw_part(ax, part, color)
        _annotate_plane(ax, placement, aircraft.id)


def _draw_part(ax: Any, part: WorldPart, color: str) -> None:
    """Render a single world part. Fuselage is near-opaque (two fuselages
    overlapping is always a conflict; no value in seeing through), wing
    translucent (so stacked wings show their plan-view overlap visually),
    strut as a thin outlined polygon (struts are physically thin, a fill
    would be near-invisible), tail rendered like a small fuselage (same
    z-tier, same conflict semantics).

    Any other ``PartKind`` value raises ``ValueError`` rather than falling
    through to a generic style. ``PartKind`` is closed at the type level,
    so a future addition without updating the renderer is a real bug —
    fail loud here.
    """
    coords = list(part.polygon.exterior.coords)[:-1]
    if part.kind == "fuselage" or part.kind == "tail":
        patch = MplPolygon(
            coords,
            closed=True,
            facecolor=color,
            edgecolor=_HANGAR_EDGE,
            alpha=_FUSELAGE_ALPHA,
            lw=0.5,
            zorder=2,
        )
    elif part.kind == "wing":
        patch = MplPolygon(
            coords,
            closed=True,
            facecolor=color,
            edgecolor=color,
            alpha=_WING_ALPHA,
            lw=0.5,
            zorder=1,
        )
    elif part.kind == "strut":
        patch = MplPolygon(
            coords,
            closed=True,
            facecolor="none",
            edgecolor=_HANGAR_EDGE,
            lw=_STRUT_LINEWIDTH,
            zorder=3,
        )
    else:
        raise ValueError(
            f"_draw_part: unhandled part kind {part.kind!r}. "
            f"visualize.py must be updated when PartKind grows."
        )
    ax.add_patch(patch)


def _add_wheel(ax: Any, wx: float, wy: float) -> MplCircle:
    """Add a single wheel-disc circle at world coordinates ``(wx, wy)``."""
    circle = MplCircle(
        (wx, wy),
        radius=_WHEEL_RADIUS_M,
        facecolor=_WHEEL_COLOR,
        edgecolor=_WHEEL_COLOR,
        lw=0.4,
        zorder=_GLYPH_ZORDER,
    )
    ax.add_patch(circle)
    return circle


def _draw_gear_glyph(ax: Any, placement: Placement, aircraft: Aircraft) -> None:
    """Draw landing-gear wheels or a cart glyph depending on ``placement.on_carts``.

    Geometry is approximated from the fuselage part's ``offset_x_m`` and
    ``length_m`` — no model changes are required.  The fuselage part is
    identified as the first ``kind == "fuselage"`` part; every fleet entry
    has exactly one fuselage as its first part, so this is safe.

    **Own-gear (``on_carts=False``):** wheel positions per ``aircraft.gear``:

    - ``nosewheel`` (tricycle): one nose wheel near the fuselage nose tip, two
      main wheels behind the CG at ±lateral offset.
    - ``tailwheel``: two main wheels ahead of the CG, one tailwheel near the
      fuselage aft tip.
    - ``monowheel``: a single centred main wheel at the CG origin.

    **Cart-borne (``on_carts=True``):** a dolly-deck rectangle (lighter gray,
    translucent) centred at the origin with four small corner wheel circles,
    drawn in the same world-transform path as parts so it rotates with the
    aircraft heading.
    """
    # Locate the fuselage part for dimensional reference.
    fuselage_part = next(
        (p for p in aircraft.parts if p.kind == "fuselage"),
        None,
    )
    if fuselage_part is None:
        # No fuselage found — skip silently.  This is defensive; every fleet
        # entry today has a fuselage, but we should not crash on novel entries.
        return

    fus_cx = fuselage_part.offset_x_m  # local +x centre of fuselage
    fus_half_len = fuselage_part.length_m / 2.0
    fus_half_wid = fuselage_part.width_m / 2.0

    fus_aft_x = fus_cx - fus_half_len  # local +x: aft/tail tip

    if placement.on_carts:
        _draw_cart_glyph(ax, placement)
    elif aircraft.gear == "nosewheel":
        # Nose wheel — near the nose tip
        nose_u = fus_cx + fus_half_len * _NOSE_GEAR_FRAC
        wx, wy = local_to_world(nose_u, 0.0, placement)
        _add_wheel(ax, wx, wy)
        # Main gear pair — slightly aft of CG (behind the wing root)
        main_u = fus_cx - fus_half_len * _MAIN_GEAR_FWD_FRAC
        lateral = fus_half_wid * _MAIN_GEAR_LATERAL_FRAC
        for v in (+lateral, -lateral):
            wx, wy = local_to_world(main_u, v, placement)
            _add_wheel(ax, wx, wy)
    elif aircraft.gear == "tailwheel":
        # Main gear pair — forward of CG, ahead of the wing root
        main_u = fus_cx + fus_half_len * _MAIN_GEAR_TAILDRAGGER_FWD_FRAC
        lateral = fus_half_wid * _MAIN_GEAR_LATERAL_FRAC
        for v in (+lateral, -lateral):
            wx, wy = local_to_world(main_u, v, placement)
            _add_wheel(ax, wx, wy)
        # Tailwheel — near the aft tip
        tail_u = fus_aft_x + fus_half_len * (1.0 - _NOSE_GEAR_FRAC)
        wx, wy = local_to_world(tail_u, 0.0, placement)
        _add_wheel(ax, wx, wy)
    elif aircraft.gear == "monowheel":
        # Single centred main wheel at the gear/cart origin
        wx, wy = local_to_world(0.0, 0.0, placement)
        _add_wheel(ax, wx, wy)
    # No else needed — Gear is a closed Literal; mypy and the model guard all values.


def _draw_cart_glyph(ax: Any, placement: Placement) -> None:
    """Draw a four-wheel dolly/cart glyph centred at the gear origin.

    The deck rectangle is sized by the module constants
    ``_CART_DECK_HALF_LENGTH_M`` / ``_CART_DECK_HALF_WIDTH_M`` and rotated
    with ``placement.heading_deg`` via the standard world-transform.  Four
    small wheel discs sit at the deck corners so the glyph reads as a
    wheeled dolly rather than a coloured rectangle.
    """
    # Deck corners in plane-local coordinates (centred at origin, ±L, ±W).
    hl = _CART_DECK_HALF_LENGTH_M
    hw = _CART_DECK_HALF_WIDTH_M
    corner_locals = [
        (+hl, +hw),
        (+hl, -hw),
        (-hl, -hw),
        (-hl, +hw),
    ]
    deck_world = [local_to_world(u, v, placement) for u, v in corner_locals]
    deck_patch = MplPolygon(
        deck_world,
        closed=True,
        facecolor=_CART_DECK_COLOR,
        edgecolor=_WHEEL_COLOR,
        alpha=_CART_DECK_ALPHA,
        lw=0.8,
        zorder=_GLYPH_ZORDER,
    )
    ax.add_patch(deck_patch)

    # Four corner wheel discs.
    for u, v in corner_locals:
        wx, wy = local_to_world(u, v, placement)
        _add_wheel(ax, wx, wy)


def _annotate_plane(ax: Any, placement: Placement, plane_id: str) -> None:
    """Plane id label + a short arrow showing the nose direction."""
    dx, dy = nose_direction(placement.heading_deg)
    ax.annotate(
        "",
        xy=(
            placement.x_m + dx * _NOSE_ARROW_LENGTH_M,
            placement.y_m + dy * _NOSE_ARROW_LENGTH_M,
        ),
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


def _draw_conflict_overlay(ax: Any, layout: Layout, check_result: CheckResult) -> None:
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


def _draw_tow_paths(ax: Any, moves_plan: MovesPlan) -> None:
    """Overlay each plane's tow path as a polyline, one colour per plane (#192).

    Companion to :func:`_draw_conflict_overlay` at the same z-tier (5), so the
    paths read on top of the aircraft parts (zorder 1-4) — spike Q7. Each path
    is the sampled :class:`~hangarfit.towplanner.DubinsArc` polyline from the
    door-cone entry pose to the target slot. Colours are assigned by *sorted*
    ``plane_id`` so a given plan always renders the same plane in the same
    colour regardless of move order (the ADR-0003 determinism spirit). The
    in-memory ``MovesPlan`` shape is rich enough that a per-move PNG sequence
    or animation can be added later without changing it (spike Q7).

    Each polyline carries its ``plane_id`` as a matplotlib ``label``. No
    legend is rendered today (``_finalize_axes`` draws none), so the label is
    currently inert — it is a deliberate forward hook for a future legend, and
    a path is already disambiguated by terminating at its annotated slot.
    """
    plane_ids = sorted({move.plane_id for move in moves_plan.moves})
    colour_for = {
        pid: _TOW_PATH_COLORS[i % len(_TOW_PATH_COLORS)] for i, pid in enumerate(plane_ids)
    }
    for move in moves_plan.moves:
        poses = list(move.path.sample())
        xs = [p.x_m for p in poses]
        ys = [p.y_m for p in poses]
        ax.plot(
            xs,
            ys,
            color=colour_for[move.plane_id],
            lw=_TOW_PATH_LINEWIDTH,
            zorder=5,
            label=move.plane_id,
        )


def _finalize_axes(ax: Any, layout: Layout, title: str | None) -> None:
    """Equal-aspect axes, hangar-sized view, sensible labels, optional title.

    The y-axis is inverted so the door (y = 0) renders at the *top* of
    the image and the back wall + maintenance bay at the bottom. That
    matches the coordinate diagram in
    ``docs/architecture/08-crosscutting-concepts.md`` "The coordinate
    convention" (which draws y going downward with the door at top) and
    matches how a person standing in front of the open door would draw
    the layout: deeper-into-hangar is farther from you.
    """
    hangar = layout.hangar
    ax.set_xlim(-_VIEW_PADDING_M, hangar.width_m + _VIEW_PADDING_M)
    ax.set_ylim(-_VIEW_PADDING_M, hangar.length_m + _VIEW_PADDING_M)
    ax.invert_yaxis()
    ax.set_aspect("equal")
    ax.set_xlabel("x (m) — door wall")
    ax.set_ylabel("y (m) — deeper into hangar")
    ax.grid(True, alpha=0.2, zorder=0)
    if title is not None:
        ax.set_title(title)
