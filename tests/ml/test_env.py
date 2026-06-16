"""Tests for the cold-joint RL environment (epic #607 sub-project #1, #672)."""

from __future__ import annotations

import random

import pytest

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


def _two_object_env(**kw):
    """A 2-object env: park ``fuji`` (own-gear), then drive ``aviat_husky``
    (own-gear) so a partial-stop has one object already placed (fraction > 0)."""
    fleet = _fuji()
    return HangarFitEnv(
        hangar=empty_hangar(), fleet=fleet, requested_ids=("fuji", "aviat_husky"), **kw
    )


def test_partial_budget_stop_includes_terminal_fraction_reward():
    # Regression for the final-review #1 gap: a budget-driven (non-Park) stop must
    # still earn r_terminal * (placed/total). Park object 1, then exhaust the
    # per-object budget on object 2 with a pivot (L pivot = no pose change, so the
    # only reward difference vs a non-terminating identical step is the terminal
    # term). per_object_step_budget=1 makes the FIRST step on object 2 terminate.
    pivot = Primitive(kind="L", magnitude=0.1, gear=1)

    # Env A — budget=1: object 2's first step terminates with one parked (frac=0.5).
    env_a = _two_object_env(difficulty=DifficultyConfig(per_object_step_budget=1))
    env_a.reset()
    _, _, done0, _ = env_a.step(Park())  # park fuji on the apron; advance to husky
    assert done0 is False  # one object left to place, so the episode continues
    _, reward_term, done_a, info_a = env_a.step(pivot)
    assert done_a is True and "unplaceable" in info_a.reason
    assert info_a.placed == 1 and info_a.total == 2
    assert info_a.terms["terminal_fraction"] == 0.5

    # Env B — budget high: the identical step on object 2 does NOT terminate.
    env_b = _two_object_env(difficulty=DifficultyConfig(per_object_step_budget=50))
    env_b.reset()
    env_b.step(Park())  # park fuji identically; advance to husky at the same pose
    _, reward_no_term, done_b, _ = env_b.step(pivot)
    assert done_b is False

    # The only reward difference between the two identical steps is the terminal
    # term r_terminal * 0.5 — present in A, absent in B. It must be measurably added.
    w = env_a.weights
    assert reward_term - reward_no_term == w.r_terminal * 0.5


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


# ---------------------------------------------------------------------------
# Final-review #3 — _on_carts is correct for ground objects (towed vs steerable)
# ---------------------------------------------------------------------------
def test_on_carts_for_ground_objects_towed_vs_steerable():
    # Regression: a GroundObject has no movement_mode/on_carts; the old getattr
    # path wrongly resolved every mover to on_carts=False (own-gear, no strafe).
    from hangarfit.loader import load_ground_objects

    gos = load_ground_objects("examples/herrenteich/fleet.yaml")
    env = HangarFitEnv(
        hangar=empty_hangar(),
        fleet=_fuji(),
        requested_ids=("glider_trailer_1",),  # a towed (free-swivel) mover
        ground_objects=gos,
    )
    # A towed trailer is free-swivel cart-like → strafe-eligible.
    assert env._on_carts("glider_trailer_1") is True
    # A steerable car has a positive turning circle → own-gear fan, NOT cart-like.
    assert env._on_carts("vw_caddy") is False
    # A fixed obstacle never moves → never cart-like.
    assert env._on_carts("maul_fuel_trailer") is False

    # And the legal-primitive fan for a towed mover includes the strafe T (#647).
    towed = env._body("glider_trailer_1")
    kinds = {p.kind for p in go.legal_primitives(towed, on_carts=env._on_carts("glider_trailer_1"))}
    assert "T" in kinds


def test_on_carts_for_aircraft_unchanged():
    env = _env()
    # fuji is always_own_gear → not cart-like (the aircraft path is untouched).
    assert env._on_carts("fuji") is False


# ---------------------------------------------------------------------------
# Task 7 (#607 SP#4b) — reset(requested_ids=…) override
# ---------------------------------------------------------------------------
# Reuses the existing _two_object_env(**kw) helper above (fuji + aviat_husky on the
# empty hangar) so we don't shadow it; the reset override is difficulty-agnostic.
def test_reset_none_is_equivalent_to_passing_the_same_ids():
    env = _two_object_env(
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=40, total_step_budget=80)
    )
    obs_default = env.reset()
    obs_explicit = env.reset(requested_ids=("fuji", "aviat_husky"))
    assert obs_default == obs_explicit  # Observation is a frozen dataclass


def test_reset_override_changes_the_requested_set():
    env = _two_object_env()
    env.reset(requested_ids=("aviat_husky",))
    assert env.requested_ids == ("aviat_husky",)


def test_reset_rejects_unknown_id():
    env = _two_object_env()
    with pytest.raises(ValueError):
        env.reset(requested_ids=("nope",))


def test_reset_rejects_empty_requested_ids():
    env = _two_object_env()
    with pytest.raises(ValueError):
        env.reset(requested_ids=())


def test_reset_truncates_requested_ids_beyond_max_objects():
    # max_objects caps the set: requesting more ids than the cap truncates so the episode
    # size (StepInfo.total / fraction_placed) matches what the env actually drives.
    env = _two_object_env(
        difficulty=DifficultyConfig(max_objects=1, per_object_step_budget=40, total_step_budget=40)
    )
    env.reset(requested_ids=("fuji", "aviat_husky"))
    assert env.requested_ids == ("fuji",)  # truncated to max_objects=1
