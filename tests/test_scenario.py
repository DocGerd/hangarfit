"""Tests for the Scenario / PlaneConstraint dataclasses in models.py."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from hangarfit.loader import load_fleet, load_hangar
from hangarfit.models import (
    Placement,
    PlaneConstraint,
    Scenario,
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


def test_scenario_rejects_pin_on_carts_false_for_always_cart(fleet, hangar):
    """A pin whose on_carts violates the plane's movement_mode is invalid."""
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


def test_scenario_rejects_pin_on_carts_true_for_always_own_gear(fleet, hangar):
    """The symmetric half of the always_cart case: on_carts=True on a Husky
    (always_own_gear) is illegal. Pinned as a separate test so a refactor
    consolidating the two pin checks can't silently delete one half."""
    with pytest.raises(ValueError, match="movement_mode"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("aviat_husky",),  # always_own_gear
            constraints={
                "aviat_husky": PlaneConstraint(
                    pin=Placement(
                        plane_id="aviat_husky",
                        x_m=2.0,
                        y_m=2.0,
                        heading_deg=0.0,
                        on_carts=True,  # illegal for always_own_gear
                    )
                )
            },
        )


def test_scenario_accepts_pin_and_force_on_carts_agreeing(fleet, hangar):
    """Positive counterpart to the disagreement test: pin.on_carts and
    force_on_carts both set to the same value should construct cleanly.

    Pins the agreement-is-fine contract — a refactor that made the
    disagreement check fire unconditionally (whenever both are set) would
    silently break a legitimate use case, but the disagreement-only test
    above would still pass. This test catches it."""
    s = Scenario(
        fleet=fleet,
        hangar=hangar,
        fleet_in=("cessna_140",),
        constraints={
            "cessna_140": PlaneConstraint(
                pin=Placement(
                    plane_id="cessna_140",
                    x_m=2.0,
                    y_m=2.0,
                    heading_deg=0.0,
                    on_carts=True,
                ),
                force_on_carts=True,
            )
        },
    )
    assert s.constraints["cessna_140"].force_on_carts is True


def test_scenario_fleet_is_mapping_proxy(fleet, hangar):
    """Pin the immutability wrap on Scenario.fleet — a refactor that
    drops the object.__setattr__ in __post_init__ would silently regress
    the docstring-promised immutability."""
    s = Scenario(fleet=fleet, hangar=hangar, fleet_in=("aviat_husky",))
    assert isinstance(s.fleet, MappingProxyType)
    with pytest.raises(TypeError):
        s.fleet["x"] = None  # type: ignore[index]


def test_scenario_constraints_is_mapping_proxy(fleet, hangar):
    """Pin the immutability wrap on Scenario.constraints — same risk as
    test_scenario_fleet_is_mapping_proxy."""
    s = Scenario(
        fleet=fleet,
        hangar=hangar,
        fleet_in=("aviat_husky",),
        constraints={"aviat_husky": PlaneConstraint()},
    )
    assert isinstance(s.constraints, MappingProxyType)
    with pytest.raises(TypeError):
        s.constraints["x"] = PlaneConstraint()  # type: ignore[index]


def test_scenario_rejects_duplicate_fleet_in(fleet, hangar):
    """One plane can't park in two places — fleet_in must have no duplicates."""
    with pytest.raises(ValueError, match="duplicate"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("aviat_husky", "aviat_husky"),
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


# ── Scenario: maintenance_plane + constraint guard ──────────────────────


def test_scenario_rejects_pin_on_maintenance_plane(fleet, hangar):
    """Pinning the maintenance plane is incoherent — it is treated as absent
    from placements, so a pin on it would be silently ignored by the solver.
    Scenario.__post_init__ must reject this with a clear ValueError."""
    # cessna_140 is cart_eligible so on_carts=True is valid per movement_mode.
    # The rejection must fire because it is ALSO the maintenance_plane, not
    # because of any cart-rule or movement_mode violation.
    with pytest.raises(ValueError, match="cessna_140"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("cessna_140", "aviat_husky"),
            maintenance_plane="cessna_140",
            constraints={
                "cessna_140": PlaneConstraint(
                    pin=Placement(
                        plane_id="cessna_140",
                        x_m=2.0,
                        y_m=14.0,
                        heading_deg=0.0,
                        on_carts=True,
                    )
                )
            },
        )


def test_scenario_rejects_force_on_carts_on_maintenance_plane(fleet, hangar):
    """force_on_carts on the maintenance plane is equally incoherent.

    The maintenance occupant is absent from placements so the solver cannot
    honour a force_on_carts constraint on it — reject at construction rather
    than silently ignoring it.

    Pinned as a separate test to ensure both branches of the disjunct
    (pin is not None | force_on_carts is not None) are covered independently
    — a refactor that checked only one branch would silently miss the other.
    """
    # aviat_husky is always_own_gear; force_on_carts=False would ordinarily
    # be an independent movement_mode violation — use cessna_150 (cart_eligible)
    # so force_on_carts=True is otherwise legal and the rejection is solely
    # due to the maintenance_plane conflict.
    with pytest.raises(ValueError, match="cessna_150"):
        Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=("cessna_150", "aviat_husky"),
            maintenance_plane="cessna_150",
            constraints={"cessna_150": PlaneConstraint(force_on_carts=True)},
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


def test_solver_diagnostics_rejects_partial_pairing():
    """best_partial and best_partial_layout are a fused pair — both-or-neither."""
    from hangarfit.models import CheckResult, SolverDiagnostics

    with pytest.raises(ValueError, match="best_partial"):
        SolverDiagnostics(
            restarts_attempted=0,
            wall_time_s=0.0,
            best_partial=CheckResult(),  # Some
            best_partial_layout=None,  # None — mismatched
            seed=42,
        )


def test_solver_diagnostics_rejects_negative_restarts():
    from hangarfit.models import SolverDiagnostics

    with pytest.raises(ValueError, match=">= 0"):
        SolverDiagnostics(
            restarts_attempted=-1,
            wall_time_s=0.0,
            best_partial=None,
            best_partial_layout=None,
            seed=42,
        )


def test_solver_diagnostics_rejects_negative_wall_time():
    from hangarfit.models import SolverDiagnostics

    with pytest.raises(ValueError, match="wall_time_s"):
        SolverDiagnostics(
            restarts_attempted=0,
            wall_time_s=-0.1,
            best_partial=None,
            best_partial_layout=None,
            seed=42,
        )


def test_solver_diagnostics_diversity_fields_default():
    """diversity_impossible and diversity_rejected_count default to False/0.

    See spec §4.1 of the v0.6.0 solver-polish release design — these two
    fields are observability signals so the diversity warning at
    ``solver.py`` becomes machine-readable rather than logger-only. The
    default of ``False`` / ``0`` means an unmodified solver run on a
    healthy fixture must report no diversity friction at all.
    """
    from hangarfit.models import SolverDiagnostics

    d = SolverDiagnostics(
        restarts_attempted=0,
        wall_time_s=0.0,
        best_partial=None,
        best_partial_layout=None,
        seed=42,
    )
    assert d.diversity_impossible is False
    assert d.diversity_rejected_count == 0


def test_solver_diagnostics_rejects_negative_diversity_rejected_count():
    """diversity_rejected_count is a non-negative counter (spec §4.1)."""
    from hangarfit.models import SolverDiagnostics

    with pytest.raises(ValueError, match="diversity_rejected_count"):
        SolverDiagnostics(
            restarts_attempted=0,
            wall_time_s=0.0,
            best_partial=None,
            best_partial_layout=None,
            seed=42,
            diversity_rejected_count=-1,
        )


def test_solve_result_construct_empty_for_infeasible():
    """Status=trivially_infeasible MUST have empty layouts."""
    from hangarfit.models import (
        SolverDiagnostics,
        SolveResult,
    )

    diag = SolverDiagnostics(
        restarts_attempted=0,
        wall_time_s=0.0,
        best_partial=None,
        best_partial_layout=None,
        seed=42,
    )
    r = SolveResult(status="trivially_infeasible", layouts=(), diagnostics=diag)
    assert r.status == "trivially_infeasible"
    assert r.layouts == ()


def test_solve_result_rejects_found_with_empty_layouts():
    """Status=found with empty layouts is self-inconsistent."""
    from hangarfit.models import (
        SolverDiagnostics,
        SolveResult,
    )

    diag = SolverDiagnostics(
        restarts_attempted=0,
        wall_time_s=0.0,
        best_partial=None,
        best_partial_layout=None,
        seed=42,
    )
    with pytest.raises(ValueError, match="requires at least one layout"):
        SolveResult(status="found", layouts=(), diagnostics=diag)


def test_solve_result_rejects_infeasible_with_layouts(fleet, hangar):
    """Status=trivially_infeasible with non-empty layouts is self-inconsistent."""
    from hangarfit.models import (
        Layout,
        SolverDiagnostics,
        SolveResult,
    )

    diag = SolverDiagnostics(
        restarts_attempted=0,
        wall_time_s=0.0,
        best_partial=None,
        best_partial_layout=None,
        seed=42,
    )
    empty_layout = Layout(fleet=fleet, hangar=hangar, placements=())
    with pytest.raises(ValueError, match="must have empty layouts"):
        SolveResult(status="trivially_infeasible", layouts=(empty_layout,), diagnostics=diag)


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
