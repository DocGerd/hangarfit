"""Vectorized training environments (#708, sub-task of #698 / epic #607).

Runs N HangarFitEnvs in parallel so the shapely geometry (env.step) + the encoder's
rasterization (the ~70% training bottleneck) parallelize across worker processes, while
the main process keeps the single batched policy forward + PPO. Workers are TORCH-FREE:
they step + encode + decode the factored action with their own env's turn radius, and
return numpy ObservationTensors. SyncVectorEnv runs the worker body in-process (the
byte-identical reference / test oracle); SubprocVectorEnv runs N spawn workers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NamedTuple

from ml.action_space import decode
from ml.curriculum import EpisodeStat
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
        next_request: Callable[[], tuple[str, ...]] | None,
    ) -> None:
        self._env = env
        self._enc = encoder
        self._next_request = next_request
        self._bodies = {**env.fleet, **env.ground_objects}

    def _encode(self, obs: Observation) -> ObservationTensors:
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
