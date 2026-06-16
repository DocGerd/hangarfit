"""Observation tensorizer (sub-project #2, epic #607). Pure, deterministic,
numpy-only: turn the cold-joint env's semantic Observation into fixed-shape
tensors a policy can consume. No torch — numpy + shapely only. The contract is
versioned by SCHEMA_VERSION; see docs/architecture/ml-observation-schema.md."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely

from hangarfit.models import Hangar

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
    if geom is None or geom.is_empty:
        return empty
    _, _, xs, ys = _cell_centers(config)
    xx, yy = np.meshgrid(xs, ys)  # (grid_h, grid_w)
    inside = shapely.contains_xy(geom, xx, yy)
    return inside.astype(np.float32)


def _floor_polygon(hangar: Hangar) -> shapely.Geometry:
    fp = hangar.floor_polygon
    if fp is None:
        return shapely.box(0.0, 0.0, hangar.width_m, hangar.length_m)
    return fp


def _static_channels(hangar: Hangar, config: EncoderConfig) -> np.ndarray:
    """(4, grid_h, grid_w): [oob, bay, apron, door]."""
    origin_x, origin_y, xs, ys = _cell_centers(config)
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
    apron_depth = hangar.apron_depth_m or 0.0
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
