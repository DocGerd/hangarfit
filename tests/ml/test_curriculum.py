"""Pure unit tests for ml/curriculum.py — no torch, no disk."""

from __future__ import annotations

import pytest

from ml.curriculum import (
    DEFAULT_LADDER,
    CurriculumHistory,
    CurriculumSchedule,
    EpisodeStat,
    PromotionPolicy,
    Stage,
    episode_metrics,
    format_iter_log,
    history_metric_records,
    sample_request,
    should_promote,
    stage_rng,
    validate_ladder,
    with_promotion_overrides,
    with_solo_box_rung,
)
from ml.encoding import EncoderConfig
from ml.types import DifficultyConfig


def test_should_promote_fires_when_windowed_mean_meets_threshold():
    pol = PromotionPolicy(metric="fraction_placed", window=2, threshold=0.5)
    window = [EpisodeStat(0.4, False, -1.0), EpisodeStat(0.8, True, 1.0)]  # mean 0.6 >= 0.5
    assert should_promote(window, pol) is True


def test_should_promote_false_below_threshold():
    pol = PromotionPolicy(metric="fraction_placed", window=2, threshold=0.9)
    window = [EpisodeStat(0.4, False, -1.0), EpisodeStat(0.8, True, 1.0)]  # mean 0.6 < 0.9
    assert should_promote(window, pol) is False


def test_should_promote_waits_for_full_window():
    pol = PromotionPolicy(window=3, threshold=0.0)
    assert should_promote([EpisodeStat(1.0, True, 1.0)], pol) is False  # only 1 < window 3


def test_should_promote_uses_last_window_only():
    pol = PromotionPolicy(metric="fraction_placed", window=2, threshold=0.95)
    # old low episodes must NOT drag down a recently-mastered window
    window = [
        EpisodeStat(0.0, False, 0.0),
        EpisodeStat(1.0, True, 0.0),
        EpisodeStat(1.0, True, 0.0),
    ]
    assert should_promote(window, pol) is True  # last 2 both 1.0


def test_should_promote_valid_rate_metric():
    pol = PromotionPolicy(metric="valid_rate", window=2, threshold=1.0)
    assert should_promote([EpisodeStat(1.0, True, 0.0), EpisodeStat(0.5, False, 0.0)], pol) is False
    assert should_promote([EpisodeStat(0.1, True, 0.0), EpisodeStat(0.2, True, 0.0)], pol) is True


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


def test_should_promote_valid_placed_credits_only_valid_episodes():
    pol = PromotionPolicy(metric="valid_placed", window=2, threshold=0.5)
    # both fully placed, but only one valid -> mean(1.0, 0.0) = 0.5 >= 0.5
    assert should_promote([EpisodeStat(1.0, True, 0.0), EpisodeStat(1.0, False, 0.0)], pol) is True
    # both fully placed but BOTH invalid -> mean(0.0, 0.0) = 0.0 < 0.5
    assert (
        should_promote([EpisodeStat(1.0, False, 0.0), EpisodeStat(1.0, False, 0.0)], pol) is False
    )


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


def test_episode_metrics_valid_placed_matches_should_promote_scoring():
    # the per-iter valid_placed must use the SAME credit rule as the promotion gate
    stats = [EpisodeStat(0.8, True, 0.0), EpisodeStat(0.9, False, 0.0), EpisodeStat(0.4, True, 0.0)]
    expected = (0.8 + 0.0 + 0.4) / 3
    assert episode_metrics(stats)["valid_placed"] == pytest.approx(expected)
    # and the gate fires exactly when that mean meets threshold
    assert should_promote(
        stats, PromotionPolicy(metric="valid_placed", window=3, threshold=expected)
    )
    assert not should_promote(
        stats, PromotionPolicy(metric="valid_placed", window=3, threshold=expected + 0.01)
    )


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
