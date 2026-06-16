"""Roll-your-own PPO for the cold-joint policy (sub-project #4a, epic #607). Drives
HangarFitEnv + HangarFitPolicy directly: a rollout buffer, GAE, and a clipped-surrogate
update reusing the policy's masked two-head logits + value. Requires the [train] extra."""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import Tensor

from ml.encoding import PARK_INDEX
from ml.policy import PolicyOutput

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
    post-buffer live obs) is used only for a non-`done` buffer tail."""
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
