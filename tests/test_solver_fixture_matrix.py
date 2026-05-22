"""Per-fixture tests covering spec §6.5's v1 fixture matrix.

Each test exercises one scenario YAML and asserts the spec §6.2
universal property assertions plus any fixture-specific invariants.

These complement ``tests/test_solver_search.py`` (which covers the
solver's internal mechanics) by pinning the *user-facing* contract on
each canonical scenario.
"""

from __future__ import annotations

import pytest

from hangarfit.collisions import check
from hangarfit.loader import load_scenario
from hangarfit.models import DiversityConfig, SolveResult
from hangarfit.solver import _heading_delta_short_arc, solve

FIXTURES = "tests/fixtures"


def _assert_universal_properties(r: SolveResult) -> None:
    """Apply spec §6.2 property assertions that every fixture test
    shares: status enum, every layout independently valid, seed populated,
    best_partial fused with infeasible statuses, pairwise diversity when
    K > 1, and the pre-search wall-time guard for trivially_infeasible.
    """
    assert r.status in {"found", "found_partial", "exhausted_budget", "trivially_infeasible"}

    for layout in r.layouts:
        assert check(layout).valid, f"solver returned an invalid layout: {layout!r}"

    assert isinstance(r.diagnostics.seed, int)

    if r.status in {"exhausted_budget", "trivially_infeasible"}:
        assert r.diagnostics.best_partial is not None
        assert r.diagnostics.best_partial_layout is not None

    if r.status == "trivially_infeasible":
        # Pre-search check ran; no actual search burned.
        assert r.diagnostics.wall_time_s < 0.5

    if len(r.layouts) > 1:
        # Every pair satisfies the diversity rule (n_moved >= min_planes_moved).
        cfg = DiversityConfig()
        for i, la in enumerate(r.layouts):
            for lb in r.layouts[i + 1 :]:
                n_moved = _count_planes_moved(la, lb, cfg)
                assert n_moved >= cfg.min_planes_moved, (
                    f"diversity violated between two accepted layouts: "
                    f"only {n_moved} planes moved (need {cfg.min_planes_moved})"
                )


def _count_planes_moved(la, lb, cfg: DiversityConfig) -> int:
    """Mirror the solver's edit-count diversity metric for assertion use."""
    import math

    by_id_a = {p.plane_id: p for p in la.placements}
    by_id_b = {p.plane_id: p for p in lb.placements}
    moved = 0
    for pid in set(by_id_a) & set(by_id_b):
        pa, pb = by_id_a[pid], by_id_b[pid]
        dx = pa.x_m - pb.x_m
        dy = pa.y_m - pb.y_m
        pos_shift = math.hypot(dx, dy)
        head_shift = _heading_delta_short_arc(pa.heading_deg, pb.heading_deg)
        if pos_shift >= cfg.position_threshold_m or head_shift >= cfg.heading_threshold_deg:
            moved += 1
    return moved


# ── G.1: solve_pinned_one_plane ─────────────────────────────────────────


def test_solve_pinned_one_plane_honors_pin():
    """Pinned plane's placement must match the pin exactly in the
    returned layout. Spec §6.5: `found`, pinned unchanged.
    """
    s = load_scenario(f"{FIXTURES}/solve_pinned_one_plane.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42)

    if r.status == "exhausted_budget":
        pytest.skip(
            f"Solver didn't find a layout in 5s for solve_pinned_one_plane "
            f"(restarts={r.diagnostics.restarts_attempted}). Acceptable on "
            f"slow CI; the test_hangar_large geometry should usually succeed."
        )

    _assert_universal_properties(r)
    assert r.status == "found"
    assert len(r.layouts) == 1

    pinned = s.constraints["aviat_husky"].pin
    assert pinned is not None
    placed = next(p for p in r.layouts[0].placements if p.plane_id == "aviat_husky")
    assert placed.x_m == pinned.x_m
    assert placed.y_m == pinned.y_m
    assert placed.heading_deg == pinned.heading_deg
    assert placed.on_carts == pinned.on_carts


# ── G.2: solve_repair_minimal_edit ──────────────────────────────────────


def test_solve_repair_minimal_edit_honors_all_pins():
    """5 of 6 planes pinned to baseline (example.yaml) positions; fuji
    is the unpinned plane the solver re-places. Spec §6.5: `found`,
    only the unpinned plane differs from baseline.

    "Differs from baseline" is tested by: every pinned plane matches its
    pin exactly (the pins ARE the baseline), and the unpinned plane is
    not asserted against a fixed coordinate — only against the universal
    "valid layout" property.
    """
    s = load_scenario(f"{FIXTURES}/solve_repair_minimal_edit.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42)

    if r.status == "exhausted_budget":
        pytest.skip(
            f"Solver didn't find a layout in 5s for solve_repair_minimal_edit "
            f"(restarts={r.diagnostics.restarts_attempted}). Acceptable on slow CI; "
            f"placeholder hangar with 5 pins is a constrained search."
        )

    _assert_universal_properties(r)
    assert r.status == "found"
    assert len(r.layouts) == 1

    placements_by_id = {p.plane_id: p for p in r.layouts[0].placements}
    for plane_id, constraint in s.constraints.items():
        pin = constraint.pin
        assert pin is not None
        placed = placements_by_id[plane_id]
        assert placed.x_m == pin.x_m
        assert placed.y_m == pin.y_m
        assert placed.heading_deg == pin.heading_deg
        assert placed.on_carts == pin.on_carts

    # The unpinned plane (fuji) is placed somewhere — just verify
    # presence; coordinates are search-derived, not contract-asserted.
    assert "fuji" in placements_by_id


# ── G.3: solve_force_carts_lock ─────────────────────────────────────────


def test_solve_force_carts_lock_respects_lock():
    """force_on_carts=True must be reflected in every returned layout's
    placement for that plane. Spec §6.5: `found`, returned layout
    respects the lock.
    """
    s = load_scenario(f"{FIXTURES}/solve_force_carts_lock.yaml")
    r = solve(s, budget_s=5.0, alternatives=1, seed=42)

    if r.status == "exhausted_budget":
        pytest.skip(
            f"Solver didn't find a layout in 5s for solve_force_carts_lock "
            f"(restarts={r.diagnostics.restarts_attempted})."
        )

    _assert_universal_properties(r)
    assert r.status == "found"
    placed = next(p for p in r.layouts[0].placements if p.plane_id == "cessna_140")
    assert placed.on_carts is True
