"""Tests for ml.geometry_oracle and ml.types (#672)."""

from __future__ import annotations

from ml.types import Park, Primitive, RewardWeights


def test_primitive_and_park_construct():
    p = Primitive(kind="S", magnitude=1.5, gear=1)
    assert p.kind == "S" and p.magnitude == 1.5 and p.gear == 1
    assert isinstance(Park(), Park)


def test_reward_weights_ordering_invariant_holds_by_default():
    w = RewardWeights()
    # Any hard weight must dominate the sum of achievable soft bonuses.
    assert min(w.w_col, w.w_oob, w.w_egress) > (w.w_gap + w.w_seq + w.w_region)
