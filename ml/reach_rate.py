"""Statistical reach-rate harness (#711, epic #607) — DEV/CI-ONLY.

Lifts the #695 reach benchmark from a 4-row existence table to a reproducible
reach-**rate** over a sampled population: N scenarios × M samples, reported as
reach-rate ± Wilson CI per scenario-kind, for both RR-MC (multi-alternative) and a
trained policy. ``ml/benchmark.py`` answers "does RR-MC/the policy reach THESE four
frozen scenarios"; this answers "what FRACTION of a population does each reach".

Both arms judge reach by the **same** predicate the #695 bench uses — the product
checker (``geometry_oracle.layout_valid`` = ``collisions.check`` + Caddy egress, #694)
**and** routable-by-construction (``plan_fill`` routes every placeable body with a real
path) — never the env oracle's parked-score validity.

**Multi-alternative RR-MC.** ``rrmc_reach_multi`` solves for ``alternatives`` candidate
layouts and counts RR-MC-reached if **any** is valid + fully routable — strictly stronger
than the #695 ``rrmc_reach`` (``alternatives=1``), which only routes the single best-spread
layout. Load-bearing the moment the solver yields valid-but-unroutable dense layouts.

**Budget (the #711 caveat).** RR-MC reach is the expensive arm (a ``solve`` + ``plan_fill``
per scenario), so the CLI defaults to a SMALL population at a MODEST restart budget; a
large-population RR-MC baseline is meant to be recorded once and frozen, mirroring the
#695 ``bench_baseline.json`` freeze (spec D4). The policy arm is cheap (rollouts, no
solver), so it affords more stochastic samples per scenario.

The sampled population (v1) varies the **fleet subset** (k aircraft drawn from a pool) on a
fixed roomy hangar — a clean fill with no ground objects / pins. Varying hangar geometry and
GO placements (issue subtask 1's other axes) is a documented extension.
"""

from __future__ import annotations

import argparse
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path

from hangarfit.loader import load_fleet, load_hangar
from hangarfit.models import Scenario, SearchConfig
from hangarfit.solver import solve
from hangarfit.towplanner import MovesPlan, NoFeasiblePlanError, plan_fill
from ml import geometry_oracle as go

_ROOT = Path(__file__).resolve().parent.parent  # repo root (ml/ sits at the root)


# ── pure statistics ──────────────────────────────────────────────────────────


def wilson_ci(successes: int, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. Preferred over the normal
    approximation because a reach-rate routinely sits at the 0/1 extremes, where the
    normal interval misbehaves (negative bounds / zero width). ``z=1.96`` ⇒ 95% CI.
    ``n == 0`` ⇒ ``(0.0, 1.0)`` (no information). Raises if ``successes`` ∉ ``0..n``."""
    if successes < 0 or successes > n:
        raise ValueError(f"wilson_ci: successes {successes} out of range 0..{n}")
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True, slots=True)
class ReachRate:
    """A reach-rate over ``n`` Bernoulli trials of one scenario-kind, with a Wilson CI."""

    kind: str
    n: int
    reached: int
    rate: float
    ci_lo: float
    ci_hi: float


def reach_rate(kind: str, reached: int, n: int) -> ReachRate:
    lo, hi = wilson_ci(reached, n)
    return ReachRate(
        kind=kind, n=n, reached=reached, rate=(reached / n if n else 0.0), ci_lo=lo, ci_hi=hi
    )


def aggregate(pairs: Iterable[tuple[str, bool]]) -> dict[str, ReachRate]:
    """Aggregate ``(kind, reached)`` trials into a ``{kind: ReachRate}`` map plus an
    ``"overall"`` row pooling every trial. Pure — the statistics layer the issue notes
    neither ``eval.py`` nor ``benchmark.py`` provides."""
    by_kind: dict[str, list[bool]] = {}
    all_trials: list[bool] = []
    for kind, reached in pairs:
        by_kind.setdefault(kind, []).append(bool(reached))
        all_trials.append(bool(reached))
    out = {k: reach_rate(k, sum(v), len(v)) for k, v in sorted(by_kind.items())}
    if all_trials:
        out["overall"] = reach_rate("overall", sum(all_trials), len(all_trials))
    return out


# ── trigger-#1 dominance gate (ADR-0028 re-open condition #1) ─────────────────


def _check_tau(tau: float) -> None:
    if not 0.0 <= tau <= 1.0:
        raise ValueError(f"tau must be in [0, 1], got {tau}")


def witness_absent_kinds(rrmc: Mapping[str, ReachRate], *, tau: float) -> list[str]:
    """The scenario-kinds whose RR-MC reach-rate upper Wilson bound sits at or below ``tau``.

    ⚠ **Necessary, not sufficient** for a true witness-absent kind. "RR-MC reach ≈ 0" has two
    causes: RR-MC *missed* a valid layout that **exists** (the chartered ground — "reach what the
    deterministic solver misses", #607), **or** **no valid layout exists** and the kind is
    **infeasible**. This function cannot distinguish them — it sees only the reach-rate — so a kind
    it returns is chartered ground **only once an independent feasibility witness** (a valid layout
    *proven to exist*: a hand-authored one like ``examples/herrenteich/layout.yaml`` or a
    big-budget ``solve`` result) is exhibited for it. Without that, an infeasible over-capacity
    kind masquerades as witness-absent and any dominance verdict over it is **vacuous** (the #832
    retraction, #835). A policy "win" anywhere RR-MC already reaches is likewise not chartered.
    Excludes the synthetic ``"overall"`` pooled row. Sorted for a deterministic verdict."""
    _check_tau(tau)
    return sorted(k for k, r in rrmc.items() if k != "overall" and r.ci_hi <= tau)


@dataclass(frozen=True, slots=True)
class KindDominance:
    """Per-kind dominance fact on one witness-absent kind: does the policy's reach-rate Wilson
    lower bound clear RR-MC's upper bound? ``policy_covered`` is False when the policy arm never
    sampled this kind (no evidence ⇒ cannot beat)."""

    kind: str
    rrmc_ci_hi: float
    policy_covered: bool
    policy_ci_lo: float
    policy_beats: bool


@dataclass(frozen=True, slots=True)
class DominanceVerdict:
    """ADR-0028 re-open **trigger #1**: does a policy's dense-notch reach-rate Wilson CI *exceed*
    RR-MC's on a **witness-absent** kind? Masquerade-proof by construction — it requires Wilson-CI
    **non-overlap** (policy lower bound strictly above RR-MC upper bound), so a policy that merely
    matches RR-MC by sampling luck cannot trip it.

    ``exercised`` (the population actually contained an RR-MC-misses kind) is reported separately
    from ``reopen`` so a *vacuous* negative — "no such kind in this population" — is never mistaken
    for a clean "policy tested and did not beat RR-MC".

    ⚠ ``exercised`` does **not** certify feasibility: it only means some kind had RR-MC
    ``ci_hi <= tau`` (see :func:`witness_absent_kinds`). A trustworthy verdict requires the
    population to be **feasibility-witnessed** — each contested kind backed by a valid layout proven
    to exist that the fair-budget *deployed* RR-MC misses. Over an over-capacity-infeasible
    population, ``reopen=False`` is the infeasible-masquerade, not a real "did not beat RR-MC"
    (#835)."""

    tau: float
    witness_absent: tuple[str, ...]
    per_kind: tuple[KindDominance, ...]
    reopen: bool

    @property
    def exercised(self) -> bool:
        """True iff the population contained at least one witness-absent kind to test on."""
        return bool(self.witness_absent)


def dominance_verdict(
    rrmc: Mapping[str, ReachRate], policy: Mapping[str, ReachRate], *, tau: float
) -> DominanceVerdict:
    """The trigger-#1 verdict from the two arms' per-kind reach-rates (the ``aggregate`` outputs).
    See :class:`DominanceVerdict`."""
    wa = witness_absent_kinds(rrmc, tau=tau)
    per_kind: list[KindDominance] = []
    for k in wa:
        rr_hi = rrmc[k].ci_hi
        p = policy.get(k)
        if p is None:
            per_kind.append(
                KindDominance(k, rr_hi, policy_covered=False, policy_ci_lo=0.0, policy_beats=False)
            )
        else:
            per_kind.append(
                KindDominance(
                    k,
                    rr_hi,
                    policy_covered=True,
                    policy_ci_lo=p.ci_lo,
                    policy_beats=p.ci_lo > rr_hi,
                )
            )
    return DominanceVerdict(
        tau=tau,
        witness_absent=tuple(wa),
        per_kind=tuple(per_kind),
        reopen=any(kd.policy_beats for kd in per_kind),
    )


# ── sampled population ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SampledScenario:
    """One member of the reach-rate population: an in-memory fill Scenario + its kind
    label (the fleet-subset size stratum, e.g. ``"k3"``)."""

    name: str
    kind: str
    scenario: Scenario


def sample_population(
    *,
    n: int,
    k_min: int = 2,
    k_max: int = 4,
    seed: int,
    fleet_path: str = "data/fleet.yaml",
    hangar_path: str = "tests/fixtures/test_hangar_large.yaml",
    distinct: bool = False,
) -> list[SampledScenario]:
    """Sample ``n`` aircraft-subset fill scenarios on a fixed hangar — the reach-rate
    population. Each draws ``k ∈ [k_min, k_max]`` aircraft from the fleet pool (no ground
    objects, no maintenance, no pins). Deterministic in ``seed`` (its sole RNG), so the
    population — and thus the baseline — is reproducible.

    When ``distinct`` is True, the members are guaranteed-distinct ``(k, subset)`` pairs and the
    population is **capped** at the number of available distinct subsets across ``[k_min, k_max]``
    (a request for more returns *fewer*, never duplicates). Capping triggers whenever ``n`` exceeds
    the available distinct count — most acute at high ``k`` relative to the pool, where the
    distinct-subset space is small (e.g. ``C(9, 8) = 9``): RR-MC reach is deterministic per subset,
    so a duplicate subset is **not** an independent trial, and counting it would inflate ``n`` and
    tighten the Wilson CI without new information (pseudo-replication).
    Default ``False`` preserves the independent-draw behaviour byte-for-byte."""
    if not 1 <= k_min <= k_max:
        raise ValueError(f"sample_population: need 1 <= k_min <= k_max, got {k_min}, {k_max}")
    fleet = load_fleet(str(_ROOT / fleet_path))
    hangar = load_hangar(str(_ROOT / hangar_path), fleet=fleet)
    pool = sorted(fleet)  # aircraft ids, sorted for a deterministic draw
    if k_max > len(pool):
        raise ValueError(f"sample_population: k_max {k_max} exceeds fleet pool size {len(pool)}")
    rng = random.Random(seed)

    def _scenario(subset: tuple[str, ...]) -> Scenario:
        return Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=subset,
            constraints={},
            ground_object_defs={},
            region_preferences={},
        )

    if distinct:
        # Enumerate the distinct-subset universe, deterministically shuffle, take min(n, available).
        universe = [
            tuple(subset) for k in range(k_min, k_max + 1) for subset in combinations(pool, k)
        ]
        rng.shuffle(universe)
        return [
            SampledScenario(
                name=f"sample{i:03d}_k{len(s)}", kind=f"k{len(s)}", scenario=_scenario(s)
            )
            for i, s in enumerate(universe[:n])
        ]

    out: list[SampledScenario] = []
    for i in range(n):
        k = rng.randint(k_min, k_max)
        subset = tuple(sorted(rng.sample(pool, k)))
        out.append(
            SampledScenario(name=f"sample{i:03d}_k{k}", kind=f"k{k}", scenario=_scenario(subset))
        )
    return out


# ── reach predicates (the #695 product-checker predicate, both arms) ─────────


def _routed_plane_count(plan: MovesPlan) -> int:
    """Number of DISTINCT aircraft with at least one routed leg (#667 Rung E).

    Counts planes, not legs: a move-aside plan emits multiple routed legs for one
    plane (a staging leg + the final leg), so summing routed Moves would over-count
    and falsely fail the ``== n_total`` reach check. Deferred (``path is None``)
    legs are excluded, matching the prior semantics."""
    return len({m.plane_id for m in plan.moves if m.path is not None})


def rrmc_reach_multi(
    scenario: Scenario,
    *,
    alternatives: int,
    max_restarts: int,
    tow_max_expansions: int,
    seed: int = 0,
) -> bool:
    """Multi-alternative RR-MC reach: solve for up to ``alternatives`` diverse layouts and
    return True if **any** is valid (product checker) AND fully routable-by-construction
    (``plan_fill`` gives every placeable body a real tow path). ``budget_s=inf`` so
    ``max_restarts`` is the only bound (a wall-clock budget would make the verdict
    machine-dependent — spec D4). Torch-free."""
    result = solve(
        scenario,
        budget_s=float("inf"),
        seed=seed,
        search=SearchConfig(spread=True, max_restarts=max_restarts),
        alternatives=alternatives,
        plan_paths=False,
    )
    n_total = len(scenario.placeable_ids)
    for layout in result.layouts:
        if not go.layout_valid(layout):
            continue
        try:
            plan = plan_fill(layout, heuristic="grid", max_total_expansions=tow_max_expansions)
        except NoFeasiblePlanError:
            continue
        if _routed_plane_count(plan) == n_total:
            return True
    return False


def _build_env(scenario: Scenario):  # noqa: ANN202 (HangarFitEnv imported lazily)
    """A driven-fill HangarFitEnv for a sampled (no-GO) Scenario — the no-ground-object
    case of ``benchmark.build_scenario_env``, built from an in-memory Scenario. Torch-free
    (HangarFitEnv carries no torch)."""
    from ml.env import HangarFitEnv
    from ml.types import DifficultyConfig

    placeable = scenario.placeable_ids
    per = 120
    difficulty = DifficultyConfig(
        max_objects=len(placeable),
        per_object_step_budget=per,
        total_step_budget=per * max(1, len(placeable)),
    )
    hangar = replace(scenario.hangar, apron_depth_m=8.0)
    return HangarFitEnv(
        hangar=hangar, fleet=scenario.fleet, requested_ids=placeable, difficulty=difficulty
    )


def policy_reach_count(scenario: Scenario, policy, *, samples: int, seed: int = 0) -> int:
    """Roll ``policy`` out ``samples`` times on ``scenario`` and count the reaches under the
    SAME product-checker predicate (parked == total AND ``go.layout_valid`` AND zero swept
    intrusion). ``samples == 1`` ⇒ a single deterministic (argmax) pass; ``samples > 1`` ⇒
    stochastic sampling (seeded per sample) so the rate has variance for a CI. Needs torch
    (imported lazily)."""
    import torch

    from ml.benchmark import _verdict_from  # single-source the spec §4 reach predicate
    from ml.encoding import EncoderConfig, encode

    # #827: the encoder's ego-frame follows the policy's own architecture (single source of
    # truth), so an ego policy gets 28-wide ego tokens instead of a 24-vs-28 shape crash.
    enc = EncoderConfig(ego_centric=getattr(policy, "relative_encoder", False))
    env = _build_env(scenario)
    bodies = {**env.fleet, **env.ground_objects}
    deterministic = samples == 1
    policy.eval()
    reached = 0
    for s in range(samples):
        torch.manual_seed(seed * 100003 + s)
        obs = env.reset()
        max_swept = 0.0
        info = None
        done = False
        with torch.no_grad():
            while not done and obs.active is not None:
                obs_t = encode(obs, env.hangar, bodies, enc)
                tr = obs.active.body.effective_turn_radius_m()
                _idx, _logprob, action = policy.act(
                    obs_t, turn_radius_m=tr, deterministic=deterministic
                )
                obs, _reward, done, info = env.step(action)
                max_swept = max(max_swept, info.terms.get("hard_swept", 0.0))
        if info is None:
            continue  # a 0-step episode (no placeable bodies) never reaches
        # Build the verdict via the bench's predicate (NOT an inline copy) so the env-oracle-
        # vs-product-checker contract (#694) stays single-sourced: final_valid is the PRODUCT
        # checker on the terminal layout, never info.valid.
        final_valid = go.layout_valid(env._layout()) if done else False
        verdict = _verdict_from(
            parked=info.placed,
            total=info.total,
            done=done,
            final_valid=final_valid,
            max_swept=max_swept,
        )
        if verdict.reached:
            reached += 1
    return reached


# ── population aggregation ───────────────────────────────────────────────────


def rrmc_population_rates(
    population: Sequence[SampledScenario],
    *,
    alternatives: int,
    max_restarts: int,
    tow_max_expansions: int,
    seed: int = 0,
) -> dict[str, ReachRate]:
    """RR-MC reach-rate ± CI per kind over the population (one Bernoulli trial per scenario)."""
    pairs = [
        (
            ss.kind,
            rrmc_reach_multi(
                ss.scenario,
                alternatives=alternatives,
                max_restarts=max_restarts,
                tow_max_expansions=tow_max_expansions,
                seed=seed,
            ),
        )
        for ss in population
    ]
    return aggregate(pairs)


def policy_population_rates(
    population: Sequence[SampledScenario], policy, *, samples: int, seed: int = 0
) -> dict[str, ReachRate]:
    """Policy reach-rate ± CI per kind: each scenario contributes ``samples`` Bernoulli
    trials (stochastic rollouts) to its kind's rate. The per-scenario seed folds in the
    scenario index, so two scenarios with an identical fleet subset still draw independent
    rollout noise (while staying fully reproducible in ``seed``)."""
    pairs: list[tuple[str, bool]] = []
    for idx, ss in enumerate(population):
        r = policy_reach_count(ss.scenario, policy, samples=samples, seed=seed + idx)
        pairs.extend((ss.kind, True) for _ in range(r))
        pairs.extend((ss.kind, False) for _ in range(samples - r))
    return aggregate(pairs)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _print_rates(title: str, rates: dict[str, ReachRate]) -> None:
    print(f"\n{title}")
    print(f"  {'kind':10}  {'reach-rate':>10}  {'95% CI':>16}  {'n':>5}")
    for kind, r in rates.items():
        ci = f"[{r.ci_lo:.2f}, {r.ci_hi:.2f}]"
        print(f"  {kind:10}  {r.rate:>10.3f}  {ci:>16}  {r.n:>5}")


def _print_dominance(v: DominanceVerdict) -> None:
    """Print the ADR-0028 trigger-#1 verdict — the masquerade-proof reach-not-beat decision."""
    print(f"\nADR-0028 trigger #1 — reach-not-beat (witness-absent tau={v.tau})")
    if not v.exercised:
        print(
            "  ⚠ NOT EXERCISED — no kind here has RR-MC reach-rate CI upper bound\n"
            "    <= tau, so there is no RR-MC-misses kind to contest. A valid\n"
            "    witness-absent kind must ALSO carry a feasibility witness (a valid\n"
            "    layout proven to exist that the deployed RR-MC misses) — e.g. a\n"
            "    hand-authored packing or a big-budget `solve` result. Do NOT\n"
            "    manufacture one by tightening the hangar past feasibility:\n"
            "    over-capacity => RR-MC reaches 0 because the layout is INFEASIBLE,\n"
            "    not missed, and a policy 'loss' there is vacuous (#832 retract, #835)."
        )
        return
    print(f"  RR-MC-misses kinds (ci_hi <= tau): {', '.join(v.witness_absent)}")
    print(
        "  ⚠ chartered ground ONLY if each carries a feasibility witness (a valid\n"
        "    layout proven to exist). RR-MC reach 0 on an over-capacity-infeasible\n"
        "    kind is not a miss, so a policy 'loss' there is vacuous — verify\n"
        "    feasibility before trusting this verdict (#835)."
    )
    print(f"  {'kind':10}  {'RR-MC ci_hi':>12}  {'policy ci_lo':>13}  {'beats?':>7}")
    for kd in v.per_kind:
        pol = f"{kd.policy_ci_lo:.3f}" if kd.policy_covered else "(n/a)"
        beats = "YES" if kd.policy_beats else "no"
        print(f"  {kd.kind:10}  {kd.rrmc_ci_hi:>12.3f}  {pol:>13}  {beats:>7}")
    decision = (
        "MET — RE-OPEN ADR-0028 (trigger #1)"
        if v.reopen
        else "NOT MET (trustworthy only if the contested kinds are feasibility-witnessed — #835)"
    )
    print(f"  ==> TRIGGER #1: {decision}")


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Statistical reach-rate harness (#711): reach-rate ± CI over a sampled "
        "population, for multi-alternative RR-MC and (optionally) a trained policy."
    )
    p.add_argument("--scenarios", type=int, default=8, help="population size N (default: 8)")
    p.add_argument("--k-min", type=int, default=2, help="min aircraft per scenario (default: 2)")
    p.add_argument("--k-max", type=int, default=4, help="max aircraft per scenario (default: 4)")
    p.add_argument(
        "--distinct",
        action="store_true",
        help="draw DISTINCT fleet subsets and cap N at the available distinct count — avoids "
        "RR-MC pseudo-replication at high k / few distinct subsets (e.g. C(9,8)=9)",
    )
    p.add_argument("--seed", type=int, default=0, help="population + RR-MC seed (default: 0)")
    p.add_argument("--alternatives", type=int, default=4, help="RR-MC alternatives (default: 4)")
    p.add_argument("--max-restarts", type=int, default=16, help="RR-MC restarts (default: 16)")
    p.add_argument(
        "--tow-max-expansions", type=int, default=8000, help="tow budget (default: 8000)"
    )
    p.add_argument("--fleet", default="data/fleet.yaml")
    p.add_argument("--hangar", default="tests/fixtures/test_hangar_large.yaml")
    p.add_argument(
        "--policy",
        default=None,
        help="path to a torch state_dict .pt — adds the policy reach-rate arm ([train] extra)",
    )
    p.add_argument(
        "--samples", type=int, default=8, help="stochastic rollouts per scenario, policy arm"
    )
    # Policy architecture (must match the checkpoint's), mirroring ml.eval's flags so a
    # checkpoint trained at a non-default size loads instead of failing load_state_dict.
    p.add_argument("--d-model", type=int, default=128)
    p.add_argument("--n-layers", type=int, default=2)
    p.add_argument("--n-heads", type=int, default=4)
    p.add_argument(
        "--relative-encoder",
        action="store_true",
        help="load the policy with the #827 ego-centric encoder (must match the checkpoint)",
    )
    p.add_argument(
        "--witness-absent-tau",
        type=float,
        default=0.15,
        help="a kind is witness-absent when its RR-MC reach-rate Wilson ci_hi <= tau "
        "(default: 0.15) — the only kinds on which trigger #1 can be met",
    )
    args = p.parse_args(argv)

    population = sample_population(
        n=args.scenarios,
        k_min=args.k_min,
        k_max=args.k_max,
        seed=args.seed,
        fleet_path=args.fleet,
        hangar_path=args.hangar,
        distinct=args.distinct,
    )
    capped = args.distinct and len(population) < args.scenarios
    print(
        f"population: {len(population)} fill scenarios (k {args.k_min}..{args.k_max}) on "
        f"{args.hangar}, seed {args.seed}"
        + (
            f" [distinct: capped from {args.scenarios} to the {len(population)} available subsets]"
            if capped
            else (" [distinct]" if args.distinct else "")
        )
    )
    rrmc = rrmc_population_rates(
        population,
        alternatives=args.alternatives,
        max_restarts=args.max_restarts,
        tow_max_expansions=args.tow_max_expansions,
        seed=args.seed,
    )
    _print_rates(
        f"RR-MC reach-rate (alternatives={args.alternatives}, restarts={args.max_restarts})", rrmc
    )
    wa = witness_absent_kinds(rrmc, tau=args.witness_absent_tau)
    print(f"\nRR-MC-misses kinds (ci_hi <= {args.witness_absent_tau}): {wa or '(none)'}")
    if wa:
        print(
            "  ⚠ witness-absent ONLY if each carries a feasibility witness (a valid layout proven "
            "to exist); an over-capacity-infeasible kind is not a miss (#835)."
        )
    if args.policy is not None:
        from ml.eval import load_policy

        policy = load_policy(
            args.policy,
            policy_kwargs={
                "d_model": args.d_model,
                "n_layers": args.n_layers,
                "n_heads": args.n_heads,
                "relative_encoder": args.relative_encoder,
            },
        )
        pol = policy_population_rates(population, policy, samples=args.samples, seed=args.seed)
        _print_rates(f"policy reach-rate ({args.samples} samples/scenario)", pol)
        _print_dominance(dominance_verdict(rrmc, pol, tau=args.witness_absent_tau))


if __name__ == "__main__":
    main()
