"""Observation tensorizer (sub-project #2, epic #607). Pure, deterministic,
numpy-only: turn the cold-joint env's semantic Observation into fixed-shape
tensors a policy can consume. No torch — numpy + shapely only. The contract is
versioned by SCHEMA_VERSION; see docs/architecture/ml-observation-schema.md."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import shapely

from hangarfit.geometry import aircraft_parts_world
from hangarfit.models import Aircraft, GroundObject, Hangar, Placement
from ml.types import Observation

SCHEMA_VERSION = 1

# Canonical discrete action order for the legal-action mask. Magnitude is a
# sub-project #3 concern; this covers only (kind, gear) legality + PARK.
_CANONICAL_ACTIONS: tuple[tuple[str, int], ...] = (
    ("L", 1),
    ("S", 1),
    ("R", 1),
    ("L", -1),
    ("S", -1),
    ("R", -1),
    ("T", 1),
    ("T", -1),
)
ACTION_DIM = len(_CANONICAL_ACTIONS) + 1  # + PARK
PARK_INDEX = ACTION_DIM - 1
TOKEN_DIM = 24
RASTER_CHANNELS = 7


@dataclass(frozen=True, slots=True)
class EncoderConfig:
    cell_m: float = 0.25  # metres per raster cell (scale-preserving)
    grid_w: int = 96  # 96 * 0.25 = 24 m window width
    grid_h: int = 192  # 192 * 0.25 = 48 m window height (apron + hangar + margin)
    apron_band_m: float = 10.0  # world-y offset of row 0 below the hangar front (y=0)
    max_objects: int = 16  # token padding cap (Herrenteich set is 12)
    z_split_m: float = 1.6  # low-band / wing-band boundary (ADR-0023)
    pos_ref_m: float = 20.0  # normalization reference for dims / radii


@dataclass(frozen=True, slots=True)
class ObservationTensors:
    raster: np.ndarray  # (RASTER_CHANNELS, grid_h, grid_w) float32
    tokens: np.ndarray  # (max_objects, TOKEN_DIM) float32
    token_mask: np.ndarray  # (max_objects,) bool
    active_index: int  # row of the active object, or -1 at a terminal state
    legal_action_mask: np.ndarray  # (ACTION_DIM,) bool
    meta: dict[str, float]
    schema_version: int = SCHEMA_VERSION


def _cell_centers(config: EncoderConfig) -> tuple[float, float, np.ndarray, np.ndarray]:
    """World (origin_x, origin_y, xs, ys) of cell centres. Hangar front-left = world
    origin; row 0 sits ``apron_band_m`` below the front so the apron (y<0) is visible."""
    origin_x = 0.0
    origin_y = -config.apron_band_m
    xs = origin_x + (np.arange(config.grid_w, dtype=np.float64) + 0.5) * config.cell_m
    ys = origin_y + (np.arange(config.grid_h, dtype=np.float64) + 0.5) * config.cell_m
    return origin_x, origin_y, xs, ys


def _rasterize(geom: shapely.Geometry | None, config: EncoderConfig) -> np.ndarray:
    """Binary occupancy (grid_h, grid_w) float32: 1.0 where a cell centre is inside
    ``geom``. Deterministic point-in-polygon via ``shapely.contains_xy`` (shapely
    >=2.0; NOT the deprecated ``shapely.vectorized.contains``)."""
    empty = np.zeros((config.grid_h, config.grid_w), dtype=np.float32)
    if geom is None or shapely.is_empty(geom):
        return empty
    _, _, xs, ys = _cell_centers(config)
    xx, yy = np.meshgrid(xs, ys)  # (grid_h, grid_w)
    inside = np.asarray(shapely.contains_xy(geom, xx, yy))
    return inside.astype(np.float32)


def _floor_polygon(hangar: Hangar) -> shapely.Geometry:
    fp = hangar.floor_polygon
    if fp is None:
        return shapely.box(0.0, 0.0, hangar.width_m, hangar.length_m)
    return fp


def _static_channels(hangar: Hangar, config: EncoderConfig) -> np.ndarray:
    """(4, grid_h, grid_w): [oob, bay, apron, door]."""
    origin_x, origin_y, _, ys = _cell_centers(config)
    window = shapely.box(
        origin_x,
        origin_y,
        origin_x + config.grid_w * config.cell_m,
        origin_y + config.grid_h * config.cell_m,
    )
    # oob: window minus the hangar floor (includes the L-notch and the apron band)
    oob = _rasterize(window.difference(_floor_polygon(hangar)), config)

    bay = hangar.maintenance_bay
    bay_poly = shapely.box(
        bay.center_x_m - bay.width_m / 2.0,
        hangar.length_m - bay.depth_m,
        bay.center_x_m + bay.width_m / 2.0,
        hangar.length_m,
    )
    bay_ch = _rasterize(bay_poly, config)

    # apron: full-width band y in [-apron_depth, 0)
    apron_depth = hangar.apron_depth_m
    apron_ch = np.zeros((config.grid_h, config.grid_w), dtype=np.float32)
    apron_rows = (ys >= -apron_depth) & (ys < 0.0)
    apron_ch[apron_rows, :] = 1.0

    # door: a one-cell-thick band across the door opening on the front wall (y≈0)
    door = hangar.door
    door_poly = shapely.box(
        door.center_x_m - door.width_m / 2.0,
        -config.cell_m,
        door.center_x_m + door.width_m / 2.0,
        config.cell_m,
    )
    door_ch = _rasterize(door_poly, config)

    return np.stack([oob, bay_ch, apron_ch, door_ch]).astype(np.float32)


def _parked_occupancy(
    obs: Observation, bodies: Mapping[str, Aircraft | GroundObject], config: EncoderConfig
) -> tuple[np.ndarray, np.ndarray]:
    """(low, wing): parked part polygons split by z-band at ``z_split_m``. A part
    paints LOW when it has mass below the split (z_bottom < z_split) and WING when it
    has mass above it (z_top > z_split); a part spanning the split paints both."""
    low_polys: list[shapely.Geometry] = []
    wing_polys: list[shapely.Geometry] = []
    for po in obs.parked:
        body = bodies[po.object_id]
        for wp in aircraft_parts_world(body, po.placement):
            if wp.z_bottom_m < config.z_split_m:
                low_polys.append(wp.polygon)
            if wp.z_top_m > config.z_split_m:
                wing_polys.append(wp.polygon)
    low = _rasterize(shapely.union_all(low_polys) if low_polys else None, config)
    wing = _rasterize(shapely.union_all(wing_polys) if wing_polys else None, config)
    return low, wing


def _active_occupancy(obs: Observation, config: EncoderConfig) -> np.ndarray:
    """Active object's footprint at its current pose (single band), or zeros if none."""
    if obs.active is None:
        return np.zeros((config.grid_h, config.grid_w), dtype=np.float32)
    a = obs.active
    placement = Placement(
        plane_id=a.object_id,
        x_m=a.pose.x_m,
        y_m=a.pose.y_m,
        heading_deg=a.pose.heading_deg,
        on_carts=a.on_carts,
    )
    polys = [wp.polygon for wp in aircraft_parts_world(a.body, placement)]
    return _rasterize(shapely.union_all(polys) if polys else None, config)


# ---------------------------------------------------------------------------
# Task 5: Set-token table + mask + active index
# ---------------------------------------------------------------------------


def _body_dims(body: Aircraft | GroundObject, config: EncoderConfig) -> tuple[float, float]:
    """(length, width) of the body footprint in its own frame, normalized by pos_ref_m.
    At heading 0 the determinant-−1 transform maps fore-aft to world-y and lateral to
    world-x, so y-extent is length and x-extent is width."""
    parts = aircraft_parts_world(
        body,
        Placement(plane_id=body.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
    )
    geom = shapely.union_all([wp.polygon for wp in parts])
    minx, miny, maxx, maxy = geom.bounds
    return (maxy - miny) / config.pos_ref_m, (maxx - minx) / config.pos_ref_m


def _norm_pos(x_m: float, y_m: float, config: EncoderConfig) -> tuple[float, float]:
    """World (x, y) → [-1, 1] over the raster window."""
    nx = 2.0 * x_m / (config.grid_w * config.cell_m) - 1.0
    origin_y = -config.apron_band_m
    ny = 2.0 * (y_m - origin_y) / (config.grid_h * config.cell_m) - 1.0
    return nx, ny


_STATUS_COL = {"parked": 0, "active": 1, "unplaced": 2}
_WING_COL = {"high": 0, "mid": 1, "low": 2}
_MOVE_COL = {"always_cart": 0, "cart_eligible": 1, "always_own_gear": 2}


def _token_row(
    body: Aircraft | GroundObject,
    *,
    status: str,
    on_carts: bool,
    pose: tuple[float, float, float] | None,
    config: EncoderConfig,
) -> np.ndarray:
    row = np.zeros(TOKEN_DIM, dtype=np.float32)
    row[_STATUS_COL[status]] = 1.0  # status one-hot 0..2
    if isinstance(body, Aircraft):
        row[3] = 1.0  # type: aircraft
        row[8 + _WING_COL[body.wing_position]] = 1.0  # wing one-hot 8..10
        row[11 + _MOVE_COL[body.movement_mode]] = 1.0  # movement one-hot 11..13
        row[15] = 1.0 if body.tow_pivotable else 0.0
    else:
        row[4 if body.object_class == "fixed_obstacle" else 5] = 1.0  # type 4..5
        row[17] = 1.0 if body.hard_door_mover else 0.0  # door flag
    length, width = _body_dims(body, config)
    row[6], row[7] = length, width  # dims 6..7 [length, width]
    row[14] = 1.0 if on_carts else 0.0  # cart flag (from placement/active)
    row[16] = body.effective_turn_radius_m() / config.pos_ref_m  # turn radius
    if pose is not None:
        nx, ny = _norm_pos(pose[0], pose[1], config)
        th = np.radians(pose[2])
        row[18], row[19], row[20], row[21] = nx, ny, float(np.sin(th)), float(np.cos(th))
    # reserved 22..23 stay 0 (region_side, seq_order)
    return row


def _tokens(
    obs: Observation,
    bodies: Mapping[str, Aircraft | GroundObject],
    config: EncoderConfig,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Build the (max_objects, TOKEN_DIM) token table, bool mask, and active_index.

    Row order: parked (placement order) → active → unplaced (queue order), padded.
    ``active_index`` is the row of the active object, or -1 at a terminal state."""
    tokens = np.zeros((config.max_objects, TOKEN_DIM), dtype=np.float32)
    mask = np.zeros(config.max_objects, dtype=bool)
    entries: list[tuple[Aircraft | GroundObject, str, bool, tuple[float, float, float] | None]] = []
    for po in obs.parked:
        pl = po.placement
        entries.append(
            (bodies[po.object_id], "parked", pl.on_carts, (pl.x_m, pl.y_m, pl.heading_deg))
        )
    active_index = -1
    if obs.active is not None:
        active_index = len(entries)
        a = obs.active
        entries.append((a.body, "active", a.on_carts, (a.pose.x_m, a.pose.y_m, a.pose.heading_deg)))
    for oid in obs.unplaced_ids:
        entries.append((bodies[oid], "unplaced", False, None))
    # Invariant: requested set <= max_objects (curriculum config). Defensive truncation
    # of the unplaced tail keeps the array fixed-shape; the active row is never dropped
    # because parked + active <= requested set <= max_objects.
    if active_index >= config.max_objects:
        active_index = -1
    for i, (body, status, on_carts, pose) in enumerate(entries[: config.max_objects]):
        tokens[i] = _token_row(body, status=status, on_carts=on_carts, pose=pose, config=config)
        mask[i] = True
    return tokens, mask, active_index
