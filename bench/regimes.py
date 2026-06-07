"""Benchmark regimes for the solve→tow profiling harness (#381).

Each :class:`Regime` is a fixed-seed, bounded scenario spanning a corner of the
performance space the v0.11.0 roadmap cares about: trivial / roomy-multi /
tight-placeholder, crossed with spread-on vs spread-off.

Two deliberate reproducibility choices:

* **Bind on ``max_restarts``, not the wall-clock ``budget_s``.** Fixing the
  restart count makes the *work* deterministic, so wall-clock numbers are
  comparable across runs and machines (the same reason ADR-0003 scopes the
  determinism contract to ``max_restarts``). A wall-clock budget would let the
  achieved restart count drift under CPU load and make the numbers noise.
* **An optional global ``tow_max_total_expansions`` cap.** ``solve()`` forwards
  only the *per-plane* tow budget, so an un-routable fill would run to the
  module's 16000-expansion global default (~hundreds of seconds). The harness
  routes via a direct ``plan_fill`` call (see :mod:`bench.harness`) so this cap
  bounds the "gives-up" failure mode and keeps the heavy regimes affordable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


@dataclass(frozen=True)
class Regime:
    """One fixed-seed, bounded benchmark point.

    ``tow_max_expansions`` is the per-plane Hybrid-A* budget (``plan_fill``'s
    ``max_expansions``); ``tow_max_total_expansions`` is the global fill cap
    (``plan_fill``'s ``max_total_expansions``). ``None`` on either means the
    ``towplanner`` module default. ``heavy`` regimes are excluded from the
    default fast set (they do substantial — sometimes un-routable — routing).

    ``apron_depth`` (#412/ADR-0021) sets the staging-apron depth applied to the
    scenario's hangar (``0`` ⇒ no apron, the pre-apron path, byte-identical;
    ``"auto"`` ⇒ fleet-derived). It enlarges the per-plane tow start set (forward
    + reverse cones × the apron y-samples) and lengthens each path, so it is the
    knob that characterises the apron's routing-cost effect (#499).
    """

    key: str
    description: str
    scenario: Path
    seed: int
    max_restarts: int
    spread: bool
    n_planes: int
    alternatives: int = 1
    tow_heuristic: Literal["euclidean", "grid"] = "grid"
    tow_max_expansions: int | None = None
    tow_max_total_expansions: int | None = None
    heavy: bool = False
    apron_depth: float | Literal["auto"] = 0.0


REGIMES: tuple[Regime, ...] = (
    Regime(
        key="trivial_single",
        description="1 plane, 30x25 m hangar — search barely does anything",
        scenario=FIXTURES / "solve_trivial_single_plane.yaml",
        seed=1,
        max_restarts=20,
        spread=True,
        n_planes=1,
    ),
    Regime(
        key="roomy_three_spread_on",
        description="3 planes, 30x25 m hangar, spread ON (the default path)",
        scenario=FIXTURES / "solve_fresh_alternatives_three.yaml",
        seed=1,
        max_restarts=30,
        spread=True,
        n_planes=3,
    ),
    Regime(
        key="roomy_three_spread_off",
        description="3 planes, 30x25 m hangar, spread OFF (--no-spread fast path)",
        scenario=FIXTURES / "solve_fresh_alternatives_three.yaml",
        seed=1,
        max_restarts=30,
        spread=False,
        n_planes=3,
    ),
    Regime(
        # Few restarts on purpose: the spread post-pass dominates placement
        # (see the #381 report), so this regime is here to characterise heavy
        # 9-plane *routing*, not to re-measure spread placement cost.
        key="full_nine_spread_on",
        description="9 planes, 30x25 m hangar — heaviest routing (multi-plane fill)",
        scenario=FIXTURES / "solve_all_nine_large_hangar.yaml",
        seed=1,
        max_restarts=4,
        spread=True,
        n_planes=9,
        tow_max_total_expansions=8000,
        heavy=True,
    ),
    Regime(
        key="tight_six_placeholder",
        description="6 planes, 25x18 m placeholder — tight, routing likely bails",
        scenario=FIXTURES / "solve_fresh_six_planes.yaml",
        seed=1,
        max_restarts=6,
        spread=True,
        n_planes=6,
        tow_max_total_expansions=4000,
        heavy=True,
    ),
    Regime(
        # Apron routing-cost characterisation (#499/ADR-0021). Same placement as
        # roomy_three_spread_on (apron is planner-only ⇒ placement unchanged); the
        # apron enlarges the tow start set (forward+reverse cones × y-samples) and
        # lengthens each path. 14 m matches the opt-in derive_apron_depth(fleet)=
        # 14.98 m over-margin and clears every plane's per-plane footprint gate with
        # room to spare, so ALL three engage the apron (the longer planes fall back
        # to the door line at a too-shallow depth like 6 m — see the #499 spike).
        key="roomy_three_apron",
        description="3 planes, 30x25 m hangar, 14 m staging apron — slide-in routing cost",
        scenario=FIXTURES / "solve_fresh_alternatives_three.yaml",
        seed=1,
        max_restarts=30,
        spread=True,
        n_planes=3,
        apron_depth=14.0,
    ),
    Regime(
        # The apron's effect on the un-routable disprove (the expensive failure
        # mode): the tight 6-plane placeholder fill with a 10 m apron. The global
        # cap bounds the bail; heavy ⇒ excluded from the gated fast set.
        key="tight_six_apron",
        description="6 planes, 25x18 m placeholder, 10 m apron — un-routable disprove cost",
        scenario=FIXTURES / "solve_fresh_six_planes.yaml",
        seed=1,
        max_restarts=6,
        spread=True,
        n_planes=6,
        tow_max_total_expansions=4000,
        apron_depth=10.0,
        heavy=True,
    ),
)


FAST_REGIMES: tuple[Regime, ...] = tuple(r for r in REGIMES if not r.heavy)


def regime_by_key(key: str) -> Regime:
    """Look up a regime by its ``key``; raise ``KeyError`` if unknown."""
    for regime in REGIMES:
        if regime.key == key:
            return regime
    raise KeyError(key)
