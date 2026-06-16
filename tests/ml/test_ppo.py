"""Tests for the PPO training core (ml/ppo.py). Requires torch."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from ml.action_space import MAGNITUDE_DIM  # noqa: E402
from ml.encoding import ACTION_DIM, PARK_INDEX  # noqa: E402
from ml.policy import PolicyOutput  # noqa: E402
from ml.ppo import PPOConfig, factored_logprob_entropy  # noqa: E402


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
    kind = torch.tensor([PARK_INDEX, 1])  # row0 = PARK, row1 = a movement (S,+1)
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


# ---------------------------------------------------------------------------
# Task 2: compute_gae
# ---------------------------------------------------------------------------
from ml.ppo import compute_gae  # noqa: E402


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


# ---------------------------------------------------------------------------
# Task 3: RolloutBuffer + sample_action
# ---------------------------------------------------------------------------
from ml.encoding import EncoderConfig, encode  # noqa: E402
from ml.policy import HangarFitPolicy, to_batch  # noqa: E402
from ml.ppo import RolloutBuffer, sample_action  # noqa: E402
from ml.types import ActiveObject, Observation, Pose  # noqa: E402
from tests.ml.conftest import _fuji, empty_hangar  # noqa: E402


def _obs_t():
    fleet = _fuji()
    active = ActiveObject(
        object_id="fuji",
        body=fleet["fuji"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0),
        on_carts=False,
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
    assert batch["legal_action_mask"][0][int(kind)]  # never an illegal kind
    assert 0 <= int(mag) < 5
