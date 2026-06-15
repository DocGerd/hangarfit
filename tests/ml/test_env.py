"""Tests for the cold-joint RL environment (epic #607 sub-project #1, #672)."""

from __future__ import annotations

from ml.env import HangarFitEnv
from tests.ml.conftest import _fuji, empty_hangar


def test_ml_package_importable():
    import ml

    assert ml.__doc__ is not None


def _env(**kw):
    fleet = _fuji()
    # Request fuji (always_own_gear) so the Park-time Layout validates cleanly
    # (an always_cart glider as the first fleet key could trip cart-pool validation).
    return HangarFitEnv(hangar=empty_hangar(), fleet=fleet, requested_ids=("fuji",), **kw)


# ---------------------------------------------------------------------------
# Task 10 — HangarFitEnv.reset
# ---------------------------------------------------------------------------
def test_reset_spawns_first_object_on_the_apron():
    env = _env()
    obs = env.reset()
    assert obs.active is not None
    assert obs.active.pose.y_m < 0.0  # spawned on the apron (y<0)
    assert obs.parked == ()
    assert len(obs.unplaced_ids) == 0  # the active one is not "unplaced"
