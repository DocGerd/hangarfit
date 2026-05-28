"""Closed-form Dubins arc primitive (#189) — analytic matrix + 45° canary.

The crux of this suite is the heading-convention guard. Dubins literature
uses CCW-positive radians measured from ``+x``; hangarfit headings are
compass-style (CW-positive, from world ``+y``) and the plane-local→world
transform has determinant ``−1`` (ADR-0002). The axis-aligned cases
(0/90/180/270) pass even a sign-flipped adapter because ``sin45 == cos45``;
the 45° canary is what actually pins the convention end-to-end.
"""

import math

import pytest

from hangarfit.geometry import aircraft_parts_world
from hangarfit.models import Aircraft, Part, Placement
from hangarfit.towplanner import Pose, _dubins_shortest, compass_to_math_rad, plan_dubins


def _heading_close(a: float, b: float, tol: float = 0.5) -> bool:
    """True if compass headings ``a`` and ``b`` agree on the shorter arc."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return abs(d) <= tol


def _canary_aircraft(*, offset_y_m: float, turn_radius_m: float = 5.0) -> Aircraft:
    """A single point-part aircraft, modelled on tests/test_geometry.py.

    ``offset_y_m`` places the part on the plane-local right side so the
    det−1 transform can be observed (right wingtip → world (+x, −y)).
    """
    part = Part(
        kind="fuselage_aft",
        length_m=0.001,
        width_m=0.001,
        offset_x_m=0.0,
        offset_y_m=offset_y_m,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )
    return Aircraft(
        id="CANARY",
        name="Canary",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=turn_radius_m,
        measured=False,
        parts=(part,),
    )


# --- convention adapter -----------------------------------------------------


def test_compass_to_math_rad_cardinals() -> None:
    # heading 0 (nose to +y) -> math angle 90deg; heading 90 (+x) -> 0deg.
    assert math.degrees(compass_to_math_rad(0.0)) == pytest.approx(90.0)
    assert math.degrees(compass_to_math_rad(90.0)) == pytest.approx(0.0)
    # 45 and 135 break the sign-flip symmetry the cardinals hide.
    assert math.degrees(compass_to_math_rad(45.0)) == pytest.approx(45.0)
    assert math.degrees(compass_to_math_rad(135.0)) == pytest.approx(-45.0)


# --- straight-line + sampler ------------------------------------------------


def test_straight_line_path_is_pure_S() -> None:
    # Start at origin heading +y (0deg); goal 10m ahead, same heading.
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(0.0, 10.0, 0.0)
    arc = plan_dubins(start, end, turn_radius_m=5.0)
    assert [s.kind for s in arc.segments] == ["S"]
    assert arc.length_m == pytest.approx(10.0)


def test_sample_walks_from_start_to_end() -> None:
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(0.0, 10.0, 0.0)
    arc = plan_dubins(start, end, turn_radius_m=5.0)
    poses = list(arc.sample(step_m=1.0, step_deg=10.0))
    assert poses[0] == start
    assert poses[-1].x_m == pytest.approx(end.x_m, abs=1e-6)
    assert poses[-1].y_m == pytest.approx(end.y_m, abs=1e-6)
    # Monotone advance along +y for a pure straight northbound leg.
    ys = [p.y_m for p in poses]
    assert ys == sorted(ys)


# --- 45° canaries (the convention guard) ------------------------------------


def test_heading_45_path_advances_into_plus_x_plus_y() -> None:
    # heading 45deg compass => forward unit vector (sin45, cos45) ~ (0.707, 0.707).
    start = Pose(0.0, 0.0, 45.0)
    fwd = (math.sin(math.radians(45.0)), math.cos(math.radians(45.0)))
    end = Pose(fwd[0], fwd[1], 45.0)
    arc = plan_dubins(start, end, turn_radius_m=5.0)
    assert [s.kind for s in arc.segments] == ["S"]
    mid = list(arc.sample(step_m=0.25, step_deg=5.0))[1]
    # A sign-flipped convention (theta = heading-90 instead of 90-heading)
    # would drive the path into +x,-y. Assert the correct quadrant.
    assert mid.x_m > 0.0 and mid.y_m > 0.0


def test_heading_45_reverse_path_advances_into_minus_x_minus_y() -> None:
    """🔬 Reverse 45° canary (ADR-0010, towplanner v2 Reeds–Shepp).

    Forward heading-45 drives into (+x, +y) (the test above). A reverse leg is
    the exact negation: backing while heading 45° must move into (−x, −y). A
    CW/CCW sign flip in :func:`compass_to_math_rad` would send the reverse leg
    into the wrong quadrant — the symmetric word matrix would pass it silently,
    so this geometric assert is the real guard for the reverse direction."""
    from hangarfit.towplanner import DubinsArc, Segment

    start = Pose(0.0, 0.0, 45.0)
    arc = DubinsArc(start, start, 5.0, (Segment("S", 2.0, gear=-1),))
    mid = list(arc.sample(step_m=0.25, step_deg=5.0))[1]
    assert mid.x_m < 0.0 and mid.y_m < 0.0


def test_pose_heading_45_right_wingtip_lands_in_plus_x_minus_y() -> None:
    """🔬 The definitive det−1 guard, driven through a planner Pose.

    Mirrors tests/test_geometry.py::test_heading_45_right_wingtip_in_plus_x_minus_y_quadrant
    but feeds the heading through a towplanner ``Pose`` to prove the planner
    consumes ADR-0002's convention identically to the collision checker. A
    pure-rotation (det+1) transform would land the right wingtip in the
    upper-left (−x, +y) quadrant instead.
    """
    ac = _canary_aircraft(offset_y_m=1.0)
    pose = Pose(0.0, 0.0, 45.0)
    [wp] = aircraft_parts_world(
        ac, Placement(ac.id, pose.x_m, pose.y_m, pose.heading_deg, on_carts=False)
    )
    cx, cy = wp.polygon.centroid.x, wp.polygon.centroid.y
    assert cx > 0.0, f"right wingtip x expected > 0 (toward +x), got {cx}"
    assert cy < 0.0, f"right wingtip y expected < 0 (toward door), got {cy}"


# --- analytic Dubins matrix -------------------------------------------------


@pytest.mark.parametrize(
    "start,end,radius,expect_words",
    [
        # Pure straight (collinear, same heading): S only.
        (Pose(0.0, 0.0, 0.0), Pose(0.0, 8.0, 0.0), 4.0, [["S"]]),
        # 90deg turn then run — assert feasible & ends correctly (any word).
        (Pose(0.0, 0.0, 0.0), Pose(4.0, 4.0, 90.0), 4.0, None),
        # U-turn in place geometry (CCC family RLR/LRL can appear at short d).
        (Pose(0.0, 0.0, 0.0), Pose(0.0, 0.0, 180.0), 2.0, None),
    ],
)
def test_dubins_endpoints_match(start, end, radius, expect_words) -> None:
    arc = plan_dubins(start, end, turn_radius_m=radius)
    # Assert the INTEGRATED endpoint (walking the segments), NOT arc.end —
    # arc.end stores the input goal verbatim, so comparing to it is a
    # tautology that would pass a wrong closed form. pose_at(length) only
    # matches `end` if plan_dubins produced correct segment lengths.
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(end.x_m, abs=1e-3)
    assert last.y_m == pytest.approx(end.y_m, abs=1e-3)
    assert _heading_close(last.heading_deg, end.heading_deg)
    if expect_words is not None:
        assert [s.kind for s in arc.segments] in expect_words


@pytest.mark.parametrize("start_heading", [0.0, 90.0, 210.0])
@pytest.mark.parametrize(
    "goal",
    [(0.0, 6.0), (5.0, 5.0), (-5.0, 3.0), (6.0, 0.0), (-3.0, -4.0), (1.0, 0.5)],
)
@pytest.mark.parametrize("end_heading", [0.0, 45.0, 135.0, 270.0])
def test_dubins_roundtrip_grid(start_heading, goal, end_heading) -> None:
    """Over a deterministic grid, the integrated endpoint must reach the goal
    pose. Diverse configurations exercise all six Dubins words, so a
    transcription typo in any single word (LSR/RSL/LRL are not hit by the
    explicit matrix above) surfaces as a missed endpoint here."""
    start = Pose(0.0, 0.0, start_heading)
    end = Pose(goal[0], goal[1], end_heading)
    arc = plan_dubins(start, end, turn_radius_m=2.0)
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(end.x_m, abs=1e-3)
    assert last.y_m == pytest.approx(end.y_m, abs=1e-3)
    assert _heading_close(last.heading_deg, end.heading_deg)


def test_zero_radius_is_pivot_in_place() -> None:
    # Cart pivot: same position, heading change only -> all turn, no translation.
    start = Pose(1.0, 1.0, 0.0)
    end = Pose(1.0, 1.0, 90.0)
    arc = plan_dubins(start, end, turn_radius_m=0.0)
    for pose in arc.sample(step_m=0.05, step_deg=1.0):
        assert pose.x_m == pytest.approx(1.0)
        assert pose.y_m == pytest.approx(1.0)
    # The pivot must actually reach the target heading (pins the L/R sign).
    assert _heading_close(arc.pose_at(arc.length_m).heading_deg, 90.0)


def test_zero_radius_sampler_density_tracks_heading_sweep() -> None:
    """A pivot has zero translation, so its sample density must come from the
    angular sweep (step_deg), not step_m. Without that branch the pivot would
    yield only [start, end] (n=1) and the #191 motion check would skip the
    swept volume of an in-place turn entirely."""
    arc = plan_dubins(Pose(1.0, 1.0, 0.0), Pose(1.0, 1.0, 90.0), turn_radius_m=0.0)
    # 90deg sweep at 1deg resolution -> ceil(90/1) intervals -> 91 poses.
    poses = list(arc.sample(step_m=0.05, step_deg=1.0))
    assert len(poses) == 91
    # And the heading must advance monotonically through the sweep, not jump.
    headings = [p.heading_deg for p in poses]
    assert headings == sorted(headings)
    assert headings[0] == pytest.approx(0.0)
    assert _heading_close(headings[-1], 90.0)


def test_zero_radius_pivot_left_for_negative_delta() -> None:
    """A negative compass delta pivots the other way (the ``L`` branch),
    complementing the +90 (``R``) case above. Pins the L sign at the
    plan_dubins level, not just inside pose_at."""
    arc = plan_dubins(Pose(2.0, 3.0, 90.0), Pose(2.0, 3.0, 0.0), turn_radius_m=0.0)
    assert [s.kind for s in arc.segments] == ["L"]
    assert arc.segments[0].length_m == pytest.approx(math.radians(90.0))
    assert _heading_close(arc.pose_at(arc.length_m).heading_deg, 0.0)


def test_zero_radius_pivot_180_resolves_to_short_arc() -> None:
    """A 180deg pivot is the sign boundary: (180+180)%360-180 == -180, so it
    takes the ``L`` branch and the >=0 'positive->R' rule never sees +180.
    Either direction is geometrically valid; pin that it lands correctly and
    deterministically as a single half-turn."""
    arc = plan_dubins(Pose(0.0, 0.0, 0.0), Pose(0.0, 0.0, 180.0), turn_radius_m=0.0)
    assert [s.kind for s in arc.segments] == ["L"]
    assert arc.segments[0].length_m == pytest.approx(math.pi)
    assert _heading_close(arc.pose_at(arc.length_m).heading_deg, 180.0)


def test_collinear_tiebreak_prefers_earliest_listed_word() -> None:
    """LSL/RSR/LSR/RSL all cost exactly ``d`` on a collinear same-heading path,
    so the deterministic tie-break (fixed word order + strict ``<``) must
    return the earliest-listed word, LSL. plan_dubins collapses this to
    ("S",), masking the choice — assert on the internal so the ADR-0003
    determinism invariant is queryable and a ``<`` -> ``<=`` flip fails."""
    word, _ = _dubins_shortest(Pose(0.0, 0.0, 0.0), Pose(0.0, 8.0, 0.0), 4.0)
    assert word == ("L", "S", "L")


def test_zero_radius_translation_is_pivot_straight_pivot() -> None:
    # Cart from origin facing +y, goal 5 m east facing +x. The faithful r->0
    # Dubins limit is: pivot to the goal bearing (+x = compass 90), drive
    # straight 5 m, then (final heading already 90) no third pivot. ADR-0007
    # models a cart as own-gear with turn_radius_m == 0, so a moved goal is a
    # legitimate pivot-straight-pivot path, NOT a caller error.
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(5.0, 0.0, 90.0)
    arc = plan_dubins(start, end, turn_radius_m=0.0)
    assert arc.turn_radius_m == 0.0
    kinds = [s.kind for s in arc.segments]
    assert kinds[0] in ("L", "R")  # initial pivot to bearing
    assert "S" in kinds  # straight leg
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(5.0, abs=1e-6)
    assert last.y_m == pytest.approx(0.0, abs=1e-6)
    assert _heading_close(last.heading_deg, 90.0)


def test_zero_radius_translation_collinear_is_pure_straight() -> None:
    # Already facing the goal and ending on the same heading: no pivots at all.
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(0.0, 5.0, 0.0)
    arc = plan_dubins(start, end, turn_radius_m=0.0)
    assert [s.kind for s in arc.segments] == ["S"]
    assert arc.length_m == pytest.approx(5.0)


def test_zero_radius_translation_with_final_pivot() -> None:
    # Goal off-axis AND a final heading change -> all three legs present.
    start = Pose(0.0, 0.0, 0.0)
    end = Pose(3.0, 4.0, 200.0)
    arc = plan_dubins(start, end, turn_radius_m=0.0)
    assert len(arc.segments) == 3
    assert [s.kind for s in arc.segments][1] == "S"
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(3.0, abs=1e-6)
    assert last.y_m == pytest.approx(4.0, abs=1e-6)
    assert _heading_close(last.heading_deg, 200.0)


@pytest.mark.parametrize("bad_radius", [-1.0, math.inf, math.nan])
def test_invalid_turn_radius_rejected(bad_radius: float) -> None:
    # plan_dubins requires a finite, non-negative radius (negative is
    # nonsensical; inf/nan would silently corrupt the closed form).
    with pytest.raises(ValueError):
        plan_dubins(Pose(0.0, 0.0, 0.0), Pose(0.0, 5.0, 0.0), turn_radius_m=bad_radius)
