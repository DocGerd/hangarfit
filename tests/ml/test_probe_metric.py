import pytest

from ml.probe_metric import marginal_completion


def test_place_nothing_floor_reads_zero_marginal():
    # k=2 of n=3 pre-parked => a place-nothing/abstain policy reads valid_placed = 2/3 exactly,
    # which is the pre-registered null: MARGINAL completion = 0 (it never parked the last object).
    assert marginal_completion(2.0 / 3.0, n=3, k=2) == pytest.approx(0.0, abs=1e-9)


def test_full_completion_reads_one():
    assert marginal_completion(1.0, n=3, k=2) == pytest.approx(1.0)


def test_partial_completion_is_linear_above_floor():
    # valid_placed = 0.7667 => marginal = 3*0.7667 - 2 = 0.30 (the GO threshold).
    assert marginal_completion(0.76667, n=3, k=2) == pytest.approx(0.30, abs=1e-3)


def test_invalid_piling_below_floor_clamps_to_zero():
    # An invalid-pile policy reads valid_placed BELOW the 2/3 floor; marginal clamps to 0
    # (no spurious negative, no false GO).
    assert marginal_completion(0.5, n=3, k=2) == 0.0


def test_rejects_non_drive_one():
    with pytest.raises(ValueError):
        marginal_completion(0.8, n=3, k=1)  # drive=2: the affine transform does not apply
