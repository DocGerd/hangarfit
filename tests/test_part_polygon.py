"""Polygon-part canonicalization + Part.local_vertices tests (issue #548).

The canonicalization invariant is the determinism crux (ADR-0003): two
orderings of the same ring MUST produce a byte-identical canonical tuple.
"""

from __future__ import annotations

import math

import pytest

from hangarfit.models import Part, _canonicalize_ring

# A simple CCW unit square, vertices listed from a non-lex-min start.
_SQUARE_CCW = [(1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
# Same square wound CW.
_SQUARE_CW = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]


def test_canonicalize_forces_ccw_and_lexmin_start() -> None:
    canon = _canonicalize_ring(_SQUARE_CCW)
    # Lex-min vertex (0,0) is first.
    assert canon[0] == (0.0, 0.0)
    # Wound CCW: signed area positive.
    n = len(canon)
    area2 = sum(
        canon[i][0] * canon[(i + 1) % n][1] - canon[(i + 1) % n][0] * canon[i][1] for i in range(n)
    )
    assert area2 > 0


def test_canonicalize_equivalent_orderings_are_identical() -> None:
    """CCW, CW, and rotated inputs of the same shape canonicalize identically."""
    rotated = _SQUARE_CCW[2:] + _SQUARE_CCW[:2]
    assert _canonicalize_ring(_SQUARE_CCW) == _canonicalize_ring(_SQUARE_CW)
    assert _canonicalize_ring(_SQUARE_CCW) == _canonicalize_ring(rotated)


def test_canonicalize_drops_closing_duplicate() -> None:
    closed = [*_SQUARE_CCW, _SQUARE_CCW[0]]
    assert _canonicalize_ring(closed) == _canonicalize_ring(_SQUARE_CCW)


def test_canonicalize_rejects_too_few_vertices() -> None:
    with pytest.raises(ValueError, match="3"):
        _canonicalize_ring([(0.0, 0.0), (1.0, 1.0)])


def test_canonicalize_rejects_non_finite() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        _canonicalize_ring([(0.0, 0.0), (1.0, 0.0), (math.inf, 1.0)])


def test_canonicalize_rejects_degenerate_collinear() -> None:
    with pytest.raises(ValueError, match="degenerate"):
        _canonicalize_ring([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])


def test_canonicalize_rejects_self_intersecting_bowtie() -> None:
    with pytest.raises(ValueError, match="self-intersect"):
        _canonicalize_ring([(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)])


def _wing(**kw):
    base = dict(
        kind="wing",
        length_m=2.0,
        width_m=10.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=1.9,
        z_top_m=2.1,
    )
    base.update(kw)
    return Part(**base)  # type: ignore[arg-type]


def test_part_defaults_local_vertices_none() -> None:
    assert _wing().local_vertices is None


def test_part_canonicalizes_local_vertices_on_construction() -> None:
    # A hexagon taper, listed CW from a non-lex-min start; must come back canonical.
    verts = [(1.0, 0.0), (0.4, -5.0), (-0.4, -5.0), (-1.0, 0.0), (-0.4, 5.0), (0.4, 5.0)]
    p = _wing(local_vertices=verts)
    assert p.local_vertices == _canonicalize_ring(verts)
    # Canonical: lex-min start, CCW.
    assert p.local_vertices is not None
    assert p.local_vertices[0] == min(p.local_vertices)


def test_part_rejects_local_vertices_outside_bbox() -> None:
    # Vertex x=1.5 exceeds half-length (length_m/2 = 1.0).
    verts = [(1.5, 0.0), (0.4, 5.0), (-0.4, 5.0), (-1.0, 0.0), (-0.4, -5.0), (0.4, -5.0)]
    with pytest.raises(ValueError, match="bbox"):
        _wing(local_vertices=verts)


def test_part_local_vertices_within_bbox_ok() -> None:
    # Tip/root within the 2.0 x 10.0 box; touching the boundary is allowed.
    verts = [(1.0, 0.0), (0.4, 5.0), (-0.4, 5.0), (-1.0, 0.0), (-0.4, -5.0), (0.4, -5.0)]
    p = _wing(local_vertices=verts)
    assert p.local_vertices is not None
    assert len(p.local_vertices) == 6
