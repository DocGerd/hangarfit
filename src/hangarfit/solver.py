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
    Aircraft,
    CheckResult,
    Conflict,
    DiversityConfig,
    Layout,
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

    # ── Pre-search infeasibility checks (§4.1) ──────────────────────────
    start = time.monotonic()

    infeasible = _check_trivially_infeasible(scenario)
    if infeasible is not None:
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
            ),
        )

    elapsed = time.monotonic() - start

    # Chunk C: actual search not yet implemented — short-circuit to
    # exhausted_budget for any feasible scenario.
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


def _check_trivially_infeasible(
    scenario: Scenario,
) -> tuple[CheckResult, Layout] | None:
    """Run the three literal-impossibility checks from spec §4.1.

    Returns ``(check_result, layout)`` if the scenario is provably
    infeasible (the caller plugs both into
    :class:`SolverDiagnostics`); else ``None``.

    The Layout is paired with the CheckResult because
    :class:`SolverDiagnostics` requires ``best_partial`` and
    ``best_partial_layout`` to both be set or both be ``None``. For
    checks #1 and #2 (no candidate placements exist yet), the paired
    Layout is the "empty" Layout — same fleet and hangar as the
    scenario, with no placements and no maintenance plane. For check #3
    it is the pin-only Layout that was used to detect the conflict.
    """
    # Check 1: per-plane bbox vs hangar
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

    # Check 2: Σ bbox areas vs hangar floor area
    total_area = 0.0
    for pid in scenario.fleet_in:
        plane = scenario.fleet[pid]
        length, width = _plane_max_extent(plane)
        total_area += length * width
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

    # Check 3 lands in Task C.4.
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
