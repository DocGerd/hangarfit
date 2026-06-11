"""Guards the real Airfield Herrenteich dataset (`examples/herrenteich/`).

These are real (DWG-measured hangar + published-spec fleet) files kept
separate from the synthetic `data/` placeholders. The dataset's promise is
that all eight usual occupants fit at once; this regression test pins the
hangar dimensions and asserts the bundled layout still passes the real
part-based collision checker, so a later edit to any of the three files
cannot silently break it.
"""

from pathlib import Path

from hangarfit import collisions
from hangarfit.geometry import aircraft_parts_world
from hangarfit.loader import load_fleet, load_hangar, load_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
HERRENTEICH = REPO_ROOT / "examples" / "herrenteich"

# Real office NOTCH (back-right corner): non-floor space. Since ADR-0018 (#527)
# it is MODELLED in hangar.yaml (`structural_notches`) and enforced by
# collisions.check. Derived from the rectangle dims:
# [width-2.36, width] x [length-9.10, length].
NOTCH = (12.72, 22.66, 15.08, 31.76)  # x_min, y_min, x_max, y_max

USUAL_OCCUPANTS = {
    "cessna_140",
    "ctsl",
    "wild_thing",
    "aviat_husky",
    "scheibe_falke",
    "fk9_mkii",
    "stemme_s10",
    "zlin_savage",
}


def test_hangar_dimensions() -> None:
    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    # Real DWG figures (rounded to cm). Door fits inside the width with the
    # asymmetric jambs (0.55 m + 13.46 m + 1.07 m = 15.08 m).
    assert hangar.width_m == 15.08
    assert hangar.length_m == 31.76
    assert hangar.door.width_m == 13.46
    assert hangar.door.center_x_m == 7.28


def test_fleet_roster() -> None:
    fleet = load_fleet(HERRENTEICH / "fleet.yaml")
    assert set(fleet) == USUAL_OCCUPANTS
    # Stemme is hangared wings-folded: the wing part carries the folded span,
    # which is what lets a 23 m glider fit a 15 m hangar.
    stemme_span = next(p.width_m for p in fleet["stemme_s10"].parts if p.kind == "wing")
    assert stemme_span == 11.4


def test_everyone_home_layout_is_valid() -> None:
    """All eight occupants parked at once must pass the real checker."""
    layout = load_layout(HERRENTEICH / "layout.yaml")
    assert {p.plane_id for p in layout.placements} == USUAL_OCCUPANTS
    result = collisions.check(layout)
    assert result.conflicts == (), (
        f"examples/herrenteich/layout.yaml is no longer valid: {[c.kind for c in result.conflicts]}"
    )


def test_layout_clears_office_notch() -> None:
    """No plane sits in the back-right office notch.

    The notch is now modelled and ``collisions.check`` enforces it (ADR-0018),
    but this independent, model-free vertex scan double-checks the *shipped*
    layout — so a future layout edit that drifts a plane into the (real,
    non-floor) office corner trips here too, with a geometry-level message.
    """
    layout = load_layout(HERRENTEICH / "layout.yaml")
    x0, y0, x1, y1 = NOTCH
    for placement in layout.placements:
        parts = aircraft_parts_world(layout.fleet[placement.plane_id], placement)
        for part in parts:
            for x, y in part.polygon.exterior.coords:
                assert not (x0 <= x <= x1 and y0 <= y <= y1), (
                    f"{placement.plane_id} {part.kind} vertex ({x:.2f}, {y:.2f}) "
                    f"is inside the office notch — non-floor space"
                )


def test_real_hangar_notch_is_enforced() -> None:
    """The real hangar.yaml carries a modelled notch (ADR-0018), and
    ``collisions.check`` rejects a part parked in the office corner — a layout
    the old rectangular model accepted. Exercises the loader → model → checker
    path on the actual data file.
    """
    from hangarfit.models import Aircraft, Layout, Part, Placement, Wheels

    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    assert hangar.floor_polygon is not None, "notch should be modelled"
    # 1x1 m wing centered at (13.9, 27.0) — wholly inside the office corner.
    probe = Aircraft(
        id="probe",
        name="Probe",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=5.0,
        measured=False,
        parts=(
            Part(
                kind="wing",
                length_m=1.0,
                width_m=1.0,
                offset_x_m=0.0,
                offset_y_m=0.0,
                angle_deg=0.0,
                z_bottom_m=2.0,
                z_top_m=2.3,
            ),
        ),
        wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0),
    )
    layout = Layout(
        fleet={"probe": probe},
        hangar=hangar,
        placements=(
            Placement(plane_id="probe", x_m=13.9, y_m=27.0, heading_deg=0.0, on_carts=False),
        ),
        maintenance_plane=None,
    )
    kinds = {c.kind for c in collisions.check(layout).conflicts}
    assert "structural_notch" in kinds, kinds


def test_demo_scenario_is_a_solvable_subset() -> None:
    """`scenario_demo.yaml` is a 3-aircraft subset of the fleet that the solver
    places into a valid layout — the working end-to-end demo. (Full tow-routing
    is exercised by the README's ``solve --render-paths`` command — it routes
    under the default spread-on path, with the ADR-0016 spread-off re-solve as a
    backstop; here we pin the load-independent claim: a valid 3-plane solve.)
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    scn = load_scenario(HERRENTEICH / "scenario_demo.yaml")
    assert set(scn.fleet_in) <= USUAL_OCCUPANTS
    assert len(scn.fleet_in) == 3
    # Pin max_restarts (not budget_s) for load-independence; budget_s high so the
    # restart count is the sole gate even on a slow runner (cf. #531).
    result = solve(
        scn, seed=3, budget_s=120.0, search=SearchConfig(max_restarts=6), plan_paths=False
    )
    assert result.layouts and len(result.layouts[0].placements) == 3
    assert collisions.check(result.layouts[0]).valid


def test_ground_objects_load_from_manifest() -> None:
    """The four real ground objects load from the herrenteich fleet manifest
    with the right object_class + motion (#605)."""
    from hangarfit.loader import load_ground_objects

    gos = load_ground_objects(HERRENTEICH / "fleet.yaml")
    assert set(gos) == {
        "maul_fuel_trailer",
        "vw_caddy",
        "glider_trailer_1",
        "glider_trailer_2",
    }
    assert gos["maul_fuel_trailer"].object_class == "fixed_obstacle"
    assert gos["vw_caddy"].object_class == "placed_routed_mover"
    assert gos["vw_caddy"].motion_mode == "steerable"
    assert gos["glider_trailer_1"].motion_mode == "towed"
    assert gos["glider_trailer_2"].motion_mode == "towed"
    # each is a single solid ground footprint
    for go in gos.values():
        assert len(go.parts) == 1 and go.parts[0].kind == "ground"


def test_hangar_clearances_calibrated() -> None:
    """The Herrenteich clearances were calibrated to fit the full real set
    (#605): horizontal 0.20, vertical 0.15 (placeholders were 0.30/0.20)."""
    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    assert hangar.clearance_m == 0.20
    assert hangar.wing_layer_clearance_m == 0.15


FULL_SET = USUAL_OCCUPANTS | {
    "vw_caddy",
    "glider_trailer_1",
    "glider_trailer_2",
    "maul_fuel_trailer",
}


def test_full_set_layout_is_valid() -> None:
    """The full real set (8 aircraft + 4 ground objects) passes the real
    checker at the calibrated clearances (#605 primary acceptance)."""
    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    present = {p.plane_id for p in layout.placements} | {
        gp.plane_id for gp in layout.ground_object_placements
    }
    assert present == FULL_SET
    result = collisions.check(layout)
    assert result.conflicts == (), [c.kind for c in result.conflicts]


def test_full_set_ground_objects_in_bounds_and_clear_notch() -> None:
    """Independent, model-free vertex scan: every ground object is inside the
    L-shaped floor and clear of the office notch (belt-and-suspenders over the
    checker's bounds/notch extension)."""
    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    floor = layout.hangar.floor_polygon
    assert floor is not None
    x0, y0, x1, y1 = NOTCH
    for gp in layout.ground_object_placements:
        obj = layout.ground_objects[gp.plane_id]
        for part in aircraft_parts_world(obj, gp):
            assert floor.covers(part.polygon), f"{gp.plane_id} {part.kind} outside floor"
            for x, y in part.polygon.exterior.coords:
                assert not (x0 <= x <= x1 and y0 <= y <= y1), (
                    f"{gp.plane_id} vertex ({x:.2f},{y:.2f}) in office notch"
                )


def test_full_set_caddy_near_door() -> None:
    """SOFT intent (pre-#603): the Caddy is parked near the door — in the front
    third of the hangar and within the door's x-span — the precursor to the #603
    hard nearest-door egress gate. (Exact 'nearest' is #603's job; the 9 m glider
    trailers run along the walls toward the door, so a strict min-y assertion
    would fight that geometry.)"""
    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    caddy = next(gp for gp in layout.ground_object_placements if gp.plane_id == "vw_caddy")
    assert caddy.y_m < layout.hangar.length_m / 3
    door = layout.hangar.door
    assert door.center_x_m - door.width_m / 2 <= caddy.x_m <= door.center_x_m + door.width_m / 2
