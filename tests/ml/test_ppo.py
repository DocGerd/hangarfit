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
