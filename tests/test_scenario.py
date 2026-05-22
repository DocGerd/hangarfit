"""Tests for the Scenario / PlaneConstraint dataclasses in models.py."""

from __future__ import annotations

import pytest

from hangarfit.loader import load_fleet, load_hangar
from hangarfit.models import (
    Placement,
    PlaneConstraint,
)

# ── PlaneConstraint ─────────────────────────────────────────────────────


def test_plane_constraint_default_is_free():
    """A constraint with no fields set means 'free'."""
    c = PlaneConstraint()
    assert c.pin is None
    assert c.force_on_carts is None


def test_plane_constraint_can_carry_pin():
    p = Placement(plane_id="aviat_husky", x_m=2.1, y_m=14.3, heading_deg=0.0, on_carts=False)
    c = PlaneConstraint(pin=p)
    assert c.pin == p


def test_plane_constraint_can_carry_force_on_carts():
    c = PlaneConstraint(force_on_carts=True)
    assert c.force_on_carts is True
    assert c.pin is None


# ── Scenario ────────────────────────────────────────────────────────────

# Helpers: build a minimal in-memory fleet + hangar for Scenario tests.
# We use the real data files rather than synthesizing fakes so that the
# tests also exercise the loader path indirectly.


@pytest.fixture
def fleet():
    return load_fleet("data/fleet.yaml")


@pytest.fixture
def hangar():
    return load_hangar("data/hangar.yaml")


def test_scenario_smoke_construct_minimal(fleet, hangar):
    """Minimal valid scenario constructs cleanly."""
    from hangarfit.models import Scenario

    s = Scenario(
        fleet=fleet,
        hangar=hangar,
        fleet_in=("aviat_husky", "ctsl"),
        maintenance_plane=None,
    )
    assert s.fleet_in == ("aviat_husky", "ctsl")
    assert s.maintenance_plane is None
    assert s.constraints == {}  # MappingProxyType, but == {} works


def test_scenario_rejects_empty_fleet_in(fleet, hangar):
    """Empty fleet_in is nonsense — nothing to solve; downstream solver
    helpers also assume at least one plane (e.g., fleet_in[0])."""
    from hangarfit.models import Scenario

    with pytest.raises(ValueError, match="non-empty"):
        Scenario(fleet=fleet, hangar=hangar, fleet_in=())


def test_scenario_rejects_unknown_plane_in_fleet_in(fleet, hangar):
    from hangarfit.models import Scenario

    with pytest.raises(ValueError, match="unknown plane"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("not_a_real_plane",),
        )


def test_scenario_rejects_maintenance_plane_not_in_fleet_in(fleet, hangar):
    from hangarfit.models import Scenario

    with pytest.raises(ValueError, match="maintenance_plane"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("aviat_husky",),
            maintenance_plane="ctsl",  # not in fleet_in
        )


def test_scenario_rejects_constraint_key_not_in_fleet_in(fleet, hangar):
    from hangarfit.models import PlaneConstraint, Scenario

    with pytest.raises(ValueError, match="constraint"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("aviat_husky",),
            constraints={"ctsl": PlaneConstraint(force_on_carts=True)},
        )


def test_scenario_rejects_pin_plane_id_mismatch(fleet, hangar):
    from hangarfit.models import Placement, PlaneConstraint, Scenario

    with pytest.raises(ValueError, match="plane_id"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("aviat_husky", "ctsl"),
            constraints={
                "aviat_husky": PlaneConstraint(
                    pin=Placement(
                        plane_id="ctsl",  # mismatch — should be "aviat_husky"
                        x_m=2.0,
                        y_m=2.0,
                        heading_deg=0.0,
                        on_carts=False,
                    )
                )
            },
        )


def test_scenario_rejects_force_on_carts_true_for_always_own_gear(fleet, hangar):
    """force_on_carts=True is illegal for an always_own_gear plane (Husky)."""
    from hangarfit.models import PlaneConstraint, Scenario

    with pytest.raises(ValueError, match="movement_mode"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("aviat_husky",),
            constraints={"aviat_husky": PlaneConstraint(force_on_carts=True)},
        )


def test_scenario_rejects_force_on_carts_false_for_always_cart(fleet, hangar):
    """force_on_carts=False is illegal for an always_cart plane (Falke)."""
    from hangarfit.models import PlaneConstraint, Scenario

    with pytest.raises(ValueError, match="movement_mode"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("scheibe_falke",),
            constraints={"scheibe_falke": PlaneConstraint(force_on_carts=False)},
        )


def test_scenario_rejects_pin_on_carts_inconsistent_with_movement_mode(fleet, hangar):
    """A pin whose on_carts violates the plane's movement_mode is invalid."""
    from hangarfit.models import Placement, PlaneConstraint, Scenario

    # Falke is always_cart — a pin with on_carts=False is illegal.
    with pytest.raises(ValueError, match="movement_mode"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("scheibe_falke",),
            constraints={
                "scheibe_falke": PlaneConstraint(
                    pin=Placement(
                        plane_id="scheibe_falke",
                        x_m=2.0,
                        y_m=2.0,
                        heading_deg=0.0,
                        on_carts=False,  # illegal for always_cart
                    )
                )
            },
        )


def test_scenario_rejects_pin_and_force_on_carts_disagreement(fleet, hangar):
    """If both pin and force_on_carts are set, their on_carts must agree."""
    from hangarfit.models import Placement, PlaneConstraint, Scenario

    with pytest.raises(ValueError, match="disagree|contradict"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("cessna_140",),  # cart_eligible — both states are valid
            constraints={
                "cessna_140": PlaneConstraint(
                    pin=Placement(
                        plane_id="cessna_140",
                        x_m=2.0,
                        y_m=2.0,
                        heading_deg=0.0,
                        on_carts=True,
                    ),
                    force_on_carts=False,  # contradicts the pin's on_carts
                )
            },
        )


# ── SolveStatus / SolverDiagnostics / SolveResult ───────────────────────


def test_solve_status_literal_values():
    """SolveStatus must be exactly these four strings."""
    import typing

    from hangarfit.models import SolveStatus

    values = set(typing.get_args(SolveStatus))
    assert values == {"found", "found_partial", "exhausted_budget", "trivially_infeasible"}


def test_solver_diagnostics_construct():
    from hangarfit.models import SolverDiagnostics

    d = SolverDiagnostics(
        restarts_attempted=47,
        wall_time_s=4.2,
        best_partial=None,
        best_partial_layout=None,
        seed=42,
    )
    assert d.seed == 42
    assert d.restarts_attempted == 47


def test_solve_result_construct():
    from hangarfit.models import (
        SolverDiagnostics,
        SolveResult,
    )

    r = SolveResult(
        status="found",
        layouts=(),
        diagnostics=SolverDiagnostics(
            restarts_attempted=0,
            wall_time_s=0.0,
            best_partial=None,
            best_partial_layout=None,
            seed=42,
        ),
    )
    assert r.status == "found"
    assert r.layouts == ()


# ── DiversityConfig / SearchConfig ──────────────────────────────────────


def test_diversity_config_defaults():
    from hangarfit.models import DiversityConfig

    d = DiversityConfig()
    assert d.min_planes_moved == 2
    assert d.position_threshold_m == 0.5
    assert d.heading_threshold_deg == 30.0


def test_search_config_defaults():
    from hangarfit.models import SearchConfig

    s = SearchConfig()
    assert s.candidates_per_iter == 8
    assert s.k_stall == 50
    assert s.pos_sigma_m == 0.5
    assert s.heading_sigma_deg == 10.0
