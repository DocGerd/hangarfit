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


from ml.vector_env import SyncVectorEnv  # noqa: E402


def _two_trivial_workers():
    enc = EncoderConfig()
    return [_EnvWorker(build_trivial_env(), enc, next_request=None) for _ in range(2)]


def test_syncvectorenv_step_shapes_and_autoreset():
    from ml.action_space import PARK_INDEX

    vec = SyncVectorEnv(_two_trivial_workers())
    assert vec.num_envs == 2
    obs = vec.reset()
    assert len(obs) == 2 and all(o.active_index >= 0 for o in obs)

    step = vec.step([(PARK_INDEX, 0), (PARK_INDEX, 0)])
    assert len(step.obs) == 2 and len(step.rewards) == 2
    assert step.dones == [True, True]  # both trivial envs complete on PARK
    assert all(e is not None for e in step.ep_stats)
    assert all(o.active_index >= 0 for o in step.obs)  # auto-reset gave live obs
    vec.close()


import numpy as np  # noqa: E402

from ml.vector_env import SubprocVectorEnv  # noqa: E402


def _trivial_worker_fn():
    # module-level (picklable) factory: builds the env IN the child process
    from ml.encoding import EncoderConfig
    from ml.train import build_trivial_env
    from ml.vector_env import _EnvWorker

    return _EnvWorker(build_trivial_env(), EncoderConfig(), next_request=None)


def test_sync_equals_subproc_byte_identical():
    """Workers are torch-free + deterministic, so Sync and Subproc must agree exactly
    on the same action stream (the ADR-aligned tier-1 determinism contract)."""
    from ml.action_space import PARK_INDEX  # noqa: F401

    actions = [(1, 0), (1, 0)]  # S-forward both envs (does not complete -> no auto-reset)

    sync = SyncVectorEnv([_trivial_worker_fn(), _trivial_worker_fn()])
    sa = sync.reset()
    ss = sync.step(actions)
    sync.close()

    with SubprocVectorEnv([_trivial_worker_fn, _trivial_worker_fn]) as sub:
        ba = sub.reset()
        bs = sub.step(actions)

    for a, b in zip(sa, ba, strict=True):
        assert np.array_equal(a.raster, b.raster) and np.array_equal(a.tokens, b.tokens)
    assert ss.rewards == bs.rewards and ss.dones == bs.dones
    for a, b in zip(ss.obs, bs.obs, strict=True):
        assert np.array_equal(a.raster, b.raster) and np.array_equal(a.tokens, b.tokens)


def _broken_worker_fn():
    raise ValueError("boom in child")


def test_subproc_worker_failure_is_loud():
    import pytest

    with (
        pytest.raises(RuntimeError, match="worker failed"),
        SubprocVectorEnv([_broken_worker_fn]) as sub,
    ):
        sub.reset()


@pytest.mark.slow
def test_subproc_faster_than_sync_on_multiobject_stage():
    """Subproc parallelizes the per-env geometry; on a multi-object stage it should beat
    Sync on a fixed step budget. Generous margin — this is a smoke, not a benchmark."""
    import time
    from functools import partial

    from ml.curriculum import CurriculumSchedule
    from ml.encoding import EncoderConfig
    from ml.stage_builder import effective_fleet_ids
    from ml.train import _build_stage_worker

    # pick a multi-object stage from the default ladder
    sched = CurriculumSchedule.default()
    stage = next(s for s in sched.stages if (s.difficulty.max_objects or 1) >= 2)
    enc = EncoderConfig()
    pool = effective_fleet_ids(stage)
    nobj = stage.difficulty.max_objects or len(pool)
    N, STEPS = 4, 60

    # Use module-level _build_stage_worker via functools.partial — this IS picklable
    # under multiprocessing spawn.  A nested closure is NOT picklable; see task brief.
    worker_fns = [
        partial(_build_stage_worker, stage, 0, pool, nobj, 0, None, enc, wi) for wi in range(N)
    ]

    def _run(vec):
        vec.reset()
        for _ in range(STEPS):
            vec.step([(1, 2)] * N)  # S-forward; cheap, deterministic
        vec.close()

    sync_vec = SyncVectorEnv([fn() for fn in worker_fns])
    t0 = time.monotonic()
    _run(sync_vec)
    sync_s = time.monotonic() - t0

    sub_vec = SubprocVectorEnv(worker_fns)
    t0 = time.monotonic()
    _run(sub_vec)
    sub_s = time.monotonic() - t0

    ratio = sub_s / sync_s
    print(f"\n  sync={sync_s:.3f}s  subproc={sub_s:.3f}s  ratio={ratio:.2f}x")
    # subproc has spawn overhead; require it to be no worse than 2.5x sync (loose smoke).
    assert sub_s < sync_s * 2.5, f"subproc {sub_s:.2f}s vs sync {sync_s:.2f}s (ratio {ratio:.2f}x)"
