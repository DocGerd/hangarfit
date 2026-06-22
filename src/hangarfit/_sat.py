"""Opt-in numpy SAT box oracle (#754 Lever B) — an accelerator, NOT the authority.

A second, **opt-in** narrow-phase for the dominant pairwise collision test on the
box-curriculum rungs, where every part is an oriented rectangle and the pairwise
shapely ``Polygon`` work dominates (#381). These pure-numpy convex-polygon kernels
reproduce the GEOS verdict surface of :func:`hangarfit.geometry.polygon_overlap` /
:func:`~hangarfit.geometry.polygon_overlap_area` to float noise:

* :func:`sat_polygon_overlap` ↔ ``polygon_overlap`` (both clearance branches),
* :func:`sat_polygon_overlap_area` ↔ ``polygon_overlap_area``.

**This module never transforms coordinates.** It consumes the *already-world*
``(N, 2)`` corner arrays the production det(−1) transform
(:func:`hangarfit.geometry.local_to_world`, ADR-0002) produced — exactly the
spike's methodology (#735), so there is no second place a sign-flip could hide.

**Equivalence is to float noise, not bit-for-bit** (spike #735: max distance/area
delta ~5e-15, **0 verdict flips** across a 200k clearance-weighted corpus). CPU
shapely therefore stays the determinism + validity authority (#694); this path is
gated behind a default-off ``--sat-collisions`` flag, used only when BOTH parts
are oriented rectangles (a part-kind guard in :mod:`hangarfit.collisions` falls
back to shapely the instant any tapered/strut polygon part appears). The
equivalence contract is pinned in ``tests/test_sat_collisions.py``.
"""

from __future__ import annotations

import math

import numpy as np

# ---------------------------------------------------------------------------
# Convex-polygon SAT / GJK-distance / Sutherland–Hodgman kernels.
# Ported verbatim from the #735 spike (tests/spikes/test_sat_geos_equivalence.py),
# which validated their GEOS-equivalence; do not "optimise" without re-running
# that equivalence corpus (the float boundary is load-bearing).
# ---------------------------------------------------------------------------


def _edge_axes(corners: np.ndarray) -> np.ndarray:
    """Outward edge normals (normalised) for a convex polygon ring.

    Direction only — the sign is irrelevant to the min/max projection test, so
    these run on the raw (possibly CW) corners before any ``_ensure_ccw``. For SAT
    a box has 4 edges but only 2 distinct normal directions; returning all 4 is
    harmless (duplicate axes don't change the min/max test).
    """
    rolled = np.roll(corners, -1, axis=0)
    edges = rolled - corners
    # Normal to (ex, ey) is (-ey, ex). Normalise so a projection gap is a metric
    # distance (irrelevant to the boolean test, but keeps the math reusable/honest).
    normals = np.stack([-edges[:, 1], edges[:, 0]], axis=1)
    lengths = np.hypot(normals[:, 0], normals[:, 1])
    # Guard a degenerate (zero-length) edge so a pathological input surfaces as a
    # clean value rather than a nan; real oriented rectangles have positive extent.
    safe = np.where(lengths > 0.0, lengths, 1.0)
    return normals / safe[:, None]


def _projection_interval(corners: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    proj = corners @ axis
    return float(proj.min()), float(proj.max())


def sat_interiors_overlap(corners_a: np.ndarray, corners_b: np.ndarray) -> bool:
    """True iff the two convex polygons share interior area (SAT).

    Matches GEOS ``intersects and not touches``: a separating axis with a
    **non-positive** overlap (gap ``>= 0``) means no shared interior, so a purely
    touching configuration (gap exactly ``0`` on some axis) is NOT an interior
    overlap — the strict ``> 0`` draws the same boundary GEOS does.
    """
    for corners in (corners_a, corners_b):
        for axis in _edge_axes(corners):
            a_min, a_max = _projection_interval(corners_a, axis)
            b_min, b_max = _projection_interval(corners_b, axis)
            overlap = min(a_max, b_max) - max(a_min, b_min)
            if overlap <= 0.0:
                return False
    return True


def _point_segment_distance_sq(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Squared Euclidean distance from point ``p`` to segment ``a→b``."""
    ab = b - a
    denom = float(ab @ ab)
    if denom == 0.0:
        d = p - a
        return float(d @ d)
    t = float((p - a) @ ab) / denom
    t = min(1.0, max(0.0, t))
    closest = a + t * ab
    d = p - closest
    return float(d @ d)


def convex_min_separation(corners_a: np.ndarray, corners_b: np.ndarray) -> float:
    """Exact min edge-to-edge separation between two convex polygons (GJK distance).

    ``0.0`` if they share interior — matching shapely ``Polygon.distance``, which
    returns ``0.0`` for intersecting geometries. For convex inputs the minimum
    separation is attained at a vertex-of-one / edge-of-the-other pair, so the
    minimum over all vertex→edge pairs (both ways) is exact. A boundary touch
    (shared edge/vertex, zero interior) yields exactly ``0`` from the sweep, so no
    special-casing is needed.
    """
    if sat_interiors_overlap(corners_a, corners_b):
        return 0.0
    best_sq = math.inf
    for poly_p, poly_e in ((corners_a, corners_b), (corners_b, corners_a)):
        n_e = len(poly_e)
        for p in poly_p:
            for i in range(n_e):
                a = poly_e[i]
                b = poly_e[(i + 1) % n_e]
                d_sq = _point_segment_distance_sq(p, a, b)
                if d_sq < best_sq:
                    best_sq = d_sq
    return math.sqrt(best_sq)


def _line_intersect(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, p4: np.ndarray) -> np.ndarray:
    """Intersection of the infinite lines (p1,p2) and (p3,p4)."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if denom == 0.0:
        # Parallel/collinear lines. Provably DEAD under the rectangle-only guard:
        # Sutherland–Hodgman only calls this when a subject edge crosses a clipper
        # half-plane (the in/out state flips), which cannot happen on a parallel
        # edge pair — so this returns a harmless placeholder for an unreachable
        # case rather than handling a real degeneracy. If the SAT eligibility ever
        # widened beyond convex rectangles, revisit this.
        return p1
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return np.array([x1 + t * (x2 - x1), y1 + t * (y2 - y1)], dtype=np.float64)


def _sutherland_hodgman_clip(subject: np.ndarray, clipper: np.ndarray) -> np.ndarray:
    """Clip convex ``subject`` against convex ``clipper`` (both must be CCW).

    Standard Sutherland–Hodgman: walk each clipper edge as a half-plane and keep
    the subject portion inside. Returns the clipped vertex ring (possibly empty).
    """
    output = [np.asarray(pt, dtype=np.float64) for pt in subject]
    n_clip = len(clipper)
    for i in range(n_clip):
        if not output:
            break
        c_a = clipper[i]
        c_b = clipper[(i + 1) % n_clip]
        edge = c_b - c_a
        input_list = output
        output = []
        n_in = len(input_list)
        for j in range(n_in):
            cur = input_list[j]
            prv = input_list[j - 1]
            # Cross-product sign: >= 0 means the point is left of the directed
            # clipper edge, i.e. inside for a CCW clipper.
            cur_in = edge[0] * (cur[1] - c_a[1]) - edge[1] * (cur[0] - c_a[0]) >= 0.0
            prv_in = edge[0] * (prv[1] - c_a[1]) - edge[1] * (prv[0] - c_a[0]) >= 0.0
            if cur_in:
                if not prv_in:
                    output.append(_line_intersect(prv, cur, c_a, c_b))
                output.append(cur)
            elif prv_in:
                output.append(_line_intersect(prv, cur, c_a, c_b))
    return np.asarray(output, dtype=np.float64) if output else np.empty((0, 2))


def _shoelace_area(corners: np.ndarray) -> float:
    if len(corners) < 3:
        return 0.0
    x = corners[:, 0]
    y = corners[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _ensure_ccw(corners: np.ndarray) -> np.ndarray:
    """Return the corners in CCW order (signed shoelace area > 0).

    The det(−1) world transform (ADR-0002) flips winding, so production world
    corners may arrive CW; Sutherland–Hodgman needs a consistent CCW sign.
    """
    x = corners[:, 0]
    y = corners[:, 1]
    signed = 0.5 * (float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    return corners if signed > 0.0 else corners[::-1].copy()


def sat_clip_area(corners_a: np.ndarray, corners_b: np.ndarray) -> float:
    """Intersection area of two convex polygons (Sutherland–Hodgman + shoelace).

    Matches GEOS ``intersection().area``; ``0.0`` for disjoint inputs.
    """
    subject = _ensure_ccw(corners_a)
    clipper = _ensure_ccw(corners_b)
    clipped = _sutherland_hodgman_clip(subject, clipper)
    return _shoelace_area(clipped)


# ---------------------------------------------------------------------------
# Collision-facing wrappers — drop-in for the GEOS predicates the collision
# oracle calls, but on corner arrays. Same clearance semantics as
# :func:`hangarfit.geometry.polygon_overlap` / ``polygon_overlap_area``.
# ---------------------------------------------------------------------------


def sat_polygon_overlap(
    corners_a: np.ndarray, corners_b: np.ndarray, clearance: float = 0.0
) -> bool:
    """SAT analogue of :func:`hangarfit.geometry.polygon_overlap`.

    - ``clearance > 0``: conflict iff :func:`convex_min_separation` ``< clearance``
      (the live box-rung branch — ``Polygon.distance < clearance``).
    - ``clearance == 0``: conflict only on genuine interior overlap
      (:func:`sat_interiors_overlap`; a boundary touch is NOT a conflict).
    - ``clearance < 0``: raises, mirroring ``polygon_overlap`` (no sensible
      "negative clearance"; the upstream :class:`~hangarfit.models.Hangar`
      already constrains the configured value to non-negative).
    """
    if clearance < 0:
        raise ValueError(f"sat_polygon_overlap: clearance must be non-negative, got {clearance}")
    if clearance > 0:
        return convex_min_separation(corners_a, corners_b) < clearance
    return sat_interiors_overlap(corners_a, corners_b)


def sat_polygon_overlap_area(corners_a: np.ndarray, corners_b: np.ndarray) -> float:
    """SAT analogue of :func:`hangarfit.geometry.polygon_overlap_area`."""
    return sat_clip_area(corners_a, corners_b)
