"""Pure unit tests for ml/curriculum.py — no torch, no disk."""

from __future__ import annotations

import random

import pytest

from ml.curriculum import (
    _BOX_FLEET,
    _BOX_HANGAR,
    _PAIR_MIXED_STAGE,
    _SOLO_BOX_STAGE,
    _WITNESS_BOX,
    DEFAULT_LADDER,
    CurriculumHistory,
    CurriculumSchedule,
    EpisodeStart,
    EpisodeStat,
    PromotionPolicy,
    Stage,
    episode_metrics,
    format_iter_log,
    history_metric_records,
    make_episode_sampler,
    plain_start,
    sample_mixed_start,
    sample_request,
    should_promote,
    stage_rng,
    truncate_after_rung,
    validate_ladder,
    with_mixed_anchor_rung,
    with_pair_anchored_rung,
    with_promotion_overrides,
    with_solo_box_rung,
    with_trio_notch_anchored_rung,
)
from ml.encoding import EncoderConfig
from ml.types import DifficultyConfig


def test_should_promote_fires_when_iteration_mean_meets_threshold():
    # should_promote reads the per-ITERATION honest-metric series — each element is one PPO
    # iteration's full-rollout metric mean — NOT a per-episode tail (the #742 fix). The metric
    # credit rule (valid_placed/valid_rate/fraction_placed) is applied upstream when the series
    # is built, so the gate itself only thresholds the windowed mean of those iteration scores.
    pol = PromotionPolicy(window=2, threshold=0.5)
    assert should_promote([0.4, 0.8], pol) is True  # mean 0.6 >= 0.5


def test_should_promote_false_below_threshold():
    pol = PromotionPolicy(window=2, threshold=0.9)
    assert should_promote([0.4, 0.8], pol) is False  # mean 0.6 < 0.9


def test_should_promote_waits_for_full_window():
    pol = PromotionPolicy(window=3, threshold=0.0)
    assert should_promote([1.0], pol) is False  # only 1 iteration < window 3


def test_should_promote_uses_last_window_only():
    # old low iterations must NOT drag down a recently-mastered window
    pol = PromotionPolicy(window=2, threshold=0.95)
    assert should_promote([0.0, 1.0, 1.0], pol) is True  # last 2 both 1.0


def test_should_promote_does_not_fire_on_subthreshold_iteration_mean():
    # THE #742 regression: a rung whose honest per-iteration mean sits ~0.65 must NOT promote
    # at threshold 0.9, however noisy the underlying per-episode tail is — the gate now reads
    # the full-rollout per-iteration mean, not a lucky last-20-episode spike that read >= 0.9.
    history = [0.62, 0.71, 0.65, 0.68, 0.64]  # every iteration's honest mean ~0.65, all < 0.9
    pol = PromotionPolicy(window=3, threshold=0.9)
    assert should_promote(history, pol) is False


def test_sample_request_is_deterministic_for_equal_rngs():
    pool = ("a", "b", "c", "d", "e")
    assert sample_request(pool, 3, stage_rng(0, 0)) == sample_request(pool, 3, stage_rng(0, 0))


def test_sample_request_size_membership_no_dupes():
    pool = ("a", "b", "c", "d")
    got = sample_request(pool, 2, stage_rng(1, 0))
    assert len(got) == 2
    assert len(set(got)) == 2
    assert set(got) <= set(pool)


def test_sample_request_raises_when_n_exceeds_pool():
    with pytest.raises(ValueError):
        sample_request(("a", "b"), 3, stage_rng(0, 0))


def test_stage_rng_keyed_by_stage_index():
    # different ladder positions => different stream => (near-certainly) different draw
    assert stage_rng(0, 0).random() != stage_rng(0, 1).random()


def test_stage_rng_keyed_by_seed():
    assert stage_rng(0, 0).random() != stage_rng(1, 0).random()


def _stage(
    name="s", n=1, hangar="data/hangar.yaml", fleet="data/fleet.yaml", ids=None, clearance=0.05
):
    return Stage(
        name=name,
        difficulty=DifficultyConfig(max_objects=n, per_object_step_budget=40, total_step_budget=40),
        hangar_path=hangar,
        fleet_path=fleet,
        fleet_ids=ids,
        clearance_m=clearance,
    )


def test_validate_ladder_accepts_a_valid_ladder():
    validate_ladder((_stage(n=1), _stage(n=2)), encoder_max_objects=16)  # no raise


def test_validate_ladder_rejects_empty():
    with pytest.raises(ValueError):
        validate_ladder((), encoder_max_objects=16)


def test_validate_ladder_rejects_max_objects_over_encoder_cap():
    with pytest.raises(ValueError):
        validate_ladder((_stage(n=17),), encoder_max_objects=16)


def test_validate_ladder_rejects_none_or_nonpositive_max_objects():
    bad = Stage(
        name="bad",
        difficulty=DifficultyConfig(max_objects=None),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
    )
    with pytest.raises(ValueError):
        validate_ladder((bad,), encoder_max_objects=16)


def test_stage_defaults():
    s = _stage()
    assert s.apron_depth_m == 8.0
    assert s.wing_layer_clearance_m is None


def test_curriculum_history_records_and_notes():
    h = CurriculumHistory()
    h.record("s0", 0, [EpisodeStat(0.5, False, -1.0)])
    h.note_promotion("s0", 3, by="competency")
    assert h.iterations == [("s0", 0, (EpisodeStat(0.5, False, -1.0),))]
    assert h.promotions == [("s0", 3, "competency")]


def test_curriculum_schedule_default_is_the_committed_ladder():
    sched = CurriculumSchedule.default()
    assert sched.stages == DEFAULT_LADDER
    assert isinstance(sched.policy, PromotionPolicy)


def test_default_ladder_has_five_named_rungs_spanning_three_dimensions():
    names = tuple(s.name for s in DEFAULT_LADDER)
    assert names == ("trivial", "pair-box", "trio-box", "trio-notch", "trio-notch-strict")
    # count ramps
    assert [s.difficulty.max_objects for s in DEFAULT_LADDER] == [1, 2, 2 + 1, 3, 3]
    # hangar shape changes at trio-notch
    assert DEFAULT_LADDER[2].hangar_path != DEFAULT_LADDER[3].hangar_path
    # clearance tightens at the final rung (lenient override -> file value)
    assert DEFAULT_LADDER[3].clearance_m == 0.05
    assert DEFAULT_LADDER[4].clearance_m is None  # inherits herrenteich file (0.10)


def test_default_ladder_passes_validation():
    validate_ladder(DEFAULT_LADDER, encoder_max_objects=EncoderConfig().max_objects)  # no raise


def test_promotion_policy_rejects_nonpositive_window():
    with pytest.raises(ValueError):
        PromotionPolicy(window=0)


def test_promotion_policy_rejects_nonpositive_max_iters():
    with pytest.raises(ValueError):
        PromotionPolicy(max_iters=0)


def test_promotion_policy_allows_out_of_range_threshold():
    # threshold > 1 / <= 0 are valid "never/always promote by competency" levers.
    PromotionPolicy(threshold=2.0)
    PromotionPolicy(threshold=-1.0)


def test_stage_rejects_negative_clearance():
    with pytest.raises(ValueError):
        Stage(
            name="bad",
            difficulty=DifficultyConfig(max_objects=1),
            hangar_path="data/hangar.yaml",
            fleet_path="data/fleet.yaml",
            clearance_m=-0.1,
        )


def test_stage_rejects_negative_apron():
    with pytest.raises(ValueError):
        Stage(
            name="bad",
            difficulty=DifficultyConfig(max_objects=1),
            hangar_path="data/hangar.yaml",
            fleet_path="data/fleet.yaml",
            apron_depth_m=-1.0,
        )


def test_default_promotion_metric_is_valid_placed():
    assert PromotionPolicy().metric == "valid_placed"


def test_default_promotion_window_counts_iterations_not_episodes():
    # #742: the window is now a count of recent ITERATIONS (each an honest full-rollout mean),
    # not the last-N completed episodes. The default is a small smoothing window because the
    # per-iteration mean is already low-variance (it averages a whole rollout's episodes).
    assert PromotionPolicy().window == 3


def test_stage_rng_worker_index_zero_is_legacy_and_distinct():
    from ml.curriculum import stage_rng

    base = stage_rng(7, 2)  # legacy 2-arg call
    w0 = stage_rng(7, 2, worker_index=0)  # must match legacy exactly
    assert [base.random() for _ in range(5)] == [w0.random() for _ in range(5)]
    # different workers => different streams
    w1 = stage_rng(7, 2, worker_index=1)
    w0b = stage_rng(7, 2, worker_index=0)
    assert [w0b.random() for _ in range(5)] != [w1.random() for _ in range(5)]


# --- #710 per-iter metrics + promotion-override plumbing (all pure / torch-free) ---


def test_episode_metrics_computes_the_four_rates():
    # one fully-placed+valid, one half-placed+invalid, one fully-placed+valid
    stats = [
        EpisodeStat(1.0, True, 10.0),
        EpisodeStat(0.5, False, -2.0),
        EpisodeStat(1.0, True, 8.0),
    ]
    m = episode_metrics(stats)
    assert m["n_eps"] == 3
    assert m["fraction_placed"] == pytest.approx((1.0 + 0.5 + 1.0) / 3)
    assert m["valid_rate"] == pytest.approx(2 / 3)
    # valid_placed credits fraction_placed only on the valid episodes: (1.0 + 0 + 1.0) / 3
    assert m["valid_placed"] == pytest.approx((1.0 + 1.0) / 3)
    assert m["mean_ep_reward"] == pytest.approx((10.0 - 2.0 + 8.0) / 3)


def test_episode_metrics_empty_is_n0_with_none_rates():
    # no episode finished this iteration -> 0 episodes, None (not 0.0) rates, so a short
    # rollout is not mistaken for a genuine zero in the learning curve.
    m = episode_metrics([])
    assert m["n_eps"] == 0
    assert m["mean_ep_reward"] is None
    assert m["fraction_placed"] is None
    assert m["valid_rate"] is None
    assert m["valid_placed"] is None


def test_episode_metrics_valid_placed_feeds_should_promote_as_one_iteration_score():
    # episode_metrics(...)["valid_placed"] IS one element of the honest series the gate reads:
    # feed it as a 1-element history and the window=1 gate fires exactly at that value. This
    # pins the contract that the metric the JSONL/ml.gate report is the metric the gate uses.
    stats = [EpisodeStat(0.8, True, 0.0), EpisodeStat(0.9, False, 0.0), EpisodeStat(0.4, True, 0.0)]
    score = episode_metrics(stats)["valid_placed"]
    expected = (0.8 + 0.0 + 0.4) / 3
    assert score == pytest.approx(expected)
    assert isinstance(score, float)
    assert should_promote([score], PromotionPolicy(window=1, threshold=expected))
    assert not should_promote([score], PromotionPolicy(window=1, threshold=expected + 0.01))


def test_history_metric_records_one_record_per_recorded_iteration():
    h = CurriculumHistory()
    h.record("pair-box", 0, [EpisodeStat(1.0, True, 5.0), EpisodeStat(0.0, False, -1.0)])
    h.record("pair-box", 1, [])  # an iteration with no completed episode
    recs = history_metric_records(h)
    assert [r["stage"] for r in recs] == ["pair-box", "pair-box"]
    assert [r["iter"] for r in recs] == [0, 1]
    assert recs[0]["valid_placed"] == pytest.approx(0.5)  # (1.0 + 0) / 2
    assert recs[0]["n_eps"] == 2
    assert recs[1]["n_eps"] == 0 and recs[1]["valid_placed"] is None


def test_history_metric_records_empty_history_is_empty_list():
    assert history_metric_records(CurriculumHistory()) == []


def test_with_promotion_overrides_all_none_is_default_neutral():
    base = PromotionPolicy()
    assert with_promotion_overrides(base) == base  # no override -> equal policy


def test_format_iter_log_surfaces_all_four_metrics_live():
    # the curriculum log line must carry valid_placed so a `python -u` long run is
    # monitorable mid-flight (the CLI used to print only mean_ep_reward).
    stats = [EpisodeStat(1.0, True, 4.0), EpisodeStat(0.0, False, -1.0)]
    line = format_iter_log("pair-box", 7, stats)
    assert "[pair-box]" in line
    assert "7" in line
    assert "valid_placed=0.500" in line
    assert "valid_rate=0.500" in line
    assert "fraction_placed=0.500" in line
    assert "mean_ep_reward=+1.500" in line  # (4.0 - 1.0) / 2
    assert "n_eps=2" in line


def test_format_iter_log_empty_iteration_has_no_phantom_zero():
    # no completed episode -> report n_eps=0, NOT valid_placed=0.000 (which would read as a
    # genuine zero rather than "no data this iteration").
    line = format_iter_log("trio-box", 3, [])
    assert "n_eps=0" in line
    assert "valid_placed" not in line


def test_with_promotion_overrides_sets_only_given_fields():
    base = PromotionPolicy(metric="valid_placed", window=20, threshold=0.9, max_iters=200)
    got = with_promotion_overrides(base, metric="valid_rate", threshold=0.3, max_iters=120)
    assert got.metric == "valid_rate"
    assert got.threshold == pytest.approx(0.3)
    assert got.max_iters == 120
    assert got.window == 20  # untouched field preserved


def test_with_promotion_overrides_sets_window():
    # #742: --promotion-window threads through with_promotion_overrides like the other levers.
    base = PromotionPolicy(window=3)
    assert with_promotion_overrides(base, window=8).window == 8
    assert with_promotion_overrides(base).window == 3  # None -> unchanged (default-neutral)


# ---------------------------------------------------------------------------
# #714 — solo-box sub-curriculum rung (Lever B), opt-in / flag-gated
# ---------------------------------------------------------------------------


def test_default_ladder_has_no_solo_box_rung():
    # Byte-identity guard: solo-box is opt-in; the default ladder is unchanged.
    assert all(s.name != "solo-box" for s in DEFAULT_LADDER)


def test_with_solo_box_rung_inserts_after_trivial():
    sched = with_solo_box_rung(CurriculumSchedule.default())
    names = [s.name for s in sched.stages]
    assert names[:3] == ["trivial", "solo-box", "pair-box"]


def test_solo_box_rung_is_single_object_whole_fleet_pool():
    sched = with_solo_box_rung(CurriculumSchedule.default())
    solo = next(s for s in sched.stages if s.name == "solo-box")
    assert solo.difficulty.max_objects == 1  # still one object...
    assert solo.fleet_ids is None  # ...but the WHOLE-fleet pool (trivial pins fleet_ids=("fuji",))


def test_with_solo_box_rung_preserves_policy_and_validates():
    base = CurriculumSchedule.default()
    sched = with_solo_box_rung(base)
    assert sched.policy == base.policy  # only the ladder changes, not the promotion gate
    validate_ladder(sched.stages, encoder_max_objects=EncoderConfig().max_objects)  # no raise


def test_with_solo_box_rung_does_not_mutate_the_default_ladder():
    with_solo_box_rung(CurriculumSchedule.default())
    assert all(s.name != "solo-box" for s in DEFAULT_LADDER)  # default still pristine


def test_with_solo_box_rung_raises_without_trivial_rung():
    # No 'trivial' rung => no anchor to insert after => loud ValueError (not a leaked
    # StopIteration from the internal next()).
    no_trivial = CurriculumSchedule(
        stages=tuple(s for s in DEFAULT_LADDER if s.name != "trivial"),
        policy=PromotionPolicy(),
    )
    with pytest.raises(ValueError, match="trivial"):
        with_solo_box_rung(no_trivial)


# ---------------------------------------------------------------------------
# #712 — seed-anchor start-state curriculum graft: DifficultyConfig.seed_anchor_k
# ---------------------------------------------------------------------------


def test_difficulty_config_seed_anchor_k_defaults_zero():
    # #712: replaces the unwired seed_anchor:bool stub. 0 => no anchored objects
    # => byte-identical to the pre-#712 env reset.
    assert DifficultyConfig().seed_anchor_k == 0


def test_difficulty_config_seed_anchor_k_is_settable():
    assert DifficultyConfig(seed_anchor_k=1).seed_anchor_k == 1
    assert DifficultyConfig(seed_anchor_k=2).seed_anchor_k == 2


# ---------------------------------------------------------------------------
# #712 — the opt-in 'pair-anchored' rung (--seed-anchor)
# ---------------------------------------------------------------------------


def test_default_ladder_has_no_pair_anchored_rung():
    # Byte-identity guard: pair-anchored is opt-in; the default ladder is unchanged.
    assert all(s.name != "pair-anchored" for s in DEFAULT_LADDER)


def test_with_pair_anchored_rung_inserts_before_pair_box():
    sched = with_pair_anchored_rung(CurriculumSchedule.default())
    names = [s.name for s in sched.stages]
    assert names[:3] == ["trivial", "pair-anchored", "pair-box"]


def test_pair_anchored_rung_is_two_object_k1_with_a_witness():
    sched = with_pair_anchored_rung(CurriculumSchedule.default())
    rung = next(s for s in sched.stages if s.name == "pair-anchored")
    assert rung.difficulty.max_objects == 2  # two objects in the set...
    assert rung.difficulty.seed_anchor_k == 1  # ...one pre-parked, one driven in
    assert rung.anchor_layout_path is not None  # the committed witness layout


def test_with_pair_anchored_rung_preserves_policy_and_validates():
    base = CurriculumSchedule.default()
    sched = with_pair_anchored_rung(base)
    assert sched.policy == base.policy  # only the ladder changes, not the promotion gate
    validate_ladder(sched.stages, encoder_max_objects=EncoderConfig().max_objects)  # no raise


def test_with_pair_anchored_rung_does_not_mutate_the_default_ladder():
    with_pair_anchored_rung(CurriculumSchedule.default())
    assert all(s.name != "pair-anchored" for s in DEFAULT_LADDER)  # default still pristine


def test_with_pair_anchored_rung_raises_without_pair_box():
    no_pair_box = CurriculumSchedule(
        stages=tuple(s for s in DEFAULT_LADDER if s.name != "pair-box"),
        policy=PromotionPolicy(),
    )
    with pytest.raises(ValueError, match="pair-box"):
        with_pair_anchored_rung(no_pair_box)


def test_seed_anchor_composes_with_solo_box_rung():
    # The two opt-in levers stack: solo-box after trivial, pair-anchored before pair-box.
    sched = with_pair_anchored_rung(with_solo_box_rung(CurriculumSchedule.default()))
    names = [s.name for s in sched.stages]
    assert names[:4] == ["trivial", "solo-box", "pair-anchored", "pair-box"]


# #736 — the opt-in witness-anchored notch trio rung (trio-notch-anchored)


def test_default_ladder_has_no_trio_notch_anchored_rung():
    # Byte-identity guard: trio-notch-anchored is opt-in; the default ladder is unchanged.
    assert all(s.name != "trio-notch-anchored" for s in DEFAULT_LADDER)


def test_with_trio_notch_anchored_rung_inserts_before_trio_notch():
    sched = with_trio_notch_anchored_rung(CurriculumSchedule.default())
    names = [s.name for s in sched.stages]
    i = names.index("trio-notch")
    assert names[i - 1] == "trio-notch-anchored"


def test_trio_notch_anchored_rung_is_three_object_k1_on_the_notch_with_a_witness():
    sched = with_trio_notch_anchored_rung(CurriculumSchedule.default())
    rung = next(s for s in sched.stages if s.name == "trio-notch-anchored")
    assert rung.difficulty.max_objects == 3  # three objects in the set...
    assert rung.difficulty.seed_anchor_k == 1  # ...one pre-parked, two driven in
    assert rung.anchor_layout_path is not None  # the committed notch witness layout
    # Scaffolds the SAME real notch hangar as the empty-start trio-notch it precedes.
    trio_notch = next(s for s in sched.stages if s.name == "trio-notch")
    assert rung.hangar_path == trio_notch.hangar_path
    assert rung.clearance_m == trio_notch.clearance_m  # same lenient 0.05 clearance


def test_with_trio_notch_anchored_rung_preserves_policy_and_validates():
    base = CurriculumSchedule.default()
    sched = with_trio_notch_anchored_rung(base)
    assert sched.policy == base.policy  # only the ladder changes, not the promotion gate
    validate_ladder(sched.stages, encoder_max_objects=EncoderConfig().max_objects)  # no raise


def test_with_trio_notch_anchored_rung_does_not_mutate_the_default_ladder():
    with_trio_notch_anchored_rung(CurriculumSchedule.default())
    assert all(s.name != "trio-notch-anchored" for s in DEFAULT_LADDER)  # default still pristine


def test_with_trio_notch_anchored_rung_raises_without_trio_notch():
    no_trio_notch = CurriculumSchedule(
        stages=tuple(s for s in DEFAULT_LADDER if s.name != "trio-notch"),
        policy=PromotionPolicy(),
    )
    with pytest.raises(ValueError, match="trio-notch"):
        with_trio_notch_anchored_rung(no_trio_notch)


def test_trio_notch_anchored_composes_with_pair_anchored_rung():
    # The anchor levers stack independently: pair-anchored before pair-box, trio-notch-anchored
    # before trio-notch — and the default ladder stays pristine under both grafts.
    sched = with_trio_notch_anchored_rung(with_pair_anchored_rung(CurriculumSchedule.default()))
    names = [s.name for s in sched.stages]
    assert names.index("pair-anchored") < names.index("pair-box")
    assert names.index("trio-notch-anchored") == names.index("trio-notch") - 1


def _anchored_test_stage(*, max_objects: int, seed_anchor_k: int) -> Stage:
    return Stage(
        name="bad",
        difficulty=DifficultyConfig(max_objects=max_objects, seed_anchor_k=seed_anchor_k),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
    )


def test_validate_ladder_rejects_negative_seed_anchor_k():
    bad = (_anchored_test_stage(max_objects=2, seed_anchor_k=-1),)
    with pytest.raises(ValueError, match="seed_anchor_k"):
        validate_ladder(bad, encoder_max_objects=EncoderConfig().max_objects)


def test_validate_ladder_rejects_seed_anchor_k_ge_max_objects():
    # k must leave >=1 object to drive; a pre-flight catch (not a mid-training reset failure).
    bad = (_anchored_test_stage(max_objects=2, seed_anchor_k=2),)
    with pytest.raises(ValueError, match="seed_anchor_k"):
        validate_ladder(bad, encoder_max_objects=EncoderConfig().max_objects)


def test_validate_ladder_accepts_valid_seed_anchor_k():
    ok = (_anchored_test_stage(max_objects=2, seed_anchor_k=1),)
    validate_ladder(ok, encoder_max_objects=EncoderConfig().max_objects)  # no raise


# ---------------------------------------------------------------------------
# #718 — EpisodeStart record + plain/mixed episode samplers
# ---------------------------------------------------------------------------


def test_plain_start_wraps_sample_request_with_no_anchor():
    rng = random.Random(0)
    s = plain_start(("fuji", "aviat_husky"), 2, rng)
    assert isinstance(s, EpisodeStart)
    assert set(s.requested_ids) == {"fuji", "aviat_husky"}
    assert s.seed_anchor_k is None


def test_mixed_start_draws_k_deterministically():
    pool = ("fuji", "aviat_husky")
    a = [
        sample_mixed_start(pool, 2, random.Random(7), seed_anchor_k=1, anchor_prob=0.5)
        for _ in range(1)
    ]
    b = [
        sample_mixed_start(pool, 2, random.Random(7), seed_anchor_k=1, anchor_prob=0.5)
        for _ in range(1)
    ]
    assert a[0] == b[0]  # same seed -> identical (ids AND k)
    assert a[0].seed_anchor_k in (0, 1)


def test_mixed_start_k_is_zero_or_seed_anchor_k_by_prob():
    pool = ("fuji", "aviat_husky")
    rng = random.Random(0)
    ks = [
        sample_mixed_start(pool, 2, rng, seed_anchor_k=1, anchor_prob=p).seed_anchor_k
        for p in (0.0,) * 50
    ]
    assert set(ks) == {0}  # prob 0 -> always empty start
    rng = random.Random(0)
    ks = [
        sample_mixed_start(pool, 2, rng, seed_anchor_k=1, anchor_prob=p).seed_anchor_k
        for p in (1.0,) * 50
    ]
    assert set(ks) == {1}  # prob 1 -> always anchored


def test_mixed_start_mixture_fraction_near_anchor_prob():
    pool = ("fuji", "aviat_husky")
    rng = random.Random(123)
    draws = [
        sample_mixed_start(pool, 2, rng, seed_anchor_k=1, anchor_prob=0.5).seed_anchor_k
        for _ in range(2000)
    ]
    frac_anchored = sum(1 for k in draws if k == 1) / len(draws)
    assert 0.45 <= frac_anchored <= 0.55  # ~0.5 mixture


# ---------------------------------------------------------------------------
# #712 mixed-rung ladder validation: anchor_prob field + validate_ladder guards
# ---------------------------------------------------------------------------


def _mixed_stage(anchor_prob, anchor_path: str | None = _WITNESS_BOX):
    return Stage(
        name="pair-mixed",
        difficulty=DifficultyConfig(max_objects=2, seed_anchor_k=1, anchor_prob=anchor_prob),
        hangar_path=_BOX_HANGAR,
        fleet_path=_BOX_FLEET,
        anchor_layout_path=anchor_path,
    )


def test_validate_ladder_accepts_valid_mixed_rung():
    validate_ladder([_mixed_stage(0.5)], encoder_max_objects=8)  # no raise


@pytest.mark.parametrize("p", [-0.1, 1.1])
def test_validate_ladder_rejects_anchor_prob_out_of_range(p):
    with pytest.raises(ValueError, match="anchor_prob"):
        validate_ladder([_mixed_stage(p)], encoder_max_objects=8)


def test_validate_ladder_rejects_mixed_rung_without_witness():
    with pytest.raises(ValueError, match="anchor_layout_path"):
        validate_ladder([_mixed_stage(0.5, anchor_path=None)], encoder_max_objects=8)


def test_validate_ladder_rejects_mixed_rung_with_one_object():
    # A mixed rung needs room for both a k=1 and a k=0 draw (spec §4.4). seed_anchor_k=0
    # keeps the pre-existing seed_anchor_k < max_objects guard satisfied so the new
    # max_objects >= 2 guard is what fires.
    one_object_mixed = Stage(
        name="pair-mixed",
        difficulty=DifficultyConfig(max_objects=1, seed_anchor_k=0, anchor_prob=0.5),
        hangar_path=_BOX_HANGAR,
        fleet_path=_BOX_FLEET,
        anchor_layout_path=_WITNESS_BOX,
    )
    with pytest.raises(ValueError, match="max_objects >= 2"):
        validate_ladder([one_object_mixed], encoder_max_objects=8)


# ---------------------------------------------------------------------------
# with_mixed_anchor_rung builder tests (#712)
# ---------------------------------------------------------------------------


def test_with_mixed_anchor_rung_inserts_before_pair_box():
    sched = with_mixed_anchor_rung(CurriculumSchedule.default())
    names = [s.name for s in sched.stages]
    assert "pair-mixed" in names
    assert names.index("pair-mixed") == names.index("pair-box") - 1


def test_mixed_rung_sits_between_pair_anchored_and_pair_box():
    sched = with_mixed_anchor_rung(with_pair_anchored_rung(CurriculumSchedule.default()))
    names = [s.name for s in sched.stages]
    assert names.index("pair-anchored") < names.index("pair-mixed") < names.index("pair-box")


def test_mixed_rung_config_is_two_object_anchor_prob_half():
    sched = with_mixed_anchor_rung(CurriculumSchedule.default())
    rung = next(s for s in sched.stages if s.name == "pair-mixed")
    assert rung.difficulty.max_objects == 2
    assert rung.difficulty.seed_anchor_k == 1
    assert rung.difficulty.anchor_prob == 0.5
    assert rung.anchor_layout_path is not None  # reuses witness_box


def test_default_ladder_untouched_by_mixed_builder():
    before = tuple(s.name for s in DEFAULT_LADDER)
    with_mixed_anchor_rung(CurriculumSchedule.default())
    assert tuple(s.name for s in DEFAULT_LADDER) == before  # no mutation


def test_with_mixed_anchor_rung_raises_without_pair_box():
    sched = CurriculumSchedule(stages=(DEFAULT_LADDER[0],), policy=PromotionPolicy())
    with pytest.raises(ValueError, match="pair-box"):
        with_mixed_anchor_rung(sched)


# ---------------------------------------------------------------------------
# make_episode_sampler (#718 Task 6)
# ---------------------------------------------------------------------------


def test_make_episode_sampler_plain_for_non_mixed_stage():
    # solo-box has anchor_prob None -> plain sampler -> seed_anchor_k None, byte-identical ids.
    pool = ("fuji",)
    rng1, rng2 = random.Random(3), random.Random(3)
    s = make_episode_sampler(_SOLO_BOX_STAGE, pool, 1, rng1)()
    assert s.seed_anchor_k is None
    assert s.requested_ids == sample_request(pool, 1, rng2)  # same rng draw


def test_make_episode_sampler_mixed_for_mixed_stage_varies_k():
    pool = ("fuji", "aviat_husky")
    rng = random.Random(5)
    sampler = make_episode_sampler(_PAIR_MIXED_STAGE, pool, 2, rng)
    ks = {sampler().seed_anchor_k for _ in range(200)}
    assert ks == {0, 1}  # mixture draws both


# ---------------------------------------------------------------------------
# #722 — truncate_after_rung: stop the ladder after a named rung (sweep tooling)
# ---------------------------------------------------------------------------


def test_truncate_after_rung_drops_later_stages():
    # Truncating the default ladder at 'pair-box' keeps trivial+pair-box and drops the
    # trio-* rungs after it — the #722 lever that lets a resumed sweep cell stop cleanly.
    sched = truncate_after_rung(CurriculumSchedule.default(), "pair-box")
    names = [s.name for s in sched.stages]
    assert names == ["trivial", "pair-box"]


def test_truncate_after_rung_keeps_named_rung_last():
    sched = truncate_after_rung(CurriculumSchedule.default(), "trio-notch")
    names = [s.name for s in sched.stages]
    assert names[-1] == "trio-notch"
    assert "trio-notch-strict" not in names  # the rung after it is dropped


def test_truncate_after_rung_at_last_rung_is_a_noop_on_stages():
    base = CurriculumSchedule.default()
    sched = truncate_after_rung(base, "trio-notch-strict")  # the last rung
    assert [s.name for s in sched.stages] == [s.name for s in base.stages]


def test_truncate_after_rung_preserves_policy_and_validates():
    base = CurriculumSchedule.default()
    sched = truncate_after_rung(base, "pair-box")
    assert sched.policy == base.policy  # only the ladder changes, not the promotion gate
    validate_ladder(sched.stages, encoder_max_objects=EncoderConfig().max_objects)  # no raise


def test_truncate_after_rung_does_not_mutate_the_default_ladder():
    truncate_after_rung(CurriculumSchedule.default(), "pair-box")
    assert [s.name for s in DEFAULT_LADDER][-1] == "trio-notch-strict"  # default still full


def test_truncate_after_rung_raises_on_unknown_rung():
    with pytest.raises(ValueError, match="no-such-rung"):
        truncate_after_rung(CurriculumSchedule.default(), "no-such-rung")


def test_truncate_after_rung_composes_with_grafts():
    # The intended #722 sweep shape: graft the opt-in rungs, then truncate at 'pair-box'
    # so the resumed cell trains only up to (and including) pair-box.
    sched = with_mixed_anchor_rung(
        with_pair_anchored_rung(with_solo_box_rung(CurriculumSchedule.default()))
    )
    truncated = truncate_after_rung(sched, "pair-box")
    names = [s.name for s in truncated.stages]
    assert names == ["trivial", "solo-box", "pair-anchored", "pair-mixed", "pair-box"]


def test_truncate_after_rung_at_pair_mixed_for_upstream_train():
    # The upstream train stops after pair-mixed (before the empty-start pair-box).
    sched = with_mixed_anchor_rung(
        with_pair_anchored_rung(with_solo_box_rung(CurriculumSchedule.default()))
    )
    names = [s.name for s in truncate_after_rung(sched, "pair-mixed").stages]
    assert names == ["trivial", "solo-box", "pair-anchored", "pair-mixed"]


def test_truncate_after_rung_at_trio_box_is_the_gate_sweep_shape():
    # #730: the trio-box gate-sweep cell resumes the grafted ladder and stops AFTER
    # trio-box — dropping trio-notch/-strict so a resumed cell does not grind on past the
    # rung under test. This is the exact ladder shape the GPU sweep launch produces.
    sched = with_mixed_anchor_rung(
        with_pair_anchored_rung(with_solo_box_rung(CurriculumSchedule.default()))
    )
    truncated = truncate_after_rung(sched, "trio-box")
    names = [s.name for s in truncated.stages]
    assert names == ["trivial", "solo-box", "pair-anchored", "pair-mixed", "pair-box", "trio-box"]
    trio = truncated.stages[-1]
    assert trio.name == "trio-box"
    assert trio.difficulty.max_objects == 3  # the ≥2-object rung the four-lever ladder must clear
