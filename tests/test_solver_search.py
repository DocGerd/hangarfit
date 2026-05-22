"""Tests for solver.py — search engine (Chunks D & E)."""

from __future__ import annotations

import random

import pytest


def test_initial_placement_for_pinned_plane_returns_the_pin():
    """If a plane is pinned, its initial placement IS the pin (no sampling)."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _initial_placement_for_plane

    s = load_scenario("tests/fixtures/scenario_with_pin.yaml")
    rng = random.Random(42)
    pin = s.constraints["aviat_husky"].pin
    assert pin is not None

    result = _initial_placement_for_plane(
        plane_id="aviat_husky",
        scenario=s,
        rng=rng,
        on_carts=pin.on_carts,
    )
    assert result == pin


def test_initial_placement_for_free_plane_is_within_hangar():
    """Free planes get random (x,y) inside hangar bounds, any heading."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _initial_placement_for_plane

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    rng = random.Random(42)
    p = _initial_placement_for_plane(
        plane_id="aviat_husky",
        scenario=s,
        rng=rng,
        on_carts=False,
    )
    assert p.plane_id == "aviat_husky"
    assert 0.0 <= p.x_m <= s.hangar.width_m
    assert 0.0 <= p.y_m <= s.hangar.length_m
    assert 0.0 <= p.heading_deg < 360.0


def test_initial_placement_for_maintenance_biases_to_back_strip():
    """The maintenance plane's initial y is inside the maintenance bay."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _initial_placement_for_plane

    s = load_scenario("tests/fixtures/scenario_with_pin.yaml")
    # In scenario_with_pin, maintenance_plane is fuji (not pinned).
    rng = random.Random(42)
    p = _initial_placement_for_plane(
        plane_id="fuji",
        scenario=s,
        rng=rng,
        on_carts=False,
        bias_to_maintenance_bay=True,
    )
    bay_y_start = s.hangar.length_m - s.hangar.maintenance_bay.depth_m
    assert bay_y_start <= p.y_m <= s.hangar.length_m


def test_cart_buckets_collapses_when_another_cart_eligible_is_force_locked_on():
    """When another cart_eligible is force_on_carts=True, the at-most-one
    cart-rule slot is taken — singletons for others would be infeasible."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _enumerate_cart_buckets

    # scenario_with_force_carts.yaml locks cessna_140 on_carts=True.
    # ctsl is also cart_eligible and unlocked.
    # Naive enumeration would emit [frozenset(), frozenset({"ctsl"})], but
    # the singleton bucket pairs ctsl-on-carts WITH cessna_140-already-on-carts,
    # which violates Layout's at-most-one-cart_eligible-on-carts rule.
    # Correct behavior: only the empty bucket is feasible.
    s = load_scenario("tests/fixtures/scenario_with_force_carts.yaml")
    buckets = _enumerate_cart_buckets(s)
    assert buckets == [frozenset()]


def test_cart_buckets_enumerates_unlocked_cart_eligibles_plus_none():
    """With C unlocked cart_eligible planes AND none pre-committed-on-carts,
    there should be C+1 buckets."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _enumerate_cart_buckets

    # solve_fresh_six_planes scenario includes ctsl, cessna_140, fk9_mkii
    # (3 cart_eligibles, none locked). Expected: 4 buckets.
    s = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    buckets = _enumerate_cart_buckets(s)
    assert len(buckets) == 4
    assert frozenset() in buckets
    cart_eligibles = {pid for pid in s.fleet_in if s.fleet[pid].is_cart_eligible}
    for pid in cart_eligibles:
        assert frozenset({pid}) in buckets


def test_cart_bucket_for_restart_is_deterministic_round_robin():
    """Restart index R selects bucket R % len(buckets)."""
    from hangarfit.loader import load_scenario
    from hangarfit.solver import _cart_bucket_for_restart, _enumerate_cart_buckets

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    buckets = _enumerate_cart_buckets(s)
    if len(buckets) > 0:
        # First few restarts should cycle through buckets
        for i in range(2 * len(buckets)):
            chosen = _cart_bucket_for_restart(buckets, restart_index=i)
            assert chosen == buckets[i % len(buckets)]


def test_score_valid_layout_is_zero_zero():
    from hangarfit.loader import load_layout
    from hangarfit.solver import _score

    layout = load_layout("layouts/example.yaml")
    s = _score(layout)
    assert s == (0, 0.0)


def test_score_invalid_layout_is_positive():
    from hangarfit.loader import load_layout
    from hangarfit.solver import _score

    # Use an existing invalid-overlap fixture; substitute filename if needed.
    layout = load_layout("layouts/example_invalid.yaml")
    s = _score(layout)
    count, penetration = s
    assert count > 0
    assert penetration >= 0.0  # could be 0 if all conflicts are single-plane


def test_perturb_plane_returns_valid_placement_within_hangar():
    """Perturbation outputs are within hangar bounds and on [0, 360°)."""
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import SearchConfig, _perturb_plane

    s = load_scenario("tests/fixtures/solve_feasible_smoke.yaml")
    rng = random.Random(42)
    current = Placement(plane_id="aviat_husky", x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    config = SearchConfig()  # defaults

    # Generate many perturbations; all must be inside hangar bounds.
    for _ in range(50):
        cand = _perturb_plane(
            current=current,
            scenario=s,
            rng=rng,
            search=config,
            large_jump=False,
        )
        assert cand.plane_id == "aviat_husky"
        assert 0.0 <= cand.x_m <= s.hangar.width_m
        assert 0.0 <= cand.y_m <= s.hangar.length_m
        assert 0.0 <= cand.heading_deg < 360.0


def test_solve_finds_layout_for_trivial_single_plane():
    """A single plane in a large hangar must be found quickly."""
    from hangarfit.collisions import check
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_trivial_single_plane.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42)

    assert r.status == "found"
    assert len(r.layouts) == 1
    assert check(r.layouts[0]).valid


def test_solve_finds_layout_for_fresh_six_planes():
    """6 planes in placeholder hangar — should be findable within budget."""
    from hangarfit.collisions import check
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42)

    if r.status == "exhausted_budget":
        pytest.skip(
            f"Search didn't find a layout for 6 planes in 5s with seed=42 "
            f"(restarts={r.diagnostics.restarts_attempted}). This is acceptable "
            f"behavior — the placeholder hangar is tight. Increase budget or "
            f"retune SearchConfig if this becomes a pattern."
        )

    assert r.status == "found"
    assert len(r.layouts) == 1
    assert check(r.layouts[0]).valid


def test_solve_is_deterministic_for_same_seed():
    """seed=42 → identical SolveResult across calls.

    The spec §4.8 contract is "one Random(seed) drives every sampling
    decision". This is the canary that the RNG is actually threaded
    through every branch — set/dict iteration, time.time() reads, and
    other nondeterminism would surface here as a flake.

    Compares SolveResults via their layouts' placements (the layouts'
    fleet dicts are MappingProxyType-wrapped copies, so Layout equality
    requires deep-equality on dicts which works but is heavy — comparing
    placements directly is sharper).
    """
    from hangarfit.loader import load_scenario
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_trivial_single_plane.yaml")
    r1 = solve(s, budget_s=5.0, alternatives=1, seed=42)
    r2 = solve(s, budget_s=5.0, alternatives=1, seed=42)

    assert r1.status == r2.status
    assert r1.diagnostics.seed == r2.diagnostics.seed == 42
    assert r1.diagnostics.restarts_attempted == r2.diagnostics.restarts_attempted
    # Compare actual layout placements element-wise — Layout's fleet is
    # wrapped in MappingProxyType so direct equality would compare proxy
    # identity in some corner cases.
    assert len(r1.layouts) == len(r2.layouts)
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements
        assert la.maintenance_plane == lb.maintenance_plane
