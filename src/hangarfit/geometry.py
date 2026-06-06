"""Geometry primitives for hangarfit.

Two distinct angle conventions live in this module and must be kept
strictly separate:

1. :func:`oriented_rect` uses **standard CCW math rotation** (positive
   ``angle_deg`` rotates a vector counter-clockwise from world ``+x``
   toward ``+y``). This is the generic 2D-rectangle builder and has no
   knowledge of compass headings or plane-local axes.

2. :func:`aircraft_parts_world` uses the **compass-style** transform
   from plane-local ``(forward, right)`` to world ``(right-along-door,
   deeper-into-hangar)`` documented in ``CLAUDE.md``. The linear part
   of this transform has determinant ``−1`` — it is a rotation
   composed with a reflection, **NOT** a pure rotation.

Mixing the two conventions is the off-by-90° trap of the project.
Tests must include at least one non-axis-aligned heading (45°) to
catch any regression — see ``tests/test_geometry.py``.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from shapely.geometry import Polygon

from .models import Aircraft, PartKind, Placement


@dataclass(frozen=True, slots=True)
class WorldPart:
    """A :class:`Part` after the plane-local → world transform.

    The collision checker (#5) iterates over ``WorldPart`` instances:
    two ``WorldPart``s from different aircraft conflict iff their
    ``polygon``s are within clearance in plan view AND their z-ranges
    overlap (see the collision rule in ``CLAUDE.md``).
    """

    polygon: Polygon
    z_bottom_m: float
    z_top_m: float
    plane_id: str
    kind: PartKind


def oriented_rect(
    cx: float,
    cy: float,
    length: float,
    width: float,
    angle_deg: float,
) -> Polygon:
    """Build an oriented rectangle as a Shapely ``Polygon``.

    The rectangle is centered at ``(cx, cy)`` with the given dimensions.
    At ``angle_deg = 0`` the length axis runs along world ``+x`` and the
    width axis along world ``+y``. Positive ``angle_deg`` rotates the
    length axis CCW from ``+x`` toward ``+y`` (standard math convention).

    Note: this is a **generic** primitive — it does NOT know about
    compass headings or plane-local axes. For aircraft transforms see
    :func:`aircraft_parts_world`.
    """
    h = math.radians(angle_deg)
    cos_h = math.cos(h)
    sin_h = math.sin(h)
    hl = length / 2.0
    hw = width / 2.0
    # Corners in local frame (CCW from front-right): +x is "forward", +y is "left side"
    corners_local = [(hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)]
    corners_world = [
        (cx + x * cos_h - y * sin_h, cy + x * sin_h + y * cos_h) for x, y in corners_local
    ]
    return Polygon(corners_world)


def polygon_overlap(p1: Polygon, p2: Polygon, clearance: float = 0.0) -> bool:
    """Whether two polygons conflict in plan view, given a clearance buffer.

    - ``clearance > 0``: ``p1`` and ``p2`` conflict iff
      ``p1.distance(p2) < clearance``. Overlapping polygons (distance 0)
      always conflict; touching polygons (distance 0) conflict because
      ``0 < clearance``; truly separated polygons conflict only when
      closer than the clearance.

    - ``clearance == 0``: conflict only on **actual area overlap**.
      Polygons that touch at the boundary (Shapely's ``touches``) are
      NOT a conflict — distance is 0 but no interior is shared.

    Raises ``ValueError`` for negative ``clearance`` — there is no
    sensible "negative clearance" semantic and the upstream
    :class:`Hangar` already constrains the configured value to
    non-negative, so a negative value here indicates a programming
    error rather than a misconfigured layout.
    """
    if clearance < 0:
        raise ValueError(f"polygon_overlap: clearance must be non-negative, got {clearance}")
    if clearance > 0:
        return p1.distance(p2) < clearance
    return p1.intersects(p2) and not p1.touches(p2)


def polygon_overlap_area(p1: Polygon, p2: Polygon) -> float:
    """Area of the intersection of two polygons (``0.0`` if disjoint).

    Used by :func:`hangarfit.collisions._pairwise_conflicts` to accumulate
    :attr:`hangarfit.models.CheckResult.total_penetration_m2` (Phase 2a's
    secondary scoring key) — and historically to format the
    ``Conflict.detail`` "overlap by 0.18 m²" message.
    """
    if not p1.intersects(p2):
        return 0.0
    return p1.intersection(p2).area


def local_to_world(u: float, v: float, placement: Placement) -> tuple[float, float]:
    """Map a single plane-local point ``(u, v)`` to world coordinates.

    This is the **one canonical definition** of the compass-style
    plane-local → world transform. ``aircraft_parts_world`` routes every
    vertex through it, and :mod:`hangarfit.visualize` imports it for its
    gear/cart glyphs, so the determinant-``−1`` formula lives in exactly
    one place (#293).

    ``placement.heading_deg`` is the compass-style angle of the nose,
    measured from world ``+y`` (deeper-into-hangar), CW positive. The
    linear part of the map is:

    .. code-block::

        world_x = placement.x_m + u·sin(h) + v·cos(h)
        world_y = placement.y_m + u·cos(h) − v·sin(h)

    where ``h = radians(placement.heading_deg)`` and ``(+u, +v)`` is
    plane-local ``(forward, right)``. The linear part has determinant
    ``−1`` (a rotation composed with a reflection), reflecting (a) the
    CW-positive compass convention vs CCW-positive math, and (b)
    plane-local ``(forward, right)`` vs world ``(right, deeper)``. This is
    deliberate — see ADR-0002 and ``CLAUDE.md`` for the full derivation.
    Do **not** "fix" it to a det = +1 pure rotation.
    """
    h = math.radians(placement.heading_deg)
    sin_h = math.sin(h)
    cos_h = math.cos(h)
    world_x = placement.x_m + u * sin_h + v * cos_h
    world_y = placement.y_m + u * cos_h - v * sin_h
    return world_x, world_y


def aircraft_parts_world(
    aircraft: Aircraft,
    placement: Placement,
) -> list[WorldPart]:
    """Transform every part of an aircraft from plane-local to world coords.

    ``placement.heading_deg`` is the compass-style angle of the nose,
    measured from world ``+y`` (deeper-into-hangar), CW positive.
    At ``heading_deg = 0`` the plane's forward direction maps to world
    ``+y``; at ``heading_deg = 90°`` it maps to world ``+x``.

    The linear transform from plane-local ``(u, v)`` (``+u`` forward,
    ``+v`` right) to world is:

    .. code-block::

        world_x = placement.x_m + u·sin(h) + v·cos(h)
        world_y = placement.y_m + u·cos(h) − v·sin(h)

    where ``h = radians(placement.heading_deg)``. The linear part has
    determinant ``−1`` (rotation composed with reflection), reflecting
    the (a) CW-positive compass convention vs CCW-positive math, and
    (b) plane-local ``(forward, right)`` vs world ``(right, deeper)``.
    See ``CLAUDE.md`` for the full derivation.

    Each vertex is routed through :func:`local_to_world`, the single
    canonical definition of this transform (#293).
    """
    world_parts: list[WorldPart] = []
    for part in aircraft.parts:
        # Build the part's polygon in plane-local coordinates. ``angle_deg``
        # rotates the part within plane-local (standard CCW). This is the
        # "in plane-local frame, where is the part?" step.
        local_poly = oriented_rect(
            cx=part.offset_x_m,
            cy=part.offset_y_m,
            length=part.length_m,
            width=part.width_m,
            angle_deg=part.angle_deg,
        )
        # Apply the plane-local-to-world transform to each vertex.
        # local_poly.exterior.coords includes the closing-point duplicate;
        # slice it off so Polygon() doesn't have to de-dup.
        world_coords = [
            local_to_world(u, v, placement) for u, v in list(local_poly.exterior.coords)[:-1]
        ]
        world_poly = Polygon(world_coords)
        world_parts.append(
            WorldPart(
                polygon=world_poly,
                z_bottom_m=part.z_bottom_m,
                z_top_m=part.z_top_m,
                plane_id=placement.plane_id,
                kind=part.kind,
            )
        )
    return world_parts


# ---------------------------------------------------------------------------
# Per-solve memoization of aircraft_parts_world (#453)
# ---------------------------------------------------------------------------
#
# The #381 profiling spike measured aircraft_parts_world as the solve→tow
# pipeline's single cross-cutting bottleneck: it rebuilds every part polygon
# from scratch on *every* collision/clearance check, and 83.8 % of those
# rebuilds are for a pose already seen this solve. The cure is a per-``solve()``
# cache, keyed on the pose, consulted at the hot call sites via
# ``cached_parts_world`` and scoped by ``pose_cache_scope``.
#
# Determinism (ADR-0003): the transform above is referentially transparent, so
# a hit returns the byte-identical WorldParts the pure function would have
# built. WorldPart is frozen/slots and every consumer is read-only, so sharing
# the cached objects is safe. The key uses *exact* float equality, so a
# near-miss is a clean miss (recompute → correct), never a false hit. The cache
# lives in a ContextVar reset on scope exit, so two sequential solves each get a
# fresh cache and the determinism-guard's double-run stays byte-identical. It is
# a plain unbounded dict with NO eviction — an LRU/eviction variant was rejected
# by the spike (it can perturb basin selection), and a module-global cache would
# return *stale* geometry if a later scenario reused a plane id for a different
# aircraft. Hence the per-solve lifetime.

# Cache key: (plane_id, x_m, y_m, heading_deg). ``on_carts`` is deliberately
# absent — carts do not affect aircraft_parts_world, so it would be an inert
# (dead) component of the key.
_PoseKey = tuple[str, float, float, float]
_pose_cache: ContextVar[dict[_PoseKey, list[WorldPart]] | None] = ContextVar(
    "aircraft_parts_world_cache", default=None
)


@contextmanager
def pose_cache_scope() -> Iterator[None]:
    """Activate a fresh per-solve ``aircraft_parts_world`` cache for the block.

    Inside the ``with`` block, :func:`cached_parts_world` memoizes by pose;
    outside it (the default) :func:`cached_parts_world` is a pure passthrough.
    The cache is reset on exit, so nested or sequential scopes never share
    state — which is what keeps a double-run solve byte-identical (ADR-0003).
    """
    token = _pose_cache.set({})
    try:
        yield
    finally:
        _pose_cache.reset(token)


def cached_parts_world(aircraft: Aircraft, placement: Placement) -> list[WorldPart]:
    """Pose-memoized :func:`aircraft_parts_world` for the active solve scope.

    When a :func:`pose_cache_scope` is active, the result is cached on
    ``(plane_id, x_m, y_m, heading_deg)`` and reused for the lifetime of that
    scope; the returned list MUST be treated read-only (it may be shared). When
    no scope is active this delegates straight to :func:`aircraft_parts_world`,
    so non-solve callers stay byte-identical (just uncached).

    Caller contract: the key omits the parts/geometry, so within one scope a
    given ``plane_id`` must always resolve to the same aircraft geometry. That
    holds because a single ``solve()`` is driven by one fixed ``scenario.fleet``
    (one :class:`~hangarfit.models.Aircraft` per id) — which is exactly why the
    cache is per-solve and never module-global.
    """
    cache = _pose_cache.get()
    if cache is None:
        return aircraft_parts_world(aircraft, placement)
    key: _PoseKey = (
        placement.plane_id,
        placement.x_m,
        placement.y_m,
        placement.heading_deg,
    )
    if key not in cache:
        cache[key] = aircraft_parts_world(aircraft, placement)
    return cache[key]
