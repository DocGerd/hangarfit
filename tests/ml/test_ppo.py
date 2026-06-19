"""Tests for the PPO training core (ml/ppo.py). Requires torch."""

from __future__ import annotations

import math

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


# ---------------------------------------------------------------------------
# Task 4: ppo_update
# ---------------------------------------------------------------------------
from ml.ppo import ppo_update  # noqa: E402


def _filled_buffer(policy, n=40):
    buf = RolloutBuffer()
    with torch.no_grad():
        for i in range(n):
            o = _obs_t()
            out = policy(to_batch([o]))
            kind, mag = sample_action(out)
            lp, _ = factored_logprob_entropy(out, kind, mag)
            buf.add(
                o,
                kind_idx=int(kind),
                mag_idx=int(mag),
                logprob=float(lp),
                value=float(out.value),
                reward=0.1 * (i % 3),
                done=(i % 7 == 6),
            )
    buf.last_value = 0.0
    return buf


def test_ppo_update_runs_changes_params_finite():
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    before = [p.detach().clone() for p in policy.parameters()]
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    metrics = ppo_update(policy, opt, _filled_buffer(policy), PPOConfig(minibatch_size=16))
    assert all(torch.isfinite(torch.tensor(v)) for v in metrics.values())
    changed = any(not torch.equal(b, a) for b, a in zip(before, policy.parameters(), strict=True))
    assert changed


@pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a CUDA device")
def test_ppo_update_runs_on_cuda_policy():
    # ppo_update must honour the policy's device: a CUDA policy + a CPU-built buffer ->
    # the update moves the batch to the policy device, runs, and changes params finitely.
    torch.manual_seed(0)
    buf = _filled_buffer(HangarFitPolicy(d_model=32, n_layers=1, n_heads=2))  # CPU obs
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2).to("cuda")
    before = [p.detach().clone() for p in policy.parameters()]
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    metrics = ppo_update(policy, opt, buf, PPOConfig(minibatch_size=16))
    assert all(math.isfinite(v) for v in metrics.values())
    assert any(not torch.equal(b, a) for b, a in zip(before, policy.parameters(), strict=True))


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


# ---------------------------------------------------------------------------
# Task 5: build_trivial_env, collect_rollout, train
# ---------------------------------------------------------------------------
from ml.train import build_trivial_env, train  # noqa: E402


def test_build_trivial_env_single_object():
    env = build_trivial_env(seed=0)
    obs = env.reset()
    assert obs.active is not None  # one active object on the apron
    assert len(env.requested_ids) == 1


def test_train_runs_and_returns_history():
    history = train(
        seed=0,
        iterations=2,
        rollout_len=32,
        policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2},
    )
    assert len(history) == 2
    assert all(isinstance(r, float) for r in history)


def test_train_is_seed_reproducible():
    kw = dict(
        iterations=2,
        rollout_len=32,
        policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2},
    )
    a = train(seed=7, **kw)
    b = train(seed=7, **kw)
    assert a == b


# ---------------------------------------------------------------------------
# Hardening: silent-failure guards (harden(607))
# ---------------------------------------------------------------------------


def test_train_no_completed_episodes_is_nan(monkeypatch):
    # An episode ends either on PARK (set complete) or on a budget stop. Force every
    # step to be a non-PARK movement (kind 0 is always a legal movement primitive) so
    # that, with rollout_len (8) < the 40-step budget, NO episode completes this
    # iteration -> the curve must record NaN ("0 episodes"), not a misleading 0.0.
    import ml.train as train_mod

    monkeypatch.setattr(train_mod, "sample_action", lambda out: (torch.tensor(0), torch.tensor(0)))
    history = train(
        seed=0,
        iterations=1,
        rollout_len=8,
        policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2},
    )
    assert len(history) == 1
    assert math.isnan(history[0])


def test_ppo_update_degenerate_advantages_stay_finite():
    # All values/rewards identical -> advantage std ≈ 0; the normalization guard must
    # keep everything finite (center-only, no /std blow-up).
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    buf = RolloutBuffer()
    with torch.no_grad():
        for _ in range(16):
            o = _obs_t()
            out = policy(to_batch([o]))
            kind, mag = sample_action(out)
            lp, _ = factored_logprob_entropy(out, kind, mag)
            buf.add(
                o,
                kind_idx=int(kind),
                mag_idx=int(mag),
                logprob=float(lp),
                value=0.5,  # identical values
                reward=0.5,  # identical rewards
                done=False,
            )
    buf.last_value = 0.5
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    metrics = ppo_update(policy, opt, buf, PPOConfig(minibatch_size=8))
    assert all(math.isfinite(v) for v in metrics.values())
    assert all(torch.isfinite(p).all() for p in policy.parameters())


def test_sample_action_all_illegal_raises():
    # A row whose kind logits are all -inf (no legal action) must fail loud, not sample
    # garbage from a degenerate distribution.
    out = PolicyOutput(
        kind_gear_logits=torch.full((1, ACTION_DIM), float("-inf")),
        magnitude_bin_logits=torch.zeros(1, MAGNITUDE_DIM),
        value=torch.zeros(1),
    )
    with pytest.raises(ValueError, match="all kind logits are -inf"):
        sample_action(out)


# ---------------------------------------------------------------------------
# Task 4: entropy_coef_at schedule
# ---------------------------------------------------------------------------
from ml.ppo import entropy_coef_at  # noqa: E402


def test_entropy_coef_constant_when_off():
    # start None -> constant base regardless of iteration.
    assert entropy_coef_at(0, base=0.01, start=None, end=None, anneal_iters=0) == 0.01
    assert entropy_coef_at(50, base=0.01, start=None, end=None, anneal_iters=0) == 0.01


def test_entropy_coef_linear_anneal_boundaries_and_monotone():
    def f(it):
        return entropy_coef_at(it, base=0.01, start=0.05, end=0.005, anneal_iters=40)

    assert f(0) == pytest.approx(0.05)
    assert f(40) == pytest.approx(0.005)
    assert f(100) == pytest.approx(0.005)  # clamped past the window
    assert f(10) > f(30)  # monotone non-increasing
    assert f(20) == pytest.approx(0.05 + (0.005 - 0.05) * 0.5)


def test_entropy_coef_at_end_none_anneals_toward_base():
    # When end=None the schedule must anneal from start toward base (not stay flat at start).
    base = 0.01
    start = 0.05
    at0 = entropy_coef_at(0, base=base, start=start, end=None, anneal_iters=40)
    at20 = entropy_coef_at(20, base=base, start=start, end=None, anneal_iters=40)
    at40 = entropy_coef_at(40, base=base, start=start, end=None, anneal_iters=40)
    assert at0 == pytest.approx(start)
    assert at40 == pytest.approx(base)  # converges to base (not stuck at start)
    assert at20 == pytest.approx(start + (base - start) * 0.5)
    assert at0 > at20 > at40  # strictly decreasing


# ---------------------------------------------------------------------------
# Task 4: ReturnNormalizer
# ---------------------------------------------------------------------------
from ml.ppo import ReturnNormalizer  # noqa: E402


def test_return_normalizer_identity_during_warmup():
    norm = ReturnNormalizer(eps=1e-8, warmup=1000)
    r = torch.tensor([1.0, -100.0, 50.0])
    out = norm.normalize(r)
    assert torch.equal(out, r)  # still warming up -> identity


def test_return_normalizer_std_only_scales_without_mean_shift():
    norm = ReturnNormalizer(eps=1e-8, warmup=0)
    r = torch.tensor([2.0, -2.0, 4.0, -4.0])  # mean 0
    out = norm.normalize(r)
    # std-only: divided by running std, NO mean subtraction -> sign preserved, ratios preserved.
    assert torch.all(torch.sign(out) == torch.sign(r))
    assert out[2] / out[0] == pytest.approx(r[2] / r[0])


def test_return_normalizer_no_mean_subtraction_on_nonzero_mean():
    # DISCRIMINATING: zero-mean data passes even for a mean-subtracting normalizer, so use
    # all-positive (non-zero-mean) data. Std-only must keep every output positive and equal
    # to r / (std + eps) exactly; a mean-subtracting normalizer would push the low values
    # negative.
    eps = 1e-8
    norm = ReturnNormalizer(eps=eps, warmup=0)
    r = torch.tensor([3.0, 4.0, 5.0, 6.0])  # mean 4.5, all positive
    out = norm.normalize(r)
    # Welford over this single batch: population variance = m2 / count = var(r, unbiased=False).
    mean = r.mean()
    pop_var = ((r - mean) ** 2).mean()
    std = float(pop_var.item()) ** 0.5
    expected = r / (std + eps)
    assert torch.all(out > 0)  # no mean subtraction -> nothing flipped negative
    assert torch.allclose(out, expected, atol=1e-6)


def test_return_normalizer_eps_floor_finite_on_zero_variance():
    norm = ReturnNormalizer(eps=1e-8, warmup=0)
    out = norm.normalize(torch.zeros(4))
    assert torch.isfinite(out).all()


# ---------------------------------------------------------------------------
# Task 4: ppo_update normalizer wiring
# ---------------------------------------------------------------------------


def test_ppo_update_normalizer_off_does_not_touch_rewards(monkeypatch):
    # When normalize_returns is False, compute_gae sees the raw rewards (normalizer ignored).
    import ml.ppo as ppo

    seen: dict[str, object] = {}
    real_gae = ppo.compute_gae

    def spy(rewards, *a, **k):
        seen["rewards"] = rewards.clone()
        return real_gae(rewards, *a, **k)

    monkeypatch.setattr(ppo, "compute_gae", spy)
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    buf = _filled_buffer(policy)
    raw_rewards = buf.batch()["reward"].clone()
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    ppo_update(policy, opt, buf, PPOConfig(minibatch_size=16, normalize_returns=False))
    # normalizer=None (default) -> rewards arrive unchanged
    assert torch.equal(seen["rewards"], raw_rewards)


def test_ppo_update_normalizer_on_changes_rewards(monkeypatch):
    # When normalize_returns=True and a warm normalizer is passed, compute_gae sees scaled rewards
    # and the scaling ratio matches 1/(std+eps) for the known batch.
    import ml.ppo as ppo

    seen: dict[str, object] = {}
    real_gae = ppo.compute_gae

    def spy(rewards, *a, **k):
        seen["rewards"] = rewards.clone()
        return real_gae(rewards, *a, **k)

    monkeypatch.setattr(ppo, "compute_gae", spy)
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    buf = _filled_buffer(policy)
    raw_rewards = buf.batch()["reward"].clone()
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    eps = 1e-8
    # warmup=0 so normalizer is active immediately
    norm = ReturnNormalizer(eps=eps, warmup=0)
    cfg_on = PPOConfig(minibatch_size=16, normalize_returns=True)
    ppo_update(policy, opt, buf, cfg_on, normalizer=norm)
    # Rewards should have been scaled (not equal to raw unless std=1, very unlikely).
    assert not torch.equal(seen["rewards"], raw_rewards)
    # Verify the scaling matches 1/(std+eps) for nonzero rewards: check that
    # seen_rewards == raw_rewards / (std+eps) element-wise on non-zero entries.
    mean = raw_rewards.mean()
    pop_var = float(((raw_rewards - mean) ** 2).mean().item())
    expected_std = pop_var**0.5
    expected_scale = 1.0 / (expected_std + eps)
    seen_rewards = seen["rewards"]
    assert isinstance(seen_rewards, torch.Tensor)
    expected_scaled = raw_rewards * expected_scale
    assert torch.allclose(seen_rewards, expected_scaled, atol=1e-4), (
        f"scaling mismatch: expected first 5={expected_scaled[:5].tolist()}, "
        f"got {seen_rewards[:5].tolist()}"
    )


def test_ppo_update_raises_when_normalize_returns_true_but_normalizer_none():
    # Loud guard: if normalize_returns=True and no normalizer is supplied → ValueError.
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    buf = _filled_buffer(policy)
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    cfg = PPOConfig(minibatch_size=16, normalize_returns=True)
    with pytest.raises(ValueError, match="normalizer"):
        ppo_update(policy, opt, buf, cfg, normalizer=None)


# ---------------------------------------------------------------------------
# Task 4 (708): VecRolloutBuffer + compute_gae_vec
# ---------------------------------------------------------------------------


def test_compute_gae_vec_matches_per_env_compute_gae():
    import torch

    from ml.ppo import compute_gae, compute_gae_vec

    T, N = 4, 3
    torch.manual_seed(0)
    rewards = torch.randn(T, N)
    values = torch.randn(T, N)
    dones = torch.rand(T, N) > 0.7
    last_values = [0.1, -0.2, 0.3]

    adv_vec, ret_vec = compute_gae_vec(rewards, values, dones, last_values, gamma=0.99, lam=0.95)
    assert adv_vec.shape == (T * N,)
    assert ret_vec.shape == (T * N,)
    # Per-env reference, flattened row-major (t*N + env).
    for env in range(N):
        a, r = compute_gae(
            rewards[:, env], values[:, env], dones[:, env], last_values[env], gamma=0.99, lam=0.95
        )
        for t in range(T):
            assert torch.allclose(adv_vec[t * N + env], a[t])
            assert torch.allclose(ret_vec[t * N + env], r[t])


def test_vecrolloutbuffer_batch_shape_matches_flat():

    from ml.encoding import ACTION_DIM, RASTER_CHANNELS, TOKEN_DIM, ObservationTensors
    from ml.ppo import VecRolloutBuffer

    def _obs():
        import numpy as np

        return ObservationTensors(
            raster=np.zeros((RASTER_CHANNELS, 8, 8), np.float32),
            tokens=np.zeros((4, TOKEN_DIM), np.float32),
            token_mask=np.ones(4, bool),
            active_index=0,
            legal_action_mask=np.ones(ACTION_DIM, bool),
            meta={},
        )

    buf = VecRolloutBuffer(num_envs=2)
    buf.add_step(
        [_obs(), _obs()], [1, 2], [0, 1], [0.0, 0.0], [0.0, 0.0], [1.0, 2.0], [False, True]
    )
    buf.add_step(
        [_obs(), _obs()], [3, 4], [2, 3], [0.0, 0.0], [0.0, 0.0], [3.0, 4.0], [True, False]
    )
    buf.last_value = [0.0, 0.0]
    data = buf.batch()
    assert data["reward"].shape == (4,)  # T*N
    assert data["reward"].tolist() == [1.0, 2.0, 3.0, 4.0]  # row-major (t, env)
    assert set(data) >= {
        "raster",
        "tokens",
        "kind_idx",
        "mag_idx",
        "old_logprob",
        "value",
        "reward",
        "done",
    }


def test_ppo_update_minibatches_all_TN_vec_transitions(monkeypatch):
    """ppo_update must shuffle/minibatch over all T*N vec transitions, not len(buffer)==T.
    Regression for #708 C1: with n=len(buffer) the update silently trained on only the first
    T of T*N rows, dropping (N-1)/N of every rollout."""
    import numpy as np

    from ml.encoding import ACTION_DIM, RASTER_CHANNELS, TOKEN_DIM, ObservationTensors
    from ml.policy import HangarFitPolicy
    from ml.ppo import PPOConfig, VecRolloutBuffer, ppo_update

    def _obs():
        return ObservationTensors(
            raster=np.zeros((RASTER_CHANNELS, 8, 8), np.float32),
            tokens=np.zeros((4, TOKEN_DIM), np.float32),
            token_mask=np.ones(4, bool),
            active_index=0,
            legal_action_mask=np.ones(ACTION_DIM, bool),
            meta={},
        )

    T, N = 3, 4
    buf = VecRolloutBuffer(num_envs=N)
    for _ in range(T):
        buf.add_step(
            [_obs() for _ in range(N)],
            [0] * N,
            [0] * N,
            [0.0] * N,
            [0.0] * N,
            [0.1] * N,
            [False] * N,
        )
    buf.last_value = [0.0] * N

    captured: list[int] = []
    real_randperm = torch.randperm

    def _spy(k, *a, **kw):  # type: ignore[no-untyped-def]
        captured.append(int(k))
        return real_randperm(k, *a, **kw)

    monkeypatch.setattr(torch, "randperm", _spy)

    policy = HangarFitPolicy()
    opt = torch.optim.SGD(policy.parameters(), lr=0.0)  # lr=0: weights frozen, coverage only
    ppo_update(policy, opt, buf, PPOConfig(epochs=1, minibatch_size=64))

    assert captured, "ppo_update did not call torch.randperm"
    assert all(k == T * N for k in captured), f"shuffled {captured}, expected T*N={T * N}"


# ---------------------------------------------------------------------------
# #720 (L4) — PPO trust-region hardening: raw-reward clip, clipped value loss, target-KL
# early-stop. Default-ON since #728 (the validated #720 bundle 50/0.2/0.03); pass None to disable.
# These tame the unclipped -w_col collision spike that drove the gate's -5000..-12000 sawtooth.
# ---------------------------------------------------------------------------
from ml.ppo import value_loss_term  # noqa: E402


def test_ppo_config_l4_knobs_default_on():
    # #728: the validated #720 L4 trust-region bundle is now the PPOConfig default.
    c = PPOConfig()
    assert c.reward_clip == 50.0 and c.value_clip_eps == 0.2 and c.target_kl == 0.03


def test_value_loss_term_unclipped_is_plain_mse():
    nv = torch.tensor([1.0, 2.0, 3.0])
    ov = torch.tensor([0.0, 0.0, 0.0])
    ret = torch.tensor([0.5, 0.5, 0.5])
    assert torch.allclose(value_loss_term(nv, ov, ret, None), ((nv - ret) ** 2).mean())


def test_value_loss_term_clip_bounds_value_movement():
    # new value moved 3.0 from old 0.0; eps=0.5 clamps v_clip to 0.5 -> (0.5-3)^2 = 6.25, and the
    # PPO2 max() picks it over the unclipped 0.0 -> the critic cannot chase returns past the eps.
    nv = torch.tensor([3.0])
    ov = torch.tensor([0.0])
    ret = torch.tensor([3.0])
    assert value_loss_term(nv, ov, ret, 0.5) == pytest.approx(6.25)
    assert value_loss_term(nv, ov, ret, None) == pytest.approx(0.0)


def test_value_loss_term_clip_noop_within_eps():
    # value moved only 0.1 < eps 0.5 -> v_clip == new value -> identical to the unclipped MSE.
    nv = torch.tensor([0.1])
    ov = torch.tensor([0.0])
    ret = torch.tensor([1.0])
    assert value_loss_term(nv, ov, ret, 0.5) == pytest.approx(value_loss_term(nv, ov, ret, None))


def _spy_gae(monkeypatch):
    import ml.ppo as ppo

    seen: dict[str, object] = {}
    real = ppo.compute_gae

    def spy(rewards, *a, **k):
        seen["rewards"] = rewards.clone()
        return real(rewards, *a, **k)

    monkeypatch.setattr(ppo, "compute_gae", spy)
    return seen


def test_ppo_update_reward_clip_clamps_rewards_into_gae(monkeypatch):
    seen = _spy_gae(monkeypatch)
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    buf = _filled_buffer(policy)
    buf.reward[0] = 100.0  # a -w_col-style spike
    buf.reward[1] = -100.0
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    ppo_update(policy, opt, buf, PPOConfig(minibatch_size=16, reward_clip=5.0))
    r = seen["rewards"]
    assert float(r.max()) <= 5.0 and float(r.min()) >= -5.0
    assert float(r[0]) == pytest.approx(5.0) and float(r[1]) == pytest.approx(-5.0)


def test_ppo_update_reward_clip_none_leaves_rewards_raw(monkeypatch):
    seen = _spy_gae(monkeypatch)
    torch.manual_seed(0)
    policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
    buf = _filled_buffer(policy)
    buf.reward[0] = 100.0
    raw = buf.batch()["reward"].clone()
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    ppo_update(policy, opt, buf, PPOConfig(minibatch_size=16, reward_clip=None))
    assert torch.equal(seen["rewards"], raw)


def test_ppo_update_target_kl_early_stops_epochs():
    def epochs_run(target_kl: float | None) -> float:
        torch.manual_seed(0)
        policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
        opt = torch.optim.Adam(policy.parameters(), lr=1e-2)  # big LR -> KL grows fast
        m = ppo_update(
            policy,
            opt,
            _filled_buffer(policy),
            PPOConfig(minibatch_size=16, epochs=4, target_kl=target_kl),
        )
        return m["epochs_run"]

    assert epochs_run(None) == 4  # no early stop -> all epochs run
    assert epochs_run(0.0) == 1  # any positive KL after epoch 1 trips the stop
