"""Roll-your-own PPO for the cold-joint policy (sub-project #4a, epic #607). Drives
HangarFitEnv + HangarFitPolicy directly: a rollout buffer, GAE, and a clipped-surrogate
update reusing the policy's masked two-head logits + value. Requires the [train] extra."""

from __future__ import annotations

from collections.abc import Sequence
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
    entropy_coef_start: float | None = None  # high->low anneal start; None = fixed entropy_coef
    entropy_coef_end: float | None = None  # anneal end; consulted only when start is set
    entropy_anneal_iters: int = 0  # iters over which to anneal; 0 = no schedule
    normalize_returns: bool = False  # std-only reward normalization before GAE
    return_norm_eps: float = 1e-8  # numerical floor on the running std
    # #720 (L4) PPO trust-region hardening, default-ON since #728 (the two-seed-validated #720
    # bundle). Set any to None to disable it — that knob's update-step path is then byte-identical
    # to the pre-#720 loop, though an unflagged *run* is the deliberate #728 default re-baseline.
    # The CLI exposes --no-reward-clip / --no-value-clip-eps / --no-target-kl for that. The
    # reward_clip value is tuned to the RewardWeights magnitudes (45 = r_valid_park 30 +
    # r_first_valid 15 graded bonus, ~50 terminal credit) — revisit on a reward-scale change.
    reward_clip: float | None = 50.0  # clamp RAW rewards to [-c, c] before normalize/GAE; tames
    # the unclipped -w_col collision spike (-12000 batches) that drove the gate sawtooth.
    value_clip_eps: float | None = 0.2  # PPO2 clipped value loss; caps per-update critic movement.
    target_kl: float | None = 0.03  # early-stop the epoch loop once a full epoch's mean approx-KL
    # exceeds this (a per-update trust region), so one catastrophic batch cannot over-rotate.


def entropy_coef_at(
    iteration: int, *, base: float, start: float | None, end: float | None, anneal_iters: int
) -> float:
    """Per-iteration entropy coefficient. Constant ``base`` when no schedule is configured
    (``start is None`` or ``anneal_iters <= 0``); else a linear ``start``→``end`` ramp over
    ``anneal_iters`` iterations, clamped at ``end`` past the window. Monotone non-increasing
    when start >= end (the intended high→low warmup). If ``end`` is None, anneals toward
    ``base``."""
    if start is None or anneal_iters <= 0:
        return base
    finish = end if end is not None else base
    if iteration >= anneal_iters:
        return finish
    frac = iteration / anneal_iters
    return start + (finish - start) * frac


class ReturnNormalizer:
    """Std-only reward normalizer (cleanrl convention: NO mean-subtraction) with a running
    variance (Welford) and warmup-to-identity. Divides the reward stream by the running std
    so −w_col collision spikes and +r_terminal sit on a scale the value head can fit, letting
    GAE propagate terminal credit through the drive-in. Identity until ``warmup`` samples seen
    and identity-equivalent at zero variance (eps floor). Std-only preserves the relative
    ordering of shaped rewards.

    SIDE EFFECT: ``normalize()`` updates the running stats — call once per rollout batch."""

    def __init__(self, *, eps: float = 1e-8, warmup: int = 256) -> None:
        self.eps = eps
        self.warmup = warmup
        self._count = 0
        self._mean = 0.0
        self._m2 = 0.0

    def _update(self, rewards: Tensor) -> None:
        for r in rewards.tolist():
            self._count += 1
            delta = r - self._mean
            self._mean += delta / self._count
            self._m2 += delta * (r - self._mean)

    def normalize(self, rewards: Tensor) -> Tensor:
        self._update(rewards)
        if self._count < self.warmup or self._count < 2:
            return rewards.clone()
        # population (biased, ÷N) variance — deliberate, cleanrl convention
        var = self._m2 / self._count
        std = max(var, 0.0) ** 0.5
        return rewards / (std + self.eps)

    def state_dict(self) -> dict[str, float | int]:
        """Plain-scalar state for the #710 resume checkpoint (no tensors, so it round-trips
        through torch.save/load weights_only=True cleanly). Captures the Welford running stats
        AND the eps/warmup config, so a reloaded normalizer scales the next batch identically."""
        return {
            "count": self._count,
            "mean": self._mean,
            "m2": self._m2,
            "eps": self.eps,
            "warmup": self.warmup,
        }

    def load_state_dict(self, state: dict[str, float | int]) -> None:
        """Restore the exact running stats + config saved by ``state_dict`` (overwrites the
        constructor's eps/warmup, so a resumed normalizer matches the saved one bit-for-bit)."""
        self._count = int(state["count"])
        self._mean = float(state["mean"])
        self._m2 = float(state["m2"])
        self.eps = float(state["eps"])
        self.warmup = int(state["warmup"])


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


def value_loss_term(
    new_value: Tensor, old_value: Tensor, returns: Tensor, clip_eps: float | None
) -> Tensor:
    """Per-minibatch critic loss. ``clip_eps`` None → plain ``((v - R)^2).mean()`` — byte-identical
    to the pre-#720 update. Else the PPO2 clipped form ``max((v - R)^2, (v_clip - R)^2)`` where
    ``v_clip = old_value + clamp(new_value - old_value, ±clip_eps)``: the clamp caps how far one
    update can move the value toward the returns and the ``max`` keeps the loss a pessimistic bound
    — a trust region on the critic so the unclipped collision spike can't yank the value head each
    batch (the #720 L4 stabilizer for the gate's value-driven sawtooth). ``old_value`` is the
    buffer's ROLLOUT-time (pre-update) critic prediction (``data['value']``), the clip anchor — not
    a re-forward (matches the SB3/cleanrl convention)."""
    unclipped = (new_value - returns) ** 2
    if clip_eps is None:
        return unclipped.mean()
    v_clipped = old_value + torch.clamp(new_value - old_value, -clip_eps, clip_eps)
    clipped = (v_clipped - returns) ** 2
    return torch.max(unclipped, clipped).mean()


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
    buffer: RolloutBuffer | VecRolloutBuffer,
    config: PPOConfig,
    *,
    normalizer: ReturnNormalizer | None = None,
) -> dict[str, float]:
    """One PPO update over the buffer: GAE, then `epochs` of clipped-surrogate +
    value-loss + entropy-bonus over shuffled minibatches. Returns the metrics averaged
    over every minibatch in the update (not just the last one).

    ``normalizer``: when ``config.normalize_returns`` is True and a ``ReturnNormalizer``
    is supplied, rewards are std-scaled (Welford running std, NO mean-subtraction) before
    GAE. Existing callers that pass no ``normalizer`` are byte-identical (default None)."""
    data = buffer.batch()
    rewards = data["reward"]
    # Clamp the RAW reward stream first (before the Welford normalizer + GAE) so a -w_col
    # collision spike can't blow up the running std or the returns. None => unchanged (#720 L4).
    if config.reward_clip is not None:
        rewards = rewards.clamp(-config.reward_clip, config.reward_clip)
    if config.normalize_returns:
        if normalizer is None:
            raise ValueError(
                "ppo_update: config.normalize_returns=True but normalizer=None; "
                "pass a ReturnNormalizer instance or set normalize_returns=False"
            )
        rewards = normalizer.normalize(rewards)
    if isinstance(buffer, VecRolloutBuffer):
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
            rewards,
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
    # Move the minibatch-loop tensors to the policy's device (CUDA opt-in). GAE + advantage
    # math above stays on CPU: it is a per-step scalar loop (`float(...)` per step), so doing
    # it on GPU would force a host sync every step. For a CPU policy every `.to()` is a no-op
    # that returns the same tensor, so the default path is byte-identical (the determinism
    # contract holds only for device='cpu'; CUDA is an explicitly non-deterministic fast path).
    device = next(policy.parameters()).device
    data = {k: (v.to(device) if torch.is_tensor(v) else v) for k, v in data.items()}
    advantages = advantages.to(device)
    returns = returns.to(device)
    # Number of FLAT transitions to shuffle/minibatch over. For a VecRolloutBuffer this is
    # T*N (advantages is length T*N), NOT len(buffer)==T — using len(buffer) would silently
    # train on only the first T of T*N rows and drop (N-1)/N of every rollout (#708). For the
    # single-stream RolloutBuffer advantages is length T == len(buffer), so this is unchanged.
    n = advantages.shape[0]
    accum: dict[str, list[float]] = {
        "policy_loss": [],
        "value_loss": [],
        "entropy": [],
        "loss": [],
        "approx_kl": [],
    }
    epochs_run = 0
    for _ in range(config.epochs):
        # device=cpu reproduces torch.randperm(n) exactly (same default generator).
        perm = torch.randperm(n, device=device)
        epoch_kls: list[float] = []
        for start in range(0, n, config.minibatch_size):
            mb = perm[start : start + config.minibatch_size]
            mb_obs = {k: data[k][mb] for k in _OBS_KEYS}
            out = policy(mb_obs)
            logprob, entropy = factored_logprob_entropy(
                out, data["kind_idx"][mb], data["mag_idx"][mb]
            )
            logratio = logprob - data["old_logprob"][mb]
            ratio = torch.exp(logratio)
            adv = advantages[mb]
            policy_loss = -torch.min(
                ratio * adv,
                torch.clamp(ratio, 1.0 - config.clip_eps, 1.0 + config.clip_eps) * adv,
            ).mean()
            value_loss = value_loss_term(
                out.value, data["value"][mb], returns[mb], config.value_clip_eps
            )
            entropy_bonus = entropy.mean()
            loss = (
                policy_loss + config.value_coef * value_loss - config.entropy_coef * entropy_bonus
            )
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), config.max_grad_norm)
            optimizer.step()
            # Schulman's non-negative approx-KL estimator (cleanrl): E[(r-1) - log r], read from
            # the pre-step forward. Pure telemetry — no grad, no RNG draw — so when target_kl is
            # None (no early stop) the update is byte-identical to the pre-#720 loop.
            with torch.no_grad():
                approx_kl = ((ratio - 1.0) - logratio).mean().item()
            epoch_kls.append(approx_kl)
            accum["policy_loss"].append(policy_loss.item())
            accum["value_loss"].append(value_loss.item())
            accum["entropy"].append(entropy_bonus.item())
            accum["loss"].append(loss.item())
            accum["approx_kl"].append(approx_kl)
        epochs_run += 1
        # Early-stop once a full epoch's mean approx-KL leaves the trust region (the #720 L4
        # backstop against a single catastrophic batch over-rotating the policy).
        if config.target_kl is not None and sum(epoch_kls) / len(epoch_kls) > config.target_kl:
            break
    metrics = {k: sum(vs) / len(vs) for k, vs in accum.items()}
    metrics["epochs_run"] = float(epochs_run)
    return metrics


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
            rewards[:, env],
            values[:, env],
            dones[:, env],
            float(last_values[env]),
            gamma=gamma,
            lam=lam,
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
        data["kind_idx"] = torch.tensor([x for row in self.kind_idx for x in row], dtype=torch.long)
        data["mag_idx"] = torch.tensor([x for row in self.mag_idx for x in row], dtype=torch.long)
        data["old_logprob"] = torch.tensor(self._flat(self.logprob), dtype=torch.float32)
        data["value"] = torch.tensor(self._flat(self.value), dtype=torch.float32)
        data["reward"] = torch.tensor(self._flat(self.reward), dtype=torch.float32)
        data["done"] = torch.tensor([x for row in self.done for x in row], dtype=torch.bool)
        return data
