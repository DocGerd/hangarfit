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
# #720 (L5) — graded valid_park: partial credit for a NEAR-valid Park so there is
# an uphill gradient INTO the witness slot instead of a flat plateau-then-spike.
# ---------------------------------------------------------------------------


def test_valid_park_grade_scale_default_zero_is_byte_identical():
    # The graded knob defaults 0.0 -> the existing binary r_valid_park path, byte-identical
    # across every park_valid state (None = not a Park step, True = valid, False = invalid).
    for pv in (None, True, False):
        ctx = _ctx(overlap_m2=0.03, park_valid=pv)
        assert step_reward(ctx, RewardWeights(r_valid_park=5.0)) == step_reward(
            ctx, RewardWeights(r_valid_park=5.0, valid_park_grade_scale=0.0)
        )


def test_graded_valid_park_full_credit_at_zero_misfit():
    # A clean valid Park (overlap 0, in bounds, egress clear) pays the FULL r_valid_park even
    # graded — exp(-0/scale) == 1 — so a valid Park is never worse off than under the binary form.
    w = RewardWeights(r_valid_park=10.0, valid_park_grade_scale=4.0, w_col=0.0)
    ctx = _ctx(overlap_m2=0.0, park_valid=True)
    assert step_reward(ctx, w) == pytest.approx(10.0)


def test_graded_valid_park_decreasing_in_overlap():
    # The whole point: with the grade on, a SMALLER near-miss overlap earns MORE valid-park
    # credit than a larger one (an uphill gradient toward the valid pose). w_col=0 isolates the
    # graded term from the collision penalty so the comparison is purely the bonus.
    w = RewardWeights(r_valid_park=10.0, valid_park_grade_scale=4.0, w_col=0.0)
    near = _ctx(overlap_m2=0.5, park_valid=False)
    far = _ctx(overlap_m2=3.0, park_valid=False)
    nonpark = _ctx(overlap_m2=0.5, park_valid=None)
    assert step_reward(near, w) > step_reward(far, w) > step_reward(nonpark, w)


def test_graded_valid_park_withheld_when_egress_blocked():
    # Egress is a BINARY hard failure with no "near" — a collision-clean but egress-blocked Park
    # must earn NO graded credit despite zero overlap, else it looks as good as a valid Park.
    w = RewardWeights(r_valid_park=10.0, valid_park_grade_scale=4.0, w_col=0.0, w_egress=0.0)
    blocked = _ctx(overlap_m2=0.0, park_valid=False, egress_blocked=True)
    assert step_reward(blocked, w) == pytest.approx(0.0)


def test_graded_valid_park_not_paid_on_nonpark_step():
    # park_valid None (a movement primitive, not a Park) earns 0 regardless of the grade scale.
    w = RewardWeights(r_valid_park=10.0, valid_park_grade_scale=4.0, w_col=0.0)
    assert step_reward(_ctx(park_valid=None), w) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# #720 (L5) — one-time first-valid bonus: a discrete positive kick the FIRST time an
# episode reaches a valid placement, so the breakthrough from the place-nothing pole pays
# a learnable return. The env flips ctx.first_valid_now True on exactly that one Park step.
# ---------------------------------------------------------------------------


def test_r_first_valid_default_zero_is_byte_identical():
    # The bonus knob defaults 0.0 -> the first_valid_now flag is never consulted, byte-identical.
    for fv in (False, True):
        ctx = _ctx(park_valid=True, first_valid_now=fv)
        assert step_reward(ctx, RewardWeights(r_valid_park=3.0)) == step_reward(
            ctx, RewardWeights(r_valid_park=3.0, r_first_valid=0.0)
        )


def test_r_first_valid_paid_when_flag_set():
    # On the flagged step the bonus is added on top of every other term.
    w = RewardWeights(r_first_valid=7.0)
    on = _ctx(park_valid=True, first_valid_now=True)
    off = _ctx(park_valid=True, first_valid_now=False)
    assert step_reward(on, w) - step_reward(off, w) == pytest.approx(7.0)


def test_r_first_valid_not_paid_when_flag_unset():
    # Default flag False (every step the env does not mark) earns no bonus.
    w = RewardWeights(r_first_valid=7.0)
    assert step_reward(_ctx(park_valid=True), w) == step_reward(
        _ctx(park_valid=True), RewardWeights(r_first_valid=0.0)
    )


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


def test_r_unplaced_penalty_default_zero_is_byte_identical():
    # The economics-rebalance knob defaults 0.0 -> terminal reward is unchanged.
    ctx = _ctx(terminal_fraction=0.5)
    assert step_reward(ctx, RewardWeights()) == step_reward(
        ctx, RewardWeights(r_unplaced_penalty=0.0)
    )


def test_r_unplaced_penalty_charges_unplaced_fraction():
    # terminal_fraction=0.5 -> unplaced fraction (1-0.5)=0.5 -> penalty = 10.0 * 0.5 = 5.0.
    ctx = _ctx(terminal_fraction=0.5)
    base = step_reward(ctx, RewardWeights(r_unplaced_penalty=0.0))
    penalized = step_reward(ctx, RewardWeights(r_unplaced_penalty=10.0))
    assert base - penalized == pytest.approx(5.0)


def test_r_unplaced_penalty_not_charged_on_nonterminal_steps():
    # A mid-episode move (terminal_fraction=None) must not pay the abandonment penalty.
    ctx = _ctx(terminal_fraction=None)
    assert step_reward(ctx, RewardWeights(r_unplaced_penalty=10.0)) == step_reward(
        ctx, RewardWeights(r_unplaced_penalty=0.0)
    )


def test_r_unplaced_penalty_widens_placed_advantage():
    # The point of the knob: penalizing abandonment makes placing MORE strictly better, so
    # the full-vs-partial terminal gap is LARGER with the penalty than without (breaks the
    # "drive to budget exhaustion is free" leak the design panel identified).
    w0 = RewardWeights(r_unplaced_penalty=0.0)
    wp = RewardWeights(r_unplaced_penalty=20.0)
    gap0 = step_reward(_ctx(terminal_fraction=1.0), w0) - step_reward(
        _ctx(terminal_fraction=0.5), w0
    )
    gapp = step_reward(_ctx(terminal_fraction=1.0), wp) - step_reward(
        _ctx(terminal_fraction=0.5), wp
    )
    assert gapp > gap0


# ---------------------------------------------------------------------------
# #714 — validity-conditional terminal (Lever A)
# ---------------------------------------------------------------------------


def test_validity_conditional_terminal_default_off_is_byte_identical():
    # Default-neutral: flag off => ctx.terminal_valid is never consulted, so an invalid
    # terminal pays exactly today's value (r_terminal*frac - r_unplaced*(1-frac)).
    ctx_invalid = _ctx(terminal_fraction=1.0, terminal_valid=False)
    ctx_none = _ctx(terminal_fraction=1.0, terminal_valid=None)
    w = RewardWeights(r_unplaced_penalty=8.0)  # validity_conditional_terminal defaults False
    assert step_reward(ctx_invalid, w) == step_reward(ctx_none, w)
    assert step_reward(ctx_invalid, w) == pytest.approx(w.r_terminal)  # frac 1 -> +r_terminal


def test_validity_conditional_terminal_on_invalid_zeros_placed_credit():
    # Flag on + invalid terminal => effective fraction 0 => terminal = -r_unplaced*(1-0).
    w = RewardWeights(r_unplaced_penalty=8.0, validity_conditional_terminal=True)
    ctx = _ctx(terminal_fraction=1.0, terminal_valid=False)
    assert step_reward(ctx, w) == pytest.approx(-w.r_unplaced_penalty)


def test_validity_conditional_terminal_on_valid_pays_full():
    # Flag on + valid terminal => effective fraction = terminal_fraction => +r_terminal at frac 1.
    w = RewardWeights(r_unplaced_penalty=8.0, validity_conditional_terminal=True)
    ctx = _ctx(terminal_fraction=1.0, terminal_valid=True)
    assert step_reward(ctx, w) == pytest.approx(w.r_terminal)


def test_validity_conditional_terminal_invalid_strictly_below_valid_same_fraction():
    # The crux of the #714 fix: at the SAME placed fraction, an INVALID terminal must score
    # strictly lower than a VALID one — removing the commit-everything-invalidly free credit.
    w = RewardWeights(r_unplaced_penalty=8.0, validity_conditional_terminal=True)
    valid = _ctx(terminal_fraction=0.5, terminal_valid=True)
    invalid = _ctx(terminal_fraction=0.5, terminal_valid=False)
    assert step_reward(invalid, w) < step_reward(valid, w)


def test_validity_conditional_terminal_nonterminal_noop():
    # terminal_fraction=None (a mid-episode step) => no terminal term regardless of flag.
    ctx = _ctx(terminal_fraction=None, terminal_valid=None)
    on = RewardWeights(r_unplaced_penalty=8.0, validity_conditional_terminal=True)
    off = RewardWeights(r_unplaced_penalty=8.0, validity_conditional_terminal=False)
    assert step_reward(ctx, on) == step_reward(ctx, off)


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
