"""Phase 2a static layout solver.

See ``docs/superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md``
for the full design. This module is built incrementally across multiple PRs;
the current implementation supports:

- pre-search infeasibility detection (§4.1)  [Chunk C]
- random-restart hill climb with min-conflicts descent (§4.2-§4.4)  [Chunk D]
- K-diverse alternatives + termination (§4.5-§4.7)  [Chunk E]
- inter-plane spread post-pass (``_spread`` / ``_inter_plane_energy``; ADR-0008,
  default on via ``SearchConfig.spread``)
- collect-then-select pool with maximin-gap selection across restarts, surfacing
  ``min_pairwise_gap_m`` / ``valid_basins_found`` (Phase 2c, #267; determinism is
  now ``max_restarts``-scoped per the ADR-0003 amendment)
- best-effort tow-path bundling: with ``plan_paths=True`` each returned layout is
  paired with a per-plane tow plan from ``hangarfit.towplanner``; an un-routable
  plane is kept as ``plans[i] = None`` (Phase 3a, ADR-0007 / ADR-0010)
"""

from __future__ import annotations

import concurrent.futures
import itertools
import logging
import math
import multiprocessing
import os
import random as _random_module
import secrets
import sys
import time
from dataclasses import replace
from typing import Literal, NamedTuple, cast

from hangarfit.collisions import check as check_layout
from hangarfit.geometry import WorldPart, aircraft_parts_world, cached_parts_world, pose_cache_scope
from hangarfit.models import (
    Aircraft,
    ApronShallowDrop,
    CheckResult,
    Conflict,
    DiversityConfig,
    GroundObject,
    Layout,
    Placement,
    Scenario,
    SearchConfig,
    SolverDiagnostics,
    SolveResult,
    SolveStatus,
)
from hangarfit.towplanner import MovesPlan, NoFeasiblePlanError, egress_first_conflict, plan_fill

_logger = logging.getLogger(__name__)


def solve(
    scenario: Scenario,
    *,
    budget_s: float = 30.0,
    alternatives: int = 1,
    seed: int | None = None,
    diversity: DiversityConfig | None = None,
    search: SearchConfig | None = None,
    plan_paths: bool = True,
    tow_heuristic: Literal["euclidean", "grid"] = "grid",
    tow_max_expansions: int | None = None,
    workers: int = 1,
) -> SolveResult:
    """Solve a Scenario into up to ``alternatives`` diverse valid Layouts.

    See spec §3.3 for the contract.

    Returns the **best-spread** valid layout(s) found across *all* restarts
    within budget (best-of-all-basins, #267) — NOT the first valid one. The
    restart loop runs to ``budget_s`` / ``search.max_restarts`` only when
    ``search.spread`` is enabled (the default); recording every valid basin
    (spread-polished) into a pool, the returned layouts are then chosen from
    that pool by maximin plan-view gap subject to the diversity gate
    (ADR-0004). With ``search.spread=False`` there is nothing to optimize, so
    the loop keeps the pre-#267 first-valid early exit — it stops as soon as
    ``alternatives`` diverse valid layouts have been found (a seed-deterministic
    termination, independent of wall-clock; preserves the ``--no-spread`` fast
    path). Consequently the returned ``alternatives`` are ordered
    **best-spread-first**, not in discovery order, and
    ``diagnostics.min_pairwise_gap_m`` / ``diagnostics.valid_basins_found``
    surface the selected gaps and the size of the explored basin pool.

    When ``plan_paths`` is ``True`` (the default), each returned layout is
    also tow-planned (#197): ``result.plans[i]`` is the
    :class:`~hangarfit.towplanner.MovesPlan` for ``result.layouts[i]``, or
    ``None`` where the v1 planner could not route it (best-effort
    enrichment — a ``None`` plan never discards an otherwise-valid layout;
    see ADR-0007 and :class:`~hangarfit.models.SolveResult`). Pass
    ``plan_paths=False`` to skip tow-planning entirely — useful when only
    the static layout is needed, since tow-planning runs a bounded
    Hybrid-A* search per plane and is the dominant cost on multi-plane
    fills. With it off, ``plans`` is all-``None`` (still index-aligned).
    Tow-planning is RNG-free, so it preserves the seeded determinism
    contract (ADR-0003) end-to-end through the bundle.

    ``tow_heuristic`` and ``tow_max_expansions`` are forwarded verbatim to
    :func:`~hangarfit.towplanner.plan_fill` (#332/#336). Since #336 the shipped
    default is ``tow_heuristic="grid"`` (the obstacle-aware free-space heuristic)
    with the module per-plane and global fill budgets; pass
    ``tow_heuristic="euclidean"`` to opt out, or a larger ``tow_max_expansions``
    to widen the per-plane budget. All RNG-free, so determinism holds (ADR-0003).

    ``workers`` (default 1 = serial) fans the RR-MC restarts across a
    ``ProcessPoolExecutor`` when > 1 (#544). The result is **byte-identical** to
    the serial path in the ``max_restarts``-bound, spread-on regime (see
    :func:`_parallel_eligible`) — provided ``budget_s`` is non-binding, since the
    parallel path always runs the full ``max_restarts`` count while a serial run
    can stop early when the wall-clock budget trips first (which ADR-0003 already
    scopes out of byte-identity). For any other config it transparently runs
    serial, so the worker count never changes the answer. Output is a pure
    function of ``(scenario, seed)`` either way: since #544 each restart is
    seeded by its index (an ADR-0003 amendment — a one-time re-base of the
    goldens, not a determinism drop).
    """
    # Resolve seed and search ONCE here, above the (possible) two-pass fallback
    # below, so both passes share an identical, reproducible seed (#402 / F5):
    # without this, a ``seed=None`` caller would draw a *different* entropy seed
    # for each pass, making the fallback non-reproducible. A concrete seed
    # passed down makes ``_run_solve``'s own resolution a pass-through.
    if workers < 1:
        raise ValueError(f"workers must be >= 1 (got {workers})")
    resolved_seed = seed if seed is not None else secrets.randbits(32)
    effective_search = search if search is not None else SearchConfig()

    # Per-solve aircraft_parts_world memoization (#453). The #381 profile found
    # this transform is the pipeline's hot spot, with ≈84 % of calls rebuilding
    # an already-seen pose. Running the whole solve (placement + spread +
    # tow-planning) inside one fresh cache collapses those rebuilds; the cache
    # resets on exit, so a double-run stays byte-identical (ADR-0003). The body
    # lives in `_run_solve` so this scope wraps every geometry call without a
    # 197-line reindent. Both fallback passes share the one scope — the cache is
    # a pure memo of a pure transform, so a warm cache changes speed, not values.
    #
    # Re-baselining against pre-#453 output must pin `max_restarts` (or
    # `spread=False`): the speedup changes how many restarts fit a wall-clock
    # `budget_s`, which #267 already scopes OUT of the byte-identical guarantee.
    with pose_cache_scope():
        result = _run_solve(
            scenario,
            budget_s=budget_s,
            alternatives=alternatives,
            seed=resolved_seed,
            diversity=diversity,
            search=effective_search,
            plan_paths=plan_paths,
            tow_heuristic=tow_heuristic,
            tow_max_expansions=tow_max_expansions,
            workers=workers,
        )

        # Spread-vs-towability fallback (#280 → #402 / F5; ADR-0016). The
        # ADR-0008 spread post-pass (default ON) maximizes inter-plane gaps,
        # which can push planes into positions the bounded tow planner can no
        # longer thread from the door cone: every plan comes back ``None`` even
        # though the SAME fleet+hangar routes cleanly with spread off. When the
        # caller asked for tow plans, kept spread on, and got valid-but-unroutable
        # layout(s), re-solve once with spread disabled and prefer the routable
        # (tighter) arrangement. The re-solve inherits ``effective_search`` with
        # only ``spread`` flipped off — so it keeps the caller's ``max_restarts``
        # (deterministic, NOT wall-clock-bound; carried ``back_bias_weight`` is
        # inert with spread off). RNG-free re-selection: changes WHICH valid
        # layout is returned, never WHETHER it is valid (the ``(0, 0.0)`` gate is
        # untouched), so determinism holds end-to-end (ADR-0003).
        if (
            plan_paths
            and effective_search.spread
            and result.layouts
            and all(plan is None for plan in result.plans)
        ):
            fallback = _run_solve(
                scenario,
                budget_s=budget_s,
                alternatives=alternatives,
                seed=resolved_seed,
                diversity=diversity,
                search=replace(effective_search, spread=False),
                plan_paths=True,
                tow_heuristic=tow_heuristic,
                tow_max_expansions=tow_max_expansions,
                workers=workers,
            )
            if fallback.layouts and any(plan is not None for plan in fallback.plans):
                return replace(
                    fallback,
                    diagnostics=replace(fallback.diagnostics, spread_fallback_applied=True),
                )

        return result


class _RestartOutput(NamedTuple):
    """The result of one RR-MC restart, as a pure function of
    ``(scenario, seed, restart_index)`` (#544). ``candidate`` is the valid,
    spread-polished basin this restart found (or ``None``); ``best_partial_*``
    is the lowest-score layout seen in this restart's trajectory (the merge
    folds these across restarts to reconstruct the global best-partial).
    ``best_partial_layout`` is ``None`` *only* when initial placement failed to
    build — that doubly-``None`` shape is how the merge detects a dead restart.
    """

    candidate: _SpreadCandidate | None
    best_partial_score: tuple[int, float]
    best_partial_layout: Layout | None


def _restart_rng(seed: int, restart_index: int) -> _random_module.Random:
    """Per-restart-index RNG (#544 / ADR-0003 amendment). Each restart draws
    from an *independent* stream keyed by ``(seed, restart_index)`` instead of a
    single ``Random(seed)`` threaded across restarts — this makes every restart
    a pure function of its index, so the serial and ProcessPool paths produce
    byte-identical results. A string seed is deterministic and cross-process
    stable even under hash randomization (``random`` seeds strings via SHA-512,
    not ``hash()``); a tuple seed would raise (only int/float/str/bytes seeds
    are accepted)."""
    return _random_module.Random(f"{seed}:{restart_index}")


def _run_restart(
    scenario: Scenario,
    *,
    restart_index: int,
    seed: int,
    search: SearchConfig,
    spread_scale: float,
    pinned_planes: frozenset[str],
    cart_buckets: list[frozenset[str]],
    start: float,
    budget_s: float,
) -> _RestartOutput:
    """Run one RR-MC restart to natural completion (success / stall / build
    failure). Pure function of its arguments — and since ``spread_scale``,
    ``pinned_planes`` and ``cart_buckets`` are all derived deterministically
    from ``scenario``, of ``(scenario, seed, restart_index, search)`` — which is
    what makes the serial and parallel drivers (which share this one extracted
    body) byte-identical. ``start``/``budget_s`` bound the inner descent + spread
    wall-clock the same way the old loop did; the parallel driver passes
    ``budget_s=inf`` so each worker runs to completion (the ``max_restarts``-bound
    regime the byte-identity contract is scoped to)."""
    rng = _restart_rng(seed, restart_index)
    cart_bucket = _cart_bucket_for_restart(cart_buckets, restart_index=restart_index)
    try:
        placements = _initial_placements(scenario=scenario, rng=rng, cart_bucket=cart_bucket)
    except _LayoutBuildFailure:
        return _RestartOutput(None, (sys.maxsize, float("inf")), None)

    best_partial_score: tuple[int, float] = (sys.maxsize, float("inf"))
    best_partial_layout: Layout | None = None
    candidate: _SpreadCandidate | None = None

    initial_layout = Layout(
        fleet=scenario.fleet,
        hangar=scenario.hangar,
        placements=tuple(placements.values()),
        maintenance_plane=scenario.maintenance_plane,
    )
    current_score = _score(initial_layout)
    if current_score < best_partial_score:
        best_partial_score = current_score
        best_partial_layout = initial_layout
    last_improved = 0

    for iter_count in range(10000):  # large outer cap; real exit via stall/success
        if time.monotonic() - start >= budget_s:
            break
        if current_score == (0, 0.0):
            if search.spread:
                placements = _spread(
                    placements,
                    scenario,
                    rng,
                    search,
                    start=start,
                    budget_s=budget_s,
                    pinned_planes=pinned_planes,
                )
            # Nose-out preference (#263 / ADR-0022): RNG-free, runs after _spread.
            n_flips = 0
            if search.nose_out:
                placements, n_flips = _nose_out(
                    placements, scenario, search, pinned_planes=pinned_planes
                )
            candidate_layout = Layout(
                fleet=scenario.fleet,
                hangar=scenario.hangar,
                placements=tuple(placements.values()),
                maintenance_plane=scenario.maintenance_plane,
            )
            min_gap, energy = _spread_quality(placements, scenario, spread_scale)
            candidate = _SpreadCandidate(
                layout=candidate_layout,
                min_gap=min_gap,
                energy=energy,
                restart_index=restart_index,
                nose_out_flips=n_flips,
            )
            break  # found a basin

        step_result = _descent_step(
            placements=placements,
            scenario=scenario,
            rng=rng,
            search=search,
            current_score=current_score,
            pinned_planes=pinned_planes,
        )
        if step_result is None:
            break  # all conflicts on pinned planes
        placements, new_score, _accepted = step_result
        if new_score < current_score:
            last_improved = iter_count
        current_score = new_score

        if current_score < best_partial_score:
            best_partial_score = current_score
            best_partial_layout = Layout(
                fleet=scenario.fleet,
                hangar=scenario.hangar,
                placements=tuple(placements.values()),
                maintenance_plane=scenario.maintenance_plane,
            )

        if iter_count - last_improved >= search.k_stall:
            break  # stall

    return _RestartOutput(candidate, best_partial_score, best_partial_layout)


def _merge_restart(
    out: _RestartOutput,
    pool: list[_SpreadCandidate],
    best_partial_score: tuple[int, float],
    best_partial_layout: Layout | None,
) -> tuple[tuple[int, float], Layout | None]:
    """Fold one restart's output into the running ``pool`` + best-partial. Used
    by BOTH the serial and parallel drivers, so the merge is identical — the key
    to byte-identity. Strict ``<`` keeps the *earliest* restart on a score tie,
    matching the old in-order loop; the caller feeds restarts in index order."""
    if out.best_partial_layout is not None and out.best_partial_score < best_partial_score:
        best_partial_score = out.best_partial_score
        best_partial_layout = out.best_partial_layout
    if out.candidate is not None:
        pool.append(out.candidate)
    return best_partial_score, best_partial_layout


def _run_restart_worker(
    args: tuple[Scenario, int, int, SearchConfig, float, frozenset[str], list[frozenset[str]]],
) -> _RestartOutput:
    """ProcessPool entry point (module-level so it pickles by reference). Each
    worker runs its restart inside a fresh ``pose_cache_scope`` (the #453 memo is
    a pure memo, so a per-worker cache changes speed, not values) and with an
    infinite budget (run to natural completion — the ``max_restarts`` regime)."""
    scenario, restart_index, seed, search, spread_scale, pinned_planes, cart_buckets = args
    with pose_cache_scope():
        return _run_restart(
            scenario,
            restart_index=restart_index,
            seed=seed,
            search=search,
            spread_scale=spread_scale,
            pinned_planes=pinned_planes,
            cart_buckets=cart_buckets,
            start=0.0,
            budget_s=float("inf"),
        )


def _run_restarts_parallel(
    scenario: Scenario,
    *,
    seed: int,
    search: SearchConfig,
    spread_scale: float,
    pinned_planes: frozenset[str],
    cart_buckets: list[frozenset[str]],
    workers: int,
    n_restarts: int,
) -> list[_RestartOutput]:
    """Fan ``n_restarts`` restarts across a spawn ProcessPool and return their
    outputs **in restart-index order** (NOT completion order). The ordered list
    lets the caller merge exactly as the serial loop would — byte-identical
    regardless of which worker finishes first. ThreadPool is useless here:
    shapely's scalar calls don't release the GIL (#544 / spike #540)."""
    ctx = multiprocessing.get_context("spawn")
    max_workers = max(1, min(workers, n_restarts, os.cpu_count() or 1))
    results: list[_RestartOutput | None] = [None] * n_restarts
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=max_workers, mp_context=ctx
    ) as executor:
        future_to_index = {
            executor.submit(
                _run_restart_worker,
                (scenario, i, seed, search, spread_scale, pinned_planes, cart_buckets),
            ): i
            for i in range(n_restarts)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            # .result() re-raises any worker exception (we never swallow it).
            results[future_to_index[future]] = future.result()
    # Invariant: one future per index, each returns a _RestartOutput, so every
    # slot is filled. Assert it LOUDLY rather than silently filtering — a future
    # edit that broke it (e.g. a timeout or FIRST_EXCEPTION on as_completed)
    # would otherwise drop restarts and quietly change the result.
    unfilled = [i for i, r in enumerate(results) if r is None]
    if unfilled:
        raise AssertionError(f"parallel restart slot(s) never filled: {unfilled}")
    return cast("list[_RestartOutput]", results)


def _parallel_eligible(search: SearchConfig, workers: int) -> bool:
    """Whether ``workers`` parallel restarts are byte-identical to the serial
    path for this config (#544). Parallel runs a FIXED ``max_restarts`` restarts
    and merges; it matches serial only when serial would *also* run them all —
    i.e. ``max_restarts`` is set (the determinism-scoped regime) and no
    completion-order-dependent early-exit gate is active (``spread`` on so the
    pre-#267 first-valid exit is off, and the opt-in spread-stall exit unset).
    Otherwise the caller runs serial so worker count never changes the result."""
    return (
        workers > 1
        and search.max_restarts is not None
        and search.spread
        and search.spread_stall_restarts is None
    )


def _run_solve(
    scenario: Scenario,
    *,
    budget_s: float,
    alternatives: int,
    seed: int | None,
    diversity: DiversityConfig | None,
    search: SearchConfig | None,
    plan_paths: bool,
    tow_heuristic: Literal["euclidean", "grid"],
    tow_max_expansions: int | None,
    workers: int = 1,
) -> SolveResult:
    """Body of :func:`solve`, run inside an active ``pose_cache_scope`` (#453)."""
    if diversity is None:
        diversity = DiversityConfig()
    if search is None:
        search = SearchConfig()

    # Resolve seed. Since #544 each restart builds its OWN
    # ``random.Random`` keyed by ``(resolved_seed, restart_index)`` (see
    # ``_restart_rng``) instead of one instance threaded across restarts —
    # that makes each restart a pure function of its index, which is what lets
    # the serial and ProcessPool paths agree byte-for-byte (ADR-0003
    # amendment). The per-restart RNG drives every sampling decision (spec §4.8:
    # initial placement, perturbation, candidate selection, conflict-plane
    # pick); cart-bucket round-robin uses the non-random restart index.
    resolved_seed = seed if seed is not None else secrets.randbits(32)

    # Diversity-impossible heuristic (spec §4.6): warn (without mutating
    # `alternatives`) when too many planes are pinned to ever yield >1
    # diverse layout. See `_diversity_is_impossible`.
    diversity_impossible = _diversity_is_impossible(scenario, alternatives, diversity)

    # ── Pre-search infeasibility checks (§4.1) ──────────────────────────
    start = time.monotonic()

    infeasible = _check_trivially_infeasible(scenario)
    if infeasible is not None:
        return _build_trivially_infeasible_result(
            infeasible,
            start=start,
            resolved_seed=resolved_seed,
            diversity_impossible=diversity_impossible,
        )

    # ── Real search (RR-MC, spec §4.2-§4.5) ─────────────────────────────
    pinned_planes = frozenset(
        pid
        for pid in scenario.fleet_in
        if pid in scenario.constraints and scenario.constraints[pid].pin is not None
    )
    cart_buckets = _enumerate_cart_buckets(scenario)

    # Type matches `_score`'s return (`tuple[int, float]`). `sys.maxsize`
    # is the sentinel int that compares strictly greater than any plausible
    # `len(conflicts)`; pairing with `inf` for the float keeps Python's
    # natural tuple lex-compare unambiguous.
    best_partial_score: tuple[int, float] = (sys.maxsize, float("inf"))
    best_partial_layout: Layout | None = None
    pool: list[_SpreadCandidate] = []
    restart_index = 0
    spread_scale = _resolve_spread_scale(scenario, search)

    # F7 (#404) spread-stagnation early-exit state. Inert unless
    # ``search.spread_stall_restarts`` is set (see the loop tail): tracks the
    # best selected-set maximin gap seen so far and how many consecutive
    # restarts have failed to beat it by ``spread_stall_epsilon_m``.
    best_stall_gap = float("-inf")
    stall_count = 0
    spread_stalled = False

    # Outer restart loop. Two independent termination gates; first to
    # trip wins:
    #   1. Wall-clock budget (`budget_s`) — always present.
    #   2. Restart count (`search.max_restarts`) — opt-in via v0.6.0's
    #      SearchConfig field (spec §4.2). `None` preserves the
    #      pre-v0.6.0 wall-clock-only behavior. Useful for
    #      cross-machine-deterministic exhaustion canaries.
    # Selection of the K best basins happens after the loop completes;
    # the loop itself runs only to budget / max_restarts.
    if _parallel_eligible(search, workers):
        # Parallel restarts (#544). Run exactly ``max_restarts`` restarts across
        # a ProcessPool and merge them IN INDEX ORDER, which reproduces the
        # serial accumulation byte-for-byte. ``_parallel_eligible`` guarantees no
        # completion-order-dependent early-exit gate is active, so running the
        # full fixed count matches what serial would do. ``budget_s`` is the
        # outer guard of the ``max_restarts``-bound regime only; the count is
        # fixed (the byte-identity contract is scoped to this regime, ADR-0003).
        assert search.max_restarts is not None  # guaranteed by _parallel_eligible
        outputs = _run_restarts_parallel(
            scenario,
            seed=resolved_seed,
            search=search,
            spread_scale=spread_scale,
            pinned_planes=pinned_planes,
            cart_buckets=cart_buckets,
            workers=workers,
            n_restarts=search.max_restarts,
        )
        for out in outputs:  # already in restart-index order
            best_partial_score, best_partial_layout = _merge_restart(
                out, pool, best_partial_score, best_partial_layout
            )
        restart_index = search.max_restarts
    else:
        # Serial restarts. Each restart is the same pure ``_run_restart`` the
        # parallel path uses (per-index reseed), so the two paths are
        # byte-identical in the eligible regime. The completion-order-dependent
        # early-exit gates below fire only on the non-eligible configs (spread
        # off / spread-stall set), which is exactly why those run serial.
        #
        # Two independent termination gates; first to trip wins:
        #   1. Wall-clock budget (`budget_s`) — always present.
        #   2. Restart count (`search.max_restarts`) — opt-in (spec §4.2);
        #      `None` preserves wall-clock-only behaviour.
        while time.monotonic() - start < budget_s and (
            search.max_restarts is None or restart_index < search.max_restarts
        ):
            out = _run_restart(
                scenario,
                restart_index=restart_index,
                seed=resolved_seed,
                search=search,
                spread_scale=spread_scale,
                pinned_planes=pinned_planes,
                cart_buckets=cart_buckets,
                start=start,
                budget_s=budget_s,
            )
            restart_index += 1

            # A build failure (initial placement couldn't build) produces no
            # candidate and no partial — skip the merge AND the early-exit gates
            # (matches the old loop's `continue`, since the pool is unchanged).
            if out.best_partial_layout is None and out.candidate is None:
                continue

            best_partial_score, best_partial_layout = _merge_restart(
                out, pool, best_partial_score, best_partial_layout
            )

            # Best-of-all-basins selection (#267) only adds value when the spread
            # post-pass can reorder basins by separation quality. With spread
            # disabled there is nothing to optimize, so continuing past
            # `alternatives` diverse valid layouts cannot improve the result —
            # restore the pre-#267 early exit (the `--no-spread` fast path,
            # seed-deterministic and independent of wall-clock).
            if not search.spread:
                selected_so_far, _ = _select_spread_diverse(pool, alternatives, diversity)
                if len(selected_so_far) >= alternatives:
                    break
            elif search.spread_stall_restarts is not None:
                # F7 (#404): opt-in spread-stagnation early-exit. Arms only once a
                # COMPLETE selected set exists, so a hard scenario still gets the
                # full budget to find its first answer; thereafter, stop once
                # ``spread_stall_restarts`` consecutive restarts fail to improve
                # the set's maximin gap by ``spread_stall_epsilon_m``. The metric
                # is ``min(min_gap)`` over the selected basins (ADR-0008);
                # ``best_stall_gap`` tracks the running max, so the stop is a
                # sound, deterministic "no improvement in the last N restarts"
                # signal for ``alternatives`` >= 1. Seed-fixed restart sequence +
                # integer counter ⇒ identical per-seed across machines, narrowing
                # the #267 timing scope (ADR-0003).
                selected_so_far, _ = _select_spread_diverse(pool, alternatives, diversity)
                if len(selected_so_far) >= alternatives:
                    current_stall_gap = min(c.min_gap for c in selected_so_far)
                    if current_stall_gap >= best_stall_gap + search.spread_stall_epsilon_m:
                        best_stall_gap = current_stall_gap
                        stall_count = 0
                    else:
                        stall_count += 1
                        if stall_count >= search.spread_stall_restarts:
                            spread_stalled = True
                            break

    elapsed = time.monotonic() - start

    selected, diversity_rejected_count = _select_spread_diverse(pool, alternatives, diversity)

    if selected:
        return _build_found_result(
            selected,
            alternatives=alternatives,
            plan_paths=plan_paths,
            tow_heuristic=tow_heuristic,
            tow_max_expansions=tow_max_expansions,
            restart_index=restart_index,
            elapsed=elapsed,
            resolved_seed=resolved_seed,
            diversity_impossible=diversity_impossible,
            diversity_rejected_count=diversity_rejected_count,
            valid_basins_found=len(pool),
            spread_stall_applied=spread_stalled,
        )
    # ``spread_stalled`` can only be True alongside a complete (non-empty)
    # selection, so the exhausted branch always leaves the flag at its default
    # False (the early-exit never fires before the first complete answer).
    return _build_exhausted_result(
        best_partial_layout,
        restart_index=restart_index,
        elapsed=elapsed,
        resolved_seed=resolved_seed,
        diversity_impossible=diversity_impossible,
        diversity_rejected_count=diversity_rejected_count,
        valid_basins_found=len(pool),
    )


def _diversity_is_impossible(
    scenario: Scenario,
    alternatives: int,
    diversity: DiversityConfig,
) -> bool:
    """Detect (and warn about) a structurally diversity-impossible request (spec §4.6).

    When the number of free (non-pinned) planes is strictly less than
    ``diversity.min_planes_moved`` AND the caller asked for >1 alternative, they
    cannot mathematically get more than one accepted layout: every candidate L'
    after the first will share too many pinned planes with L to ever pass
    ``n_moved ≥ min_planes_moved``. Logged as a warning so CLI / library users
    see it; ``alternatives`` is deliberately NOT mutated — search runs normally
    and the natural outcome is found_partial with one accepted layout (the spec
    avoids downgrading the API contract). Pin-detection mirrors
    ``_check_trivially_infeasible``. RNG-free.
    """
    free_planes = sum(
        1
        for pid in scenario.fleet_in
        if scenario.constraints.get(pid) is None or scenario.constraints[pid].pin is None
    )
    diversity_impossible = alternatives > 1 and free_planes < diversity.min_planes_moved
    if diversity_impossible:
        _logger.warning(
            "requested %d alternatives but only 1 is achievable "
            "(%d of %d planes are pinned). Expect status=found_partial.",
            alternatives,
            len(scenario.fleet_in) - free_planes,
            len(scenario.fleet_in),
        )
    return diversity_impossible


def _build_trivially_infeasible_result(
    infeasible: tuple[CheckResult, Layout],
    *,
    start: float,
    resolved_seed: int,
    diversity_impossible: bool,
) -> SolveResult:
    """Build the ``trivially_infeasible`` :class:`SolveResult` (spec §4.1).

    RNG-free. ``wall_time_s`` is measured at the same point in execution as the
    inline code this replaces; the determinism canaries do not assert on it.
    """
    bad_check, bad_layout = infeasible
    return SolveResult(
        status="trivially_infeasible",
        layouts=(),
        diagnostics=SolverDiagnostics(
            restarts_attempted=0,
            wall_time_s=time.monotonic() - start,
            best_partial=bad_check,
            best_partial_layout=bad_layout,
            seed=resolved_seed,
            diversity_impossible=diversity_impossible,
        ),
    )


def _tow_plan_layouts(
    accepted_layouts: list[Layout],
    *,
    plan_paths: bool,
    tow_heuristic: Literal["euclidean", "grid"],
    tow_max_expansions: int | None,
) -> tuple[tuple[MovesPlan | None, ...], tuple[str, ...], tuple[ApronShallowDrop, ...]]:
    """Tow-plan every returned layout (best-effort enrichment, #197).

    Returns ``(plans, unroutable, apron_drops)``. ``plans`` is index-aligned with
    ``accepted_layouts``. The v2 planner (Reeds–Shepp arcs + bounded Hybrid-A* —
    #222/#261 under ADR-0007 + ADR-0010) cannot route dense multi-plane fills and
    has documented false-negatives, so an un-routable layout is recorded as
    ``plans[i]=None`` rather than discarding the otherwise-valid static arrangement
    — the layout is the headline answer; the tow plan is advisory (spike Risk #8).
    RNG-free, so it preserves the ADR-0003 determinism contract.

    ``apron_drops`` is the flat, advisory list of every returned layout's
    too-shallow-apron drops (#503): a plane that towed via the ``y = 0`` door line
    despite an apron being set, because the apron was too shallow for its footprint
    (one :class:`ApronShallowDrop` per dropped plane per layout, in
    layout-then-move order). Because this collects only the layouts ACTUALLY
    returned in the :class:`SolveResult`, a discarded spread-fallback pass's drops
    never surface — only the chosen result's diagnostics are built (see
    :func:`solve`). Empty when no returned layout had a too-shallow drop.

    With ``plan_paths=False`` no planning runs and ``plans`` is all-``None``
    (still index-aligned), with no ``unroutable`` planes and no ``apron_drops``.
    """
    if not plan_paths:
        return (None,) * len(accepted_layouts), (), ()

    built: list[MovesPlan | None] = []
    unroutable: list[str] = []
    apron_drops: list[ApronShallowDrop] = []
    for layout in accepted_layouts:
        # Diagnostics-only out-param (#503): plan_fill populates this with the
        # too-shallow-apron drops of THIS layout's tow plan, plan-inert — it is
        # never read back into the MovesPlan, so the plan stays byte-identical
        # whether or not the list is passed (ADR-0003). A fresh list per layout
        # keeps the per-layout drops in their own scope; we extend the flat
        # collection so a plane dropped across multiple layouts appears once per
        # layout (the CLI dedups by plane id before warning).
        layout_drops: list[ApronShallowDrop] = []
        try:
            # Default path calls ``plan_fill(layout)`` with NO budget kwargs (only
            # the plan-inert diagnostics out-param), so the ADR-0003 determinism
            # canaries are unaffected (the out-param never changes the plan). Since
            # #336 the shipped default is grid + the module budgets, which is
            # exactly what a bare ``plan_fill(layout)`` applies; an explicit
            # euclidean / custom budget takes the kwargs path below.
            if tow_heuristic == "grid" and tow_max_expansions is None:
                plan = plan_fill(layout, apron_dropped_out=layout_drops)
            else:
                plan = plan_fill(
                    layout,
                    heuristic=tow_heuristic,
                    max_expansions=tow_max_expansions,
                    apron_dropped_out=layout_drops,
                )
            # #603: a hard-door mover (e.g. the rescue Caddy) must be able to drive
            # OUT the door against the full parked scene, else the layout is
            # operationally useless -> record it un-routable (exit 3) via the same
            # NoFeasiblePlanError path as a boxed-in plane. Inert (byte-identical)
            # when no hard-door mover is present (the loop body never runs).
            for gp in layout.ground_object_placements:
                if layout.ground_objects[gp.plane_id].hard_door_mover:
                    egress = egress_first_conflict(
                        layout,
                        gp.plane_id,
                        heuristic=tow_heuristic,
                        max_expansions=tow_max_expansions,
                    )
                    if egress is not None:
                        raise NoFeasiblePlanError(gp.plane_id, egress)
            built.append(plan)
            apron_drops.extend(layout_drops)
        except NoFeasiblePlanError as e:
            built.append(None)
            unroutable.append(e.plane_id)
            # A NoFeasiblePlanError means this layout produced no plan at all, so
            # any partial drops collected before the bail describe a plan the user
            # never receives — discard them (don't extend apron_drops).
            # Log the conflict kind/detail too, not just the plane: it
            # distinguishes a genuinely-boxed-in plane from a Hybrid-A* budget
            # exhaustion (a known false-negative class), which call for different
            # operator responses.
            _logger.warning(
                "layout not tow-routable by the tow-path planner: plane %r blocked "
                "(%s: %s); returning the valid static layout without a tow plan",
                e.plane_id,
                e.conflict.kind,
                e.conflict.detail,
            )
    return tuple(built), tuple(unroutable), tuple(apron_drops)


def _build_found_result(
    selected: list[_SpreadCandidate],
    *,
    alternatives: int,
    plan_paths: bool,
    tow_heuristic: Literal["euclidean", "grid"],
    tow_max_expansions: int | None,
    restart_index: int,
    elapsed: float,
    resolved_seed: int,
    diversity_impossible: bool,
    diversity_rejected_count: int,
    valid_basins_found: int,
    spread_stall_applied: bool,
) -> SolveResult:
    """Build the ``found`` / ``found_partial`` :class:`SolveResult`.

    Derives the accepted layouts and per-layout maximin gaps from the selected
    basins, tow-plans them (best-effort, see :func:`_tow_plan_layouts`), and
    assembles the diagnostics. The ``status`` stays search-driven — tow-planning
    never flips found ↔ found_partial. RNG-free.
    """
    accepted_layouts = [c.layout for c in selected]
    min_gaps = tuple(c.min_gap for c in selected)
    nose_out_flips = tuple(c.nose_out_flips for c in selected)
    status: SolveStatus = "found" if len(accepted_layouts) >= alternatives else "found_partial"
    plans, unroutable, apron_drops = _tow_plan_layouts(
        accepted_layouts,
        plan_paths=plan_paths,
        tow_heuristic=tow_heuristic,
        tow_max_expansions=tow_max_expansions,
    )
    return SolveResult(
        status=status,
        layouts=tuple(accepted_layouts),
        plans=plans,
        diagnostics=SolverDiagnostics(
            restarts_attempted=restart_index,
            wall_time_s=elapsed,
            best_partial=None,
            best_partial_layout=None,
            seed=resolved_seed,
            diversity_impossible=diversity_impossible,
            diversity_rejected_count=diversity_rejected_count,
            unroutable_planes=unroutable,
            min_pairwise_gap_m=min_gaps,
            valid_basins_found=valid_basins_found,
            spread_stall_applied=spread_stall_applied,
            apron_shallow_drops=apron_drops,
            nose_out_flips=nose_out_flips,
        ),
    )


def _build_exhausted_result(
    best_partial_layout: Layout | None,
    *,
    restart_index: int,
    elapsed: float,
    resolved_seed: int,
    diversity_impossible: bool,
    diversity_rejected_count: int,
    valid_basins_found: int,
) -> SolveResult:
    """Build the ``exhausted_budget`` :class:`SolveResult`.

    Re-checks ``best_partial_layout`` (if any) for the fused best-partial pair.
    RNG-free.
    """
    bp = check_layout(best_partial_layout) if best_partial_layout is not None else None
    return SolveResult(
        status="exhausted_budget",
        layouts=(),
        diagnostics=SolverDiagnostics(
            restarts_attempted=restart_index,
            wall_time_s=elapsed,
            best_partial=bp,
            best_partial_layout=best_partial_layout,
            seed=resolved_seed,
            diversity_impossible=diversity_impossible,
            diversity_rejected_count=diversity_rejected_count,
            valid_basins_found=valid_basins_found,
        ),
    )


def _check_trivially_infeasible(
    scenario: Scenario,
) -> tuple[CheckResult, Layout] | None:
    """Run the three literal-impossibility checks from spec §4.1.

    Returns ``(check_result, layout)`` if the scenario is provably
    infeasible (the caller plugs both into
    :class:`SolverDiagnostics`); else ``None``.

    The three checks run in a **fixed order** — per-plane bbox (#1), summed
    footprint area (#2), pin self-collision (#3) — and the first to trip is
    returned. The order is part of the observed behaviour (it determines which
    conflict ``kind`` a multiply-infeasible scenario reports), so it must not
    change; each sub-check is a pure predicate over ``scenario`` returning the
    same ``(CheckResult, Layout)`` pair or ``None``.

    The Layout is paired with the CheckResult because
    :class:`SolverDiagnostics` requires ``best_partial`` and
    ``best_partial_layout`` to both be set or both be ``None``. For
    checks #1 and #2 (no candidate placements exist yet), the paired
    Layout is the "empty" Layout — same fleet and hangar as the
    scenario, with no placements and no maintenance plane. For check #3
    it is the pin-only Layout that was used to detect the conflict.
    """
    for check in (
        _check_plane_too_big,
        _check_sum_areas,
        _check_pin_feasibility,
    ):
        result = check(scenario)
        if result is not None:
            return result
    return None


def _check_plane_too_big(scenario: Scenario) -> tuple[CheckResult, Layout] | None:
    """Check 1: any single plane's bbox exceeds the hangar's larger dimension.

    A plane whose max part extent is larger than ``max(length, width)`` cannot
    fit at any heading. Returns the conflict paired with the empty Layout, or
    ``None`` if every plane fits.
    """
    for pid in scenario.fleet_in:
        plane = scenario.fleet[pid]
        length, width = _plane_max_extent(plane)
        max_hangar = max(scenario.hangar.length_m, scenario.hangar.width_m)
        if length > max_hangar or width > max_hangar:
            check = CheckResult(
                conflicts=(
                    Conflict.single(
                        kind="trivially_infeasible_plane_too_big",
                        plane=pid,
                        detail=(
                            f"plane bbox {length:.1f}x{width:.1f} m exceeds "
                            f"hangar max dimension {max_hangar:.1f} m"
                        ),
                    ),
                ),
                total_penetration_m2=0.0,
            )
            return check, _empty_layout(scenario)
    return None


def _check_sum_areas(scenario: Scenario) -> tuple[CheckResult, Layout] | None:
    """Check 2: Σ part-footprint areas vs hangar floor area.

    Sums each plane's actual part rectangles (:func:`_plane_parts_total_area`),
    not its bounding box: a thin-winged glider's bbox is ``fuselage_span ×
    wingspan`` — mostly empty air — so the old bbox sum false-rejected glider
    fleets that a valid nested layout would fit (#425). Returns the conflict
    paired with the empty Layout, or ``None`` if the summed part footprint fits
    the floor.
    """
    total_area = 0.0
    for pid in scenario.fleet_in:
        plane = scenario.fleet[pid]
        total_area += _plane_parts_total_area(plane)
    hangar_area = scenario.hangar.length_m * scenario.hangar.width_m
    if total_area > hangar_area:
        check = CheckResult(
            conflicts=(
                Conflict.single(
                    kind="trivially_infeasible_sum_areas",
                    # The conflict's `planes` field is cosmetic here — single-plane
                    # arity is required by Conflict.__post_init__ but the real
                    # information is in `detail`. Pick the first plane in
                    # fleet_in deterministically.
                    plane=scenario.fleet_in[0],
                    detail=(
                        f"fleet footprint Σ areas {total_area:.1f} m² exceeds "
                        f"hangar floor area {hangar_area:.1f} m²"
                    ),
                ),
            ),
            total_penetration_m2=0.0,
        )
        return check, _empty_layout(scenario)
    return None


def _check_pin_feasibility(scenario: Scenario) -> tuple[CheckResult, Layout] | None:
    """Check 3: pinned placements must satisfy the cart rule and not collide.

    Builds a Layout containing only the pinned planes and runs ``check()`` on
    it (after a sharp cart-rule pre-check). Returns the conflict paired with the
    pin-only Layout, or ``None`` if there are no pins or the pin-only layout is
    valid.
    """
    pinned_placements = []
    for pid in scenario.fleet_in:
        constraint = scenario.constraints.get(pid)
        if constraint is not None and constraint.pin is not None:
            pinned_placements.append(constraint.pin)

    if not pinned_placements:
        return None

    # The cart rule (at most hangar.max_carts cart_eligible planes on
    # carts at a time) is enforced by Layout.__post_init__ but NOT by
    # Scenario.__post_init__ — that's a cross-pin invariant Scenario
    # currently doesn't check. Guard explicitly here so we can return
    # a sharp `pin_cart_rule` conflict instead of silently absorbing
    # a generic Layout `ValueError`. Morally this check belongs in
    # Scenario; a follow-up could migrate it to Scenario.__post_init__
    # so every caller (not just solve()) benefits. The limit tracks
    # scenario.hangar.max_carts so it agrees with the Layout invariant
    # (and with any --max-carts override already applied to the hangar).
    max_carts = scenario.hangar.max_carts
    cart_eligible_on_carts = sum(
        1
        for p in pinned_placements
        if p.on_carts and scenario.fleet[p.plane_id].movement_mode == "cart_eligible"
    )
    if cart_eligible_on_carts > max_carts:
        check = CheckResult(
            conflicts=(
                Conflict.single(
                    kind="trivially_infeasible_pin_cart_rule",
                    plane=pinned_placements[0].plane_id,
                    detail=(
                        f"{cart_eligible_on_carts} cart_eligible pins on "
                        f"carts (cart rule allows at most {max_carts})"
                    ),
                ),
            ),
            total_penetration_m2=0.0,
        )
        return check, _empty_layout(scenario)

    # Build a Layout containing ONLY the pinned planes.
    #
    # ``maintenance_plane`` is forwarded to the pin-only Layout so that
    # ``collisions.check()`` can fire the maintenance_position rule if the
    # pins collectively leave the maintenance area occupied by another plane
    # or violated (detected early, before burning the full solve() budget
    # on a restart loop that can never escape a pinned conflict).
    #
    # Scenario.__post_init__ guarantees that the maintenance_plane cannot
    # carry a pin (raises ValueError if it does), so ``pinned_placements``
    # is provably free of the maintenance occupant — no filter is needed.
    #
    # No try/except: every remaining Layout invariant that could fire
    # here is either structurally impossible given pin-only construction
    # or already caught by Scenario.__post_init__ (pin.plane_id mismatch,
    # pin.on_carts vs movement_mode). A genuinely unexpected ValueError
    # should propagate as a bug, not get silently re-wrapped as a pin
    # infeasibility.
    pin_only_layout = Layout(
        fleet=scenario.fleet,
        hangar=scenario.hangar,
        placements=tuple(pinned_placements),
        maintenance_plane=scenario.maintenance_plane,
    )

    pin_check = check_layout(pin_only_layout)
    if not pin_check.valid:
        return pin_check, pin_only_layout

    return None


def _plane_max_extent(plane: Aircraft) -> tuple[float, float]:
    """Return (max_length_m, max_width_m) over all of the plane's Parts.

    Takes the maximum ``length_m`` and maximum ``width_m`` across all
    parts. This is a **lower bound** on the plane's true plane-local
    outline — it IGNORES per-part offsets (``Part.offset_x_m``,
    ``Part.offset_y_m``), so a plane whose individual parts each fit
    but whose offsets push the combined outline outside will pass
    this check (false negative).

    That's an acceptable trade-off for the literal-infeasibility gate
    (Chunk C check #1): false negatives don't cause incorrect
    rejection — they just defer to the actual search, which detects
    the failure via ``collisions.check()``. What this function CANNOT
    produce is a false positive: if even one part's bbox dimension
    exceeds ``max(hangar.length_m, hangar.width_m)``, the plane
    provably cannot fit at any heading, regardless of offsets.

    Returns max length and max width as separate values; the caller
    compares both against ``max(hangar.length_m, hangar.width_m)`` to
    catch rotation-aware infeasibility (either bbox dim can become
    the deep one).
    """
    max_length = max(p.length_m for p in plane.parts)
    max_width = max(p.width_m for p in plane.parts)
    return max_length, max_width


def _plane_parts_total_area(plane: Aircraft) -> float:
    """Σ of each part's footprint rectangle — the Σ-areas gate's per-plane term.

    Consumed only by :func:`_check_sum_areas` (check #2). It replaces the old
    bounding-box estimate (``max_length × max_width``), which multiplied the
    fuselage span by the *wingspan* and so counted the empty air between a thin
    wing and a narrow fuselage. For an 18 m-span glider that bbox was ~5× the
    real footprint, so the gate false-rejected glider fleets that a valid nested
    layout would fit — the bug this fixes (#425).

    Summing the real part rectangles is a much tighter estimate. It is *not* a
    strict lower bound on the plane's true plan-view footprint: parts that
    overlap in plan view (a wing sitting over its own z-disjoint fuselage) are
    counted twice. But that residual over-count is small (≈ one wing-root ×
    fuselage-width) — it keeps the gate conservative without the empty-air
    inflation a bbox invents. Like the rest of the gate it is RNG-free and runs
    before the search, so it cannot perturb the determinism contract
    (ADR-0003).
    """
    return sum(p.length_m * p.width_m for p in plane.parts)


class _LayoutBuildFailure(Exception):
    """Raised when initial placement can't satisfy basic invariants.

    Used by :func:`_initial_placements` to signal a sample-error that
    should trigger a restart (vs. crashing the solver). Currently the
    helper never raises — every constraint case is handled inline — but
    the type is retained so future invariant-aware sampling can fail
    sharply without leaking ``ValueError`` from the search loop.
    """


def _initial_placement_for_plane(
    *,
    plane_id: str,
    scenario: Scenario,
    rng: _random_module.Random,
    on_carts: bool,
) -> Placement:
    """Sample an initial :class:`Placement` for one plane (spec §4.2).

    - If pinned → return the pin verbatim.
    - Otherwise → ``(x, y)`` uniform inside hangar (with bbox-derived margin),
      ``heading_deg`` uniform on ``[0, 360°)``.

    The margin equals ``max(max_length, max_width) / 2`` so the placement
    bounding box is unlikely to immediately violate the hangar bounds at any
    heading. ``rng.random() * 360.0`` (NOT ``rng.uniform(0, 360)``) is used
    so the upper bound stays exclusive — required by the
    ``heading_deg < 360.0`` test assertion and matching the spec's
    ``[0°, 360°)`` interval.
    """
    constraint = scenario.constraints.get(plane_id)
    if constraint is not None and constraint.pin is not None:
        return constraint.pin

    hangar = scenario.hangar
    plane = scenario.fleet[plane_id]
    max_length, max_width = _plane_max_extent(plane)
    margin_x = max(max_length, max_width) / 2
    margin_y = margin_x

    y_lo = margin_y
    y_hi = hangar.length_m - margin_y

    x_lo = margin_x
    x_hi = hangar.width_m - margin_x

    # If margins eat the entire hangar (very tiny hangar / very big plane),
    # fall back to placing at the centre — the infeasibility checks should
    # have rejected this case, but defend in code.
    x = hangar.width_m / 2 if x_hi <= x_lo else rng.uniform(x_lo, x_hi)
    y = hangar.length_m / 2 if y_hi <= y_lo else rng.uniform(y_lo, y_hi)

    # rng.random() returns [0.0, 1.0); multiplying by 360 keeps the
    # exclusive upper bound (avoids the rng.uniform(0, 360) inclusive-
    # endpoint pitfall — see test assertion `heading_deg < 360.0`).
    heading = rng.random() * 360.0

    return Placement(
        plane_id=plane_id,
        x_m=x,
        y_m=y,
        heading_deg=heading,
        on_carts=on_carts,
    )


def _body(scenario: Scenario, body_id: str) -> Aircraft | GroundObject:
    """Resolve a placeable body id to its Aircraft or GroundObject definition (#604).

    Aircraft (``fleet``) are checked first so the common, pre-#604 path is
    unchanged; a placed_routed_mover id falls through to ``ground_object_defs``."""
    plane = scenario.fleet.get(body_id)
    if plane is not None:
        return plane
    return scenario.ground_object_defs[body_id]


def _body_parts_world(scenario: Scenario, body_id: str, placement: Placement) -> list[WorldPart]:
    """World parts for any placeable body (#604). Aircraft reuse the pose-memoized
    ``cached_parts_world`` — BYTE-IDENTICAL to the pre-#604 solver path (ADR-0003);
    a GroundObject mover goes through the union-typed uncached ``aircraft_parts_world``,
    matching exactly how the towplanner computes mover geometry (#602)."""
    body = _body(scenario, body_id)
    return list(
        cached_parts_world(body, placement)
        if isinstance(body, Aircraft)
        else aircraft_parts_world(body, placement)
    )


def _priority_weight(scenario: Scenario, pid: str) -> float:
    """Soft spread weight for plane ``pid``: ``1.0 + priority`` (#441).

    A plane's soft :attr:`~hangarfit.models.PlaneConstraint.priority` (default
    ``None`` ≡ the neutral ``0.0``) scales how hard the spread post-pass works to
    clear space around it: each plane-pair's repulsion energy is weighted by
    ``w_i · w_j``. With every priority unset every weight is exactly ``1.0``, so
    ``w_i · w_j == 1.0`` and ``1.0 * exp(x) == exp(x)`` — the energy, and hence
    the whole search, is byte-identical to the pre-#441 behaviour (ADR-0003).
    The non-negative range is enforced by ``Scenario.__post_init__``.
    """
    constraint = scenario.constraints.get(pid)
    priority = constraint.priority if constraint is not None else None
    return 1.0 + (priority if priority is not None else 0.0)


def _inter_plane_energy(
    placements: dict[str, Placement],
    scenario: Scenario,
    scale: float,
    *,
    gap_cache: dict[tuple[str, str], float] | None = None,
    moved: str | None = None,
) -> float:
    """Smooth repulsion energy ``E = Σ_{i<j} w_i·w_j·exp(−gap_ij / scale)`` (spec §4).

    ``gap_ij`` is the minimum plan-view edge-to-edge distance between plane
    ``i``'s and plane ``j``'s world parts (shapely ``polygon.distance``).
    Lower ``E`` ⇒ planes further apart; close pairs dominate the sum, so
    minimizing it maximizes the *minimum* gap (a smooth maximin surrogate).
    ``w_i·w_j`` is the soft-priority pair weight (:func:`_priority_weight`, #441);
    it is identically ``1.0`` when no priorities are set, so this reduces to the
    unweighted ``Σ exp(−gap/scale)`` byte-for-byte (ADR-0003). Returns ``0.0``
    when fewer than two planes are present. Ignores z (plan-view only) — see
    ADR-0008 for the nesting limitation.

    Incremental single-plane re-scoring (#455): the :func:`_spread` hill-climb
    perturbs ONE plane (``moved``) per iteration and scores several candidate
    positions for it. Every pair that does *not* involve ``moved`` is identical
    across those candidates, so when ``gap_cache`` is supplied this memoizes the
    expensive ``gap_ij`` (the shapely distance) for exactly those pairs and
    reuses it. The energy is still accumulated over **all** pairs in canonical
    ``sorted``-id order, so a cached gap (the same float, recomputed from the same
    unchanged poses) makes the result **byte-for-byte identical** to the cache-free
    full recompute (ADR-0003) — this is a distance memo, never the bit-divergent
    delta-update (#455). Pairs touching ``moved`` are always recomputed and never
    cached (``moved`` moves between calls). With ``gap_cache=None`` (every caller
    outside ``_spread``) this is exactly the original full pairwise sweep.
    """
    ids = sorted(placements)
    if len(ids) < 2:
        return 0.0
    world: dict[str, list[WorldPart]] = {
        pid: cached_parts_world(scenario.fleet[pid], placements[pid]) for pid in ids
    }
    w = {pid: _priority_weight(scenario, pid) for pid in ids}
    energy = 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            # A pair is cacheable iff neither endpoint is the perturbed plane:
            # only then is its gap invariant across the candidates sharing the cache.
            cache = gap_cache if (moved != a and moved != b) else None
            if cache is not None and (a, b) in cache:
                gap = cache[(a, b)]
            else:
                gap = min(pa.polygon.distance(pb.polygon) for pa in world[a] for pb in world[b])
                if cache is not None:
                    cache[(a, b)] = gap
            energy += w[a] * w[b] * math.exp(-gap / scale)
    return energy


def _back_bias_energy(placements: dict[str, Placement], scenario: Scenario) -> float:
    """Back-of-hangar fill bias ``B = Σ (length_m − y_p) / length_m`` (#320).

    Minimized when planes park deep (large ``y``, toward the back wall at
    ``y = hangar.length_m``), so the spread hill-climb leaves free space at the
    door end (``y ≈ 0``) instead of mid-hangar. Normalized by ``length_m`` so a
    single ``back_bias_weight`` reads consistently across hangar sizes. Summed
    over ALL placements — pinned planes add a constant offset that cannot change
    the per-target argmin. RNG-free. See ADR-0008 (amended) and #320.
    """
    length = scenario.hangar.length_m
    # Iterate in sorted plane-id order (mirrors _inter_plane_energy /
    # _spread_quality) so this float sum is order-stable even if a future
    # refactor ever builds the placements dict from an unordered source — an
    # ADR-0003 hardening; the dict is currently fleet_in-ordered already.
    return sum((length - placements[pid].y_m) / length for pid in sorted(placements))


def _resolve_spread_scale(scenario: Scenario, search: SearchConfig) -> float:
    """Repulsion length-scale for spread (spec §4): explicit override or
    20% of the smaller hangar dimension. Single source so ``_spread`` and
    ``_spread_quality`` always agree."""
    if search.spread_scale_m is not None:
        return search.spread_scale_m
    return 0.2 * min(scenario.hangar.width_m, scenario.hangar.length_m)


def _spread_quality(
    placements: dict[str, Placement],
    scenario: Scenario,
    scale: float,
) -> tuple[float, float]:
    """Return ``(min_gap, energy)`` for a layout in one pass over plane-pairs.

    ``min_gap`` is the minimum plan-view edge-to-edge distance between any two
    planes' world parts (``math.inf`` when <2 planes — no pairs); it is the raw
    geometric gap and is **never** priority-weighted, so basin selection stays
    maximin-primary (ADR-0008). ``energy`` is the same priority-weighted
    ``Σ w_i·w_j·exp(−gap/scale)`` repulsion :func:`_inter_plane_energy` computes
    (#441; identically the unweighted sum when no priorities are set, ADR-0003);
    returning both from one pairwise sweep avoids paying the (expensive) shapely
    distances twice when scoring a candidate basin. The hot ``_spread`` loop
    keeps using the energy-only :func:`_inter_plane_energy` — this is called once
    per accepted basin, not per perturbation.
    """
    ids = sorted(placements)
    if len(ids) < 2:
        return (math.inf, 0.0)
    world: dict[str, list[WorldPart]] = {
        pid: cached_parts_world(scenario.fleet[pid], placements[pid]) for pid in ids
    }
    w = {pid: _priority_weight(scenario, pid) for pid in ids}
    min_gap = math.inf
    energy = 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            gap = min(
                pa.polygon.distance(pb.polygon) for pa in world[ids[i]] for pb in world[ids[j]]
            )
            min_gap = min(min_gap, gap)
            energy += w[ids[i]] * w[ids[j]] * math.exp(-gap / scale)
    return (min_gap, energy)


def _nose_out(
    placements: dict[str, Placement],
    scenario: Scenario,
    search: SearchConfig,
    *,
    pinned_planes: frozenset[str],
) -> tuple[dict[str, Placement], int]:
    """RNG-free post-pass: flip movable planes' parked headings toward nose-out.

    For each movable plane in ``sorted(plane_id)`` order, apply the
    zero-displacement antipodal flip ``(h + 180) % 360`` (preserving
    x/y/on_carts) iff it is **strictly more nose-out** (closer to heading 180,
    toward the door per ADR-0002) AND the layout stays valid
    (``_score == (0, 0.0)``). Each flip is re-validated against the CURRENT
    (possibly already-flipped) set, one plane at a time, so two
    individually-valid flips can never jointly invalidate. Returns
    ``(placements, n_flips)``.

    Soft preference (#263 / ADR-0022), mirroring :func:`_spread`'s discipline but
    **RNG-free** — it takes no ``rng`` and consumes no RNG draw, so the seeded
    stream (and thus the ADR-0003 byte-identical contract) is unchanged even with
    the feature ON.

    Per-plane override via :attr:`PlaneConstraint.nose_out` (tri-state): ``None``
    ⇒ follow the global ``search.nose_out``; ``True`` ⇒ prefer-out; ``False`` ⇒
    never flip (the nose-IN exemption, e.g. a low-wing tucked under a high-wing
    tail).
    """
    movable = sorted(pid for pid in placements if pid not in pinned_planes)
    flips = 0
    for pid in movable:
        constraint = scenario.constraints.get(pid)
        want = (
            constraint.nose_out
            if constraint is not None and constraint.nose_out is not None
            else search.nose_out
        )
        if not want:
            continue
        current = placements[pid]
        flipped_heading = (current.heading_deg + 180.0) % 360.0
        # Strictly more nose-out. Because the flip is the exact antipode and
        # short_arc(antipode, 180) == 180 - short_arc(h, 180), this is identical
        # to "the flip lands in the nose-out hemisphere (< 90°)".
        if _heading_delta_short_arc(flipped_heading, 180.0) >= _heading_delta_short_arc(
            current.heading_deg, 180.0
        ):
            continue
        flipped = Placement(
            plane_id=current.plane_id,
            x_m=current.x_m,
            y_m=current.y_m,
            heading_deg=flipped_heading,
            on_carts=current.on_carts,
        )
        trial = dict(placements)
        trial[pid] = flipped
        # A heading-only flip preserves plane_id / x / y / on_carts, so it cannot
        # trip any Layout cross-reference invariant (cart rule, on_carts
        # consistency, duplicate placements) — build directly. A ValueError here
        # would be a structural bug, so let it propagate rather than silently skip.
        trial_layout = Layout(
            fleet=scenario.fleet,
            hangar=scenario.hangar,
            placements=tuple(trial.values()),
            maintenance_plane=scenario.maintenance_plane,
        )
        if _score(trial_layout) != (0, 0.0):
            continue
        placements[pid] = flipped
        flips += 1
    return placements, flips


def _spread(
    placements: dict[str, Placement],
    scenario: Scenario,
    rng: _random_module.Random,
    search: SearchConfig,
    *,
    start: float,
    budget_s: float,
    pinned_planes: frozenset[str],
) -> dict[str, Placement]:
    """Post-pass spread: maximize inter-plane separation on a VALID layout.

    Greedy seeded hill-climb that minimizes :func:`_inter_plane_energy`,
    accepting only candidates that stay valid (score ``(0, 0.0)``). Input
    must already be valid; output is therefore always valid — this can only
    improve separation or no-op. Pinned planes are fixed obstacles: they
    contribute to pair distances but are never moved. Shares the global
    wall-clock budget; returns the best (lowest-energy) placements found.

    See ADR-0008 and spec §5.
    """
    scale = _resolve_spread_scale(scenario, search)

    movable = sorted(pid for pid in placements if pid not in pinned_planes)
    back_fill = search.back_bias_weight > 0.0
    if not movable or (len(placements) < 2 and not back_fill):
        # Nothing to optimize: no movable target, or <2 planes with no back-fill
        # bias (inter-plane energy is identically 0.0). With back-fill active a
        # lone plane is still pulled to the back wall, so we do NOT bail there.
        return placements

    def _energy(
        trial: dict[str, Placement],
        *,
        gap_cache: dict[tuple[str, str], float] | None = None,
        moved: str | None = None,
    ) -> float:
        # Spread repulsion, plus the #320 back-of-hangar bias when active. The
        # back-bias re-ranks candidates *within* this hill-climb; basin selection
        # stays min-gap-primary (ADR-0008 amended). gap_cache/moved memoize the
        # unchanged-pair distances within one iteration (#455, byte-identical);
        # the back-bias is a per-plane sum (no pairs) so it is always re-summed.
        e = _inter_plane_energy(trial, scenario, scale, gap_cache=gap_cache, moved=moved)
        if back_fill:
            e += search.back_bias_weight * _back_bias_energy(trial, scenario)
        return e

    current_energy = _energy(placements)
    last_improved = 0

    for iter_count in range(10000):  # large cap; real exit via stall/budget
        if time.monotonic() - start >= budget_s:
            break

        target = rng.choice(movable)

        # Same candidate mix as _descent_step: (N-2) small nudges + 1 large + 1 flip.
        candidates = _generate_candidates(
            current=placements[target],
            target=target,
            scenario=scenario,
            rng=rng,
            search=search,
        )

        # Pick the lowest-(energy, displacement) VALID candidate. Adopt it
        # only if its energy is strictly below current (so the stall counter
        # advances on non-improving iterations — no plateau-wander livelock).
        # Per-iteration distance memo (#455): only `target` moves across these
        # candidates, so the pairs not touching it have an invariant gap — compute
        # each once and reuse. Re-summed in canonical order ⇒ byte-identical.
        gap_cache: dict[tuple[str, str], float] = {}
        best_key: tuple[float, float] | None = None
        best_placements = placements
        for cand in candidates:
            trial = dict(placements)
            trial[target] = cand
            try:
                trial_layout = Layout(
                    fleet=scenario.fleet,
                    hangar=scenario.hangar,
                    placements=tuple(trial.values()),
                    maintenance_plane=scenario.maintenance_plane,
                )
            except ValueError:
                # Mirrors _descent_step's defensive catch. The only routinely-reachable
                # trigger is the cart rule, but _perturb_plane and the 180° flip both
                # preserve on_carts, so in practice no perturbation changes the cart
                # configuration and this branch is never exercised in the current fleet.
                # The catch remains as a defensive guard consistent with _descent_step;
                # a ValueError here would indicate a structural bug, which is acceptable
                # to skip-and-continue because the spread output is independently validated
                # by the `_score == (0, 0.0)` gate and the whole pass is bounded.
                continue
            if _score(trial_layout) != (0, 0.0):
                continue  # must STAY valid
            e = _energy(trial, gap_cache=gap_cache, moved=target)
            disp = (
                (cand.x_m - placements[target].x_m) ** 2 + (cand.y_m - placements[target].y_m) ** 2
            ) ** 0.5
            key = (e, disp)
            if best_key is None or key < best_key:
                best_key = key
                best_placements = trial

        if best_key is not None and best_key[0] < current_energy:
            placements = best_placements
            current_energy = best_key[0]
            last_improved = iter_count

        if iter_count - last_improved >= search.k_stall:
            break

    return placements


def _score(layout: Layout) -> tuple[int, float]:
    """Hierarchical scoring (spec §4.4): ``(conflict_count, total_penetration_m2)``.

    Lower wins. Tuples compare lexicographically: a lower conflict count
    beats any penetration; ties on count are broken by lower penetration.
    ``(0, 0.0)`` means the layout is valid.

    Uses the module-level :func:`check_layout` import (no per-call import
    indirection inside the descent loop).
    """
    result = check_layout(layout)
    return (len(result.conflicts), result.total_penetration_m2)


def _initial_placements(
    *,
    scenario: Scenario,
    rng: _random_module.Random,
    cart_bucket: frozenset[str],
) -> dict[str, Placement]:
    """Sample initial placements for every plane in ``fleet_in``.

    ``cart_bucket`` is the set of cart_eligible planes that should be
    ``on_carts=True`` for this restart (up to ``hangar.max_carts``
    elements per the enumeration in :func:`_enumerate_cart_buckets`).
    Per-plane ``on_carts`` resolution:

    1. ``constraint.force_on_carts`` (if set) — highest priority.
    2. ``constraint.pin.on_carts`` (if pinned).
    3. Plane's ``movement_mode`` for always_cart / always_own_gear.
    4. Membership in ``cart_bucket`` for cart_eligible planes.

    The maintenance plane (if any) is skipped entirely — under the
    ``bay_intrusion`` semantics (see ``docs/architecture/08-crosscutting-concepts.md``
    "The maintenance bay rule") the occupant is treated as away (absent
    from the Layout's placements). The bay rectangle is a hard obstacle
    via the collision rule, so no surrogate sample is needed.
    """
    placements: dict[str, Placement] = {}
    for pid in scenario.fleet_in:
        if pid == scenario.maintenance_plane:
            continue

        plane = scenario.fleet[pid]
        constraint = scenario.constraints.get(pid)

        # Decide on_carts (priority order: force_on_carts > pin > movement_mode > bucket)
        if constraint is not None and constraint.force_on_carts is not None:
            on_carts = constraint.force_on_carts
        elif constraint is not None and constraint.pin is not None:
            on_carts = constraint.pin.on_carts
        elif plane.movement_mode == "always_cart":
            on_carts = True
        elif plane.movement_mode == "always_own_gear":
            on_carts = False
        else:  # cart_eligible
            on_carts = pid in cart_bucket

        placements[pid] = _initial_placement_for_plane(
            plane_id=pid,
            scenario=scenario,
            rng=rng,
            on_carts=on_carts,
        )

    return placements


def _enumerate_cart_buckets(scenario: Scenario) -> list[frozenset[str]]:
    """Enumerate the cart-assignment buckets to round-robin over (spec §4.2).

    A bucket is the set of *free* (unlocked) ``cart_eligible`` planes to
    put ``on_carts=True`` for one restart. The buckets are every subset of
    the free ``cart_eligible`` planes of size ``0 .. remaining``, where
    ``remaining = hangar.max_carts − (cart_eligible planes already
    committed to on_carts by a pin or force_on_carts)``. With the default
    ``max_carts = 1`` and nothing pre-committed this is exactly the empty
    set plus a singleton per free plane — the original behaviour, preserved
    byte-for-byte (so the ADR-0003 determinism canaries are unaffected). A
    larger ``max_carts`` additionally enumerates pairs, triples, … up to
    the inventory size, so the search can actually reach multi-cart
    layouts (#210).

    Locked ``cart_eligible`` planes (any pin, or ``force_on_carts`` set)
    bypass round-robin: their ``on_carts`` state is fixed by the
    constraint, and a pin/force that puts one on carts consumes one unit of
    the inventory (shrinking ``remaining``). A pin with ``on_carts=False``
    locks the plane out of every bucket but consumes no inventory. If
    commitments already meet ``max_carts``, ``remaining`` is 0 and the only
    bucket is the empty set; a genuine over-commit is caught earlier as
    ``trivially_infeasible_pin_cart_rule``.

    The cart rule is still enforced holistically by
    ``Layout.__post_init__`` later — this enumeration just avoids wasting
    restart budget on guaranteed-infeasible configurations.

    Iteration is over ``scenario.fleet_in`` (a tuple), not
    ``scenario.fleet`` (a ``MappingProxyType``-wrapped dict view), and
    combinations are generated in that order, so bucket ordering is
    deterministic by construction.
    """
    free_cart_eligibles: list[str] = []
    committed_on_carts = 0
    for pid in scenario.fleet_in:
        if pid == scenario.maintenance_plane:
            # Occupant is treated as away — it never enters the layout, so
            # round-robining a cart bucket for it would be a no-op restart
            # that wastes one slot of the rotation.
            continue
        plane = scenario.fleet[pid]
        if not plane.is_cart_eligible:
            continue
        constraint = scenario.constraints.get(pid)
        if constraint is not None:
            if constraint.pin is not None:
                # Any pin locks the plane out of round-robin. If the pin
                # puts it on carts, one unit of inventory is consumed.
                if constraint.pin.on_carts:
                    committed_on_carts += 1
                continue
            if constraint.force_on_carts is not None:
                # force_on_carts=True consumes one unit of inventory.
                if constraint.force_on_carts:
                    committed_on_carts += 1
                continue
        free_cart_eligibles.append(pid)

    remaining = min(
        max(0, scenario.hangar.max_carts - committed_on_carts),
        len(free_cart_eligibles),
    )
    buckets: list[frozenset[str]] = []
    for size in range(remaining + 1):
        for combo in itertools.combinations(free_cart_eligibles, size):
            buckets.append(frozenset(combo))
    return buckets


def _cart_bucket_for_restart(
    buckets: list[frozenset[str]], *, restart_index: int
) -> frozenset[str]:
    """Pick the cart-assignment bucket for the given restart (round-robin)."""
    if not buckets:
        return frozenset()
    return buckets[restart_index % len(buckets)]


def _perturb_plane(
    *,
    current: Placement,
    scenario: Scenario,
    rng: _random_module.Random,
    search: SearchConfig,
    large_jump: bool,
) -> Placement:
    """One candidate perturbation for the named plane (spec §4.3).

    - ``large_jump=True`` re-samples ``(x, y)`` and ``heading_deg``
      globally (uniform inside hangar with bbox margin; heading uniform
      on ``[0, 360°)``).
    - ``large_jump=False`` does a small Gaussian nudge: ``(dx, dy)`` ~
      ``N(0, pos_sigma_m)`` and ``dh`` ~ ``N(0, heading_sigma_deg)``,
      clamped to hangar bounds (margin-protected).

    The 180° heading-flip variant is handled by the caller
    (:func:`_descent_step`) as a third variant.

    ``on_carts`` is preserved from ``current`` — the cart assignment is
    fixed at restart time (spec §4.2) and never perturbed within a
    trajectory.
    """
    hangar = scenario.hangar
    plane = scenario.fleet[current.plane_id]
    max_length, max_width = _plane_max_extent(plane)
    margin = max(max_length, max_width) / 2

    if large_jump:
        x_lo, x_hi = margin, hangar.width_m - margin
        y_lo, y_hi = margin, hangar.length_m - margin
        x = hangar.width_m / 2 if x_hi <= x_lo else rng.uniform(x_lo, x_hi)
        y = hangar.length_m / 2 if y_hi <= y_lo else rng.uniform(y_lo, y_hi)
        # Exclusive upper bound — see _initial_placement_for_plane note.
        heading = rng.random() * 360.0
    else:
        dx = rng.gauss(0.0, search.pos_sigma_m)
        dy = rng.gauss(0.0, search.pos_sigma_m)
        dh = rng.gauss(0.0, search.heading_sigma_deg)
        # Clamp to hangar bounds (margin-protected)
        x = max(margin, min(hangar.width_m - margin, current.x_m + dx))
        y = max(margin, min(hangar.length_m - margin, current.y_m + dy))
        heading = (current.heading_deg + dh) % 360.0

    return Placement(
        plane_id=current.plane_id,
        x_m=x,
        y_m=y,
        heading_deg=heading,
        on_carts=current.on_carts,
    )


def _generate_candidates(
    *,
    current: Placement,
    target: str,
    scenario: Scenario,
    rng: _random_module.Random,
    search: SearchConfig,
) -> list[Placement]:
    """Build the standard candidate mix for one plane (spec §4.3 step 4).

    ``N = candidates_per_iter`` candidates, generated in a fixed order that
    pins the RNG draw sequence: ``N - 2`` small Gaussian nudges, then 1 large
    global jump, then 1 deterministic 180° heading flip. The two RNG-driven
    variants (small nudges, large jump) consume entropy in exactly this order;
    the flip draws nothing.

    Shared verbatim by :func:`_descent_step` (min-conflicts descent) and
    :func:`_spread` (the inter-plane spread post-pass) — both need the identical
    mix. The extraction is byte-for-byte behaviour-preserving: keeping the draw
    order (small × ``n_small`` → large → flip) is what preserves the ADR-0003
    determinism contract, so do not reorder the appends.
    """
    candidates: list[Placement] = []
    n_small = max(0, search.candidates_per_iter - 2)
    for _ in range(n_small):
        candidates.append(
            _perturb_plane(
                current=current,
                scenario=scenario,
                rng=rng,
                search=search,
                large_jump=False,
            )
        )
    candidates.append(
        _perturb_plane(
            current=current,
            scenario=scenario,
            rng=rng,
            search=search,
            large_jump=True,
        )
    )
    candidates.append(
        Placement(
            plane_id=target,
            x_m=current.x_m,
            y_m=current.y_m,
            heading_deg=(current.heading_deg + 180.0) % 360.0,
            on_carts=current.on_carts,
        )
    )
    return candidates


def _descent_step(
    *,
    placements: dict[str, Placement],
    scenario: Scenario,
    rng: _random_module.Random,
    search: SearchConfig,
    current_score: tuple[int, float],
    pinned_planes: frozenset[str],
) -> tuple[dict[str, Placement], tuple[int, float], bool] | None:
    """Run one min-conflicts iteration (spec §4.3).

    Returns ``(new_placements, new_score, accepted)`` where ``accepted``
    is ``True`` whenever any candidate beat the (score, displacement)
    tracker — *including* the pure-displacement tiebreaker case where
    score didn't change but a closer move was preferred. Caller's
    "score improved" check at ``solve()``'s descent loop reads the
    score directly (``new_score < current_score``) and does not depend
    on this flag's exact semantic. Returns ``None`` if the trajectory
    should restart (all conflicts involve only pinned planes — locally
    unsolvable).

    Algorithm per spec §4.3:

    1. Build a Layout from ``placements`` and score it (free invariant
       check — ``Layout.__post_init__`` rejects cart-rule violations,
       so this also screens for those).
    2. Take the conflicting-plane set ``S = (⋃ c.planes) − pinned``. If
       empty → return ``None`` (trajectory stuck).
    3. Pick one plane from ``S`` uniformly at random.
    4. Generate ``N = candidates_per_iter`` candidates for that plane:
       ``N - 2`` small Gaussian nudges, 1 large jump, 1 180° flip.
    5. Score each candidate's tentative Layout; pick the best (lowest)
       score. Ties broken by smallest displacement from the current
       state (smooth trajectory).
    6. Return the winning placements (greedy ≤; updates whenever the
       loop body sets ``best_cand``).

    Candidates whose tentative Layout violates a ``Layout`` invariant
    (e.g. cart rule) raise ``ValueError`` from
    ``Layout.__post_init__`` and are skipped.
    """
    # Build current Layout from placements (uses Layout invariants — free check).
    current_layout = Layout(
        fleet=scenario.fleet,
        hangar=scenario.hangar,
        placements=tuple(placements.values()),
        maintenance_plane=scenario.maintenance_plane,
    )

    current_result = check_layout(current_layout)

    # Build conflicting-plane set (excluding pinned). `sorted()` later
    # ensures `rng.choice` sees a deterministic ordering — set iteration
    # order would otherwise leak into RNG state.
    conflicting: set[str] = set()
    for c in current_result.conflicts:
        for pid in c.planes:
            if pid not in pinned_planes:
                conflicting.add(pid)
    if not conflicting:
        return None  # restart — all conflicts are on pinned planes

    target = rng.choice(sorted(conflicting))

    # Generate N candidate perturbations: (N-2) small + 1 large + 1 flip
    # (shared with _spread; see _generate_candidates for the draw-order contract).
    candidates = _generate_candidates(
        current=placements[target],
        target=target,
        scenario=scenario,
        rng=rng,
        search=search,
    )

    # Score each candidate. Tie-break by displacement from the current
    # state (encourages smooth trajectories — spec §4.3 step 4).
    #
    # Implicit policy note: the 180°-flip candidate has displacement
    # exactly 0 (it doesn't move position), which beats any Gaussian
    # nudge's strictly-positive displacement when scores tie. Effect:
    # heading-only changes are preferred over position+heading changes
    # on tie. This matches "smallest motion wins" and is desirable for
    # local optima escape, but it's an implicit choice — don't
    # accidentally invert it.
    best_score = current_score
    best_placements = placements
    best_cand: Placement | None = None
    best_disp = float("inf")
    for cand in candidates:
        trial = dict(placements)
        trial[target] = cand
        try:
            trial_layout = Layout(
                fleet=scenario.fleet,
                hangar=scenario.hangar,
                placements=tuple(trial.values()),
                maintenance_plane=scenario.maintenance_plane,
            )
        except ValueError:
            # Layout invariant violated (cart rule, etc.) — skip this candidate
            continue
        s = _score(trial_layout)
        disp = (
            (cand.x_m - placements[target].x_m) ** 2 + (cand.y_m - placements[target].y_m) ** 2
        ) ** 0.5
        if (s < best_score) or (s == best_score and disp < best_disp):
            best_score = s
            best_placements = trial
            best_cand = cand
            best_disp = disp

    # By construction the loop only updates best_score when the new
    # candidate's score is ≤ best_score (which starts at current_score),
    # so best_score ≤ current_score whenever best_cand is not None. No
    # need to re-test that — just dispatch on whether anything improved.
    if best_cand is None:
        return placements, current_score, False
    return best_placements, best_score, True


def _heading_delta_short_arc(a: float, b: float) -> float:
    """Shortest angular distance on the circle, in degrees.

    Returns the shorter of the two arcs between ``a`` and ``b`` measured
    on the unit circle, in ``[0.0, 180.0]``. Equivalent to
    ``min(|a-b| mod 360, 360 - |a-b| mod 360)``. So 0° vs 359° → 1°,
    not 359°. Spec §4.6 requires this short-arc distance for the
    diversity-filter heading test; using raw ``|a - b|`` would make the
    filter mis-classify near-identical headings across the wrap as
    "moved" and silently degrade diversity.

    The ``% 360.0`` is defensive against headings outside ``[0, 360)`` —
    ``Placement.__post_init__`` currently doesn't validate the range
    (filed as follow-up after PR #89). Once that lands and Placement
    headings are guaranteed canonical, the modulo becomes a no-op (still
    correct, just unnecessary).
    """
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


class _SpreadCandidate(NamedTuple):
    """A valid, spread-polished basin found during search, with its quality."""

    layout: Layout
    min_gap: float
    energy: float
    restart_index: int
    nose_out_flips: int = 0


def _select_spread_diverse(
    pool: list[_SpreadCandidate],
    alternatives: int,
    diversity: DiversityConfig,
) -> tuple[list[_SpreadCandidate], int]:
    """Select up to ``alternatives`` best-spread, pairwise-diverse candidates.

    Order the pool by ``(−min_gap, energy, restart_index)``: largest minimum
    plan-view gap first, ties broken by lower repulsion energy, then by restart
    order for a *total* (so deterministic — ADR-0003) ordering. Greedily accept
    a candidate iff it is diverse enough (ADR-0004) against everything already
    selected; the first pick is always accepted (diversity is vacuous on the
    empty selection). Returns ``(selected, diversity_rejected)`` in best-spread
    order, where ``diversity_rejected`` counts candidates *examined* (before the
    ``alternatives`` quota was met) that the diversity gate turned away. For
    ``alternatives == 1`` this is always 0 — selection stops after the first,
    vacuous pick.
    """
    ordered = sorted(pool, key=lambda c: (-c.min_gap, c.energy, c.restart_index))
    selected: list[_SpreadCandidate] = []
    diversity_rejected = 0
    for cand in ordered:
        if _is_diverse_enough(cand.layout, [c.layout for c in selected], diversity):
            selected.append(cand)
            if len(selected) >= alternatives:
                break
        else:
            diversity_rejected += 1
    return selected, diversity_rejected


def _is_diverse_enough(
    candidate: Layout,
    accepted: list[Layout],
    diversity: DiversityConfig,
) -> bool:
    """Return True iff candidate differs from every accepted layout (spec §4.6).

    For each already-accepted layout, count the number of planes in the
    candidate whose placement differs by at least ``position_threshold_m``
    of Euclidean distance OR at least ``heading_threshold_deg`` of
    short-arc heading (a plane absent from the reference counts as moved).
    The candidate is "diverse enough" iff that count meets
    ``min_planes_moved`` against EVERY accepted layout — pairwise diversity,
    not aggregate.

    Iterates ``candidate.placements`` (a tuple, deterministic) and
    ``accepted`` (a list, deterministic). The intermediate ``cand_by_id``
    and ``L_by_id`` dicts are only used for O(1) plane-id lookups; no
    iteration order leaks out.
    """
    cand_by_id = {p.plane_id: p for p in candidate.placements}
    for L in accepted:
        L_by_id = {p.plane_id: p for p in L.placements}
        n_moved = 0
        for pid, cand_p in cand_by_id.items():
            ref = L_by_id.get(pid)
            # Under `solve()` invariants, both `candidate` and every L in
            # `accepted` are built from `scenario.fleet_in` — so plane sets
            # always match and `ref` is never None. Make that invariant
            # load-bearing with an assertion: if a future caller passes
            # mismatched layouts (e.g., an external/test invocation), we
            # want a sharp AssertionError, not the silent "absent ≡ moved"
            # semantic the previous defensive branch implemented.
            assert ref is not None, (
                f"diversity: candidate has plane {pid!r} not present in an "
                f"accepted layout (fleet mismatch; only the in-solver flow "
                f"is supported)"
            )
            pos_delta = ((cand_p.x_m - ref.x_m) ** 2 + (cand_p.y_m - ref.y_m) ** 2) ** 0.5
            head_delta = _heading_delta_short_arc(cand_p.heading_deg, ref.heading_deg)
            if (
                pos_delta >= diversity.position_threshold_m
                or head_delta >= diversity.heading_threshold_deg
            ):
                n_moved += 1
        if n_moved < diversity.min_planes_moved:
            return False  # too similar to this accepted layout
    return True


def _empty_layout(scenario: Scenario) -> Layout:
    """Build a placement-less Layout for pairing with a synthetic CheckResult.

    Checks #1 and #2 of :func:`_check_trivially_infeasible` reject the
    scenario before any placements have been sampled. There is no real
    Layout to attach to the diagnostic CheckResult, but
    :class:`SolverDiagnostics` requires ``best_partial`` and
    ``best_partial_layout`` to be a fused pair. An empty Layout —
    same fleet and hangar, no placements, no maintenance plane — is
    the natural "we never got far enough to place anything" stand-in
    and satisfies ``Layout.__post_init__`` (which only mandates that
    a non-None ``maintenance_plane`` be placed).
    """
    return Layout(
        fleet=scenario.fleet,
        hangar=scenario.hangar,
        placements=(),
        maintenance_plane=None,
    )
