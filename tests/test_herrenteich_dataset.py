"""Guards the real Airfield Herrenteich dataset (`examples/herrenteich/`).

These are real (DWG-measured hangar + published-spec fleet) files kept
separate from the synthetic `data/` placeholders. The dataset's promise is
that all eight usual occupants fit at once — that lives in `layout.yaml`
(aircraft only). These tests also guard the 9-entry fleet roster (incl. the
Fuji), the four ground objects, the real 'today' `layout_today.yaml` (all nine
aircraft + ONE glider trailer + fuel + Caddy, the #664 existence proof behind
the 0.20 → 0.10 m clearance recalibration), and the alternative GO-laden
`layout_full.yaml` (seven aircraft + four GOs, fishbone, with a clear Caddy
egress). They pin the hangar dimensions and assert the bundled layouts still
pass the real part-based collision checker, so a later edit cannot silently
break them.
"""

from pathlib import Path

import pytest

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


def test_hangar_motion_clearance_calibrated() -> None:
    """The Herrenteich hangar carries the calibrated tow-MOTION clearance (#605/#643):
    tighter than the parked spacing, which stays the static-validity margin."""
    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    assert hangar.motion_clearance_m == 0.05
    assert hangar.motion_wing_layer_clearance_m == 0.05
    # parked spacing is the static-validity margin, recalibrated to 0.10 / 0.15
    # against the real 'today' layout (#664).
    assert hangar.clearance_m == 0.10
    assert hangar.wing_layer_clearance_m == 0.15
    # the tow planner sees the tighter motion margin
    assert hangar.motion_hangar().clearance_m == 0.05


def test_fleet_roster() -> None:
    fleet = load_fleet(HERRENTEICH / "fleet.yaml")
    # The roster is the eight usual occupants PLUS the Fuji FA-200, a permanent
    # ninth occupant added in the #657 realism pass (a placeholder for a future
    # C150). The hangar cannot park all nine alongside the ground objects, so no
    # single layout places the whole roster — layout.yaml parks the usual eight
    # (test_everyone_home_layout_is_valid) and layout_full.yaml the realistic
    # GO-laden subset (test_full_set_layout_is_valid).
    assert set(fleet) == USUAL_OCCUPANTS | {"fuji"}
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


def test_scheibe_wing_sits_below_the_high_wing_layer() -> None:
    """Regression for #842. The Scheibe SF-25 is a real LOW-wing glider whose 18 m wing is
    modelled as a thin RAISED keep-out band — deliberately NOT in the high-wing layer. Two
    z-layering invariants make the real all-8 layout tow-routable; a future edit must not
    silently break either (both are static-validity-neutral, so the layout tests above would
    NOT catch a regression on the ceiling one):

      * CEILING — the wing top must sit at least ``wing_layer_clearance_m`` BELOW the genuine
        high-wingers' wing layer (aviat_husky, wing bottom 2.0 m, is the representative one a
        tow must pass), so those wings overhang it and can be towed PAST the parked Scheibe.
        The prior z[1.9,2.1] put this wing INTO the high layer, manufacturing a phantom
        wing-vs-wing block during the tow that made the (real, valid) all-8 un-tow-routable.
      * FLOOR — the wing bottom must sit at least ``wing_layer_clearance_m`` ABOVE the fuselage
        tops it overhangs in the dense layouts (zlin/husky aft fuselage, top 1.5 m), or it
        clips them (statically invalid) — the constraint that sets the 1.70 m floor.
    """
    fleet = load_fleet(HERRENTEICH / "fleet.yaml")
    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    wlc = hangar.wing_layer_clearance_m

    scheibe_wings = [p for p in fleet["scheibe_falke"].parts if p.kind == "wing"]
    scheibe_bottom = min(p.z_bottom_m for p in scheibe_wings)
    scheibe_top = max(p.z_top_m for p in scheibe_wings)

    # CEILING: stay clear below the high-wing layer (aviat_husky is the representative
    # high-winger whose tow path passes over the parked Scheibe).
    husky_wing_bottom = min(p.z_bottom_m for p in fleet["aviat_husky"].parts if p.kind == "wing")
    assert scheibe_top + wlc <= husky_wing_bottom + 1e-9, (
        f"Scheibe wing top {scheibe_top} m + wing-layer clearance {wlc} m must stay below the "
        f"high-wing layer (aviat_husky wing bottom {husky_wing_bottom} m) so high-wingers can tow "
        f"past the parked Scheibe (#842); a wing in the high layer re-creates the phantom block."
    )

    # FLOOR: clear the fuselage tops the wing overhangs in the dense layout_today.yaml.
    fuselage_top = max(
        p.z_top_m
        for pid in ("zlin_savage", "aviat_husky")
        for p in fleet[pid].parts
        if p.kind.startswith("fuselage")
    )
    assert scheibe_bottom >= fuselage_top + wlc - 1e-9, (
        f"Scheibe wing floor {scheibe_bottom} m must clear the overhung fuselage tops "
        f"({fuselage_top} m) by the wing-layer clearance {wlc} m (#842)."
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
    # All parts are solid 'ground' footprints. The Caddy is multi-part (#658:
    # a van body 0->1.84 m + a small roof-gear box 1.84->2.04 m, so a high wing
    # may overhang the body and only has to clear the localized rack); the rest
    # are single boxes.
    for go in gos.values():
        assert go.parts and all(p.kind == "ground" for p in go.parts)
    assert len(gos["vw_caddy"].parts) == 2
    for sid in ("maul_fuel_trailer", "glider_trailer_1", "glider_trailer_2"):
        assert len(gos[sid].parts) == 1


def test_hangar_clearances_calibrated() -> None:
    """The Herrenteich horizontal clearance was recalibrated 0.20 -> 0.10 against
    the real 'today' layout (#664): the actual club set (9 aircraft + Duo trailer
    + fuel + Caddy) is infeasible at 0.20 m but valid at 0.10 m, and PK confirmed
    real gaps vary a lot / are very tight. Vertical (wing-layer) stays 0.15 — it
    was not the binding constraint. (History: #605 set 0.20/0.15 from the all-8 +
    4-GO frontier of <=0.22/0.15.)"""
    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    assert hangar.clearance_m == 0.10
    assert hangar.wing_layer_clearance_m == 0.15


# The realistic in-hangar set (#657/#659): SEVEN of the eight usual aircraft +
# ALL FOUR ground objects, packed FISHBONE (mixed continuous headings). With the
# rescue Caddy keeping a clear drive-out egress (the user's hard rule) plus the fuel
# trailer by the door and both glider trailers inside, the hangar is one aircraft
# over capacity — an exhaustive search (orthogonal AND fishbone) could never seat
# all eight alongside the ground objects with a clear Caddy egress. So the
# motor-glider Scheibe Falke parks OUTSIDE in this arrangement; it stays in the
# fleet manifest (test_fleet_roster), this layout just doesn't place it.
# (layout.yaml still parks all eight aircraft — with no ground clutter — so the
# "all eight fit" promise is unchanged; it lives there, not here.)
FULL_SET = (USUAL_OCCUPANTS - {"scheibe_falke"}) | {
    "vw_caddy",
    "glider_trailer_1",
    "glider_trailer_2",
    "maul_fuel_trailer",
}


def test_full_set_layout_is_valid() -> None:
    """The realistic in-hangar set (7 aircraft + 4 ground objects, fishbone) passes
    the real checker at the calibrated clearances (#605/#659). The Caddy keeps a
    clear egress (test_full_set_caddy_egress_clear) and the fuel trailer sits hard
    against the left wall by the door; the Scheibe Falke is parked outside (see
    FULL_SET)."""
    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    present = {p.plane_id for p in layout.placements} | {
        gp.plane_id for gp in layout.ground_object_placements
    }
    assert present == FULL_SET
    assert "scheibe_falke" not in present  # the one aircraft deliberately left outside (#659)
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
    """The Caddy is parked near the door — in the front third of the hangar and
    within the door's x-span — at the mouth of its rescue lane, so it can drive
    straight out (the clear egress is asserted by test_full_set_caddy_egress_clear).
    A strict min-y assertion would fight the surrounding nest geometry, so this
    pins the looser near-door envelope."""
    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    caddy = next(gp for gp in layout.ground_object_placements if gp.plane_id == "vw_caddy")
    assert caddy.y_m < layout.hangar.length_m / 3
    door = layout.hangar.door
    assert door.center_x_m - door.width_m / 2 <= caddy.x_m <= door.center_x_m + door.width_m / 2


@pytest.mark.slow
def test_full_set_caddy_egress_clear() -> None:
    """The rescue Caddy can drive out of layout_full.yaml without moving anything
    (#657/#659 — the user's hard requirement; flips the old egress-BLOCKED finding).

    Earlier the maximally-dense, ORTHOGONAL all-8 + 4-GO nest walled the Caddy in
    (the prior test recorded that packing-wall finding). The realistic arrangement
    instead parks the aircraft FISHBONE and leaves one aircraft out (the Scheibe
    Falke parks outside — all four ground objects stay in), which frees a clear
    drive-out corridor from the Caddy to the door. The egress oracle (the
    authoritative #603/#652 gate, full budget) must find that corridor.
    """
    from hangarfit.towplanner import egress_first_conflict

    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    c = egress_first_conflict(layout, "vw_caddy")
    assert c is None, f"expected the Caddy to have a clear egress; got {c}"


def test_caddy_multipart_body_clears_wing_but_rack_conflicts() -> None:
    """#658: the Caddy's two-box geometry, pinned by behavior, not just part count.

    A high wing (z 2.0-2.3) may overhang the low VAN BODY (z 0->1.84: gap 0.16 m,
    clears the 0.15 m wing-layer clearance) but conflicts with the localized ROOF
    RACK (z 1.84->2.04: gap -0.04 m). This guards the exact mechanism the multi-part
    remodel exists for — a single full-height 2.04 box would conflict everywhere, and
    a part-count check alone would not catch a regression that flattened the rack
    back to full height. (layout_full.yaml itself happens not to nest a wing over the
    Caddy, so this is where the body-clears-while-rack-conflicts contract is pinned.)
    """
    from hangarfit.loader import load_ground_objects, load_hangar
    from hangarfit.models import Aircraft, Layout, Part, Placement, Wheels

    caddy = load_ground_objects(HERRENTEICH / "fleet.yaml")["vw_caddy"]
    body = next(p for p in caddy.parts if p.z_top_m == 1.84)
    rack = next(p for p in caddy.parts if p.z_bottom_m == 1.84)
    assert (body.z_bottom_m, rack.z_top_m) == (0.0, 2.04)  # contiguous stack to 2.04 m
    assert rack.length_m < body.length_m and rack.width_m < body.width_m  # rack is localized

    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    # Caddy heading 0 => body length (4.88) runs along +y, width (1.79) along +x;
    # at (7.0, 8.0) the body spans x[6.1,7.9] y[5.6,10.4], the rack x[6.6,7.4] y[7.5,8.5].
    caddy_pl = Placement("vw_caddy", x_m=7.0, y_m=8.0, heading_deg=0.0, on_carts=False)

    def wing_overhang_conflicts(x: float, y: float) -> tuple[str, ...]:
        probe = Aircraft(
            id="probe",
            name="Probe",
            wing_position="high",
            gear="nosewheel",
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
            wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=2.0),
        )
        layout = Layout(
            fleet={"probe": probe},
            hangar=hangar,
            placements=(Placement("probe", x_m=x, y_m=y, heading_deg=0.0, on_carts=False),),
            maintenance_plane=None,
            ground_objects={"vw_caddy": caddy},
            ground_object_placements=(caddy_pl,),
        )
        return tuple(c.kind for c in collisions.check(layout).conflicts)

    # Over the body, clear of the rack -> clears (0.16 m >= 0.15 m wing-layer gap).
    assert wing_overhang_conflicts(7.0, 6.5) == ()
    # Directly over the rack -> conflicts (rack top 2.04 m vs wing 2.0 m = -0.04 m).
    over_rack = wing_overhang_conflicts(7.0, 8.0)
    assert over_rack and all("ground" in k for k in over_rack), over_rack


# The REAL 'today' layout (#664): the club's actual in-hangar set as described by
# PK — all NINE usual aircraft (incl. the Scheibe Falke) PLUS ONE glider trailer
# (the Duo Discus; the spare is stored elsewhere), the fixed fuel trailer, and the
# rescue Caddy with a clear drive-out. This is the existence proof that drove the
# clearance recalibration (0.20 -> 0.10): the set is infeasible at 0.20 m but valid
# at 0.10 m. Contrast layout_full.yaml, which keeps BOTH trailers and so must park
# one aircraft (the Scheibe) outside.
TODAY_PRESENT = USUAL_OCCUPANTS | {"fuji"} | {"vw_caddy", "maul_fuel_trailer", "glider_trailer_1"}


def test_today_layout_is_valid() -> None:
    """The real 'today' layout (9 aircraft + Duo trailer + fuel + Caddy) passes the
    real checker at the recalibrated clearances (#664). It KEEPS the Scheibe (unlike
    layout_full.yaml) and parks only ONE glider trailer."""
    layout = load_layout(HERRENTEICH / "layout_today.yaml")
    present = {p.plane_id for p in layout.placements} | {
        gp.plane_id for gp in layout.ground_object_placements
    }
    assert present == TODAY_PRESENT
    assert "scheibe_falke" in present  # the real layout keeps the Scheibe inside
    assert "glider_trailer_2" not in present  # only the Duo trailer is inside today
    assert len([p.plane_id for p in layout.placements]) == 9  # all nine aircraft
    result = collisions.check(layout)
    assert result.conflicts == (), [c.kind for c in result.conflicts]


def test_today_layout_ground_objects_in_bounds_and_clear_notch() -> None:
    """Independent, model-free vertex scan: every ground object in the real 'today'
    layout is inside the L-shaped floor and clear of the office notch."""
    layout = load_layout(HERRENTEICH / "layout_today.yaml")
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


@pytest.mark.slow
def test_today_caddy_egress_clear() -> None:
    """The rescue Caddy can drive out of the real 'today' layout without moving
    anything (#664/#603/#652 — the club's hard rule). The egress oracle (the
    authoritative gate, full budget) must find the drive-out corridor."""
    from hangarfit.towplanner import egress_first_conflict

    layout = load_layout(HERRENTEICH / "layout_today.yaml")
    c = egress_first_conflict(layout, "vw_caddy")
    assert c is None, f"expected the Caddy to have a clear egress; got {c}"
