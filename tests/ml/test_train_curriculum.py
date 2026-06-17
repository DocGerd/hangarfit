"""Torch-gated tests for the curriculum training loop + collect_rollout extension."""

from __future__ import annotations

from dataclasses import replace

import pytest

torch = pytest.importorskip("torch")

from ml.curriculum import DEFAULT_LADDER, EpisodeStat, sample_request, stage_rng  # noqa: E402
from ml.encoding import EncoderConfig  # noqa: E402
from ml.policy import HangarFitPolicy  # noqa: E402
from ml.stage_builder import build_stage_env, effective_fleet_ids  # noqa: E402
from ml.train import collect_rollout, train  # noqa: E402


def test_collect_rollout_returns_episode_stats_with_resampling():
    # Seed torch so the "an untrained policy parks >=1 object within 64 steps"
    # premise is deterministic — without it the result rides on whatever global
    # RNG state the prior test left (a latent order-dependent flake).
    torch.manual_seed(0)
    stage = DEFAULT_LADDER[1]  # pair-box, 2 objects
    env = build_stage_env(stage)
    policy = HangarFitPolicy()
    pool = effective_fleet_ids(stage)
    rng = stage_rng(0, 1)
    buf, stats = collect_rollout(
        env, policy, EncoderConfig(), 64, sample_request=lambda: sample_request(pool, 2, rng)
    )
    assert stats, "at least one episode should complete in 64 steps"
    assert all(isinstance(s, EpisodeStat) for s in stats)
    for s in stats:
        assert 0.0 <= s.fraction_placed <= 1.0


def test_trivial_train_still_runs_with_new_return_type():
    history = train(seed=0, iterations=1, rollout_len=32)
    assert len(history) == 1


from ml.curriculum import (  # noqa: E402
    CurriculumSchedule,
    DifficultyConfig,
    PromotionPolicy,
    Stage,
)
from ml.train import build_argparser, train_curriculum  # noqa: E402


def _tiny_schedule(threshold: float):
    def stage(name, ids):
        return Stage(
            name=name,
            difficulty=DifficultyConfig(
                max_objects=1, per_object_step_budget=12, total_step_budget=12
            ),
            hangar_path="data/hangar.yaml",
            fleet_path="data/fleet.yaml",
            fleet_ids=ids,
            clearance_m=0.05,
        )

    pol = PromotionPolicy(metric="fraction_placed", window=1, threshold=threshold, max_iters=2)
    return CurriculumSchedule(
        stages=(stage("t0", ("fuji",)), stage("t1", ("aviat_husky",))), policy=pol
    )


def test_train_curriculum_is_deterministic():
    sched = _tiny_schedule(threshold=-1.0)  # fraction_placed >= -1 always -> promote by competency
    h1 = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    h2 = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    assert h1.promotions == h2.promotions
    assert h1.iterations == h2.iterations


def test_train_curriculum_promotes_by_competency_then_advances():
    sched = _tiny_schedule(threshold=-1.0)
    h = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    assert [p[0] for p in h.promotions] == ["t0", "t1"]
    assert all(p[2] == "competency" for p in h.promotions)


def test_train_curriculum_promotes_by_cap_when_unreachable():
    sched = _tiny_schedule(threshold=2.0)  # fraction_placed <= 1 < 2 -> never competency
    h = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    assert all(p[2] == "cap" for p in h.promotions)


def test_argparser_schedule_defaults_to_curriculum():
    parser = build_argparser()
    assert parser.parse_args([]).schedule == "curriculum"
    assert parser.parse_args(["--schedule", "trivial"]).schedule == "trivial"


def test_train_curriculum_validates_ladder_eagerly():
    # A rung whose max_objects exceeds the encoder token capacity must fail by name
    # BEFORE any training, not as a deep tensorizer overflow several rungs in.
    bad = Stage(
        name="too-many",
        difficulty=DifficultyConfig(max_objects=999),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji",),
        clearance_m=0.05,
    )
    sched = CurriculumSchedule(stages=(bad,), policy=PromotionPolicy(window=1, max_iters=1))
    with pytest.raises(ValueError):
        train_curriculum(seed=0, schedule=sched, rollout_len=8)


# ---------------------------------------------------------------------------
# Task 4: per-rung entropy re-warm + RewardWeights threading + defaults neutral
# ---------------------------------------------------------------------------


def test_entropy_schedule_rewarms_per_stage_in_train_curriculum(monkeypatch):
    # BEHAVIORAL: prove train_curriculum keys the entropy schedule on the PER-STAGE
    # iteration `it` (resets 0 each rung), not a global counter that would decay to ~end
    # by later rungs. Monkeypatch ppo_update to record the entropy_coef actually applied
    # and collect_rollout to a cheap no-episode stub so the per-stage loop runs its full
    # max_iters without promoting or doing any real torch training.
    import ml.train as train_mod
    from ml.ppo import PPOConfig, RolloutBuffer

    recorded: list[float] = []

    def recorder(policy, optimizer, buf, config, *, normalizer=None):
        recorded.append(config.entropy_coef)
        return {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0, "loss": 0.0}

    def empty_rollout(env, policy, enc, rollout_len, *, sample_request=None):
        return RolloutBuffer(), []  # no completed episodes -> never promotes by competency

    monkeypatch.setattr(train_mod, "ppo_update", recorder)
    monkeypatch.setattr(train_mod, "collect_rollout", empty_rollout)

    max_iters = 3
    # threshold 2.0 + zero episodes -> the window stays empty -> every rung caps at max_iters.
    sched = _tiny_schedule(threshold=2.0)
    sched = CurriculumSchedule(
        stages=sched.stages,
        policy=replace(sched.policy, max_iters=max_iters),
    )
    cfg = PPOConfig(entropy_coef_start=0.05, entropy_coef_end=0.005, entropy_anneal_iters=4)
    train_curriculum(seed=0, schedule=sched, rollout_len=8, ppo=cfg)

    n_stages = len(sched.stages)
    assert len(recorded) == n_stages * max_iters  # full cap each rung, no early promote
    stage0 = recorded[:max_iters]
    stage1 = recorded[max_iters : 2 * max_iters]
    # Each rung RE-WARMS to start at its first iteration (proves per-stage keying):
    assert stage0[0] == pytest.approx(0.05)
    assert stage1[0] == pytest.approx(0.05)
    # ...and decays within each rung (it=0,1,2 -> start + (end-start)*it/4, non-increasing):
    for seq in (stage0, stage1):
        # seq[1:] is intentionally one shorter (consecutive-pair idiom) -> strict=False.
        assert all(later <= earlier for earlier, later in zip(seq, seq[1:], strict=False))
        assert seq[1] < seq[0]  # strictly decaying inside the anneal window
    # If the schedule were keyed on a GLOBAL counter, stage1[0] would be the it=3 value
    # (already decayed), NOT the 0.05 start — this is the discriminating assertion.


def test_build_stage_env_threads_reward_weights():
    from ml.curriculum import CurriculumSchedule
    from ml.stage_builder import build_stage_env
    from ml.types import RewardWeights

    stage = CurriculumSchedule.default().stages[0]
    env = build_stage_env(stage, weights=RewardWeights(r_valid_park=2.0))
    assert env.weights.r_valid_park == 2.0


def test_build_trivial_env_threads_reward_weights():
    from ml.train import build_trivial_env
    from ml.types import RewardWeights

    env = build_trivial_env(seed=0, weights=RewardWeights(r_valid_park=3.0))
    assert env.weights.r_valid_park == 3.0


def test_train_weights_default_neutral():
    # Passing no weights → default RewardWeights() → r_valid_park == 0.0 (neutral)
    from ml.types import RewardWeights

    env_weights = RewardWeights()
    assert env_weights.r_valid_park == 0.0
    history = train(seed=0, iterations=1, rollout_len=16)
    assert len(history) == 1  # runs without error


def test_train_curriculum_weights_default_neutral():
    # weights=None → neutral defaults; trivial schedule with cap-2 iters completes
    sched = _tiny_schedule(threshold=2.0)  # always caps
    h = train_curriculum(seed=0, schedule=sched, rollout_len=8)
    assert len(h.promotions) == 2
