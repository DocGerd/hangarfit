"""Tests for the pure scene/v1 builder (hangarfit.scene).

The core correctness claim — that the per-frame affine reproduces the
production ``aircraft_parts_world`` transform (the determinant-−1 trap,
ADR-0002 / ADR-0017) — is pinned by ``test_affine_matches_oracle_*``.
All transform math lives in Python so the viewer never re-derives it.
"""

from __future__ import annotations

import json
import math

import pytest

from hangarfit import scene
from hangarfit.geometry import aircraft_parts_world, oriented_rect
from hangarfit.loader import load_layout
from hangarfit.models import Aircraft, Part, Placement, Wheels
from hangarfit.towplanner import back_first_order, plan_fill

LAYOUT = "tests/fixtures/valid_left_side_nesting.yaml"


# ── Task 1: colour map + hangar block ───────────────────────────────────────


def test_color_map_is_sorted_id_keyed():
    assert scene._color_map(["b", "a"]) == {"a": scene.PLANES[0], "b": scene.PLANES[1]}


def test_color_map_wraps_palette():
    ids = [f"p{i:02d}" for i in range(len(scene.PLANES) + 2)]
    cm = scene._color_map(ids)
    assert cm[ids[0]] == cm[ids[len(scene.PLANES)]]  # wraps with %


def test_hangar_block_shape():
    lay = load_layout(LAYOUT)
    h = scene._hangar_block(lay)
    assert h["length_m"] == lay.hangar.length_m
    assert h["width_m"] == lay.hangar.width_m
    assert h["door"] == {
        "center_x_m": lay.hangar.door.center_x_m,
        "width_m": lay.hangar.door.width_m,
    }
    assert h["maintenance_bay"]["closed"] is (lay.maintenance_plane is not None)


# ── Task 2: plane boxes ──────────────────────────────────────────────────────


def test_plane_boxes_carry_local_geometry_and_z():
    lay = load_layout(LAYOUT)
    planes = scene._plane_blocks(lay)
    assert {p["id"] for p in planes} == {pl.plane_id for pl in lay.placements}
    pid = lay.placements[0].plane_id
    p = next(p for p in planes if p["id"] == pid)
    ac = lay.fleet[pid]
    assert len(p["boxes"]) == len(ac.parts)
    b0, part0 = p["boxes"][0], ac.parts[0]
    assert b0["length_m"] == part0.length_m
    assert b0["width_m"] == part0.width_m
    assert math.isclose(b0["height_m"], part0.z_top_m - part0.z_bottom_m)
    assert math.isclose(b0["cz"], (part0.z_top_m + part0.z_bottom_m) / 2)
    assert b0["cx"] == part0.offset_x_m and b0["cy"] == part0.offset_y_m
    assert b0["angle_deg"] == part0.angle_deg
    assert p["color"].startswith("#")


def test_plane_blocks_sorted_by_id():
    lay = load_layout(LAYOUT)
    ids = [p["id"] for p in scene._plane_blocks(lay)]
    assert ids == sorted(ids)


# ── Task 3: affine + anchors (the correctness seam) ──────────────────────────


def _apply(affine, u, v):
    a, b, tx, c, d, ty = affine
    return (a * u + b * v + tx, c * u + d * v + ty)


def test_affine_matches_oracle_with_angle_and_heading():
    # Synthetic aircraft with a nonzero-angle part to exercise angle composition
    # (no shipped fleet part uses a nonzero angle_deg today).
    ac = Aircraft(
        id="t",
        name="t",
        wing_position="high",
        gear="tailwheel",
        movement_mode="always_own_gear",
        turn_radius_m=5.0,
        measured=False,
        parts=(
            Part("fuselage_front", 3.0, 0.7, 1.0, 0.0, 0.0, 0.0, 1.5),
            Part("wing", 1.2, 10.0, 0.5, 0.0, 17.0, 1.9, 2.1),
            Part("tail", 1.0, 2.0, -2.5, 0.3, 33.0, 0.0, 1.0),  # nonzero angle + offset
        ),
        wheels=Wheels(0.0, 2.0, -3.0),
    )
    placement = Placement("t", x_m=4.0, y_m=6.0, heading_deg=45.0, on_carts=False)
    affine = scene._affine(placement)
    world_parts = aircraft_parts_world(ac, placement)
    for part, wp in zip(ac.parts, world_parts, strict=True):
        local = list(
            oriented_rect(
                part.offset_x_m, part.offset_y_m, part.length_m, part.width_m, part.angle_deg
            ).exterior.coords
        )[:-1]
        oracle = list(wp.polygon.exterior.coords)[:-1]
        for (u, v), (ox, oy) in zip(local, oracle, strict=True):
            ax, ay = _apply(affine, u, v)
            assert math.isclose(ax, ox, abs_tol=1e-9)
            assert math.isclose(ay, oy, abs_tol=1e-9)


@pytest.mark.parametrize("heading", [0.0, 30.0, 90.0, 135.0, 246.0, 359.0])
def test_affine_matches_oracle_across_headings(heading):
    lay = load_layout(LAYOUT)
    pid = lay.placements[0].plane_id
    ac = lay.fleet[pid]
    placement = Placement(pid, x_m=5.0, y_m=7.0, heading_deg=heading, on_carts=False)
    affine = scene._affine(placement)
    for part, wp in zip(ac.parts, aircraft_parts_world(ac, placement), strict=True):
        local = list(
            oriented_rect(
                part.offset_x_m, part.offset_y_m, part.length_m, part.width_m, part.angle_deg
            ).exterior.coords
        )[:-1]
        oracle = list(wp.polygon.exterior.coords)[:-1]
        for (u, v), (ox, oy) in zip(local, oracle, strict=True):
            ax, ay = _apply(affine, u, v)
            assert math.isclose(ax, ox, abs_tol=1e-9) and math.isclose(ay, oy, abs_tol=1e-9)


def test_anchors_are_world_box_corners():
    lay = load_layout(LAYOUT)
    anchors = scene._anchors(lay)
    pid = lay.placements[0].plane_id
    ac = lay.fleet[pid]
    placement = next(p for p in lay.placements if p.plane_id == pid)
    world = aircraft_parts_world(ac, placement)
    assert len(anchors[pid]) == len(world)
    got = anchors[pid][0]
    want = [list(xy) for xy in list(world[0].polygon.exterior.coords)[:-1]]
    for (gx, gy), (wx, wy) in zip(got, want, strict=True):
        assert math.isclose(gx, wx, abs_tol=1e-9) and math.isclose(gy, wy, abs_tol=1e-9)


# ── Task 4: timeline ─────────────────────────────────────────────────────────


def test_timeline_segments_in_back_first_order_and_sequential():
    lay = load_layout(LAYOUT)
    plan = plan_fill(lay)
    tl, finals = scene._timeline(lay, plan)
    order = [p.plane_id for p in back_first_order(lay.placements)]
    assert [s["plane_id"] for s in tl["segments"]] == order
    for prev, nxt in zip(tl["segments"], tl["segments"][1:], strict=False):
        assert math.isclose(nxt["start_s"], prev["end_s"])
    assert math.isclose(tl["total_s"], tl["segments"][-1]["end_s"])
    for s in tl["segments"]:
        assert len(s["samples"][0]) == 6  # affine
        assert finals[s["plane_id"]] == s["samples"][-1]


def test_timeline_static_when_no_plan():
    lay = load_layout(LAYOUT)
    tl, finals = scene._timeline(lay, None)
    assert tl["segments"] == [] and tl["total_s"] == 0.0
    assert set(finals) == {p.plane_id for p in lay.placements}


def test_timeline_sample_count_capped():
    lay = load_layout(LAYOUT)
    plan = plan_fill(lay)
    tl, _ = scene._timeline(lay, plan, max_samples_per_path=20)
    for s in tl["segments"]:
        # sample() yields up to n+1 poses for a cap of n; allow a small margin.
        assert len(s["samples"]) <= 25


# ── Task 5: build_scene ──────────────────────────────────────────────────────


def test_build_scene_shape_and_conflicts():
    lay = load_layout(LAYOUT)
    plan = plan_fill(lay)
    sc = scene.build_scene(lay, moves_plan=plan)
    assert sc["schema"] == scene.SCHEMA and sc["units"] == "m"
    assert set(sc) >= {
        "schema",
        "units",
        "coordinate_note",
        "hangar",
        "planes",
        "timeline",
        "final_poses",
        "conflicts",
        "anchors",
    }
    assert sc["conflicts"] == []
    json.dumps(sc)  # fully serializable


def test_build_scene_conflicts_flattened_from_check_result():
    from hangarfit import collisions
    from hangarfit.loader import load_layout as _ll

    lay = _ll("tests/fixtures/invalid_fuselage_wing_overlap.yaml")
    cr = collisions.check(lay)
    sc = scene.build_scene(lay, check_result=cr)
    expected = sorted({pid for c in cr.conflicts for pid in c.planes})
    assert sc["conflicts"] == expected
    assert expected  # this fixture is meant to be invalid


def test_build_scene_is_byte_deterministic():
    lay = load_layout(LAYOUT)
    plan = plan_fill(lay)
    a = json.dumps(scene.build_scene(lay, moves_plan=plan), sort_keys=True)
    b = json.dumps(scene.build_scene(lay, moves_plan=plan), sort_keys=True)
    assert a == b
