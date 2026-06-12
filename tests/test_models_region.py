import pytest

from hangarfit.models import RegionPreference


def test_region_preference_valid_right():
    rp = RegionPreference(side="right", weight=1.0)
    assert rp.side == "right"
    assert rp.weight == 1.0


def test_region_preference_zero_weight_allowed():
    assert RegionPreference(side="left", weight=0.0).weight == 0.0


@pytest.mark.parametrize("bad", [-0.1, float("nan"), float("inf")])
def test_region_preference_rejects_bad_weight(bad):
    with pytest.raises(ValueError):
        RegionPreference(side="right", weight=bad)


def test_region_preference_rejects_bad_side():
    with pytest.raises(ValueError):
        RegionPreference(side="up", weight=1.0)  # type: ignore[arg-type]
