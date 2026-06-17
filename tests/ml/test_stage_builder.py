"""Unit tests for ml/stage_builder.py — touches disk (loader) but NO torch, so it
runs in the no-torch CI."""

from __future__ import annotations

import pytest

from ml.curriculum import DEFAULT_LADDER, Stage
from ml.stage_builder import build_stage_env, effective_fleet_ids
from ml.types import DifficultyConfig


def test_effective_fleet_ids_returns_explicit_pool_verbatim():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji", "aviat_husky"),
    )
    assert effective_fleet_ids(s) == ("fuji", "aviat_husky")


def test_effective_fleet_ids_resolves_whole_fleet_when_none():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=None,
    )
    ids = effective_fleet_ids(s)
    assert "fuji" in ids and "aviat_husky" in ids
    assert len(ids) >= 9  # the synthetic manifest lists 9 aircraft


def test_effective_fleet_ids_herrenteich_excludes_ground_objects():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="examples/herrenteich/hangar.yaml",
        fleet_path="examples/herrenteich/fleet.yaml",
        fleet_ids=None,
    )
    ids = effective_fleet_ids(s)
    # load_fleet returns aircraft only; the 4 ground objects must NOT appear
    assert "vw_caddy" not in ids
    assert "maul_fuel_trailer" not in ids


def test_every_default_ladder_rung_pool_covers_max_objects():
    for s in DEFAULT_LADDER:
        pool = effective_fleet_ids(s)
        assert s.difficulty.max_objects <= len(pool)


def test_build_stage_env_applies_clearance_and_apron_overrides():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1, per_object_step_budget=40, total_step_budget=40),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji",),
        clearance_m=0.05,
        apron_depth_m=8.0,
    )
    env = build_stage_env(s)
    assert env.hangar.clearance_m == 0.05
    assert env.hangar.apron_depth_m == 8.0
    assert env.difficulty.max_objects == 1
    assert "fuji" in env.fleet


def test_build_stage_env_strict_rung_inherits_file_clearance():
    strict = DEFAULT_LADDER[4]  # trio-notch-strict, clearance_m=None
    env = build_stage_env(strict)
    assert env.hangar.clearance_m == 0.10  # the herrenteich file value (#664)


def test_build_stage_env_raises_when_max_objects_exceeds_pool():
    s = Stage(
        name="toobig",
        difficulty=DifficultyConfig(max_objects=2),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji",),  # pool of 1, but want 2
    )
    with pytest.raises(ValueError):
        build_stage_env(s)


def test_build_stage_env_every_default_rung_constructs():
    for s in DEFAULT_LADDER:
        env = build_stage_env(s)
        assert len(env.requested_ids) == s.difficulty.max_objects
