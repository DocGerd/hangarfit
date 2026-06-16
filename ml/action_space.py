"""Discrete action-space contract for the cold-joint policy (sub-project #3, #607).

Pure (no torch). The factored action = a kind index over ``encoding``'s canonical
9-wide action space (8 ``(kind, gear)`` movement actions + PARK at ``PARK_INDEX``)
times a ``K``-way magnitude bin. ``decode()`` turns a sampled action into the env's
``Primitive | Park`` in the units the env expects: radians for a cart pivot
(``L``/``R`` at ``turn_radius == 0``), metres for straights/strafes/own-gear arcs."""

from __future__ import annotations

import math
from typing import Literal, cast

from hangarfit.towplanner import SegmentKind
from ml.encoding import _CANONICAL_ACTIONS, ACTION_DIM, PARK_INDEX
from ml.types import Park, Primitive

__all__ = [
    "TRANSLATION_BINS",
    "PIVOT_BINS_DEG",
    "MAGNITUDE_DIM",
    "ACTION_DIM",
    "PARK_INDEX",
    "decode",
]

TRANSLATION_BINS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0)  # metres
PIVOT_BINS_DEG: tuple[float, ...] = (5.0, 15.0, 30.0, 45.0, 90.0)  # degrees -> radians at decode
MAGNITUDE_DIM = len(TRANSLATION_BINS)
assert len(PIVOT_BINS_DEG) == MAGNITUDE_DIM, "magnitude bin tables must match in length"


def decode(kind_gear_idx: int, mag_bin_idx: int, *, turn_radius_m: float) -> Primitive | Park:
    """Resolve a sampled factored action into the env's ``Primitive | Park``.

    ``PARK_INDEX`` -> ``Park()`` (``mag_bin_idx`` ignored). A cart pivot (``kind`` in
    ``{'L', 'R'}`` and ``turn_radius_m == 0``) decodes to ``radians(PIVOT_BINS_DEG)``;
    everything else (``S``/``T`` and own-gear arcs) decodes to ``TRANSLATION_BINS`` metres.
    """
    if kind_gear_idx == PARK_INDEX:
        return Park()
    kind, gear = _CANONICAL_ACTIONS[kind_gear_idx]
    if kind in ("L", "R") and turn_radius_m == 0.0:
        magnitude = math.radians(PIVOT_BINS_DEG[mag_bin_idx])
    else:
        magnitude = TRANSLATION_BINS[mag_bin_idx]
    return Primitive(
        kind=cast(SegmentKind, kind), magnitude=magnitude, gear=cast("Literal[1, -1]", gear)
    )
