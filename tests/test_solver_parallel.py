"""Tests for #544 ProcessPool parallel restarts.

The contract: ``solve(..., workers=N)`` is **byte-identical** to
``solve(..., workers=1)`` in the ``max_restarts``-bound, spread-on regime
(``_parallel_eligible``), so the worker count never changes the answer — only
the wall-clock. For any other config, ``workers > 1`` transparently runs serial.
These tests bind on ``max_restarts`` (not wall-clock), so they are
load-independent: byte-identity holds regardless of how slow the spawned pool
is under concurrent CPU pressure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hangarfit.loader import load_scenario
from hangarfit.models import SearchConfig
from hangarfit.solver import (
    _enumerate_cart_buckets,
    _parallel_eligible,
    _resolve_spread_scale,
    _RestartOutput,
    _run_restart_worker,
    _run_restarts_parallel,
    solve,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def _placement_signature(result):
    """A hashable, order-preserving signature of every returned layout's
    placements — the byte-identity surface (the floats are compared exactly)."""
    return [
        [(p.plane_id, p.x_m, p.y_m, p.heading_deg, p.on_carts) for p in layout.placements]
        for layout in result.layouts
    ]


# ── _parallel_eligible predicate ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("workers", "max_restarts", "spread", "stall", "expected"),
    [
        (4, 8, True, None, True),  # the eligible regime
        (1, 8, True, None, False),  # serial: workers == 1
        (4, None, True, None, False),  # wall-clock only — count not fixed
        (4, 8, False, None, False),  # spread off → first-valid early exit
        (4, 8, True, 5, False),  # spread-stall early exit active
    ],
)
def test_parallel_eligible_predicate(workers, max_restarts, spread, stall, expected):
    cfg = SearchConfig(max_restarts=max_restarts, spread=spread, spread_stall_restarts=stall)
    assert _parallel_eligible(cfg, workers) is expected


# ── byte-identity: workers=N ≡ workers=1 ──────────────────────────────────


def test_parallel_byte_identical_to_serial_alternatives_one():
    """The headline contract: the same scenario+seed yields bit-identical
    placements at workers=1 and workers=4 (eligible regime)."""
    scenario = load_scenario(FIXTURES / "solve_fresh_alternatives_three.yaml")
    cfg = SearchConfig(max_restarts=8, spread=True)

    serial = solve(scenario, seed=42, budget_s=120.0, search=cfg, plan_paths=False, workers=1)
    parallel = solve(scenario, seed=42, budget_s=120.0, search=cfg, plan_paths=False, workers=4)

    assert serial.status == parallel.status == "found"
    assert _placement_signature(serial) == _placement_signature(parallel)
    # The merge is completion-order-invariant: same pool, same selection.
    assert serial.diagnostics.valid_basins_found == parallel.diagnostics.valid_basins_found
    assert serial.diagnostics.restarts_attempted == parallel.diagnostics.restarts_attempted == 8


def test_parallel_byte_identical_with_alternatives_gt_one():
    """The spike's explicit obligation: the ``alternatives > 1`` diversity-gated
    selection (unexercised by the prototype) reconstructs identically in
    parallel — the full pool is merged before ``_select_spread_diverse``."""
    scenario = load_scenario(FIXTURES / "solve_fresh_alternatives_three.yaml")
    cfg = SearchConfig(max_restarts=10, spread=True)

    serial = solve(
        scenario, seed=7, budget_s=120.0, alternatives=3, search=cfg, plan_paths=False, workers=1
    )
    parallel = solve(
        scenario, seed=7, budget_s=120.0, alternatives=3, search=cfg, plan_paths=False, workers=4
    )

    assert serial.status == parallel.status
    assert len(serial.layouts) == len(parallel.layouts)
    assert _placement_signature(serial) == _placement_signature(parallel)


def test_parallel_byte_identical_best_partial_under_exhaustion():
    """The spike's OTHER obligation: the ``best_partial_layout`` accumulator
    reconstructs identically. A tight fill that never reaches a valid layout
    within ``max_restarts`` exhausts the budget, so the result carries the
    best partial — which must be the same min-over-restarts in parallel."""
    scenario = load_scenario(FIXTURES / "solve_canary_six_planes_tight.yaml")
    cfg = SearchConfig(max_restarts=3, spread=True)

    serial = solve(scenario, seed=99, budget_s=120.0, search=cfg, plan_paths=False, workers=1)
    parallel = solve(scenario, seed=99, budget_s=120.0, search=cfg, plan_paths=False, workers=4)

    assert serial.status == parallel.status == "exhausted_budget"
    bp_serial = serial.diagnostics.best_partial_layout
    bp_parallel = parallel.diagnostics.best_partial_layout
    assert bp_serial is not None and bp_parallel is not None
    assert bp_serial.placements == bp_parallel.placements


# ── transparent serial fallback for non-eligible configs ──────────────────


def test_workers_gt_one_with_spread_off_runs_serial_no_error():
    """``workers > 1`` on a non-eligible config (spread off) does not error and
    returns the serial result — the worker count is simply ignored."""
    scenario = load_scenario(FIXTURES / "solve_fresh_alternatives_three.yaml")
    cfg = SearchConfig(max_restarts=8, spread=False)

    serial = solve(scenario, seed=42, budget_s=120.0, search=cfg, plan_paths=False, workers=1)
    asked_parallel = solve(
        scenario, seed=42, budget_s=120.0, search=cfg, plan_paths=False, workers=4
    )

    assert _placement_signature(serial) == _placement_signature(asked_parallel)


def test_workers_below_one_rejected():
    scenario = load_scenario(FIXTURES / "solve_fresh_alternatives_three.yaml")
    with pytest.raises(ValueError, match="workers must be >= 1"):
        solve(scenario, seed=1, workers=0, plan_paths=False)


# ── coverage of the worker entry point + defensive guard (#561) ───────────
#
# The byte-identity tests above DO execute ``_run_restart_worker`` and
# ``_run_restarts_parallel`` — but inside spawned worker subprocesses, which
# coverage.py does not trace by default (it needs ``concurrency =
# multiprocessing`` + a ``COVERAGE_PROCESS_START`` hook). So those lines read as
# uncovered (codecov/patch gap, #561). These two tests exercise the same code
# IN the main process so coverage can measure it, and additionally pin two
# contracts the subprocess path can only assert end-to-end.


def test_run_restart_worker_in_process_is_pure_function_of_index():
    """Cover the ProcessPool entry point in-process and pin its purity.

    ``_run_restart_worker`` just opens a per-process ``pose_cache_scope`` and
    delegates to ``_run_restart``, so calling it directly is a faithful,
    coverage-visible exercise of the worker wrapper. Two calls with identical
    ``(scenario, seed, restart_index)`` must yield identical output — the
    "pure function of index" property (#544 / ADR-0003) that lets the serial
    and parallel paths agree byte-for-byte.
    """
    scenario = load_scenario(FIXTURES / "solve_fresh_alternatives_three.yaml")
    search = SearchConfig(max_restarts=4, spread=True)
    spread_scale = _resolve_spread_scale(scenario, search)
    cart_buckets = _enumerate_cart_buckets(scenario)
    args = (scenario, 0, 42, search, spread_scale, frozenset(), cart_buckets)

    out = _run_restart_worker(args)
    again = _run_restart_worker(args)

    assert isinstance(out, _RestartOutput)
    # Placement always builds for this feasible fixture, so best_partial_layout
    # is populated (the doubly-None shape only appears on a dead restart).
    assert out.best_partial_layout is not None
    assert again.best_partial_layout is not None
    # Byte-identical across calls: same index → same layout, same candidacy.
    assert out.best_partial_layout.placements == again.best_partial_layout.placements
    assert out.best_partial_score == again.best_partial_score
    assert (out.candidate is None) == (again.candidate is None)


def test_parallel_unfilled_slot_raises_loudly(monkeypatch):
    """Cover the defensive "every restart slot filled" guard in
    ``_run_restarts_parallel``.

    The guard is unreachable in normal runs (one future per index, each
    returns), so force it: if ``as_completed`` yields nothing, no result slot is
    filled and the guard must raise ``AssertionError`` LOUDLY rather than
    silently dropping restarts (the #544 silent-failure review). The pool still
    spawns and the workers still run — we just discard their completions — so
    this also confirms shutdown happens before the guard fires.
    """
    monkeypatch.setattr("concurrent.futures.as_completed", lambda _: iter(()))
    scenario = load_scenario(FIXTURES / "solve_fresh_alternatives_three.yaml")
    search = SearchConfig(max_restarts=2, spread=True)
    spread_scale = _resolve_spread_scale(scenario, search)
    cart_buckets = _enumerate_cart_buckets(scenario)

    with pytest.raises(AssertionError, match="never filled"):
        _run_restarts_parallel(
            scenario,
            seed=1,
            search=search,
            spread_scale=spread_scale,
            pinned_planes=frozenset(),
            cart_buckets=cart_buckets,
            workers=2,
            n_restarts=2,
        )
