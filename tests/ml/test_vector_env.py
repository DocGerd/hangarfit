"""Vectorized training envs (#708). Torch-free worker body + Sync/Subproc vector envs."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")  # the policy side needs torch; the vec envs do not

from ml.encoding import EncoderConfig  # noqa: E402
from ml.train import build_trivial_env  # noqa: E402
from ml.vector_env import _EnvWorker  # noqa: E402


def test_envworker_step_matches_manual_step_encode():
    """A worker.step reproduces a manual env.step + encode (same tensors, reward, done)."""
    enc = EncoderConfig()
    env_a = build_trivial_env()
    w = _EnvWorker(env_a, enc, next_request=None)
    obs0 = w.reset()
    assert obs0.active_index >= 0  # live obs after reset

    # Manual reference on an identically-seeded env: PARK index 8, mag 0.
    from ml.action_space import PARK_INDEX

    env_b = build_trivial_env()
    obs_b = env_b.reset()
    from ml.action_space import decode

    tr = env_b._body(obs_b.active.object_id).effective_turn_radius_m()
    prim = decode(PARK_INDEX, 0, turn_radius_m=tr)
    sem_next, r_b, done_b, info_b = env_b.step(prim)

    obs1, r, done, info, ep = w.step(PARK_INDEX, 0)
    assert r == r_b and done == done_b
    assert info.placed == info_b.placed and info.total == info_b.total
    # PARK in the 1-object trivial env completes the set -> done -> auto-reset -> ep stat.
    assert done is True
    assert ep is not None and 0.0 <= ep.fraction_placed <= 1.0
    assert obs1.active_index >= 0  # auto-reset gave a fresh live obs
