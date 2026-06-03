"""Pure builder for the ``hangarfit.scene/v1`` JSON contract.

Turns a :class:`~hangarfit.models.Layout` (+ optional ``MovesPlan`` and
``CheckResult``) into a JSON-serializable dict consumed by the offline
Three.js viewer (:mod:`hangarfit.viewer`). No I/O, no rendering: every
geometry/transform value is computed here so the viewer never re-derives the
determinant-−1 plane-local→world transform (ADR-0002, ADR-0017). The viewer
only applies the per-frame 2×3 affine matrices this module emits.

The schema is documented in ``docs/architecture/scene-v1-schema.md``. This
module is a leaf consumer of the core types — the same role
:mod:`hangarfit.visualize` plays for the 2D PNG.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from hangarfit.geometry import aircraft_parts_world
from hangarfit.models import CheckResult, Layout, Placement
from hangarfit.towplanner import back_first_order
from hangarfit.visualize import PLANES

if TYPE_CHECKING:
    from hangarfit.towplanner import DubinsArc, MovesPlan

SCHEMA = "hangarfit.scene/v1"

# A plane-local→world 2D affine, serialized as a flat list (JSON has no tuples):
# ``[a, b, tx, c, d, ty]`` maps local (u=forward, v=right) to world via
# ``world_x = a·u + b·v + tx`` / ``world_y = c·u + d·v + ty``.
Affine = list[float]

_COORD_NOTE = (
    "world: x right along door wall, y deeper into hangar, z up; "
    "heading_deg compass-style (from +y, CW+). See ADR-0002 / ADR-0017."
)


def _color_map(plane_ids: list[str]) -> dict[str, str]:
    """Stable per-plane colour by sorted id — parity with
    :func:`hangarfit.visualize._plane_colour_map` so 2D and 3D agree."""
    ordered = sorted(set(plane_ids))
    return {pid: PLANES[i % len(PLANES)] for i, pid in enumerate(ordered)}


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
    }


def _plane_blocks(layout: Layout) -> list[dict]:
    """One block per placed plane (sorted by id): id, colour, and plane-local
    box list. A box mirrors a :class:`~hangarfit.models.Part`: plane-local
    centre (cx=forward offset, cy=right offset, cz=mid-height), extents (length
    along +x/forward, width along +y/right, height along z), and ``angle_deg``
    (CCW within plane-local, as :func:`hangarfit.geometry.oriented_rect` uses)."""
    colour = _color_map([p.plane_id for p in layout.placements])
    blocks: list[dict] = []
    for placement in sorted(layout.placements, key=lambda p: p.plane_id):
        ac = layout.fleet[placement.plane_id]
        boxes = [
            {
                "kind": part.kind,
                "cx": part.offset_x_m,
                "cy": part.offset_y_m,
                "cz": (part.z_top_m + part.z_bottom_m) / 2.0,
                "length_m": part.length_m,
                "width_m": part.width_m,
                "height_m": part.z_top_m - part.z_bottom_m,
                "angle_deg": part.angle_deg,
            }
            for part in ac.parts
        ]
        blocks.append(
            {"id": placement.plane_id, "color": colour[placement.plane_id], "boxes": boxes}
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


def _sample_affines(path: DubinsArc, max_samples: int) -> list[Affine]:
    """Affines along a tow path, door→slot. The sample step is coarsened so a
    long path never blows past ``max_samples`` (keeps the HTML small)."""
    length = path.length_m
    step_m = max(0.05, length / max_samples) if length > 0 else 0.05
    step_deg = max(1.0, 360.0 / max_samples)
    return [
        _pose_affine(p.x_m, p.y_m, p.heading_deg)
        for p in path.sample(step_m=step_m, step_deg=step_deg)
    ]


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
    finals: dict[str, Affine] = {
        p.plane_id: _pose_affine(p.x_m, p.y_m, p.heading_deg) for p in layout.placements
    }
    if moves_plan is None:
        return {"total_s": 0.0, "segments": []}, finals

    move_by_id = {m.plane_id: m for m in moves_plan.moves}
    segments: list[dict] = []
    t = 0.0
    for placement in back_first_order(layout.placements):
        move = move_by_id.get(placement.plane_id)
        if move is None:
            continue  # defensive: a placement with no move stays at its final pose
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
    """Assemble the full ``hangarfit.scene/v1`` dict (pure, deterministic).

    Same ``(layout, moves_plan, check_result)`` ⇒ byte-identical dict (the
    closed-form paths are RNG-free; the spirit of ADR-0003). See the schema
    reference in ``docs/architecture/scene-v1-schema.md``.
    """
    timeline, finals = _timeline(
        layout,
        moves_plan,
        tow_speed_mps=tow_speed_mps,
        min_seg_s=min_seg_s,
        max_seg_s=max_seg_s,
        max_samples_per_path=max_samples_per_path,
    )
    return {
        "schema": SCHEMA,
        "units": "m",
        "coordinate_note": _COORD_NOTE,
        "hangar": _hangar_block(layout),
        "planes": _plane_blocks(layout),
        "timeline": timeline,
        "final_poses": dict(finals),
        "conflicts": _conflict_ids(check_result),
        "anchors": _anchors(layout),
    }
