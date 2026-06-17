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

from hangarfit.collisions import check
from hangarfit.loader import load_layout
from hangarfit.models import Layout
from ml import geometry_oracle as go

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
