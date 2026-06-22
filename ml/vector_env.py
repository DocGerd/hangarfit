"""Vectorized training environments (#708, sub-task of #698 / epic #607).

Runs N HangarFitEnvs in parallel so the shapely geometry (env.step) + the encoder's
rasterization (the ~70% training bottleneck) parallelize across worker processes, while
the main process keeps the single batched policy forward + PPO. Workers are TORCH-FREE:
they step + encode + decode the factored action with their own env's turn radius, and
return numpy ObservationTensors. SyncVectorEnv runs the worker body in-process (the
byte-identical reference / test oracle); SubprocVectorEnv runs N spawn workers."""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from typing import Any, NamedTuple, cast

from hangarfit.geometry import pose_cache_scope
from ml.action_space import decode
from ml.curriculum import EpisodeStart, EpisodeStat
from ml.encoding import EncoderConfig, ObservationTensors, encode
from ml.env import HangarFitEnv
from ml.types import Observation, StepInfo


class _EnvWorker:
    """One env + encoder + (optional) per-worker episode sampler. Torch-free. ``step``
    decodes the factored (kind, mag) action using the active object's turn radius, steps,
    and on ``done`` auto-resets (resampling the object set) and returns the fresh obs."""

    def __init__(
        self,
        env: HangarFitEnv,
        encoder: EncoderConfig,
        next_request: Callable[[], EpisodeStart] | None,
        *,
        pose_cache: bool = True,
    ) -> None:
        self._env = env
        self._enc = encoder
        self._next_request = next_request
        self._bodies = {**env.fleet, **env.ground_objects}
        # #733: route this worker's step+encode geometry through cached_parts_world
        # (pose-memoized inside the scope; delegates to aircraft_parts_world on a miss).
        # One fixed fleet, so the pose key is never stale. Default-on; the cache is a
        # byte-identical passthrough, so pose_cache=False reproduces the un-cached run
        # bit-for-bit (the determinism reference for the byte-identity test). The scope is
        # opened per method call, so it is per-env even when SyncVectorEnv steps workers in
        # one process.
        self._pose_cache = pose_cache

    def _scope(self) -> contextlib.AbstractContextManager[None]:
        return pose_cache_scope() if self._pose_cache else contextlib.nullcontext()

    def _encode(self, obs: Observation) -> ObservationTensors:
        return encode(obs, self._env.hangar, self._bodies, self._enc)

    def reset(self) -> ObservationTensors:
        with self._scope():
            start = self._next_request() if self._next_request is not None else None
            obs = self._env.reset(
                requested_ids=start.requested_ids if start else None,
                seed_anchor_k=start.seed_anchor_k if start else None,
            )
            return self._encode(obs)

    def step(
        self, kind_idx: int, mag_idx: int
    ) -> tuple[ObservationTensors, float, bool, StepInfo, EpisodeStat | None]:
        with self._scope():
            obs = self._env._observe()
            if obs.active is None:
                raise RuntimeError(
                    "_EnvWorker.step called on a terminal env (auto-reset failed or step "
                    "after done)"
                )
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
                start = self._next_request() if self._next_request is not None else None
                sem = self._env.reset(
                    requested_ids=start.requested_ids if start else None,
                    seed_anchor_k=start.seed_anchor_k if start else None,
                )
            return self._encode(sem), reward, done, info, ep


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


_WORKER_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


@contextlib.contextmanager
def worker_thread_cap_env() -> Iterator[None]:
    """Set the single-thread BLAS/OMP/MKL caps in ``os.environ`` for the duration of the
    block, restoring prior values on exit (#747).

    Why the PARENT, not the child: OpenBLAS/MKL fix their thread-pool size when the shared
    lib is *loaded* — i.e. at ``import numpy`` — and ignore the env var afterward. Under
    ``spawn`` the child imports numpy/torch during its bootstrap (it unpickles the worker
    factory, which lives in ``ml.train`` → imports them) BEFORE the worker body runs, so
    setting the env vars inside the child is *too late*: the pool is already cores-wide
    (measured cpu/wall ≈ 31 vs 1). Setting them in the parent before ``Process.start()``
    means each spawn child inherits the capped env at interpreter startup, so its
    OpenBLAS/MKL read the cap. The parent's own already-loaded BLAS is unaffected (it does
    not re-read), so the learner keeps its threads; the caps are restored after spawning so
    no unrelated parent code sees them.

    Raising ``--n-envs`` toward the core count is only safe with this cap: otherwise N
    workers each spin a cores-wide pool and the box oversubscribes (the measured ~5.5/32
    duty cycle is the symptom). GEOS/shapely — the dominant rollout cost — is single-
    threaded regardless; this stops a numpy BLAS call from fanning out across the box.

    Byte-identity: workers are TORCH-FREE in their *ops* (no policy forward/backward — see
    the module docstring), so their step+encode float reductions are numpy indexing +
    shapely, never multi-threaded BLAS, and the thread count cannot perturb the reduction
    order. This is the torch-free-worker invariant the #747 byte-identity rests on — proven
    empirically by ``test_sync_equals_subproc_byte_identical`` (Sync runs uncapped in the
    parent; Subproc runs capped) plus the fixed-action reward-stream diff, not from theory.
    """
    prev = {var: os.environ.get(var) for var in _WORKER_THREAD_ENV_VARS}
    os.environ.update({var: "1" for var in _WORKER_THREAD_ENV_VARS})
    try:
        yield
    finally:
        for var, old in prev.items():
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old


def _limit_worker_threads() -> None:
    """Belt-and-suspenders thread cap run at spawn-worker entry (#747).

    The load-bearing cap is the parent-set inherited env (see :func:`worker_thread_cap_env`);
    this re-asserts the env vars and caps torch's intra-op pool at runtime — the one cap
    that *is* effective from inside the child, since ``torch.set_num_threads`` resizes the
    pool live rather than reading an env var at import. torch is imported transitively in
    every real worker (the picklable factory lives in ``ml.train``, which imports torch), so
    the guarded lookup finds it without force-importing torch into a genuinely torch-free
    worker."""
    for var in _WORKER_THREAD_ENV_VARS:
        os.environ[var] = "1"
    torch_mod = sys.modules.get("torch")
    if torch_mod is not None:
        torch_mod.set_num_threads(1)


def _obs_to_picklable(obs: ObservationTensors) -> ObservationTensors:
    """Replace MappingProxyType meta with a plain dict so pickle succeeds across Pipe."""
    if not isinstance(obs.meta, dict):
        return ObservationTensors(
            raster=obs.raster,
            tokens=obs.tokens,
            token_mask=obs.token_mask,
            active_index=obs.active_index,
            legal_action_mask=obs.legal_action_mask,
            meta=dict(obs.meta),
            schema_version=obs.schema_version,
        )
    return obs


def _worker_loop(remote: mp.connection.Connection, worker_fn: Callable[[], _EnvWorker]) -> None:
    """Child process: build the env, then serve reset/step/close over the pipe. Any
    exception is sent back as ('error', repr) so the parent raises rather than hangs."""
    try:
        _limit_worker_threads()  # #747: one core per worker, before the env's first BLAS op
        worker = worker_fn()
        remote.send(("ready", None))
        while True:
            cmd, payload = remote.recv()
            if cmd == "reset":
                remote.send(("ok", _obs_to_picklable(worker.reset())))
            elif cmd == "step":
                k, m = payload
                result = worker.step(k, m)
                # result: (ObservationTensors, float, bool, StepInfo, EpisodeStat|None)
                remote.send(("ok", (_obs_to_picklable(result[0]),) + result[1:]))
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
        self._parents: list[mp.connection.Connection] = []
        self._procs: list[Any] = []  # SpawnProcess is not mp.Process in typeshed
        # Cap the workers' BLAS/OMP threads via the inherited env: each spawn child reads
        # OPENBLAS/MKL/OMP_NUM_THREADS at its OWN numpy/torch import (during bootstrap),
        # so the cap must be in os.environ at Process.start() time — not set late inside
        # the child, where the pool is already cores-wide. Restored once all are spawned.
        with worker_thread_cap_env():
            for fn in worker_fns:
                parent, child = ctx.Pipe()
                proc = ctx.Process(target=_worker_loop, args=(child, fn), daemon=True)
                proc.start()
                child.close()  # parent keeps only its end
                self._parents.append(parent)
                self._procs.append(proc)
        self._closed = False
        for i, (parent, proc) in enumerate(zip(self._parents, self._procs, strict=True)):
            if not parent.poll(timeout=30.0):
                proc.terminate()
                raise RuntimeError(
                    f"SubprocVectorEnv worker {i} did not start within 30s "
                    f"(likely a pickle/import failure in the child — "
                    f"try vec_backend='sync' to see the traceback)"
                )
            # consume the ("ready", None) ack; raises if the child sent ("error", ...) instead
            self._recv(parent, i)

    @property
    def num_envs(self) -> int:
        return self._n

    def _recv(self, parent: mp.connection.Connection, worker_idx: int) -> object:
        try:
            tag, payload = parent.recv()
        except (EOFError, ConnectionResetError) as e:
            raise RuntimeError(
                f"SubprocVectorEnv worker {worker_idx} pipe closed unexpectedly "
                f"(worker crashed before replying — check stderr): {e}"
            ) from e
        if tag == "error":
            raise RuntimeError(f"SubprocVectorEnv worker {worker_idx} failed: {payload}")
        return payload

    def reset(self) -> list[ObservationTensors]:
        for p in self._parents:
            p.send(("reset", None))
        # _recv returns the (untyped) pipe payload; for reset it is always an
        # ObservationTensors (the worker's encoded obs). Narrow it for mypy.
        return [cast(ObservationTensors, self._recv(p, i)) for i, p in enumerate(self._parents)]

    def step(self, actions: Sequence[tuple[int, int]]) -> VecStep:
        if len(actions) != self._n:
            raise ValueError(f"expected {self._n} actions, got {len(actions)}")
        for p, (k, m) in zip(self._parents, actions, strict=True):
            p.send(("step", (int(k), int(m))))
        results = [self._recv(p, i) for i, p in enumerate(self._parents)]
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
                if p.poll(timeout=5.0):
                    p.recv()
            except (EOFError, OSError):
                pass
        for i, proc in enumerate(self._procs):
            proc.join(timeout=5.0)
            if proc.is_alive():  # pragma: no cover
                proc.terminate()
            elif proc.exitcode not in (0, None):
                import warnings

                warnings.warn(
                    f"SubprocVectorEnv worker {i} exited with code {proc.exitcode} — "
                    f"training data from this worker may be incomplete.",
                    RuntimeWarning,
                    stacklevel=2,
                )

    def __enter__(self) -> SubprocVectorEnv:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
