"""python -m ml.train — train HangarFitPolicy on the fixed trivial curriculum stage
via the roll-your-own PPO in ml.ppo. Sub-project #4a (#607). Requires the [train] extra.

The trivial stage: a single object driven in from the apron and parked in a loose
hangar within a small step budget — the easiest curriculum rung. Curriculum ramping is
sub-project #4b; the reach-not-beat benchmark is #4c."""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

import torch

from hangarfit.loader import load_fleet, load_hangar
from ml.action_space import decode
from ml.curriculum import (
    CurriculumHistory,
    CurriculumSchedule,
    EpisodeStat,
    sample_request,
    should_promote,
    stage_rng,
    validate_ladder,
)
from ml.encoding import EncoderConfig, encode
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
from ml.vector_env import VecStep

_TRIVIAL_DIFFICULTY = DifficultyConfig(
    max_objects=1, per_object_step_budget=40, total_step_budget=40
)


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
    sample_request: Callable[[], tuple[str, ...]] | None = None,
) -> tuple[RolloutBuffer, list[EpisodeStat]]:
    """Drive the env single-stream for `rollout_len` steps; return the buffer and the
    per-completed-episode stats (competency + reward sum). On each episode boundary,
    `sample_request()` (when given) picks the next episode's object subset; None keeps
    the env's fixed requested set (the 4a trivial path)."""
    buf = RolloutBuffer()
    bodies = _bodies(env)
    obs = env.reset()
    ep_reward, ep_stats = 0.0, []
    with torch.no_grad():
        while len(buf) < rollout_len:
            obs_t = encode(obs, env.hangar, bodies, encoder)
            out = policy(to_batch([obs_t]))
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
                obs = env.reset(requested_ids=sample_request() if sample_request else None)
            else:
                obs = nxt
        # bootstrap value for a non-done tail
        if not buf.done[-1]:
            tail = encode(obs, env.hangar, bodies, encoder)
            buf.last_value = float(policy(to_batch([tail])).value)
    return buf, ep_stats


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
    policy = HangarFitPolicy(**(policy_kwargs or {}))
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
    if save is not None:
        torch.save(policy.state_dict(), save)
    if save_onnx is not None:
        export_onnx(policy, save_onnx)
    return history


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
) -> CurriculumHistory:
    """Climb the ladder: one policy/optimizer across rungs (transfer); per rung, run
    PPO until the competency gate fires or the per-stage cap is hit, then advance.

    ``weights``: optional reward weights forwarded to every stage env (defaults to neutral).
    ``ppo.entropy_coef_start/end/anneal_iters``: per-rung entropy schedule; the iteration
    index resets to 0 at each new stage, so each rung re-warms from the high start.
    ``ppo.normalize_returns``: std-only Welford return normalizer (single run-level
    normalizer shared across all rungs; identity until warmed up)."""
    torch.manual_seed(seed)
    cfg = ppo or PPOConfig()
    enc = encoder or EncoderConfig()
    sched = schedule or CurriculumSchedule.default()
    # Eager invariant check on the WHOLE ladder before any (expensive) training, so a bad
    # rung (e.g. max_objects > encoder capacity) fails by name now instead of as a deep
    # tensorizer overflow several rungs in.
    validate_ladder(sched.stages, encoder_max_objects=enc.max_objects)
    pol = sched.policy
    policy = HangarFitPolicy(**(policy_kwargs or {}))
    optimizer = torch.optim.Adam(policy.parameters(), lr=cfg.lr)
    normalizer = ReturnNormalizer(eps=cfg.return_norm_eps) if cfg.normalize_returns else None
    history = CurriculumHistory()
    for stage_index, stage in enumerate(sched.stages):
        env = build_stage_env(stage, weights=weights)
        pool = effective_fleet_ids(stage)
        n = stage.difficulty.max_objects if stage.difficulty.max_objects is not None else len(pool)
        rng = stage_rng(seed, stage_index)
        # The window holds the last `pol.window` COMPLETED EPISODES (not iterations): one
        # rollout can complete many episodes, so a rung the transferred policy already
        # masters can legitimately promote on its first iteration. That is intended — the
        # curriculum advances as soon as competent, it does not "serve time" per rung.
        window: deque[EpisodeStat] = deque(maxlen=pol.window)
        # partial binds THIS stage's pool/n/rng by value (so the per-iteration closure
        # is not the flake8-bugbear B023 late-binding trap) and stays mypy-inferrable
        # where a default-arg lambda would not be.
        next_request = partial(sample_request, pool, n, rng)
        for it in range(pol.max_iters):
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
            window.extend(ep_stats)
            history.record(stage.name, it, ep_stats)
            if log:
                mean_r = (
                    sum(s.total_reward for s in ep_stats) / len(ep_stats)
                    if ep_stats
                    else float("nan")
                )
                print(
                    f"[{stage.name}] iter {it:4d}  mean_ep_reward={mean_r:+.3f}  "
                    f"n_eps={len(ep_stats)}"
                )
            if should_promote(list(window), pol):
                history.note_promotion(stage.name, it, by="competency")
                break
        else:
            history.note_promotion(stage.name, pol.max_iters - 1, by="cap")
        if log:
            by = history.promotions[-1][2]
            print(f"[{stage.name}] promoted by {by}")
    if save is not None:
        torch.save(policy.state_dict(), save)
    if save_onnx is not None:
        export_onnx(policy, save_onnx)
    return history


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
    p.add_argument("--rollout-len", type=int, default=1024)
    p.add_argument("--lr", type=float, default=3e-4)
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
        "--r-valid-park",
        type=float,
        default=0.0,
        help="bonus per Park action when the full layout is valid (basin-escape shaping)",
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
    return p


def main() -> None:
    args = build_argparser().parse_args()
    weights = RewardWeights(
        r_valid_park=args.r_valid_park,
        dense_slot_potential=args.dense_slot_potential,
    )
    ppo_cfg = PPOConfig(
        lr=args.lr,
        entropy_coef_start=args.entropy_start,
        entropy_coef_end=args.entropy_end,
        entropy_anneal_iters=args.entropy_anneal_iters,
        normalize_returns=args.normalize_returns,
    )
    if args.schedule == "trivial":
        train(
            seed=args.seed,
            iterations=args.iterations,
            rollout_len=args.rollout_len,
            ppo=ppo_cfg,
            weights=weights,
            log=True,
            save=args.save,
            save_onnx=args.save_onnx,
        )
    else:
        sched = CurriculumSchedule.default()
        if args.max_iters_per_stage is not None:
            sched = replace(sched, policy=replace(sched.policy, max_iters=args.max_iters_per_stage))
        train_curriculum(
            seed=args.seed,
            schedule=sched,
            rollout_len=args.rollout_len,
            ppo=ppo_cfg,
            weights=weights,
            log=True,
            save=args.save,
            save_onnx=args.save_onnx,
        )


if __name__ == "__main__":
    main()
