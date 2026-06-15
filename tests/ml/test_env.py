"""Tests for the cold-joint RL environment (epic #607 sub-project #1, #672)."""

from __future__ import annotations

import random

from ml import geometry_oracle as go
from ml.env import HangarFitEnv
from ml.types import DifficultyConfig, Park, Primitive
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


# ---------------------------------------------------------------------------
# Task 11 — _potential
# ---------------------------------------------------------------------------
def test_potential_reflects_slot_distance_and_unplaced():
    env = _env()
    env.reset()
    phi0 = env._potential()
    # Φ is the NEGATIVE of (overlap + slot-distance + unplaced); after reset the
    # active object sits on the apron (y<0) with one unplaced, so Φ is strictly
    # negative — distinguishing the real Φ from the temporary 0.0 stub.
    assert phi0 < 0.0


# ---------------------------------------------------------------------------
# Task 12 — step (transition + reward + termination)
# ---------------------------------------------------------------------------
def test_step_primitive_moves_active_and_returns_reward():
    env = _env()
    env.reset()
    obs, reward, done, info = env.step(Primitive(kind="S", magnitude=1.0, gear=1))
    assert isinstance(reward, float)
    assert done is False
    assert obs.active is not None and obs.active.pose.y_m > -env.hangar.apron_depth_m
    assert "hard_overlap" in info.terms and isinstance(info.terms["hard_overlap"], float)


def test_park_advances_to_next_object_or_finishes():
    env = _env()  # single requested object
    env.reset()
    # Drive in until y>=1 then park.
    for _ in range(20):
        if env._active_pose is not None and env._active_pose.y_m >= 1.0:
            break
        env.step(Primitive(kind="S", magnitude=1.0, gear=1))
    obs, reward, done, info = env.step(Park())
    assert done is True  # the only object was parked
    assert info.placed == info.total == 1


# ---------------------------------------------------------------------------
# Task 13 — curriculum max_objects + per-object partial-stop termination
# ---------------------------------------------------------------------------
def test_max_objects_caps_the_requested_set():
    fleet = _fuji()
    env = HangarFitEnv(
        hangar=empty_hangar(),
        fleet=fleet,
        requested_ids=tuple(fleet),
        difficulty=DifficultyConfig(max_objects=1),
    )
    env.reset()
    assert len(env._queue) + 1 == 1  # exactly one object in play


def test_per_object_budget_terminates_with_partial():
    env = _env(difficulty=DifficultyConfig(per_object_step_budget=2))
    env.reset()
    env.step(Primitive(kind="L", magnitude=0.1, gear=1))
    obs, reward, done, info = env.step(Primitive(kind="L", magnitude=0.1, gear=1))
    assert done is True and "unplaceable" in info.reason


# ---------------------------------------------------------------------------
# Task 14 — integration: random-policy rollout + RNG-free reward determinism
# ---------------------------------------------------------------------------
def _rollout(env: HangarFitEnv, actions: list) -> float:
    env.reset()
    total = 0.0
    for a in actions:
        _, r, done, _ = env.step(a)
        total += r
        if done:
            break
    return total


def test_random_rollout_completes_and_is_bounded():
    env = _env(difficulty=DifficultyConfig(per_object_step_budget=10, total_step_budget=40))
    rng = random.Random(0)
    fan = list(go.legal_primitives(env._body(env.requested_ids[0]), on_carts=False)) + [Park()]
    actions = [rng.choice(fan) for _ in range(40)]
    total = _rollout(env, actions)
    assert isinstance(total, float)


def test_reward_is_rng_free_for_a_fixed_action_sequence():
    actions: list = [Primitive(kind="S", magnitude=1.0, gear=1)] * 5 + [Park()]
    a = _rollout(_env(), actions)
    b = _rollout(_env(), actions)
    assert a == b  # byte-identical reward for identical actions (ADR-0027 env tier)
