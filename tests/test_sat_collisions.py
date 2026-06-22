"""Lever B (#754): the opt-in numpy SAT box oracle (``--sat-collisions``).

These tests pin the contract the spike (#735, ``tests/spikes/test_sat_geos_equivalence.py``)
validated, now as PRODUCTION code: the numpy convex-rectangle kernels in
:mod:`hangarfit._sat` must reproduce the GEOS verdict surface of
:func:`hangarfit.geometry.polygon_overlap` / :func:`~hangarfit.geometry.polygon_overlap_area`
to float noise, with **zero verdict flips** on a clearance-weighted corpus.

CPU shapely stays the determinism + validity authority (#694); SAT is an opt-in
accelerator, NOT bit-for-bit identical — so the area/distance asserts use a tight
float tolerance, while the boolean verdict must match exactly.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

import hangarfit.collisions as collisions
from hangarfit import _sat
from hangarfit.collisions import check
from hangarfit.geometry import (
    aircraft_parts_world,
    oriented_rect,
    polygon_overlap,
    polygon_overlap_area,
)
from hangarfit.loader import load_layout, load_scenario
from hangarfit.models import (
    Aircraft,
    Door,
    Hangar,
    Layout,
    MaintenanceBay,
    Part,
    Placement,
    SearchConfig,
    Wheels,
)
from hangarfit.solver import solve

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _placement_signature(result):
    """Order-preserving, exact-float signature of every returned layout's
    placements — the byte-identity surface (mirrors test_solver_parallel)."""
    return [
        [(p.plane_id, p.x_m, p.y_m, p.heading_deg, p.on_carts) for p in layout.placements]
        for layout in result.layouts
    ]


def _probe_aircraft(part: Part) -> Aircraft:
    return Aircraft(
        id="probe",
        name="Probe",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=5.0,
        measured=False,
        parts=(part,),
        wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0),
    )


def _rect_corners(cx: float, cy: float, length: float, width: float, angle_deg: float):
    """Build a production oriented rectangle and return (shapely Polygon, (4,2) corners).

    Uses the SAME :func:`hangarfit.geometry.oriented_rect` the collision oracle
    builds from, so the corners fed to SAT are the exact production world corners
    (no phantom transform mismatch — the spike's key methodology, ADR-0002)."""
    poly = oriented_rect(cx, cy, length, width, angle_deg)
    corners = np.asarray(poly.exterior.coords[:-1], dtype=float)
    return poly, corners


def _corpus(n: int, seed: int):
    """A fixed-seed corpus of oriented-rectangle pairs, heavily weighted to the
    clearance boundary (pairs placed a small random offset apart) so the verdict
    boundary is actually exercised, not just trivially-disjoint pairs."""
    rng = np.random.default_rng(seed)
    pairs = []
    for _ in range(n):
        length_a = float(rng.uniform(0.5, 12.0))
        width_a = float(rng.uniform(0.3, 4.0))
        ang_a = float(rng.uniform(0.0, 360.0))
        pa, ca = _rect_corners(0.0, 0.0, length_a, width_a, ang_a)
        # Place B near A's boundary: offset by roughly A's half-extent + a small jitter
        # straddling zero so overlapping, touching, and just-separated cases all appear.
        reach = 0.5 * math.hypot(length_a, width_a)
        ox = float(rng.uniform(-reach, reach))
        oy = float(rng.uniform(-reach, reach))
        length_b = float(rng.uniform(0.5, 12.0))
        width_b = float(rng.uniform(0.3, 4.0))
        ang_b = float(rng.uniform(0.0, 360.0))
        pb, cb = _rect_corners(ox, oy, length_b, width_b, ang_b)
        pairs.append((pa, ca, pb, cb))
    return pairs


def test_sat_interior_overlap_matches_geos_zero_clearance():
    """At clearance 0, ``sat_polygon_overlap`` must match GEOS ``intersects and not
    touches`` on every corpus pair (0 verdict flips)."""
    flips = 0
    for pa, ca, pb, cb in _corpus(4000, seed=0):
        geos = polygon_overlap(pa, pb, clearance=0.0)
        sat = _sat.sat_polygon_overlap(ca, cb, clearance=0.0)
        if geos != sat:
            flips += 1
    assert flips == 0, f"{flips} clearance-0 verdict flips vs GEOS"


def test_sat_overlap_matches_geos_positive_clearance():
    """At clearance 0.05 (the box-rung value), ``sat_polygon_overlap`` must match
    GEOS ``distance < clearance`` on every corpus pair (0 verdict flips)."""
    flips = 0
    for pa, ca, pb, cb in _corpus(4000, seed=1):
        geos = polygon_overlap(pa, pb, clearance=0.05)
        sat = _sat.sat_polygon_overlap(ca, cb, clearance=0.05)
        if geos != sat:
            flips += 1
    assert flips == 0, f"{flips} clearance-0.05 verdict flips vs GEOS"


def test_sat_clip_area_matches_geos():
    """``sat_polygon_overlap_area`` must reproduce GEOS ``intersection().area`` to
    float noise (the area feeds total_penetration_m2; not bit-identical, but ~1e-9)."""
    max_delta = 0.0
    for pa, ca, pb, cb in _corpus(4000, seed=2):
        geos = polygon_overlap_area(pa, pb)
        sat = _sat.sat_polygon_overlap_area(ca, cb)
        max_delta = max(max_delta, abs(geos - sat))
    assert max_delta < 1e-9, f"max area delta {max_delta:g} exceeds float-noise tolerance"


def test_sat_min_separation_matches_geos_distance():
    """``convex_min_separation`` must reproduce shapely ``Polygon.distance`` to float
    noise on separated pairs (the live ``distance < clearance`` branch)."""
    max_delta = 0.0
    for pa, ca, pb, cb in _corpus(4000, seed=3):
        geos = pa.distance(pb)
        sat = _sat.convex_min_separation(ca, cb)
        max_delta = max(max_delta, abs(geos - sat))
    assert max_delta < 1e-9, f"max distance delta {max_delta:g} exceeds float-noise tolerance"


def test_scalar_part_is_oriented_rect():
    """A scalar (oriented-rectangle) part — ``local_vertices is None`` — produces a
    WorldPart flagged SAT-eligible (``is_oriented_rect``). This is the only path on
    which the SAT box oracle may run."""
    scalar = Part(
        kind="fuselage_aft",
        length_m=3.0,
        width_m=1.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.0,
    )
    pl = Placement(plane_id="probe", x_m=2.0, y_m=3.0, heading_deg=37.0, on_carts=False)
    [world] = aircraft_parts_world(_probe_aircraft(scalar), pl)
    assert world.is_oriented_rect is True
    # The SAT-eligible flag must coincide with a genuine 4-corner ring.
    assert len(world.polygon.exterior.coords) - 1 == 4


def test_polygon_part_is_not_oriented_rect():
    """A polygon (tapered/strut) part — ``local_vertices`` set — is NOT SAT-eligible,
    so the collision oracle falls back to shapely for any pair touching it."""
    tapered = Part(
        kind="wing",
        length_m=2.0,
        width_m=10.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=1.0,
        z_top_m=2.0,
        local_vertices=(
            (1.0, 0.0),
            (0.4, 5.0),
            (-0.4, 5.0),
            (-1.0, 0.0),
            (-0.4, -5.0),
            (0.4, -5.0),
        ),
    )
    pl = Placement(plane_id="probe", x_m=2.0, y_m=3.0, heading_deg=37.0, on_carts=False)
    [world] = aircraft_parts_world(_probe_aircraft(tapered), pl)
    assert world.is_oriented_rect is False


def _two_rect_overlap_layout(*, clearance: float = 0.05) -> Layout:
    """Two single-rectangle aircraft overlapping in plan AND height — so the ONLY
    pair test is rectangle×rectangle (every pair is SAT-eligible), with no ground
    objects and no closed bay. Used to prove the SAT path bypasses GEOS."""

    def _plane(pid: str) -> Aircraft:
        return Aircraft(
            id=pid,
            name=pid,
            wing_position="high",
            gear="tailwheel",
            movement_mode="always_own_gear",
            turn_radius_m=5.0,
            measured=False,
            parts=(
                Part(
                    kind="fuselage_aft",
                    length_m=3.0,
                    width_m=1.0,
                    offset_x_m=0.0,
                    offset_y_m=0.0,
                    angle_deg=0.0,
                    z_bottom_m=0.0,
                    z_top_m=1.5,
                ),
            ),
            wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-3.0),
        )

    hangar = Hangar(
        length_m=40.0,
        width_m=20.0,
        door=Door(center_x_m=10.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=8.0, depth_m=9.0),
        clearance_m=clearance,
        wing_layer_clearance_m=0.2,
    )
    return Layout(
        fleet={"a": _plane("a"), "b": _plane("b")},
        hangar=hangar,
        placements=(
            Placement(plane_id="a", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False),
            # Overlapping in plan (0.5 m apart on a 1.0 m-wide body) and same z-band.
            Placement(plane_id="b", x_m=10.5, y_m=10.0, heading_deg=0.0, on_carts=False),
        ),
        maintenance_plane=None,
    )


def _conflict_kinds(result) -> set[str]:
    return {c.kind for c in result.conflicts}


@pytest.mark.parametrize(
    "name",
    [
        "invalid_fuselage_fuselage",
        "invalid_fuselage_wing_overlap",
        "invalid_wing_wing_same_height",
        "invalid_strut_blocks_nesting",
        "valid_left_side_nesting",
    ],
)
def test_check_sat_matches_shapely_on_fixtures(name):
    """The bit-diff harness: on real layouts, ``check(sat_collisions=True)`` yields
    the IDENTICAL conflict set as the shapely path (0 verdict flips) and a
    total_penetration_m2 within float noise. Covers all-rect, mixed, and
    polygon-part (tapered-wing fallback) layouts alike."""
    layout = load_layout(FIXTURES_DIR / f"{name}.yaml")
    base = check(layout)
    sat = check(layout, sat_collisions=True)
    assert sat.conflicts == base.conflicts, name
    assert sat.total_penetration_m2 == pytest.approx(base.total_penetration_m2, abs=1e-9), name


def test_check_default_is_shapely_and_byte_identical():
    """Flag defaults OFF, and the off path is byte-identical to a check() with no
    keyword at all — the #754 'flag-OFF byte-identical' acceptance."""
    layout = load_layout(FIXTURES_DIR / "invalid_fuselage_fuselage.yaml")
    explicit_off = check(layout, sat_collisions=False)
    implicit = check(layout)
    assert explicit_off.conflicts == implicit.conflicts
    assert explicit_off.total_penetration_m2 == implicit.total_penetration_m2


class _GeosSeamCalled(RuntimeError):
    """Raised by the patched GEOS predicates to prove SAT bypassed them."""


def test_sat_bypasses_geos_seam_on_rectangles(monkeypatch):
    """Prove the lever FIRES (not a silent fallback): with the GEOS predicates
    patched to raise, ``check(sat_collisions=True)`` still decides the all-rectangle
    overlap — because SAT replaced them — while the shapely path hits the seam."""
    layout = _two_rect_overlap_layout()

    def _boom(*_a, **_k):
        raise _GeosSeamCalled("GEOS predicate called on a SAT-eligible rectangle pair")

    monkeypatch.setattr(collisions, "polygon_overlap", _boom)
    monkeypatch.setattr(collisions, "polygon_overlap_area", _boom)

    # SAT path: must succeed and find the overlap without touching the GEOS seam.
    result = check(layout, sat_collisions=True)
    assert not result.valid
    assert _conflict_kinds(result) == {"fuselage_aft_fuselage_aft_overlap"}, result.conflicts

    # Shapely path: the same pair routes through the (now-exploding) GEOS seam,
    # confirming that seam is exactly what SAT replaced.
    with pytest.raises(_GeosSeamCalled):
        check(layout)


def test_cli_solve_sat_collisions_flag():
    """``solve --sat-collisions`` parses to ``args.sat_collisions`` (default off)."""
    from hangarfit.cli import build_parser

    smoke = str(FIXTURES_DIR / "scenario_minimal.yaml")
    parser = build_parser()
    assert parser.parse_args(["solve", smoke]).sat_collisions is False
    assert parser.parse_args(["solve", smoke, "--sat-collisions"]).sat_collisions is True


def test_search_config_sat_collisions_defaults_off():
    """The solver opts into SAT via ``SearchConfig.sat_collisions``, default off so
    every existing solve is byte-identical (ADR-0003)."""
    assert SearchConfig().sat_collisions is False


def test_solve_with_sat_is_self_deterministic_and_shapely_valid():
    """The determinism contract for the opt-in accelerator: ``solve`` with SAT on,
    same scenario+seed, is byte-identical to ITSELF across two runs (SAT is
    referentially transparent). NOT claimed equal to the no-flag run — shapely
    stays the authority — so the returned layout is re-checked with the DEFAULT
    (shapely) checker and must be valid (#694)."""
    scenario = load_scenario(FIXTURES_DIR / "solve_fresh_alternatives_three.yaml")
    cfg = SearchConfig(max_restarts=3, spread=True, sat_collisions=True)

    run_a = solve(scenario, seed=42, budget_s=60.0, search=cfg, plan_paths=False)
    run_b = solve(scenario, seed=42, budget_s=60.0, search=cfg, plan_paths=False)

    assert run_a.status == "found"
    assert _placement_signature(run_a) == _placement_signature(run_b)
    # Authority gate: EVERY returned layout is valid under the default shapely
    # checker — SAT only accelerated the search, it never decides validity (#694).
    assert run_a.layouts
    assert all(check(layout).valid for layout in run_a.layouts)


def test_sat_polygon_overlap_rejects_negative_clearance():
    """Mirror :func:`polygon_overlap`'s guard: negative clearance is a programming
    error, not a layout config, so it raises rather than silently misbehaving."""
    _pa, ca = _rect_corners(0.0, 0.0, 2.0, 1.0, 0.0)
    _pb, cb = _rect_corners(0.5, 0.0, 2.0, 1.0, 0.0)
    try:
        _sat.sat_polygon_overlap(ca, cb, clearance=-0.1)
    except ValueError:
        return
    raise AssertionError("expected ValueError for negative clearance")
