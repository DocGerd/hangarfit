"""Disk-touching, torch-free bridge between a curriculum Stage and a HangarFitEnv.
Split out of ml/train.py precisely because train.py imports torch at module level —
keeping these here lets their tests run in the no-torch CI."""

from __future__ import annotations

from pathlib import Path

from hangarfit.loader import load_fleet
from ml.curriculum import Stage

_ROOT = Path(__file__).resolve().parent.parent  # repo root (ml/ sits at the root)


def effective_fleet_ids(stage: Stage) -> tuple[str, ...]:
    """The stage's sampling pool: its explicit ``fleet_ids`` if set, else the keys of
    ``load_fleet(stage.fleet_path)`` (aircraft only — a manifest's ground_objects load
    via a separate path and are never returned by load_fleet). The ONLY disk touch in
    the sampling chain; resolved once per rung by train_curriculum."""
    if stage.fleet_ids is not None:
        return stage.fleet_ids
    return tuple(load_fleet(str(_ROOT / stage.fleet_path)).keys())
