"""Torch-gated tests for the curriculum training loop + collect_rollout extension."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from ml.curriculum import DEFAULT_LADDER, EpisodeStat, sample_request, stage_rng  # noqa: E402
from ml.encoding import EncoderConfig  # noqa: E402
from ml.policy import HangarFitPolicy  # noqa: E402
from ml.stage_builder import build_stage_env, effective_fleet_ids  # noqa: E402
from ml.train import collect_rollout, train  # noqa: E402


def test_collect_rollout_returns_episode_stats_with_resampling():
    # Seed torch so the "an untrained policy parks >=1 object within 64 steps"
    # premise is deterministic — without it the result rides on whatever global
    # RNG state the prior test left (a latent order-dependent flake).
    torch.manual_seed(0)
    stage = DEFAULT_LADDER[1]  # pair-box, 2 objects
    env = build_stage_env(stage)
    policy = HangarFitPolicy()
    pool = effective_fleet_ids(stage)
    rng = stage_rng(0, 1)
    buf, stats = collect_rollout(
        env, policy, EncoderConfig(), 64, sample_request=lambda: sample_request(pool, 2, rng)
    )
    assert stats, "at least one episode should complete in 64 steps"
    assert all(isinstance(s, EpisodeStat) for s in stats)
    for s in stats:
        assert 0.0 <= s.fraction_placed <= 1.0


def test_trivial_train_still_runs_with_new_return_type():
    history = train(seed=0, iterations=1, rollout_len=32)
    assert len(history) == 1
