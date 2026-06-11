"""Top-down matplotlib renderer for hangarfit layouts.

Renders a :class:`Layout` to a PNG: hangar outline, door as a gap in the
front wall (at the *top* of the rendered image, matching the coordinate
diagram in ``docs/architecture/08-crosscutting-concepts.md``),
maintenance bay rendered conditionally on ``layout.maintenance_plane``
(see :func:`_draw_maintenance_bay` for the open/closed contract), each
placed aircraft drawn as its world :class:`Part` polygons (fuselage
near-opaque — ``fuselage_front``/cockpit a darker tint than
``fuselage_aft``/tail — wing translucent so overlapping wings show their
stack, struts as thin lines). Each plane gets one colour from the
DocGerdSoft CVD-safe ``PLANES`` brand palette, keyed per plane by sorted
``plane_id``; every part also carries an ink outline so identity never rests
on hue alone. If a
:class:`CheckResult` is supplied, the parts of conflicting planes are
overdrawn in the conflict colour with a hatch + dashed edge.

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

from . import brand, metrics  # noqa: E402
from .geometry import WorldPart, aircraft_parts_world, local_to_world  # noqa: E402
from .models import Aircraft, CheckResult, Layout, Placement  # noqa: E402

# #401 honesty banner: shown whenever any placed aircraft is on placeholder
# (unmeasured) data, so a club member never mistakes an illustrative render for a
# real parking plan. Wording is shared 2D/3D via metrics.PLACEHOLDER_BANNER.
_PLACEHOLDER_BANNER = metrics.PLACEHOLDER_BANNER

if TYPE_CHECKING:
    # Annotation-only import: the runtime code in _draw_tow_paths is duck-typed
    # (moves_plan.moves / move.path.sample()), so importing MovesPlan eagerly
    # would add a needless module dependency. towplanner does not import
    # visualize, so this is safe under TYPE_CHECKING either way.
    from .towplanner import MovesPlan

# ── DocGerdSoft brand palette (Horizon-blue expression) ─────────────────────
# All brand tokens are DEFINED ONCE in :mod:`hangarfit.brand` and re-exported
# here under the names this module has always exposed, so external importers
# (e.g. ``scene.py`` historically read ``visualize.PLANES_DARK``; tests read
# ``visualize.PLANES`` / ``STATUS`` / ``_CONFLICT_COLOR`` / ``_INK_EDGE`` / …)
# keep resolving while the values live in a single source (#419). The palette is
# Okabe–Ito-derived CVD-safe (#326) and traces to ``docs/assets/BRAND.md``.
# Every plane also carries an ink outline (``_INK_EDGE``) + a mono id label, and
# conflicts add a hatch + ink edge — identity never rests on hue alone.
PLANES = brand.PLANES
PLANES_DARK = brand.PLANES_DARK
STATUS = brand.STATUS
_INK_EDGE = brand.INK_EDGE
_FALLBACK_COLOR = brand.FALLBACK_COLOR
_CONFLICT_COLOR = brand.CONFLICT_COLOR
_TOW_PATH_COLORS = brand.TOW_PATH_COLORS
_TOW_PATH_LINEWIDTH = brand.TOW_PATH_LINEWIDTH
_FUSELAGE_ALPHA = brand.FUSELAGE_ALPHA
_FUSELAGE_FRONT_DARKEN = brand.FUSELAGE_FRONT_DARKEN
_WING_ALPHA = brand.WING_ALPHA
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
# (Tokens sourced from :mod:`hangarfit.brand`, #419.)
_BAY_WALL_FACE = brand.BAY_WALL_FACE
_BAY_WALL_EDGE = brand.BAY_WALL_EDGE
_BAY_WALL_ALPHA = brand.BAY_WALL_ALPHA
_BAY_WALL_HATCH = brand.BAY_WALL_HATCH
_BAY_LABEL_COLOR = brand.BAY_LABEL_COLOR
# Wall / door / datum ink, sourced from the brand STATUS map (#3B4046,
# 10.5:1 on white). The handoff folds wall, door, and datum onto this one
# graphite-strong ink. The door is then *lightened* (``_DOOR_EDGE``) so the
# opening reads as "open" rather than a wall.
_HANGAR_EDGE = brand.HANGAR_EDGE  # "#3B4046" — wall · door · datum ink
_DOOR_EDGE = brand.DOOR_EDGE  # light gray — visually "open"

# ── Wheel / cart glyph constants ────────────────────────────────────────────
# All drawn at zorder=1.5, strictly between the wing/floor layer (zorder=1)
# and the fuselage patch (zorder=2), so the wheels peek out at the
# nose/tail/sides without obscuring aircraft body colours.
_GLYPH_ZORDER = 1.5  # between wings (1) and fuselage (2)
#
# COLOUR: neutral dark-gray that reads on the off-white floor, stays clear of
# the plane palette, the conflict-red, and the tow-path colours (all defined in
# brand.py — no hexes are restated here, so this comment can't go stale). A
# second shade is used for the cart pallets so each dolly square is distinct
# from its wheel disc.
_WHEEL_COLOR = brand.WHEEL_COLOR  # dark slate-gray — individual wheel discs
_CART_DECK_COLOR = brand.CART_DECK_COLOR  # lighter gray — cart/dolly pallet squares
_CART_DECK_ALPHA = brand.CART_DECK_ALPHA

# Wheel disc radius in meters. Visually "a tyre" at ~6–9 m fuselage scale
# inside an 18–30 m hangar. Mirror the _NOSE_ARROW_LENGTH_M tuning idiom.
_WHEEL_RADIUS_M = 0.18

# Cart pallet half-extent in meters (#321). A cart-borne plane no longer draws
# one body-sized deck rectangle; instead each wheel sits on its own small
# pallet, drawn as a square centred on the wheel position (from
# ``aircraft.wheels.positions``) and rotated with the aircraft. At 0.4 m the
# pallet (0.8 m across) reads as "a pallet under a tyre" — comfortably larger
# than the 0.18 m wheel disc it backs, yet far smaller than the ~6.5 m
# fuselage, so it never masquerades as the body.
_CART_PALLET_HALF_EXTENT_M = 0.4


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
        if metrics.has_placeholder_data(layout):
            _draw_placeholder_banner(fig)
        # Readouts only make sense for a *verified-valid* arrangement (#401): an
        # invalid one has overlaps, so a "tightest gap" of 0 would mislead. Trust a
        # supplied CheckResult, else verify (so a caller that renders an unchecked
        # layout never gets misleading numbers).
        if metrics.layout_is_valid(layout, check_result):
            _draw_readouts(fig, layout)
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
    """Hangar rectangle with a gap in the front wall for the door, a
    conditional maintenance-bay overlay (closed-bay only), and an always-on
    hatched overlay for any structural notch (ADR-0018)."""
    hangar = layout.hangar
    door_left = hangar.door.center_x_m - hangar.door.width_m / 2
    door_right = hangar.door.center_x_m + hangar.door.width_m / 2

    # Keep-out overlays first (zorder=0) so walls and aircraft layer on top.
    _draw_maintenance_bay(ax, layout)
    _draw_structural_notches(ax, layout)

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


def _draw_structural_notches(ax: Any, layout: Layout) -> None:
    """Overlay each always-on structural notch (ADR-0018) as a cross-hatched
    keep-out — the slice of the bounding rectangle that is *not* floor (e.g. the
    Herrenteich back-right office annex). Drawn unconditionally whenever notches
    are present, unlike the state-gated maintenance bay, and rendered in the
    wall ink (no fill) so it reads as "structure, not parkable floor"."""
    for notch in layout.hangar.structural_notches:
        patch = MplPolygon(
            [
                (notch.x_min_m, notch.y_min_m),
                (notch.x_max_m, notch.y_min_m),
                (notch.x_max_m, notch.y_max_m),
                (notch.x_min_m, notch.y_max_m),
            ],
            closed=True,
            facecolor="none",
            edgecolor=_HANGAR_EDGE,
            hatch="xx",
            lw=1.5,
            zorder=0,
        )
        ax.add_patch(patch)


def _draw_aircraft(ax: Any, layout: Layout) -> None:
    """Draw each placed plane as its world parts, one brand colour per plane.

    Colour is keyed *per plane* from the DocGerdSoft ``PLANES`` categorical set
    (handoff Deliverable 4) — a shift from the pre-brand scheme, which keyed
    three colours off ``wing_position``. Each placement gets a stable index by
    enumerating ``layout.placements`` sorted by ``plane_id`` (the same
    deterministic sorted-id approach :func:`_draw_tow_paths` uses), so a given
    layout always renders the same plane in the same colour regardless of
    placement order. The 9-colour palette wraps with ``idx % len(PLANES)`` for
    fleets beyond nine. Identity never rests on hue alone: every part also
    carries the ``_INK_EDGE`` outline and a mono id label.
    """
    colour_for = _plane_colour_map(layout)
    for placement in layout.placements:
        aircraft = layout.fleet[placement.plane_id]
        color = colour_for.get(placement.plane_id, _FALLBACK_COLOR)
        world_parts = aircraft_parts_world(aircraft, placement)
        # Gear/cart glyph drawn before parts so the fuselage patch (zorder=2)
        # sits on top — wheels peek out at nose/tail/sides.
        _draw_gear_glyph(ax, placement, aircraft)
        for part in world_parts:
            _draw_part(ax, part, color)
        _annotate_plane(ax, placement, aircraft.id)


def _plane_colour_map(layout: Layout) -> dict[str, str]:
    """Map each placed ``plane_id`` to a stable colour from ``PLANES``.

    Indices are assigned by *sorted* ``plane_id`` so the mapping is independent
    of placement order (ADR-0003 determinism spirit), mirroring
    :func:`_draw_tow_paths`. Wraps with ``idx % len(PLANES)`` so fleets larger
    than the nine-colour palette never raise.
    """
    plane_ids = sorted({p.plane_id for p in layout.placements})
    return {pid: PLANES[i % len(PLANES)] for i, pid in enumerate(plane_ids)}


def _darken(color: str, factor: float = _FUSELAGE_FRONT_DARKEN) -> tuple[float, float, float]:
    """Return ``color`` with each RGB channel multiplied by ``factor`` (toward
    black). Used to tint ``fuselage_front`` a darker shade of the plane's
    brand fill so the cockpit boundary is legible (ADR-0012)."""
    from matplotlib.colors import to_rgb

    r, g, b = to_rgb(color)
    return (r * factor, g * factor, b * factor)


def _draw_part(ax: Any, part: WorldPart, color: str) -> None:
    """Render a single world part. Fuselage segments are near-opaque (two
    fuselages overlapping is always a conflict; no value in seeing through) —
    ``fuselage_front`` (cockpit) a darker tint of the plane's brand fill,
    ``fuselage_aft`` (cabin-aft) the plain fill — wing translucent (so stacked
    wings show their plan-view overlap visually), strut as a thin outlined
    polygon (struts are physically thin, a fill would be near-invisible), tail
    (the horizontal stabilizer) rendered like a small fuselage (same z-tier,
    same conflict semantics), and ``vertical_stabilizer`` (the fin) opaque on
    the top z-tier as a cue that it rises through the wing layer (ADR-0023).

    Solid parts are stroked in ``_INK_EDGE`` (#14161A), the brand "never hue
    alone" outline — so a plane's silhouette reads even in greyscale or under
    colour-vision deficiency, independent of its fill hue.

    Any other ``PartKind`` value raises ``ValueError`` rather than falling
    through to a generic style. ``PartKind`` is closed at the type level,
    so a future addition without updating the renderer is a real bug —
    fail loud here.
    """
    coords = list(part.polygon.exterior.coords)[:-1]
    if part.kind == "fuselage_front":
        patch = MplPolygon(
            coords,
            closed=True,
            facecolor=_darken(color),
            edgecolor=_INK_EDGE,
            alpha=_FUSELAGE_ALPHA,
            lw=0.5,
            zorder=2,
        )
    elif part.kind == "fuselage_aft" or part.kind == "tail":
        patch = MplPolygon(
            coords,
            closed=True,
            facecolor=color,
            edgecolor=_INK_EDGE,
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
            edgecolor=_INK_EDGE,
            lw=_STRUT_LINEWIDTH,
            zorder=3,
        )
    elif part.kind == "vertical_stabilizer":
        # The fin (ADR-0023): a thin centreline surface that rises into / above
        # the wing layer. Drawn opaque and ink-edged on top (zorder above the
        # wing) so its height — invisible in a top-down view — reads as "this
        # pokes up through the wing band."
        patch = MplPolygon(
            coords,
            closed=True,
            facecolor=color,
            edgecolor=_INK_EDGE,
            alpha=_FUSELAGE_ALPHA,
            lw=0.5,
            zorder=4,
        )
    elif part.kind == "ground":
        # Ground-object footprint (#601): solid keep-out, drawn opaque like a
        # fuselage body at the fuselage z-level (above wings).
        patch = MplPolygon(
            coords,
            closed=True,
            facecolor=color,
            edgecolor=_INK_EDGE,
            alpha=_FUSELAGE_ALPHA,
            lw=0.5,
            zorder=2,
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

    Wheel positions come straight from ``aircraft.wheels.positions`` — the
    canonical per-aircraft plane-local coordinates (ADR-0013), no longer
    reconstructed from heuristic fuselage fractions. Each is mapped to world
    coords through ``local_to_world`` so the gear rotates with the aircraft
    heading.

    When the plane rides on a cart (``placement.on_carts=True``), a small cart
    pallet is drawn under each wheel instead of the plane's own bare gear (see
    ``_draw_cart_glyph``).
    """
    if placement.on_carts:
        _draw_cart_glyph(ax, placement, aircraft)
        return
    for u, v in aircraft.wheels.positions:
        wx, wy = local_to_world(u, v, placement)
        _add_wheel(ax, wx, wy)


def _add_cart_pallet(ax: Any, u: float, v: float, placement: Placement) -> MplPolygon:
    """Add one small square cart pallet centred on plane-local wheel ``(u, v)``.

    The pallet's four corners are offset by ``±_CART_PALLET_HALF_EXTENT_M`` in
    plane-local space and mapped to world coordinates via ``local_to_world`` so
    the pallet rotates with ``placement.heading_deg`` — exactly the transform
    the own-gear wheel loop uses for the disc centres.
    """
    h = _CART_PALLET_HALF_EXTENT_M
    corner_locals = (
        (u + h, v + h),
        (u + h, v - h),
        (u - h, v - h),
        (u - h, v + h),
    )
    corner_world = [local_to_world(cu, cv, placement) for cu, cv in corner_locals]
    pallet = MplPolygon(
        corner_world,
        closed=True,
        facecolor=_CART_DECK_COLOR,
        edgecolor=_WHEEL_COLOR,
        alpha=_CART_DECK_ALPHA,
        lw=0.8,
        zorder=_GLYPH_ZORDER,
    )
    ax.add_patch(pallet)
    return pallet


def _draw_cart_glyph(ax: Any, placement: Placement, aircraft: Aircraft) -> None:
    """Draw a cart pallet under each wheel of a cart-borne aircraft (#321).

    Physically the cart sits under the wheels: each wheel rides on its own
    pallet. This draws a small square pallet (``_CART_PALLET_HALF_EXTENT_M``)
    centred on every wheel position from ``aircraft.wheels.positions`` — the
    same canonical plane-local coordinates the own-gear path consumes — with a
    wheel disc on top of each. The number of pallets therefore equals the wheel
    count: 1 for a monowheel, 3 for a tricycle/tailwheel. The old single
    body-sized deck rectangle is gone; nothing here spans the fuselage.
    """
    for u, v in aircraft.wheels.positions:
        _add_cart_pallet(ax, u, v, placement)
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
        arrowprops=dict(arrowstyle="->", color=_INK_EDGE, lw=1),
        zorder=4,
    )
    ax.text(
        placement.x_m,
        placement.y_m - 0.2,
        plane_id,
        ha="center",
        va="top",
        fontsize=7,
        color=_INK_EDGE,
        zorder=4,
    )


def _draw_conflict_overlay(ax: Any, layout: Layout, check_result: CheckResult) -> None:
    """Redraw every part of every plane named in any conflict, in red,
    with a thicker dashed edge and a cross hatch so the conflict reads at
    a glance — even on a B&W printout or for red-green colour-blind viewers.

    Non-colour redundancy: ``hatch="xxx"`` (dense cross pattern) plus
    ``linestyle="--"`` (dashed stroke) ensure "this part is in conflict" is
    legible without relying on the conflict colour alone.  The conflict edge
    (``_CONFLICT_COLOR`` = ``STATUS["conflict"]``) is *kept* as a fast visual
    signal for colour-normal viewers — the non-colour signals are additive,
    not replacements.

    TODO(brand): the DocGerdSoft handoff calls for a 45° hatch (``"////"``)
    paired with the conflict fill + ink edge. The current overlay uses
    ``hatch="xxx"`` on a ``facecolor="none"`` overdraw, which satisfies the
    "never colour alone" rule and the existing accessibility regression test
    (``test_conflict_overlay_carries_non_colour_redundancy`` asserts a truthy
    hatch, not a specific pattern). Switching to the 45° fill+hatch form is a
    deliberate visual change deferred to avoid any subtle test interaction.
    """
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
                linestyle="--",
                hatch="xxx",
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
    # Deferred moves (path=None) — a #601 ground-object mover whose route search
    # is deferred to #602 — have no polyline to draw; skip them.
    routed_moves = [move for move in moves_plan.moves if move.path is not None]
    plane_ids = sorted({move.plane_id for move in routed_moves})
    colour_for = {
        pid: _TOW_PATH_COLORS[i % len(_TOW_PATH_COLORS)] for i, pid in enumerate(plane_ids)
    }
    for move in routed_moves:
        assert move.path is not None  # filtered above; narrows for the type-checker
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


def _draw_placeholder_banner(fig: Any) -> None:
    """Draw the persistent "PLACEHOLDER DATA" honesty banner across the top of the
    figure (#401). Caller decides whether to show it (unmeasured data loaded)."""
    fig.text(
        0.5,
        0.995,
        _PLACEHOLDER_BANNER,
        ha="center",
        va="top",
        fontsize=12,
        fontweight="bold",
        color=brand.PLACEHOLDER_BANNER_TEXT_2D,
        bbox={
            "facecolor": brand.PLACEHOLDER_BANNER_BG_2D,
            "edgecolor": "none",
            "boxstyle": "round,pad=0.45",
        },
    )


def _readout_text(layout: Layout) -> str:
    """Human one-liner of the actionable readouts (#401): the tightest plan-view
    inter-plane gap and the smallest wing-over-tail vertical clearance. Each reads
    ``n/a`` when undefined (single plane / no overhang)."""
    gap = metrics.min_pairwise_gap_m(layout)
    clr = metrics.min_wing_over_tail_clearance_m(layout)
    gap_s = f"{gap:.2f} m" if gap is not None else "n/a (single plane)"
    clr_s = f"{clr:.2f} m" if clr is not None else "n/a"
    return f"tightest inter-plane gap: {gap_s}    ·    smallest wing-over-tail clearance: {clr_s}"


def _draw_readouts(fig: Any, layout: Layout) -> None:
    """Draw the actionable quality readouts along the bottom of the figure (#401)."""
    fig.text(
        0.5,
        0.005,
        _readout_text(layout),
        ha="center",
        va="bottom",
        fontsize=9,
        color=brand.READOUT_TEXT_2D,
        bbox={
            "facecolor": brand.READOUT_BG_2D,
            "edgecolor": brand.READOUT_EDGE_2D,
            "boxstyle": "round,pad=0.4",
        },
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
