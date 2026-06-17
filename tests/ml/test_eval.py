"""Torch-gated tests for the policy-rollout eval runner (#4c-i, #607)."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")  # whole module skips without the [train] extra

from ml.benchmark import BENCH_SET, ReachVerdict  # noqa: E402
from ml.eval import load_policy, policy_reach  # noqa: E402
from ml.policy import HangarFitPolicy  # noqa: E402

_DEMO = next(s for s in BENCH_SET if s.name == "herrenteich_demo")


def test_policy_reach_runs_on_go_free_control():
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    v = policy_reach(_DEMO, policy)
    assert isinstance(v, ReachVerdict)
    assert v.total == 3  # scenario_demo has 3 aircraft
    # An untrained policy will not reach; we only assert it runs end-to-end and verdicts.


def test_checkpoint_save_load_roundtrip(tmp_path):
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    ckpt = tmp_path / "p.pt"
    torch.save(policy.state_dict(), ckpt)
    loaded = load_policy(ckpt, policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2})
    for a, b in zip(policy.state_dict().values(), loaded.state_dict().values(), strict=True):
        assert torch.equal(a, b)
    assert not loaded.training  # load_policy puts it in eval() mode
