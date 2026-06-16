"""Observation tensorizer (sub-project #2, epic #607). Pure, deterministic,
numpy-only: turn the cold-joint env's semantic Observation into fixed-shape
tensors a policy can consume. No torch — numpy + shapely only. The contract is
versioned by SCHEMA_VERSION; see docs/architecture/ml-observation-schema.md."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

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
