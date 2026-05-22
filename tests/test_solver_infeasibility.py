"""Tests for solver.solve() — pre-search infeasibility + smoke."""

from __future__ import annotations


def test_solve_feasible_smoke_returns_exhausted_budget_placeholder():
    """Until Chunk D lands, any feasible scenario returns exhausted_budget."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    r = solve(s, budget_s=0.1, seed=42)

    # Chunk C placeholder: no search yet, so feasible inputs return
    # exhausted_budget immediately.
    assert r.status == "exhausted_budget"
    assert r.layouts == ()
    assert r.diagnostics.seed == 42
    assert r.diagnostics.wall_time_s < 1.0
    # best_partial is None at this stage (no search means no partial)
    assert r.diagnostics.best_partial is None


def test_solve_resolves_none_seed_to_entropy():
    """seed=None resolves to a random int and is recorded in diagnostics."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    r = solve(s, budget_s=0.1, seed=None)
    assert isinstance(r.diagnostics.seed, int)
    assert r.diagnostics.seed != 0  # entropy is essentially always nonzero


def test_solve_trivially_infeasible_when_plane_too_big_for_hangar():
    """A plane whose bbox exceeds hangar dims → trivially_infeasible."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_plane_too_big.yaml")
    r = solve(s, budget_s=5.0, seed=42)

    assert r.status == "trivially_infeasible"
    assert r.layouts == ()
    # Pre-search check must short-circuit fast (no actual search).
    assert r.diagnostics.wall_time_s < 5.0  # well below the 30 s default budget
    assert r.diagnostics.restarts_attempted == 0


def test_solve_trivially_infeasible_when_sum_areas_exceeds_hangar():
    """All 9 planes in the placeholder hangar → sum of bbox areas > hangar floor."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_too_big.yaml")
    r = solve(s, budget_s=5.0, seed=42)

    assert r.status == "trivially_infeasible"
    assert r.diagnostics.wall_time_s < 5.0  # well below the 30 s default budget
    # The diagnostic should mention "sum" or "area" so the user can tell
    # WHICH infeasibility check fired.
    bp = r.diagnostics.best_partial
    assert bp is not None
    assert any("area" in c.detail.lower() or "footprint" in c.detail.lower() for c in bp.conflicts)


def test_solve_trivially_infeasible_when_pins_clash():
    """Two pins overlapping at the same coordinates → trivially_infeasible."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_infeasible_pins_clash.yaml")
    r = solve(s, budget_s=5.0, seed=42)

    assert r.status == "trivially_infeasible"
    assert r.diagnostics.wall_time_s < 5.0  # well below the 30 s default budget
    # The best_partial's conflicts should include the pin pair
    bp = r.diagnostics.best_partial
    assert bp is not None
    # At least one conflict must reference both pinned planes
    refs = [set(c.planes) for c in bp.conflicts if len(c.planes) == 2]
    assert any({"aviat_husky", "ctsl"} == r for r in refs), (
        f"Expected a pairwise conflict between aviat_husky and ctsl, got {refs}"
    )
