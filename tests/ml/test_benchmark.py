"""Torch-free tests for the eval benchmark machinery (#4c-i, #607)."""

from __future__ import annotations

import pytest

from ml.benchmark import BenchScenario, ReachVerdict, RrmcVerdict, _verdict_from, witness_valid
from ml.types import StepInfo


def _info(*, valid: bool, placed: int, total: int) -> StepInfo:
    return StepInfo(terms={"hard_swept": 0.0}, valid=valid, placed=placed, total=total)


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
    v = _verdict_from(_info(valid=True, placed=3, total=3), done=True, max_swept=0.0)
    assert v == ReachVerdict(
        reached=True,
        parked=3,
        total=3,
        final_valid=True,
        max_swept_intrusion=0.0,
        reason="reached",
    )


def test_verdict_blocked_by_unparked():
    v = _verdict_from(_info(valid=True, placed=2, total=3), done=True, max_swept=0.0)
    assert not v.reached and "2/3" in v.reason


def test_verdict_blocked_by_invalid_final_layout():
    v = _verdict_from(_info(valid=False, placed=3, total=3), done=True, max_swept=0.0)
    assert not v.reached and v.reason == "invalid final layout"


def test_verdict_blocked_by_swept_intrusion():
    v = _verdict_from(_info(valid=True, placed=3, total=3), done=True, max_swept=0.5)
    assert not v.reached and "swept" in v.reason


def test_verdict_not_done_is_unreached():
    v = _verdict_from(_info(valid=True, placed=2, total=3), done=False, max_swept=0.0)
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


def test_witness_valid_true_for_today_layout():
    sc = BenchScenario(
        name="today",
        scenario_path="examples/herrenteich/scenario.yaml",
        kind="anchor",
        max_restarts=1,
        tow_max_expansions=1,
        seed=0,
        witness_path="examples/herrenteich/layout_today.yaml",
    )
    assert witness_valid(sc) is True


@pytest.mark.xfail(
    reason=(
        "layout_full.yaml parks ctsl clipping the OPEN maintenance bay by ~0.278 m². "
        "hangarfit check (collisions.check) treats the bay as a keep-out ONLY when "
        "maintenance_plane is set (it is None here), so check passes. But "
        "go.intrusion_area_m2 — and therefore env._layout_valid, which witness_valid "
        "faithfully mirrors — counts the bay as an unconditional keep-out. This is a "
        "pre-existing oracle/checker divergence, NOT a benchmark bug; witness_valid is "
        "kept identical to env._layout_valid rather than relaxed. Fixing the oracle to "
        "gate the bay on maintenance_plane is out of scope for #4c-i (touches the shared "
        "ml.geometry_oracle the env/reward/curriculum all depend on)."
    ),
    strict=True,
)
def test_witness_valid_true_for_full_layout():
    sc = BenchScenario(
        name="full",
        scenario_path="examples/herrenteich/scenario.yaml",
        kind="anchor",
        max_restarts=1,
        tow_max_expansions=1,
        seed=0,
        witness_path="examples/herrenteich/layout_full.yaml",
    )
    assert witness_valid(sc) is True


def test_rrmcverdict_is_constructible():
    v = RrmcVerdict(reached=True, n_routed=8, n_total=8, status="routed")
    assert v.reached and v.n_routed == 8
