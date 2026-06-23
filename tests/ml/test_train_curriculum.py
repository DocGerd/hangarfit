"""Torch-gated tests for the curriculum training loop + collect_rollout extension."""

from __future__ import annotations

from dataclasses import replace

import pytest

torch = pytest.importorskip("torch")

from ml.curriculum import (  # noqa: E402
    DEFAULT_LADDER,
    EpisodeStat,
    plain_start,
    stage_rng,
)
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
        env, policy, EncoderConfig(), 64, sample_request=lambda: plain_start(pool, 2, rng)
    )
    assert stats, "at least one episode should complete in 64 steps"
    assert all(isinstance(s, EpisodeStat) for s in stats)
    for s in stats:
        assert 0.0 <= s.fraction_placed <= 1.0


def test_trivial_train_still_runs_with_new_return_type():
    history = train(seed=0, iterations=1, rollout_len=32)
    assert len(history) == 1


def test_spatial_tokens_ppo_smoke_trains_without_nan():
    # A handful of PPO iterations on the trivial rung with the ON architecture (#809) must run
    # and return finite rewards — proves the spatial path trains end-to-end (rollout + GAE +
    # update) with no NaN and no terminal-forward crash.
    history = train(
        seed=0,
        iterations=2,
        rollout_len=32,
        policy_kwargs={"spatial_tokens": True, "d_model": 32, "n_layers": 1, "n_heads": 2},
    )
    assert len(history) == 2
    # finite (no NaN/inf): r == r rejects NaN; the magnitude bound rejects inf.
    assert all(isinstance(r, float) and r == r and abs(r) < 1e9 for r in history)


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


def test_train_curriculum_competency_reads_honest_per_iteration_mean_not_episode_tail(monkeypatch):
    # #742 regression: the competency gate must threshold the HONEST per-iteration mean over the
    # WHOLE rollout, not the last-N-episode tail. Feed a rollout whose full-iteration mean is 0.2
    # (below threshold 0.9) but whose trailing 20 episodes spike to 1.0; the rung must CAP, never
    # promote by competency. The pre-#742 last-20-episode deque would have read the 1.0 tail and
    # false-promoted — this test fails loudly if anyone reintroduces a per-episode window.
    import ml.train as train_mod
    from ml.ppo import RolloutBuffer

    ep_stats = [EpisodeStat(0.0, True, 0.0)] * 80 + [EpisodeStat(1.0, True, 0.0)] * 20  # mean 0.2

    def biased_rollout(env, policy, enc, rollout_len, *, sample_request=None):
        return RolloutBuffer(), list(ep_stats)

    monkeypatch.setattr(train_mod, "collect_rollout", biased_rollout)
    monkeypatch.setattr(
        train_mod,
        "ppo_update",
        lambda *a, **k: {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0, "loss": 0.0},
    )

    sched = CurriculumSchedule.default()
    sched = replace(
        sched,
        stages=(sched.stages[0],),  # one rung keeps the run tiny
        policy=PromotionPolicy(metric="fraction_placed", window=1, threshold=0.9, max_iters=2),
    )
    h = train_curriculum(
        seed=0,
        schedule=sched,
        rollout_len=8,
        policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2},
    )
    _name, _it, by = h.promotions[-1]
    assert by == "cap"  # honest mean 0.2 < 0.9 -> never competency, despite the 1.0 episode tail


@pytest.mark.parametrize("min_level,expected_by", [(0.0, "budget-plateau"), (0.05, "cap")])
def test_auto_budget_floor_guard_blocks_premature_stop_in_loop(monkeypatch, min_level, expected_by):
    # #743 wiring: a rung whose honest valid_placed is flat AT THE FLOOR (every rollout places
    # but INVALIDLY -> valid_placed 0.0) must NOT budget-plateau early. With the floor-guard on
    # (min_level=0.05) the rung trains to its cap; with it off (min_level=0.0) the same flat-at-0
    # series early-stops — the contrast proves the loop routes the honest series through the guard.
    import ml.train as train_mod
    from ml.curriculum import BudgetController
    from ml.ppo import RolloutBuffer

    flat_floor = [EpisodeStat(1.0, False, 0.0)] * 30  # placed but invalid -> valid_placed = 0.0

    def floor_rollout(env, policy, enc, rollout_len, *, sample_request=None):
        return RolloutBuffer(), list(flat_floor)

    monkeypatch.setattr(train_mod, "collect_rollout", floor_rollout)
    monkeypatch.setattr(
        train_mod,
        "ppo_update",
        lambda *a, **k: {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0, "loss": 0.0},
    )

    sched = CurriculumSchedule.default()
    sched = replace(
        sched,
        stages=(sched.stages[0],),
        policy=PromotionPolicy(metric="valid_placed", window=1, threshold=2.0, max_iters=5),
    )
    budget = BudgetController(
        min_iters=2, slope_window=2, plateau_patience=1, max_iters=5, eps=1e9, min_level=min_level
    )
    h = train_curriculum(
        seed=0,
        schedule=sched,
        rollout_len=8,
        policy_kwargs={"d_model": 32, "n_layers": 1, "n_heads": 2},
        auto_budget=budget,
    )
    assert h.promotions[-1][2] == expected_by


def test_argparser_schedule_defaults_to_curriculum():
    parser = build_argparser()
    assert parser.parse_args([]).schedule == "curriculum"
    assert parser.parse_args(["--schedule", "trivial"]).schedule == "trivial"


# ---------------------------------------------------------------------------
# #747: --n-envs auto resolver (sched_getaffinity- and MemAvailable-bounded)
# ---------------------------------------------------------------------------


def test_resolve_n_envs_passes_through_explicit_int():
    from ml.train import resolve_n_envs

    assert resolve_n_envs("1") == 1
    assert resolve_n_envs("8") == 8


@pytest.mark.parametrize("bad", ["0", "-3", "foo", "2.5", "", "auto2"])
def test_resolve_n_envs_rejects_non_positive_and_garbage(bad):
    import argparse

    from ml.train import resolve_n_envs

    with pytest.raises(argparse.ArgumentTypeError):
        resolve_n_envs(bad)


def test_resolve_n_envs_auto_is_core_bounded_when_ram_is_ample(monkeypatch):
    import ml.train as t

    monkeypatch.setattr(t, "_available_cores", lambda: 32)
    monkeypatch.setattr(t, "_available_gib", lambda: 1000.0)  # RAM never binds
    assert t.resolve_n_envs("auto") == 32 - t._AUTO_RESERVED_CORES


def test_resolve_n_envs_auto_is_ram_bounded_when_memory_is_tight(monkeypatch):
    import ml.train as t

    monkeypatch.setattr(t, "_available_cores", lambda: 32)
    monkeypatch.setattr(t, "_available_gib", lambda: 12.0)  # tight RAM binds below cores
    n = t.resolve_n_envs("auto")
    expected = max(1, int((12.0 - t._AUTO_RAM_HEADROOM_GIB) / t._AUTO_PER_WORKER_GIB))
    assert n == expected
    assert n < 32 - t._AUTO_RESERVED_CORES  # genuinely RAM-bound, not core-bound


def test_resolve_n_envs_auto_floors_at_one(monkeypatch):
    import ml.train as t

    monkeypatch.setattr(t, "_available_cores", lambda: 1)  # cores - reserved <= 0
    monkeypatch.setattr(t, "_available_gib", lambda: 0.1)
    assert t.resolve_n_envs("auto") == 1


def test_resolve_n_envs_auto_falls_back_to_cores_when_meminfo_missing(monkeypatch):
    import ml.train as t

    monkeypatch.setattr(t, "_available_cores", lambda: 8)
    monkeypatch.setattr(t, "_available_gib", lambda: None)  # /proc/meminfo unreadable
    assert t.resolve_n_envs("auto") == 8 - t._AUTO_RESERVED_CORES


def test_available_cores_prefers_affinity_over_cpu_count(monkeypatch):
    """sched_getaffinity respects cgroup/taskset pinning; os.cpu_count overcounts on
    WSL2/containers. The resolver must use affinity when available."""
    import ml.train as t

    monkeypatch.setattr(t.os, "sched_getaffinity", lambda _pid: {0, 1, 2, 3}, raising=False)
    assert t._available_cores() == 4


def test_argparser_n_envs_default_is_one_byte_identity_floor():
    assert build_argparser().parse_args([]).n_envs == 1


def test_argparser_vec_start_method_defaults_to_spawn():
    """#751: the default stays `spawn` (the byte-identity reference); forkserver/fork are
    opt-in. A bad value is rejected by argparse choices."""
    parser = build_argparser()
    assert parser.parse_args([]).vec_start_method == "spawn"
    assert parser.parse_args(["--vec-start-method", "forkserver"]).vec_start_method == "forkserver"
    assert parser.parse_args(["--vec-start-method", "fork"]).vec_start_method == "fork"
    with pytest.raises(SystemExit):
        parser.parse_args(["--vec-start-method", "bogus"])


def test_argparser_n_envs_auto_resolves_to_positive_int(monkeypatch):
    import ml.train as t

    monkeypatch.setattr(t, "_available_cores", lambda: 8)
    monkeypatch.setattr(t, "_available_gib", lambda: 1000.0)
    ns = build_argparser().parse_args(["--n-envs", "auto"])
    assert isinstance(ns.n_envs, int) and ns.n_envs == 8 - t._AUTO_RESERVED_CORES


def test_argparser_n_envs_rejects_garbage_with_clean_error():
    parser = build_argparser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--n-envs", "nope"])


def test_main_rejects_n_envs_above_one_on_trivial_schedule():
    """--n-envs > 1 (or auto) is a curriculum-only knob; the trivial path is single-env.
    A misdirected value must fail LOUD, not silently run serial."""
    from ml.train import main

    with pytest.raises(SystemExit):
        main(["--schedule", "trivial", "--n-envs", "4", "--iterations", "1", "--rollout-len", "4"])


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

    from ml.encoding import EncoderConfig, static_block
    from ml.policy import HangarFitPolicy
    from ml.train import build_trivial_env, collect_rollout_vec
    from ml.vector_env import SyncVectorEnv, _EnvWorker

    torch.manual_seed(0)
    enc = EncoderConfig()
    vec = SyncVectorEnv([_EnvWorker(build_trivial_env(), enc, None) for _ in range(2)])
    policy = HangarFitPolicy()
    # #752: the rung's cached static block is re-prepended to the trimmed worker obs.
    sb = static_block(build_trivial_env().hangar, enc)
    buf, stats = collect_rollout_vec(vec, policy, enc, rollout_len=8, static_block=sb)
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


def test_argparser_promotion_window_and_auto_budget_floor_default_none():
    a = build_argparser().parse_args([])
    assert a.promotion_window is None
    assert a.auto_budget_min_iters is None
    assert a.auto_budget_min_level is None


def test_main_threads_promotion_window_into_policy(monkeypatch):
    # #742: --promotion-window overrides PromotionPolicy.window (recent ITERATIONS to average).
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(*, schedule, **kw):
        captured["schedule"] = schedule
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum", "--promotion-window", "5"])
    assert captured["schedule"].policy.window == 5


def test_main_threads_auto_budget_floor_knobs(monkeypatch):
    # #743: --auto-budget-min-iters / --auto-budget-min-level build the BudgetController.
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(*, auto_budget, **kw):
        captured["auto_budget"] = auto_budget
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(
        [
            "--schedule",
            "curriculum",
            "--auto-budget",
            "--auto-budget-min-iters",
            "50",
            "--auto-budget-min-level",
            "0.1",
        ]
    )
    b = captured["auto_budget"]
    assert b is not None
    assert b.min_iters == 50
    assert b.min_level == pytest.approx(0.1)


def test_main_auto_budget_floor_knobs_require_auto_budget():
    # the floor knobs are inert without --auto-budget: fail LOUD (like --auto-budget-max-iters),
    # never silently drop a typed numeric flag.
    import ml.train as train_mod

    with pytest.raises(SystemExit):
        train_mod.main(["--schedule", "curriculum", "--auto-budget-min-iters", "50"])
    with pytest.raises(SystemExit):
        train_mod.main(["--schedule", "curriculum", "--auto-budget-min-level", "0.1"])


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


# ---------------------------------------------------------------------------
# #714 — validity-conditional terminal + solo-box rung CLI wiring.
# ---------------------------------------------------------------------------


def test_argparser_validity_conditional_terminal_defaults_false():
    assert build_argparser().parse_args([]).validity_conditional_terminal is False


def test_main_threads_validity_conditional_terminal_into_weights(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum", "--validity-conditional-terminal"])
    assert captured["weights"].validity_conditional_terminal is True


def test_main_solo_box_rung_requires_curriculum_schedule(monkeypatch):
    # --solo-box-rung is a curriculum ladder edit; under --schedule trivial it fails LOUD.
    import ml.train as train_mod

    ran = {"train": False}

    def guard(**kw):
        ran["train"] = True
        return []

    monkeypatch.setattr(train_mod, "train", guard)
    with pytest.raises(SystemExit):
        train_mod.main(["--schedule", "trivial", "--solo-box-rung"])
    assert ran["train"] is False


def test_main_solo_box_rung_inserts_rung_into_schedule(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum", "--solo-box-rung"])
    assert "solo-box" in [s.name for s in captured["schedule"].stages]


def test_main_without_solo_box_rung_keeps_default_ladder(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum"])
    assert "solo-box" not in [s.name for s in captured["schedule"].stages]


# ---------------------------------------------------------------------------
# #712 — --seed-anchor CLI wiring (the pair-anchored start-state graft).
# ---------------------------------------------------------------------------


def test_argparser_seed_anchor_defaults_false():
    assert build_argparser().parse_args([]).seed_anchor is False


def test_main_seed_anchor_requires_curriculum_schedule(monkeypatch):
    # --seed-anchor is a curriculum ladder edit; under --schedule trivial it fails LOUD.
    import ml.train as train_mod

    ran = {"train": False}

    def guard(**kw):
        ran["train"] = True
        return []

    monkeypatch.setattr(train_mod, "train", guard)
    with pytest.raises(SystemExit):
        train_mod.main(["--schedule", "trivial", "--seed-anchor"])
    assert ran["train"] is False


def test_main_seed_anchor_inserts_rung_into_schedule(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum", "--seed-anchor"])
    assert "pair-anchored" in [s.name for s in captured["schedule"].stages]


def test_main_without_seed_anchor_keeps_default_ladder(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum"])
    assert "pair-anchored" not in [s.name for s in captured["schedule"].stages]


# ---------------------------------------------------------------------------
# #712 — --mixed-anchor CLI wiring (the pair-mixed start-state rung, Task 6).
# ---------------------------------------------------------------------------


def test_mixed_anchor_flag_inserts_pair_mixed(monkeypatch):
    # Reuse this module's helper that builds the schedule from argv as the seed-anchor
    # test does; assert "pair-mixed" appears only when --mixed-anchor is passed.
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured_off: dict = {}
    captured_on: dict = {}

    def fake_off(**kw):
        captured_off.update(kw)
        return CurriculumHistory()

    def fake_on(**kw):
        captured_on.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake_off)
    train_mod.main(["--schedule", "curriculum"])
    assert "pair-mixed" not in [s.name for s in captured_off["schedule"].stages]

    monkeypatch.setattr(train_mod, "train_curriculum", fake_on)
    train_mod.main(["--schedule", "curriculum", "--seed-anchor", "--mixed-anchor"])
    names = [s.name for s in captured_on["schedule"].stages]
    assert "pair-mixed" in names
    assert names.index("pair-anchored") < names.index("pair-mixed") < names.index("pair-box")


# ---------------------------------------------------------------------------
# #722 — --stop-after-rung CLI wiring (truncate the ladder for sweep cells).
# ---------------------------------------------------------------------------


def test_argparser_stop_after_rung_defaults_none():
    assert build_argparser().parse_args([]).stop_after_rung is None


def test_main_stop_after_rung_requires_curriculum_schedule(monkeypatch):
    # --stop-after-rung is a curriculum ladder edit; under --schedule trivial it fails LOUD.
    import ml.train as train_mod

    ran = {"train": False}

    def guard(**kw):
        ran["train"] = True
        return []

    monkeypatch.setattr(train_mod, "train", guard)
    with pytest.raises(SystemExit):
        train_mod.main(["--schedule", "trivial", "--stop-after-rung", "pair-box"])
    assert ran["train"] is False


def test_main_stop_after_rung_truncates_schedule(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum", "--stop-after-rung", "pair-box"])
    names = [s.name for s in captured["schedule"].stages]
    assert names == ["trivial", "pair-box"]  # trio-* dropped


def test_main_without_stop_after_rung_keeps_full_ladder(monkeypatch):
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum"])
    assert captured["schedule"].stages[-1].name == "trio-notch-strict"  # full ladder


def test_main_stop_after_rung_composes_with_grafts(monkeypatch):
    # The intended sweep shape: graft the opt-in rungs, then stop after pair-box.
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
            "--solo-box-rung",
            "--seed-anchor",
            "--mixed-anchor",
            "--stop-after-rung",
            "pair-box",
        ]
    )
    names = [s.name for s in captured["schedule"].stages]
    assert names == ["trivial", "solo-box", "pair-anchored", "pair-mixed", "pair-box"]


def test_main_stop_after_rung_unknown_rung_errors(monkeypatch):
    # A typo'd rung name fails LOUD (the truncate_after_rung ValueError surfaces) rather than
    # silently disabling the cap and grinding the whole ladder.
    import ml.train as train_mod

    monkeypatch.setattr(train_mod, "train_curriculum", lambda **kw: None)
    with pytest.raises(ValueError, match="no-such-rung"):
        train_mod.main(["--schedule", "curriculum", "--stop-after-rung", "no-such-rung"])


def _anchored_smoke_schedule(name: str = "pair-anchored-smoke"):
    anchored = Stage(
        name=name,
        difficulty=DifficultyConfig(
            max_objects=2, seed_anchor_k=1, per_object_step_budget=30, total_step_budget=30
        ),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        anchor_layout_path="tests/fixtures/ml/witness_box.yaml",
        clearance_m=0.05,
    )
    return CurriculumSchedule(
        stages=(anchored,),
        # threshold>1 => never promote by competency => the rung runs its single capped iter.
        policy=PromotionPolicy(metric="fraction_placed", window=1, threshold=2.0, max_iters=1),
    )


def test_train_curriculum_trains_an_anchored_rung_end_to_end():
    # End-to-end: train_curriculum runs an anchored rung — the witness pool feeds sample_request,
    # the env pre-parks the k=1 prefix, and collect_rollout + ppo_update consume the partial-start
    # episodes. Every completed episode must report fraction_placed >= 0.5 (the k=1 anchor is
    # counted in the denominator), which only holds if the anchor was actually pre-parked —
    # without it a place-nothing episode would be 0/2 = 0.0.
    hist = train_curriculum(seed=0, schedule=_anchored_smoke_schedule(), rollout_len=32)
    eps = [s for _, _, eps in hist.iterations for s in eps]
    assert any(name == "pair-anchored-smoke" for name, _, _ in hist.iterations)
    assert eps, "the smoke must complete at least one episode"
    assert all(s.fraction_placed >= 0.5 for s in eps)  # anchor counted -> floor 1/2


def test_train_curriculum_anchored_rung_is_deterministic():
    # Anchoring adds no RNG, so the same seed reproduces the anchored run bit-for-bit (the
    # determinism contract, mirrored from test_train_curriculum_is_deterministic).
    h1 = train_curriculum(seed=0, schedule=_anchored_smoke_schedule("pa-det"), rollout_len=32)
    h2 = train_curriculum(seed=0, schedule=_anchored_smoke_schedule("pa-det"), rollout_len=32)
    assert h1.iterations == h2.iterations


@pytest.mark.parametrize("backend", ["sync", "subproc"])
def test_train_curriculum_anchored_rung_runs_vectorized(backend):
    # The anchored Stage (now carrying anchor_layout_path) must pickle across the spawn
    # boundary and the witness must reload IN the worker — exercise both vec backends.
    hist = train_curriculum(
        seed=0,
        schedule=_anchored_smoke_schedule(f"pa-vec-{backend}"),
        rollout_len=32,
        n_envs=2,
        vec_backend=backend,
    )
    assert any(name == f"pa-vec-{backend}" for name, _, _ in hist.iterations)


# ---------------------------------------------------------------------------
# #720 (L5+L4) — graded-economics weights + PPO trust-region knobs thread through main().
# The L5 economics knobs stay default-neutral; the three L4 trust-region knobs are default-ON
# since #728 (graduated to the validated #720 bundle 50/0.2/0.03), with --no-* off-switches.
# ---------------------------------------------------------------------------


def test_argparser_l5_l4_knobs_defaults():
    from ml.ppo import PPOConfig
    from ml.types import RewardWeights

    a = build_argparser().parse_args([])
    assert a.w_col == RewardWeights().w_col  # default 100.0, read from the dataclass not hardcoded
    assert a.valid_park_grade_scale == 0.0
    assert a.r_first_valid == 0.0
    # #728: the L4 trust-region knobs default to the validated #720 bundle.
    assert a.reward_clip == 50.0
    assert a.value_clip_eps == 0.2
    assert a.target_kl == 0.03
    # The PPOConfig dataclass agrees (argparse default == dataclass default).
    assert PPOConfig().reward_clip == 50.0
    assert PPOConfig().value_clip_eps == 0.2
    assert PPOConfig().target_kl == 0.03


def _capture_main(monkeypatch, argv: list[str]) -> dict:
    import ml.train as train_mod
    from ml.curriculum import CurriculumHistory

    captured: dict = {}

    def fake(**kw):
        captured.update(kw)
        return CurriculumHistory()

    monkeypatch.setattr(train_mod, "train_curriculum", fake)
    train_mod.main(["--schedule", "curriculum", *argv])
    return captured


def test_main_threads_l5_knobs_into_weights(monkeypatch):
    w = _capture_main(
        monkeypatch,
        ["--w-col", "20.0", "--valid-park-grade-scale", "4.0", "--r-first-valid", "15.0"],
    )["weights"]
    assert w.w_col == 20.0
    assert w.valid_park_grade_scale == 4.0
    assert w.r_first_valid == 15.0


def test_main_threads_l4_knobs_into_ppo(monkeypatch):
    ppo = _capture_main(
        monkeypatch,
        ["--reward-clip", "10.0", "--value-clip-eps", "0.2", "--target-kl", "0.03"],
    )["ppo"]
    assert ppo.reward_clip == 10.0
    assert ppo.value_clip_eps == 0.2
    assert ppo.target_kl == 0.03


def test_argparser_l4_off_switches_disable():
    # #728: the three --no-* off-switches restore the disabled (None) behavior — the seed-1
    # clip-OFF A/B control needs this, since there is no in-band "off" value.
    a = build_argparser().parse_args(["--no-reward-clip", "--no-value-clip-eps", "--no-target-kl"])
    assert a.reward_clip is None
    assert a.value_clip_eps is None
    assert a.target_kl is None


def test_argparser_l4_off_switch_last_wins():
    # An explicit value then --no-* disables (argparse last-wins on the shared dest).
    a = build_argparser().parse_args(["--reward-clip", "30.0", "--no-reward-clip"])
    assert a.reward_clip is None


def test_main_threads_l4_off_switches_into_ppo(monkeypatch):
    ppo = _capture_main(monkeypatch, ["--no-reward-clip", "--no-value-clip-eps", "--no-target-kl"])[
        "ppo"
    ]
    assert ppo.reward_clip is None
    assert ppo.value_clip_eps is None
    assert ppo.target_kl is None


def test_main_no_flags_l5_neutral_l4_default_on(monkeypatch):
    # #728: an unflagged run keeps the L5 economics weights neutral but now carries the
    # default-ON L4 trust-region bundle through to PPOConfig.
    from ml.types import RewardWeights

    captured = _capture_main(monkeypatch, [])
    w = captured["weights"]
    assert w.w_col == RewardWeights().w_col
    assert w.valid_park_grade_scale == 0.0
    assert w.r_first_valid == 0.0
    ppo = captured["ppo"]
    assert ppo.reward_clip == 50.0
    assert ppo.value_clip_eps == 0.2
    assert ppo.target_kl == 0.03


def test_r_valid_progress_flag_defaults_neutral():
    # #812: the per-commitment economics knob defaults 0.0 (byte-identical) and parses a value.
    parser = build_argparser()
    assert parser.parse_args([]).r_valid_progress == 0.0
    assert parser.parse_args(["--r-valid-progress", "8.0"]).r_valid_progress == 8.0


def test_main_threads_r_valid_progress_into_weights(monkeypatch):
    # #812: --r-valid-progress flows all the way into the RewardWeights used for training.
    w = _capture_main(monkeypatch, ["--r-valid-progress", "8.0"])["weights"]
    assert w.r_valid_progress == 8.0
