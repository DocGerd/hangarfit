"""python -m ml.train — train HangarFitPolicy on the fixed trivial curriculum stage
via the roll-your-own PPO in ml.ppo. Sub-project #4a (#607). Requires the [train] extra.

The trivial stage: a single object driven in from the apron and parked in a loose
hangar within a small step budget — the easiest curriculum rung. Curriculum ramping is
sub-project #4b; the reach-not-beat benchmark is #4c."""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Literal

import numpy as np
import torch
from torch import Tensor

from hangarfit.geometry import pose_cache_scope
from hangarfit.loader import load_fleet, load_hangar
from ml.action_space import decode
from ml.checkpoint import load_checkpoint, save_checkpoint
from ml.curriculum import (
    BudgetController,
    CurriculumHistory,
    CurriculumSchedule,
    EpisodeStart,
    EpisodeStat,
    PromotionPolicy,
    Stage,
    format_iter_log,
    history_metric_records,
    make_episode_sampler,
    should_promote,
    stage_rng,
    truncate_after_rung,
    validate_ladder,
    window_score,
    with_mixed_anchor_rung,
    with_pair_anchored_rung,
    with_promotion_overrides,
    with_solo_box_rung,
    with_trio_notch_anchored_rung,
)
from ml.encoding import EncoderConfig, encode, static_block
from ml.env import HangarFitEnv
from ml.export import export_onnx
from ml.policy import HangarFitPolicy, to_batch
from ml.ppo import (
    PPOConfig,
    ReturnNormalizer,
    RolloutBuffer,
    VecRolloutBuffer,
    entropy_coef_at,
    factored_logprob_entropy,
    ppo_update,
    sample_action,
)
from ml.stage_builder import build_stage_env, effective_fleet_ids
from ml.types import DifficultyConfig, RewardWeights
from ml.vector_env import VecStep, _EnvWorker

_TRIVIAL_DIFFICULTY = DifficultyConfig(
    max_objects=1, per_object_step_budget=40, total_step_budget=40
)


def _to_device(batch: dict[str, Tensor], device: torch.device) -> dict[str, Tensor]:
    """Move a batched-observation dict to the policy's device for the forward. A no-op for
    a CPU device (returns the SAME dict — no copy), so the CUDA path is fully opt-in and the
    default CPU rollout is byte-identical."""
    if device.type == "cpu":
        return batch
    return {k: v.to(device) for k, v in batch.items()}


def build_trivial_env(seed: int = 0, *, weights: RewardWeights | None = None) -> HangarFitEnv:
    """A 1-object, loose-hangar, small-budget env — the easiest curriculum rung.

    seed is accepted for forward-compat (#4b); the trivial env itself is deterministic
    (no RNG), so it is unused here.

    ``weights``: optional reward weights forwarded to the env (defaults to
    ``RewardWeights()`` inside the env when None)."""
    _ = seed  # reserved for #4b object-set sampling; the trivial env has no RNG
    root = Path(__file__).resolve().parent.parent
    fleet = load_fleet(str(root / "data/fleet.yaml"))
    if "fuji" not in fleet:
        raise ValueError(f"build_trivial_env: 'fuji' not in fleet (available: {sorted(fleet)})")
    hangar = replace(load_hangar(str(root / "data/hangar.yaml")), apron_depth_m=8.0)
    return HangarFitEnv(
        hangar=hangar,
        fleet=fleet,
        requested_ids=("fuji",),
        difficulty=_TRIVIAL_DIFFICULTY,
        weights=weights,
    )


def _bodies(env: HangarFitEnv) -> dict:
    return {**env.fleet, **env.ground_objects}


def collect_rollout(
    env: HangarFitEnv,
    policy: HangarFitPolicy,
    encoder: EncoderConfig,
    rollout_len: int,
    *,
    sample_request: Callable[[], EpisodeStart] | None = None,
    pose_cache: bool = True,
) -> tuple[RolloutBuffer, list[EpisodeStat]]:
    """Drive the env single-stream for `rollout_len` steps; return the buffer and the
    per-completed-episode stats (competency + reward sum). On each episode boundary,
    `sample_request()` (when given) returns an EpisodeStart that picks the next episode's
    object subset and optional seed_anchor_k; None keeps the env's fixed requested set
    (the 4a trivial path).

    `pose_cache` (#733, default-on) opens a per-step `pose_cache_scope` spanning the
    encode + env.step so the shapely parts are built once across the encoder and the
    reward oracle. The cache is a byte-identical passthrough, so `pose_cache=False`
    reproduces the un-cached rollout bit-for-bit."""
    buf = RolloutBuffer()
    bodies = _bodies(env)
    device = next(policy.parameters()).device
    obs = env.reset()
    ep_reward, ep_stats = 0.0, []

    def _scope() -> contextlib.AbstractContextManager[None]:
        return pose_cache_scope() if pose_cache else contextlib.nullcontext()

    with torch.no_grad():
        while len(buf) < rollout_len:
            with _scope():
                obs_t = encode(obs, env.hangar, bodies, encoder)
                out = policy(_to_device(to_batch([obs_t]), device))
                kind, mag = sample_action(out)
                logprob, _ = factored_logprob_entropy(out, kind, mag)
                tr = obs.active.body.effective_turn_radius_m()  # type: ignore[union-attr]
                primitive = decode(int(kind), int(mag), turn_radius_m=tr)
                nxt, reward, done, info = env.step(primitive)
            buf.add(
                obs_t,
                kind_idx=int(kind),
                mag_idx=int(mag),
                logprob=float(logprob),
                value=float(out.value),
                reward=float(reward),
                done=bool(done),
            )
            ep_reward += float(reward)
            if done:
                # info.total = len(requested_ids) >= 1, so the division is safe.
                ep_stats.append(
                    EpisodeStat(
                        fraction_placed=info.placed / info.total,
                        valid=info.valid,
                        total_reward=ep_reward,
                    )
                )
                ep_reward = 0.0
                start = sample_request() if sample_request else None
                obs = env.reset(
                    requested_ids=start.requested_ids if start else None,
                    seed_anchor_k=start.seed_anchor_k if start else None,
                )
            else:
                obs = nxt
        # bootstrap value for a non-done tail
        if not buf.done[-1]:
            with _scope():
                tail = encode(obs, env.hangar, bodies, encoder)
                buf.last_value = float(policy(_to_device(to_batch([tail]), device)).value)
    return buf, ep_stats


def collect_rollout_vec(
    vec_env,  # SyncVectorEnv | SubprocVectorEnv (duck-typed: num_envs/reset/step)
    policy: HangarFitPolicy,
    encoder: EncoderConfig,
    rollout_len: int,
    *,
    static_block: np.ndarray,
) -> tuple[VecRolloutBuffer, list[EpisodeStat]]:
    """Drive `vec_env` for `rollout_len` steps (rollout_len * num_envs transitions),
    batching the N observations through one policy forward per step. Returns the
    (T, N) buffer + the per-completed-episode stats (with the per-env reward sum).

    `static_block` (#752) is the rung's cached (STATIC_CHANNELS, H, W) raster block
    (`encoding.static_block(hangar, encoder)`). The vec workers ship only the dynamic
    channels (uint8); this block is re-prepended in `to_batch` (and stored on the buffer for
    the PPO minibatch reassembly) to rehydrate the full 7ch float32 raster byte-identically.
    It is the SAME for every step of a rung (one hangar+config), so the caller computes it
    once per rung."""
    n = vec_env.num_envs
    buf = VecRolloutBuffer(num_envs=n)
    buf.static_block = static_block
    device = next(policy.parameters()).device
    obs = vec_env.reset()
    ep_reward = [0.0] * n
    ep_stats: list[EpisodeStat] = []
    with torch.no_grad():
        for _ in range(rollout_len):
            out = policy(_to_device(to_batch(obs, static_block=static_block), device))
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
                    if s is None:
                        raise RuntimeError(f"env {i} signalled done but ep_stats[{i}] is None")
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
        tail = policy(_to_device(to_batch(obs, static_block=static_block), device))
        buf.last_value = [float(tail.value[i]) for i in range(n)]
    return buf, ep_stats


def _build_stage_worker(
    stage: Stage,
    stage_index: int,
    pool: tuple[str, ...],
    n: int,
    seed: int,
    weights: RewardWeights | None,
    encoder: EncoderConfig,
    worker_index: int,
) -> _EnvWorker:
    """Picklable (module-level, used via ``functools.partial``) per-worker factory for the
    vectorized training path. A CLOSURE is NOT picklable under ``spawn``, so this MUST stay a
    module-level function. Rebuilds the stage env + this worker's seeded episode sampler IN
    the child process; ``worker_index`` gives each worker a distinct ``stage_rng`` stream
    (``worker_index=0`` is the legacy stream)."""
    wenv = build_stage_env(stage, weights=weights)
    wrng = stage_rng(seed, stage_index, worker_index=worker_index)
    wnext = make_episode_sampler(stage, pool, n, wrng)
    return _EnvWorker(wenv, encoder, wnext)


def train(
    *,
    seed: int = 0,
    iterations: int = 50,
    rollout_len: int = 512,
    ppo: PPOConfig | None = None,
    policy_kwargs: dict | None = None,
    encoder: EncoderConfig | None = None,
    weights: RewardWeights | None = None,
    log: bool = False,
    save: str | None = None,
    save_onnx: str | None = None,
    device: str = "cpu",
) -> list[float]:
    """Train on the trivial stage; return the per-iteration mean episode reward.

    ``weights``: optional reward weights forwarded to the env (defaults to neutral).
    ``ppo.entropy_coef_start/end/anneal_iters``: per-iteration entropy schedule; decays
    ONCE over the run's iterations (NO per-stage reset — that is ``train_curriculum()``
    only, where ``it`` resets to 0 at each new stage). Keyed on the iteration index so
    it decays from ``it=0`` at the start of the run.
    ``ppo.normalize_returns``: std-only Welford return normalizer (single run-level
    normalizer; identity until warmed up)."""
    torch.manual_seed(seed)
    cfg = ppo or PPOConfig()
    enc = encoder or EncoderConfig()
    env = build_trivial_env(seed, weights=weights)
    policy = HangarFitPolicy(**(policy_kwargs or {})).to(torch.device(device))
    optimizer = torch.optim.Adam(policy.parameters(), lr=cfg.lr)
    normalizer = ReturnNormalizer(eps=cfg.return_norm_eps) if cfg.normalize_returns else None
    history: list[float] = []
    for it in range(iterations):
        it_cfg = replace(
            cfg,
            entropy_coef=entropy_coef_at(
                it,
                base=cfg.entropy_coef,
                start=cfg.entropy_coef_start,
                end=cfg.entropy_coef_end,
                anneal_iters=cfg.entropy_anneal_iters,
            ),
        )
        buf, ep_stats = collect_rollout(env, policy, enc, rollout_len)
        metrics = ppo_update(policy, optimizer, buf, it_cfg, normalizer=normalizer)
        # NaN (not 0.0) when no episode finished within the rollout, so a short rollout
        # is not mistaken for a genuine zero-reward iteration in the curve.
        mean_r = sum(s.total_reward for s in ep_stats) / len(ep_stats) if ep_stats else float("nan")
        history.append(mean_r)
        if log:
            if ep_stats:
                reward_str = f"mean_ep_reward={mean_r:+.3f}  n_eps={len(ep_stats)}"
            else:
                reward_str = "mean_ep_reward=N/A (0 episodes)"
            print(
                f"iter {it:4d}  {reward_str}  "
                f"loss={metrics['loss']:+.3f}  entropy={metrics['entropy']:.3f}"
            )
    # Move the (possibly CUDA) policy to CPU before persisting: --save writes a state_dict the
    # CPU ml.eval / ONNX consumer loads, and export_onnx traces CPU dummy inputs against it (a
    # CUDA-resident policy would device-mismatch). Training is finished here, so the in-place
    # move is harmless; it is a no-op (byte-identical) for an already-CPU policy.
    if save is not None or save_onnx is not None:
        policy = policy.to("cpu")
    if save is not None:
        torch.save(policy.state_dict(), save)
    if save_onnx is not None:
        export_onnx(policy, save_onnx)
    return history


def _clone_policy(policy: HangarFitPolicy) -> HangarFitPolicy:
    """A frozen deep copy of ``policy`` for the #755 ``--pipeline-update`` rollout.

    The pipelined rollout reads the **pre-update** weights on a background thread
    while ``ppo_update`` mutates the live policy in place on the main thread, so the
    snapshot must be an independent module (params + buffers), not an aliasing view.
    ``deepcopy`` clones onto the same device; the copy is used only for
    ``torch.no_grad()`` forward passes in :func:`collect_rollout_vec`."""
    return copy.deepcopy(policy)


def _rung_stop_reason(
    promo_history: list[float],
    ep_stats: Sequence[EpisodeStat],
    pol: PromotionPolicy,
    auto_budget: BudgetController | None,
) -> str | None:
    """Append this iteration's HONEST per-iteration metric mean to ``promo_history`` — the
    ``window_score`` over the WHOLE rollout's episodes, the same ``valid_placed`` the
    ``--metrics-out`` JSONL and ``ml.gate`` report — then decide whether the rung stops THIS
    iteration. Returns the promotion reason (``"competency"`` | ``"budget-plateau"``) or None
    to keep training. The #742 fix routes BOTH the competency gate (``should_promote``) and the
    #734 auto-budget slope-fit through this single honest series, replacing the old per-episode
    ``deque(maxlen=window)`` tail that each read separately.

    Only an iteration that completed >= 1 episode contributes a score: an empty rollout carries
    no competency signal (matching ``episode_metrics``' None-when-empty convention and
    ``ml.gate``'s None-row filter), so it neither advances the gate nor injects a phantom-flat
    point that could trip the auto-budget plateau detector. Competency wins over a budget
    plateau when both fire in the same iteration."""
    if ep_stats:
        promo_history.append(window_score(ep_stats, pol.metric))
    if should_promote(promo_history, pol):
        return "competency"
    if auto_budget is not None and auto_budget.should_stop(promo_history):
        return "budget-plateau"
    return None


def train_curriculum(
    *,
    seed: int = 0,
    schedule: CurriculumSchedule | None = None,
    rollout_len: int = 512,
    ppo: PPOConfig | None = None,
    policy_kwargs: dict | None = None,
    encoder: EncoderConfig | None = None,
    weights: RewardWeights | None = None,
    log: bool = False,
    save: str | None = None,
    save_onnx: str | None = None,
    n_envs: int = 1,
    vec_backend: Literal["sync", "subproc"] = "subproc",
    vec_start_method: Literal["spawn", "forkserver", "fork"] = "spawn",
    device: str = "cpu",
    load: str | None = None,
    checkpoint_out: str | None = None,
    auto_budget: BudgetController | None = None,
    pipeline_update: bool = False,
) -> CurriculumHistory:
    """Climb the ladder: one policy/optimizer across rungs (transfer); per rung, run
    PPO until the competency gate fires or the per-stage cap is hit, then advance.

    ``auto_budget`` (#734): when given, each rung runs up to the controller's hard ceiling
    and stops early once the promotion metric (default ``valid_placed``) plateaus
    (slope-aware), instead of the fixed ``pol.max_iters`` cap. Default None reproduces the
    fixed-cap path byte-identically.

    ``weights``: optional reward weights forwarded to every stage env (defaults to neutral).
    ``ppo.entropy_coef_start/end/anneal_iters``: per-rung entropy schedule; the iteration
    index resets to 0 at each new stage, so each rung re-warms from the high start.
    ``ppo.normalize_returns``: std-only Welford return normalizer (single run-level
    normalizer shared across all rungs; identity until warmed up).
    ``load``: resume from a #710 checkpoint — restore the policy, Adam optimizer, return
    normalizer, and curriculum position (already-completed rungs are skipped). The
    checkpoint's architecture is authoritative; a conflicting ``policy_kwargs`` raises.
    ``checkpoint_out``: write a resume checkpoint after EACH rung completes, so a long run
    survives a crash. Both default None (no IO) -> the legacy path is byte-identical."""
    torch.manual_seed(seed)
    cfg = ppo or PPOConfig()
    enc = encoder or EncoderConfig()
    sched = schedule or CurriculumSchedule.default()
    # Eager invariant check on the WHOLE ladder before any (expensive) training, so a bad
    # rung (e.g. max_objects > encoder capacity) fails by name now instead of as a deep
    # tensorizer overflow several rungs in.
    validate_ladder(sched.stages, encoder_max_objects=enc.max_objects)
    pol = sched.policy
    dev = torch.device(device)
    # Resume (#710): restore the policy + optimizer + normalizer + curriculum position from a
    # checkpoint, else build them fresh. The default path (load is None) is unchanged and
    # byte-identical. ``saved_policy_kwargs`` is the architecture the checkpoint records, so a
    # per-rung ``checkpoint_out`` write stores the EXACT kwargs the weights were built with.
    completed_stages: list[str] = []
    if load is not None:
        ckpt = load_checkpoint(load)
        if policy_kwargs is not None and dict(policy_kwargs) != dict(ckpt.policy_kwargs):
            raise ValueError(
                f"train_curriculum: --load architecture (policy_kwargs) {ckpt.policy_kwargs} "
                f"!= passed policy_kwargs {policy_kwargs}; resume must reuse the checkpoint's "
                f"architecture (a shape mismatch would break load_state_dict)"
            )
        saved_policy_kwargs = dict(ckpt.policy_kwargs)
        policy = HangarFitPolicy(**saved_policy_kwargs).to(dev)
        policy.load_state_dict(ckpt.policy_state)
        optimizer = torch.optim.Adam(policy.parameters(), lr=cfg.lr)
        # load_state_dict overwrites param_groups (lr included), so resume INHERITS the
        # checkpoint's optimizer hyperparameters — --lr is intentionally not re-applied here.
        optimizer.load_state_dict(ckpt.optimizer_state)
        normalizer = ReturnNormalizer(eps=cfg.return_norm_eps) if cfg.normalize_returns else None
        if normalizer is not None and ckpt.normalizer_state is not None:
            normalizer.load_state_dict(ckpt.normalizer_state)
        completed_stages = list(ckpt.completed_stages)
        # Resume sanity warnings (stderr; the resume path is never the byte-identical default).
        foreign = [s for s in completed_stages if s not in {st.name for st in sched.stages}]
        if foreign:
            print(
                f"warning: --load checkpoint marks rungs {foreign} complete, but they are not "
                f"in the current schedule (resuming a different ladder?)",
                file=sys.stderr,
            )
        if (ckpt.normalizer_state is not None) != cfg.normalize_returns:
            print(
                f"warning: --load checkpoint normalizer presence "
                f"({ckpt.normalizer_state is not None}) disagrees with "
                f"normalize_returns={cfg.normalize_returns}; the saved return-normalizer state "
                f"is dropped / re-initialized mid-run",
                file=sys.stderr,
            )
    else:
        saved_policy_kwargs = dict(policy_kwargs or {})
        policy = HangarFitPolicy(**saved_policy_kwargs).to(dev)
        optimizer = torch.optim.Adam(policy.parameters(), lr=cfg.lr)
        normalizer = ReturnNormalizer(eps=cfg.return_norm_eps) if cfg.normalize_returns else None
    completed_set = set(completed_stages)
    history = CurriculumHistory()
    for stage_index, stage in enumerate(sched.stages):
        if stage.name in completed_set:
            continue  # already fully trained in a prior run (resume) — skip this rung
        env = build_stage_env(stage, weights=weights)
        pool = effective_fleet_ids(stage)
        n = stage.difficulty.max_objects if stage.difficulty.max_objects is not None else len(pool)
        rng = stage_rng(seed, stage_index)
        # promo_history holds ONE honest score per ITERATION — window_score over the WHOLE
        # rollout's episodes (the same valid_placed the JSONL/ml.gate report), NOT the last
        # `pol.window` completed episodes (the #742 fix). should_promote thresholds its windowed
        # mean and the #734 auto-budget controller fits its slope on the SAME series, so the gate
        # and the budget watch one honest trajectory. A rung the transferred policy already
        # masters can promote as soon as `pol.window` consecutive iterations are competent (3 by
        # default) — the curriculum advances on sustained competency, it does not "serve time".
        promo_history: list[float] = []
        # partial binds THIS stage's pool/n/rng by value (so the per-iteration closure
        # is not the flake8-bugbear B023 late-binding trap) and stays mypy-inferrable
        # where a default-arg lambda would not be.
        next_request = make_episode_sampler(stage, pool, n, rng)
        # #734 auto-budget: with a controller, each rung runs up to its hard ceiling and
        # stops early on a valid_placed plateau; without one, the fixed pol.max_iters cap.
        # auto_budget is None (default) => same loop bound + no controller calls.
        ceiling = auto_budget.max_iters if auto_budget is not None else pol.max_iters
        # n_envs == 1: the legacy single-stream path — byte-identical to the pre-#708 loop (the
        # #708 contract); it shares the #742 per-iteration gate with the vectorized path below.
        if n_envs == 1:
            for it in range(ceiling):
                # Per-rung entropy schedule: `it` resets to 0 each stage, so each rung
                # re-warms from entropy_coef_start (the intended high→low warmup per rung).
                it_cfg = replace(
                    cfg,
                    entropy_coef=entropy_coef_at(
                        it,
                        base=cfg.entropy_coef,
                        start=cfg.entropy_coef_start,
                        end=cfg.entropy_coef_end,
                        anneal_iters=cfg.entropy_anneal_iters,
                    ),
                )
                buf, ep_stats = collect_rollout(
                    env, policy, enc, rollout_len, sample_request=next_request
                )
                ppo_update(policy, optimizer, buf, it_cfg, normalizer=normalizer)
                history.record(stage.name, it, ep_stats)
                if log:
                    print(format_iter_log(stage.name, it, ep_stats), flush=True)
                reason = _rung_stop_reason(promo_history, ep_stats, pol, auto_budget)
                if reason is not None:
                    history.note_promotion(stage.name, it, by=reason)
                    break
            else:
                history.note_promotion(stage.name, ceiling - 1, by="cap")
        else:
            from ml.vector_env import SubprocVectorEnv, SyncVectorEnv

            # functools.partial over the MODULE-LEVEL _build_stage_worker is picklable under
            # spawn (a nested closure is not); each partial binds this stage's args + the
            # worker index by value. worker_index gives each worker its own stage_rng stream.
            worker_fns: list[Callable[[], _EnvWorker]] = [
                partial(_build_stage_worker, stage, stage_index, pool, n, seed, weights, enc, wi)
                for wi in range(n_envs)
            ]
            vec_cm: SyncVectorEnv | SubprocVectorEnv
            if vec_backend == "subproc":
                vec_cm = SubprocVectorEnv(worker_fns, start_method=vec_start_method)
            else:
                vec_cm = SyncVectorEnv([fn() for fn in worker_fns])
            # #752: the static raster block (oob/bay/apron/door) depends only on this rung's
            # (hangar, config), so compute it ONCE here; the vec workers ship only the dynamic
            # channels (uint8) and collect_rollout_vec re-prepends this block in to_batch.
            rung_static_block = static_block(env.hangar, enc)
            with vec_cm as vec:

                def _it_cfg(it: int) -> PPOConfig:
                    return replace(
                        cfg,
                        entropy_coef=entropy_coef_at(
                            it,
                            base=cfg.entropy_coef,
                            start=cfg.entropy_coef_start,
                            end=cfg.entropy_coef_end,
                            anneal_iters=cfg.entropy_anneal_iters,
                        ),
                    )

                if pipeline_update:
                    # #755 (Wave 4, opt-in, NON-deterministic): overlap the next
                    # rollout — collected under a frozen snapshot of the PRE-update
                    # policy, exactly one iteration stale — with this iteration's
                    # ppo_update. The rollout runs on a single background thread while
                    # the live policy is updated on the main thread; they share no
                    # mutable state (separate module objects; the env is touched only
                    # by the rollout thread, serialized across iterations by
                    # future.result()). The next rollout is launched ONLY when another
                    # iteration will consume it (never on the stop / final iteration),
                    # so no speculative rollout is ever wasted and there is nothing to
                    # drain. Re-gated on a two-seed ml.gate valid_placed delta, NOT a
                    # byte-diff (the staleness perturbs the learning curve).
                    #
                    # Non-determinism is two-fold and DELIBERATE: (1) the one-iteration
                    # stale policy snapshot, and (2) the background rollout's action
                    # sampling shares the GLOBAL torch RNG with the main thread's
                    # ppo_update (minibatch shuffle), so the two threads' draws
                    # interleave by timing. torch's generator is mutex-guarded, so this
                    # is a SAFE race (no state corruption) — but the run is not
                    # reproducible, which is exactly why this path is gated on a
                    # learning-metric delta rather than byte-identity.
                    executor = ThreadPoolExecutor(max_workers=1)
                    pending: Future[tuple[VecRolloutBuffer, list[EpisodeStat]]] | None = None
                    try:
                        for it in range(ceiling):
                            if pending is None:
                                # Iteration 0 primes on-policy under the current weights.
                                buf_vec, ep_stats = collect_rollout_vec(
                                    vec, policy, enc, rollout_len, static_block=rung_static_block
                                )
                            else:
                                buf_vec, ep_stats = pending.result()
                                pending = None
                            history.record(stage.name, it, ep_stats)
                            # Evaluate the stop gate BEFORE launching the next rollout (and
                            # before this update) so a stop suppresses the otherwise-wasted
                            # speculative rollout. Equivalent to the sequential branch's
                            # post-update gate: the decision reads only this rollout's
                            # ep_stats + promo_history, never this iteration's ppo_update —
                            # keep that true if _rung_stop_reason ever grows new inputs.
                            reason = _rung_stop_reason(promo_history, ep_stats, pol, auto_budget)
                            if reason is None and it < ceiling - 1:
                                snapshot = _clone_policy(policy)
                                pending = executor.submit(
                                    collect_rollout_vec,
                                    vec,
                                    snapshot,
                                    enc,
                                    rollout_len,
                                    static_block=rung_static_block,
                                )
                            ppo_update(
                                policy, optimizer, buf_vec, _it_cfg(it), normalizer=normalizer
                            )
                            if log:
                                print(format_iter_log(stage.name, it, ep_stats), flush=True)
                            if reason is not None:
                                history.note_promotion(stage.name, it, by=reason)
                                break
                        else:
                            history.note_promotion(stage.name, ceiling - 1, by="cap")
                    finally:
                        # On NORMAL exit (break/cap) pending is None — the next rollout
                        # is never launched on the stop/last iteration. On an EXCEPTIONAL
                        # exit (e.g. ppo_update raised mid-iteration) pending may still be
                        # in flight: drain it so a concurrent rollout failure surfaces on
                        # stderr instead of being silently dropped by shutdown() (which
                        # joins the thread but never retrieves the future's result). We
                        # print rather than re-raise so the primary exception that
                        # triggered this unwind still propagates. shutdown(wait=True) then
                        # blocks until the rollout finishes stepping `vec`, so the env is
                        # never torn down under an in-flight step.
                        if pending is not None:
                            try:
                                pending.result()
                            except Exception:
                                traceback.print_exc(file=sys.stderr)
                        executor.shutdown(wait=True)
                else:
                    for it in range(ceiling):
                        it_cfg = _it_cfg(it)
                        buf_vec, ep_stats = collect_rollout_vec(
                            vec, policy, enc, rollout_len, static_block=rung_static_block
                        )
                        ppo_update(policy, optimizer, buf_vec, it_cfg, normalizer=normalizer)
                        history.record(stage.name, it, ep_stats)
                        if log:
                            print(format_iter_log(stage.name, it, ep_stats), flush=True)
                        reason = _rung_stop_reason(promo_history, ep_stats, pol, auto_budget)
                        if reason is not None:
                            history.note_promotion(stage.name, it, by=reason)
                            break
                    else:
                        history.note_promotion(stage.name, ceiling - 1, by="cap")
        if log:
            by = history.promotions[-1][2]
            print(f"[{stage.name}] promoted by {by}")
        # Mark this rung done and (if requested) checkpoint, so a crash resumes at the next
        # rung with the policy/optimizer/normalizer this rung produced. No-op when
        # checkpoint_out is None -> the default path stays byte-identical.
        completed_stages.append(stage.name)
        completed_set.add(stage.name)
        if checkpoint_out is not None:
            save_checkpoint(
                checkpoint_out,
                policy=policy,
                optimizer=optimizer,
                normalizer=normalizer,
                policy_kwargs=saved_policy_kwargs,
                completed_stages=completed_stages,
            )
    # Move the (possibly CUDA) policy to CPU before persisting: --save writes a state_dict the
    # CPU ml.eval / ONNX consumer loads, and export_onnx traces CPU dummy inputs against it (a
    # CUDA-resident policy would device-mismatch). Training is finished here, so the in-place
    # move is harmless; it is a no-op (byte-identical) for an already-CPU policy.
    if save is not None or save_onnx is not None:
        policy = policy.to("cpu")
    if save is not None:
        torch.save(policy.state_dict(), save)
    if save_onnx is not None:
        export_onnx(policy, save_onnx)
    return history


# --n-envs auto (#747): pack one spawn worker per idle core, RAM permitting. The cap
# constants are deliberately conservative — auto picking too FEW workers wastes cores;
# picking too many risks OOM (the issue's named risk), so the gate leans toward fewer.
# Per-worker RAM is grounded in the measured ~0.5 GiB incremental PSS per worker (16-worker
# trio-box run, see the throughput diagnosis) carried at ~2x for headroom; #751 (forkserver)
# will later shrink it. These are not user knobs yet — promote to flags if a box needs it.
_AUTO_RESERVED_CORES = 2  # learner forward/backward + the GPU-feed / main loop
_AUTO_PER_WORKER_GIB = 1.0  # ~2x the measured ~0.5 GiB incremental PSS per spawn worker
_AUTO_RAM_HEADROOM_GIB = 2.0  # leave room for the parent (CUDA context, larger batches) to grow


def _available_cores() -> int:
    """Schedulable core count. ``sched_getaffinity`` honours cgroup / ``taskset`` pinning
    (so WSL2 / container limits are respected); ``os.cpu_count`` overcounts those. Falls
    back to ``cpu_count`` only where affinity is unavailable (non-Linux)."""
    getaffinity = getattr(os, "sched_getaffinity", None)
    if getaffinity is not None:
        return len(getaffinity(0))
    return os.cpu_count() or 1


def _available_gib() -> float | None:
    """Allocatable RAM in GiB from ``/proc/meminfo`` ``MemAvailable`` (the kernel's honest
    "without swapping" figure), or ``None`` where it cannot be read (non-Linux / sandbox).
    ``None`` makes ``--n-envs auto`` fall back to a cores-only bound."""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024 * 1024)  # kB -> GiB
    except (OSError, ValueError, IndexError):
        return None
    return None


def resolve_n_envs(value: str) -> int:
    """argparse ``type`` for ``--n-envs``: an explicit positive int passes through; the
    literal ``auto`` resolves to
    ``max(1, min(cores - reserved, (MemAvailable_gib - headroom) // per_worker))`` using
    :func:`_available_cores` and :func:`_available_gib`. The floor of 1 keeps the
    byte-identity reference (``--n-envs 1``) reachable even on a 1-core / tiny-RAM box.
    Raises :class:`argparse.ArgumentTypeError` (-> a clean parser error) on a non-positive
    or non-integer value."""
    if value == "auto":
        core_cap = max(1, _available_cores() - _AUTO_RESERVED_CORES)
        gib = _available_gib()
        if gib is None:
            return core_cap
        ram_cap = max(1, int((gib - _AUTO_RAM_HEADROOM_GIB) / _AUTO_PER_WORKER_GIB))
        return max(1, min(core_cap, ram_cap))
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--n-envs must be a positive integer or 'auto', got {value!r}"
        ) from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"--n-envs must be >= 1, got {n}")
    return n


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Train the cold-joint policy (trivial stage or curriculum)."
    )
    p.add_argument("--schedule", choices=["trivial", "curriculum"], default="curriculum")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--iterations", type=int, default=200, help="trivial: PPO iters")
    p.add_argument(
        "--max-iters-per-stage",
        type=int,
        default=None,
        help="curriculum: per-rung safety cap (default = schedule policy)",
    )
    p.add_argument(
        "--auto-budget",
        action="store_true",
        help="curriculum: slope-aware per-rung budget (#734) — keep training while the "
        "promotion metric (default valid_placed) climbs, stop early on plateau, up to "
        "--auto-budget-max-iters. Replaces the fixed --max-iters-per-stage cap; default off "
        "(fixed cap, byte-identical).",
    )
    p.add_argument(
        "--auto-budget-max-iters",
        type=int,
        default=None,
        help="curriculum: hard ceiling for --auto-budget (default = BudgetController default, "
        "1000). Ignored without --auto-budget.",
    )
    p.add_argument(
        "--auto-budget-min-iters",
        type=int,
        default=None,
        help="curriculum: --auto-budget no-stop floor — no plateau-stop decision before this many "
        "iterations (default = BudgetController default, 30). Raise it for a hard rung with a long "
        "pre-climb warmup. Requires --auto-budget.",
    )
    p.add_argument(
        "--auto-budget-min-level",
        type=float,
        default=None,
        help="curriculum: --auto-budget floor-guard (#743) — the recent metric level must clear "
        "this before any plateau-stop, so a flat-at-floor WARMUP is not mistaken for convergence "
        "(default = BudgetController default, 0.05; 0 disables the guard). Requires --auto-budget.",
    )
    p.add_argument(
        "--promotion-metric",
        choices=["fraction_placed", "valid_rate", "valid_placed"],
        default=None,
        help="curriculum: PromotionPolicy.metric override (default = schedule policy, "
        "valid_placed); use valid_rate to advance easy rungs while valid_placed is still 0",
    )
    p.add_argument(
        "--promotion-threshold",
        type=float,
        default=None,
        help="curriculum: PromotionPolicy.threshold override in [0,1] "
        "(default = schedule policy, 0.9); lower it so the easy rungs reveal the ladder",
    )
    p.add_argument(
        "--promotion-window",
        type=int,
        default=None,
        help="curriculum: PromotionPolicy.window override — recent ITERATIONS to average for the "
        "competency gate (default = schedule policy, 3). Each iteration contributes its honest "
        "full-rollout valid_placed mean (the #742 fix), NOT a per-episode tail; raise it to "
        "require sustained competency over more iterations before promoting.",
    )
    p.add_argument(
        "--metrics-out",
        type=str,
        default=None,
        help="curriculum: write per-iter per-rung metrics JSONL "
        "(stage/iter/n_eps/mean_ep_reward/fraction_placed/valid_rate/valid_placed) to this "
        "path — the #710 valid_placed learning curves",
    )
    p.add_argument("--rollout-len", type=int, default=1024)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument(
        "--d-model",
        type=int,
        default=None,
        help="policy embedding dim (None = HangarFitPolicy default, 128)",
    )
    p.add_argument(
        "--n-layers",
        type=int,
        default=None,
        help="transformer encoder layers (None = HangarFitPolicy default, 2)",
    )
    p.add_argument(
        "--n-heads",
        type=int,
        default=None,
        help="attention heads (None = HangarFitPolicy default, 4)",
    )
    p.add_argument(
        "--spatial-tokens",
        action="store_true",
        help="opt-in spatial-token cross-attention policy (#809): object tokens attend to "
        "free-space cells instead of one global-pool summary. Default off = byte-identical net "
        "(a deliberate new-architecture re-baseline when on; the flag is persisted in the "
        "checkpoint's policy_kwargs)",
    )
    p.add_argument(
        "--epochs",
        type=int,
        default=PPOConfig().epochs,
        help="PPO epochs per update (default = PPOConfig.epochs)",
    )
    p.add_argument(
        "--minibatch-size",
        type=int,
        default=PPOConfig().minibatch_size,
        help="PPO minibatch size (default = PPOConfig.minibatch_size)",
    )
    p.add_argument(
        "--save", type=str, default=None, help="write the trained policy state_dict to this path"
    )
    p.add_argument(
        "--save-onnx",
        type=str,
        default=None,
        help="also export the trained policy forward to this ONNX path (inference)",
    )
    p.add_argument(
        "--load",
        type=str,
        default=None,
        help="curriculum: resume from a #710 checkpoint (policy + optimizer + normalizer + "
        "completed rungs); reuses the checkpoint's architecture",
    )
    p.add_argument(
        "--checkpoint-out",
        type=str,
        default=None,
        help="curriculum: write a resume checkpoint after each rung (crash-survivable run); "
        "pair with --load PATH to resume the same path",
    )
    p.add_argument(
        "--solo-box-rung",
        action="store_true",
        help="curriculum: insert the opt-in #714 'solo-box' rung (1 object, whole fleet) after "
        "trivial, so single-object competency transfers before the 2-object jump",
    )
    p.add_argument(
        "--seed-anchor",
        action="store_true",
        help="curriculum: insert the opt-in #712 'pair-anchored' rung before pair-box (1 object "
        "pre-parked at a committed-witness pose, the other driven in), scaffolding 2-object "
        "joint discovery before the empty-start pair-box",
    )
    p.add_argument(
        "--mixed-anchor",
        action="store_true",
        help="curriculum: insert the opt-in #712 'pair-mixed' rung before pair-box (each "
        "episode randomly starts anchored k=1 or empty k=0 by a fixed probability), keeping "
        "empty-start episodes in the training mix so the policy does not collapse to "
        "place-nothing. Apply with --seed-anchor so pair-mixed lands between pair-anchored "
        "and pair-box.",
    )
    p.add_argument(
        "--anchor-trio-notch",
        action="store_true",
        help="curriculum: insert the opt-in #736 'trio-notch-anchored' rung before trio-notch "
        "(1 of 3 notch-witness objects pre-parked at a committed pose, the other two driven "
        "in), scaffolding 3-object joint discovery on the real notch hangar to break the "
        "cold-start coverage minimum (place one, abandon two) before the empty-start trio-notch",
    )
    p.add_argument(
        "--stop-after-rung",
        type=str,
        default=None,
        help="curriculum: truncate the ladder after this rung (the named rung is the last "
        "trained), dropping every rung after it. Default = run the whole ladder "
        "(byte-identical). The #722 sweep lever: stop after pair-box so a resumed cell does "
        "not grind on into trio-*. Applied after the --solo-box-rung/--seed-anchor/"
        "--mixed-anchor grafts, so a name they introduce (pair-mixed) is valid.",
    )
    p.add_argument(
        "--r-valid-park",
        type=float,
        default=0.0,
        help="bonus per Park action when the full layout is valid (basin-escape shaping)",
    )
    p.add_argument(
        "--r-unplaced-penalty",
        type=float,
        default=0.0,
        help="terminal penalty per UNPLACED fraction (charges abandonment so driving to "
        "budget exhaustion is no longer free vs committing a Park; #710 economics rebalance)",
    )
    p.add_argument(
        "--w-col",
        type=float,
        default=RewardWeights().w_col,
        help="collision-overlap penalty weight (default 100.0). Lower it to shrink the unbounded "
        "-w_col spike that makes attempting a Park dominate place-nothing; #720 L5 economics",
    )
    p.add_argument(
        "--valid-park-grade-scale",
        type=float,
        default=0.0,
        help="when >0, GRADE the r_valid_park bonus by near-miss misfit "
        "(r_valid_park*exp(-misfit/scale)) so a Park landing CLOSE to valid earns partial credit "
        "— the uphill gradient into the witness slot; 0 = binary bonus (byte-identical); #720 L5",
    )
    p.add_argument(
        "--r-first-valid",
        type=float,
        default=0.0,
        help="one-time bonus the first time an episode reaches a valid placement (breakthrough "
        "off the place-nothing pole); paid once per episode, 0 = off (byte-identical); #720 L5",
    )
    p.add_argument(
        "--r-valid-progress",
        type=float,
        default=0.0,
        help="banked per-valid-Park credit scaled by the marginal valid-object count beyond the "
        "freebie (r_valid_progress*max(0, n-1) on a Park where the whole layout is valid); 0 = off "
        "(byte-identical); the #812 per-commitment economics lever",
    )
    p.add_argument(
        "--validity-conditional-terminal",
        action="store_true",
        help="terminal credits the VALID placed fraction (invalid layout -> 0), so an "
        "overlapping pile no longer books +r_terminal; #714 multi-object commit-invalidly fix",
    )
    p.add_argument(
        "--dense-slot-potential",
        action="store_true",
        help="add in-hangar nearest-free-pocket shaping term",
    )
    p.add_argument(
        "--entropy-start",
        type=float,
        default=None,
        help="entropy coef anneal start (high→low per rung); None = fixed entropy_coef",
    )
    p.add_argument(
        "--entropy-end",
        type=float,
        default=None,
        help="entropy coef anneal end value (consulted only when --entropy-start is set)",
    )
    p.add_argument(
        "--entropy-anneal-iters",
        type=int,
        default=0,
        help="number of iterations over which to anneal entropy_coef (0 = no schedule)",
    )
    p.add_argument(
        "--normalize-returns",
        action="store_true",
        help="std-only Welford return normalization before GAE",
    )
    # #720 L4 trust-region bundle, default-ON since #728 (the two-seed-validated config). Each
    # value flag is paired with a --no-* off-switch sharing its dest; the value flag is defined
    # FIRST so argparse's first-default-wins rule makes the bundle value the namespace default,
    # and last-on-the-CLI-wins lets a later --no-* (or explicit value) override. The off-switch is
    # the only way to reach the disabled (None) behavior — there is no in-band "off" value
    # (--reward-clip 0 zeroes all rewards; --target-kl 0 stops after the first epoch).
    p.add_argument(
        "--reward-clip",
        type=float,
        default=50.0,
        help="clamp RAW rewards to [-c, c] before normalize/GAE (tames the -w_col collision "
        "spike that drove the gate sawtooth); default 50 (#720/#728); --no-reward-clip disables",
    )
    p.add_argument(
        "--no-reward-clip",
        action="store_const",
        const=None,
        dest="reward_clip",
        help="disable L4 reward clipping (reward_clip=None) — for the clip-OFF A/B control",
    )
    p.add_argument(
        "--value-clip-eps",
        type=float,
        default=0.2,
        help="PPO2 clipped value loss epsilon — caps how far one update moves the critic; "
        "default 0.2 (#720/#728); --no-value-clip-eps disables (plain MSE)",
    )
    p.add_argument(
        "--no-value-clip-eps",
        action="store_const",
        const=None,
        dest="value_clip_eps",
        help="disable L4 clipped value loss (value_clip_eps=None, plain MSE)",
    )
    p.add_argument(
        "--target-kl",
        type=float,
        default=0.03,
        help="early-stop the PPO epoch loop once a full epoch's mean approx-KL exceeds this "
        "(per-update trust region); default 0.03 (#720/#728); --no-target-kl disables (run all "
        "epochs). Note 0.0 is NOT 'off' — it stops after the first epoch (mean-KL > 0)",
    )
    p.add_argument(
        "--no-target-kl",
        action="store_const",
        const=None,
        dest="target_kl",
        help="disable L4 target-KL early-stop (target_kl=None) — run all PPO epochs",
    )
    p.add_argument(
        "--n-envs",
        type=resolve_n_envs,
        default=1,
        help="number of parallel envs: an integer, or 'auto' to fill idle cores "
        "(sched_getaffinity - reserved, RAM-capped). 1 = legacy single-stream, byte-identical",
    )
    p.add_argument(
        "--vec-backend",
        choices=["sync", "subproc"],
        default="subproc",
        help="vectorized env backend: sync (in-process, CI-safe) or subproc (parallel workers)",
    )
    p.add_argument(
        "--vec-start-method",
        choices=["spawn", "forkserver", "fork"],
        default="spawn",
        help="subproc worker start method (#751): spawn (default, byte-identical) or "
        "forkserver (forks from a shared preloaded server, cutting per-worker RAM). fork is "
        "an explicit escape hatch — unsafe if the parent holds a CUDA context.",
    )
    p.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="compute device: cpu (default — deterministic / byte-identical) or cuda "
        "(opt-in GPU fast path; ~5-6x on the PPO update, non-deterministic)",
    )
    p.add_argument(
        "--pipeline-update",
        action="store_true",
        help="(#755, opt-in, vectorized path only) overlap the next rollout — under a "
        "frozen snapshot of the pre-update policy, one iteration stale — with this "
        "iteration's ppo_update, recovering the GPU/worker idle during the non-overlapping "
        "phases. Default off = byte-identical sequential training. On is NON-deterministic "
        "(the staleness perturbs the learning curve) and must be re-gated on a two-seed "
        "ml.gate valid_placed delta. No effect with --schedule trivial or --n-envs 1.",
    )
    return p


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_argparser()
    args = parser.parse_args(argv)
    if args.device == "cuda" and not torch.cuda.is_available():
        parser.error("--device cuda requested but torch.cuda.is_available() is False")
    weights = RewardWeights(
        w_col=args.w_col,
        r_valid_park=args.r_valid_park,
        valid_park_grade_scale=args.valid_park_grade_scale,
        r_first_valid=args.r_first_valid,
        r_valid_progress=args.r_valid_progress,
        dense_slot_potential=args.dense_slot_potential,
        r_unplaced_penalty=args.r_unplaced_penalty,
        validity_conditional_terminal=args.validity_conditional_terminal,
    )
    # Only the supplied arch flags go into policy_kwargs; an all-None set yields None, so
    # the policy falls back to HangarFitPolicy's own defaults (default-neutral / byte-identical).
    policy_kwargs = {
        k: v
        for k, v in (
            ("d_model", args.d_model),
            ("n_layers", args.n_layers),
            ("n_heads", args.n_heads),
            ("spatial_tokens", True if args.spatial_tokens else None),
        )
        if v is not None
    } or None
    ppo_cfg = PPOConfig(
        lr=args.lr,
        epochs=args.epochs,
        minibatch_size=args.minibatch_size,
        entropy_coef_start=args.entropy_start,
        entropy_coef_end=args.entropy_end,
        entropy_anneal_iters=args.entropy_anneal_iters,
        normalize_returns=args.normalize_returns,
        reward_clip=args.reward_clip,
        value_clip_eps=args.value_clip_eps,
        target_kl=args.target_kl,
    )
    if args.schedule == "trivial":
        # --metrics-out / --promotion-* are curriculum-only; the trivial path has no
        # CurriculumHistory and no PromotionPolicy. Fail LOUD (not silent-ignore) so a
        # misdirected sweep flag is caught before the run, not after.
        if args.metrics_out is not None:
            parser.error("--metrics-out requires --schedule curriculum")
        if args.promotion_metric is not None or args.promotion_threshold is not None:
            parser.error("--promotion-metric/--promotion-threshold require --schedule curriculum")
        if args.load is not None or args.checkpoint_out is not None:
            parser.error("--load/--checkpoint-out require --schedule curriculum")
        if args.solo_box_rung:
            parser.error("--solo-box-rung requires --schedule curriculum")
        if args.seed_anchor:
            parser.error("--seed-anchor requires --schedule curriculum")
        if args.mixed_anchor:
            parser.error("--mixed-anchor requires --schedule curriculum")
        if args.anchor_trio_notch:
            parser.error("--anchor-trio-notch requires --schedule curriculum")
        if args.stop_after_rung is not None:
            parser.error("--stop-after-rung requires --schedule curriculum")
        if args.auto_budget or args.auto_budget_max_iters is not None:
            parser.error("--auto-budget/--auto-budget-max-iters require --schedule curriculum")
        if args.n_envs > 1:  # resolve_n_envs floors at 1, so >1 is the only over-floor case
            # --n-envs (and 'auto') only feed the vectorized curriculum path; train() is
            # single-env. Fail LOUD so `--n-envs auto --schedule trivial` is not a silent no-op.
            parser.error("--n-envs > 1 (or 'auto') requires --schedule curriculum")
        train(
            seed=args.seed,
            iterations=args.iterations,
            rollout_len=args.rollout_len,
            ppo=ppo_cfg,
            policy_kwargs=policy_kwargs,
            weights=weights,
            log=True,
            save=args.save,
            save_onnx=args.save_onnx,
            device=args.device,
        )
    else:
        sched = CurriculumSchedule.default()
        sched = replace(
            sched,
            policy=with_promotion_overrides(
                sched.policy,
                metric=args.promotion_metric,
                threshold=args.promotion_threshold,
                max_iters=args.max_iters_per_stage,
                window=args.promotion_window,
            ),
        )
        if args.solo_box_rung:
            sched = with_solo_box_rung(sched)
        if args.seed_anchor:
            sched = with_pair_anchored_rung(sched)
        if args.mixed_anchor:
            sched = with_mixed_anchor_rung(sched)
        if args.anchor_trio_notch:
            sched = with_trio_notch_anchored_rung(sched)
        # Truncate LAST, after the grafts, so a name they introduce (pair-mixed) is in scope.
        if args.stop_after_rung is not None:
            sched = truncate_after_rung(sched, args.stop_after_rung)
        # #734/#743: build the slope-aware controller only when --auto-budget is set; None keeps
        # the fixed per-rung cap (byte-identical default). Each per-knob override (max-iters /
        # min-iters / min-level) is inert without the switch, so fail LOUD rather than silently
        # dropping a typed numeric flag. Unsupplied knobs fall back to the BudgetController default.
        # (field, user-facing flag, value) — the flag string is explicit, not derived from the
        # field name, so a future knob whose flag diverges from its dataclass field can't desync.
        budget_specs = (
            ("max_iters", "--auto-budget-max-iters", args.auto_budget_max_iters),
            ("min_iters", "--auto-budget-min-iters", args.auto_budget_min_iters),
            ("min_level", "--auto-budget-min-level", args.auto_budget_min_level),
        )
        budget_overrides = {field: v for field, _flag, v in budget_specs if v is not None}
        supplied_flags = [flag for _field, flag, v in budget_specs if v is not None]
        if supplied_flags and not args.auto_budget:
            verb = "requires" if len(supplied_flags) == 1 else "require"
            parser.error(f"{', '.join(supplied_flags)} {verb} --auto-budget")
        budget = BudgetController(**budget_overrides) if args.auto_budget else None
        if args.n_envs > 1:
            # Surface the effective parallelism + the box it was sized against, so an
            # `auto` (or an explicit) value is visible in the run log rather than implicit.
            gib = _available_gib()
            mem = f"{gib:.1f} GiB" if gib is not None else "unknown"
            print(
                f"note: training with {args.n_envs} parallel envs "
                f"(schedulable cores={_available_cores()}, MemAvailable={mem})",
                file=sys.stderr,
            )
        history = train_curriculum(
            seed=args.seed,
            schedule=sched,
            rollout_len=args.rollout_len,
            ppo=ppo_cfg,
            policy_kwargs=policy_kwargs,
            weights=weights,
            log=True,
            save=args.save,
            save_onnx=args.save_onnx,
            n_envs=args.n_envs,
            vec_backend=args.vec_backend,
            vec_start_method=args.vec_start_method,
            device=args.device,
            load=args.load,
            checkpoint_out=args.checkpoint_out,
            auto_budget=budget,
            pipeline_update=args.pipeline_update,
        )
        if args.metrics_out is not None:
            records = history_metric_records(history)
            Path(args.metrics_out).write_text(
                "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
            )


if __name__ == "__main__":
    main()
