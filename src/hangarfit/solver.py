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
import sys
import time

from hangarfit.collisions import check as check_layout
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
    # `diversity` is accepted in the signature for forward compatibility
    # with Chunk E (K-diversity filter); the alternatives=1 path in this
    # chunk never consults it. Reference it once so static analysis
    # doesn't flag it as unused without obscuring intent.
    _ = diversity

    # Resolve seed. Validate eagerly — a bad seed would raise here, at
    # solve() entry, instead of 30 s into the search loop. ONE
    # random.Random instance drives every sampling decision (spec §4.8):
    # initial placement, perturbation, candidate selection, conflict-
    # plane pick. Cart-bucket round-robin uses the non-random restart
    # index, so it's deterministic by construction.
    resolved_seed = seed if seed is not None else secrets.randbits(32)
    rng = _random_module.Random(resolved_seed)

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
    accepted_layouts: list[Layout] = []
    restart_index = 0

    while time.monotonic() - start < budget_s:
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
        # placements are over `scenario.fleet_in` (no duplicates, all in
        # fleet); the maintenance plane is in placements by construction.
        # Any ValueError here would mean a real bug — let it propagate as
        # one rather than burn the budget on silent restart absorption.
        initial_layout = Layout(
            fleet=scenario.fleet,
            hangar=scenario.hangar,
            placements=tuple(placements[pid] for pid in scenario.fleet_in),
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
                # Valid! Accept (no diversity filter yet in Chunk D — just take it)
                accepted_layouts.append(
                    Layout(
                        fleet=scenario.fleet,
                        hangar=scenario.hangar,
                        placements=tuple(placements[pid] for pid in scenario.fleet_in),
                        maintenance_plane=scenario.maintenance_plane,
                    )
                )
                break  # found one; outer loop terminates because alternatives=1

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
                    placements=tuple(placements[pid] for pid in scenario.fleet_in),
                    maintenance_plane=scenario.maintenance_plane,
                )

            if iter_count - last_improved >= search.k_stall:
                break  # stall — restart

        restart_index += 1
        if len(accepted_layouts) >= alternatives:
            break

    elapsed = time.monotonic() - start

    if accepted_layouts:
        # alternatives=1 in Chunk D — found_partial cannot fire (we break
        # the outer loop as soon as len(accepted) >= alternatives), but
        # keep the branch shape so Chunk E's K-diversity addition is a
        # one-liner.
        status: SolveStatus = "found" if len(accepted_layouts) >= alternatives else "found_partial"
        return SolveResult(
            status=status,
            layouts=tuple(accepted_layouts),
            diagnostics=SolverDiagnostics(
                restarts_attempted=restart_index,
                wall_time_s=elapsed,
                best_partial=None,
                best_partial_layout=None,
                seed=resolved_seed,
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
        #
        # ``maintenance_plane`` is set on the pin-only Layout iff the
        # maintenance plane is itself pinned. This lets `collisions.check()`
        # fire the maintenance_position rule on a pinned-out-of-bay
        # maintenance plane — without it, that case would silently slip
        # past pre-search and burn the entire solve() budget in the
        # trajectory loop (every restart would hit the same conflict on
        # the pinned plane, `_descent_step` would return None, restart
        # again, ad infinitum until budget).
        #
        # If the maintenance plane is NOT pinned, we leave maintenance
        # off the pin-only Layout — its position will be sampled freely
        # during search, so pre-search has nothing to check.
        #
        # No try/except: every remaining Layout invariant that could fire
        # here is either structurally impossible given pin-only construction
        # or already caught by Scenario.__post_init__ (pin.plane_id mismatch,
        # pin.on_carts vs movement_mode). A genuinely unexpected ValueError
        # should propagate as a bug, not get silently re-wrapped as a pin
        # infeasibility.
        maintenance_pinned = (
            scenario.maintenance_plane is not None
            and scenario.maintenance_plane in scenario.constraints
            and scenario.constraints[scenario.maintenance_plane].pin is not None
        )
        maint_for_check = scenario.maintenance_plane if maintenance_pinned else None
        pin_only_layout = Layout(
            fleet=scenario.fleet,
            hangar=scenario.hangar,
            placements=tuple(pinned_placements),
            maintenance_plane=maint_for_check,
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
    bias_to_maintenance_bay: bool = False,
) -> Placement:
    """Sample an initial :class:`Placement` for one plane (spec §4.2).

    - If pinned → return the pin verbatim.
    - If ``bias_to_maintenance_bay`` → ``(x, y)`` uniform in the back bay strip.
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

    if bias_to_maintenance_bay:
        bay_depth = hangar.maintenance_bay.depth_m
        y_lo = max(margin_y, hangar.length_m - bay_depth)
        y_hi = hangar.length_m - margin_y
    else:
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
    ``on_carts=True`` for this restart (at most one element per the
    enumeration in :func:`_enumerate_cart_buckets`). Per-plane
    ``on_carts`` resolution:

    1. ``constraint.force_on_carts`` (if set) — highest priority.
    2. ``constraint.pin.on_carts`` (if pinned).
    3. Plane's ``movement_mode`` for always_cart / always_own_gear.
    4. Membership in ``cart_bucket`` for cart_eligible planes.

    The maintenance plane gets the bay-bias unless it's pinned (a pin
    already fixes its position; biasing would be a no-op since the pin
    is returned verbatim).
    """
    placements: dict[str, Placement] = {}
    for pid in scenario.fleet_in:
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

        bias = scenario.maintenance_plane == pid and (constraint is None or constraint.pin is None)

        placements[pid] = _initial_placement_for_plane(
            plane_id=pid,
            scenario=scenario,
            rng=rng,
            on_carts=on_carts,
            bias_to_maintenance_bay=bias,
        )

    return placements


def _enumerate_cart_buckets(scenario: Scenario) -> list[frozenset[str]]:
    """Enumerate the cart-assignment buckets to round-robin over (spec §4.2).

    Returns ``C + 1`` buckets — the empty set plus a singleton for each
    unlocked ``cart_eligible`` plane — when no cart_eligible plane is
    pre-committed to ``on_carts=True``. If one IS pre-committed (by
    ``force_on_carts=True`` or a pin with ``on_carts=True``), the
    at-most-one cart-rule slot is already taken; the only feasible
    bucket is ``[frozenset()]``, because any singleton would put a
    second cart_eligible plane on carts and violate the rule.

    Locked cart_eligible planes (any pin, or ``force_on_carts`` set)
    bypass round-robin: their ``on_carts`` state is fixed by the
    constraint. The cart rule is enforced holistically by
    ``Layout.__post_init__`` later — this enumeration just avoids
    wasting restart budget on guaranteed-infeasible configurations.

    Note: a pin with ``on_carts=False`` also locks the plane (so it
    can't appear in any bucket) but does NOT consume the on-carts slot
    — other unlocked cart_eligibles can still be enumerated as
    singletons.

    Iteration is over ``scenario.fleet_in`` (a tuple), not
    ``scenario.fleet`` (a ``MappingProxyType``-wrapped dict view) — so
    bucket ordering is deterministic by construction.
    """
    free_cart_eligibles: list[str] = []
    has_committed_cart_eligible_on_carts = False
    for pid in scenario.fleet_in:
        plane = scenario.fleet[pid]
        if not plane.is_cart_eligible:
            continue
        constraint = scenario.constraints.get(pid)
        if constraint is not None:
            if constraint.pin is not None:
                # Any pin locks the plane out of round-robin. If the pin
                # puts it on carts, the on-carts slot is consumed.
                if constraint.pin.on_carts:
                    has_committed_cart_eligible_on_carts = True
                continue
            if constraint.force_on_carts is not None:
                # force_on_carts=True consumes the on-carts slot.
                if constraint.force_on_carts:
                    has_committed_cart_eligible_on_carts = True
                continue
        free_cart_eligibles.append(pid)

    buckets: list[frozenset[str]] = [frozenset()]
    if not has_committed_cart_eligible_on_carts:
        for pid in free_cart_eligibles:
            buckets.append(frozenset({pid}))
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
    # Build current Layout from placements (uses Layout invariants — free check)
    current_layout = Layout(
        fleet=scenario.fleet,
        hangar=scenario.hangar,
        placements=tuple(placements[pid] for pid in scenario.fleet_in),
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
                placements=tuple(trial[pid] for pid in scenario.fleet_in),
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
    """
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


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
            if ref is None:
                # Plane not in the reference layout — count as moved
                n_moved += 1
                continue
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
