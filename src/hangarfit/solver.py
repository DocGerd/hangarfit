"""Phase 2a static layout solver.

See ``docs/superpowers/specs/2026-05-22-phase2a-static-layout-solver-design.md``
for the full design. This module is built incrementally across multiple PRs;
the current implementation supports:

- pre-search infeasibility detection (§4.1)  [Chunk C]
- random-restart hill climb with min-conflicts descent (§4.2-§4.4)  [Chunk D]
- K-diverse alternatives + termination (§4.5-§4.7)  [Chunk E]
- inter-plane spread post-pass (``_spread`` / ``_inter_plane_energy``; ADR-0008,
  default on via ``SearchConfig.spread``)
"""

from __future__ import annotations

import itertools
import logging
import math
import random as _random_module
import secrets
import sys
import time
from typing import NamedTuple

from hangarfit.collisions import check as check_layout
from hangarfit.geometry import WorldPart, aircraft_parts_world
from hangarfit.models import (
    Aircraft,
    CheckResult,
    Conflict,
    DiversityConfig,
    Layout,
    Placement,
    Scenario,
    SearchConfig,
    SolverDiagnostics,
    SolveResult,
    SolveStatus,
)
from hangarfit.towplanner import MovesPlan, NoFeasiblePlanError, plan_fill

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
    """
    if diversity is None:
        diversity = DiversityConfig()
    if search is None:
        search = SearchConfig()

    # Resolve seed. Validate eagerly — a bad seed would raise here, at
    # solve() entry, instead of 30 s into the search loop. ONE
    # random.Random instance drives every sampling decision (spec §4.8):
    # initial placement, perturbation, candidate selection, conflict-
    # plane pick. Cart-bucket round-robin uses the non-random restart
    # index, so it's deterministic by construction.
    resolved_seed = seed if seed is not None else secrets.randbits(32)
    rng = _random_module.Random(resolved_seed)

    # ── Diversity-impossible heuristic (spec §4.6) ──────────────────────
    # When the number of free (non-pinned) planes is strictly less than
    # diversity.min_planes_moved AND the caller asked for >1 alternative,
    # they cannot mathematically get more than one accepted layout: every
    # candidate L' after the first will share too many pinned planes with
    # L to ever pass `n_moved ≥ min_planes_moved`. Logged as a warning
    # so CLI / library users see it; we do NOT mutate `alternatives` —
    # search runs normally and the natural outcome is found_partial with
    # one accepted layout (spec deliberately avoids downgrading the API
    # contract). Pin-detection mirrors `_check_trivially_infeasible`.
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
                diversity_impossible=diversity_impossible,
            ),
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

    # Outer restart loop. Two independent termination gates; first to
    # trip wins:
    #   1. Wall-clock budget (`budget_s`) — always present.
    #   2. Restart count (`search.max_restarts`) — opt-in via v0.6.0's
    #      SearchConfig field (spec §4.2). `None` preserves the
    #      pre-v0.6.0 wall-clock-only behavior. Useful for
    #      cross-machine-deterministic exhaustion canaries.
    # Selection of the K best basins happens after the loop completes;
    # the loop itself runs only to budget / max_restarts.
    while time.monotonic() - start < budget_s and (
        search.max_restarts is None or restart_index < search.max_restarts
    ):
        cart_bucket = _cart_bucket_for_restart(cart_buckets, restart_index=restart_index)
        try:
            placements = _initial_placements(scenario=scenario, rng=rng, cart_bucket=cart_bucket)
        except _LayoutBuildFailure:
            restart_index += 1
            continue

        # Initial Layout build.
        #
        # No try/except: under current invariants, NO Layout.__post_init__
        # ValueError is reachable here. `_enumerate_cart_buckets` already
        # filters bucket assignments that would violate the cart rule;
        # Scenario invariants enforce pin/on_carts/movement_mode consistency;
        # `_initial_placements` skips the maintenance plane so the
        # bay-occupant invariant holds; remaining placements are over
        # ``scenario.fleet_in − {maintenance_plane}`` (no duplicates, all in
        # fleet). Any ValueError here would mean a real bug — let it
        # propagate as one rather than burn the budget on silent restart
        # absorption.
        initial_layout = Layout(
            fleet=scenario.fleet,
            hangar=scenario.hangar,
            placements=tuple(placements.values()),
            maintenance_plane=scenario.maintenance_plane,
        )
        current_score = _score(initial_layout)
        # Track the initial layout as a best-partial candidate too — it
        # may be the lowest-score thing we see in this trajectory.
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
                # _spread preserves every Layout invariant; a ValueError here
                # would be a structural bug, so let it propagate.
                candidate_layout = Layout(
                    fleet=scenario.fleet,
                    hangar=scenario.hangar,
                    placements=tuple(placements.values()),
                    maintenance_plane=scenario.maintenance_plane,
                )
                min_gap, energy = _spread_quality(placements, scenario, spread_scale)
                pool.append(
                    _SpreadCandidate(
                        layout=candidate_layout,
                        min_gap=min_gap,
                        energy=energy,
                        restart_index=restart_index,
                    )
                )
                break  # restart to seek a different basin

            step_result = _descent_step(
                placements=placements,
                scenario=scenario,
                rng=rng,
                search=search,
                current_score=current_score,
                pinned_planes=pinned_planes,
            )
            if step_result is None:
                break  # restart (all conflicts on pinned planes)
            placements, new_score, _accepted = step_result
            if new_score < current_score:
                last_improved = iter_count
            current_score = new_score

            # Track best partial AFTER the move (lowest-score Layout ever seen)
            if current_score < best_partial_score:
                best_partial_score = current_score
                best_partial_layout = Layout(
                    fleet=scenario.fleet,
                    hangar=scenario.hangar,
                    placements=tuple(placements.values()),
                    maintenance_plane=scenario.maintenance_plane,
                )

            if iter_count - last_improved >= search.k_stall:
                break  # stall — restart

        restart_index += 1

        # Best-of-all-basins selection (#267) only adds value when the spread
        # post-pass can reorder basins by separation quality. With spread
        # disabled there is nothing to optimize, so continuing past
        # `alternatives` diverse valid layouts cannot improve the result —
        # restore the pre-#267 early exit. This keeps the `--no-spread` fast
        # path (the speed escape hatch) and gives a seed-deterministic
        # termination for that mode (independent of wall-clock).
        if not search.spread:
            selected_so_far, _ = _select_spread_diverse(pool, alternatives, diversity)
            if len(selected_so_far) >= alternatives:
                break

    elapsed = time.monotonic() - start

    selected, diversity_rejected_count = _select_spread_diverse(pool, alternatives, diversity)

    if selected:
        accepted_layouts = [c.layout for c in selected]
        min_gaps = tuple(c.min_gap for c in selected)
        status: SolveStatus = "found" if len(accepted_layouts) >= alternatives else "found_partial"
        # Tow-plan every returned layout (best-effort enrichment, #197). The
        # v2 planner (Reeds–Shepp arcs + bounded Hybrid-A* — #222/#261 under
        # ADR-0007 + ADR-0010) cannot route dense multi-plane fills and has
        # documented false-negatives, so an un-routable layout is recorded as
        # plans[i]=None rather than discarding the otherwise-valid static
        # arrangement — the layout is the headline answer; the tow plan is
        # advisory (spike Risk #8). The `status` stays search-driven:
        # tow-planning never changes found/found_partial.
        plans: tuple[MovesPlan | None, ...]
        unroutable: list[str] = []
        if plan_paths:
            built: list[MovesPlan | None] = []
            for layout in accepted_layouts:
                try:
                    built.append(plan_fill(layout))
                except NoFeasiblePlanError as e:
                    built.append(None)
                    unroutable.append(e.plane_id)
                    # Log the conflict kind/detail too, not just the plane: it
                    # distinguishes a genuinely-boxed-in plane from a Hybrid-A*
                    # budget exhaustion (a known false-negative class), which
                    # call for different operator responses.
                    _logger.warning(
                        "layout not tow-routable by the tow-path planner: plane %r blocked "
                        "(%s: %s); returning the valid static layout without a tow plan",
                        e.plane_id,
                        e.conflict.kind,
                        e.conflict.detail,
                    )
            plans = tuple(built)
        else:
            plans = (None,) * len(accepted_layouts)
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
                unroutable_planes=tuple(unroutable),
                min_pairwise_gap_m=min_gaps,
                valid_basins_found=len(pool),
            ),
        )
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
            valid_basins_found=len(pool),
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

    # Check 2: Σ bbox areas vs hangar floor area. Uses the full-fuselage
    # footprint (not _plane_max_extent) so the fuselage front/aft split
    # (#50/ADR-0012) doesn't shrink the estimate and let an infeasible fleet
    # slip the gate.
    total_area = 0.0
    for pid in scenario.fleet_in:
        plane = scenario.fleet[pid]
        total_area += _plane_footprint_area(plane)
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


def _plane_footprint_area(plane: Aircraft) -> float:
    """A bbox-area lower bound for the Σ-areas infeasibility gate (check #2).

    Like :func:`_plane_max_extent` this is a deliberately coarse, offset-
    ignoring estimate, but it must NOT undercount the fuselage: the loader
    splits one fuselage box into a ``fuselage_front`` + ``fuselage_aft`` pair
    (#50/ADR-0012), so a plain ``max(length_m)`` over parts would collapse the
    fuselage extent to its longer *segment* and shrink the footprint estimate
    — making a genuinely-infeasible full fleet slip past the gate. Reconstruct
    the full fuselage span (union of the segments, the same way the area is
    conserved at load time) before taking the max length.

    Kept separate from :func:`_plane_max_extent` on purpose: that function also
    feeds the initial-placement spawn margin, and changing its return value
    would shift the solver's RNG stream and disturb the determinism canaries.
    This helper is consumed only by the infeasibility gate, where no RNG flows.
    """
    fuselage_segs = [p for p in plane.parts if p.kind in ("fuselage_front", "fuselage_aft", "tail")]
    lengths = [p.length_m for p in plane.parts if p.kind not in ("fuselage_front", "fuselage_aft")]
    if fuselage_segs:
        nose = max(p.offset_x_m + p.length_m / 2.0 for p in fuselage_segs)
        tail = min(p.offset_x_m - p.length_m / 2.0 for p in fuselage_segs)
        lengths.append(nose - tail)
    max_length = max(lengths)
    max_width = max(p.width_m for p in plane.parts)
    return max_length * max_width


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


def _inter_plane_energy(
    placements: dict[str, Placement],
    scenario: Scenario,
    scale: float,
) -> float:
    """Smooth repulsion energy ``E = Σ_{i<j} exp(−gap_ij / scale)`` (spec §4).

    ``gap_ij`` is the minimum plan-view edge-to-edge distance between plane
    ``i``'s and plane ``j``'s world parts (shapely ``polygon.distance``).
    Lower ``E`` ⇒ planes further apart; close pairs dominate the sum, so
    minimizing it maximizes the *minimum* gap (a smooth maximin surrogate).
    Returns ``0.0`` when fewer than two planes are present. Ignores z
    (plan-view only) — see ADR-0008 for the nesting limitation.
    """
    ids = sorted(placements)
    if len(ids) < 2:
        return 0.0
    world: dict[str, list[WorldPart]] = {
        pid: aircraft_parts_world(scenario.fleet[pid], placements[pid]) for pid in ids
    }
    energy = 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            gap = min(
                pa.polygon.distance(pb.polygon) for pa in world[ids[i]] for pb in world[ids[j]]
            )
            energy += math.exp(-gap / scale)
    return energy


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
    planes' world parts (``math.inf`` when <2 planes — no pairs). ``energy``
    is the same ``Σ exp(−gap/scale)`` repulsion :func:`_inter_plane_energy`
    computes; returning both from one pairwise sweep avoids paying the
    (expensive) shapely distances twice when scoring a candidate basin. The
    hot ``_spread`` loop keeps using the energy-only :func:`_inter_plane_energy`
    — this is called once per accepted basin, not per perturbation.
    """
    ids = sorted(placements)
    if len(ids) < 2:
        return (math.inf, 0.0)
    world: dict[str, list[WorldPart]] = {
        pid: aircraft_parts_world(scenario.fleet[pid], placements[pid]) for pid in ids
    }
    min_gap = math.inf
    energy = 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            gap = min(
                pa.polygon.distance(pb.polygon) for pa in world[ids[i]] for pb in world[ids[j]]
            )
            min_gap = min(min_gap, gap)
            energy += math.exp(-gap / scale)
    return (min_gap, energy)


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
    if not movable or len(placements) < 2:
        # Nothing to optimize: all planes pinned (no movable target) or
        # <2 planes (energy is identically 0.0).
        return placements

    current_energy = _inter_plane_energy(placements, scenario, scale)
    last_improved = 0

    for iter_count in range(10000):  # large cap; real exit via stall/budget
        if time.monotonic() - start >= budget_s:
            break

        target = rng.choice(movable)

        # Same candidate mix as _descent_step: (N-2) small nudges + 1 large + 1 flip.
        candidates: list[Placement] = []
        n_small = max(0, search.candidates_per_iter - 2)
        for _ in range(n_small):
            candidates.append(
                _perturb_plane(
                    current=placements[target],
                    scenario=scenario,
                    rng=rng,
                    search=search,
                    large_jump=False,
                )
            )
        candidates.append(
            _perturb_plane(
                current=placements[target],
                scenario=scenario,
                rng=rng,
                search=search,
                large_jump=True,
            )
        )
        candidates.append(
            Placement(
                plane_id=target,
                x_m=placements[target].x_m,
                y_m=placements[target].y_m,
                heading_deg=(placements[target].heading_deg + 180.0) % 360.0,
                on_carts=placements[target].on_carts,
            )
        )

        # Pick the lowest-(energy, displacement) VALID candidate. Adopt it
        # only if its energy is strictly below current (so the stall counter
        # advances on non-improving iterations — no plateau-wander livelock).
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
            e = _inter_plane_energy(trial, scenario, scale)
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
    candidates: list[Placement] = []
    n_small = max(0, search.candidates_per_iter - 2)
    for _ in range(n_small):
        candidates.append(
            _perturb_plane(
                current=placements[target],
                scenario=scenario,
                rng=rng,
                search=search,
                large_jump=False,
            )
        )
    candidates.append(
        _perturb_plane(
            current=placements[target],
            scenario=scenario,
            rng=rng,
            search=search,
            large_jump=True,
        )
    )
    flipped = Placement(
        plane_id=target,
        x_m=placements[target].x_m,
        y_m=placements[target].y_m,
        heading_deg=(placements[target].heading_deg + 180.0) % 360.0,
        on_carts=placements[target].on_carts,
    )
    candidates.append(flipped)

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
