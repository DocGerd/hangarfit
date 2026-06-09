"""Tests for solver.solve() — pre-search infeasibility + smoke."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_towplanning(monkeypatch):
    """Keep these infeasibility/smoke tests fast by stubbing tow-planning.

    solve() tow-plans every returned layout by default (``plan_paths=True``,
    #197), and tow-planning runs a bounded Hybrid-A* search per plane —
    seconds on a multi-plane fill. These tests assert on status/seed/diagnostics,
    never on ``plans``, so a trivial ``plan_fill`` stub preserves the default
    code path while removing the cost (the trivially-infeasible cases never
    reach tow-planning anyway). The real planner↔solver integration lives in
    ``tests/test_solver_towplanner.py``.
    """
    import hangarfit.solver as solver_mod
    from hangarfit.towplanner import MovesPlan

    monkeypatch.setattr(
        solver_mod, "plan_fill", lambda target, **kwargs: MovesPlan(target_layout=target, moves=())
    )


def test_solve_resolves_none_seed_to_entropy():
    """seed=None resolves to a random int and is recorded in diagnostics."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    r = solve(s, budget_s=0.1, seed=None)
    assert isinstance(r.diagnostics.seed, int)
    assert r.diagnostics.seed != 0  # entropy is essentially always nonzero


def test_solve_none_seed_produces_different_seeds_across_calls():
    """Spec §4.8 contract: seed=None draws fresh entropy each call.

    Pin the actual entropy property — not just \"nonzero\". A regression
    that hardcoded `resolved_seed = 1` when seed is None would pass the
    previous test but fail this one.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    seeds = {solve(s, budget_s=0.1, seed=None).diagnostics.seed for _ in range(5)}
    # 32-bit entropy → birthday-paradox collision probability across 5
    # draws is ~5.8e-9. Require at least 4 distinct values to give a
    # huge margin while still pinning the contract.
    assert len(seeds) >= 4, f"Expected fresh entropy per call, got seeds={seeds}"


def test_solve_trivially_infeasible_when_plane_too_big_for_hangar():
    """A plane whose bbox exceeds hangar dims → trivially_infeasible."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_plane_too_big.yaml")
    r = solve(s, budget_s=5.0, seed=42)

    assert r.status == "trivially_infeasible"
    assert r.layouts == ()
    # Pre-search check must short-circuit fast (no actual search).
    assert 0.0 <= r.diagnostics.wall_time_s < 0.1
    assert r.diagnostics.restarts_attempted == 0
    # best_partial + best_partial_layout must be paired (both Some).
    bp = r.diagnostics.best_partial
    assert bp is not None
    assert r.diagnostics.best_partial_layout is not None
    # The fixture is also a check #2 candidate (Σ areas) — pinning the
    # specific kind also pins first-match-wins ordering of the 3 checks.
    assert bp.conflicts[0].kind == "trivially_infeasible_plane_too_big"


def test_solve_trivially_infeasible_when_sum_areas_exceeds_hangar():
    """All 9 planes in a deliberately-small hangar → Σ *part-footprint* areas
    (~185 m²) > hangar floor (171 m²) → trivially_infeasible (#425 part-area
    gate, spec §4.1.2). The hangar's 19 m larger dimension clears the 18 m
    glider span so check #1 (per-plane bbox) does not fire first."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _check_plane_too_big, solve

    s = load_scenario("tests/fixtures/solve_infeasible_sum_areas.yaml")
    # Guard the fixture's narrow check-#1 margin (worst plane extent 18 m vs the
    # hangar's 19 m larger dimension): if a future synthetic-fleet edit pushed a
    # span past 19 m, check #1 would fire first and this test would silently pin
    # the wrong conflict kind. Assert check #1 DEFERS here so this stays a
    # check-#2 (Σ part-areas) regression rather than misfiring.
    assert _check_plane_too_big(s) is None
    r = solve(s, budget_s=5.0, seed=42)

    assert r.status == "trivially_infeasible"
    assert 0.0 <= r.diagnostics.wall_time_s < 0.1
    assert r.diagnostics.restarts_attempted == 0
    bp = r.diagnostics.best_partial
    assert bp is not None
    assert r.diagnostics.best_partial_layout is not None
    # Pin the structured kind (downstream consumers branch on this,
    # not the detail string).
    assert bp.conflicts[0].kind == "trivially_infeasible_sum_areas"


def test_area_gate_does_not_false_reject_glider_fleet():
    """#425: a glider-containing fleet whose bounding boxes don't tile the floor
    (Σ bbox ~654 m² > 450 m²) but whose actual part footprints do (Σ parts
    ~185 m² < 450 m²) must NOT be trivially rejected by check #2 — the gate
    sums part footprints now, so the search is allowed to run.

    Asserts the gate directly (`_check_sum_areas` returns None = defers). The
    old bbox gate returned a `trivially_infeasible_sum_areas` conflict here,
    never letting the solver search — the exact false-reject #425 fixes."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _check_plane_too_big, _check_sum_areas

    s = load_scenario("tests/fixtures/solve_infeasible_too_big.yaml")

    # No single plane is too big for the placeholder hangar (check #1 passes),
    # so the fleet genuinely reaches the Σ-areas gate...
    assert _check_plane_too_big(s) is None
    # ...and the Σ-areas gate now DEFERS (part areas fit the floor) instead of
    # firing on the empty air the bounding boxes invent.
    assert _check_sum_areas(s) is None


def test_solve_trivially_infeasible_when_pins_clash():
    """Two pins overlapping at the same coordinates → trivially_infeasible."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_pins_clash.yaml")
    r = solve(s, budget_s=5.0, seed=42)

    assert r.status == "trivially_infeasible"
    assert 0.0 <= r.diagnostics.wall_time_s < 0.1
    assert r.diagnostics.restarts_attempted == 0
    # best_partial + best_partial_layout must be paired (both Some).
    bp = r.diagnostics.best_partial
    assert bp is not None
    assert r.diagnostics.best_partial_layout is not None
    # At least one conflict must reference both pinned planes.
    refs = [set(c.planes) for c in bp.conflicts if len(c.planes) == 2]
    assert any({"aviat_husky", "ctsl"} == r for r in refs), (
        f"Expected a pairwise conflict between aviat_husky and ctsl, got {refs}"
    )


def test_solve_trivially_infeasible_when_two_cart_eligible_pins_on_carts():
    """Two cart_eligible planes both pinned with on_carts=True violates the
    cart rule (at most one cart_eligible on carts at a time).

    This is the case the broad `try/except ValueError` around the pin-only
    Layout was defending — now replaced with an explicit upstream check
    that emits a sharper conflict kind. The pre-check fires BEFORE
    Layout construction, so any other ValueError from Layout propagates
    loudly (no longer silently absorbed as a generic "pin invariant").
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_two_cart_pins.yaml")
    r = solve(s, budget_s=5.0, seed=42)

    assert r.status == "trivially_infeasible"
    bp = r.diagnostics.best_partial
    assert bp is not None
    assert bp.conflicts[0].kind == "trivially_infeasible_pin_cart_rule"


def test_solve_trivially_infeasible_threads_diversity_impossible_when_k_gt_1():
    """Pre-search early-return must thread diversity_impossible into diagnostics.

    Covers solver.py's `_check_trivially_infeasible` branch (around L96-107):
    when the static K>1 ∧ free_planes<min_planes_moved precondition AND the
    trivially-infeasible check both fire, the SolveResult's diagnostics
    must still carry the correct `diversity_impossible=True` signal. Without
    explicit coverage, a refactor that moved the early return *above* the
    diversity_impossible computation would silently lose the signal — and
    nothing else asserts it on this codepath.

    The pins-clash fixture is the right host: 2 pinned planes → free_planes=0,
    DiversityConfig default min_planes_moved=2 → 0<2 ∧ alternatives=3>1 →
    diversity_impossible=True. The pin self-collision then trips
    `_check_trivially_infeasible` check #3.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import DiversityConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_pins_clash.yaml")
    r = solve(
        s, budget_s=5.0, alternatives=3, seed=42, diversity=DiversityConfig(min_planes_moved=2)
    )

    assert r.status == "trivially_infeasible"
    assert r.diagnostics.diversity_impossible is True
    # rejected_count must be 0 — the early return short-circuits BEFORE
    # the search loop runs (this is also the case the new __post_init__
    # cross-field guard enforces).
    assert r.diagnostics.diversity_rejected_count == 0


def test_solve_trivially_infeasible_alternatives_1_clears_diversity_impossible():
    """Companion to the K>1 test above — pins the threading in the negative direction.

    Same fixture (pins clash → trivially_infeasible), but with alternatives=1
    the static precondition `alternatives > 1` is False so
    diversity_impossible must be False even though free_planes=0. Without
    this negative-direction assertion, a regression that hardcoded
    diversity_impossible=True on the early-return path would slip past
    the positive test above.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_pins_clash.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42)

    assert r.status == "trivially_infeasible"
    assert r.diagnostics.diversity_impossible is False
    assert r.diagnostics.diversity_rejected_count == 0


# Note: the legacy ``test_solve_trivially_infeasible_when_maintenance_pin_outside_bay``
# (and its fixture ``solve_infeasible_maint_pin_outside_bay.yaml``) was removed
# along with the ``maintenance_position`` collision rule. Pinning the
# maintenance plane is incoherent under the new "occupant is away"
# semantics — the bay-closure rule operates on the perimeter, not on the
# occupant's geometry, so there is nothing to pin against.


def test_solve_trivially_infeasible_when_pinned_plane_intrudes_into_closed_bay():
    """Pinning a non-maintenance plane such that its geometry intrudes
    into the closed bay must be caught pre-search.

    Pre-search check #3 builds a pin-only Layout from the pinned
    placements (with the maintenance occupant filtered out per the
    invariant) and runs ``collisions.check()`` on it. A pinned wingtip
    strictly inside the closed bay triggers ``bay_intrusion`` on that
    Layout — and the solver short-circuits to trivially_infeasible
    instead of burning the budget on restarts where every iteration
    hits the same conflict on a pinned plane.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_bay_closes_floor.yaml")
    r = solve(s, budget_s=5.0, seed=42)

    # Must fire trivially_infeasible (pre-search), NOT exhausted_budget.
    assert r.status == "trivially_infeasible", (
        f"expected pre-search rejection; got {r.status} after "
        f"{r.diagnostics.restarts_attempted} restarts (silent-failure regression?)"
    )
    # Wall time should be ~zero — no real search ran.
    assert r.diagnostics.wall_time_s < 0.5
    assert r.diagnostics.restarts_attempted == 0
    bp = r.diagnostics.best_partial
    assert bp is not None
    assert any(c.kind == "bay_intrusion" for c in bp.conflicts), (
        f"expected a bay_intrusion conflict on the pin-only Layout, "
        f"got {[c.kind for c in bp.conflicts]}"
    )
