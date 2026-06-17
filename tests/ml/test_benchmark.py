"""Torch-free tests for the eval benchmark machinery (#4c-i, #607)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hangarfit.loader import load_fleet, load_hangar, load_layout, load_scenario
from ml.benchmark import (
    _ROOT,
    BENCH_SET,
    BenchScenario,
    ReachVerdict,
    RrmcVerdict,
    _verdict_from,
    build_scenario_env,
    score_episode,
    witness_valid,
)
from ml.env import HangarFitEnv
from ml.types import DifficultyConfig, Park, Primitive


def test_benchscenario_anchor_requires_witness():
    with pytest.raises(ValueError, match="anchor requires a witness_path"):
        BenchScenario(
            name="x",
            scenario_path="s.yaml",
            kind="anchor",
            max_restarts=1,
            tow_max_expansions=1,
            seed=0,
            witness_path=None,
        )


def test_benchscenario_rejects_nonpositive_budgets():
    with pytest.raises(ValueError, match="max_restarts"):
        BenchScenario(
            name="x",
            scenario_path="s.yaml",
            kind="control",
            max_restarts=0,
            tow_max_expansions=1,
            seed=0,
        )


def test_verdict_reached_when_all_clauses_pass():
    v = _verdict_from(parked=3, total=3, done=True, final_valid=True, max_swept=0.0)
    assert v == ReachVerdict(
        reached=True,
        parked=3,
        total=3,
        final_valid=True,
        max_swept_intrusion=0.0,
        reason="reached",
    )


def test_verdict_blocked_by_unparked():
    v = _verdict_from(parked=2, total=3, done=True, final_valid=True, max_swept=0.0)
    assert not v.reached and "2/3" in v.reason


def test_verdict_blocked_by_invalid_final_layout():
    v = _verdict_from(parked=3, total=3, done=True, final_valid=False, max_swept=0.0)
    assert not v.reached and v.reason == "invalid final layout"


def test_verdict_blocked_by_swept_intrusion():
    v = _verdict_from(parked=3, total=3, done=True, final_valid=True, max_swept=0.5)
    assert not v.reached and "swept" in v.reason


def test_verdict_not_done_is_unreached():
    v = _verdict_from(parked=2, total=3, done=False, final_valid=True, max_swept=0.0)
    assert not v.reached


def test_witness_valid_true_for_committed_all8_layout():
    sc = BenchScenario(
        name="all8",
        scenario_path="examples/herrenteich/scenario.yaml",
        kind="anchor",
        max_restarts=1,
        tow_max_expansions=1,
        seed=0,
        witness_path="examples/herrenteich/layout.yaml",
    )
    assert witness_valid(sc) is True


def test_witness_valid_true_for_today_and_full():
    # All committed witnesses pass the PRODUCT checker (== `hangarfit check`).
    # layout_full clips the INERT placeholder maintenance bay, which collisions.check
    # correctly allows (bay is a keep-out only when maintenance_plane is set); the env
    # oracle's over-strict bay enforcement is tracked separately as issue #694.
    for wp in ("layout_today.yaml", "layout_full.yaml"):
        sc = BenchScenario(
            name=wp,
            scenario_path="examples/herrenteich/scenario.yaml",
            kind="anchor",
            max_restarts=1,
            tow_max_expansions=1,
            seed=0,
            witness_path=f"examples/herrenteich/{wp}",
        )
        assert witness_valid(sc) is True, wp


def test_rrmcverdict_is_constructible():
    v = RrmcVerdict(reached=True, n_routed=8, n_total=8, status="routed")
    assert v.reached and v.n_routed == 8


_TODAY = BenchScenario(
    name="today",
    scenario_path="examples/herrenteich/scenario_today.yaml",
    kind="anchor",
    max_restarts=1,
    tow_max_expansions=1,
    seed=0,
    witness_path="examples/herrenteich/layout_today.yaml",
)
_FULL = BenchScenario(
    name="full",
    scenario_path="examples/herrenteich/scenario_full.yaml",
    kind="anchor",
    max_restarts=1,
    tow_max_expansions=1,
    seed=0,
    witness_path="examples/herrenteich/layout_full.yaml",
)


@pytest.mark.parametrize("sc", [_TODAY, _FULL], ids=["today", "full"])
def test_scenario_input_matches_witness_movable_idset(sc):
    """The scenario input's movable id-set (fleet_in + placed-routed movers) must equal
    the witness layout's movable placements, with fixed obstacles excluded both sides."""
    scenario = load_scenario(_ROOT / sc.scenario_path)
    layout = load_layout(_ROOT / sc.witness_path)
    scenario_movable = set(scenario.placeable_ids)
    fixed_ids = {
        p.plane_id
        for p in layout.ground_object_placements
        if layout.ground_objects[p.plane_id].object_class == "fixed_obstacle"
    }
    witness_movable = {p.plane_id for p in layout.placements} | (
        {p.plane_id for p in layout.ground_object_placements} - fixed_ids
    )
    assert scenario_movable == witness_movable


def test_bench_set_wellformed():
    assert len(BENCH_SET) >= 4
    names = [s.name for s in BENCH_SET]
    assert len(names) == len(set(names)), "duplicate scenario names"
    assert any(s.kind == "control" for s in BENCH_SET), "need >=1 control"
    for s in BENCH_SET:
        assert (_ROOT / s.scenario_path).exists(), s.scenario_path
        if s.kind == "anchor":
            assert s.witness_path is not None and (_ROOT / s.witness_path).exists()


def test_bench_set_anchor_witnesses_all_valid():
    for s in BENCH_SET:
        if s.kind == "anchor":
            assert witness_valid(s), s.name


_DEMO = next(s for s in BENCH_SET if s.name == "herrenteich_demo")
_ALL8 = next(s for s in BENCH_SET if s.name == "herrenteich_all8")


def test_build_scenario_env_go_free_control():
    env = build_scenario_env(_DEMO)
    assert len(env.requested_ids) == 3
    assert env.ground_objects == {}


def test_build_scenario_env_refuses_fixed_obstacle_scenario():
    with pytest.raises(NotImplementedError, match="4c-ii"):
        build_scenario_env(_ALL8)


def _fuji_env() -> HangarFitEnv:
    fleet = load_fleet("data/fleet.yaml")
    hangar = replace(load_hangar("data/hangar.yaml"), apron_depth_m=8.0)
    return HangarFitEnv(
        hangar=hangar,
        fleet=fleet,
        requested_ids=("fuji",),
        difficulty=DifficultyConfig(per_object_step_budget=40, total_step_budget=40),
    )


def test_score_episode_reaches_when_driven_in_and_parked():
    env = _fuji_env()
    actions = [Primitive(kind="S", magnitude=2.0, gear=1)] * 6 + [Park()]
    v = score_episode(env, actions)
    assert v.reached, v.reason
    assert v.max_swept_intrusion == 0.0


def test_score_episode_apron_park_is_invalid():
    env = _fuji_env()
    v = score_episode(env, [Park()])
    assert not v.reached
    assert not v.final_valid
