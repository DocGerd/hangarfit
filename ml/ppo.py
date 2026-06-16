"""Roll-your-own PPO for the cold-joint policy (sub-project #4a, epic #607). Drives
HangarFitEnv + HangarFitPolicy directly: a rollout buffer, GAE, and a clipped-surrogate
update reusing the policy's masked two-head logits + value. Requires the [train] extra."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor

from ml.encoding import PARK_INDEX, ObservationTensors
from ml.policy import HangarFitPolicy, PolicyOutput, to_batch

_OBS_KEYS = ("raster", "tokens", "token_mask", "active_index", "legal_action_mask")


@dataclass
class PPOConfig:
    gamma: float = 0.99
    lam: float = 0.95
    clip_eps: float = 0.2
    lr: float = 3e-4
    epochs: int = 4
    minibatch_size: int = 64
    value_coef: float = 0.5
    entropy_coef: float = 0.01
    max_grad_norm: float = 0.5


def factored_logprob_entropy(
    out: PolicyOutput, kind_idx: Tensor, mag_idx: Tensor
) -> tuple[Tensor, Tensor]:
    """Per-step PARK-gated joint log-prob and entropy. For PARK steps the magnitude
    head is excluded (decode ignores the magnitude bin for PARK), so including it would
    inject a spurious gradient. Returns (logprob[B], entropy[B]); the entropy bonus is
    the minibatch mean of entropy[B] (PARK rows contribute kind-entropy only)."""
    kind_dist = torch.distributions.Categorical(logits=out.kind_gear_logits)
    mag_dist = torch.distributions.Categorical(logits=out.magnitude_bin_logits)
    not_park = (kind_idx != PARK_INDEX).to(out.kind_gear_logits.dtype)
    logprob = kind_dist.log_prob(kind_idx) + not_park * mag_dist.log_prob(mag_idx)
    entropy = kind_dist.entropy() + not_park * mag_dist.entropy()
    return logprob, entropy


def compute_gae(
    rewards: Tensor,
    values: Tensor,
    dones: Tensor,
    last_value: float,
    *,
    gamma: float = 0.99,
    lam: float = 0.95,
) -> tuple[Tensor, Tensor]:
    """GAE-λ advantages + returns. Every `done` is a true terminal (the env emits a
    terminal reward at both set-complete and budget-stop — §4.5): on a `done` step the
    bootstrap is zeroed and the λ-recursion resets. `last_value` (a forward on the
    post-buffer live obs) is used only for a non-`done` buffer tail. `last_value` is
    multiplied by `(1 - dones[-1])` internally, so passing a non-zero `last_value` for a
    terminal tail step is safe — it is zeroed when the final step is itself a `done`."""
    n = rewards.shape[0]
    advantages = torch.zeros(n)
    last_gae = 0.0
    for t in reversed(range(n)):
        nonterminal = 1.0 - float(dones[t])
        next_value = last_value if t == n - 1 else float(values[t + 1])
        next_value = next_value * nonterminal
        delta = float(rewards[t]) + gamma * next_value - float(values[t])
        last_gae = delta + gamma * lam * nonterminal * last_gae
        advantages[t] = last_gae
    returns = advantages + values
    return advantages, returns


def sample_action(out: PolicyOutput) -> tuple[Tensor, Tensor]:
    """Sample (kind_idx, mag_idx) from the masked kind head + the magnitude head.
    The kind logits are already -inf-masked, so an illegal kind is never sampled."""
    if not out.kind_gear_logits.isfinite().any(dim=-1).all():
        raise ValueError(
            "sample_action: all kind logits are -inf in a batch row (terminal observation?)"
        )
    kind = torch.distributions.Categorical(logits=out.kind_gear_logits).sample()
    mag = torch.distributions.Categorical(logits=out.magnitude_bin_logits).sample()
    return kind, mag


class RolloutBuffer:
    """Accumulates single-env transitions; `batch()` stacks them into tensors (the obs
    via policy.to_batch). `last_value` is the bootstrap value for a non-done tail."""

    def __init__(self) -> None:
        self.obs: list[ObservationTensors] = []
        self.kind_idx: list[int] = []
        self.mag_idx: list[int] = []
        self.logprob: list[float] = []
        self.value: list[float] = []
        self.reward: list[float] = []
        self.done: list[bool] = []
        self.last_value: float = 0.0

    def add(
        self,
        obs: ObservationTensors,
        *,
        kind_idx: int,
        mag_idx: int,
        logprob: float,
        value: float,
        reward: float,
        done: bool,
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

    def batch(self) -> dict[str, Tensor]:
        data = dict(to_batch(self.obs))
        data["kind_idx"] = torch.tensor(self.kind_idx, dtype=torch.long)
        data["mag_idx"] = torch.tensor(self.mag_idx, dtype=torch.long)
        data["old_logprob"] = torch.tensor(self.logprob, dtype=torch.float32)
        data["value"] = torch.tensor(self.value, dtype=torch.float32)
        data["reward"] = torch.tensor(self.reward, dtype=torch.float32)
        data["done"] = torch.tensor(self.done, dtype=torch.bool)
        return data


def ppo_update(
    policy: HangarFitPolicy,
    optimizer: torch.optim.Optimizer,
    buffer: RolloutBuffer,
    config: PPOConfig,
) -> dict[str, float]:
    """One PPO update over the buffer: GAE, then `epochs` of clipped-surrogate +
    value-loss + entropy-bonus over shuffled minibatches. Returns the metrics averaged
    over every minibatch in the update (not just the last one)."""
    data = buffer.batch()
    advantages, returns = compute_gae(
        data["reward"],
        data["value"],
        data["done"],
        buffer.last_value,
        gamma=config.gamma,
        lam=config.lam,
    )
    # Degenerate (≈zero-variance) advantages make the usual /std normalization
    # numerically meaningless — a tiny std can blow up or NaN the ratios. Center only
    # in that case; normalize otherwise. Then assert finiteness so a bad batch fails
    # loud instead of silently poisoning the gradient.
    advantages = advantages - advantages.mean()
    std = advantages.std()
    if torch.isfinite(std) and std >= 1e-6:
        advantages = advantages / std
    if not torch.isfinite(advantages).all():
        raise RuntimeError("advantages contain NaN/inf after normalization")
    n = len(buffer)
    accum: dict[str, list[float]] = {
        "policy_loss": [],
        "value_loss": [],
        "entropy": [],
        "loss": [],
    }
    for _ in range(config.epochs):
        perm = torch.randperm(n)
        for start in range(0, n, config.minibatch_size):
            mb = perm[start : start + config.minibatch_size]
            mb_obs = {k: data[k][mb] for k in _OBS_KEYS}
            out = policy(mb_obs)
            logprob, entropy = factored_logprob_entropy(
                out, data["kind_idx"][mb], data["mag_idx"][mb]
            )
            ratio = torch.exp(logprob - data["old_logprob"][mb])
            adv = advantages[mb]
            policy_loss = -torch.min(
                ratio * adv,
                torch.clamp(ratio, 1.0 - config.clip_eps, 1.0 + config.clip_eps) * adv,
            ).mean()
            value_loss = ((out.value - returns[mb]) ** 2).mean()
            entropy_bonus = entropy.mean()
            loss = (
                policy_loss + config.value_coef * value_loss - config.entropy_coef * entropy_bonus
            )
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), config.max_grad_norm)
            optimizer.step()
            accum["policy_loss"].append(policy_loss.item())
            accum["value_loss"].append(value_loss.item())
            accum["entropy"].append(entropy_bonus.item())
            accum["loss"].append(loss.item())
    return {k: sum(vs) / len(vs) for k, vs in accum.items()}
