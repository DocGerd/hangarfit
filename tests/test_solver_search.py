"""Tests for solver.py — search engine (Chunks D & E)."""

from __future__ import annotations

import random


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
