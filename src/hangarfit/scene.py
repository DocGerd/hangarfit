"""Pure builder for the ``hangarfit.scene/v2`` JSON contract.

Turns a :class:`~hangarfit.models.Layout` (+ optional ``MovesPlan`` and
``CheckResult``) into a JSON-serializable dict consumed by the offline
Three.js viewer (:mod:`hangarfit.viewer`). No I/O, no rendering: every
geometry/transform value is computed here so the viewer never re-derives the
determinant-−1 plane-local→world transform (ADR-0002, ADR-0017). The viewer
only applies the per-frame 2×3 affine matrices this module emits.

The schema is documented in ``docs/architecture/scene-v2-schema.md``. This
module is a leaf consumer of the core types — the same role
:mod:`hangarfit.visualize` plays for the 2D PNG.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from hangarfit import metrics
from hangarfit.brand import GROUND_OBSTACLE_3D, MOVER_3D, PLANES_DARK
from hangarfit.geometry import aircraft_parts_world, local_to_world, part_local_ring
from hangarfit.models import CheckResult, Layout, Part, Placement
from hangarfit.towplanner import back_first_order

if TYPE_CHECKING:
    from hangarfit.towplanner import DubinsArc, MovesPlan

SCHEMA = "hangarfit.scene/v2"

# A plane-local→world 2D affine, serialized as a flat list (JSON has no tuples):
# ``[a, b, tx, c, d, ty]`` maps local (u=forward, v=right) to world via
# ``world_x = a·u + b·v + tx`` / ``world_y = c·u + d·v + ty``.
Affine = list[float]

_COORD_NOTE = (
    "world: x right along door wall, y deeper into hangar, z up; "
    "heading_deg compass-style (from +y, CW+). See ADR-0002 / ADR-0017."
)


def _color_map(plane_ids: list[str]) -> dict[str, str]:
    """Stable per-plane fill by sorted id, dark-lifted for the ``#0D0E10`` 3D
    surface (``PLANES_DARK``). Same sorted-id index as
    :func:`hangarfit.visualize._plane_colour_map`, so 2D and 3D keep the same
    plane->colour identity (ADR-0017 brand parity, #415)."""
    ordered = sorted(set(plane_ids))
    return {pid: PLANES_DARK[i % len(PLANES_DARK)] for i, pid in enumerate(ordered)}


def _hangar_block(layout: Layout) -> dict:
    h = layout.hangar
    bay = h.maintenance_bay
    return {
        "length_m": h.length_m,
        "width_m": h.width_m,
        "door": {"center_x_m": h.door.center_x_m, "width_m": h.door.width_m},
        "maintenance_bay": {
            "center_x_m": bay.center_x_m,
            "width_m": bay.width_m,
            "depth_m": bay.depth_m,
            "closed": layout.maintenance_plane is not None,
            "plane_id": layout.maintenance_plane,
        },
        # Always-on floor keep-outs (ADR-0018). Always emitted (empty list for the
        # common rectangular hangar); the viewer cuts each from the floor and
        # raises interior walls, rendering the L-shaped footprint.
        "structural_notches": [
            {
                "x_min_m": n.x_min_m,
                "y_min_m": n.y_min_m,
                "x_max_m": n.x_max_m,
                "y_max_m": n.y_max_m,
            }
            for n in h.structural_notches
        ],
    }


def _box(part: Part) -> dict:
    """One scene/v2 box for a :class:`~hangarfit.models.Part`: plane-local centre
    (cx=forward offset, cy=right offset, cz=mid-height), extents (length along
    +x/forward, width along +y/right, height along z), and ``angle_deg`` (CCW
    within plane-local, as :func:`hangarfit.geometry.oriented_rect` uses).

    scene/v2 (#549) adds two always-present keys: ``z_band`` (``[z_bottom_m,
    z_top_m]``, the explicit height band) and ``vertices`` — the plane-local
    ``(u, v)`` polygon footprint for a polygon part, or ``None`` for a scalar
    rectangle (which the viewer renders byte-identically to v1). The polygon ring
    comes from :func:`hangarfit.geometry.part_local_ring`, the same helper the
    anchor oracle consumes, so the viewer's single det-−1 affine reproduces
    :func:`_anchors` vertex-for-vertex (ADR-0002 / ADR-0017). Shared by planes and
    ground objects (#606) — a ``kind="ground"`` part is just a scalar box."""
    ring = part_local_ring(part)
    return {
        "kind": part.kind,
        "cx": part.offset_x_m,
        "cy": part.offset_y_m,
        "cz": (part.z_top_m + part.z_bottom_m) / 2.0,
        "length_m": part.length_m,
        "width_m": part.width_m,
        "height_m": part.z_top_m - part.z_bottom_m,
        "angle_deg": part.angle_deg,
        "z_band": [part.z_bottom_m, part.z_top_m],
        "vertices": None if ring is None else [[u, v] for u, v in ring],
    }


def _plane_blocks(layout: Layout) -> list[dict]:
    """One block per placed plane (sorted by id): id, colour, and plane-local
    box list (each via :func:`_box`)."""
    colour = _color_map([p.plane_id for p in layout.placements])
    blocks: list[dict] = []
    for placement in sorted(layout.placements, key=lambda p: p.plane_id):
        ac = layout.fleet[placement.plane_id]
        blocks.append(
            {
                "id": placement.plane_id,
                "color": colour[placement.plane_id],
                "boxes": [_box(part) for part in ac.parts],
                # Canonical plane-local wheel points (ADR-0013) + the per-placement
                # cart flag, so the viewer draws gear (and, when carted, pallets)
                # parented to the same affine Group as the boxes (#399). Render
                # sizes (wheel radius, pallet extent) stay viewer-layer constants.
                "wheels": [[u, v] for u, v in ac.wheels.positions],
                "on_carts": placement.on_carts,
            }
        )
    return blocks


def _pose_affine(x_m: float, y_m: float, heading_deg: float) -> Affine:
    """The plane-local→world affine for a pose, matching
    :func:`hangarfit.geometry.local_to_world`. The linear part has determinant
    −1 (a rotation composed with a reflection, ADR-0002) — emitted as data so
    the viewer applies it as a matrix and does no transform math of its own."""
    h = math.radians(heading_deg)
    s, c = math.sin(h), math.cos(h)
    return [s, c, x_m, c, -s, y_m]


def _affine(placement: Placement) -> Affine:
    return _pose_affine(placement.x_m, placement.y_m, placement.heading_deg)


def _anchors(layout: Layout) -> dict[str, list[list[list[float]]]]:
    """Per-plane, per-box world corner points at the FINAL placement, from the
    production :func:`hangarfit.geometry.aircraft_parts_world` oracle. The viewer
    recomputes these from its box geometry + the final affine and asserts
    agreement on load — a fail-loud check that the JS matrix path matches the
    Python oracle (ADR-0017)."""
    out: dict[str, list[list[list[float]]]] = {}
    for placement in sorted(layout.placements, key=lambda p: p.plane_id):
        ac = layout.fleet[placement.plane_id]
        out[placement.plane_id] = [
            [[x, y] for x, y in list(wp.polygon.exterior.coords)[:-1]]
            for wp in aircraft_parts_world(ac, placement)
        ]
    return out


def _gear_anchors(layout: Layout) -> dict[str, list[list[float]]]:
    """Per-plane world wheel positions at the FINAL placement, via the production
    :func:`hangarfit.geometry.local_to_world` transform. Sibling to
    :func:`_anchors` (which oracles box corners): the viewer recomputes each wheel
    from the plane-local ``wheels[]`` + the final affine and asserts agreement on
    load. ``viewer.js`` is not pytest-covered, so this is the only cross-language
    backstop for the gear render (#399), and it shares the same det-−1 transform
    as the box anchors — so a sign-flip regression fails both at once."""
    out: dict[str, list[list[float]]] = {}
    for placement in sorted(layout.placements, key=lambda p: p.plane_id):
        ac = layout.fleet[placement.plane_id]
        out[placement.plane_id] = [
            list(local_to_world(u, v, placement)) for u, v in ac.wheels.positions
        ]
    return out


def _ground_object_blocks(layout: Layout) -> list[dict]:
    """One block per placed ground object (sorted by id): a fixed obstacle
    (keep-out) or a placed/routed mover, each a static placed body (#606) — the
    3D analogue of the 2D :mod:`hangarfit.visualize` ground-object render (#649).

    Mirrors :func:`_plane_blocks` (same ``boxes`` shape via :func:`_box`), but a
    ground object has no wheels/carts and no per-id palette: ``color`` is
    brand-resolved per *class* (a warm-graphite ``fixed_obstacle`` keep-out vs the
    slate ``placed_routed_mover``), so the viewer reads ``color`` and hard-codes
    nothing (#419, the plane colour-map idiom). ``final_pose`` is the placement
    affine — a ground object is static (no timeline) until mover animation lands
    (a deferred follow-up; the Caddy egress lane is too, the egress oracle exports
    no corridor geometry). Always-emitted, inert-when-empty (the
    ``structural_notches`` discipline)."""
    blocks: list[dict] = []
    for gp in sorted(layout.ground_object_placements, key=lambda p: p.plane_id):
        go = layout.ground_objects[gp.plane_id]
        fill = GROUND_OBSTACLE_3D if go.object_class == "fixed_obstacle" else MOVER_3D
        blocks.append(
            {
                "id": gp.plane_id,
                "object_class": go.object_class,
                "color": fill,
                "hard_door_mover": go.hard_door_mover,
                "boxes": [_box(part) for part in go.parts],
                "final_pose": _affine(gp),
            }
        )
    return blocks


def _go_anchors(layout: Layout) -> dict[str, list[list[list[float]]]]:
    """Per-ground-object world box corners at the placed pose, from the production
    :func:`hangarfit.geometry.aircraft_parts_world` oracle (it accepts a
    ``GroundObject``). The viewer recomputes these from the block geometry +
    ``final_pose`` and asserts agreement on load — the ground-object sibling of
    :func:`_anchors`, the det-−1 backstop for the mover/obstacle render
    (``viewer.js`` is not pytest-covered). Always-emitted (empty when none)."""
    out: dict[str, list[list[list[float]]]] = {}
    for gp in sorted(layout.ground_object_placements, key=lambda p: p.plane_id):
        go = layout.ground_objects[gp.plane_id]
        out[gp.plane_id] = [
            [[x, y] for x, y in list(wp.polygon.exterior.coords)[:-1]]
            for wp in aircraft_parts_world(go, gp)
        ]
    return out


def _sample_affines(path: DubinsArc, max_samples: int) -> list[Affine]:
    """Affines along a tow path, door→slot. The sample step is coarsened so a
    long path never blows past ``max_samples`` (keeps the HTML small)."""
    length = path.length_m
    step_m = max(0.05, length / max_samples) if length > 0 else 0.05
    step_deg = max(1.0, 360.0 / max_samples)
    affines = [
        _pose_affine(p.x_m, p.y_m, p.heading_deg)
        for p in path.sample(step_m=step_m, step_deg=step_deg)
    ]
    # The step sizes bound translation/angular density but not the total count
    # (a multi-loop path could exceed max_samples on sweep alone). Hard-clamp to
    # an exact bound, evenly strided, always keeping the first (door) and last
    # (parked) pose so the segment still starts and ends correctly.
    if len(affines) > max_samples >= 2:
        last = len(affines) - 1
        keep = sorted({round(i * last / (max_samples - 1)) for i in range(max_samples)})
        affines = [affines[i] for i in keep]
    return affines


def _timeline(
    layout: Layout,
    moves_plan: MovesPlan | None,
    *,
    tow_speed_mps: float = 1.0,
    min_seg_s: float = 1.5,
    max_seg_s: float = 6.0,
    max_samples_per_path: int = 240,
) -> tuple[dict, dict[str, Affine]]:
    """Build the sequential whole-fill timeline + per-plane final affines.

    Planes enter in ``back_first_order`` (deepest first); segments are laid
    end-to-end (``segment[k].start_s == segment[k-1].end_s``). Per-plane
    duration ∝ path length via ``tow_speed_mps``, clamped to ``[min_seg_s,
    max_seg_s]``. Static (no plan): ``segments == []``, ``total_s == 0`` and
    ``finals`` carries every plane at its slot.
    """
    # Sorted by id so final_poses key order matches the planes/anchors blocks
    # (all three id-ordered) — keeps the whole scene consistently ordered.
    finals: dict[str, Affine] = {
        p.plane_id: _affine(p) for p in sorted(layout.placements, key=lambda p: p.plane_id)
    }
    if moves_plan is None:
        return {"total_s": 0.0, "segments": []}, finals

    move_by_id = {m.plane_id: m for m in moves_plan.moves}
    segments: list[dict] = []
    t = 0.0
    for placement in back_first_order(layout.placements):
        move = move_by_id.get(placement.plane_id)
        if move is None or move.path is None:
            # No move, or a deferred (path=None) move — the body stays at its final
            # pose. A deferred path is a #601 ground-object mover (route → #602);
            # it never keys an aircraft placement here, so this is defensive.
            continue
        samples = _sample_affines(move.path, max_samples_per_path)
        dur = min(max(move.path.length_m / tow_speed_mps, min_seg_s), max_seg_s)
        segments.append(
            {
                "plane_id": placement.plane_id,
                "start_s": t,
                "end_s": t + dur,
                "samples": samples,
            }
        )
        finals[placement.plane_id] = samples[-1]
        t += dur
    return {"total_s": t, "segments": segments}, finals


def _conflict_ids(check_result: CheckResult | None) -> list[str]:
    if check_result is None:
        return []
    return sorted({pid for c in check_result.conflicts for pid in c.planes})


def _readouts(layout: Layout) -> dict:
    """Actionable quality numbers (#401): the tightest plan-view inter-plane gap
    and the smallest wing-over-tail vertical clearance (either may be ``null`` —
    single plane / no overhang). Only meaningful for a *valid* layout; the caller
    (:func:`build_scene`) decides validity via :func:`metrics.layout_is_valid` and
    emits ``null`` instead for an invalid one."""
    return {
        "min_gap_m": metrics.min_pairwise_gap_m(layout),
        "min_wing_over_tail_clearance_m": metrics.min_wing_over_tail_clearance_m(layout),
    }


def build_scene(
    layout: Layout,
    *,
    moves_plan: MovesPlan | None = None,
    check_result: CheckResult | None = None,
    tow_speed_mps: float = 1.0,
    min_seg_s: float = 1.5,
    max_seg_s: float = 6.0,
    max_samples_per_path: int = 240,
) -> dict:
    """Assemble the full ``hangarfit.scene/v2`` dict (pure, deterministic).

    Same ``(layout, moves_plan, check_result)`` ⇒ byte-identical dict (the
    closed-form paths are RNG-free; the spirit of ADR-0003). See the schema
    reference in ``docs/architecture/scene-v2-schema.md``.
    """
    timeline, finals = _timeline(
        layout,
        moves_plan,
        tow_speed_mps=tow_speed_mps,
        min_seg_s=min_seg_s,
        max_seg_s=max_seg_s,
        max_samples_per_path=max_samples_per_path,
    )
    conflicts = _conflict_ids(check_result)
    # Readouts imply a *verified-valid* layout. Determine validity ourselves when
    # the caller passed no CheckResult (e.g. `view` without --check) so we never
    # present a misleading "gap 0.00 m" on an unchecked, actually-invalid layout
    # (#401 review). collisions.check is pure/deterministic — determinism intact.
    valid = metrics.layout_is_valid(layout, check_result)
    return {
        "schema": SCHEMA,
        "units": "m",
        "coordinate_note": _COORD_NOTE,
        "hangar": _hangar_block(layout),
        "planes": _plane_blocks(layout),
        "ground_objects": _ground_object_blocks(layout),
        "timeline": timeline,
        "final_poses": dict(finals),
        "conflicts": conflicts,
        "anchors": _anchors(layout),
        "gear_anchors": _gear_anchors(layout),
        "go_anchors": _go_anchors(layout),
        "placeholder": metrics.has_placeholder_data(layout),
        "readouts": _readouts(layout) if valid else None,
    }
