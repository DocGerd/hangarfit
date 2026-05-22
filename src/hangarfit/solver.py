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

from hangarfit.collisions import check as check_layout
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

    # Resolve seed. Validate eagerly — a bad seed would raise here, at
    # solve() entry, instead of 30 s into Chunk D's search loop. The rng
    # itself will be re-created when search lands; the construction here
    # is deliberately preserved (don't "clean it up" as dead code).
    resolved_seed = seed if seed is not None else secrets.randbits(32)
    rng = _random_module.Random(resolved_seed)
    del rng

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

    # Check 3: pin self-collision (build a pin-only Layout and run check())
    pinned_placements = []
    for pid in scenario.fleet_in:
        constraint = scenario.constraints.get(pid)
        if constraint is not None and constraint.pin is not None:
            pinned_placements.append(constraint.pin)

    if pinned_placements:
        # The cart rule (at most one cart_eligible plane on carts at a
        # time) is enforced by Layout.__post_init__ but NOT by
        # Scenario.__post_init__ — that's a cross-pin invariant Scenario
        # currently doesn't check. Guard explicitly here so we can return
        # a sharp `pin_cart_rule` conflict instead of silently absorbing
        # a generic Layout `ValueError`. Morally this check belongs in
        # Scenario; a follow-up could migrate it to Scenario.__post_init__
        # so every caller (not just solve()) benefits.
        cart_eligible_on_carts = sum(
            1
            for p in pinned_placements
            if p.on_carts and scenario.fleet[p.plane_id].movement_mode == "cart_eligible"
        )
        if cart_eligible_on_carts > 1:
            check = CheckResult(
                conflicts=(
                    Conflict.single(
                        kind="trivially_infeasible_pin_cart_rule",
                        plane=pinned_placements[0].plane_id,
                        detail=(
                            f"{cart_eligible_on_carts} cart_eligible pins on "
                            f"carts (cart rule allows at most 1)"
                        ),
                    ),
                ),
                total_penetration_m2=0.0,
            )
            return check, _empty_layout(scenario)

        # Build a Layout containing ONLY the pinned planes.
        # maintenance_plane=None to bypass Layout's "maintenance must be placed"
        # invariant; we're only checking pin-vs-pin and pin-vs-hangar here.
        # (Spec §4.1 step 3 spells this out explicitly: the pre-search check is
        # only about pin-vs-pin self-collision and pin-vs-hangar bounds; the
        # maintenance-position rule is not in scope at this stage because no
        # maintenance plane is set.)
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
            maintenance_plane=None,
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
