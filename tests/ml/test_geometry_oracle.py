"""Tests for ml.geometry_oracle and ml.types (#672)."""

from __future__ import annotations

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
