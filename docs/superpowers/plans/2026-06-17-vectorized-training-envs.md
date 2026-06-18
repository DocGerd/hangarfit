# Vectorized Training Envs (Sync + Subproc) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run N training environments in parallel so the #698 train-to-mastery run gets throughput — the shapely geometry+encoding (the ~70% bottleneck) parallelizes across torch-free worker processes while the main process does a single batched NN forward + PPO.

**Architecture:** A `VectorEnv` abstraction (`SyncVectorEnv` in-process reference + `SubprocVectorEnv` over N `spawn` workers); workers run **step + encode** (torch-free) and decode the factored action with their own env's turn radius; an N-stream `VecRolloutBuffer` + per-env-column GAE feeding the *unchanged* `ppo_update`; an `n_envs` knob where `n_envs=1` keeps the legacy single-stream path untouched.

**Tech Stack:** Python 3.12, PyTorch 2.12 (`[train]` extra, main process only), numpy, shapely, `multiprocessing` (`spawn` context).

**Spec:** `docs/superpowers/specs/2026-06-17-vectorized-training-envs-design.md`. **Issue:** #708 (sub-task of #698, epic #607).

## Global Constraints

- **Python 3.12 only.** torch is the `[train]` extra; `ml/` is never in the wheel. All ml-side tests `pytest.importorskip("torch")`.
- **Workers are torch-free.** All torch (policy forward + action sampling) stays in the **main process**. `ml/vector_env.py` must not import torch. `SubprocVectorEnv` uses the `multiprocessing` **`spawn`** context.
- **Action over the wire is the factored index pair `(kind_idx, mag_idx)`** (two ints), not a decoded `Primitive`. The worker decodes via `ml.action_space.decode(kind_idx, mag_idx, turn_radius_m=<its active body's effective_turn_radius_m()>)` — the turn radius lookup must stay in the worker (it holds the env).
- **`n_envs == 1` keeps the legacy `collect_rollout` path UNTOUCHED** (trivially byte-identical). Only `n_envs > 1` routes to the vectorized collector. Do not modify `collect_rollout`, `RolloutBuffer`, or `compute_gae`'s existing behavior.
- **Determinism (ml-rl-guard scope, NOT determinism-guard):** `SyncVectorEnv(seed,N)` ≡ `SubprocVectorEnv(seed,N)` byte-identical (workers torch-free); per-worker episode RNG via `stage_rng(seed, stage_index, worker_index)` where **`worker_index=0` must reproduce the existing `stage_rng(seed, stage_index)` stream exactly**. This branch does **not** touch `solver.py`/`towplanner.py`.
- **`VecRolloutBuffer.batch()` returns the SAME dict shape as `RolloutBuffer.batch()`** (flat `T·N` tensors), so `ppo_update` is unchanged.
- `ruff check ml/` + `mypy ml/` clean. `onnxruntime`/`onnx` are unrelated here. After ml/ edits the PostToolUse hook runs `ruff` + `pytest tests/ml/`; the Stop hook runs `mypy ml/`. Keep green.
- **Reference signatures (verbatim from the code):**
  - `ml.env.HangarFitEnv`: `.reset(requested_ids: tuple[str,...] | None = None) -> Observation`; `.step(action: Primitive | Park) -> tuple[Observation, float, bool, StepInfo]`; `.fleet`, `.ground_objects`, `.hangar`, `._body(id)`.
  - `ml.encoding.encode(obs, hangar, bodies, config=EncoderConfig()) -> ObservationTensors`.
  - `ml.action_space.decode(kind_gear_idx: int, mag_bin_idx: int, *, turn_radius_m: float) -> Primitive | Park`.
  - `ml.policy.to_batch(seq[ObservationTensors]) -> dict[str,Tensor]`; `HangarFitPolicy.__call__(batch) -> PolicyOutput`.
  - `ml.ppo.sample_action(out) -> (kind: Tensor, mag: Tensor)`; `factored_logprob_entropy(out, kind_idx, mag_idx) -> (logprob, entropy)`; `compute_gae(rewards, values, dones, last_value, *, gamma, lam) -> (adv, returns)`.
  - `ml.curriculum.EpisodeStat(fraction_placed: float, valid: bool, total_reward: float)`; `stage_rng(seed, stage_index) -> random.Random`; `sample_request(pool, n, rng) -> tuple[str,...]`; `Stage`, `effective_fleet_ids(stage)`, `build_stage_env(stage, *, weights=None) -> HangarFitEnv` (ml.stage_builder).
  - `ml.types.Observation` has `.active: ActiveObject | None` with `.object_id`; `StepInfo` has `.placed`, `.total`, `.valid`.

---

### Task 1: `_EnvWorker` — the torch-free step+encode body with auto-reset

**Files:**
- Create: `ml/vector_env.py`
- Test: `tests/ml/test_vector_env.py`

**Interfaces:**
- Consumes: `ml.env.HangarFitEnv`, `ml.encoding.{encode, EncoderConfig, ObservationTensors}`, `ml.action_space.decode`, `ml.curriculum.EpisodeStat`, `ml.types.StepInfo`.
- Produces:
  - `class _EnvWorker` with `__init__(self, env: HangarFitEnv, encoder: EncoderConfig, next_request: Callable[[], tuple[str, ...]] | None)`, `reset() -> ObservationTensors`, and `step(kind_idx: int, mag_idx: int) -> tuple[ObservationTensors, float, bool, StepInfo, EpisodeStat | None]`. On `done`, it records the finished episode's `EpisodeStat`, calls `env.reset(requested_ids=self._next_request() if set else None)`, and returns the encoded NEW initial obs (auto-reset) — so the returned tensors are always live to act on.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_vector_env.py
"""Vectorized training envs (#708). Torch-free worker body + Sync/Subproc vector envs."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")  # the policy side needs torch; the vec envs do not

from ml.encoding import EncoderConfig, encode  # noqa: E402
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_vector_env.py::test_envworker_step_matches_manual_step_encode -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.vector_env'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ml/vector_env.py
"""Vectorized training environments (#708, sub-task of #698 / epic #607).

Runs N HangarFitEnvs in parallel so the shapely geometry (env.step) + the encoder's
rasterization (the ~70% training bottleneck) parallelize across worker processes, while
the main process keeps the single batched policy forward + PPO. Workers are TORCH-FREE:
they step + encode + decode the factored action with their own env's turn radius, and
return numpy ObservationTensors. SyncVectorEnv runs the worker body in-process (the
byte-identical reference / test oracle); SubprocVectorEnv runs N spawn workers."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ml.action_space import decode
from ml.curriculum import EpisodeStat
from ml.encoding import EncoderConfig, ObservationTensors, encode
from ml.env import HangarFitEnv
from ml.types import StepInfo


class _EnvWorker:
    """One env + encoder + (optional) per-worker episode sampler. Torch-free. ``step``
    decodes the factored (kind, mag) action using the active object's turn radius, steps,
    and on ``done`` auto-resets (resampling the object set) and returns the fresh obs."""

    def __init__(
        self,
        env: HangarFitEnv,
        encoder: EncoderConfig,
        next_request: Callable[[], tuple[str, ...]] | None,
    ) -> None:
        self._env = env
        self._enc = encoder
        self._next_request = next_request
        self._bodies = {**env.fleet, **env.ground_objects}

    def _encode(self, obs) -> ObservationTensors:  # type: ignore[no-untyped-def]
        return encode(obs, self._env.hangar, self._bodies, self._enc)

    def reset(self) -> ObservationTensors:
        req = self._next_request() if self._next_request is not None else None
        return self._encode(self._env.reset(requested_ids=req))

    def step(
        self, kind_idx: int, mag_idx: int
    ) -> tuple[ObservationTensors, float, bool, StepInfo, EpisodeStat | None]:
        obs = self._env._observe()
        assert obs.active is not None, "step on a terminal env (auto-reset failed?)"
        tr = self._env._body(obs.active.object_id).effective_turn_radius_m()
        action = decode(kind_idx, mag_idx, turn_radius_m=tr)
        sem, reward, done, info = self._env.step(action)
        ep: EpisodeStat | None = None
        if done:
            ep = EpisodeStat(
                fraction_placed=info.placed / info.total,
                valid=info.valid,
                total_reward=0.0,  # per-episode reward sum is tracked by the collector
            )
            req = self._next_request() if self._next_request is not None else None
            sem = self._env.reset(requested_ids=req)
        return self._encode(sem), reward, done, info, ep
```

> Note: `_EnvWorker.step` re-derives `obs` via `self._env._observe()` (the live pre-step obs) to read the active body's turn radius — the env exposes `_observe()` and `_body()` (used the same way by `ml/infer.py`/`ml/eval.py`). `total_reward` in the auto-reset `EpisodeStat` is left 0.0 here; the **collector** owns the per-episode reward sum (Task 5), matching how `collect_rollout` builds `EpisodeStat` today.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_vector_env.py::test_envworker_step_matches_manual_step_encode -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/vector_env.py tests/ml/test_vector_env.py
git commit -m "feat(708): _EnvWorker — torch-free step+encode body with auto-reset

Refs #708"
```

---

### Task 2: `SyncVectorEnv` — the in-process reference

**Files:**
- Modify: `ml/vector_env.py`
- Test: `tests/ml/test_vector_env.py`

**Interfaces:**
- Consumes: `_EnvWorker`.
- Produces: `VecStep` (a `NamedTuple`: `obs: list[ObservationTensors]`, `rewards: list[float]`, `dones: list[bool]`, `infos: list[StepInfo]`, `ep_stats: list[EpisodeStat | None]`); `class SyncVectorEnv` with `__init__(self, workers: Sequence[_EnvWorker])`, `num_envs: int`, `reset() -> list[ObservationTensors]`, `step(actions: Sequence[tuple[int, int]]) -> VecStep`, `close() -> None`, and context-manager methods.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_vector_env.py — append
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_vector_env.py::test_syncvectorenv_step_shapes_and_autoreset -v`
Expected: FAIL — `ImportError: cannot import name 'SyncVectorEnv'`.

- [ ] **Step 3: Write minimal implementation**

Add to `ml/vector_env.py` (and `from typing import NamedTuple` to the imports):

```python
class VecStep(NamedTuple):
    obs: list[ObservationTensors]
    rewards: list[float]
    dones: list[bool]
    infos: list[StepInfo]
    ep_stats: list[EpisodeStat | None]


class SyncVectorEnv:
    """N _EnvWorkers stepped serially in-process. The byte-identical reference + the
    test oracle for SubprocVectorEnv; needs no multiprocessing, so it runs in CI."""

    def __init__(self, workers: Sequence[_EnvWorker]) -> None:
        if not workers:
            raise ValueError("SyncVectorEnv needs at least one worker")
        self._workers = list(workers)

    @property
    def num_envs(self) -> int:
        return len(self._workers)

    def reset(self) -> list[ObservationTensors]:
        return [w.reset() for w in self._workers]

    def step(self, actions: Sequence[tuple[int, int]]) -> VecStep:
        if len(actions) != self.num_envs:
            raise ValueError(f"expected {self.num_envs} actions, got {len(actions)}")
        obs, rewards, dones, infos, eps = [], [], [], [], []
        for w, (k, m) in zip(self._workers, actions, strict=True):
            o, r, d, info, ep = w.step(int(k), int(m))
            obs.append(o)
            rewards.append(r)
            dones.append(d)
            infos.append(info)
            eps.append(ep)
        return VecStep(obs, rewards, dones, infos, eps)

    def close(self) -> None:
        pass

    def __enter__(self) -> SyncVectorEnv:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_vector_env.py::test_syncvectorenv_step_shapes_and_autoreset -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/vector_env.py tests/ml/test_vector_env.py
git commit -m "feat(708): SyncVectorEnv — in-process N-env reference

Refs #708"
```

---

### Task 3: `SubprocVectorEnv` — N spawn workers + Sync≡Subproc byte-identity

**Files:**
- Modify: `ml/vector_env.py`
- Test: `tests/ml/test_vector_env.py`

**Interfaces:**
- Consumes: `_EnvWorker`, `VecStep`. Workers are built inside each process from a **picklable factory** `Callable[[], _EnvWorker]` (env objects + their shapely geometry must be constructed in the child, not pickled across).
- Produces: `class SubprocVectorEnv` with `__init__(self, worker_fns: Sequence[Callable[[], _EnvWorker]])`, the same `num_envs`/`reset`/`step`/`close`/context-manager surface as `SyncVectorEnv`. A dead/raising worker surfaces a loud `RuntimeError` from `step`/`reset` (no silent hang).

- [ ] **Step 1: Write the failing test (Sync ≡ Subproc byte-identity)**

```python
# tests/ml/test_vector_env.py — append
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
    from ml.action_space import PARK_INDEX

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
        assert np.array_equal(a.raster, b.raster)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_vector_env.py::test_sync_equals_subproc_byte_identical -v`
Expected: FAIL — `ImportError: cannot import name 'SubprocVectorEnv'`.

- [ ] **Step 3: Write minimal implementation**

Add to `ml/vector_env.py` (and `import multiprocessing as mp` to the imports):

```python
def _worker_loop(remote, worker_fn):  # type: ignore[no-untyped-def]
    """Child process: build the env, then serve reset/step/close over the pipe. Any
    exception is sent back as ('error', repr) so the parent raises rather than hangs."""
    try:
        worker = worker_fn()
        while True:
            cmd, payload = remote.recv()
            if cmd == "reset":
                remote.send(("ok", worker.reset()))
            elif cmd == "step":
                k, m = payload
                remote.send(("ok", worker.step(k, m)))
            elif cmd == "close":
                remote.send(("ok", None))
                remote.close()
                return
            else:  # pragma: no cover - defensive
                remote.send(("error", f"unknown cmd {cmd!r}"))
    except Exception as exc:  # surface loudly to the parent
        try:
            remote.send(("error", repr(exc)))
        finally:
            remote.close()


class SubprocVectorEnv:
    """N _EnvWorkers, each in its own spawn process. Parallelizes the per-env shapely
    geometry + encoding. Workers are torch-free, so output is byte-identical to
    SyncVectorEnv on the same action stream."""

    def __init__(self, worker_fns: Sequence[Callable[[], _EnvWorker]]) -> None:
        if not worker_fns:
            raise ValueError("SubprocVectorEnv needs at least one worker_fn")
        ctx = mp.get_context("spawn")
        self._n = len(worker_fns)
        self._parents, self._procs = [], []
        for fn in worker_fns:
            parent, child = ctx.Pipe()
            proc = ctx.Process(target=_worker_loop, args=(child, fn), daemon=True)
            proc.start()
            child.close()  # parent keeps only its end
            self._parents.append(parent)
            self._procs.append(proc)
        self._closed = False

    @property
    def num_envs(self) -> int:
        return self._n

    def _recv(self, parent) -> object:  # type: ignore[no-untyped-def]
        tag, payload = parent.recv()
        if tag == "error":
            raise RuntimeError(f"SubprocVectorEnv worker failed: {payload}")
        return payload

    def reset(self) -> list[ObservationTensors]:
        for p in self._parents:
            p.send(("reset", None))
        return [self._recv(p) for p in self._parents]  # type: ignore[misc]

    def step(self, actions: Sequence[tuple[int, int]]) -> VecStep:
        if len(actions) != self._n:
            raise ValueError(f"expected {self._n} actions, got {len(actions)}")
        for p, (k, m) in zip(self._parents, actions, strict=True):
            p.send(("step", (int(k), int(m))))
        results = [self._recv(p) for p in self._parents]
        obs = [r[0] for r in results]  # type: ignore[index]
        return VecStep(
            obs,
            [r[1] for r in results],  # type: ignore[index]
            [r[2] for r in results],  # type: ignore[index]
            [r[3] for r in results],  # type: ignore[index]
            [r[4] for r in results],  # type: ignore[index]
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for p in self._parents:
            try:
                p.send(("close", None))
                p.recv()
            except (EOFError, OSError):
                pass
        for proc in self._procs:
            proc.join(timeout=5.0)
            if proc.is_alive():  # pragma: no cover
                proc.terminate()

    def __enter__(self) -> SubprocVectorEnv:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_vector_env.py::test_sync_equals_subproc_byte_identical -v`
Expected: PASS. (If `spawn` re-imports are slow, the test still completes in a few seconds. If a worker import fails, the parent raises `RuntimeError: ... worker failed` — that is the loud-failure contract; fix the import, do not swallow.)

- [ ] **Step 5: Add the worker-failure test**

```python
# tests/ml/test_vector_env.py — append
def _broken_worker_fn():
    raise ValueError("boom in child")


def test_subproc_worker_failure_is_loud():
    import pytest

    with pytest.raises(RuntimeError, match="worker failed"):
        with SubprocVectorEnv([_broken_worker_fn]) as sub:
            sub.reset()
```

Run: `pytest tests/ml/test_vector_env.py -v`
Expected: PASS (all vector_env tests).

- [ ] **Step 6: Commit**

```bash
git add ml/vector_env.py tests/ml/test_vector_env.py
git commit -m "feat(708): SubprocVectorEnv — spawn workers, byte-identical to Sync, loud failure

Refs #708"
```

---

### Task 4: `VecRolloutBuffer` + per-env GAE

**Files:**
- Modify: `ml/ppo.py`
- Test: `tests/ml/test_ppo.py`

**Interfaces:**
- Consumes: `compute_gae` (existing), `to_batch`, `ObservationTensors`.
- Produces:
  - `class VecRolloutBuffer(num_envs: int)` storing per-step rows: `add_step(obs: list[ObservationTensors], kind_idx: list[int], mag_idx: list[int], logprob: list[float], value: list[float], reward: list[float], done: list[bool])`; `last_value: list[float]`; `__len__` = number of steps `T`; `batch() -> dict[str, Tensor]` flattening **row-major `(t, env)` → index `t*N + env`**, returning the SAME keys as `RolloutBuffer.batch()`.
  - `compute_gae_vec(rewards, values, dones, last_values, *, gamma, lam) -> (advantages, returns)` where inputs are `(T, N)` tensors + `last_values` length-N; computes per env column via `compute_gae` and returns flat `(T*N,)` tensors in the same row-major order as `batch()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_ppo.py — append (file already importorskips torch)
def test_compute_gae_vec_matches_per_env_compute_gae():
    import torch

    from ml.ppo import compute_gae, compute_gae_vec

    T, N = 4, 3
    torch.manual_seed(0)
    rewards = torch.randn(T, N)
    values = torch.randn(T, N)
    dones = (torch.rand(T, N) > 0.7)
    last_values = [0.1, -0.2, 0.3]

    adv_vec, ret_vec = compute_gae_vec(rewards, values, dones, last_values, gamma=0.99, lam=0.95)
    # Per-env reference, flattened row-major (t*N + env).
    for env in range(N):
        a, r = compute_gae(
            rewards[:, env], values[:, env], dones[:, env], last_values[env], gamma=0.99, lam=0.95
        )
        for t in range(T):
            assert torch.allclose(adv_vec[t * N + env], a[t])
            assert torch.allclose(ret_vec[t * N + env], r[t])


def test_vecrolloutbuffer_batch_shape_matches_flat():
    import torch

    from ml.encoding import ACTION_DIM, RASTER_CHANNELS, TOKEN_DIM, ObservationTensors
    from ml.ppo import VecRolloutBuffer

    def _obs():
        import numpy as np

        return ObservationTensors(
            raster=np.zeros((RASTER_CHANNELS, 8, 8), np.float32),
            tokens=np.zeros((4, TOKEN_DIM), np.float32),
            token_mask=np.ones(4, bool),
            active_index=0,
            legal_action_mask=np.ones(ACTION_DIM, bool),
            meta={},
        )

    buf = VecRolloutBuffer(num_envs=2)
    buf.add_step([_obs(), _obs()], [1, 2], [0, 1], [0.0, 0.0], [0.0, 0.0], [1.0, 2.0], [False, True])
    buf.add_step([_obs(), _obs()], [3, 4], [2, 3], [0.0, 0.0], [0.0, 0.0], [3.0, 4.0], [True, False])
    buf.last_value = [0.0, 0.0]
    data = buf.batch()
    assert data["reward"].shape == (4,)  # T*N
    assert data["reward"].tolist() == [1.0, 2.0, 3.0, 4.0]  # row-major (t, env)
    assert set(data) >= {"raster", "tokens", "kind_idx", "mag_idx", "old_logprob", "value", "reward", "done"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_ppo.py::test_compute_gae_vec_matches_per_env_compute_gae -v`
Expected: FAIL — `ImportError: cannot import name 'compute_gae_vec'`.

- [ ] **Step 3: Write minimal implementation**

Add to `ml/ppo.py`:

```python
def compute_gae_vec(
    rewards: Tensor,  # (T, N)
    values: Tensor,  # (T, N)
    dones: Tensor,  # (T, N) bool
    last_values: Sequence[float],  # length N
    *,
    gamma: float = 0.99,
    lam: float = 0.95,
) -> tuple[Tensor, Tensor]:
    """Per-env GAE: run the single-stream ``compute_gae`` on each env column (each env's
    ``done`` resets its own λ-recursion + bootstrap), then flatten row-major ``(t, env) ->
    t*N + env`` to match ``VecRolloutBuffer.batch()``."""
    t_len, n = rewards.shape
    adv = torch.zeros(t_len, n)
    ret = torch.zeros(t_len, n)
    for env in range(n):
        a, r = compute_gae(
            rewards[:, env], values[:, env], dones[:, env], float(last_values[env]),
            gamma=gamma, lam=lam,
        )
        adv[:, env] = a
        ret[:, env] = r
    return adv.reshape(-1), ret.reshape(-1)


class VecRolloutBuffer:
    """N-stream rollout buffer. Stores T per-step rows of width N; ``batch()`` flattens
    row-major (t, env) into the SAME flat dict shape as RolloutBuffer.batch(), so
    ppo_update is unchanged. ``last_value`` is the per-env bootstrap (length N)."""

    def __init__(self, num_envs: int) -> None:
        self.num_envs = num_envs
        self.obs: list[list[ObservationTensors]] = []
        self.kind_idx: list[list[int]] = []
        self.mag_idx: list[list[int]] = []
        self.logprob: list[list[float]] = []
        self.value: list[list[float]] = []
        self.reward: list[list[float]] = []
        self.done: list[list[bool]] = []
        self.last_value: list[float] = [0.0] * num_envs

    def add_step(
        self,
        obs: list[ObservationTensors],
        kind_idx: list[int],
        mag_idx: list[int],
        logprob: list[float],
        value: list[float],
        reward: list[float],
        done: list[bool],
    ) -> None:
        self.obs.append(obs)
        self.kind_idx.append(kind_idx)
        self.mag_idx.append(mag_idx)
        self.logprob.append(logprob)
        self.value.append(value)
        self.reward.append(reward)
        self.done.append(done)

    def __len__(self) -> int:
        return len(self.reward)

    def _flat(self, rows: list[list[float]]) -> list[float]:
        return [x for row in rows for x in row]  # row-major (t, env)

    def batch(self) -> dict[str, Tensor]:
        flat_obs = [o for row in self.obs for o in row]
        data = dict(to_batch(flat_obs))
        data["kind_idx"] = torch.tensor(
            [x for row in self.kind_idx for x in row], dtype=torch.long
        )
        data["mag_idx"] = torch.tensor(
            [x for row in self.mag_idx for x in row], dtype=torch.long
        )
        data["old_logprob"] = torch.tensor(self._flat(self.logprob), dtype=torch.float32)
        data["value"] = torch.tensor(self._flat(self.value), dtype=torch.float32)
        data["reward"] = torch.tensor(self._flat(self.reward), dtype=torch.float32)
        data["done"] = torch.tensor(
            [x for row in self.done for x in row], dtype=torch.bool
        )
        return data
```

Add `from collections.abc import Sequence` to `ml/ppo.py`'s imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ml/test_ppo.py -k "vec or gae_vec" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ml/ppo.py tests/ml/test_ppo.py
git commit -m "feat(708): VecRolloutBuffer + per-env compute_gae_vec (flat batch shape)

Refs #708"
```

---

### Task 5: `collect_rollout_vec` — the vectorized collector

**Files:**
- Modify: `ml/train.py`
- Test: `tests/ml/test_train_curriculum.py`

**Interfaces:**
- Consumes: `SyncVectorEnv`/`SubprocVectorEnv` (anything with `num_envs`/`reset`/`step`), `VecRolloutBuffer`, `compute_gae_vec` (for the bootstrap-aware GAE happens in `ppo_update` — see note), `to_batch`, `sample_action`, `factored_logprob_entropy`, `EncoderConfig`.
- Produces: `collect_rollout_vec(vec_env, policy, encoder, rollout_len) -> tuple[VecRolloutBuffer, list[EpisodeStat]]` — drives the vec env for `rollout_len` steps (`rollout_len * num_envs` transitions), batching obs through the policy; tracks a per-env running reward sum so each completed episode's `EpisodeStat` carries `total_reward`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_train_curriculum.py — append (file importorskips torch)
def test_collect_rollout_vec_fills_buffer_and_stats():
    import torch

    from ml.encoding import EncoderConfig
    from ml.policy import HangarFitPolicy
    from ml.train import build_trivial_env, collect_rollout_vec
    from ml.vector_env import SyncVectorEnv, _EnvWorker

    torch.manual_seed(0)
    enc = EncoderConfig()
    vec = SyncVectorEnv([_EnvWorker(build_trivial_env(), enc, None) for _ in range(2)])
    policy = HangarFitPolicy()
    buf, stats = collect_rollout_vec(vec, policy, enc, rollout_len=8)
    vec.close()
    assert len(buf) == 8 and buf.num_envs == 2
    assert len(buf.last_value) == 2
    # the trivial env completes on PARK, so some episodes finish -> stats with total_reward
    assert all(isinstance(s.total_reward, float) for s in stats)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_train_curriculum.py::test_collect_rollout_vec_fills_buffer_and_stats -v`
Expected: FAIL — `ImportError: cannot import name 'collect_rollout_vec'`.

- [ ] **Step 3: Write minimal implementation**

Add to `ml/train.py` (imports: `from ml.ppo import VecRolloutBuffer`; `from ml.vector_env import VecStep`):

```python
def collect_rollout_vec(
    vec_env,  # SyncVectorEnv | SubprocVectorEnv (duck-typed: num_envs/reset/step)
    policy: HangarFitPolicy,
    encoder: EncoderConfig,
    rollout_len: int,
) -> tuple[VecRolloutBuffer, list[EpisodeStat]]:
    """Drive `vec_env` for `rollout_len` steps (rollout_len * num_envs transitions),
    batching the N observations through one policy forward per step. Returns the
    (T, N) buffer + the per-completed-episode stats (with the per-env reward sum)."""
    n = vec_env.num_envs
    buf = VecRolloutBuffer(num_envs=n)
    obs = vec_env.reset()
    ep_reward = [0.0] * n
    ep_stats: list[EpisodeStat] = []
    with torch.no_grad():
        for _ in range(rollout_len):
            out = policy(to_batch(obs))
            kind, mag = sample_action(out)
            logprob, _ = factored_logprob_entropy(out, kind, mag)
            actions = [(int(kind[i]), int(mag[i])) for i in range(n)]
            step: VecStep = vec_env.step(actions)
            buf.add_step(
                obs,
                kind_idx=[int(kind[i]) for i in range(n)],
                mag_idx=[int(mag[i]) for i in range(n)],
                logprob=[float(logprob[i]) for i in range(n)],
                value=[float(out.value[i]) for i in range(n)],
                reward=list(step.rewards),
                done=list(step.dones),
            )
            for i in range(n):
                ep_reward[i] += step.rewards[i]
                if step.dones[i]:
                    s = step.ep_stats[i]
                    assert s is not None
                    ep_stats.append(
                        EpisodeStat(
                            fraction_placed=s.fraction_placed,
                            valid=s.valid,
                            total_reward=ep_reward[i],
                        )
                    )
                    ep_reward[i] = 0.0
            obs = step.obs
        # per-env bootstrap value for non-done tails
        tail = policy(to_batch(obs))
        buf.last_value = [float(tail.value[i]) for i in range(n)]
    return buf, ep_stats
```

Update `ppo_update` to accept either buffer: it already calls `buffer.batch()` and reads `buffer.last_value`. For a `VecRolloutBuffer`, `last_value` is a **list**; the GAE must use `compute_gae_vec`. Add a branch in `ppo_update` keyed on `isinstance(buffer, VecRolloutBuffer)`:

```python
# in ppo_update, replace the single compute_gae call with:
    if isinstance(buffer, VecRolloutBuffer):
        from ml.ppo import compute_gae_vec  # local: same module, avoids reorder churn
        T = len(buffer)
        N = buffer.num_envs
        advantages, returns = compute_gae_vec(
            rewards.reshape(T, N),
            data["value"].reshape(T, N),
            data["done"].reshape(T, N),
            buffer.last_value,
            gamma=config.gamma,
            lam=config.lam,
        )
    else:
        advantages, returns = compute_gae(
            rewards, data["value"], data["done"], buffer.last_value,
            gamma=config.gamma, lam=config.lam,
        )
```

(`rewards` is already the normalized-or-raw flat tensor; reshape `(T, N)` is row-major, matching `batch()`. The `RolloutBuffer` path is byte-identical — the `else` branch is the original call.) Type the `buffer` param as `RolloutBuffer | VecRolloutBuffer`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_train_curriculum.py::test_collect_rollout_vec_fills_buffer_and_stats -v`
Expected: PASS. Also run `pytest tests/ml/test_ppo.py -q` to confirm the `RolloutBuffer` path is unchanged.

- [ ] **Step 5: Commit**

```bash
git add ml/train.py ml/ppo.py tests/ml/test_train_curriculum.py
git commit -m "feat(708): collect_rollout_vec + ppo_update VecRolloutBuffer branch

Refs #708"
```

---

### Task 6: `n_envs` knob + per-worker seeding + N=1≡legacy + Sync≡Subproc training

**Files:**
- Modify: `ml/curriculum.py` (extend `stage_rng` with `worker_index`), `ml/train.py` (`train_curriculum(n_envs=…)` + CLI), `ml/stage_builder.py` (no change expected — verify)
- Test: `tests/ml/test_train_curriculum.py`, `tests/ml/test_curriculum.py`

**Interfaces:**
- Consumes: Task 5's `collect_rollout_vec`; `SyncVectorEnv`/`SubprocVectorEnv`; `_EnvWorker`.
- Produces: `stage_rng(seed, stage_index, worker_index: int = 0)` (worker_index=0 reproduces the current stream exactly); `train_curriculum(..., n_envs: int = 1, vec_backend: Literal["sync","subproc"] = "subproc")`; `--n-envs`/`--vec-backend` CLI flags.

- [ ] **Step 1: Write the failing test (stage_rng worker_index=0 unchanged + distinct streams)**

```python
# tests/ml/test_curriculum.py — append
def test_stage_rng_worker_index_zero_is_legacy_and_distinct():
    import random

    from ml.curriculum import stage_rng

    base = stage_rng(7, 2)  # legacy 2-arg call
    w0 = stage_rng(7, 2, worker_index=0)  # must match legacy exactly
    assert [base.random() for _ in range(5)] == [w0.random() for _ in range(5)]
    # different workers => different streams
    w1 = stage_rng(7, 2, worker_index=1)
    w0b = stage_rng(7, 2, worker_index=0)
    assert [w0b.random() for _ in range(5)] != [w1.random() for _ in range(5)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_curriculum.py::test_stage_rng_worker_index_zero_is_legacy_and_distinct -v`
Expected: FAIL — `stage_rng() got an unexpected keyword argument 'worker_index'`.

- [ ] **Step 3: Extend `stage_rng`**

In `ml/curriculum.py`:

```python
_WORKER_RNG_STRIDE = 1000003  # a prime distinct from _STAGE_RNG_STRIDE (per-worker offset)


def stage_rng(seed: int, stage_index: int, worker_index: int = 0) -> random.Random:
    """A per-stage (and, for vectorized training, per-worker) RNG isolated from torch's
    global stream. ``worker_index=0`` reproduces the legacy single-stream value exactly
    (the +0 term), so the n_envs=1 path is byte-identical; worker_index>0 derives a
    distinct, collision-free stream."""
    return random.Random(seed * _STAGE_RNG_STRIDE + stage_index + worker_index * _WORKER_RNG_STRIDE)
```

(Verify `worker_index=0` gives `seed * _STAGE_RNG_STRIDE + stage_index` — the original.)

- [ ] **Step 4: Write the n_envs training test + the byte-identity test**

```python
# tests/ml/test_train_curriculum.py — append
def test_train_curriculum_n_envs_runs():
    from ml.curriculum import CurriculumSchedule
    from ml.train import train_curriculum

    sched = CurriculumSchedule.default()
    # cap the run tiny: 1 iter per stage, 2 envs, sync backend (no subprocess in CI)
    from dataclasses import replace

    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    hist = train_curriculum(seed=0, schedule=sched, rollout_len=16, n_envs=2, vec_backend="sync")
    assert hist.promotions  # advanced through stages


def test_train_curriculum_n_envs_1_matches_legacy_byte_identical():
    """n_envs=1 must reproduce the legacy single-stream training history exactly."""
    from dataclasses import replace

    from ml.curriculum import CurriculumSchedule
    from ml.train import train_curriculum

    sched = replace(CurriculumSchedule.default(), policy=replace(CurriculumSchedule.default().policy, max_iters=1))
    legacy = train_curriculum(seed=0, schedule=sched, rollout_len=16)  # default n_envs=1
    again = train_curriculum(seed=0, schedule=sched, rollout_len=16, n_envs=1)
    assert legacy.promotions == again.promotions
    assert legacy.history == again.history  # CurriculumHistory equality (per-iter records)
```

> If `CurriculumHistory` is not directly `==`-comparable, compare its public record list (e.g. `legacy.records == again.records` or whatever the dataclass exposes — read `ml/curriculum.py` `CurriculumHistory` and assert on its stored per-iteration data). The point is exact equality of the recorded training signal.

- [ ] **Step 5: Wire `n_envs` into `train_curriculum`**

In `ml/train.py::train_curriculum`, after `env = build_stage_env(stage, weights=weights)` and the existing `pool`/`n`/`rng`/`next_request` setup, branch:

```python
    # n_envs == 1: the legacy single-stream path is UNTOUCHED (byte-identical).
    if n_envs == 1:
        ... existing collect_rollout(...) loop unchanged ...
    else:
        from ml.vector_env import SyncVectorEnv, SubprocVectorEnv, _EnvWorker

        def _make_worker_fn(wi: int):
            # picklable for subproc: rebuild stage env + this worker's sampler in-child
            def _fn():
                wenv = build_stage_env(stage, weights=weights)
                wrng = stage_rng(seed, stage_index, worker_index=wi)
                wnext = partial(sample_request, pool, n, wrng)
                return _EnvWorker(wenv, enc, wnext)
            return _fn

        worker_fns = [_make_worker_fn(wi) for wi in range(n_envs)]
        if vec_backend == "subproc":
            vec_cm = SubprocVectorEnv(worker_fns)
        else:
            vec_cm = SyncVectorEnv([fn() for fn in worker_fns])
        with vec_cm as vec:
            for it in range(pol.max_iters):
                it_cfg = replace(cfg, entropy_coef=entropy_coef_at(it, base=cfg.entropy_coef,
                    start=cfg.entropy_coef_start, end=cfg.entropy_coef_end,
                    anneal_iters=cfg.entropy_anneal_iters))
                buf, ep_stats = collect_rollout_vec(vec, policy, enc, rollout_len)
                ppo_update(policy, optimizer, buf, it_cfg, normalizer=normalizer)
                window.extend(ep_stats)
                history.record(stage.name, it, ep_stats)
                if should_promote(list(window), pol):
                    history.note_promotion(stage.name, it, by="competency")
                    break
            else:
                history.note_promotion(stage.name, pol.max_iters - 1, by="cap")
        continue  # next stage
```

Add `n_envs: int = 1` and `vec_backend: Literal["sync", "subproc"] = "subproc"` to the `train_curriculum` signature; thread `n_envs`/`vec_backend` through `main()` from new argparse flags `--n-envs` (int, default 1) and `--vec-backend` (choices sync/subproc, default subproc). The `enc` local is the existing `enc = encoder or EncoderConfig()`.

> Keep the refactor minimal: the cleanest shape is to extract the per-iteration body into a small helper both branches call, but if that risks the n_envs=1 byte-identity, leave the legacy branch literally untouched and only add the `else` branch.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/ml/test_curriculum.py tests/ml/test_train_curriculum.py -v`
Expected: PASS — including the n_envs=1 byte-identity test. Run `mypy ml/` clean (the `vec_cm` union type may need a `VectorEnv` Protocol or a `SyncVectorEnv | SubprocVectorEnv` annotation — add whichever mypy requires).

- [ ] **Step 7: Commit**

```bash
git add ml/curriculum.py ml/train.py tests/ml/test_curriculum.py tests/ml/test_train_curriculum.py
git commit -m "feat(708): n_envs knob (n_envs=1 byte-identical to legacy) + per-worker seeding

Refs #708"
```

---

### Task 7: Throughput smoke + docs + final verification

**Files:**
- Test: `tests/ml/test_vector_env.py` (the `@slow` throughput smoke)
- Modify: `CHANGELOG.md`, `ml/README.md`

**Interfaces:** none new.

- [ ] **Step 1: Throughput smoke (`@slow`)**

```python
# tests/ml/test_vector_env.py — append
import pytest


@pytest.mark.slow
def test_subproc_faster_than_sync_on_multiobject_stage():
    """Subproc parallelizes the per-env geometry; on a multi-object stage it should beat
    Sync on a fixed step budget. Generous margin — this is a smoke, not a benchmark."""
    import time

    from dataclasses import replace

    from ml.curriculum import CurriculumSchedule, stage_rng
    from ml.encoding import EncoderConfig
    from ml.stage_builder import build_stage_env, effective_fleet_ids
    from ml.vector_env import SubprocVectorEnv, SyncVectorEnv, _EnvWorker
    from functools import partial
    from ml.curriculum import sample_request

    # pick a multi-object stage from the default ladder
    sched = CurriculumSchedule.default()
    stage = next(s for s in sched.stages if (s.difficulty.max_objects or 1) >= 2)
    enc = EncoderConfig()
    pool = effective_fleet_ids(stage)
    nobj = stage.difficulty.max_objects or len(pool)
    N, STEPS = 4, 30

    def _fn(wi):
        def _f():
            rng = stage_rng(0, 0, worker_index=wi)
            return _EnvWorker(build_stage_env(stage), enc, partial(sample_request, pool, nobj, rng))
        return _f

    def _run(vec):
        vec.reset()
        for _ in range(STEPS):
            vec.step([(1, 2)] * N)  # S-forward; cheap, deterministic
        vec.close()

    fns = [_fn(i) for i in range(N)]
    t0 = time.monotonic(); _run(SyncVectorEnv([f() for f in fns])); sync_s = time.monotonic() - t0
    with SubprocVectorEnv(fns) as sub:
        t0 = time.monotonic(); _run(sub); sub_s = time.monotonic() - t0
    # subproc has spawn overhead; require it to be no worse than 1.5x sync (loose smoke).
    assert sub_s < sync_s * 1.5, f"subproc {sub_s:.2f}s vs sync {sync_s:.2f}s"
```

Run: `pytest tests/ml/test_vector_env.py -m slow -v`
Expected: PASS. (This is a loose smoke; if `spawn` overhead dominates on the tiny step budget, raise STEPS or the margin — but do not delete the parallelism check. Note the result in the commit message.)

- [ ] **Step 2: CHANGELOG entry**

Under `## [Unreleased]` → `### Added`:

```markdown
- **Vectorized training envs (#708, epic #607).** `train_curriculum`/`ml.train` gain an
  `n_envs` knob (`--n-envs`, `--vec-backend {sync,subproc}`) that runs N cold-joint envs in
  parallel for throughput — the shapely geometry + encoder rasterization run across N
  torch-free worker processes (`ml/vector_env.py`: `SyncVectorEnv`/`SubprocVectorEnv`), while
  the main process keeps the single batched policy forward + PPO (`VecRolloutBuffer` +
  per-env GAE). `n_envs=1` keeps the legacy single-stream path byte-identical. Because the
  workers are torch-free, `Sync(seed,N)` and `Subproc(seed,N)` are byte-identical.
  Foundation for the #698 train-to-mastery run.
```

- [ ] **Step 3: ml/README.md**

Add an "Vectorized training (#708)" note under the training section: `python -m ml.train --schedule curriculum --n-envs 8 --vec-backend subproc` runs 8 parallel envs; `n_envs=1` (default) is the unchanged single-stream path; Sync/Subproc are byte-identical (workers torch-free).

- [ ] **Step 4: Final verification**

Run:
```bash
pytest tests/ml/test_vector_env.py tests/ml/test_ppo.py tests/ml/test_curriculum.py tests/ml/test_train_curriculum.py -v
ruff check ml/ tests/ml/ && ruff format --check ml/ tests/ml/
mypy ml/
```
Expected: all green (the `@slow` smoke runs only under `-m slow`).

- [ ] **Step 5: Commit**

```bash
git add tests/ml/test_vector_env.py CHANGELOG.md ml/README.md
git commit -m "test(708): throughput smoke + docs (CHANGELOG, ml/README)

Refs #708"
```

---

## Self-Review

**Spec coverage:**
- §3.1 `VectorEnv`/`SyncVectorEnv`/`SubprocVectorEnv`/worker step+encode/auto-reset → Tasks 1–3. ✓
- §3.2 `VecRolloutBuffer` + per-env GAE + unchanged `ppo_update` (same batch shape) → Task 4 (+ the `ppo_update` branch in Task 5). ✓
- §3.3 `collect_rollout_vec` + `n_envs` knob + `n_envs=1` legacy-untouched + CLI flags → Tasks 5–6. ✓
- §4 determinism: `Sync≡Subproc` byte-identical → Task 3; `n_envs=1 ≡ legacy` → Task 6; per-worker `stage_rng(worker_index=0)==legacy` → Task 6. ✓
- §6 tests: worker (T1), Sync (T2), Sync≡Subproc + worker-failure (T3), per-env GAE (T4), collector (T5), n_envs train + byte-identity (T6), throughput smoke (T7). ✓
- §7 acceptance: every checkbox maps to a task. ✓

**Placeholder scan:** Two spots intentionally defer a detail to the implementer with a concrete fallback, not a vague TODO: the `CurriculumHistory` equality assertion in T6 (read the dataclass; assert on its stored records) and the `vec_cm` union-type annotation in T6 (add what mypy requires). Both are bounded "use the real type from the code" notes, not unspecified logic. No "add error handling"/"TBD" placeholders.

**Type consistency:** `VecStep` fields (obs/rewards/dones/infos/ep_stats) are produced in T2 and consumed in T5. `VecRolloutBuffer.add_step` signature (T4) matches the call in `collect_rollout_vec` (T5). `compute_gae_vec(rewards,values,dones,last_values,*,gamma,lam)` defined T4, used T5. `stage_rng(seed,stage_index,worker_index=0)` defined T6, used in the worker_fn (T6). `_EnvWorker(env, encoder, next_request)` defined T1, used T2/T5/T6/T7. Action over the wire is `(kind_idx, mag_idx)` everywhere.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-17-vectorized-training-envs.md`.
