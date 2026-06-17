"""Reach-not-beat eval benchmark machinery (sub-project #4c-i, epic #607). TORCH-FREE:
the scenario set, the valid+routable-by-construction success predicate, the RR-MC
reach-oracle (via the PUBLIC hangarfit solve/plan_fill — no bench import), and the
committed-baseline I/O. The torch policy-rollout half lives in ml/eval.py.

Spec: docs/superpowers/specs/2026-06-17-learned-backend-eval-benchmark-design.md.
Keep this module import-light (NO torch, NO ml.policy/ml.ppo) so the no-torch CI lane
loads it cleanly."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from hangarfit.collisions import check
from hangarfit.loader import load_layout, load_scenario
from hangarfit.models import Layout, SearchConfig
from hangarfit.solver import solve
from hangarfit.towplanner import NoFeasiblePlanError, plan_fill
from ml import geometry_oracle as go
from ml.env import HangarFitEnv
from ml.types import Action, DifficultyConfig, StepInfo

_ROOT = Path(__file__).resolve().parent.parent  # repo root (ml/ sits at the root)


@dataclass(frozen=True, slots=True)
class ReachVerdict:
    """Did an agent (policy or RR-MC, via score_episode) reach a valid+routable layout?"""

    reached: bool
    parked: int
    total: int
    final_valid: bool
    max_swept_intrusion: float
    reason: str


@dataclass(frozen=True, slots=True)
class RrmcVerdict:
    """The RR-MC->tow pipeline's verdict on a scenario (recorded offline)."""

    reached: bool
    n_routed: int
    n_total: int
    status: str


@dataclass(frozen=True, slots=True)
class BenchScenario:
    """One frozen benchmark scenario. `witness_path` is required for anchors (the
    committed reachability proof) and None for controls (RR-MC reaching them IS the
    proof). Budgets are PRE-REGISTERED (frozen before measurement — spec D4)."""

    name: str
    scenario_path: str  # repo-relative solver-input YAML
    kind: Literal["anchor", "control"]
    max_restarts: int
    tow_max_expansions: int
    seed: int
    witness_path: str | None = None  # repo-relative witness layout; None only for controls

    def __post_init__(self) -> None:
        if self.kind == "anchor" and self.witness_path is None:
            raise ValueError(f"BenchScenario {self.name!r}: an anchor requires a witness_path")
        if self.max_restarts < 1:
            raise ValueError(f"BenchScenario {self.name!r}: max_restarts must be >= 1")
        if self.tow_max_expansions < 1:
            raise ValueError(f"BenchScenario {self.name!r}: tow_max_expansions must be >= 1")


def _verdict_from(
    *, parked: int, total: int, done: bool, final_valid: bool, max_swept: float
) -> ReachVerdict:
    """The valid + routable-by-construction predicate (spec §4). `final_valid` is computed
    by the caller via `_layout_valid` (the product checker), NOT the env oracle."""
    parked_all = done and parked == total
    reached = parked_all and final_valid and max_swept == 0.0
    if reached:
        reason = "reached"
    elif not parked_all:
        reason = f"only {parked}/{total} parked"
    elif not final_valid:
        reason = "invalid final layout"
    else:
        reason = "swept-path intrusion (not routable-by-construction)"
    return ReachVerdict(
        reached=reached,
        parked=parked,
        total=total,
        final_valid=final_valid,
        max_swept_intrusion=max_swept,
        reason=reason,
    )


def _layout_valid(layout: Layout) -> bool:
    """Valid per the PRODUCT deterministic checker (the spec's 'prime directive' final
    gate, == `hangarfit check`): collisions.check reports no conflicts (overlap + hangar
    bounds/notch + CONDITIONAL maintenance bay + ground-obstacle keep-outs) AND no Caddy
    hard-door egress violation (ADR-0026). Deliberately NOT the env oracle's
    `intrusion_area_m2`, which over-strictly enforces an INERT placeholder maintenance bay
    (issue #694) — the herrenteich bay is explicitly inert, so layout_full is valid here.
    Used identically by witness_valid, rrmc_reach, and the policy scorer so all sides are
    judged apples-to-apples."""
    return not check(layout).conflicts and not go.egress_blocked(layout)


def witness_valid(scenario: BenchScenario) -> bool:
    """Load the committed witness layout and prove it is valid+routable-by-existence
    (the never-rots reachability proof). Raises if the scenario has no witness."""
    if scenario.witness_path is None:
        raise ValueError(f"witness_valid: scenario {scenario.name!r} has no witness_path")
    layout = load_layout(_ROOT / scenario.witness_path)
    return _layout_valid(layout)


# Pre-registered RR-MC budgets — FROZEN before measurement (spec D4). Do NOT retune
# after seeing baseline reach RESULTS: that would silently make the comparison circular.
# 64 anchor restarts == the control budget: a clean symmetric pre-registration ("same
# 64-restart budget; RR-MC reaches the 3-plane demo, misses all three dense fills"). The
# value was calibrated on RR-MC *runtime* only (≈10 s/restart on the herrenteich anchors,
# measured before recording), NOT on reach outcomes — the anchors find 0 valid layouts
# robustly even at 16 restarts, so 64 is a fair "RR-MC tried hard"; 200 was unrunnable
# (≈30–50 min/anchor) with no change to the (missed) verdict.
_ANCHOR_RESTARTS = 64
_ANCHOR_TOW_EXPANSIONS = 16_000
_CONTROL_RESTARTS = 64
_CONTROL_TOW_EXPANSIONS = 8_000
_SEED = 0

BENCH_SET: tuple[BenchScenario, ...] = (
    BenchScenario(
        name="herrenteich_all8",
        scenario_path="examples/herrenteich/scenario.yaml",
        witness_path="examples/herrenteich/layout.yaml",
        kind="anchor",
        max_restarts=_ANCHOR_RESTARTS,
        tow_max_expansions=_ANCHOR_TOW_EXPANSIONS,
        seed=_SEED,
    ),
    BenchScenario(
        name="herrenteich_today",
        scenario_path="examples/herrenteich/scenario_today.yaml",
        witness_path="examples/herrenteich/layout_today.yaml",
        kind="anchor",
        max_restarts=_ANCHOR_RESTARTS,
        tow_max_expansions=_ANCHOR_TOW_EXPANSIONS,
        seed=_SEED,
    ),
    BenchScenario(
        name="herrenteich_full",
        scenario_path="examples/herrenteich/scenario_full.yaml",
        witness_path="examples/herrenteich/layout_full.yaml",
        kind="anchor",
        max_restarts=_ANCHOR_RESTARTS,
        tow_max_expansions=_ANCHOR_TOW_EXPANSIONS,
        seed=_SEED,
    ),
    # GO-free control: RR-MC routes it (reachability proof) AND it is the policy-rollout
    # control for 4c-i (no fixed obstacle -> build_scenario_env accepts it).
    BenchScenario(
        name="herrenteich_demo",
        scenario_path="examples/herrenteich/scenario_demo.yaml",
        witness_path=None,
        kind="control",
        max_restarts=_CONTROL_RESTARTS,
        tow_max_expansions=_CONTROL_TOW_EXPANSIONS,
        seed=_SEED,
    ),
)


def build_scenario_env(scenario: BenchScenario) -> HangarFitEnv:
    """Build a HangarFitEnv for a scenario's MOVABLE bodies (aircraft + placed-routed
    movers), with an apron for drive-in. RAISES NotImplementedError if the scenario carries
    any fixed obstacle — env pre-placement of immovable keep-outs is deferred to 4c-ii, and
    silently dropping the keep-out would score the policy on an easier scenario than RR-MC
    faces (spec §5.5/D11)."""
    sc = load_scenario(_ROOT / scenario.scenario_path)
    # Detect fixed obstacles among the scenario's ACTIVE ground objects by object_class,
    # NOT via fixed_obstacle_placements: a class-`fixed_obstacle` listed in the scenario's
    # ground_objects but WITHOUT a placement entry would otherwise slip past the gate, land
    # in the env un-queued, and be silently absent from scoring (the silent-keep-out-drop
    # the gate exists to prevent). Scan sc.ground_objects (the active id tuple), not the
    # catalog-merged ground_object_defs — the latter always carries every catalog def
    # (e.g. the fuel trailer) even for a scenario that doesn't use it.
    fixed = [
        gid
        for gid in sc.ground_objects
        if sc.ground_object_defs[gid].object_class == "fixed_obstacle"
    ]
    if fixed:
        raise NotImplementedError(
            f"build_scenario_env: scenario {scenario.name!r} carries fixed obstacle(s) {fixed}; "
            f"the env cannot yet pre-place immovable keep-outs (deferred to #607 sub-project "
            f"4c-ii). Use a ground-object-free scenario for the policy rollout."
        )
    placeable = sc.placeable_ids
    # Pass only the MOVER defs (placed-routed movers), not the whole catalog-merged
    # ``ground_object_defs``: a GO-free scenario must yield an env with NO ground objects,
    # and the env should carry exactly the bodies it can drive.
    movers = {gid: sc.ground_object_defs[gid] for gid in sc.mover_ids}
    per_object = 120
    difficulty = DifficultyConfig(
        max_objects=len(placeable),
        per_object_step_budget=per_object,
        total_step_budget=per_object * max(1, len(placeable)),
    )
    hangar = replace(sc.hangar, apron_depth_m=8.0)
    return HangarFitEnv(
        hangar=hangar,
        fleet=sc.fleet,
        requested_ids=placeable,
        ground_objects=movers,
        difficulty=difficulty,
    )


def score_episode(env: HangarFitEnv, actions: Sequence[Action]) -> ReachVerdict:
    """Reset `env`, replay an explicit action sequence, and apply the success predicate
    (spec §4). Torch-free — the test/RR-MC path. final_valid is computed by the PRODUCT
    checker `_layout_valid` on the env's terminal layout (env._layout()), NOT the env's
    oracle-based StepInfo.valid (#694). ml.eval.policy_reach runs the same loop with
    policy-chosen actions and reuses _verdict_from."""
    env.reset()
    max_swept = 0.0
    info: StepInfo | None = None
    done = False
    for action in actions:
        if done:
            break
        _obs, _reward, done, info = env.step(action)
        max_swept = max(max_swept, info.terms.get("hard_swept", 0.0))
    if info is None:
        raise ValueError("score_episode: empty action sequence")
    final_valid = _layout_valid(env._layout()) if done else False
    return _verdict_from(
        parked=info.placed,
        total=info.total,
        done=done,
        final_valid=final_valid,
        max_swept=max_swept,
    )


_BASELINE_PATH = _ROOT / "tests/fixtures/ml/bench_baseline.json"


def rrmc_reach(scenario: BenchScenario) -> RrmcVerdict:
    """Run the RR-MC -> tow pipeline on `scenario` at its pinned budget and apply the SAME
    valid+routable predicate as the policy side (_layout_valid, the product checker). OFFLINE/
    dev-only (RR-MC is slow; CI reads the committed fixture). Mirrors bench/harness's
    _solve_placement/_route_layout via the PUBLIC solve/plan_fill (no bench import).
    `budget_s=inf` so max_restarts is the only bound — the 30 s default would make 'missed'
    machine-dependent (spec D4). 'Routable' = plan_fill returns without NoFeasiblePlanError
    (it raises when a body can't be routed) AND every placeable body got a move."""
    sc = load_scenario(_ROOT / scenario.scenario_path)
    n_total = len(sc.placeable_ids)
    result = solve(
        sc,
        budget_s=float("inf"),
        seed=scenario.seed,
        search=SearchConfig(spread=True, max_restarts=scenario.max_restarts),
        plan_paths=False,
    )
    if not result.layouts:
        return RrmcVerdict(reached=False, n_routed=0, n_total=n_total, status=result.status)
    layout = result.layouts[0]
    if not _layout_valid(layout):
        return RrmcVerdict(reached=False, n_routed=0, n_total=n_total, status="invalid")
    try:
        plan = plan_fill(layout, heuristic="grid", max_total_expansions=scenario.tow_max_expansions)
    except NoFeasiblePlanError:
        return RrmcVerdict(reached=False, n_routed=0, n_total=n_total, status="unroutable")
    n_routed = len(plan.moves)
    return RrmcVerdict(
        reached=(n_routed == n_total), n_routed=n_routed, n_total=n_total, status=result.status
    )


def load_baseline() -> dict[str, dict[str, object]]:
    """Read the committed RR-MC baseline fixture into {scenario_name: record}."""
    with _BASELINE_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {row["name"]: row for row in data["scenarios"]}


def record_baseline(*, repo_sha: str, recorded_at: str) -> None:
    """Re-derive every scenario's RR-MC verdict and WRITE the committed fixture. OFFLINE
    only (slow). repo_sha + recorded_at are passed in (the module is RNG/clock-free)."""
    rows = []
    for s in BENCH_SET:
        v = rrmc_reach(s)
        rows.append(
            {
                "name": s.name,
                "reached": v.reached,
                "n_routed": v.n_routed,
                "n_total": v.n_total,
                "status": v.status,
                "max_restarts": s.max_restarts,
                "tow_max_expansions": s.tow_max_expansions,
                "seed": s.seed,
                "repo_sha": repo_sha,
                "recorded_at": recorded_at,
            }
        )
    _BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _BASELINE_PATH.open("w", encoding="utf-8") as fh:
        json.dump({"scenarios": rows}, fh, indent=2)
        fh.write("\n")


def _main(argv: Sequence[str] | None = None) -> None:
    import argparse
    import datetime
    import subprocess

    parser = argparse.ArgumentParser(description="Eval benchmark (RR-MC baseline recorder).")
    parser.add_argument(
        "--record",
        action="store_true",
        help="re-derive + write the baseline fixture (slow, offline)",
    )
    args = parser.parse_args(argv)
    if args.record:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        record_baseline(repo_sha=sha, recorded_at=now)
        print(f"wrote {_BASELINE_PATH} @ {sha}")
    else:
        parser.error("nothing to do; pass --record to regenerate the baseline fixture")


if __name__ == "__main__":
    _main()
