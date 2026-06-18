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


def test_collect_rollout_vec_fills_buffer_and_stats():
    import torch

    from ml.encoding import EncoderConfig
    from ml.policy import HangarFitPolicy
    from ml.train import build_trivial_env, collect_rollout_vec
    from ml.vector_env import SyncVectorEnv, _EnvWorker

    torch.manual_seed(0)
    enc = EncoderConfig()
    vec = SyncVectorEnv([_EnvWorker(build_trivial_env(), enc, None) for _ in range(2)])
    policy = HangarFitPolicy()
    buf, stats = collect_rollout_vec(vec, policy, enc, rollout_len=8)
    vec.close()
    assert len(buf) == 8 and buf.num_envs == 2
    assert len(buf.last_value) == 2
    import math

    assert all(math.isfinite(v) for v in buf.last_value)
    # the trivial env completes on PARK, so some episodes finish -> stats with total_reward
    assert all(isinstance(s.total_reward, float) for s in stats)


def test_train_curriculum_n_envs_runs():
    from ml.curriculum import CurriculumSchedule
    from ml.train import train_curriculum

    sched = CurriculumSchedule.default()
    # cap the run tiny: 1 iter per stage, 2 envs, sync backend (no subprocess in CI)
    from dataclasses import replace

    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    hist = train_curriculum(seed=0, schedule=sched, rollout_len=16, n_envs=2, vec_backend="sync")
    assert hist.promotions  # advanced through stages


def test_train_curriculum_subproc_runs():
    """The subproc backend must actually run end-to-end — it spawns workers and PICKLES the
    per-worker factories, which a nested closure would silently break (the sync test cannot
    catch that). Guards the picklable module-level _build_stage_worker contract."""
    from dataclasses import replace

    from ml.curriculum import CurriculumSchedule
    from ml.train import train_curriculum

    sched = replace(
        CurriculumSchedule.default(),
        policy=replace(CurriculumSchedule.default().policy, max_iters=1),
    )
    hist = train_curriculum(seed=0, schedule=sched, rollout_len=8, n_envs=2, vec_backend="subproc")
    assert hist.promotions and hist.iterations  # spawned, pickled, trained


# ---------------------------------------------------------------------------
# #710: --metrics-out per-iter dump + --promotion-metric/--promotion-threshold CLI
# ---------------------------------------------------------------------------


def test_argparser_new_flags_default_none():
    a = build_argparser().parse_args([])
    assert a.promotion_metric is None
    assert a.promotion_threshold is None
    assert a.metrics_out is None


def test_main_applies_promotion_overrides_to_schedule(monkeypatch):
    # main() must thread --promotion-metric/--promotion-threshold/--max-iters-per-stage
    # into the PromotionPolicy it passes to train_curriculum (stubbed so no real training).
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(*, schedule, **kw):
        captured["schedule"] = schedule
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(
        [
            "--schedule",
            "curriculum",
            "--promotion-metric",
            "valid_rate",
            "--promotion-threshold",
            "0.3",
            "--max-iters-per-stage",
            "7",
        ]
    )
    pol = captured["schedule"].policy
    assert pol.metric == "valid_rate"
    assert pol.threshold == pytest.approx(0.3)
    assert pol.max_iters == 7


def test_main_no_override_flags_keeps_default_policy(monkeypatch):
    # default-neutral: with no override flag, the schedule policy is the committed default.
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory, PromotionPolicy

    captured: dict = {}

    def fake(*, schedule, **kw):
        captured["schedule"] = schedule
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum"])
    assert captured["schedule"].policy == PromotionPolicy()


def test_main_writes_metrics_jsonl(monkeypatch, tmp_path):
    import json

    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory, EpisodeStat

    hist = CurriculumHistory()
    hist.record("pair-box", 0, [EpisodeStat(1.0, True, 5.0), EpisodeStat(0.0, False, -1.0)])
    monkeypatch.setattr(train_mod, "train_curriculum", lambda **kw: hist)
    out = tmp_path / "metrics.jsonl"
    train_mod.main(["--schedule", "curriculum", "--metrics-out", str(out)])
    lines = out.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["stage"] == "pair-box"
    assert rec["iter"] == 0
    assert rec["n_eps"] == 2
    assert rec["fraction_placed"] == pytest.approx(0.5)
    assert rec["valid_rate"] == pytest.approx(0.5)
    assert rec["valid_placed"] == pytest.approx(0.5)
    assert rec["mean_ep_reward"] == pytest.approx(2.0)


def test_main_metrics_out_requires_curriculum_schedule(monkeypatch):
    # --metrics-out is curriculum-only; with --schedule trivial it must fail LOUD and
    # BEFORE any training runs (not silently ignore the flag).
    import ml.train as train_mod

    ran = {"train": False}

    def guard(**kw):
        ran["train"] = True
        return []

    monkeypatch.setattr(train_mod, "train", guard)
    with pytest.raises(SystemExit):
        train_mod.main(
            ["--schedule", "trivial", "--metrics-out", "/tmp/should_not_be_written.jsonl"]
        )
    assert ran["train"] is False  # rejected before training


def test_train_curriculum_n_envs_1_matches_legacy_byte_identical():
    """n_envs=1 must reproduce the legacy single-stream training history exactly."""
    from dataclasses import replace

    from ml.curriculum import CurriculumSchedule
    from ml.train import train_curriculum

    default_pol = CurriculumSchedule.default().policy
    sched = replace(CurriculumSchedule.default(), policy=replace(default_pol, max_iters=1))
    legacy = train_curriculum(seed=0, schedule=sched, rollout_len=16)  # default n_envs=1
    again = train_curriculum(seed=0, schedule=sched, rollout_len=16, n_envs=1)
    assert any(eps for _, _, eps in legacy.iterations), "no episodes completed — vacuous equality"
    assert legacy.promotions == again.promotions
    assert legacy.iterations == again.iterations  # CurriculumHistory equality (per-iter records)


# ---------------------------------------------------------------------------
# #710 item 2: --d-model/--n-layers/--n-heads (policy arch) + --epochs/--minibatch-size
# (PPOConfig) CLI flags. Default-neutral: omitting them reproduces today's behaviour.
# ---------------------------------------------------------------------------


def test_argparser_arch_and_ppo_flags_defaults():
    from ml.ppo import PPOConfig

    a = build_argparser().parse_args([])
    # arch flags default None -> policy_kwargs stays None -> HangarFitPolicy own defaults
    assert a.d_model is None
    assert a.n_layers is None
    assert a.n_heads is None
    # epochs/minibatch default = the PPOConfig dataclass defaults (read, not hardcoded)
    assert a.epochs == PPOConfig().epochs
    assert a.minibatch_size == PPOConfig().minibatch_size


def test_main_threads_arch_and_ppo_flags_to_curriculum(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(
        [
            "--schedule",
            "curriculum",
            "--d-model",
            "64",
            "--n-layers",
            "1",
            "--n-heads",
            "2",
            "--epochs",
            "6",
            "--minibatch-size",
            "128",
        ]
    )
    assert captured["policy_kwargs"] == {"d_model": 64, "n_layers": 1, "n_heads": 2}
    assert captured["ppo"].epochs == 6
    assert captured["ppo"].minibatch_size == 128


def test_main_no_arch_flags_default_neutral(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory
    from ml.ppo import PPOConfig

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum"])
    # no arch flags -> policy_kwargs None (own defaults), PPO epochs/minibatch unchanged
    assert captured["policy_kwargs"] is None
    assert captured["ppo"].epochs == PPOConfig().epochs
    assert captured["ppo"].minibatch_size == PPOConfig().minibatch_size


def test_main_threads_partial_arch_flags_to_trivial_train(monkeypatch):
    # The arch flags apply to the trivial path too, and a PARTIAL set yields a
    # policy_kwargs holding only the supplied keys (the rest fall back to defaults).
    import ml.train as train_mod

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(train_mod, "train", fake)
    train_mod.main(["--schedule", "trivial", "--d-model", "64"])
    assert captured["policy_kwargs"] == {"d_model": 64}


# ---------------------------------------------------------------------------
# #710 item 1: --load checkpoint resume (policy + optimizer + normalizer +
# curriculum position). Default-neutral: load=None/checkpoint_out=None = no IO.
# ---------------------------------------------------------------------------


def test_train_curriculum_writes_checkpoint_per_stage(tmp_path):
    from ml.checkpoint import load_checkpoint

    sched = _tiny_schedule(threshold=2.0)  # always caps; max_iters=1 below = fast
    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    ckpt = tmp_path / "ck.pt"
    train_curriculum(seed=0, schedule=sched, rollout_len=8, checkpoint_out=str(ckpt))
    assert ckpt.exists()
    loaded = load_checkpoint(ckpt)
    # both rungs trained -> both recorded as completed, in ladder order
    assert loaded.completed_stages == ["t0", "t1"]


def test_train_curriculum_resume_skips_completed_stages(tmp_path):
    sched = _tiny_schedule(threshold=2.0)
    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    ckpt = tmp_path / "ck.pt"
    train_curriculum(seed=0, schedule=sched, rollout_len=8, checkpoint_out=str(ckpt))
    # Resume from a checkpoint whose completed set covers EVERY rung -> nothing to train.
    resumed = train_curriculum(seed=0, schedule=sched, rollout_len=8, load=str(ckpt))
    assert resumed.iterations == []
    assert resumed.promotions == []


def test_train_curriculum_resume_partial_runs_only_remaining(tmp_path):
    from ml.checkpoint import save_checkpoint
    from ml.policy import HangarFitPolicy
    from ml.ppo import ReturnNormalizer

    sched = _tiny_schedule(threshold=2.0)
    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    # Hand-build a checkpoint that marks ONLY the first rung complete.
    policy = HangarFitPolicy()
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    ckpt = tmp_path / "ck.pt"
    save_checkpoint(
        ckpt,
        policy=policy,
        optimizer=opt,
        normalizer=ReturnNormalizer(),
        policy_kwargs=None,
        completed_stages=["t0"],
    )
    resumed = train_curriculum(seed=0, schedule=sched, rollout_len=8, load=str(ckpt))
    # only the SECOND rung ("t1") runs; "t0" is skipped.
    assert [p[0] for p in resumed.promotions] == ["t1"]
    assert {stage for stage, _, _ in resumed.iterations} == {"t1"}


def test_train_curriculum_resume_arch_mismatch_raises(tmp_path):
    from ml.checkpoint import save_checkpoint
    from ml.policy import HangarFitPolicy

    pk = {"d_model": 32, "n_layers": 1, "n_heads": 2}
    policy = HangarFitPolicy(**pk)
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    ckpt = tmp_path / "ck.pt"
    save_checkpoint(
        ckpt, policy=policy, optimizer=opt, normalizer=None, policy_kwargs=pk, completed_stages=[]
    )
    sched = _tiny_schedule(threshold=2.0)
    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    # resume must REUSE the checkpoint's architecture; a conflicting one fails loud.
    with pytest.raises(ValueError, match="arch|policy_kwargs"):
        train_curriculum(
            seed=0,
            schedule=sched,
            rollout_len=8,
            load=str(ckpt),
            policy_kwargs={"d_model": 64},
        )


def test_train_curriculum_default_no_checkpoint_byte_identical():
    # load=None + checkpoint_out=None must reproduce the no-arg history exactly (no IO path).
    sched = _tiny_default_sched()
    base = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    same = train_curriculum(seed=0, schedule=sched, rollout_len=16, load=None, checkpoint_out=None)
    assert any(eps for _, _, eps in base.iterations), "no episodes completed — vacuous equality"
    assert base.iterations == same.iterations
    assert base.promotions == same.promotions


def test_train_curriculum_resume_warns_on_foreign_completed_stage(tmp_path, capsys):
    # Resuming a checkpoint whose completed rungs are not in the current schedule (a different
    # ladder) is tolerated but must WARN — else the wrong rungs are silently (not) skipped.
    from ml.checkpoint import save_checkpoint
    from ml.policy import HangarFitPolicy

    policy = HangarFitPolicy()
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    ckpt = tmp_path / "ck.pt"
    save_checkpoint(
        ckpt,
        policy=policy,
        optimizer=opt,
        normalizer=None,
        policy_kwargs=None,
        completed_stages=["ghost-rung"],
    )
    sched = _tiny_schedule(threshold=2.0)
    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    train_curriculum(seed=0, schedule=sched, rollout_len=8, load=str(ckpt))
    assert "ghost-rung" in capsys.readouterr().err


def test_train_curriculum_resume_warns_on_normalizer_config_mismatch(tmp_path, capsys):
    # Checkpoint carries normalizer stats but the resume cfg has normalize_returns off -> the
    # saved stats are dropped; that silent divergence must WARN (the multi-day-run footgun).
    from ml.checkpoint import save_checkpoint
    from ml.policy import HangarFitPolicy
    from ml.ppo import PPOConfig, ReturnNormalizer

    policy = HangarFitPolicy()
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    ckpt = tmp_path / "ck.pt"
    save_checkpoint(
        ckpt,
        policy=policy,
        optimizer=opt,
        normalizer=ReturnNormalizer(),
        policy_kwargs=None,
        completed_stages=[],
    )
    sched = _tiny_schedule(threshold=2.0)
    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    train_curriculum(
        seed=0,
        schedule=sched,
        rollout_len=8,
        load=str(ckpt),
        ppo=PPOConfig(normalize_returns=False),
    )
    assert "normaliz" in capsys.readouterr().err.lower()


def test_train_curriculum_resume_inherits_checkpoint_optimizer_lr(tmp_path, monkeypatch):
    # T6: resume restores the optimizer via load_state_dict, which carries the checkpoint's lr
    # (train.py documents this deliberate inheritance). A refactor re-applying cfg.lr after the
    # load would silently violate it — pin the contract with a distinct checkpoint lr.
    import ml.train as train_mod
    from ml.checkpoint import save_checkpoint
    from ml.policy import HangarFitPolicy
    from ml.ppo import PPOConfig

    policy = HangarFitPolicy()
    opt = torch.optim.Adam(policy.parameters(), lr=0.05)  # distinct from cfg.lr below
    ckpt = tmp_path / "ck.pt"
    save_checkpoint(
        ckpt,
        policy=policy,
        optimizer=opt,
        normalizer=None,
        policy_kwargs=None,
        completed_stages=["t0"],
    )
    seen: dict = {}
    real = train_mod.ppo_update

    def spy(pol, optim, *a, **k):
        seen["lr"] = optim.param_groups[0]["lr"]
        return real(pol, optim, *a, **k)

    monkeypatch.setattr(train_mod, "ppo_update", spy)
    sched = _tiny_schedule(threshold=2.0)
    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    train_curriculum(seed=0, schedule=sched, rollout_len=8, load=str(ckpt), ppo=PPOConfig(lr=3e-4))
    assert seen["lr"] == pytest.approx(0.05)  # checkpoint optimizer lr wins, not cfg.lr 3e-4


def test_train_curriculum_resume_same_path_load_and_checkpoint_out(tmp_path):
    # T8: the headline usage — --load PATH and --checkpoint-out PATH the SAME file (read at
    # start, atomically overwritten per rung). Must complete and extend completed_stages.
    from ml.checkpoint import load_checkpoint, save_checkpoint
    from ml.policy import HangarFitPolicy

    policy = HangarFitPolicy()
    opt = torch.optim.Adam(policy.parameters(), lr=3e-4)
    ck = tmp_path / "ck.pt"
    save_checkpoint(
        ck,
        policy=policy,
        optimizer=opt,
        normalizer=None,
        policy_kwargs=None,
        completed_stages=["t0"],
    )
    sched = _tiny_schedule(threshold=2.0)
    sched = replace(sched, policy=replace(sched.policy, max_iters=1))
    hist = train_curriculum(
        seed=0, schedule=sched, rollout_len=8, load=str(ck), checkpoint_out=str(ck)
    )
    assert [p[0] for p in hist.promotions] == ["t1"]  # only the remaining rung ran
    assert load_checkpoint(ck).completed_stages == ["t0", "t1"]  # same-path write extended it


def test_argparser_load_and_checkpoint_out_default_none():
    a = build_argparser().parse_args([])
    assert a.load is None
    assert a.checkpoint_out is None


def test_main_threads_load_and_checkpoint_out_to_curriculum(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(
        ["--schedule", "curriculum", "--load", "/tmp/x.pt", "--checkpoint-out", "/tmp/y.pt"]
    )
    assert captured["load"] == "/tmp/x.pt"
    assert captured["checkpoint_out"] == "/tmp/y.pt"


def test_main_load_requires_curriculum_schedule(monkeypatch):
    # resume needs a curriculum position; --load under --schedule trivial fails LOUD, no training.
    import ml.train as train_mod

    ran = {"train": False}

    def guard(**kw):
        ran["train"] = True
        return []

    monkeypatch.setattr(train_mod, "train", guard)
    with pytest.raises(SystemExit):
        train_mod.main(["--schedule", "trivial", "--load", "/tmp/x.pt"])
    assert ran["train"] is False


def test_main_checkpoint_out_requires_curriculum_schedule(monkeypatch):
    import ml.train as train_mod

    ran = {"train": False}

    def guard(**kw):
        ran["train"] = True
        return []

    monkeypatch.setattr(train_mod, "train", guard)
    with pytest.raises(SystemExit):
        train_mod.main(["--schedule", "trivial", "--checkpoint-out", "/tmp/y.pt"])
    assert ran["train"] is False


# ---------------------------------------------------------------------------
# #710 economics rebalance — --r-unplaced-penalty threads into RewardWeights.
# ---------------------------------------------------------------------------


def test_argparser_r_unplaced_penalty_defaults_zero():
    assert build_argparser().parse_args([]).r_unplaced_penalty == 0.0


def test_main_threads_r_unplaced_penalty_into_weights(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum", "--r-unplaced-penalty", "12.5"])
    assert captured["weights"].r_unplaced_penalty == 12.5


# ---------------------------------------------------------------------------
# CUDA opt-in (--device): default cpu must stay byte-identical; cuda is opt-in.
# ---------------------------------------------------------------------------


def _tiny_default_sched():
    return replace(
        CurriculumSchedule.default(),
        policy=replace(CurriculumSchedule.default().policy, max_iters=1),
    )


def test_train_curriculum_device_cpu_is_byte_identical_to_default():
    # device='cpu' (the default) must reproduce the no-device history exactly — the
    # ADR-0027 / ml-rl-guard determinism contract holds for the CPU path.
    sched = _tiny_default_sched()
    base = train_curriculum(seed=0, schedule=sched, rollout_len=16)
    dev = train_curriculum(seed=0, schedule=sched, rollout_len=16, device="cpu")
    assert any(eps for _, _, eps in base.iterations), "no episodes completed — vacuous equality"
    assert base.promotions == dev.promotions
    assert base.iterations == dev.iterations


@pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a CUDA device")
def test_train_curriculum_device_cuda_runs():
    sched = _tiny_default_sched()
    hist = train_curriculum(seed=0, schedule=sched, rollout_len=16, device="cuda")
    assert hist.promotions and hist.iterations  # trained on GPU end-to-end


@pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a CUDA device")
def test_train_curriculum_device_cuda_save_and_onnx_export_cpu(tmp_path):
    # T1: after --device cuda training the policy is CUDA-resident; --save / --save-onnx must
    # not crash (export traces CPU dummy inputs) and must write CPU-loadable artifacts (the
    # eval/ONNX consumer expects CPU). Pre-fix, export_onnx raised a device-mismatch at run end.
    sched = _tiny_default_sched()
    save = tmp_path / "p.pt"
    onnx = tmp_path / "p.onnx"
    train_curriculum(
        seed=0, schedule=sched, rollout_len=16, device="cuda", save=str(save), save_onnx=str(onnx)
    )
    assert onnx.exists()
    state = torch.load(str(save), map_location="cpu", weights_only=True)
    assert all(v.device.type == "cpu" for v in state.values())  # saved tensors are CPU


def test_argparser_device_defaults_to_cpu():
    assert build_argparser().parse_args([]).device == "cpu"
    assert build_argparser().parse_args(["--device", "cuda"]).device == "cuda"


def test_main_threads_device_to_train_curriculum(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum", "--device", "cpu"])
    assert captured["device"] == "cpu"


def test_main_device_cuda_unavailable_errors(monkeypatch):
    # requesting --device cuda when no GPU is present must fail loud, before training.
    import ml.train as train_mod

    ran = {"train_curriculum": False}

    def guard(**kw):
        ran["train_curriculum"] = True
        from ml.curriculum import CurriculumHistory

        return CurriculumHistory()

    monkeypatch.setattr(train_mod.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(train_mod, "train_curriculum", guard)
    with pytest.raises(SystemExit):
        train_mod.main(["--schedule", "curriculum", "--device", "cuda"])
    assert ran["train_curriculum"] is False
