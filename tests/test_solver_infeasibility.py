"""Tests for solver.solve() — pre-search infeasibility + smoke."""

from __future__ import annotations


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
    """All 9 planes in the placeholder hangar → sum of bbox areas > hangar floor."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_too_big.yaml")
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


def test_solve_trivially_infeasible_when_maintenance_pin_outside_bay():
    """Maintenance plane pinned outside the back maintenance bay.

    Regression test for the silent-failure path identified in PR #89:
    pre-search check #3 now includes the maintenance_position rule when
    the maintenance plane is itself pinned. Without the fix, this case
    would slip past pre-search and burn the entire solve() budget on
    infinite restarts.
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_maint_pin_outside_bay.yaml")
    r = solve(s, budget_s=5.0, seed=42)

    # Must fire trivially_infeasible (pre-search), NOT exhausted_budget.
    assert r.status == "trivially_infeasible", (
        f"Expected pre-search rejection; got {r.status} after "
        f"{r.diagnostics.restarts_attempted} restarts (silent-failure regression?)"
    )
    # Wall time should be ~zero — no real search ran.
    assert r.diagnostics.wall_time_s < 0.5
    assert r.diagnostics.restarts_attempted == 0
    bp = r.diagnostics.best_partial
    assert bp is not None
    # The fired conflict is maintenance_position (one of collisions.py's
    # standard kinds), not one of the solver's synthetic
    # "trivially_infeasible_*" kinds. That's fine — what matters is that
    # the user gets a SHARP signal pre-search rather than a generic
    # exhausted_budget.
    assert any("maintenance" in c.kind for c in bp.conflicts), (
        f"Expected a maintenance-related conflict, got {[c.kind for c in bp.conflicts]}"
    )
