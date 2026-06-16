# PPO Training Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a roll-your-own (cleanrl-style) PPO that drives `HangarFitEnv` + `HangarFitPolicy` directly, plus a runnable `python -m ml.train` that trains on the fixed trivial curriculum stage.

**Architecture:** `ml/ppo.py` holds the testable machinery (`PPOConfig`, the PARK-gated factored log-prob/entropy, `compute_gae`, `RolloutBuffer`, `ppo_update`). `ml/train.py` is the runnable entry that builds a trivial-stage env + policy, collects single-env rollouts, and runs the PPO loop, logging a reward curve. No curriculum schedule (→ 4b) or eval benchmark (→ 4c).

**Tech Stack:** Python 3.12, torch (the `[train]` extra, installed locally as CPU 2.12), pytest.

**Spec:** `docs/superpowers/specs/2026-06-16-learned-backend-training-core-design.md`

> **torch prerequisite:** `tests/ml/test_ppo.py` uses `pytest.importorskip("torch")` — it skips without torch. torch is installed in this dev env (CPU 2.12), so the tests run. CI skips them (a dedicated torch CI job is #6).

---

## File Structure
- **Create** `ml/ppo.py` — `PPOConfig`, `sample_action`, `factored_logprob_entropy`, `compute_gae`, `RolloutBuffer`, `ppo_update`.
- **Create** `ml/train.py` — `build_trivial_env`, `collect_rollout`, `train`, `python -m ml.train` CLI.
- **Create** `tests/ml/test_ppo.py` (`importorskip` torch).
- **Modify** `CHANGELOG.md`.

No `pyproject.toml` change (torch already in `[train]`). `ml/` stays out of the wheel.

---

### Task 1: `PPOConfig` + the PARK-gated factored log-prob / entropy

**Files:** Create `ml/ppo.py`; Test `tests/ml/test_ppo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_ppo.py
"""Tests for the PPO training core (ml/ppo.py). Requires torch."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from ml.encoding import ACTION_DIM, PARK_INDEX
from ml.action_space import MAGNITUDE_DIM
from ml.policy import PolicyOutput
from ml.ppo import PPOConfig, factored_logprob_entropy


def _out(batch=2):
    # deterministic logits
    torch.manual_seed(0)
    return PolicyOutput(
        kind_gear_logits=torch.randn(batch, ACTION_DIM),
        magnitude_bin_logits=torch.randn(batch, MAGNITUDE_DIM),
        value=torch.randn(batch),
    )


def test_ppo_config_defaults():
    c = PPOConfig()
    assert c.gamma == 0.99 and c.lam == 0.95 and c.clip_eps == 0.2
    assert c.epochs == 4 and c.value_coef == 0.5


def test_factored_logprob_park_excludes_magnitude():
    out = _out(batch=2)
    kind = torch.tensor([PARK_INDEX, 1])   # row0 = PARK, row1 = a movement (S,+1)
    mag = torch.tensor([3, 3])
    logprob, entropy = factored_logprob_entropy(out, kind, mag)
    kind_dist = torch.distributions.Categorical(logits=out.kind_gear_logits)
    mag_dist = torch.distributions.Categorical(logits=out.magnitude_bin_logits)
    # PARK row: joint logprob == kind logprob only (magnitude excluded)
    assert torch.isclose(logprob[0], kind_dist.log_prob(kind)[0])
    # movement row: joint logprob == kind + mag
    assert torch.isclose(logprob[1], kind_dist.log_prob(kind)[1] + mag_dist.log_prob(mag)[1])
    # PARK row entropy excludes the magnitude head
    assert torch.isclose(entropy[0], kind_dist.entropy()[0])
    assert torch.isclose(entropy[1], kind_dist.entropy()[1] + mag_dist.entropy()[1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_ppo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.ppo'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ml/ppo.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_ppo.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/ppo.py tests/ml/test_ppo.py
git commit -m "feat(607): PPO config + PARK-gated factored log-prob/entropy"
```

---

### Task 2: `compute_gae` (every `done` a true terminal)

**Files:** Modify `ml/ppo.py`; Test `tests/ml/test_ppo.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_ppo.py
from ml.ppo import compute_gae


def test_compute_gae_hand_checked_no_done():
    # One episode, no terminals, bootstrap from last_value.
    rewards = torch.tensor([1.0, 1.0, 1.0])
    values = torch.tensor([0.5, 0.6, 0.7])
    dones = torch.tensor([False, False, False])
    gamma, lam, last_value = 0.99, 0.95, 0.8
    adv, ret = compute_gae(rewards, values, dones, last_value, gamma=gamma, lam=lam)
    # recompute by hand, backward
    exp = [0.0, 0.0, 0.0]
    lastgae = 0.0
    vals = [0.5, 0.6, 0.7]
    for t in (2, 1, 0):
        nv = last_value if t == 2 else vals[t + 1]
        delta = [1.0, 1.0, 1.0][t] + gamma * nv - vals[t]
        lastgae = delta + gamma * lam * lastgae
        exp[t] = lastgae
    assert torch.allclose(adv, torch.tensor(exp), atol=1e-5)
    assert torch.allclose(ret, adv + values, atol=1e-5)


def test_compute_gae_done_zeroes_bootstrap_and_resets():
    # done at t=1 -> no bootstrap past it, lambda recursion resets.
    rewards = torch.tensor([1.0, 2.0, 3.0])
    values = torch.tensor([0.5, 0.6, 0.7])
    dones = torch.tensor([False, True, False])
    gamma, lam, last_value = 0.99, 0.95, 0.4
    adv, _ = compute_gae(rewards, values, dones, last_value, gamma=gamma, lam=lam)
    # t=2 (tail, not done): delta2 = 3 + gamma*last_value - 0.7; adv2 = delta2
    d2 = 3.0 + gamma * 0.4 - 0.7
    # t=1 (done): nonterminal=0 -> delta1 = 2 + 0 - 0.6; adv1 = delta1 (recursion reset)
    d1 = 2.0 + 0.0 - 0.6
    # t=0: delta0 = 1 + gamma*values[1] - 0.5; adv0 = delta0 + gamma*lam*adv1
    d0 = 1.0 + gamma * 0.6 - 0.5
    a0 = d0 + gamma * lam * d1
    assert torch.allclose(adv, torch.tensor([a0, d1, d2]), atol=1e-5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_ppo.py -q -k gae`
Expected: FAIL — `ImportError: cannot import name 'compute_gae'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/ppo.py
def compute_gae(
    rewards: Tensor, values: Tensor, dones: Tensor, last_value: float,
    *, gamma: float = 0.99, lam: float = 0.95,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_ppo.py -q -k gae`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/ppo.py tests/ml/test_ppo.py
git commit -m "feat(607): compute_gae (every done a true terminal, intrinsic horizon)"
```

---

### Task 3: `RolloutBuffer` + `sample_action`

**Files:** Modify `ml/ppo.py`; Test `tests/ml/test_ppo.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_ppo.py
from ml.encoding import EncoderConfig, encode
from ml.types import ActiveObject, Observation, ParkedObject, Pose
from hangarfit.models import Placement
from tests.ml.conftest import _fuji, empty_hangar
from ml.ppo import RolloutBuffer, sample_action
from ml.policy import HangarFitPolicy


def _obs_t():
    fleet = _fuji()
    active = ActiveObject(
        object_id="fuji", body=fleet["fuji"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0), on_carts=False,
    )
    obs = Observation(active=active, parked=(), unplaced_ids=(), steps_this_object=0, steps_total=0)
    return encode(obs, empty_hangar(), fleet, EncoderConfig())


def test_rollout_buffer_batches_to_expected_shapes():
    buf = RolloutBuffer()
    for _ in range(5):
        buf.add(_obs_t(), kind_idx=1, mag_idx=2, logprob=-1.0, value=0.5, reward=0.1, done=False)
    assert len(buf) == 5
    data = buf.batch()
    assert data["raster"].shape[0] == 5 and data["tokens"].shape[0] == 5
    assert data["kind_idx"].tolist() == [1, 1, 1, 1, 1]
    assert data["kind_idx"].dtype == torch.long
    assert data["old_logprob"].shape == (5,) and data["reward"].shape == (5,)
    assert data["done"].dtype == torch.bool


def test_sample_action_returns_legal_kind():
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2).eval()
    batch = to_batch([_obs_t()])
    out = policy(batch)
    kind, mag = sample_action(out)
    assert batch["legal_action_mask"][0][int(kind)]   # never an illegal kind
    assert 0 <= int(mag) < 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_ppo.py -q -k "buffer or sample_action"`
Expected: FAIL — `ImportError: cannot import name 'RolloutBuffer'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/ppo.py
def sample_action(out: PolicyOutput) -> tuple[Tensor, Tensor]:
    """Sample (kind_idx, mag_idx) from the masked kind head + the magnitude head.
    The kind logits are already -inf-masked, so an illegal kind is never sampled."""
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
        self, obs: ObservationTensors, *, kind_idx: int, mag_idx: int,
        logprob: float, value: float, reward: float, done: bool,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_ppo.py -q -k "buffer or sample_action"`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/ppo.py tests/ml/test_ppo.py
git commit -m "feat(607): RolloutBuffer + sample_action"
```

---

### Task 4: `ppo_update`

**Files:** Modify `ml/ppo.py`; Test `tests/ml/test_ppo.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_ppo.py
from ml.ppo import ppo_update


def _filled_buffer(policy, n=40):
    buf = RolloutBuffer()
    with torch.no_grad():
        for i in range(n):
            o = _obs_t()
            out = policy(to_batch([o]))
            kind, mag = sample_action(out)
            lp, _ = factored_logprob_entropy(out, kind, mag)
            buf.add(o, kind_idx=int(kind), mag_idx=int(mag), logprob=float(lp),
                    value=float(out.value), reward=0.1 * (i % 3), done=(i % 7 == 6))
    buf.last_value = 0.0
    return buf


def test_ppo_update_runs_changes_params_finite():
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    before = [p.detach().clone() for p in policy.parameters()]
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    metrics = ppo_update(policy, opt, _filled_buffer(policy), PPOConfig(minibatch_size=16))
    assert all(torch.isfinite(torch.tensor(v)) for v in metrics.values())
    changed = any(not torch.equal(b, a) for b, a in zip(before, policy.parameters()))
    assert changed


def test_ppo_update_overfits_fixed_batch():
    torch.manual_seed(1)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    buf = _filled_buffer(policy)
    opt = torch.optim.Adam(policy.parameters(), lr=1e-3)
    cfg = PPOConfig(minibatch_size=16, epochs=1, entropy_coef=0.0)
    first = ppo_update(policy, opt, buf, cfg)["policy_loss"]
    for _ in range(8):
        last = ppo_update(policy, opt, buf, cfg)["policy_loss"]
    # repeatedly updating on the same batch drives the surrogate down
    assert last < first + 1e-3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_ppo.py -q -k ppo_update`
Expected: FAIL — `ImportError: cannot import name 'ppo_update'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/ppo.py
def ppo_update(
    policy: HangarFitPolicy, optimizer: "torch.optim.Optimizer",
    buffer: RolloutBuffer, config: PPOConfig,
) -> dict[str, float]:
    """One PPO update over the buffer: GAE, then `epochs` of clipped-surrogate +
    value-loss + entropy-bonus over shuffled minibatches. Returns last-minibatch metrics."""
    data = buffer.batch()
    advantages, returns = compute_gae(
        data["reward"], data["value"], data["done"], buffer.last_value,
        gamma=config.gamma, lam=config.lam,
    )
    advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
    n = len(buffer)
    metrics: dict[str, float] = {}
    for _ in range(config.epochs):
        perm = torch.randperm(n)
        for start in range(0, n, config.minibatch_size):
            mb = perm[start : start + config.minibatch_size]
            mb_obs = {k: data[k][mb] for k in _OBS_KEYS}
            out = policy(mb_obs)
            logprob, entropy = factored_logprob_entropy(out, data["kind_idx"][mb], data["mag_idx"][mb])
            ratio = torch.exp(logprob - data["old_logprob"][mb])
            adv = advantages[mb]
            policy_loss = -torch.min(
                ratio * adv,
                torch.clamp(ratio, 1.0 - config.clip_eps, 1.0 + config.clip_eps) * adv,
            ).mean()
            value_loss = ((out.value - returns[mb]) ** 2).mean()
            entropy_bonus = entropy.mean()
            loss = policy_loss + config.value_coef * value_loss - config.entropy_coef * entropy_bonus
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), config.max_grad_norm)
            optimizer.step()
            metrics = {
                "policy_loss": float(policy_loss),
                "value_loss": float(value_loss),
                "entropy": float(entropy_bonus),
                "loss": float(loss),
            }
    return metrics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_ppo.py -q -k ppo_update`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/ppo.py tests/ml/test_ppo.py
git commit -m "feat(607): ppo_update (clipped surrogate + value + entropy)"
```

---

### Task 5: `ml/train.py` — trivial-stage env, rollout, train loop

**Files:** Create `ml/train.py`; Test `tests/ml/test_ppo.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_ppo.py
from ml.train import build_trivial_env, train


def test_build_trivial_env_single_object():
    env = build_trivial_env(seed=0)
    obs = env.reset()
    assert obs.active is not None                 # one active object on the apron
    assert len(env.requested_ids) == 1


def test_train_runs_and_returns_history():
    history = train(seed=0, iterations=2, rollout_len=32,
                    policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2})
    assert len(history) == 2
    assert all(isinstance(r, float) for r in history)


def test_train_is_seed_reproducible():
    kw = dict(iterations=2, rollout_len=32,
              policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2})
    a = train(seed=7, **kw)
    b = train(seed=7, **kw)
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ml/test_ppo.py -q -k "trivial_env or train"`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.train'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ml/train.py
"""python -m ml.train — train HangarFitPolicy on the fixed trivial curriculum stage
via the roll-your-own PPO in ml.ppo. Sub-project #4a (#607). Requires the [train] extra.

The trivial stage: a single object driven in from the apron and parked in a loose
hangar within a small step budget — the easiest curriculum rung. Curriculum ramping is
sub-project #4b; the reach-not-beat benchmark is #4c."""

from __future__ import annotations

import argparse
from dataclasses import replace

import torch

from ml.action_space import decode
from ml.encoding import EncoderConfig, encode
from ml.env import HangarFitEnv
from ml.policy import HangarFitPolicy, to_batch
from ml.ppo import PPOConfig, RolloutBuffer, factored_logprob_entropy, ppo_update, sample_action
from ml.types import DifficultyConfig
from tests.ml.conftest import _fuji, empty_hangar  # reuse the synthetic fleet + hangar

_TRIVIAL_DIFFICULTY = DifficultyConfig(max_objects=1, per_object_step_budget=40, total_step_budget=40)


def build_trivial_env(seed: int = 0) -> HangarFitEnv:
    """A 1-object, loose-hangar, small-budget env — the easiest curriculum rung."""
    fleet = _fuji()
    return HangarFitEnv(
        hangar=empty_hangar(),
        fleet=fleet,
        requested_ids=("fuji",),
        difficulty=_TRIVIAL_DIFFICULTY,
    )


def _bodies(env: HangarFitEnv) -> dict:
    return {**env.fleet, **env.ground_objects}


def collect_rollout(
    env: HangarFitEnv, policy: HangarFitPolicy, encoder: EncoderConfig, rollout_len: int
) -> tuple[RolloutBuffer, list[float]]:
    """Drive the env single-stream for `rollout_len` steps; return the buffer and the
    list of completed-episode total rewards (for the reward-curve log)."""
    buf = RolloutBuffer()
    bodies = _bodies(env)
    obs = env.reset()
    ep_reward, ep_rewards = 0.0, []
    with torch.no_grad():
        while len(buf) < rollout_len:
            obs_t = encode(obs, env.hangar, bodies, encoder)
            out = policy(to_batch([obs_t]))
            kind, mag = sample_action(out)
            logprob, _ = factored_logprob_entropy(out, kind, mag)
            tr = obs.active.body.effective_turn_radius_m()
            primitive = decode(int(kind), int(mag), turn_radius_m=tr)
            nxt, reward, done, _info = env.step(primitive)
            buf.add(obs_t, kind_idx=int(kind), mag_idx=int(mag), logprob=float(logprob),
                    value=float(out.value), reward=float(reward), done=bool(done))
            ep_reward += float(reward)
            if done:
                ep_rewards.append(ep_reward)
                ep_reward = 0.0
                obs = env.reset()
            else:
                obs = nxt
        # bootstrap value for a non-done tail
        if not buf.done[-1]:
            tail = encode(obs, env.hangar, bodies, encoder)
            buf.last_value = float(policy(to_batch([tail])).value)
    return buf, ep_rewards


def train(
    *, seed: int = 0, iterations: int = 50, rollout_len: int = 512,
    ppo: PPOConfig | None = None, policy_kwargs: dict | None = None,
    encoder: EncoderConfig | None = None, log: bool = False,
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
        buf, ep_rewards = collect_rollout(env, policy, enc, rollout_len)
        metrics = ppo_update(policy, optimizer, buf, cfg)
        mean_r = sum(ep_rewards) / len(ep_rewards) if ep_rewards else 0.0
        history.append(mean_r)
        if log:
            print(f"iter {it:4d}  mean_ep_reward={mean_r:+.3f}  "
                  f"loss={metrics['loss']:+.3f}  entropy={metrics['entropy']:.3f}")
    return history


def main() -> None:
    p = argparse.ArgumentParser(description="Train the cold-joint policy on the trivial stage.")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--iterations", type=int, default=200)
    p.add_argument("--rollout-len", type=int, default=1024)
    p.add_argument("--lr", type=float, default=3e-4)
    args = p.parse_args()
    train(seed=args.seed, iterations=args.iterations, rollout_len=args.rollout_len,
          ppo=PPOConfig(lr=args.lr), log=True)


if __name__ == "__main__":
    main()
```

> **Note on the `tests.ml.conftest` import.** `build_trivial_env` reuses the synthetic `_fuji()` fleet + `empty_hangar()` from `tests/ml/conftest.py` so 4a needs no new fixture data. If importing from `tests/` inside `ml/` is undesirable, the implementer may inline a tiny fleet/hangar loader in `ml/train.py` instead (using `hangarfit.loader.load_fleet("data/fleet.yaml")` + `load_hangar("data/hangar.yaml")` with `apron_depth_m` set) — functionally identical. Pick one and keep it consistent; the tests above import `build_trivial_env`, not the fixtures, so either works.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ml/test_ppo.py -q -k "trivial_env or train"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/train.py tests/ml/test_ppo.py
git commit -m "feat(607): ml.train — trivial-stage env, rollout, PPO train loop"
```

---

### Task 6: CHANGELOG, full verification, manual learn-validation, PR

**Files:** Modify `CHANGELOG.md`

- [ ] **Step 1: Add the CHANGELOG entry**

Under `## [Unreleased] / ### Added` in `CHANGELOG.md`:

```markdown
- Learned backend (#607, sub-project #4a): the cold-joint PPO **training core** —
  `ml/ppo.py` (a roll-your-own cleanrl-style PPO: `RolloutBuffer`, GAE, clipped-surrogate
  `ppo_update`, PARK-gated factored log-prob/entropy) driving `HangarFitEnv` +
  `HangarFitPolicy` directly, plus `python -m ml.train` that trains on the fixed trivial
  curriculum stage. Contributor-only (the `[train]` torch extra; tests `importorskip`
  torch). Curriculum schedule (#4b) and the reach-not-beat eval (#4c) are separate.
```

- [ ] **Step 2: Lint, type-check, full suite**

Run:
```bash
ruff check ml/ppo.py ml/train.py tests/ml/test_ppo.py
ruff format --check ml/ppo.py ml/train.py tests/ml/test_ppo.py
mypy ml/ppo.py ml/train.py
python -m pytest tests/ml/ -q
```
Expected: ruff/mypy clean; the suite green (the existing 65 + the new ~11 PPO tests). (`mypy` needs torch installed for stubs — it is.)

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(607): CHANGELOG for the PPO training core (sub-project #4a)"
```

- [ ] **Step 4: Manual learn-validation (report, not a CI gate)**

Run a real training run on the trivial stage and capture the reward curve:
```bash
python -m ml.train --seed 0 --iterations 200 --rollout-len 1024 2>&1 | tee /tmp/train_curve.txt
tail -20 /tmp/train_curve.txt
```
Expected: the `mean_ep_reward` trends upward over iterations (the agent learns to drive the single object in and park it). **Paste the curve (or a downsampled summary) into the PR body.** If it does not improve, that is a finding to report — note it and the hyper-parameters tried; do not silently claim success.

- [ ] **Step 5: Push + open the draft PR (base develop)**

```bash
git push -u origin feature/607-rung5-training-core
gh pr create --draft --base develop \
  --title "feat(607): PPO training core (sub-project #4a impl)" \
  --body "Closes #684. Roll-your-own PPO + python -m ml.train per the design spec (PR #683). PPO mechanics CI-tested (importorskip torch); learning validated by the manual reward curve below. No curriculum (#4b) / eval (#4c). Review arc: code-reviewer + silent-failure-hunter (rollout/GAE edges). <PASTE REWARD CURVE>"
```

- [ ] **Step 6: Review arc, resolve, ready**

Invoke `/pr-review` (code-reviewer + silent-failure-hunter). Convert findings to threads, fix or rebut, then `gh pr ready <n>` and hand off (the user is the sole merger).

---

## Self-Review (completed during plan authoring)
- **Spec coverage:** §3 modules → `ml/ppo.py` (Tasks 1–4) + `ml/train.py` (Task 5); §4 rollout (env-direct, value-from-forward, loop ordering, turn-radius from `obs.active.body`) → Task 5 `collect_rollout`; §5 PARK-gated joint logprob/entropy → Task 1; §6 GAE (every done terminal) + PPO update → Tasks 2/4; §7 seeding/determinism → Task 5 `test_train_is_seed_reproducible`; §8 train.py entry + trivial stage → Task 5; §9 testing (mechanics CI + manual learn-validation) → Tasks 1–5 + Task 6 Step 4; §11 workflow → Task 6. No gaps.
- **Type consistency:** `PPOConfig`, `factored_logprob_entropy(out, kind_idx, mag_idx)`, `compute_gae(rewards, values, dones, last_value, *, gamma, lam)`, `RolloutBuffer.add(obs, *, kind_idx, mag_idx, logprob, value, reward, done)` / `.batch()` / `.last_value`, `sample_action(out)`, `ppo_update(policy, optimizer, buffer, config)`, `build_trivial_env(seed)`, `collect_rollout(env, policy, encoder, rollout_len)`, `train(*, seed, iterations, rollout_len, ...)` are used identically across tasks and tests.
- **Placeholder scan:** every code step is complete; the only placeholder is `#<RUNG5_ISSUE>` / `<PASTE REWARD CURVE>` in the Task-6 PR body, filled at PR time.
- **Known notes:** `mypy` on `ml/ppo.py`/`ml/train.py` needs torch installed (it is); `train.py` imports the synthetic fleet/hangar from `tests/ml/conftest.py` (documented alternative: inline a loader). The entropy bonus uses the per-step mean (`entropy.mean()`), which excludes PARK magnitude-entropy as the spec requires (the non-PARK denominator nuance is absorbed by `entropy_coef`).
