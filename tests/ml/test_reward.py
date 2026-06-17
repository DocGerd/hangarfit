"""Tests for ml.reward (#672) — the graded-lexicographic reward + shaping."""

from __future__ import annotations

import pytest

from ml.reward import RewardContext, potential, step_reward
from ml.types import RewardWeights


def _ctx(**kw):
    base = dict(
        overlap_m2=0.0,
        intrusion_m2=0.0,
        swept_intrusion_m2=0.0,
        egress_blocked=False,
        move_cost=0.0,
        min_gap_m=0.0,
        seq_deviation=0.0,
        region_match=0.0,
        prev_potential=0.0,
        potential=0.0,
        terminal_fraction=None,
    )
    base.update(kw)
    return RewardContext(**base)


def test_hard_violation_outweighs_any_soft_bonus():
    w = RewardWeights()
    # Max achievable soft bonus (generous gap/seq/region) vs a tiny 0.5 m² overlap.
    soft = step_reward(_ctx(min_gap_m=5.0, seq_deviation=0.0, region_match=1.0), w)
    hard = step_reward(_ctx(overlap_m2=0.5, min_gap_m=5.0, region_match=1.0), w)
    assert hard < soft  # any overlap drops the score below the clean-but-spread one


def test_terminal_fraction_rewards_more_placed():
    w = RewardWeights()
    half = step_reward(_ctx(terminal_fraction=0.5), w)
    full = step_reward(_ctx(terminal_fraction=1.0), w)
    assert full > half


# ---------------------------------------------------------------------------
# Task 3 — r_valid_park bonus (Step 1)
# ---------------------------------------------------------------------------


def test_r_valid_park_default_zero_is_byte_identical():
    # park_valid defaults False and r_valid_park defaults 0.0 -> no change.
    ctx = _ctx()
    assert step_reward(ctx, RewardWeights()) == step_reward(ctx, RewardWeights(r_valid_park=0.0))


def test_r_valid_park_paid_only_when_park_valid():
    w = RewardWeights(r_valid_park=2.0)
    valid = _ctx(park_valid=True)
    invalid = _ctx(park_valid=False)
    assert step_reward(valid, w) - step_reward(invalid, w) == pytest.approx(2.0)


def test_r_valid_park_cannot_make_an_overlapping_park_profitable():
    # Ordering invariant: bonus << w_col * smallest meaningful overlap, and only paid on valid.
    w = RewardWeights(r_valid_park=2.0)
    overlapping = _ctx(overlap_m2=0.05, park_valid=False)  # invalid -> no bonus, big penalty
    assert step_reward(overlapping, w) < 0.0
    assert w.r_valid_park < w.w_col * 0.05


# ---------------------------------------------------------------------------
# Task 3 — dense_slot_potential / active_misfit_m2 in potential() (Step 10)
# ---------------------------------------------------------------------------


def test_potential_active_misfit_default_zero_is_byte_identical():
    a = potential(remaining_overlap_m2=1.0, active_dist_to_slot_m=2.0, unplaced=3)
    b = potential(
        remaining_overlap_m2=1.0, active_dist_to_slot_m=2.0, unplaced=3, active_misfit_m2=0.0
    )
    assert a == b


def test_potential_active_misfit_lowers_potential():
    base = potential(remaining_overlap_m2=0.0, active_dist_to_slot_m=0.0, unplaced=0)
    worse = potential(
        remaining_overlap_m2=0.0, active_dist_to_slot_m=0.0, unplaced=0, active_misfit_m2=5.0
    )
    assert worse < base  # higher misfit -> lower potential (Φ is negative cost)
