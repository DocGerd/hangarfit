"""Tests for the statistical reach-rate harness (#711). The stats / sampling / RR-MC
arm is torch-free; the policy arm importorskips torch per-test."""

from __future__ import annotations

import pytest

from ml.reach_rate import (
    DominanceVerdict,
    KindDominance,
    ReachRate,
    SampledScenario,
    aggregate,
    dominance_verdict,
    reach_rate,
    rrmc_reach_multi,
    sample_population,
    wilson_ci,
    witness_absent_kinds,
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


def _subset_keys(pop):
    return [(s.kind, tuple(s.scenario.fleet_in)) for s in pop]


def test_sample_population_distinct_has_no_duplicate_subsets():
    # distinct=True guarantees each (kind, subset) appears at most once — RR-MC is deterministic,
    # so duplicate subsets are not independent trials (pseudo-replication).
    pop = sample_population(n=30, k_min=2, k_max=4, seed=0, distinct=True)
    keys = _subset_keys(pop)
    assert len(keys) == len(set(keys))


def test_sample_population_distinct_caps_at_available():
    # Only C(9,8)=9 distinct k8 subsets exist; a request for more is capped, not padded with dups.
    pop = sample_population(n=50, k_min=8, k_max=8, seed=0, distinct=True)
    keys = _subset_keys(pop)
    assert len(pop) == 9
    assert len(keys) == len(set(keys))


def test_sample_population_distinct_is_deterministic_in_seed():
    a = sample_population(n=12, k_min=2, k_max=4, seed=5, distinct=True)
    b = sample_population(n=12, k_min=2, k_max=4, seed=5, distinct=True)
    assert [s.name for s in a] == [s.name for s in b]
    assert _subset_keys(a) == _subset_keys(b)


def test_sample_population_distinct_varies_with_seed():
    a = _subset_keys(sample_population(n=10, k_min=2, k_max=4, seed=1, distinct=True))
    b = _subset_keys(sample_population(n=10, k_min=2, k_max=4, seed=2, distinct=True))
    assert a != b  # n < available ⇒ a different seeded draw selects different subsets


def test_sample_population_default_is_unchanged_and_can_repeat():
    # Default (distinct=False) is byte-identical to today: returns exactly n even where the
    # distinct space is smaller (here 9 distinct k8 subsets, but n=20 is honored).
    pop = sample_population(n=20, k_min=8, k_max=8, seed=0)
    assert len(pop) == 20


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


def test_routed_plane_count_counts_planes_not_legs():
    # #667 Rung E: a move-aside plan emits MULTIPLE routed legs for one plane
    # (staging + final), so summing routed Moves over-counts and would falsely fail
    # the reach check. Count DISTINCT planes with a routed leg; deferred (path=None)
    # legs are excluded.
    from hangarfit.towplanner import DubinsArc, Move, MovesPlan, Pose, Segment
    from ml.reach_rate import _routed_plane_count

    def _arc(x: float, y: float) -> DubinsArc:
        return DubinsArc(
            start=Pose(0.0, 0.0, 0.0),
            end=Pose(x, y, 0.0),
            turn_radius_m=8.0,
            segments=(Segment("S", (x * x + y * y) ** 0.5),),
        )

    plan = MovesPlan(
        target_layout=object(),  # MovesPlan does not validate the layout
        moves=(
            Move("a", Pose(0.0, -3.0, 180.0), _arc(0.0, -3.0), leg_index=0),  # staging
            Move("a", Pose(0.0, 5.0, 0.0), _arc(0.0, 5.0), leg_index=1),  # final
            Move("b", Pose(1.0, 4.0, 0.0), _arc(1.0, 4.0), leg_index=0),
            Move("c", Pose(2.0, 4.0, 0.0), None, leg_index=0),  # deferred — excluded
        ),
    )
    assert _routed_plane_count(plan) == 2  # planes a, b — not 3 routed legs, not c


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


# ── trigger-#1 dominance gate (ADR-0028 re-open condition #1) ─────────────────
# Pure decision logic over Wilson CIs — torch-free, no solver. The Wilson math itself is
# covered by the wilson_ci tests above, so these construct synthetic ReachRates with chosen
# bounds and assert the *gate* logic: which kinds are witness-absent, and the masquerade-proof
# CI-non-overlap dominance verdict.


def _rr(kind: str, *, ci_lo: float = 0.0, ci_hi: float = 1.0) -> ReachRate:
    """A synthetic ReachRate whose only meaningful fields are the Wilson bounds the dominance
    logic keys on; n/reached/rate are filler."""
    return ReachRate(kind=kind, n=0, reached=0, rate=0.0, ci_lo=ci_lo, ci_hi=ci_hi)


def test_witness_absent_kinds_selects_low_rrmc_and_excludes_high():
    rrmc = {
        "k2": _rr("k2", ci_hi=0.95),  # RR-MC reaches ⇒ NOT witness-absent
        "k7": _rr("k7", ci_hi=0.08),  # RR-MC misses ⇒ witness-absent
    }
    assert witness_absent_kinds(rrmc, tau=0.1) == ["k7"]


def test_witness_absent_kinds_excludes_overall_pool_row():
    rrmc = {"overall": _rr("overall", ci_hi=0.05), "k7": _rr("k7", ci_hi=0.05)}
    assert witness_absent_kinds(rrmc, tau=0.1) == ["k7"]  # the pooled row is never a kind


def test_witness_absent_kinds_is_sorted():
    rrmc = {k: _rr(k, ci_hi=0.05) for k in ("k9", "k3", "k7")}
    assert witness_absent_kinds(rrmc, tau=0.1) == ["k3", "k7", "k9"]


def test_witness_absent_kinds_boundary_is_inclusive():
    rrmc = {"k7": _rr("k7", ci_hi=0.10)}
    assert witness_absent_kinds(rrmc, tau=0.10) == ["k7"]  # ci_hi == tau ⇒ included


def test_witness_absent_kinds_rejects_bad_tau():
    with pytest.raises(ValueError, match="tau"):
        witness_absent_kinds({}, tau=1.5)


def test_dominance_verdict_reopens_when_policy_ci_clears_rrmc():
    rrmc = {"k7": _rr("k7", ci_hi=0.08)}
    policy = {"k7": _rr("k7", ci_lo=0.20)}  # 0.20 > 0.08 ⇒ CI non-overlap ⇒ beats
    v = dominance_verdict(rrmc, policy, tau=0.1)
    assert v.reopen is True
    assert v.exercised is True
    assert v.witness_absent == ("k7",)
    (kd,) = v.per_kind
    assert kd.policy_beats is True


def test_dominance_verdict_no_reopen_on_ci_overlap():
    # Masquerade-proof: the policy POINT estimate could be higher, but overlapping CIs don't trip.
    rrmc = {"k7": _rr("k7", ci_hi=0.30)}
    policy = {"k7": _rr("k7", ci_lo=0.25)}  # 0.25 is NOT > 0.30
    v = dominance_verdict(rrmc, policy, tau=0.35)
    assert v.reopen is False
    assert v.exercised is True
    assert v.per_kind[0].policy_beats is False


def test_dominance_verdict_ignores_kinds_rrmc_already_reaches():
    # A kind RR-MC reaches (not witness-absent) is excluded even if the policy "beats" there:
    # reaching what the solver already reaches is not the charter.
    rrmc = {"k2": _rr("k2", ci_hi=0.90)}
    policy = {"k2": _rr("k2", ci_lo=0.99)}
    v = dominance_verdict(rrmc, policy, tau=0.1)
    assert v.exercised is False  # no witness-absent kind ⇒ gate not exercised
    assert v.reopen is False
    assert v.witness_absent == ()


def test_dominance_verdict_missing_policy_kind_counts_as_no_evidence():
    rrmc = {"k7": _rr("k7", ci_hi=0.05)}
    policy: dict[str, ReachRate] = {}  # policy arm never covered k7
    v = dominance_verdict(rrmc, policy, tau=0.1)
    assert v.exercised is True
    assert v.reopen is False
    kd = v.per_kind[0]
    assert kd.policy_covered is False
    assert kd.policy_beats is False


def test_dominance_verdict_not_exercised_distinct_from_not_met():
    # Empty population ⇒ NOT exercised (vacuous), which must not masquerade as a clean negative.
    v = dominance_verdict({}, {}, tau=0.1)
    assert v.exercised is False
    assert v.reopen is False


def test_dominance_verdict_rejects_bad_tau():
    with pytest.raises(ValueError, match="tau"):
        dominance_verdict({}, {}, tau=-0.1)


def test_dominance_verdict_types():
    rrmc = {"k7": _rr("k7", ci_hi=0.05)}
    policy = {"k7": _rr("k7", ci_lo=0.5)}
    v = dominance_verdict(rrmc, policy, tau=0.1)
    assert isinstance(v, DominanceVerdict)
    assert all(isinstance(kd, KindDominance) for kd in v.per_kind)
