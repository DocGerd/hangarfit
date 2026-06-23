"""Graded-lexicographic reward + potential-based shaping (spec §5)."""

from __future__ import annotations

import math
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
    # Whole-layout validity at the TERMINAL step (the product checker), consumed only when
    # RewardWeights.validity_conditional_terminal is on. None on non-terminal steps. (#714)
    terminal_valid: bool | None = None
    # True on EXACTLY the one Park step where the episode first reaches a valid placement; the
    # env flips it once per episode. Consumed only when RewardWeights.r_first_valid > 0. (#720)
    first_valid_now: bool = False
    # The number of validly-co-placed objects at THIS Park step (len(_parked) on a Park where
    # the whole layout is valid, else 0). Consumed only when RewardWeights.r_valid_progress > 0.
    # Internal reward input — not an observation. (#812)
    valid_park_count: int = 0


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
    # Park — the #710 Park/drive-out economics rebalance. When validity_conditional_terminal is
    # on, an INVALID terminal layout collapses the effective placed fraction to 0 (so the
    # +r_terminal credit goes to abandonment instead) — the #714 fix for the multi-object
    # commit-everything-invalidly attractor. Default off -> eff_fraction == terminal_fraction.
    if ctx.terminal_fraction is None:
        terminal = 0.0
    else:
        eff_fraction = ctx.terminal_fraction
        if w.validity_conditional_terminal:
            # The env sets terminal_valid whenever terminal_fraction is set (both gated on
            # `done`); assert that here so a future call-site that forgets it fails LOUD rather
            # than silently zeroing a valid layout via `not None`. Zero only on a known-invalid
            # layout (`is False`), never on the None sentinel.
            assert ctx.terminal_valid is not None, "terminal_valid unset at a terminal step"
            if ctx.terminal_valid is False:
                eff_fraction = 0.0
        terminal = w.r_terminal * eff_fraction - w.r_unplaced_penalty * (1.0 - eff_fraction)
    shaping = w.gamma * ctx.potential - ctx.prev_potential
    valid_park = _valid_park_term(ctx, w)
    first_valid = w.r_first_valid if ctx.first_valid_now else 0.0
    # Banked marginal valid-coverage credit (#812): pays only on a Park where the whole layout
    # is valid (park_valid True), scaled by the marginal valid-object count beyond the freebie.
    # park_valid None (non-Park) and False (invalid pile) both pay 0 -> the pile firewall.
    valid_progress = (
        w.r_valid_progress * float(max(0, ctx.valid_park_count - 1)) if ctx.park_valid else 0.0
    )
    return hard + movement + soft + terminal + shaping + valid_park + first_valid + valid_progress


def _valid_park_term(ctx: RewardContext, w: RewardWeights) -> float:
    """The Park bonus. When ``park_valid`` is None (not a Park step) → no bonus. With
    ``valid_park_grade_scale == 0`` the bonus is the binary all-or-nothing form (the layout is
    valid → full ``r_valid_park``, else 0 — byte-identical to the pre-#720 path). With scale > 0
    it is GRADED by a near-miss ``misfit`` so a Park landing CLOSE to valid earns partial credit
    and the rare near-miss pays a learnable return — the uphill gradient INTO the witness slot
    that the flat valid-only plateau lacked. A graded Park is withheld entirely when egress is
    blocked: egress is a binary hard failure with no graded 'near'.

    ``misfit = overlap_m2 + intrusion_m2`` — the static FINAL-POSE collision + out-of-bounds/notch
    area. It deliberately excludes ``swept_intrusion_m2`` (the tow-sweep area the hard collision
    term also charges): a Park is judged on its final pose, not its path, and the env sets
    ``swept_intrusion_m2 = 0`` on a Park step anyway. INVARIANT: ``misfit`` must remain a SUPERSET
    of every non-egress source of ``park_valid is False`` (today: overlap + bounds/notch intrusion).
    If a future keep-out can flip validity without raising overlap/intrusion it MUST be summed in
    here — else an invalid-but-zero-misfit Park would earn ~full credit (``exp(0) ≈ 1``) and reopen
    a 'looks valid, isn't' reward leak."""
    if ctx.park_valid is None:
        return 0.0
    if w.valid_park_grade_scale <= 0.0:
        return w.r_valid_park if ctx.park_valid else 0.0
    if ctx.egress_blocked:
        return 0.0
    misfit = ctx.overlap_m2 + ctx.intrusion_m2
    return w.r_valid_park * math.exp(-misfit / w.valid_park_grade_scale)
