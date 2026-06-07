"""Closed-form Reeds–Shepp motion model (#261) — towplanner v2.

Reeds–Shepp extends Dubins (forward arc-line-arc) with **reverse** arcs and
straights, so a car can back up to reorient instead of driving a full
turning-circle loop. The primitive is still closed-form and deterministic, so
the ADR-0003 byte-identical-plan contract holds (ADR-0010 supersedes ADR-0007
fork-2 "Dubins-only").

The crux of correctness is the **integrator round-trip**: walking the signed,
geared segments :func:`plan_reeds_shepp` emits must reproduce the goal pose.
:func:`test_reeds_shepp_roundtrip_grid` is the primary oracle — it mirrors the
Dubins ``test_dubins_roundtrip_grid`` across a grid of poses and radii. The
heading-convention guard (ADR-0002: compass CW-positive vs. math CCW-positive)
is pinned by the reverse 45° canary in ``test_towplanner_dubins.py`` and by the
reverse-quadrant asserts here.
"""

from __future__ import annotations

import math

import pytest

from hangarfit.towplanner import (
    _REVERSE_COST_FACTOR,
    CUSP_PENALTY,
    Pose,
    Segment,
    _count_cusps,
    _wrap180,
    plan_reeds_shepp,
)


def _heading_close(a: float, b: float, tol: float = 0.5) -> bool:
    d = (a - b + 180.0) % 360.0 - 180.0
    return abs(d) <= tol


def test_wrap180_folds_to_half_open_interval() -> None:
    # Canonical interval is (-180, 180]: +180 stays +180, -180 maps to +180.
    assert _wrap180(0.0) == 0.0
    assert _wrap180(180.0) == 180.0
    assert _wrap180(181.0) == -179.0
    assert _wrap180(-180.0) == 180.0
    assert _wrap180(360.0) == 0.0
    assert _wrap180(540.0) == 180.0
    assert _wrap180(-90.0) == -90.0


# --- cusp counting (#480): travel-direction reversals among translating legs ---


def test_count_cusps_pure_forward_is_zero() -> None:
    assert _count_cusps([(1, True), (1, True), (1, True)]) == 0


def test_count_cusps_pure_reverse_is_zero() -> None:
    # A single-direction reverse drive is "one move", zero cusps.
    assert _count_cusps([(-1, True), (-1, True)]) == 0


def test_count_cusps_one_reversal() -> None:
    assert _count_cusps([(1, True), (-1, True)]) == 1


def test_count_cusps_forward_reverse_forward_is_two() -> None:
    assert _count_cusps([(1, True), (-1, True), (1, True)]) == 2


def test_count_cusps_excludes_nontranslating_pivots() -> None:
    # Cart: pivot (non-translating), reverse straight, pivot -> a single reverse
    # drive, 0 cusps. In-place pivots are free reorientations, not moves.
    assert _count_cusps([(1, False), (-1, True), (1, False)]) == 0


def test_count_cusps_empty_is_zero() -> None:
    assert _count_cusps([]) == 0


def test_cusp_penalty_is_positive_finite() -> None:
    # Large-but-finite (not lexicographic): a deterministic scalar (#480).
    assert 0.0 < CUSP_PENALTY < float("inf")


# --- Segment gear field -----------------------------------------------------


def test_segment_defaults_to_forward_gear() -> None:
    # Every existing Segment(...) call omits gear, so the default must be +1.
    seg = Segment("S", 3.0)
    assert seg.gear == 1


def test_segment_accepts_reverse_gear() -> None:
    seg = Segment("S", 3.0, gear=-1)
    assert seg.gear == -1


@pytest.mark.parametrize("bad_gear", [0, 2, -2, 100])
def test_segment_rejects_invalid_gear(bad_gear: int) -> None:
    with pytest.raises(ValueError):
        Segment("S", 3.0, gear=bad_gear)


# --- reverse-leg integration in pose_at -------------------------------------


def test_reverse_straight_drives_backwards() -> None:
    """A reverse straight from a pose heading +y must move toward −y.

    Built directly via DubinsArc so the integrator is exercised in isolation
    of word selection. heading 0 (compass) → forward +y; gear −1 ⇒ −y.
    """
    from hangarfit.towplanner import DubinsArc

    start = Pose(0.0, 5.0, 0.0)
    end = Pose(0.0, 2.0, 0.0)  # 3 m behind, same heading
    arc = DubinsArc(start, end, 4.0, (Segment("S", 3.0, gear=-1),))
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(0.0, abs=1e-9)
    assert last.y_m == pytest.approx(2.0, abs=1e-9)
    assert _heading_close(last.heading_deg, 0.0)


def test_reverse_arc_curves_opposite_to_forward_arc() -> None:
    """For a fixed steering kind ("L"), reversing flips the position update.

    A forward "L" leg and a reverse "L" leg of equal length sweep the heading
    the SAME way (steering is independent of gear) but translate to opposite
    sides — the round-trip grid is the exhaustive proof; this pins the sign in
    isolation.
    """
    from hangarfit.towplanner import DubinsArc

    start = Pose(0.0, 0.0, 0.0)
    fwd = DubinsArc(start, start, 2.0, (Segment("L", 1.0, gear=1),)).pose_at(1.0)
    rev = DubinsArc(start, start, 2.0, (Segment("L", 1.0, gear=-1),)).pose_at(1.0)
    # The turning CENTRE is set by steering alone (to the −x side for an
    # L-steered car heading +y), so it is the same for both gears. Forward
    # advances +y around it; reverse retreats −y around the same centre, so
    # the two endpoints are mirror images across the y = 0 line through the
    # start: same x, negated y. Heading sweeps the opposite way for reverse.
    assert not _heading_close(fwd.heading_deg, rev.heading_deg)
    assert fwd.x_m == pytest.approx(rev.x_m, abs=1e-9)
    assert fwd.y_m == pytest.approx(-rev.y_m, abs=1e-9)


def test_sample_density_unchanged_for_reverse_legs() -> None:
    """``sample()`` must use ``abs(length)`` so a reverse leg gets the same
    sample density as the equivalent forward leg (the #191 motion check relies
    on dense sampling regardless of travel direction)."""
    from hangarfit.towplanner import DubinsArc

    fwd = DubinsArc(Pose(0.0, 0.0, 0.0), Pose(0.0, 5.0, 0.0), 4.0, (Segment("S", 5.0, gear=1),))
    rev = DubinsArc(Pose(0.0, 5.0, 0.0), Pose(0.0, 0.0, 0.0), 4.0, (Segment("S", 5.0, gear=-1),))
    assert len(list(fwd.sample(step_m=0.5, step_deg=5.0))) == len(
        list(rev.sample(step_m=0.5, step_deg=5.0))
    )


# --- round-trip oracle (primary correctness check) --------------------------


@pytest.mark.parametrize("start_heading", [0.0, 90.0, 210.0])
@pytest.mark.parametrize(
    "goal",
    [(0.0, 6.0), (5.0, 5.0), (-5.0, 3.0), (6.0, 0.0), (-3.0, -4.0), (1.0, 0.5), (0.0, 0.0)],
)
@pytest.mark.parametrize("end_heading", [0.0, 45.0, 135.0, 270.0])
@pytest.mark.parametrize("radius", [1.0, 2.0, 5.0])
def test_reeds_shepp_roundtrip_grid(start_heading, goal, end_heading, radius) -> None:
    """The integrated endpoint of the RS path must reach the goal pose.

    This is the RS analog of ``test_dubins_roundtrip_grid`` and the primary
    correctness oracle: a transcription error in any generated word surfaces
    here as a missed endpoint. Spans forward + reverse words across a grid of
    start/end poses and three radii."""
    start = Pose(0.0, 0.0, start_heading)
    end = Pose(goal[0], goal[1], end_heading)
    arc = plan_reeds_shepp(start, end, turn_radius_m=radius)
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(end.x_m, abs=1e-3)
    assert last.y_m == pytest.approx(end.y_m, abs=1e-3)
    assert _heading_close(last.heading_deg, end.heading_deg)


# --- cost model: prefer forward --------------------------------------------


def test_reverse_cost_factor_value() -> None:
    # Pinned so a casual retune to 2.0 (which would suppress the measured
    # nose-out reverse win) trips this and forces an ADR update.
    assert _REVERSE_COST_FACTOR == 1.5


def test_rs_solve_normalised_cusp_penalty_never_increases_cusps() -> None:
    """#480 fewest-moves invariant: scoring is ``Σt + cusp_penalty_normalised *
    cusps``, so raising the cusp penalty must never INCREASE the chosen word's
    cusp count vs a zero penalty. Pins the new signature + the objective."""
    from hangarfit.towplanner import _rs_solve_normalised

    def _cusps(word: object) -> int:
        return _count_cusps([(e.gear, True) for e in word])  # type: ignore[attr-defined]

    goals = [
        (1.5, 0.8, math.radians(200.0)),
        (-1.2, 0.5, math.radians(160.0)),
        (0.5, -0.9, math.radians(170.0)),
        (2.0, -1.3, math.radians(135.0)),
    ]
    for x, y, phi in goals:
        zero = _rs_solve_normalised(x, y, phi, cusp_penalty_normalised=0.0)
        high = _rs_solve_normalised(x, y, phi, cusp_penalty_normalised=100.0)
        assert _cusps(high) <= _cusps(zero), f"goal {(x, y, phi)}: high-penalty word added cusps"


def test_seg_cost_reverse_not_taxed_per_metre() -> None:
    """#480: the Hybrid-A* search g-cost is gear-agnostic — a reverse leg costs
    the same per-metre as forward (direction changes are charged once as
    CUSP_PENALTY in the expansion loop, not per-metre here)."""
    from hangarfit.towplanner import _seg_cost

    fwd = Segment("S", 4.0, gear=1)
    rev = Segment("S", 4.0, gear=-1)
    assert _seg_cost(rev, 2.0) == _seg_cost(fwd, 2.0) == 4.0
    # A reverse arc costs the same as the forward arc (length + turn penalty).
    fwd_arc = Segment("L", 3.0, gear=1)
    rev_arc = Segment("L", 3.0, gear=-1)
    assert _seg_cost(rev_arc, 2.0) == _seg_cost(fwd_arc, 2.0)


def test_cart_seg_weight_reverse_straight_not_taxed_per_metre() -> None:
    """#480: a cart's reverse straight is no longer ×1.5 — both forward and
    reverse pivot-straight-pivot are single 0-cusp drives, so the choice reduces
    to length with forward as the tie-break. A pivot stays its radians."""
    from hangarfit.towplanner import _cart_seg_weight

    fwd_straight = Segment("S", 4.0, gear=1)
    rev_straight = Segment("S", 4.0, gear=-1)
    assert _cart_seg_weight(rev_straight) == _cart_seg_weight(fwd_straight) == 4.0
    pivot = Segment("L", 1.0)  # length_m is radians for a cart pivot
    assert _cart_seg_weight(pivot) == 1.0


def test_collinear_forward_goal_stays_pure_forward_straight() -> None:
    """When the goal is straight ahead on the same heading, RS must NOT pick a
    reverse maneuver — a forward "S" is both shortest (0 cusps, same length) and
    wins the forward-first tie-break (#480 cusp cost)."""
    arc = plan_reeds_shepp(Pose(0.0, 0.0, 0.0), Pose(0.0, 8.0, 0.0), turn_radius_m=4.0)
    assert all(s.gear == 1 for s in arc.segments)
    assert [s.kind for s in arc.segments] == ["S"]


def test_reverse_beats_forward_loop_for_short_backup() -> None:
    """The nose-out case: goal directly behind, same heading. A forward-only
    Dubins car must drive a full loop to get there; RS backs straight up. The
    chosen path must therefore contain a reverse leg and be far shorter than
    the forward-only loop."""
    from hangarfit.towplanner import plan_dubins

    start = Pose(0.0, 10.0, 0.0)
    end = Pose(0.0, 4.0, 0.0)  # 6 m directly behind, same heading
    rs = plan_reeds_shepp(start, end, turn_radius_m=2.0)
    dubins = plan_dubins(start, end, turn_radius_m=2.0)
    assert any(s.gear == -1 for s in rs.segments)
    # The straight back-up is 6 m, 0 cusps (#480: no per-metre reverse tax), so it
    # crushes the forward loop (≳ 4πr ≈ 25 m for a same-heading reversal at r = 2).
    assert rs.length_m < dubins.length_m


# --- reverse 45° canary (heading-convention sign guard) ---------------------


def test_reverse_straight_45_advances_into_minus_x_minus_y() -> None:
    """Backing straight while heading 45° (compass) must move into −x,−y.

    Forward heading-45 drives into (+x, +y) (see the Dubins canary); reverse is
    the exact negation. A CW/CCW sign flip in the adapter would send it into
    (−x, +y) or (+x, −y) — this asserts the correct reverse quadrant."""
    from hangarfit.towplanner import DubinsArc

    start = Pose(0.0, 0.0, 45.0)
    arc = DubinsArc(start, start, 4.0, (Segment("S", 2.0, gear=-1),))
    mid = list(arc.sample(step_m=0.25, step_deg=5.0))[1]
    assert mid.x_m < 0.0 and mid.y_m < 0.0


# --- cart r = 0 reverse straight --------------------------------------------


def test_cart_reverse_straight_backs_out() -> None:
    """A carted plane (r = 0) must be able to back straight out: goal directly
    behind, same heading ⇒ a single reverse "S" leg, no pivots."""
    start = Pose(5.0, 8.0, 0.0)
    end = Pose(5.0, 3.0, 0.0)  # 5 m straight behind
    arc = plan_reeds_shepp(start, end, turn_radius_m=0.0)
    assert arc.turn_radius_m == 0.0
    assert [s.kind for s in arc.segments] == ["S"]
    assert arc.segments[0].gear == -1
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(5.0, abs=1e-9)
    assert last.y_m == pytest.approx(3.0, abs=1e-9)
    assert _heading_close(last.heading_deg, 0.0)


def test_cart_prefers_forward_when_not_cheaper_to_reverse() -> None:
    """Mirror of :func:`test_cart_reverse_straight_backs_out`: a goal directly
    AHEAD (same heading) is a single FORWARD "S" leg. Backing in would cost a
    180° pivot + a reverse straight (×_REVERSE_COST_FACTOR) + a 180° pivot, so
    the forward option is strictly cheaper and `_plan_cart`'s `min((forward,
    reverse), …)` must keep it — pinning the prefer-forward tie-break (ADR-0003
    determinism: no gratuitous reverse)."""
    start = Pose(5.0, 3.0, 0.0)
    end = Pose(5.0, 8.0, 0.0)  # 5 m straight ahead (heading 0 ⇒ +y)
    arc = plan_reeds_shepp(start, end, turn_radius_m=0.0)
    assert arc.turn_radius_m == 0.0
    assert [s.kind for s in arc.segments] == ["S"]
    assert arc.segments[0].gear == 1  # forward, not a contrived reverse
    last = arc.pose_at(arc.length_m)
    assert last.x_m == pytest.approx(5.0, abs=1e-9)
    assert last.y_m == pytest.approx(8.0, abs=1e-9)
    assert _heading_close(last.heading_deg, 0.0)


def test_cart_pivot_in_place_still_works_via_reeds_shepp() -> None:
    """RS must degrade to the same pivot-in-place as Dubins for r = 0 when the
    positions coincide (delegation to the cart branch)."""
    arc = plan_reeds_shepp(Pose(1.0, 1.0, 0.0), Pose(1.0, 1.0, 90.0), turn_radius_m=0.0)
    for pose in arc.sample(step_m=0.05, step_deg=1.0):
        assert pose.x_m == pytest.approx(1.0)
        assert pose.y_m == pytest.approx(1.0)
    assert _heading_close(arc.pose_at(arc.length_m).heading_deg, 90.0)


@pytest.mark.parametrize("bad_radius", [-1.0, math.inf, math.nan])
def test_reeds_shepp_invalid_turn_radius_rejected(bad_radius: float) -> None:
    with pytest.raises(ValueError):
        plan_reeds_shepp(Pose(0.0, 0.0, 0.0), Pose(0.0, 5.0, 0.0), turn_radius_m=bad_radius)


# --- determinism (ADR-0003) -------------------------------------------------


def test_reeds_shepp_is_deterministic() -> None:
    """Same input → byte-identical segment decomposition (no RNG, fixed word
    order + strict-< tie-break)."""
    args = (Pose(0.0, 0.0, 30.0), Pose(4.0, -3.0, 200.0))
    a = plan_reeds_shepp(*args, turn_radius_m=3.0)
    b = plan_reeds_shepp(*args, turn_radius_m=3.0)
    assert a.segments == b.segments
