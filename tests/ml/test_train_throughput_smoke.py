"""Smoke test for the ml/ training-loop throughput canary (#750).

Asserts ``bench.train_throughput`` RUNS and emits the expected JSON keys with a positive
transitions/sec — NOT a timing threshold (a throughput ceiling is jitter-prone on shared
runners; see the #750 risk note). Torch-gated like the rest of the train-loop tests."""

from __future__ import annotations

import json

import pytest

torch = pytest.importorskip("torch")  # noqa: F841  (bench imports torch transitively)

from bench.train_throughput import ThroughputResult, main, run_throughput  # noqa: E402

# A tiny fixed loop: enough to drive both phases (rollout + update) without being slow.
_SMOKE = dict(iterations=2, warmup_iters=1, rollout_len=16, n_envs=2)


def test_run_throughput_reports_positive_rate():
    r = run_throughput(seed=0, **_SMOKE)
    assert isinstance(r, ThroughputResult)
    # Bound on a fixed step COUNT: transitions = timed iters * rollout_len * n_envs.
    assert r.transitions == _SMOKE["iterations"] * _SMOKE["rollout_len"] * _SMOKE["n_envs"]
    assert r.transitions_per_s > 0.0
    assert r.iters_per_s > 0.0
    assert r.total_s > 0.0
    # The phase split is consistent: rollout + update == total, and the fraction is in [0, 1].
    assert r.rollout_s + r.update_s == pytest.approx(r.total_s)
    assert 0.0 <= r.rollout_frac <= 1.0


def test_main_json_emits_expected_keys(capsys):
    rc = main(
        ["--json", "--iters", "2", "--warmup-iters", "1", "--rollout-len", "16", "--n-envs", "2"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    expected = {
        "iterations",
        "warmup_iters",
        "rollout_len",
        "n_envs",
        "transitions",
        "rollout_s",
        "update_s",
        "total_s",
        "transitions_per_s",
        "iters_per_s",
        "rollout_frac",
    }
    assert set(payload) == expected
    assert payload["transitions_per_s"] > 0.0
    assert payload["transitions"] == 2 * 16 * 2


def test_run_throughput_rejects_zero_iterations():
    with pytest.raises(ValueError, match="iterations must be >= 1"):
        run_throughput(iterations=0)
