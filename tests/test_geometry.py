"""Tests for hangarfit.geometry.

The headline tests pin the **off-by-90° / determinant−1 trap** of the
project: at a non-axis-aligned heading like 45°, the world coordinates
of plane-local axes must point into the correct quadrants. Tests at
0°, 90°, 180° alone do NOT catch a wrong-handedness implementation
(they all happen to look right by symmetry).
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Polygon

from hangarfit.geometry import (
    WorldPart,
    aircraft_parts_world,
    oriented_rect,
    polygon_overlap,
    polygon_overlap_area,
)
from hangarfit.models import Aircraft, Part, Placement

SQRT2_2 = math.sqrt(2) / 2  # ≈ 0.7071...


def _sorted_corners(poly: Polygon) -> list[tuple[float, float]]:
    """Polygon exterior coords sorted lexicographically (and with the
    closing-point duplicate removed) for stable comparison."""
    coords = list(poly.exterior.coords)
    if coords and coords[0] == coords[-1]:
        coords = coords[:-1]
    return sorted(coords)


def _almost_equal(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) < tol


def _corners_almost_equal(
    poly: Polygon, expected: list[tuple[float, float]], tol: float = 1e-9
) -> bool:
    got = _sorted_corners(poly)
    want = sorted(expected)
    if len(got) != len(want):
        return False
    return all(
        _almost_equal(gx, wx, tol) and _almost_equal(gy, wy, tol)
        for (gx, gy), (wx, wy) in zip(got, want)
    )


# ----------------------------------------------------------------------------
# oriented_rect
# ----------------------------------------------------------------------------


class TestOrientedRect:
    def test_axis_aligned(self) -> None:
        """A 10×4 rect centered at origin at angle 0: corners at (±5, ±2)."""
        rect = oriented_rect(cx=0, cy=0, length=10, width=4, angle_deg=0)
        expected = [(5, -2), (5, 2), (-5, 2), (-5, -2)]
        assert _corners_almost_equal(rect, expected)

    def test_translated(self) -> None:
        rect = oriented_rect(cx=10, cy=20, length=4, width=2, angle_deg=0)
        expected = [(12, 19), (12, 21), (8, 21), (8, 19)]
        assert _corners_almost_equal(rect, expected)

    def test_rotated_90(self) -> None:
        """At 90° CCW, length axis runs along +y, width along -x."""
        rect = oriented_rect(cx=0, cy=0, length=10, width=4, angle_deg=90)
        # Corners (rotated): the long axis is now ±5 along y, short ±2 along x
        expected = [(2, 5), (-2, 5), (-2, -5), (2, -5)]
        assert _corners_almost_equal(rect, expected)

    def test_rotated_45(self) -> None:
        """At 45° CCW, a 2×2 square's corner at local (1, -1) maps to world (√2, 0)."""
        rect = oriented_rect(cx=0, cy=0, length=2, width=2, angle_deg=45)
        # Square is symmetric; corners should be at distance √2 along the axes.
        expected = [(math.sqrt(2), 0), (0, math.sqrt(2)), (-math.sqrt(2), 0), (0, -math.sqrt(2))]
        assert _corners_almost_equal(rect, expected)

    def test_area_preserved_under_rotation(self) -> None:
        rect0 = oriented_rect(cx=0, cy=0, length=10, width=4, angle_deg=0)
        rect45 = oriented_rect(cx=0, cy=0, length=10, width=4, angle_deg=45)
        rect90 = oriented_rect(cx=0, cy=0, length=10, width=4, angle_deg=90)
        rect_neg = oriented_rect(cx=0, cy=0, length=10, width=4, angle_deg=-30)
        assert _almost_equal(rect0.area, 40.0)
        assert _almost_equal(rect45.area, 40.0)
        assert _almost_equal(rect90.area, 40.0)
        assert _almost_equal(rect_neg.area, 40.0)


# ----------------------------------------------------------------------------
# polygon_overlap
# ----------------------------------------------------------------------------


class TestPolygonOverlap:
    def test_disjoint_no_overlap(self) -> None:
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(5, 0, 2, 2, 0)
        assert polygon_overlap(p1, p2) is False

    def test_overlapping_areas(self) -> None:
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(1, 0, 2, 2, 0)
        assert polygon_overlap(p1, p2) is True

    def test_clearance_zero_touching_not_a_conflict(self) -> None:
        """At clearance=0, polygons touching at the boundary do NOT conflict
        (no interior overlap)."""
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(2, 0, 2, 2, 0)  # share the line x=1
        assert polygon_overlap(p1, p2, clearance=0.0) is False

    def test_clearance_positive_touching_is_a_conflict(self) -> None:
        """At clearance>0, polygons exactly touching (distance 0) conflict."""
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(2, 0, 2, 2, 0)
        assert polygon_overlap(p1, p2, clearance=0.1) is True

    def test_clearance_separates(self) -> None:
        """Polygons farther apart than clearance do not conflict."""
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(5, 0, 2, 2, 0)  # 3m gap (5-1-1)
        assert polygon_overlap(p1, p2, clearance=0.3) is False

    def test_clearance_pulls_in(self) -> None:
        """Polygons just outside clearance: no conflict. Just inside: conflict."""
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(2.5, 0, 2, 2, 0)  # 0.5m gap
        assert polygon_overlap(p1, p2, clearance=0.3) is False
        assert polygon_overlap(p1, p2, clearance=0.6) is True

    def test_negative_clearance_rejected(self) -> None:
        """No sensible 'negative clearance' semantic — raise instead of
        silently falling through to the clearance=0 branch."""
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(5, 0, 2, 2, 0)
        with pytest.raises(ValueError, match="clearance must be non-negative"):
            polygon_overlap(p1, p2, clearance=-0.1)


class TestPolygonOverlapArea:
    def test_disjoint(self) -> None:
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(5, 0, 2, 2, 0)
        assert polygon_overlap_area(p1, p2) == 0.0

    def test_overlapping(self) -> None:
        p1 = oriented_rect(0, 0, 2, 2, 0)  # area 4, x ∈ [-1, 1]
        p2 = oriented_rect(1, 0, 2, 2, 0)  # area 4, x ∈ [0, 2]
        # Intersection: x ∈ [0, 1], y ∈ [-1, 1] → area = 1 * 2 = 2.0
        assert _almost_equal(polygon_overlap_area(p1, p2), 2.0)

    def test_touching_zero_area(self) -> None:
        p1 = oriented_rect(0, 0, 2, 2, 0)
        p2 = oriented_rect(2, 0, 2, 2, 0)
        assert polygon_overlap_area(p1, p2) == 0.0


# ----------------------------------------------------------------------------
# aircraft_parts_world — the core transform.
#
# Per CLAUDE.md the transform is:
#   world_x = px + u·sin(h) + v·cos(h)
#   world_y = py + u·cos(h) − v·sin(h)
# with determinant −1 (rotation + reflection).
#
# The 45° tests are the critical regression catches: a textbook CCW
# rotation matrix would produce the wrong quadrants at h=45 even though
# 0°/90°/180° still look correct by symmetry.
# ----------------------------------------------------------------------------


def _aircraft_with_one_part(
    part: Part,
    *,
    movement_mode: str = "always_own_gear",
    turn_radius_m: float | None = 5.0,
) -> Aircraft:
    return Aircraft(
        id="probe",
        name="Probe",
        wing_position="high",
        gear="tailwheel",
        movement_mode=movement_mode,  # type: ignore[arg-type]
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(part,),
    )


def _point_part(
    *,
    offset_x_m: float = 0.0,
    offset_y_m: float = 0.0,
    length_m: float = 0.001,
    width_m: float = 0.001,
) -> Part:
    """A tiny rectangle approximating a point at (offset_x, offset_y) plane-local."""
    return Part(
        kind="fuselage",
        length_m=length_m,
        width_m=width_m,
        offset_x_m=offset_x_m,
        offset_y_m=offset_y_m,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )


class TestAircraftPartsWorld:
    def test_heading_zero_nose_maps_to_world_plus_y(self) -> None:
        """At heading 0°, a part at plane-local (u=1, v=0) lands at world (px, py+1)."""
        ac = _aircraft_with_one_part(_point_part(offset_x_m=1.0, offset_y_m=0.0))
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, 0.0, tol=1e-6)
        assert _almost_equal(cy, 1.0, tol=1e-6)

    def test_heading_zero_right_wingtip_maps_to_world_plus_x(self) -> None:
        """At heading 0°, a part at plane-local (u=0, v=1) lands at world (px+1, py)."""
        ac = _aircraft_with_one_part(_point_part(offset_x_m=0.0, offset_y_m=1.0))
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, 1.0, tol=1e-6)
        assert _almost_equal(cy, 0.0, tol=1e-6)

    def test_heading_90_nose_maps_to_world_plus_x(self) -> None:
        """At heading 90°, nose (plane +x) points to world +x."""
        ac = _aircraft_with_one_part(_point_part(offset_x_m=1.0, offset_y_m=0.0))
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=90.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, 1.0, tol=1e-6)
        assert _almost_equal(cy, 0.0, tol=1e-6)

    def test_heading_90_right_wingtip_maps_to_world_minus_y(self) -> None:
        """At heading 90° (nose right), right-wingtip points toward door (world -y)."""
        ac = _aircraft_with_one_part(_point_part(offset_x_m=0.0, offset_y_m=1.0))
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=90.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, 0.0, tol=1e-6)
        assert _almost_equal(cy, -1.0, tol=1e-6)

    def test_heading_45_nose_in_plus_x_plus_y_quadrant(self) -> None:
        """🔬 The off-by-90° regression test.

        At heading 45°, nose should point into the (+x, +y) quadrant.
        A textbook CCW rotation matrix at 45° would send the forward
        vector to (cos 45, sin 45) — same direction here — but the
        right-wingtip case below distinguishes the two.
        """
        ac = _aircraft_with_one_part(_point_part(offset_x_m=1.0, offset_y_m=0.0))
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=45.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, SQRT2_2, tol=1e-6)
        assert _almost_equal(cy, SQRT2_2, tol=1e-6)

    def test_heading_45_right_wingtip_in_plus_x_minus_y_quadrant(self) -> None:
        """🔬 The DEFINITIVE off-by-90° / determinant-sign regression.

        A textbook CCW rotation matrix would send plane-local (0, 1) to
        (-sin 45, cos 45) = (-√2/2, +√2/2) — UPPER-LEFT quadrant.
        The correct (det=-1) transform sends it to (cos 45, -sin 45)
        = (+√2/2, -√2/2) — LOWER-RIGHT quadrant (right and toward door).

        If this test fails, the implementation is using a pure rotation
        somewhere and the parts model will be silently wrong.
        """
        ac = _aircraft_with_one_part(_point_part(offset_x_m=0.0, offset_y_m=1.0))
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=45.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, SQRT2_2, tol=1e-6), f"x expected {SQRT2_2}, got {cx}"
        assert _almost_equal(cy, -SQRT2_2, tol=1e-6), f"y expected {-SQRT2_2}, got {cy}"

    def test_placement_offset_applied(self) -> None:
        """Placement translation is applied AFTER the rotation/reflection."""
        ac = _aircraft_with_one_part(_point_part(offset_x_m=1.0, offset_y_m=0.0))
        pl = Placement(plane_id="probe", x_m=10.0, y_m=20.0, heading_deg=0.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, 10.0, tol=1e-6)
        assert _almost_equal(cy, 21.0, tol=1e-6)  # 20 + 1 (nose forward)

    def test_heading_135_nose_distinguishes_correct_from_ccw(self) -> None:
        """🔬 Bonus regression: at heading 135°, the NOSE itself
        distinguishes the correct transform from a textbook CCW rotation
        — unlike at heading 45° where sin(45°) == cos(45°) makes the two
        formulations coincide on the nose vector. At 135°:

          correct: nose → (sin 135°, cos 135°) = (+√2/2, -√2/2)
          CCW:     nose → (cos 135°, sin 135°) = (-√2/2, +√2/2)

        Different in BOTH coordinates — the cleanest single test for the
        wrong-handedness bug."""
        ac = _aircraft_with_one_part(_point_part(offset_x_m=1.0, offset_y_m=0.0))
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=135.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, SQRT2_2, tol=1e-6), f"x expected {SQRT2_2}, got {cx}"
        assert _almost_equal(cy, -SQRT2_2, tol=1e-6), f"y expected {-SQRT2_2}, got {cy}"

    @pytest.mark.parametrize("heading_deg", [180.0, 360.0, 720.0, -360.0])
    def test_heading_wraparound_works(self, heading_deg: float) -> None:
        """Heading values outside [-180, 180] or multiples of 360° should
        produce the expected forward direction (sin/cos handle wrap natively).
        Pins that we don't normalize heading anywhere upstream and that the
        transform stays consistent across the wrap."""
        ac = _aircraft_with_one_part(_point_part(offset_x_m=1.0, offset_y_m=0.0))
        pl = Placement(
            plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=heading_deg, on_carts=False
        )
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        expected_x = math.sin(math.radians(heading_deg))
        expected_y = math.cos(math.radians(heading_deg))
        assert _almost_equal(cx, expected_x, tol=1e-6)
        assert _almost_equal(cy, expected_y, tol=1e-6)

    def test_part_angle_deg_composed_with_world_transform(self) -> None:
        """🔬 Coverage gap caught in PR #11 review: every other test uses
        ``angle_deg=0``. This test exercises the composition of
        (in-plane-local rotation) ∘ (world transform).

        Setup: a thin rectangle at plane-local origin with ``angle_deg=90``
        (rotated CCW 90° within plane-local) — so its long axis now runs
        along plane-local +y instead of +x. At placement heading 0°, the
        plane-local axes map as plane +x → world +y, plane +y → world +x.
        So the rectangle's long axis (now along plane +y) should run
        along world +x.
        """
        part = Part(
            kind="strut",
            length_m=4.0,  # long
            width_m=0.1,   # thin
            offset_x_m=0.0,
            offset_y_m=0.0,
            angle_deg=90.0,  # rotate CCW 90° within plane-local
            z_bottom_m=0.5,
            z_top_m=2.0,
        )
        ac = _aircraft_with_one_part(part)
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        # Plane-local: 0.1 wide along +x, 4.0 long along +y (after the 90° rot).
        # World mapping at heading 0°: plane +x → world +y, plane +y → world +x.
        # So in world: long axis (4.0) runs along +x, narrow (0.1) along +y.
        minx, miny, maxx, maxy = world.polygon.bounds
        assert _almost_equal(maxx - minx, 4.0, tol=1e-6), f"world x-extent: {maxx - minx}"
        assert _almost_equal(maxy - miny, 0.1, tol=1e-6), f"world y-extent: {maxy - miny}"

    def test_heading_minus_90(self) -> None:
        """At heading -90° (nose toward world -x, i.e. left wall), right-wingtip
        points toward world +y (deeper into hangar)."""
        ac = _aircraft_with_one_part(_point_part(offset_x_m=0.0, offset_y_m=1.0))
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=-90.0, on_carts=False)
        [world] = aircraft_parts_world(ac, pl)
        cx, cy = world.polygon.centroid.x, world.polygon.centroid.y
        assert _almost_equal(cx, 0.0, tol=1e-6)
        assert _almost_equal(cy, 1.0, tol=1e-6)


class TestWorldPartMetadata:
    """``aircraft_parts_world`` must preserve every part's metadata
    untouched — kind, z-range, and plane id."""

    def test_kind_z_range_and_plane_id_preserved(self) -> None:
        parts = (
            Part(
                kind="fuselage",
                length_m=7.0,
                width_m=0.8,
                offset_x_m=0,
                offset_y_m=0,
                angle_deg=0,
                z_bottom_m=0.0,
                z_top_m=1.5,
            ),
            Part(
                kind="wing",
                length_m=1.4,
                width_m=10.0,
                offset_x_m=1.0,
                offset_y_m=0,
                angle_deg=0,
                z_bottom_m=2.0,
                z_top_m=2.3,
            ),
            Part(
                kind="strut",
                length_m=0.05,
                width_m=1.5,
                offset_x_m=0.5,
                offset_y_m=1.0,
                angle_deg=0,
                z_bottom_m=0.5,
                z_top_m=2.0,
            ),
        )
        ac = Aircraft(
            id="multipart",
            name="Multipart",
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=parts,
        )
        pl = Placement(plane_id="multipart", x_m=5.0, y_m=10.0, heading_deg=37.0, on_carts=False)
        worlds = aircraft_parts_world(ac, pl)
        assert len(worlds) == 3
        # Order preserved; metadata preserved 1:1 from source parts.
        for src, dst in zip(parts, worlds):
            assert dst.kind == src.kind
            assert dst.z_bottom_m == src.z_bottom_m
            assert dst.z_top_m == src.z_top_m
            assert dst.plane_id == "multipart"


class TestAircraftPartsWorldOnRealAircraft:
    """End-to-end: a strut-braced aircraft loaded from the bundled fleet.yaml
    produces transformed parts whose footprints are inside the expected
    region of the world frame."""

    def test_husky_at_origin_heading_zero(self) -> None:
        """A Husky placed at (0, 0) heading 0° should have its fuselage
        roughly along world +y (nose deeper into hangar)."""
        from hangarfit.loader import load_fleet

        from pathlib import Path

        fleet = load_fleet(Path(__file__).resolve().parent.parent / "data" / "fleet.yaml")
        husky = fleet["aviat_husky"]
        pl = Placement(plane_id="aviat_husky", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
        worlds = aircraft_parts_world(husky, pl)
        # 4 parts: fuselage + wing + 2 struts
        kinds = [w.kind for w in worlds]
        assert kinds.count("fuselage") == 1
        assert kinds.count("wing") == 1
        assert kinds.count("strut") == 2
        # Fuselage at heading 0: long axis (length_m=7.0) runs along world y.
        fuselage = next(w for w in worlds if w.kind == "fuselage")
        minx, miny, maxx, maxy = fuselage.polygon.bounds
        # length 6.88 → spans ≈ 6.88m along y; width 0.85 → spans ≈ 0.85m along x.
        assert _almost_equal(maxy - miny, 6.88, tol=1e-6)
        assert _almost_equal(maxx - minx, 0.85, tol=1e-6)
        # Two struts are mirrored across plane-local +y=0; at heading 0,
        # plane +y maps to world +x, so the struts mirror across world x=0.
        struts = [w for w in worlds if w.kind == "strut"]
        strut_xs = sorted(s.polygon.centroid.x for s in struts)
        assert _almost_equal(strut_xs[0], -strut_xs[1], tol=1e-6), (
            f"struts should mirror across world x=0, got centroids at {strut_xs}"
        )
