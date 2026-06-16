"""Unit tests for ml/stage_builder.py — touches disk (loader) but NO torch, so it
runs in the no-torch CI."""

from __future__ import annotations

from ml.curriculum import DEFAULT_LADDER, Stage
from ml.stage_builder import effective_fleet_ids
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
