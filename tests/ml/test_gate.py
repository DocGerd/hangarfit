"""Tests for ml/gate.py — the torch-free `--metrics-out` JSONL gate reader (#730).

PURE: no torch import/skip — the gate harness reads the per-iteration metric records
that `history_metric_records()` emits, so it is exercised under the [dev]-only CI.
"""

from __future__ import annotations

import json

from ml.gate import gate_verdict, main, read_metric_records, render_verdict


def _rec(stage, it, *, vp, fp, vr=None, reward=0.0, n_eps=8):
    """A `history_metric_records()`-shaped per-iteration record."""
    return {
        "stage": stage,
        "iter": it,
        "n_eps": n_eps,
        "mean_ep_reward": reward,
        "fraction_placed": fp,
        "valid_rate": vr if vr is not None else (vp / fp if fp else 0.0),
        "valid_placed": vp,
    }


def test_verdict_is_mastered_when_valid_placed_crosses_threshold():
    records = [
        _rec("trio-box", 0, vp=0.0, fp=0.1),
        _rec("trio-box", 1, vp=0.45, fp=0.6),
        _rec("trio-box", 2, vp=0.92, fp=0.95),
    ]
    v = gate_verdict(records, "trio-box", threshold=0.9)
    assert v.outcome == "mastered"
    assert v.competency_iter == 2
    assert v.peak_valid_placed == 0.92


def test_verdict_is_no_data_when_rung_absent_or_all_empty_iters():
    records = [
        _rec("pair-box", 0, vp=0.9, fp=0.95),  # different rung
        {
            "stage": "trio-box",
            "iter": 0,
            "n_eps": 0,
            "valid_placed": None,
            "fraction_placed": None,
            "valid_rate": None,
            "mean_ep_reward": None,
        },
    ]
    v = gate_verdict(records, "trio-box", threshold=0.9)
    assert v.outcome == "no-data"
    assert v.n_iters == 0
    assert v.peak_valid_placed is None
    assert v.competency_iter is None


def test_verdict_is_piling_when_fraction_high_but_valid_placed_low():
    # Commits objects invalidly: fraction_placed >= 0.5 while valid_placed <= 0.2.
    records = [
        _rec("trio-box", 0, vp=0.05, fp=0.70),
        _rec("trio-box", 1, vp=0.10, fp=0.85),
        _rec("trio-box", 2, vp=0.08, fp=0.66),
    ]
    v = gate_verdict(records, "trio-box", threshold=0.9)
    assert v.outcome == "piling"
    assert v.piling_iters == 3
    assert v.competency_iter is None


def test_verdict_is_place_nothing_when_fraction_stays_low():
    # Flees to do-nothing: never places much, so never piles either.
    records = [
        _rec("trio-box", 0, vp=0.0, fp=0.10),
        _rec("trio-box", 1, vp=0.0, fp=0.05),
        _rec("trio-box", 2, vp=0.0, fp=0.02),
    ]
    v = gate_verdict(records, "trio-box", threshold=0.9)
    assert v.outcome == "place-nothing"
    assert v.piling_iters == 0


def test_verdict_is_in_progress_when_placing_validly_but_below_threshold():
    # Genuine partial progress: places most of the set validly, climbing, not yet mastered.
    records = [
        _rec("trio-box", 0, vp=0.30, fp=0.55),
        _rec("trio-box", 1, vp=0.55, fp=0.70),
        _rec("trio-box", 2, vp=0.72, fp=0.80),
    ]
    v = gate_verdict(records, "trio-box", threshold=0.9)
    assert v.outcome == "in-progress"
    assert v.piling_iters == 0
    assert v.peak_valid_placed == 0.72


def test_read_metric_records_parses_jsonl_round_trip(tmp_path):
    records = [_rec("trio-box", 0, vp=0.1, fp=0.2), _rec("trio-box", 1, vp=0.9, fp=0.95)]
    path = tmp_path / "metrics.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    assert read_metric_records(path) == records


def test_read_metric_records_skips_blank_lines(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text(json.dumps(_rec("trio-box", 0, vp=0.5, fp=0.6)) + "\n\n")
    assert len(read_metric_records(path)) == 1


def test_render_verdict_reports_valid_placed_not_valid_rate():
    v = gate_verdict([_rec("trio-box", 2, vp=0.92, fp=0.95)], "trio-box", threshold=0.9)
    text = render_verdict(v)
    assert "MASTERED" in text
    assert "trio-box" in text
    assert "valid_placed" in text
    assert "valid_rate" not in text  # the documented trap — never headline valid_rate


def _write(tmp_path, records):
    path = tmp_path / "metrics.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def test_main_exits_zero_and_prints_mastered_on_win(tmp_path, capsys):
    path = _write(
        tmp_path, [_rec("trio-box", 0, vp=0.3, fp=0.5), _rec("trio-box", 1, vp=0.91, fp=0.95)]
    )
    code = main([str(path), "--rung", "trio-box"])
    assert code == 0
    assert "MASTERED" in capsys.readouterr().out


def test_main_exits_one_when_not_mastered(tmp_path, capsys):
    path = _write(
        tmp_path, [_rec("trio-box", 0, vp=0.05, fp=0.8), _rec("trio-box", 1, vp=0.1, fp=0.85)]
    )
    code = main([str(path), "--rung", "trio-box"])
    assert code == 1
    assert "PILING" in capsys.readouterr().out


def test_main_exits_two_on_no_data(tmp_path):
    path = _write(tmp_path, [_rec("pair-box", 0, vp=0.9, fp=0.95)])
    assert main([str(path), "--rung", "trio-box"]) == 2


def test_main_defaults_rung_to_trio_box(tmp_path, capsys):
    path = _write(tmp_path, [_rec("trio-box", 0, vp=0.95, fp=0.97)])
    assert main([str(path)]) == 0
    assert "trio-box" in capsys.readouterr().out
