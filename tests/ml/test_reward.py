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
    # park_valid defaults None (not a Park step) and r_valid_park defaults 0.0 -> no change.
    ctx = _ctx()
    assert step_reward(ctx, RewardWeights()) == step_reward(ctx, RewardWeights(r_valid_park=0.0))


def test_r_valid_park_paid_only_when_park_valid():
    w = RewardWeights(r_valid_park=2.0)
    valid = _ctx(park_valid=True)
    invalid = _ctx(park_valid=False)
    assert step_reward(valid, w) - step_reward(invalid, w) == pytest.approx(2.0)


def test_r_valid_park_cannot_make_an_overlapping_park_profitable():
    # An overlapping invalid Park (park_valid=False, overlap>0) must yield a LOWER step_reward
    # than a clean valid Park (park_valid=True, no overlap) with the same r_valid_park weight.
    w = RewardWeights(r_valid_park=2.0)
    overlapping_invalid = _ctx(overlap_m2=0.05, park_valid=False)
    clean_valid = _ctx(overlap_m2=0.0, park_valid=True)
    assert step_reward(overlapping_invalid, w) < step_reward(clean_valid, w)


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


# ---------------------------------------------------------------------------
# Task 3 — dense_slot_potential reward effect (Step 12)
# ---------------------------------------------------------------------------


def test_dense_slot_potential_on_lowers_potential_when_misfit_positive():
    """dense_slot_potential=True must STRICTLY lower the shaping potential vs False when the
    active object sits in a bad pose (active_misfit_m2 > 0), catching a gate-inversion the
    pure helper-level potential() test would miss (the env wires it via _potential()).

    NOTE: active_misfit_m2 EXCLUDES the apron (y<0) — misfit comes only from in-hangar
    (y>=0) out-of-floor (wall/notch) intrusion, so we place the object inside the y-band but
    poking the left wall (x<0), and assert that pose has positive misfit before comparing."""
    from ml import geometry_oracle as go
    from ml.env import HangarFitEnv
    from ml.types import Pose
    from tests.ml.conftest import _fuji, empty_hangar, single_object_layout

    fleet = _fuji()
    hangar = empty_hangar()
    # In the hangar y-band (y>=0) but straddling the left wall (x<0) -> out-of-floor area > 0.
    # A far-away parked fuji stands in for "some obstacle layout"; it does not overlap bad_pose,
    # so the misfit here is pure in-hangar wall intrusion (matching the env's empty _layout()).
    bad_pose = Pose(x_m=-3.0, y_m=10.0, heading_deg=0.0)
    obstacle_layout = single_object_layout(x_m=15.0, y_m=25.0)
    assert go.active_misfit_m2(fleet["fuji"], bad_pose, obstacle_layout, hangar) > 0.0, (
        "fixture must put the active object in a positive-misfit pose"
    )

    def potential_at(dense: bool) -> float:
        env = HangarFitEnv(
            hangar=hangar,
            fleet=fleet,
            requested_ids=("fuji",),
            weights=RewardWeights(dense_slot_potential=dense),
        )
        env.reset()
        env._active_pose = bad_pose  # override the apron spawn with the bad in-hangar pose
        return env._potential()

    # Only the misfit term differs between the two (same dist-to-slot, unplaced, overlap),
    # so the knob-on potential must be STRICTLY lower by exactly the misfit penalty.
    assert potential_at(dense=True) < potential_at(dense=False)
