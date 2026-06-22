"""#734: slope-aware auto-budget controller — pure, torch-free.

The controller decides when a curriculum rung has *plateaued* (stop training early) vs is
still *climbing* (keep going up to a hard ceiling), from the per-iteration windowed-mean
``valid_placed`` series. It mirrors ``should_promote``'s purity: no torch, no IO, a pure
function of the float series + config.
"""

from __future__ import annotations

import pytest

from ml.curriculum import (
    BudgetController,
    EpisodeStat,
    theil_sen_slope,
    window_score,
)

# ---------------------------------------------------------------------------
# theil_sen_slope — robust trend
# ---------------------------------------------------------------------------


def test_theil_sen_slope_positive_for_climbing():
    assert theil_sen_slope([0.0, 1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)


def test_theil_sen_slope_zero_for_flat():
    assert theil_sen_slope([5.0, 5.0, 5.0, 5.0]) == 0.0


def test_theil_sen_slope_negative_for_declining():
    assert theil_sen_slope([3.0, 2.0, 1.0, 0.0]) == pytest.approx(-1.0)


def test_theil_sen_slope_robust_to_a_single_outlier():
    # One spike must not flip a flat trend to "climbing" (the point of Theil–Sen).
    assert theil_sen_slope([5.0, 5.0, 99.0, 5.0, 5.0]) == pytest.approx(0.0)


def test_theil_sen_slope_degenerate_inputs():
    assert theil_sen_slope([]) == 0.0
    assert theil_sen_slope([7.0]) == 0.0


# ---------------------------------------------------------------------------
# window_score — mean of the promotion metric over a window of EpisodeStats
# ---------------------------------------------------------------------------


def _ep(fraction: float, valid: bool) -> EpisodeStat:
    return EpisodeStat(fraction_placed=fraction, valid=valid, total_reward=0.0)


def test_window_score_valid_placed_credits_only_valid_episodes():
    w = [_ep(1.0, True), _ep(1.0, False), _ep(0.5, True), _ep(1.0, False)]
    # valid_placed = mean(1.0, 0, 0.5, 0) = 0.375
    assert window_score(w, "valid_placed") == pytest.approx(0.375)


def test_window_score_empty_is_zero():
    assert window_score([], "valid_placed") == 0.0


def test_window_score_valid_rate_and_fraction_placed():
    w = [_ep(1.0, True), _ep(0.5, False), _ep(0.5, True), _ep(0.0, False)]
    assert window_score(w, "valid_rate") == pytest.approx(0.5)  # 2 of 4 valid
    assert window_score(w, "fraction_placed") == pytest.approx(0.5)  # mean(1, .5, .5, 0)


# ---------------------------------------------------------------------------
# BudgetController.should_stop
# ---------------------------------------------------------------------------


def _ctrl(**kw) -> BudgetController:
    from dataclasses import replace

    base = BudgetController(
        min_iters=10, slope_window=5, plateau_patience=2, max_iters=1000, eps=1e-6
    )
    return replace(base, **kw)


def test_no_stop_before_min_iters():
    c = _ctrl()
    assert c.should_stop([0.0] * 5) is False  # flat but too short to judge


def test_no_stop_while_climbing():
    c = _ctrl()
    history = [float(i) for i in range(15)]  # steadily climbing
    assert c.should_stop(history) is False


def test_stop_on_plateau():
    c = _ctrl()
    history = [0.8] * 15  # flat for well over the patience window
    assert c.should_stop(history) is True


def test_no_stop_while_flat_at_floor():
    # #743 regression: a curve flat at ~0 is a WARMUP (not started), not a converged plateau.
    # Slope alone cannot tell floor-flat from ceiling-flat (both are ~0). The floor-guard keeps
    # training while the recent level is below min_level, so --auto-budget no longer truncates a
    # hard rung's flat pre-climb warmup (the trio-box failure: plateau-stopped at iter 29, ~0.04).
    c = _ctrl(min_level=0.05)
    assert c.should_stop([0.0] * 15) is False


def test_stop_when_plateaued_above_floor():
    # flat at a MEANINGFUL level (converged below the competency threshold) still stops — the
    # floor-guard only blocks floor-flat, not a genuine ceiling-plateau.
    c = _ctrl(min_level=0.05)
    assert c.should_stop([0.7] * 15) is True


def test_floor_guard_uses_recent_level_not_early_floor():
    # warm up at the floor, then climb to a flat plateau: the early floor iterations must not
    # veto the genuine late plateau — the guard reads the RECENT slope_window's level.
    c = _ctrl(min_level=0.05, min_iters=10, slope_window=5, plateau_patience=2)
    history = [0.0] * 8 + [0.6] * 7  # floor warmup then flat at 0.6
    assert c.should_stop(history) is True


def test_min_level_zero_disables_floor_guard():
    # min_level=0 restores pure slope-only stopping — the isolation lever for the wiring test.
    c = _ctrl(min_level=0.0)
    assert c.should_stop([0.0] * 15) is True


def test_stop_on_decline():
    c = _ctrl()
    history = [1.0 - 0.01 * i for i in range(15)]  # slowly declining
    assert c.should_stop(history) is True


def test_no_stop_when_recent_window_resumes_climbing():
    c = _ctrl()
    history = [0.5] * 10 + [0.6, 0.7, 0.8, 0.9, 1.0]  # plateau then climbs again
    assert c.should_stop(history) is False


def test_plateau_patience_requires_consecutive_flat_windows():
    # Only the very last window is flat; the prior window still climbs → not yet a plateau.
    c = _ctrl(min_iters=6, slope_window=4, plateau_patience=2)
    history = [0.0, 1.0, 2.0, 3.0, 4.0, 4.0, 4.0, 4.0]  # last window flat, prior climbing
    assert c.should_stop(history) is False


def test_should_stop_boundary_just_enough_points():
    # min_iters=2, slope_window=4, plateau_patience=2 => both trailing windows form at n>=5.
    # Locks the negative-slice-safety guard at exactly the boundary.
    c = _ctrl(min_iters=2, slope_window=4, plateau_patience=2)
    assert c.should_stop([0.5] * 4) is False  # one short of forming both windows
    assert c.should_stop([0.5] * 5) is True  # exactly enough; flat => plateau


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        BudgetController(min_iters=0)
    with pytest.raises(ValueError):
        BudgetController(slope_window=1)  # need >= 2 points for a slope
    with pytest.raises(ValueError):
        BudgetController(plateau_patience=0)
    with pytest.raises(ValueError):
        BudgetController(max_iters=0)
    with pytest.raises(ValueError):
        BudgetController(min_level=-0.1)  # the floor must be a non-negative metric level


# ---------------------------------------------------------------------------
# Wiring into train_curriculum's per-rung loop (torch path). A never-promote
# threshold (2.0 > the [0,1] metric) isolates the budget/cap behaviour.
# ---------------------------------------------------------------------------

_SMALL_NET = {"d_model": 32, "n_layers": 1, "n_heads": 2}


def _one_stage_sched(*, threshold: float, max_iters: int):
    from dataclasses import replace

    from ml.curriculum import CurriculumSchedule

    sched = CurriculumSchedule.default()
    return replace(
        sched,
        stages=(sched.stages[0],),  # just the trivial rung — keep the run tiny
        policy=replace(sched.policy, threshold=threshold, max_iters=max_iters),
    )


def test_auto_budget_none_keeps_fixed_cap():
    """Default (auto_budget=None) path: the rung caps at the fixed pol.max_iters - 1."""
    pytest.importorskip("torch")
    from ml.train import train_curriculum

    sched = _one_stage_sched(threshold=2.0, max_iters=3)
    hist = train_curriculum(
        seed=0, schedule=sched, rollout_len=8, policy_kwargs=_SMALL_NET, auto_budget=None
    )
    _name, it, by = hist.promotions[-1]
    assert by == "cap" and it == 2  # pol.max_iters - 1 — fixed-cap path unchanged


def test_auto_budget_ceiling_replaces_fixed_cap():
    """With a controller that never stops early (min_iters above the ceiling), the loop runs
    to the controller's hard ceiling, NOT the fixed pol.max_iters."""
    pytest.importorskip("torch")
    from ml.train import train_curriculum

    sched = _one_stage_sched(threshold=2.0, max_iters=2)  # fixed cap would stop at it=1
    budget = BudgetController(min_iters=100, slope_window=2, plateau_patience=1, max_iters=4)
    hist = train_curriculum(
        seed=0, schedule=sched, rollout_len=8, policy_kwargs=_SMALL_NET, auto_budget=budget
    )
    _name, it, by = hist.promotions[-1]
    assert by == "cap" and it == 3  # ran to the controller ceiling (4) - 1, not the fixed cap (2)


def test_auto_budget_stops_early_on_plateau():
    """A controller with a huge eps treats every slope as non-positive, so it plateau-stops
    as soon as it has min_iters points — well before the high fixed cap. min_level=0 isolates
    the slope-plateau wiring from the #743 floor-guard (separately unit-tested above); without
    it the floor-guard would keep an early flat-at-floor trivial rung training past min_iters."""
    pytest.importorskip("torch")
    from ml.train import train_curriculum

    sched = _one_stage_sched(threshold=2.0, max_iters=100)
    budget = BudgetController(
        min_iters=2, slope_window=2, plateau_patience=1, max_iters=100, eps=1e9, min_level=0.0
    )
    hist = train_curriculum(
        seed=0, schedule=sched, rollout_len=8, policy_kwargs=_SMALL_NET, auto_budget=budget
    )
    _name, it, by = hist.promotions[-1]
    assert by == "budget-plateau" and it == 1  # stopped at the 2nd iter, before the cap
