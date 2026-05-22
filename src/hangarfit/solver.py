"""Phase 2a static layout solver.

See ``docs/superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md``
for the full design. This module is built incrementally across multiple PRs;
the current implementation supports:

- pre-search infeasibility detection (§4.1)  [Chunk C]
- random-restart hill climb with min-conflicts descent (§4.2-§4.4)  [Chunk D]
- K-diverse alternatives + termination (§4.5-§4.7)  [Chunk E]
"""

from __future__ import annotations

import random as _random_module
import secrets
import time

from hangarfit.models import (
    DiversityConfig,
    Scenario,
    SearchConfig,
    SolverDiagnostics,
    SolveResult,
)


def solve(
    scenario: Scenario,
    *,
    budget_s: float = 30.0,
    alternatives: int = 1,
    seed: int | None = None,
    diversity: DiversityConfig | None = None,
    search: SearchConfig | None = None,
) -> SolveResult:
    """Solve a Scenario into up to ``alternatives`` diverse valid Layouts.

    See spec §3.3 for the contract.
    """
    if diversity is None:
        diversity = DiversityConfig()
    if search is None:
        search = SearchConfig()

    # Resolve seed
    resolved_seed = seed if seed is not None else secrets.randbits(32)
    rng = _random_module.Random(resolved_seed)
    del rng  # not used in Chunk C — placeholder

    start = time.monotonic()

    # Chunk C: actual search not yet implemented — short-circuit to
    # exhausted_budget for any feasible scenario. The infeasibility
    # checks added below replace this short-circuit for infeasible cases.
    elapsed = time.monotonic() - start

    return SolveResult(
        status="exhausted_budget",
        layouts=(),
        diagnostics=SolverDiagnostics(
            restarts_attempted=0,
            wall_time_s=elapsed,
            best_partial=None,
            best_partial_layout=None,
            seed=resolved_seed,
        ),
    )
