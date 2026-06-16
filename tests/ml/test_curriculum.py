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
    sample_request,
    should_promote,
    stage_rng,
    validate_ladder,
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
