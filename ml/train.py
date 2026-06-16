"""python -m ml.train — train HangarFitPolicy on the fixed trivial curriculum stage
via the roll-your-own PPO in ml.ppo. Sub-project #4a (#607). Requires the [train] extra.

The trivial stage: a single object driven in from the apron and parked in a loose
hangar within a small step budget — the easiest curriculum rung. Curriculum ramping is
sub-project #4b; the reach-not-beat benchmark is #4c."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import torch

from hangarfit.loader import load_fleet, load_hangar
from ml.action_space import decode
from ml.curriculum import EpisodeStat
from ml.encoding import EncoderConfig, encode
from ml.env import HangarFitEnv
from ml.policy import HangarFitPolicy, to_batch
from ml.ppo import PPOConfig, RolloutBuffer, factored_logprob_entropy, ppo_update, sample_action
from ml.types import DifficultyConfig

_TRIVIAL_DIFFICULTY = DifficultyConfig(
    max_objects=1, per_object_step_budget=40, total_step_budget=40
)


def build_trivial_env(seed: int = 0) -> HangarFitEnv:
    """A 1-object, loose-hangar, small-budget env — the easiest curriculum rung.

    seed is accepted for forward-compat (#4b); the trivial env itself is deterministic
    (no RNG), so it is unused here.
    """
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


def train(
    *,
    seed: int = 0,
    iterations: int = 50,
    rollout_len: int = 512,
    ppo: PPOConfig | None = None,
    policy_kwargs: dict | None = None,
    encoder: EncoderConfig | None = None,
    log: bool = False,
) -> list[float]:
    """Train on the trivial stage; return the per-iteration mean episode reward."""
    torch.manual_seed(seed)
    cfg = ppo or PPOConfig()
    enc = encoder or EncoderConfig()
    env = build_trivial_env(seed)
    policy = HangarFitPolicy(**(policy_kwargs or {}))
    optimizer = torch.optim.Adam(policy.parameters(), lr=cfg.lr)
    history: list[float] = []
    for it in range(iterations):
        buf, ep_stats = collect_rollout(env, policy, enc, rollout_len)
        metrics = ppo_update(policy, optimizer, buf, cfg)
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
    return history


def main() -> None:
    p = argparse.ArgumentParser(description="Train the cold-joint policy on the trivial stage.")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--iterations", type=int, default=200)
    p.add_argument("--rollout-len", type=int, default=1024)
    p.add_argument("--lr", type=float, default=3e-4)
    args = p.parse_args()
    train(
        seed=args.seed,
        iterations=args.iterations,
        rollout_len=args.rollout_len,
        ppo=PPOConfig(lr=args.lr),
        log=True,
    )


if __name__ == "__main__":
    main()
