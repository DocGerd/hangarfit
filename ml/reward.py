"""Graded-lexicographic reward + potential-based shaping (spec §5)."""

from __future__ import annotations

from dataclasses import dataclass

from ml.types import RewardWeights


@dataclass(frozen=True, slots=True)
class RewardContext:
    """Everything ``step_reward`` needs, precomputed by the env from the oracle."""

    overlap_m2: float
    intrusion_m2: float
    swept_intrusion_m2: float
    egress_blocked: bool
    move_cost: float
    min_gap_m: float
    seq_deviation: float
    region_match: float
    prev_potential: float
    potential: float
    terminal_fraction: float | None  # set only on the terminal step
    # None = not a Park step (bonus structurally absent);
    # False = Park step with invalid layout; True = Park step with valid layout.
    park_valid: bool | None = None


def potential(
    *,
    remaining_overlap_m2: float,
    active_dist_to_slot_m: float,
    unplaced: int,
    active_misfit_m2: float = 0.0,
) -> float:
    """Shaping potential Φ(s) (spec §5). Higher is better. Φ is the NEGATIVE of the costs.
    ``active_misfit_m2`` (the dense_slot_potential in-hangar term) defaults 0.0 → byte-identical
    when the knob is off. Policy-invariant per Ng–Harada–Russell — cannot be reward-hacked."""
    return -(remaining_overlap_m2 + active_dist_to_slot_m + float(unplaced) + active_misfit_m2)


def step_reward(ctx: RewardContext, w: RewardWeights) -> float:
    """Single-step scalar reward. Hard terms dominate (graded so there's a gradient);
    soft terms tie-break; movement keeps tows efficient; a terminal term encodes the
    'best partial' objective; shaping adds γ·Φ(s′)−Φ(s)."""
    hard = -(
        w.w_col * (ctx.overlap_m2 + ctx.swept_intrusion_m2)
        + w.w_oob * ctx.intrusion_m2
        + w.w_egress * (1.0 if ctx.egress_blocked else 0.0)
    )
    movement = -w.w_move * ctx.move_cost
    soft = w.w_gap * ctx.min_gap_m - w.w_seq * ctx.seq_deviation + w.w_region * ctx.region_match
    # Terminal: reward the placed fraction MINUS a penalty on the unplaced fraction. The
    # penalty (r_unplaced_penalty, default 0 -> the second term vanishes) charges abandonment
    # so running an object to budget exhaustion is no longer free relative to committing a
    # Park — the #710 Park/drive-out economics rebalance.
    terminal = (
        w.r_terminal * ctx.terminal_fraction - w.r_unplaced_penalty * (1.0 - ctx.terminal_fraction)
        if ctx.terminal_fraction is not None
        else 0.0
    )
    shaping = w.gamma * ctx.potential - ctx.prev_potential
    valid_park = w.r_valid_park if ctx.park_valid else 0.0
    return hard + movement + soft + terminal + shaping + valid_park
