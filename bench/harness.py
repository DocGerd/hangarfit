"""Profiling/benchmark harness for the solve→tow pipeline (#381).

Splits each regime's wall-clock into **placement** (the RR-MC restart loop) vs
**routing** (the bounded Hybrid-A* tow planner), asserts the three correctness
invariants the v0.11.0 roadmap wants guarded, and produces a cProfile
attribution of where the routing time actually goes.

The three always-checkable invariants (the substrate F6/#403 turns into CI
gates):

* **VALIDITY** — every returned layout scores ``(0, 0.0)`` under
  :func:`collisions.check`.
* **PATH-VALIDITY** — every committed tow arc passes
  :func:`towplanner.path_first_conflict` at the fine ``0.05 m / 1°`` sampling,
  re-validated against the *faithful* back-first obstacle context.
* **DETERMINISM** — a second run of the same regime yields a byte-identical
  layout + plan digest (ADR-0003, ``max_restarts``-scoped).

Timing method: ``solve(plan_paths=False)`` is timed for placement; routing is
driven by a **direct** ``plan_fill`` call on each selected layout (``solve()``
forwards only the per-plane budget, so the direct call is the only way to set
the global expansion cap and bound the un-routable regimes). For a regime with
``tow_max_total_expansions=None`` (the fast set), this direct call is *exactly*
what ``solve(plan_paths=True)`` runs internally, so ``placement_s + routing_s``
is a faithful decomposition of the end-to-end wall-clock. The heavy regimes
deliberately pass a **tighter** global cap than ``solve()`` ever does (to bound
the un-routable "gives-up" case), so their ``routing_s`` and "un-routable" verdict
are harness-specific — a *lower bound* on what ``solve()`` would spend before
bailing at the 16000-expansion module default, not a reproduction of it.
"""

from __future__ import annotations

import cProfile
import io
import pstats
import time
from dataclasses import dataclass, field

from hangarfit.collisions import check as check_layout
from hangarfit.loader import load_layout, load_scenario
from hangarfit.models import Layout, Placement, Scenario, SolveResult
from hangarfit.solver import SearchConfig, solve
from hangarfit.towplanner import (
    MovesPlan,
    NoFeasiblePlanError,
    Pose,
    path_first_conflict,
    plan_fill,
)

from .regimes import FAST_REGIMES, REGIMES, Regime

# Functions whose cumulative time a routing cProfile is bucketed into. Keys are
# the human-facing stage names from #381; values are (module-substring,
# function-name) pairs matched against pstats rows. Ordered most→least specific.
# NOTE: bucket times are *cumulative* (pstats ct), so an outer stage subsumes
# the inner ones it calls — they overlap and do NOT sum to 100% of the stage
# wall (e.g. path_first_conflict ⊃ collisions.check ⊃ _parts_conflict ⊃
# polygon_overlap ⊃ aircraft_parts_world). The module-substring in each value
# matches a function by its *defining* file, so polygon_overlap and
# aircraft_parts_world key on "geometry" (where they are defined), not on the
# importing module — keying them on "collisions" would silently never match.
_ROUTING_STAGES: tuple[tuple[str, tuple[str, str]], ...] = (
    ("grid-heuristic build", ("towplanner", "_build_grid_heuristic")),
    ("Reeds-Shepp enumeration", ("towplanner", "_rs_solve_normalised")),
    ("motion-clear (fast collision)", ("towplanner", "_motion_clear")),
    ("mover bounds (in-transit)", ("towplanner", "_mover_motion_bounds_conflict")),
    ("path_first_conflict re-check", ("towplanner", "path_first_conflict")),
    ("collisions.check", ("collisions", "check")),
    ("exact parts-conflict", ("collisions", "_parts_conflict")),
    ("polygon overlap (shapely)", ("geometry", "polygon_overlap")),
    ("world-part build (shapely)", ("geometry", "aircraft_parts_world")),
)

_PLACEMENT_STAGES: tuple[tuple[str, tuple[str, str]], ...] = (
    ("descent step", ("solver", "_descent_step")),
    ("spread post-pass", ("solver", "_spread")),
    ("inter-plane energy", ("solver", "_inter_plane_energy")),
    ("collisions.check", ("collisions", "check")),
    ("exact parts-conflict", ("collisions", "_parts_conflict")),
    ("polygon overlap (shapely)", ("geometry", "polygon_overlap")),
    ("world-part build (shapely)", ("geometry", "aircraft_parts_world")),
)


@dataclass
class RouteOutcome:
    """The result of routing a single selected layout."""

    routed: bool
    routing_s: float
    plan: MovesPlan | None
    note: str = ""


@dataclass
class RegimeResult:
    """One regime's measured timing + correctness verdicts."""

    key: str
    n_planes: int
    spread: bool
    restarts_done: int
    placement_s: float
    routing_s: float
    n_layouts: int
    n_routed: int
    layouts_valid: bool
    paths_valid: bool
    deterministic: bool
    status: str
    notes: list[str] = field(default_factory=list)

    @property
    def total_s(self) -> float:
        return self.placement_s + self.routing_s


# ── scenario / search wiring ────────────────────────────────────────────────


def load_regime_scenario(regime: Regime) -> Scenario:
    """Load a regime's scenario (relative fleet/hangar refs resolve correctly).

    A non-zero ``apron_depth`` (#499/ADR-0021) is applied to the resolved hangar;
    ``0`` passes ``apron_depth=None`` so the no-apron regimes keep the original
    load path (and their numbers) byte-identical.
    """
    assert regime.scenario is not None, f"{regime.key}: not a solve regime (no scenario)"
    apron = regime.apron_depth if regime.apron_depth else None
    return load_scenario(regime.scenario, apron_depth=apron)


def load_regime_layout(regime: Regime) -> Layout:
    """Load a WITNESS regime's pre-built layout (#667 Rung B).

    Relative ``fleet:`` / ``hangar:`` refs resolve against the layout file's own
    directory; a non-zero ``apron_depth`` is applied identically to the solve
    path. The layout is routed as-is — no solve runs.
    """
    assert regime.layout is not None, f"{regime.key}: not a witness regime (no layout)"
    apron = regime.apron_depth if regime.apron_depth else None
    return load_layout(regime.layout, apron_depth=apron)


def _search_config(regime: Regime) -> SearchConfig:
    return SearchConfig(spread=regime.spread, max_restarts=regime.max_restarts)


def _solve_placement(regime: Regime, scenario: Scenario) -> SolveResult:
    """Run placement only (no tow planning). Bounded by ``max_restarts``."""
    return solve(
        scenario,
        budget_s=1_000.0,  # effectively unbounded — max_restarts is the binding gate
        alternatives=regime.alternatives,
        seed=regime.seed,
        search=_search_config(regime),
        plan_paths=False,
    )


def _route_layout(regime: Regime, layout: Layout) -> RouteOutcome:
    """Route one layout via a bounded direct ``plan_fill`` call."""
    start = time.perf_counter()
    try:
        plan = plan_fill(
            layout,
            heuristic=regime.tow_heuristic,
            max_expansions=regime.tow_max_expansions,
            max_total_expansions=regime.tow_max_total_expansions,
        )
    except NoFeasiblePlanError as exc:
        # Name the deepest unplaceable body AND the conflict detail — the detail
        # distinguishes a genuine geometric wall from a global-budget-exhaustion
        # bail (both report kind ``no_feasible_path``), which the #667 routing
        # ceiling baseline needs to interpret the wall honestly.
        return RouteOutcome(
            routed=False,
            routing_s=time.perf_counter() - start,
            plan=None,
            note=f"un-routable: {exc.plane_id} ({exc.conflict.kind}: {exc.conflict.detail})",
        )
    return RouteOutcome(routed=True, routing_s=time.perf_counter() - start, plan=plan)


# ── correctness checks ──────────────────────────────────────────────────────


def _layout_score(layout: Layout) -> tuple[int, float]:
    cr = check_layout(layout)
    return (len(cr.conflicts), cr.total_penetration_m2)


def _path_failures(plan: MovesPlan, target: Layout) -> list[str]:
    """Re-validate every move's arc against its faithful back-first context.

    Rebuilds the exact obstacle layout ``plan_fill`` saw when it committed each
    move (the already-committed planes, in execution order) and re-runs the
    fine-sampled ``path_first_conflict``. Duplicates ``plan_fill``'s internal
    safety net by design — an independent assertion that the *shipped* plan is
    collision-free, which is what a regression gate wants.
    """
    by_id = {p.plane_id: p for p in target.placements}
    placed: list[Placement] = []
    failures: list[str] = []
    for move in plan.moves:
        if move.plane_id not in by_id:
            # A placed_routed_mover ground-object move (towplanner.py): its id is
            # not an aircraft placement. Movers route LAST against the fully-placed
            # aircraft set and are not aircraft keep-outs (they live in
            # `ground_object_placements`), so they are out of scope for this
            # aircraft-arc re-validation — re-validating mover arcs is Rung E / #602
            # work. Skipping avoids both a KeyError here and corrupting `placed`
            # (an aircraft-only list) with a ground object.
            continue
        original = by_id[move.plane_id]
        if move.path is None:
            # A path-less at-rest move (a #667 hand-placed aircraft body): no arc
            # to re-validate. It is still a keep-out for later routed bodies —
            # `plan_fill` seeds `placed` with the hand-placed slots — so keep the
            # obstacle context faithful by committing it before the next move.
            placed.append(original)
            continue
        placed_layout = Layout(
            fleet=target.fleet,
            hangar=target.hangar,
            placements=tuple(placed),
            maintenance_plane=target.maintenance_plane,
        )
        conflict = path_first_conflict(
            move.path,
            target.fleet[move.plane_id],
            mover_on_carts=original.on_carts,
            placed=placed_layout,
            step_m=0.05,
            step_deg=1.0,
        )
        if conflict is not None:
            failures.append(f"{move.plane_id}:{conflict.kind}")
        placed.append(original)
    return failures


def _layout_digest(layout: Layout) -> str:
    """Order-preserving, exact-float digest of a layout's placements."""
    return "|".join(
        f"{p.plane_id},{p.x_m!r},{p.y_m!r},{p.heading_deg!r},{p.on_carts}"
        for p in layout.placements
    )


def _plan_digest(plan: MovesPlan | None) -> str:
    """Exact digest of a plan's moves (target slot + full arc shape).

    Each segment contributes its ``kind`` (L/S/R), ``gear`` (±1), and exact
    ``length_m`` plus the arc's ``turn_radius_m`` — not length alone — so two
    arcs that share a target slot and per-leg lengths but differ in steering,
    travel direction, or radius do not collapse to the same digest (which would
    let an arc-*shape* non-determinism slip through as ``det = ok``).
    """
    if plan is None:
        return "None"
    parts: list[str] = []
    for move in plan.moves:
        slot: Pose = move.target_slot
        if move.path is None:
            # Path-less at-rest move (#667 hand-placed / #601 deferred): no arc to
            # digest. Still digest the at-rest slot so a change in the hand-placed
            # pose is caught as a difference.
            parts.append(f"{move.plane_id}@{slot.x_m!r},{slot.y_m!r},{slot.heading_deg!r}<at-rest>")
            continue
        segs = ",".join(f"{s.kind}{s.gear:+d}:{s.length_m!r}" for s in move.path.segments)
        parts.append(
            f"{move.plane_id}@{slot.x_m!r},{slot.y_m!r},{slot.heading_deg!r}"
            f"r{move.path.turn_radius_m!r}[{segs}]"
        )
    return "|".join(parts)


def _result_digest(result: SolveResult, plans: list[MovesPlan | None]) -> str:
    layouts = ";".join(_layout_digest(layout) for layout in result.layouts)
    plan_digest = ";".join(_plan_digest(p) for p in plans)
    return f"{result.status}#{layouts}#{plan_digest}"


# ── top-level run ───────────────────────────────────────────────────────────


def _route_digest(outcome: RouteOutcome) -> str:
    """Determinism key for a single witness route (#667 Rung B).

    Excludes the wall-clock ``routing_s`` (which varies run-to-run); includes the
    bail note so a *different* deepest-unplaceable body across two routes — which
    would leave both plans ``None`` and so escape ``_plan_digest`` alone — is
    still caught as non-deterministic.
    """
    return f"{outcome.routed}|{outcome.note}|{_plan_digest(outcome.plan)}"


def _run_witness_regime(regime: Regime) -> RegimeResult:
    """Route a pre-built WITNESS layout directly — no solve (#667 Rung B).

    The real Herrenteich all-8 is statically valid but ``solve`` cannot reproduce
    it, so its *routing* ceiling is measured by routing the known-valid witness
    layout via ``plan_fill``. Placement is skipped entirely (``placement_s == 0.0``,
    ``restarts_done == 0``); validity / path-validity / determinism are computed on
    the single witness layout exactly as the solve path computes them.
    """
    layout = load_regime_layout(regime)

    outcome = _route_layout(regime, layout)
    notes: list[str] = [outcome.note] if outcome.note else []

    layouts_valid = _layout_score(layout) == (0, 0.0)
    path_fails = _path_failures(outcome.plan, layout) if outcome.plan is not None else []
    if path_fails:
        notes.append("path failures: " + ", ".join(path_fails))

    # Determinism: a second identical route must produce the same digest (ADR-0003).
    deterministic = _route_digest(_route_layout(regime, layout)) == _route_digest(outcome)

    return RegimeResult(
        key=regime.key,
        n_planes=regime.n_planes,
        spread=regime.spread,
        restarts_done=0,
        placement_s=0.0,
        routing_s=outcome.routing_s,
        n_layouts=1,
        n_routed=1 if outcome.routed else 0,
        layouts_valid=layouts_valid,
        paths_valid=not path_fails,
        deterministic=deterministic,
        status="witness",
        notes=notes,
    )


def run_regime(regime: Regime) -> RegimeResult:
    """Measure one regime end-to-end: timing + the three correctness verdicts."""
    if regime.layout is not None:
        return _run_witness_regime(regime)
    scenario = load_regime_scenario(regime)

    start = time.perf_counter()
    result = _solve_placement(regime, scenario)
    placement_s = time.perf_counter() - start

    routing_s = 0.0
    plans: list[MovesPlan | None] = []
    n_routed = 0
    notes: list[str] = []
    for layout in result.layouts:
        outcome = _route_layout(regime, layout)
        routing_s += outcome.routing_s
        plans.append(outcome.plan)
        if outcome.routed:
            n_routed += 1
        elif outcome.note:
            notes.append(outcome.note)

    layouts_valid = all(_layout_score(layout) == (0, 0.0) for layout in result.layouts)
    path_fails: list[str] = []
    for layout, plan in zip(result.layouts, plans, strict=True):
        if plan is not None:
            path_fails.extend(_path_failures(plan, layout))
    if path_fails:
        notes.append("path failures: " + ", ".join(path_fails))

    # Determinism: a second identical run must produce a byte-identical digest.
    digest1 = _result_digest(result, plans)
    result2 = _solve_placement(regime, scenario)
    plans2 = [_route_layout(regime, layout).plan for layout in result2.layouts]
    deterministic = _result_digest(result2, plans2) == digest1

    return RegimeResult(
        key=regime.key,
        n_planes=regime.n_planes,
        spread=regime.spread,
        restarts_done=result.diagnostics.restarts_attempted,
        placement_s=placement_s,
        routing_s=routing_s,
        n_layouts=len(result.layouts),
        n_routed=n_routed,
        layouts_valid=layouts_valid,
        paths_valid=not path_fails,
        deterministic=deterministic,
        status=result.status,
        notes=notes,
    )


# ── cProfile attribution ────────────────────────────────────────────────────


def _bucket_profile(
    stats: pstats.Stats, stages: tuple[tuple[str, tuple[str, str]], ...]
) -> list[tuple[str, float, int]]:
    """Extract (stage, cumulative_seconds, ncalls) for each named stage."""
    rows: list[tuple[str, float, int]] = []
    raw = stats.stats  # type: ignore[attr-defined]  # {(file, line, func): (cc, nc, tt, ct, callers)}
    for stage, (mod_sub, func) in stages:
        cum = 0.0
        ncalls = 0
        for key, entry in raw.items():
            filename, fname = key[0], key[2]
            if fname == func and mod_sub in filename:
                cum += entry[3]  # ct = cumulative time
                ncalls += entry[1]  # nc = number of calls
        rows.append((stage, cum, ncalls))
    return rows


def profile_routing(regime: Regime) -> tuple[float, list[tuple[str, float, int]], str]:
    """cProfile a single ``plan_fill`` on the regime's first selected layout.

    Returns (total_routing_seconds, stage_buckets, pstats_top_text).
    """
    if regime.layout is not None:
        # #667 Rung B: witness regimes route a pre-built layout directly (no solve).
        layout = load_regime_layout(regime)
    else:
        scenario = load_regime_scenario(regime)
        result = _solve_placement(regime, scenario)
        if not result.layouts:
            return (0.0, [], "(no layout to route)")
        layout = result.layouts[0]

    profiler = cProfile.Profile()
    start = time.perf_counter()
    profiler.enable()
    try:
        plan_fill(
            layout,
            heuristic=regime.tow_heuristic,
            max_expansions=regime.tow_max_expansions,
            max_total_expansions=regime.tow_max_total_expansions,
        )
    except NoFeasiblePlanError:
        pass
    finally:
        profiler.disable()
    elapsed = time.perf_counter() - start

    stats = pstats.Stats(profiler)
    buf = io.StringIO()
    stats.stream = buf  # type: ignore[attr-defined]
    stats.sort_stats("cumulative").print_stats(18)
    return (elapsed, _bucket_profile(stats, _ROUTING_STAGES), buf.getvalue())


def profile_placement(regime: Regime) -> tuple[float, list[tuple[str, float, int]], str]:
    """cProfile the placement solve (no tow planning)."""
    if regime.layout is not None:
        # #667 Rung B: a witness regime has no placement phase.
        return (0.0, [], "(witness regime: placement skipped — no solve)")
    scenario = load_regime_scenario(regime)
    profiler = cProfile.Profile()
    start = time.perf_counter()
    profiler.enable()
    _solve_placement(regime, scenario)
    profiler.disable()
    elapsed = time.perf_counter() - start

    stats = pstats.Stats(profiler)
    buf = io.StringIO()
    stats.stream = buf  # type: ignore[attr-defined]
    stats.sort_stats("cumulative").print_stats(18)
    return (elapsed, _bucket_profile(stats, _PLACEMENT_STAGES), buf.getvalue())


def run_all(*, include_heavy: bool = False) -> list[RegimeResult]:
    """Run every regime (fast set by default) and return their results."""
    regimes = REGIMES if include_heavy else FAST_REGIMES
    return [run_regime(r) for r in regimes]
