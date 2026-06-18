"""Disk-touching, torch-free bridge between a curriculum Stage and a HangarFitEnv.
Split out of ml/train.py precisely because train.py imports torch at module level —
keeping these here lets their tests run in the no-torch CI."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hangarfit.loader import load_fleet, load_hangar, load_layout
from hangarfit.models import Placement
from ml.curriculum import Stage
from ml.env import HangarFitEnv
from ml.types import RewardWeights

_ROOT = Path(__file__).resolve().parent.parent  # repo root (ml/ sits at the root)


def witness_placements(stage: Stage) -> tuple[Placement, ...]:
    """Load an anchored rung's committed witness layout and return its placements (aircraft
    + any ground objects). The poses are hangar-independent, so this loads against the rung's
    own hangar/fleet purely to PARSE the YAML (load_layout validates id references but not
    geometry — the witness's validity is pinned by tests/ml/test_stage_builder.py). Raises if
    ``stage.anchor_layout_path`` is None (call only for an anchored rung)."""
    if stage.anchor_layout_path is None:
        raise ValueError(
            f"stage {stage.name!r}: witness_placements called but anchor_layout_path is None"
        )
    fleet = load_fleet(str(_ROOT / stage.fleet_path))
    hangar = load_hangar(str(_ROOT / stage.hangar_path), fleet=fleet)
    layout = load_layout(str(_ROOT / stage.anchor_layout_path), fleet=fleet, hangar=hangar)
    return layout.placements + (layout.ground_object_placements or ())


def effective_fleet_ids(stage: Stage) -> tuple[str, ...]:
    """The stage's sampling pool. For a #712 anchored rung it is EXACTLY the witness's objects
    (Q2 — requested_ids pinned to the witness set, so every episode's seeded permutation draws
    the same N ids and the env anchors a k-prefix of them). Otherwise: the explicit
    ``fleet_ids`` if set, else the keys of ``load_fleet(stage.fleet_path)`` (aircraft only — a
    manifest's ground_objects load via a separate path and are never returned by load_fleet).
    The ONLY disk touch in the sampling chain; resolved once per rung by train_curriculum."""
    if stage.anchor_layout_path is not None:
        return tuple(p.plane_id for p in witness_placements(stage))
    if stage.fleet_ids is not None:
        return stage.fleet_ids
    return tuple(load_fleet(str(_ROOT / stage.fleet_path)).keys())


def build_stage_env(stage: Stage, *, weights: RewardWeights | None = None) -> HangarFitEnv:
    """Load the rung's hangar + fleet, apply the clearance/apron overrides, and build
    a HangarFitEnv whose difficulty is the stage's. The initial requested_ids is just
    the first ``max_objects`` of the pool — every episode resamples via
    env.reset(requested_ids=...). Raises if the pool can't supply max_objects.

    ``weights``: optional reward weights to forward to the env (defaults to
    ``RewardWeights()`` inside the env when None)."""
    hangar = load_hangar(str(_ROOT / stage.hangar_path))
    overrides: dict[str, float] = {"apron_depth_m": stage.apron_depth_m}
    if stage.clearance_m is not None:
        overrides["clearance_m"] = stage.clearance_m
    if stage.wing_layer_clearance_m is not None:
        overrides["wing_layer_clearance_m"] = stage.wing_layer_clearance_m
    hangar = replace(hangar, **overrides)

    fleet = load_fleet(str(_ROOT / stage.fleet_path))
    # #712 anchored rung: load the witness once and derive BOTH the pool (its object ids) and
    # the env's anchor poses from it (single source of truth). Else: no anchoring.
    if stage.anchor_layout_path is not None:
        anchors = witness_placements(stage)
        pool: tuple[str, ...] = tuple(p.plane_id for p in anchors)
    else:
        anchors = ()
        pool = effective_fleet_ids(stage)
    n = stage.difficulty.max_objects if stage.difficulty.max_objects is not None else len(pool)
    if n > len(pool):
        raise ValueError(
            f"stage {stage.name!r}: max_objects {n} exceeds fleet pool size {len(pool)}"
        )
    return HangarFitEnv(
        hangar=hangar,
        fleet=fleet,
        requested_ids=tuple(pool[:n]),
        anchor_placements=anchors,
        difficulty=stage.difficulty,
        weights=weights,
    )
