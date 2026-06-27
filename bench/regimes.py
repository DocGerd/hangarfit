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
HERRENTEICH = REPO_ROOT / "examples" / "herrenteich"


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

    A regime drives **exactly one** of two modes (enforced in ``__post_init__``):

    * ``scenario`` — *solve-then-route*: ``solve()`` finds the layout(s), which are
      then tow-routed. The original mode (the solve-feasible regimes).
    * ``layout`` — *route a pre-built WITNESS layout directly*, skipping the solve
      (#667 Rung B). This is for a statically-valid layout that ``solve`` **cannot
      reproduce** — the real Herrenteich all-8 (``examples/herrenteich/scenario.yaml``
      header): RR-MC won't find the dense nest, so a solve-then-route regime would
      measure placement-failure, not the *routing ceiling*. The witness layout is
      loaded and routed as-is, so ``placement`` is skipped (``placement_s == 0.0``,
      ``restarts_done == 0``) and ``seed``/``max_restarts``/``spread`` are unused.
    """

    key: str
    description: str
    scenario: Path | None = None
    layout: Path | None = None
    seed: int = 1
    max_restarts: int = 0
    spread: bool = True
    n_planes: int = 0
    alternatives: int = 1
    tow_heuristic: Literal["euclidean", "grid"] = "grid"
    tow_max_expansions: int | None = None
    tow_max_total_expansions: int | None = None
    heavy: bool = False
    apron_depth: float | Literal["auto"] = 0.0

    def __post_init__(self) -> None:
        # Exactly one routing source: a typo'd / half-specified regime must fail
        # loudly here, never silently route nothing (silent-failure guard).
        if (self.scenario is None) == (self.layout is None):
            raise ValueError(
                f"regime {self.key!r}: exactly one of `scenario` or `layout` must "
                "be set (`scenario` ⇒ solve-then-route; `layout` ⇒ route a "
                "pre-built witness layout directly)"
            )


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
        # parts²-scaling guard (#547, from spike #540). The all-nine fill maximizes
        # part-pairs, so the O(planes²·parts²) collision sweep is heaviest here as
        # the parts model grows (the empennage #518–#520 already added 2 parts per
        # aircraft). Unlike full_nine_spread_on (heavy, characterises 9-plane
        # ROUTING), this is a FAST regime that characterises 9-plane PLACEMENT: a
        # tiny global tow cap makes routing bail fast (NoFeasiblePlanError ⇒ no
        # committed arc ⇒ paths vacuously valid + deterministic) so placement
        # dominates the measured wall-clock. Binds on max_restarts (8).
        key="full_nine_placement",
        description="9 planes, large hangar, spread ON — placement parts²-scaling guard",
        scenario=FIXTURES / "solve_all_nine_large_hangar.yaml",
        seed=1,
        max_restarts=8,
        spread=True,
        n_planes=9,
        tow_max_total_expansions=300,
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
    # ── #667 Rung B: real-Herrenteich routing-ceiling WITNESS regimes ─────────
    # The objective baseline every later #667 rung (C/D/E) is graded against.
    # These route the KNOWN-VALID hand-authored witness layouts directly (no
    # solve — `solve` provably cannot reproduce the dense all-8 nest), so they
    # measure the *routing* ceiling, not placement. `plan_fill` is all-or-nothing
    # (a full plan or a `NoFeasiblePlanError` naming the deepest unplaceable
    # body), so the baseline is binary: does the dense fill route? Measured
    # 2026-06-27: it does NOT — at the 8000 global cap (and at 16000, the solve
    # default) both witnesses EXHAUST the budget grinding the nest and bail
    # (deepest-unplaced `zlin_savage`). The cap bounds the un-routable disprove;
    # 8000 matches `full_nine_spread_on` (~66–68 s local). A partial routed-body
    # COUNT is Rung C's reverse-teardown probe, not this binary baseline. heavy ⇒
    # excluded from the default fast `--gate` (the multi-minute route never runs
    # in CI). Move-aside (Rung E) must flip the verdict to routed.
    Regime(
        key="herrenteich_all_eight",
        description="Real Herrenteich all-8 WITNESS layout — #667 routing-ceiling baseline",
        layout=HERRENTEICH / "layout.yaml",
        n_planes=8,
        tow_max_total_expansions=8000,
        heavy=True,
    ),
    Regime(
        key="herrenteich_today",
        description="Real Herrenteich today (all-8 + Fuji = 9) WITNESS — #667 routing baseline",
        layout=HERRENTEICH / "layout_today.yaml",
        n_planes=9,
        tow_max_total_expansions=8000,
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
