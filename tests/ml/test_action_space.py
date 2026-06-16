"""Tests for the pure discrete action-space contract (ml/action_space.py)."""

from __future__ import annotations

import math

from ml import action_space
from ml.action_space import MAGNITUDE_DIM, PIVOT_BINS_DEG, TRANSLATION_BINS, decode
from ml.encoding import _CANONICAL_ACTIONS, ACTION_DIM, PARK_INDEX
from ml.types import Park, Primitive


def test_bins_and_dim():
    assert MAGNITUDE_DIM == len(TRANSLATION_BINS) == len(PIVOT_BINS_DEG) == 5
    assert TRANSLATION_BINS == (0.25, 0.5, 1.0, 2.0, 4.0)


def test_reuses_encoding_canonical_order():
    # single source of truth: action_space must reuse encoding's constants
    assert action_space._CANONICAL_ACTIONS is _CANONICAL_ACTIONS
    assert action_space.ACTION_DIM == ACTION_DIM == 9
    assert action_space.PARK_INDEX == PARK_INDEX == 8


def test_decode_park_ignores_bin():
    for b in range(MAGNITUDE_DIM):
        assert decode(PARK_INDEX, b, turn_radius_m=0.0) == Park()


def test_decode_cart_pivot_is_radians():
    # ('L', 1) is index 0; on a cart (turn_radius 0) the magnitude is radians of pivot
    act = decode(0, 2, turn_radius_m=0.0)
    assert isinstance(act, Primitive)
    assert act.kind == "L" and act.gear == 1
    assert math.isclose(act.magnitude, math.radians(PIVOT_BINS_DEG[2]))


def test_decode_owngear_arc_is_metres():
    # ('R', 1) is index 2; own-gear (turn_radius > 0) -> arc length in metres
    act = decode(2, 3, turn_radius_m=8.0)
    assert isinstance(act, Primitive)
    assert act.kind == "R" and act.gear == 1
    assert act.magnitude == TRANSLATION_BINS[3]


def test_decode_straight_and_strafe_are_metres():
    s = decode(1, 1, turn_radius_m=0.0)  # ('S', 1)
    assert isinstance(s, Primitive) and s.kind == "S" and s.magnitude == TRANSLATION_BINS[1]
    # 'T' (strafe) is index 6; always metres
    t = decode(6, 4, turn_radius_m=0.0)
    assert isinstance(t, Primitive) and t.kind == "T" and t.magnitude == TRANSLATION_BINS[4]
