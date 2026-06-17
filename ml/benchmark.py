"""Reach-not-beat eval benchmark machinery (sub-project #4c-i, epic #607). TORCH-FREE:
the scenario set, the valid+routable-by-construction success predicate, the RR-MC
reach-oracle (via the PUBLIC hangarfit solve/plan_fill — no bench import), and the
committed-baseline I/O. The torch policy-rollout half lives in ml/eval.py.

Spec: docs/superpowers/specs/2026-06-17-learned-backend-eval-benchmark-design.md.
Keep this module import-light (NO torch, NO ml.policy/ml.ppo) so the no-torch CI lane
loads it cleanly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hangarfit.loader import load_layout
from hangarfit.models import Layout
from ml import geometry_oracle as go
from ml.types import StepInfo

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


def _verdict_from(info: StepInfo, *, done: bool, max_swept: float) -> ReachVerdict:
    """The valid + routable-by-construction predicate (spec §4), shared by score_episode
    (explicit actions) and ml.eval.policy_reach (policy actions). `reached` iff every
    requested object was parked, the final layout is valid, AND no drive-in leg intruded."""
    parked_all = done and info.placed == info.total
    reached = parked_all and info.valid and max_swept == 0.0
    if reached:
        reason = "reached"
    elif not parked_all:
        reason = f"only {info.placed}/{info.total} parked"
    elif not info.valid:
        reason = "invalid final layout"
    else:
        reason = "swept-path intrusion (not routable-by-construction)"
    return ReachVerdict(
        reached=reached,
        parked=info.placed,
        total=info.total,
        final_valid=info.valid,
        max_swept_intrusion=max_swept,
        reason=reason,
    )


def _layout_valid(layout: Layout) -> bool:
    """Whole-layout validity matching env._layout_valid + the deterministic checker:
    no part overlap, no out-of-bounds/notch/apron intrusion by any placed body, and no
    Caddy hard-door egress violation. Reused by witness_valid and rrmc_reach so the
    policy and RR-MC sides apply the IDENTICAL predicate."""
    if go.overlap_area_m2(layout) > 0.0:
        return False
    if go.egress_blocked(layout):
        return False
    bodies = {**layout.fleet, **layout.ground_objects}
    placements = (*layout.placements, *layout.ground_object_placements)
    return all(
        go.intrusion_area_m2(bodies[p.plane_id], p, layout.hangar) == 0.0 for p in placements
    )


def witness_valid(scenario: BenchScenario) -> bool:
    """Load the committed witness layout and prove it is valid+routable-by-existence
    (the never-rots reachability proof). Raises if the scenario has no witness."""
    if scenario.witness_path is None:
        raise ValueError(f"witness_valid: scenario {scenario.name!r} has no witness_path")
    layout = load_layout(_ROOT / scenario.witness_path)
    return _layout_valid(layout)
