"""Tests for ml.geometry_oracle and ml.types (#672)."""

from __future__ import annotations

from hangarfit.loader import load_fleet
from ml import geometry_oracle as go
from ml.types import Park, Primitive, RewardWeights
from tests.ml.conftest import single_object_layout


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
