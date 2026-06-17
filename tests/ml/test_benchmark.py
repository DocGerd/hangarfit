"""Torch-free tests for the eval benchmark machinery (#4c-i, #607)."""

from __future__ import annotations

import pytest

from ml.benchmark import BenchScenario, ReachVerdict, RrmcVerdict, _verdict_from, witness_valid


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
