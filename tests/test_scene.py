"""Tests for the pure scene/v2 builder (hangarfit.scene).

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
from hangarfit.visualize import PLANES, PLANES_DARK

LAYOUT = "tests/fixtures/valid_left_side_nesting.yaml"


# ── Task 1: colour map + hangar block ───────────────────────────────────────


def test_color_map_is_sorted_id_keyed():
    assert scene._color_map(["b", "a"]) == {
        "a": PLANES_DARK[0],
        "b": PLANES_DARK[1],
    }


def test_color_map_wraps_palette():
    ids = [f"p{i:02d}" for i in range(len(PLANES_DARK) + 2)]
    cm = scene._color_map(ids)
    assert cm[ids[0]] == cm[ids[len(PLANES_DARK)]]  # wraps with %


def test_plane_colors_are_dark_palette():
    """#415: the 3D scene emits the dark-lifted fleet fills (PLANES_DARK), not
    the light 2D PLANES — brand parity on the #0D0E10 surface."""
    lay = load_layout(LAYOUT)
    emitted = {b["color"] for b in scene._plane_blocks(lay)}
    assert emitted, "expected at least one plane block"
    assert emitted <= set(PLANES_DARK)
    assert emitted.isdisjoint(set(PLANES) - set(PLANES_DARK))


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
    # structural_notches is always emitted; the default layout's hangar has none.
    assert h["structural_notches"] == []


def test_hangar_block_emits_structural_notches():
    """A notched hangar emits each notch rectangle for the viewer (ADR-0018)."""
    import dataclasses

    from hangarfit.models import StructuralNotch

    lay = load_layout(LAYOUT)  # 22 x 25 hangar
    notch = StructuralNotch(x_min_m=18.0, y_min_m=20.0, x_max_m=22.0, y_max_m=25.0)
    notched = dataclasses.replace(lay.hangar, structural_notches=(notch,))
    h = scene._hangar_block(dataclasses.replace(lay, hangar=notched))
    assert h["structural_notches"] == [
        {"x_min_m": 18.0, "y_min_m": 20.0, "x_max_m": 22.0, "y_max_m": 25.0}
    ]


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


def test_timeline_skips_placement_with_no_move():
    # Defensive path: a placement present in the layout but absent from the plan's
    # moves gets no segment, yet must still appear parked in final_poses.
    from hangarfit.towplanner import MovesPlan

    lay = load_layout(LAYOUT)
    plan = plan_fill(lay)
    dropped = plan.moves[0].plane_id
    partial = MovesPlan(target_layout=plan.target_layout, moves=plan.moves[1:])
    tl, finals = scene._timeline(lay, partial)
    assert dropped not in {s["plane_id"] for s in tl["segments"]}  # no segment (the guard)
    assert dropped in finals  # but still parked


def test_timeline_sample_count_capped():
    lay = load_layout(LAYOUT)
    plan = plan_fill(lay)
    tl, _ = scene._timeline(lay, plan, max_samples_per_path=20)
    for s in tl["segments"]:
        assert len(s["samples"]) <= 20  # hard-clamped to the exact bound


def test_sample_affines_hard_clamp_is_exact_and_keeps_endpoints():
    # Directly exercise the overshoot branch: max_samples=2 forces the densely
    # sampled path to overshoot, so the clamp must fire — bounding the count
    # EXACTLY at 2 while keeping the door (first) and parked (last) poses.
    lay = load_layout(LAYOUT)
    path = plan_fill(lay).moves[0].path
    full = scene._sample_affines(path, 10000)
    clamped = scene._sample_affines(path, 2)
    assert len(full) > 2  # the path genuinely oversamples at the small cap
    assert len(clamped) == 2
    assert clamped[0] == full[0] and clamped[-1] == full[-1]


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
    # No sort_keys: this must prove the *serialized* dict (key + value order)
    # is byte-identical, not just equal-as-sets — the scene/v2 determinism claim.
    lay = load_layout(LAYOUT)
    plan = plan_fill(lay)
    a = json.dumps(scene.build_scene(lay, moves_plan=plan))
    b = json.dumps(scene.build_scene(lay, moves_plan=plan))
    assert a == b


# ── Task 6 (#399): gear + carts — plane-local wheels + world gear anchors ─────


def test_plane_blocks_carry_wheels_and_on_carts():
    # #399: each plane block emits its canonical plane-local wheel positions
    # (ADR-0013) and the per-placement on_carts flag, so the viewer can draw gear
    # and (when carted) pallets parented to the same affine Group.
    lay = load_layout(LAYOUT)
    planes = scene._plane_blocks(lay)
    for placement in lay.placements:
        ac = lay.fleet[placement.plane_id]
        p = next(b for b in planes if b["id"] == placement.plane_id)
        assert p["wheels"] == [[u, v] for u, v in ac.wheels.positions]
        assert p["on_carts"] == placement.on_carts


def test_gear_anchors_are_world_wheel_positions():
    # The cross-language gear oracle: world wheel positions at the FINAL pose,
    # via the production local_to_world transform (the viewer recomputes them from
    # the plane-local wheels[] + the final affine and asserts agreement on load).
    from hangarfit.geometry import local_to_world

    lay = load_layout(LAYOUT)
    ga = scene._gear_anchors(lay)
    for placement in lay.placements:
        ac = lay.fleet[placement.plane_id]
        got = ga[placement.plane_id]
        want = [local_to_world(u, v, placement) for u, v in ac.wheels.positions]
        assert len(got) == len(want)
        for (gx, gy), (wx, wy) in zip(got, want, strict=True):
            assert math.isclose(gx, wx, abs_tol=1e-9) and math.isclose(gy, wy, abs_tol=1e-9)


def test_build_scene_includes_gear_anchors_and_wheels():
    lay = load_layout(LAYOUT)
    plan = plan_fill(lay)
    sc = scene.build_scene(lay, moves_plan=plan)
    assert "gear_anchors" in sc
    assert set(sc["gear_anchors"]) == {pl.plane_id for pl in lay.placements}
    for p in sc["planes"]:
        assert "wheels" in p and "on_carts" in p
    json.dumps(sc)  # the new fields stay JSON-serializable


# ── Task 7 (#401): honesty banner flag + actionable readouts ─────────────────


def test_build_scene_emits_placeholder_and_readouts_when_valid():
    lay = load_layout(LAYOUT)  # unmeasured fleet, valid (no conflicts)
    sc = scene.build_scene(lay, moves_plan=plan_fill(lay))
    assert sc["placeholder"] is True  # shipped fleet is measured: false
    assert sc["readouts"] is not None
    assert {"min_gap_m", "min_wing_over_tail_clearance_m"} <= set(sc["readouts"])
    json.dumps(sc)


def test_build_scene_readouts_none_when_conflicts():
    from hangarfit import collisions
    from hangarfit.loader import load_layout as _ll

    lay = _ll("tests/fixtures/invalid_fuselage_wing_overlap.yaml")
    sc = scene.build_scene(lay, check_result=collisions.check(lay))
    assert sc["readouts"] is None  # an invalid layout shows no quality readouts
    assert sc["placeholder"] is True


def test_build_scene_readouts_none_for_unchecked_invalid_layout():
    # #401 review (silent-failure): even with NO check_result (e.g. `view` without
    # --check), an actually-invalid layout must not get readouts — build_scene
    # verifies validity itself rather than conflating "not checked" with "valid".
    from hangarfit.loader import load_layout as _ll

    lay = _ll("tests/fixtures/invalid_fuselage_wing_overlap.yaml")
    sc = scene.build_scene(lay)  # no check_result
    assert sc["readouts"] is None


# ── #440: scene/v2 ↔ TypeScript contract key-set parity ─────────────────────
#
# The TS viewer types its scene/v2 consumption against hand-written mirrors
# (viewer/src/scene-contract.ts, brand-contract.ts). These guard against the
# documented desync risk (ADR-0020): if scene.py/brand.py grow or drop a key and
# the TS mirror is not updated in lockstep, these fail the test the maintainer
# already runs — the near-term substitute for the deferred JSON-Schema
# single-source (spike #444). They pin KEY SETS only; runtime checkAnchors() still
# guards transform *values*.
import re  # noqa: E402
from pathlib import Path  # noqa: E402

_VIEWER_SRC = Path(__file__).resolve().parent.parent / "viewer" / "src"


def _ts_interface_fields(filename: str, interface: str) -> set[str]:
    """Field names declared in `export interface <interface> { … }`.

    Interfaces in these contracts have flat bodies (no inline object-literal
    types), so the first column-0 `}` closes the block.
    """
    text = (_VIEWER_SRC / filename).read_text(encoding="utf-8")
    m = re.search(rf"export interface {interface}\s*\{{(.*?)\n\}}", text, re.S)
    assert m is not None, f"interface {interface} not found in {filename}"
    return {
        fm.group(1)
        for line in m.group(1).splitlines()
        if (fm := re.match(r"\s*(\w+)\??\s*:", line))
    }


def _animated_scene() -> dict:
    lay = load_layout(LAYOUT)
    return scene.build_scene(lay, moves_plan=plan_fill(lay))


def test_brand_contract_ts_keys_match_brand_py():
    from hangarfit import brand

    assert _ts_interface_fields("brand-contract.ts", "BrandTokens") == set(
        brand.viewer_brand_tokens()
    )


def test_scene_contract_ts_top_level_keys_match_scene_py():
    assert _ts_interface_fields("scene-contract.ts", "SceneV2") == set(_animated_scene())


def test_scene_contract_ts_nested_keys_match_scene_py():
    sc = _animated_scene()
    cases = {
        "HangarData": sc["hangar"],
        "DoorData": sc["hangar"]["door"],
        "MaintenanceBay": sc["hangar"]["maintenance_bay"],
        "PlaneData": sc["planes"][0],
        "BoxData": sc["planes"][0]["boxes"][0],
        "TimelineData": sc["timeline"],
        "SegmentData": sc["timeline"]["segments"][0],
        "Readouts": sc["readouts"],
    }
    for interface, obj in cases.items():
        assert _ts_interface_fields("scene-contract.ts", interface) == set(obj), interface

    # StructuralNotchData: the animated scene uses a rectangular hangar, so its
    # structural_notches list is empty — sample a notched hangar block to pin the
    # nested notch field names against the TS mirror too (else a rename in scene.py
    # could silently drift from scene-contract.ts).
    import dataclasses

    from hangarfit.models import StructuralNotch

    lay = load_layout(LAYOUT)  # 22 x 25 hangar
    notched = dataclasses.replace(
        lay,
        hangar=dataclasses.replace(
            lay.hangar,
            structural_notches=(
                StructuralNotch(x_min_m=18.0, y_min_m=20.0, x_max_m=22.0, y_max_m=25.0),
            ),
        ),
    )
    notch_obj = scene._hangar_block(notched)["structural_notches"][0]
    assert _ts_interface_fields("scene-contract.ts", "StructuralNotchData") == set(notch_obj)


# ── #549: scene/v2 — explicit footprint vertices + z-band (polygon viewer seam) ─
#
# v2 adds two always-present box keys: `z_band` ([z_bottom_m, z_top_m]) and
# `vertices` (the plane-local N-gon footprint, or `null` for a scalar box). A
# scalar (rectangle) box emits `vertices: null` and renders byte-identically to
# v1; a polygon part emits its plane-local ring so the viewer's single det-−1
# affine reproduces the anchor oracle vertex-for-vertex.


def _polygon_layout(verts=None):
    """Loaded layout with the first placed plane's parts replaced by a single
    polygon (N-gon) wing — a *placed* polygon aircraft so ``_plane_blocks`` and
    ``_anchors`` exercise the ``local_vertices`` path (the shipped fleet still
    ships rectangles until PR3). The hexagon (6 verts) is distinguishable from a
    4-corner box."""
    import dataclasses

    if verts is None:
        # Convex hexagon inside the ±1.0 × ±3.0 bbox (length_m=2.0, width_m=6.0).
        verts = ((1.0, 0.0), (0.4, 3.0), (-0.4, 3.0), (-1.0, 0.0), (-0.4, -3.0), (0.4, -3.0))
    lay = load_layout(LAYOUT)
    pid = sorted(pl.plane_id for pl in lay.placements)[0]
    poly = Part("wing", 2.0, 6.0, 1.2, 0.0, 0.0, 1.9, 2.1, local_vertices=verts)
    poly_ac = dataclasses.replace(lay.fleet[pid], parts=(poly,))
    poly_lay = dataclasses.replace(lay, fleet={**lay.fleet, pid: poly_ac})
    return poly_lay, pid


def test_schema_is_scene_v2():
    assert scene.SCHEMA == "hangarfit.scene/v2"


def test_scalar_box_emits_zband_and_null_vertices():
    lay = load_layout(LAYOUT)  # shipped fleet: all-rectangle (scalar) parts
    pid = lay.placements[0].plane_id
    p = next(b for b in scene._plane_blocks(lay) if b["id"] == pid)
    part0 = lay.fleet[pid].parts[0]
    b0 = p["boxes"][0]
    assert b0["vertices"] is None  # scalar part → null signals the box render path
    assert b0["z_band"] == [part0.z_bottom_m, part0.z_top_m]


def test_polygon_box_emits_plane_local_vertices():
    from hangarfit.geometry import part_local_ring

    poly_lay, pid = _polygon_layout()
    p = next(b for b in scene._plane_blocks(poly_lay) if b["id"] == pid)
    part = poly_lay.fleet[pid].parts[0]
    b0 = p["boxes"][0]
    # N-gon footprint in plane-local (u,v) — the exact ring the oracle folds
    # before the affine, emitted verbatim so the viewer does no transform math.
    assert b0["vertices"] == [[u, v] for u, v in part_local_ring(part)]
    assert len(b0["vertices"]) == 6  # a hexagon, not a 4-corner box
    assert b0["z_band"] == [part.z_bottom_m, part.z_top_m]


def test_polygon_vertices_through_affine_reproduce_anchors():
    # Python-side mirror of the JS checkAnchors parity: the emitted plane-local
    # vertices, pushed through the plane's final affine, must equal the world
    # anchor oracle vertex-for-vertex (the det-−1 surface, ADR-0002/ADR-0017).
    poly_lay, pid = _polygon_layout()
    p = next(b for b in scene._plane_blocks(poly_lay) if b["id"] == pid)
    placement = next(pl for pl in poly_lay.placements if pl.plane_id == pid)
    affine = scene._affine(placement)
    anchors = scene._anchors(poly_lay)[pid][0]
    verts = p["boxes"][0]["vertices"]
    assert len(verts) == len(anchors)
    for (u, v), (ax, ay) in zip(verts, anchors, strict=True):
        wx, wy = _apply(affine, u, v)
        assert math.isclose(wx, ax, abs_tol=1e-9) and math.isclose(wy, ay, abs_tol=1e-9)


def test_build_scene_v2_byte_deterministic_with_polygon():
    poly_lay, _ = _polygon_layout()
    a = json.dumps(scene.build_scene(poly_lay))
    b = json.dumps(scene.build_scene(poly_lay))
    assert a == b
