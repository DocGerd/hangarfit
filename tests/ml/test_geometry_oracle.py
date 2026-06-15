"""Tests for ml.geometry_oracle and ml.types (#672)."""

from __future__ import annotations

from hangarfit.loader import load_fleet
from ml import geometry_oracle as go
from ml.types import Park, Primitive, RewardWeights
from tests.ml.conftest import single_object_layout, two_object_layout


def test_primitive_and_park_construct():
    p = Primitive(kind="S", magnitude=1.5, gear=1)
    assert p.kind == "S" and p.magnitude == 1.5 and p.gear == 1
    assert isinstance(Park(), Park)


def test_reward_weights_ordering_invariant_holds_by_default():
    w = RewardWeights()
    # Any hard weight must dominate the sum of achievable soft bonuses.
    assert min(w.w_col, w.w_oob, w.w_egress) > (w.w_gap + w.w_seq + w.w_region)


# ---------------------------------------------------------------------------
# T3: overlap_area_m2
# ---------------------------------------------------------------------------


def test_overlap_area_zero_for_valid_layout():
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    assert go.overlap_area_m2(layout) == 0.0


# ---------------------------------------------------------------------------
# T4: intrusion_area_m2
# ---------------------------------------------------------------------------


def test_intrusion_zero_when_inside():
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    pid = next(iter(layout.fleet))
    pl = layout.placements[0]
    assert go.intrusion_area_m2(layout.fleet[pid], pl, layout.hangar) == 0.0


def test_intrusion_positive_when_object_pushed_off_the_front():
    # y deep-negative drives the footprint out past the front wall (y<0 beyond apron).
    layout = single_object_layout(x_m=5.0, y_m=-50.0)
    pid = next(iter(layout.fleet))
    pl = layout.placements[0]
    assert go.intrusion_area_m2(layout.fleet[pid], pl, layout.hangar) > 0.0


# ---------------------------------------------------------------------------
# T5: legal_primitives
# ---------------------------------------------------------------------------


def test_legal_primitives_cart_includes_strafe():
    # scheibe_falke: always_cart, r=0 → lateral=True → T primitives included.
    fleet = load_fleet("data/fleet.yaml")
    body = fleet["scheibe_falke"]
    kinds = {p.kind for p in go.legal_primitives(body, on_carts=True)}
    assert "T" in kinds  # carts can strafe (#647)


def test_legal_primitives_own_gear_excludes_strafe():
    # fuji: always_own_gear, r=7.0 → lateral ignored → no T primitive.
    fleet = load_fleet("data/fleet.yaml")
    body = fleet["fuji"]
    kinds = {p.kind for p in go.legal_primitives(body, on_carts=False)}
    assert "T" not in kinds


# ---------------------------------------------------------------------------
# T6: apply_primitive
# ---------------------------------------------------------------------------


def test_apply_straight_moves_along_heading():
    from hangarfit.towplanner import Pose

    start = Pose(x_m=5.0, y_m=0.0, heading_deg=0.0)  # heading 0 = +y (into hangar)
    end, swept = go.apply_primitive(
        start, Primitive(kind="S", magnitude=2.0, gear=1), turn_radius_m=0.0
    )
    assert abs(end.x_m - 5.0) < 1e-9
    assert abs(end.y_m - 2.0) < 1e-6
    assert swept[0] == start and len(swept) >= 2


def test_apply_strafe_translates_sideways():
    from hangarfit.towplanner import Pose

    start = Pose(x_m=5.0, y_m=4.0, heading_deg=0.0)
    end, _ = go.apply_primitive(
        start, Primitive(kind="T", magnitude=1.0, gear=1), turn_radius_m=0.0
    )
    assert abs(end.y_m - 4.0) < 1e-6  # strafe keeps the along-heading coordinate
    assert abs(end.x_m - 5.0) > 0.5  # and moves perpendicular


# ---------------------------------------------------------------------------
# T7: swept_intrusion_m2
# ---------------------------------------------------------------------------


def test_swept_intrusion_zero_for_clear_move_in_empty_hangar():
    from hangarfit.towplanner import Pose

    layout = single_object_layout(x_m=5.0, y_m=8.0)  # one body; we move it, others empty
    body = layout.fleet[next(iter(layout.fleet))]
    start = Pose(x_m=5.0, y_m=8.0, heading_deg=0.0)
    _, swept = go.apply_primitive(
        start, Primitive(kind="S", magnitude=0.5, gear=1), turn_radius_m=0.0
    )
    intr = go.swept_intrusion_m2(
        body, swept, parked_layout=layout, active_id=next(iter(layout.fleet))
    )
    assert intr == 0.0


def test_swept_intrusion_positive_when_sweeping_into_a_parked_body():
    from hangarfit.towplanner import Pose

    # Park a fuji ahead; the active husky sweeps straight FORWARD into its footprint.
    layout, active, active_id = two_object_layout(parked_y_m=10.0, active_y_m=4.0)
    start = Pose(x_m=5.0, y_m=4.0, heading_deg=0.0)
    _, swept = go.apply_primitive(
        start, Primitive(kind="S", magnitude=6.0, gear=1), turn_radius_m=0.0
    )
    intr = go.swept_intrusion_m2(active, swept, parked_layout=layout, active_id=active_id)
    assert intr > 0.0  # the swept path overlaps the parked obstacle → graded penalty


# ---------------------------------------------------------------------------
# T8: movement_cost + egress_blocked
# ---------------------------------------------------------------------------


def test_movement_cost_adds_cusp_penalty_on_reversal():
    # Forward then reverse straight => one cusp.
    fwd = Primitive(kind="S", magnitude=1.0, gear=1)
    rev = Primitive(kind="S", magnitude=1.0, gear=-1)
    c_no_cusp = go.movement_cost(fwd, prev_gear=1, cusp_penalty=10.0)
    c_cusp = go.movement_cost(rev, prev_gear=1, cusp_penalty=10.0)
    assert c_cusp - c_no_cusp >= 10.0


def test_egress_blocked_false_without_hard_door_mover():
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    assert go.egress_blocked(layout) is False  # no hard-door mover present
