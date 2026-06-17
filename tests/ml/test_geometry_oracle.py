"""Tests for ml.geometry_oracle and ml.types (#672)."""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely.geometry import box

from hangarfit.collisions import check
from hangarfit.geometry import aircraft_parts_world
from hangarfit.loader import load_fleet, load_layout
from hangarfit.models import Placement
from ml import geometry_oracle as go
from ml.types import Park, Primitive, RewardWeights
from tests.ml.conftest import _fuji, empty_hangar, single_object_layout, two_object_layout

_ROOT = Path(__file__).resolve().parents[2]  # repo root


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


# ---------------------------------------------------------------------------
# T9: intrusion_area_m2 bay gate (#694)
# ---------------------------------------------------------------------------


def _bay_area_for(body, placement, hangar):
    bay = hangar.maintenance_bay
    bay_poly = box(
        bay.center_x_m - bay.width_m / 2,
        hangar.length_m - bay.depth_m,
        bay.center_x_m + bay.width_m / 2,
        hangar.length_m,
    )
    return sum(
        wp.polygon.intersection(bay_poly).area for wp in aircraft_parts_world(body, placement)
    )


def test_intrusion_bay_term_gated_on_bay_closed():
    fleet = _fuji()
    hangar = empty_hangar()
    body = fleet["fuji"]
    bay = hangar.maintenance_bay
    # Park inside the bay rectangle (centroid at the bay centre, one body-length in).
    pl = Placement(
        plane_id="fuji",
        x_m=bay.center_x_m,
        y_m=hangar.length_m - bay.depth_m / 2,
        heading_deg=0.0,
        on_carts=False,
    )
    overlap_area = _bay_area_for(body, pl, hangar)
    assert overlap_area > 0.0, "fixture must actually clip the bay; adjust y if not"
    open_intr = go.intrusion_area_m2(body, pl, hangar, bay_closed=False)
    closed_intr = go.intrusion_area_m2(body, pl, hangar, bay_closed=True)
    assert closed_intr - open_intr == pytest.approx(overlap_area, abs=1e-6)


# ---------------------------------------------------------------------------
# T10: layout_valid + #694 regression
# ---------------------------------------------------------------------------


def test_layout_valid_matches_product_checker_plus_egress():
    layout = single_object_layout(x_m=5.0, y_m=5.0)  # a clean, valid placement
    assert go.layout_valid(layout) == (check(layout).valid and not go.egress_blocked(layout))
    assert go.layout_valid(layout) is True


def test_layout_full_witness_is_valid_694_regression():
    # The committed herrenteich_full witness was WRONGLY rejected by the old env oracle
    # (inert maintenance bay over-enforced, #694). It is valid per the product checker.
    layout = load_layout(str(_ROOT / "examples/herrenteich/layout_full.yaml"))
    assert check(layout).valid, "precondition: witness valid per collisions.check"
    assert go.layout_valid(layout) is True


# ---------------------------------------------------------------------------
# Task 3 — active_misfit_m2 (Step 6)
# ---------------------------------------------------------------------------


def test_active_misfit_zero_in_clean_pocket_and_positive_when_overlapping():
    from ml.types import Pose

    fleet = _fuji()
    hangar = empty_hangar()
    body = fleet["fuji"]
    empty = single_object_layout(x_m=5.0, y_m=5.0)  # one parked body near (5,5)
    # Clean pocket far from the parked body, well inside the floor -> misfit 0.
    clean = Pose(x_m=14.0, y_m=20.0, heading_deg=0.0)
    assert go.active_misfit_m2(body, clean, empty, hangar) == pytest.approx(0.0, abs=1e-9)
    # Right on top of the parked body -> positive misfit.
    on_top = Pose(x_m=5.0, y_m=5.0, heading_deg=0.0)
    assert go.active_misfit_m2(body, on_top, empty, hangar) > 0.0


def test_active_misfit_never_invokes_search(monkeypatch):
    import hangarfit.solver as solver
    from ml.types import Pose

    monkeypatch.setattr(
        solver,
        "solve",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("active_misfit_m2 must not call solve()")
        ),
    )
    fleet = _fuji()
    go.active_misfit_m2(
        fleet["fuji"],
        Pose(x_m=5.0, y_m=5.0, heading_deg=0.0),
        single_object_layout(x_m=12.0, y_m=20.0),
        empty_hangar(),
    )
