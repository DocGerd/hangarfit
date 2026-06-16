"""Pure unit tests for ml/curriculum.py — no torch, no disk."""

from __future__ import annotations

import pytest

from ml.curriculum import (
    EpisodeStat,
    PromotionPolicy,
    sample_request,
    should_promote,
    stage_rng,
)


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
