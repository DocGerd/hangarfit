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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from hangarfit.loader import load_fleet, load_hangar
from hangarfit.models import Scenario, SearchConfig
from hangarfit.solver import solve
from hangarfit.towplanner import NoFeasiblePlanError, plan_fill
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
) -> list[SampledScenario]:
    """Sample ``n`` aircraft-subset fill scenarios on a fixed hangar — the reach-rate
    population. Each draws ``k ∈ [k_min, k_max]`` aircraft from the fleet pool (no ground
    objects, no maintenance, no pins). Deterministic in ``seed`` (its sole RNG), so the
    population — and thus the baseline — is reproducible."""
    if not 1 <= k_min <= k_max:
        raise ValueError(f"sample_population: need 1 <= k_min <= k_max, got {k_min}, {k_max}")
    fleet = load_fleet(str(_ROOT / fleet_path))
    hangar = load_hangar(str(_ROOT / hangar_path), fleet=fleet)
    pool = sorted(fleet)  # aircraft ids, sorted for a deterministic draw
    if k_max > len(pool):
        raise ValueError(f"sample_population: k_max {k_max} exceeds fleet pool size {len(pool)}")
    rng = random.Random(seed)
    out: list[SampledScenario] = []
    for i in range(n):
        k = rng.randint(k_min, k_max)
        subset = tuple(sorted(rng.sample(pool, k)))
        sc = Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=subset,
            constraints={},
            ground_object_defs={},
            region_preferences={},
        )
        out.append(SampledScenario(name=f"sample{i:03d}_k{k}", kind=f"k{k}", scenario=sc))
    return out


# ── reach predicates (the #695 product-checker predicate, both arms) ─────────


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
        if sum(1 for m in plan.moves if m.path is not None) == n_total:
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


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Statistical reach-rate harness (#711): reach-rate ± CI over a sampled "
        "population, for multi-alternative RR-MC and (optionally) a trained policy."
    )
    p.add_argument("--scenarios", type=int, default=8, help="population size N (default: 8)")
    p.add_argument("--k-min", type=int, default=2, help="min aircraft per scenario (default: 2)")
    p.add_argument("--k-max", type=int, default=4, help="max aircraft per scenario (default: 4)")
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
    args = p.parse_args(argv)

    population = sample_population(
        n=args.scenarios,
        k_min=args.k_min,
        k_max=args.k_max,
        seed=args.seed,
        fleet_path=args.fleet,
        hangar_path=args.hangar,
    )
    print(
        f"population: {len(population)} fill scenarios (k {args.k_min}..{args.k_max}) on "
        f"{args.hangar}, seed {args.seed}"
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
    if args.policy is not None:
        from ml.eval import load_policy

        policy = load_policy(
            args.policy,
            policy_kwargs={
                "d_model": args.d_model,
                "n_layers": args.n_layers,
                "n_heads": args.n_heads,
            },
        )
        pol = policy_population_rates(population, policy, samples=args.samples, seed=args.seed)
        _print_rates(f"policy reach-rate ({args.samples} samples/scenario)", pol)


if __name__ == "__main__":
    main()
