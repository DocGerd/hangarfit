"""Tests for the statistical reach-rate harness (#711). The stats / sampling / RR-MC
arm is torch-free; the policy arm importorskips torch per-test."""

from __future__ import annotations

import pytest

from ml.reach_rate import (
    ReachRate,
    SampledScenario,
    aggregate,
    reach_rate,
    rrmc_reach_multi,
    sample_population,
    wilson_ci,
)

# ── pure statistics ──────────────────────────────────────────────────────────


def test_wilson_ci_all_success_is_below_one_and_brackets_one():
    lo, hi = wilson_ci(10, 10)
    assert hi == pytest.approx(1.0, abs=1e-9)  # clamped at 1
    assert 0.0 < lo < 1.0  # ...but the lower bound pulls in (the Wilson virtue vs normal)


def test_wilson_ci_all_failure_is_above_zero():
    lo, hi = wilson_ci(0, 10)
    assert lo == pytest.approx(0.0, abs=1e-9)
    assert 0.0 < hi < 1.0


def test_wilson_ci_brackets_the_point_estimate():
    lo, hi = wilson_ci(5, 10)
    assert lo < 0.5 < hi


def test_wilson_ci_zero_n_is_full_uncertainty():
    assert wilson_ci(0, 0) == (0.0, 1.0)


def test_wilson_ci_narrows_with_n():
    _, hi_small = wilson_ci(5, 10)
    _, hi_big = wilson_ci(50, 100)
    assert (hi_big - 0.5) < (hi_small - 0.5)  # same rate, more data ⇒ tighter


def test_wilson_ci_rejects_out_of_range():
    with pytest.raises(ValueError, match="out of range"):
        wilson_ci(11, 10)


def test_reach_rate_fields():
    r = reach_rate("k3", 3, 4)
    assert (r.kind, r.reached, r.n) == ("k3", 3, 4)
    assert r.rate == pytest.approx(0.75)
    assert r.ci_lo < 0.75 < r.ci_hi


def test_aggregate_groups_by_kind_and_adds_overall():
    rates = aggregate([("k2", True), ("k2", False), ("k3", True), ("k3", True)])
    assert rates["k2"].reached == 1 and rates["k2"].n == 2
    assert rates["k3"].reached == 2 and rates["k3"].n == 2
    assert rates["overall"].reached == 3 and rates["overall"].n == 4
    assert all(isinstance(v, ReachRate) for v in rates.values())


def test_aggregate_empty_is_empty():
    assert aggregate([]) == {}


# ── sampled population ───────────────────────────────────────────────────────


def test_sample_population_is_deterministic_in_seed():
    a = sample_population(n=5, seed=7)
    b = sample_population(n=5, seed=7)
    assert [s.name for s in a] == [s.name for s in b]
    assert [tuple(s.scenario.fleet_in) for s in a] == [tuple(s.scenario.fleet_in) for s in b]


def test_sample_population_varies_with_seed():
    a = [tuple(s.scenario.fleet_in) for s in sample_population(n=6, seed=1)]
    b = [tuple(s.scenario.fleet_in) for s in sample_population(n=6, seed=2)]
    assert a != b


def test_sample_population_respects_k_bounds_and_kind_label():
    pop = sample_population(n=12, k_min=2, k_max=3, seed=0)
    for ss in pop:
        k = len(ss.scenario.fleet_in)
        assert 2 <= k <= 3
        assert ss.kind == f"k{k}"
        assert len(set(ss.scenario.fleet_in)) == k  # distinct ids
    assert isinstance(pop[0], SampledScenario)


def test_sample_population_rejects_bad_k_bounds():
    with pytest.raises(ValueError, match="k_min <= k_max"):
        sample_population(n=1, k_min=3, k_max=2, seed=0)


def test_sample_population_rejects_k_over_pool():
    with pytest.raises(ValueError, match="exceeds fleet pool"):
        sample_population(n=1, k_min=2, k_max=999, seed=0)


# ── multi-alternative RR-MC ──────────────────────────────────────────────────


@pytest.mark.slow
def test_rrmc_reach_multi_reaches_an_easy_two_plane_fill():
    # A 2-plane fill on the roomy test hangar is trivially valid+routable; RR-MC reaches it
    # at a small budget. (Fast: 2 planes, few restarts.)
    ss = sample_population(n=1, k_min=2, k_max=2, seed=0)[0]
    reached = rrmc_reach_multi(
        ss.scenario, alternatives=2, max_restarts=4, tow_max_expansions=2000, seed=0
    )
    assert reached is True


@pytest.mark.slow
def test_rrmc_reach_multi_is_deterministic():
    ss = sample_population(n=1, k_min=2, k_max=2, seed=3)[0]
    kw = dict(alternatives=2, max_restarts=4, tow_max_expansions=2000, seed=0)
    assert rrmc_reach_multi(ss.scenario, **kw) == rrmc_reach_multi(ss.scenario, **kw)


# ── policy arm (torch-gated) ─────────────────────────────────────────────────


def test_policy_reach_count_runs_end_to_end():
    pytest.importorskip("torch")
    from ml.policy import HangarFitPolicy
    from ml.reach_rate import policy_reach_count

    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    ss = sample_population(n=1, k_min=2, k_max=2, seed=0)[0]
    # An untrained policy may or may not reach; assert it runs and returns a valid count.
    count = policy_reach_count(ss.scenario, policy, samples=3, seed=0)
    assert isinstance(count, int)
    assert 0 <= count <= 3


def test_policy_population_rates_shape():
    pytest.importorskip("torch")
    from ml.policy import HangarFitPolicy
    from ml.reach_rate import policy_population_rates

    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    pop = sample_population(n=2, k_min=2, k_max=2, seed=0)
    rates = policy_population_rates(pop, policy, samples=2, seed=0)
    assert "overall" in rates
    assert rates["overall"].n == 4  # 2 scenarios × 2 samples
    assert 0.0 <= rates["overall"].rate <= 1.0
