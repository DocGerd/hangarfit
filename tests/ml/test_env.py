"""Tests for the cold-joint RL environment (epic #607 sub-project #1, #672)."""

from __future__ import annotations

import random

import pytest

from hangarfit.models import GroundObject, Part, Placement
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


def test_parked_version_zero_then_bumps_on_park_then_resets():
    env = _two_object_env(
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=40, total_step_budget=80)
    )
    env.reset()
    assert env._parked_version == 0
    env.step(Park())  # park object 1 -> version bumps (validity irrelevant)
    assert env._parked_version == 1
    env.reset()
    assert env._parked_version == 0


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


def test_parked_out_of_bounds_layout_is_invalid():
    # Park the lone object immediately, while it is still on the apron (y < 0): no overlap
    # (single object) but out of hangar bounds. The tightened valid predicate must report
    # invalid (the old overlap-only predicate wrongly said valid=True). #607 SP#4b review.
    env = _env()
    env.reset()
    _, _, done, info = env.step(Park())
    assert done is True and info.placed == 1
    assert info.valid is False  # parked on the apron / out of bounds


def test_env_layout_valid_delegates_to_product_checker():
    # At reset, no objects are parked yet (_layout() is effectively empty of aircraft).
    # The Layout is structurally valid (no overlaps, no out-of-bounds placements).
    # The expected value is True for a clean empty state (not a tautology — it asserts
    # the predicate returns a concrete expected result on a known-good state).
    env = HangarFitEnv(hangar=empty_hangar(), fleet=_fuji(), requested_ids=("fuji",))
    env.reset()
    assert env._layout_valid() is True


# ---------------------------------------------------------------------------
# Task 2 (#607 SP#4c-ii / #693) — fixed-obstacle pre-placement
# ---------------------------------------------------------------------------
def _fuel_trailer() -> GroundObject:
    # Minimal fixed obstacle; mirrors the catalog maul_fuel_trailer shape closely
    # enough. GroundObject carries a `parts` tuple of kind="ground" Parts (no
    # length_m/width_m/height_m fields) — Part positional args after kind are
    # length/width/offset_x/offset_y/angle/z_bottom/z_top (verified vs
    # tests/test_scene.py:541). A fixed_obstacle carries no motion_mode/hard_door_mover.
    return GroundObject(
        id="fuel",
        name="Fuel trailer",
        parts=(Part("ground", 2.0, 1.5, 0.0, 0.0, 0.0, 0.0, 1.2),),
        object_class="fixed_obstacle",
    )


def test_fixed_obstacle_in_layout_not_parked_and_fraction_uncorrupted():
    fleet = _fuji()
    fuel = _fuel_trailer()
    fixed = (Placement(plane_id="fuel", x_m=2.0, y_m=10.0, heading_deg=0.0, on_carts=False),)
    env = HangarFitEnv(
        hangar=empty_hangar(),
        fleet=fleet,
        requested_ids=("fuji",),
        ground_objects={"fuel": fuel},
        fixed_placements=fixed,
    )
    env.reset()
    # The fixed obstacle is present in the scene from step 0...
    layout = env._layout()
    assert "fuel" in {gp.plane_id for gp in layout.ground_object_placements}
    # ...but it is NOT counted as a parked (driven-in) object.
    assert env._fixed == list(fixed)
    assert all(p.plane_id != "fuel" for p in env._parked)
    # terminal_fraction denominator is the requested (driven) set only -> 1 here, not 2.
    # Drive fuji nowhere and Park it: fraction = 1/1 even with the fixed obstacle present.
    _obs, _r, done, info = env.step(Park())
    assert done and info.total == 1 and info.placed == 1


def test_placed_body_overlapping_fixed_obstacle_is_invalid():
    fleet = _fuji()
    fuel = _fuel_trailer()
    # Place the fuel obstacle exactly where we will park the fuji -> guaranteed overlap.
    fixed = (Placement(plane_id="fuel", x_m=9.0, y_m=10.0, heading_deg=0.0, on_carts=False),)
    env = HangarFitEnv(
        hangar=empty_hangar(),
        fleet=fleet,
        requested_ids=("fuji",),
        ground_objects={"fuel": fuel},
        fixed_placements=fixed,
    )
    env.reset()
    env._active_pose = type(env._active_pose)(x_m=9.0, y_m=10.0, heading_deg=0.0)
    env.step(Park())
    assert env._layout_valid() is False  # fuji parts overlap the fixed fuel obstacle


# ---------------------------------------------------------------------------
# #704 — _parked_score() episode cache
# ---------------------------------------------------------------------------
def test_parked_score_cache_equals_fresh_each_step():
    env = _two_object_env(
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=40, total_step_budget=80)
    )
    env.reset()
    fwd = Primitive(kind="S", magnitude=1.0, gear=1)
    actions = [fwd, fwd, fwd, Park(), fwd, fwd]  # drive+park obj1, then drive obj2
    for a in actions:
        _, _, done, _ = env.step(a)
        if done:
            break
        # After a non-terminal step the cache must equal a fresh score of the parked set.
        assert env._parked_score() == go.score_layout(env._layout())


def test_parked_score_empty_set_is_trivial():
    env = _env()
    env.reset()
    s = env._parked_score()  # nothing parked yet
    assert s == go.LayoutScore(0.0, True, False)


def test_parked_obstacles_cache_is_stable_per_version_and_active():
    env = _two_object_env(
        difficulty=DifficultyConfig(max_objects=2, per_object_step_budget=40, total_step_budget=80)
    )
    env.reset()
    env.step(Park())  # park obj1; active is now obj2
    aid = env._active_id
    assert aid is not None
    o1 = env._parked_obstacles(aid)
    o2 = env._parked_obstacles(aid)
    assert o1 is o2  # same (version, active_id) -> cached object reused


def test_fixed_obstacle_is_perceived_in_observation_and_encoding():
    # The policy must PERCEIVE the keep-out it is penalized for hitting: the fixed obstacle
    # belongs in obs.parked (the observed frozen set) and surfaces in the encoded tokens
    # (its fixed_obstacle type column, row[4]==1) — NOT just in the reward-side _layout().
    from ml.encoding import EncoderConfig, encode

    fuel = _fuel_trailer()
    fixed = (Placement(plane_id="fuel", x_m=2.0, y_m=10.0, heading_deg=0.0, on_carts=False),)
    env = HangarFitEnv(
        hangar=empty_hangar(),
        fleet=_fuji(),
        requested_ids=("fuji",),
        ground_objects={"fuel": fuel},
        fixed_placements=fixed,
    )
    obs = env.reset()
    # (a) The fixed obstacle is in the observed frozen set from step 0...
    assert "fuel" in {po.object_id for po in obs.parked}
    # ...and it is NOT counted as parked/driven (the LIST, not obs.parked).
    assert all(p.plane_id != "fuel" for p in env._parked)
    # (b) The encoded observation surfaces it: a token row with the fixed_obstacle type
    # column (row[4]) set. (Bodies = fleet ∪ ground_objects so the encoder resolves it.)
    tensors = encode(obs, env.hangar, {**env.fleet, **env.ground_objects}, EncoderConfig())
    fixed_rows = [
        i for i in range(tensors.tokens.shape[0]) if tensors.token_mask[i] and tensors.tokens[i, 4]
    ]
    assert fixed_rows, "no fixed_obstacle token row (row[4]) — keep-out is not perceived"


def test_r_unplaced_penalty_lowers_terminal_reward_on_abandonment():
    """End-to-end through HangarFitEnv: driving an object to budget exhaustion without ever
    Parking it (abandonment, terminal_fraction=0) must cost exactly r_unplaced_penalty more
    than the same trajectory with the penalty off — proving the #710 economics knob is wired
    from RewardWeights through env.step's terminal branch (not just step_reward in isolation)."""
    from ml.types import DifficultyConfig, Primitive, RewardWeights

    diff = DifficultyConfig(max_objects=1, per_object_step_budget=2, total_step_budget=2)

    def run(penalty: float) -> float:
        env = HangarFitEnv(
            hangar=empty_hangar(),
            fleet=_fuji(),
            requested_ids=("fuji",),
            difficulty=diff,
            weights=RewardWeights(r_unplaced_penalty=penalty),
        )
        env.reset()
        total, done, info = 0.0, False, None
        while not done:
            _, r, done, info = env.step(Primitive(kind="S", magnitude=1.0, gear=1))
            total += r
        assert info is not None and info.placed == 0  # never parked -> pure abandonment
        return total

    base = run(0.0)
    penalized = run(10.0)
    # unplaced fraction = 1 - terminal_fraction = 1.0 -> penalty term = -10.0, terminal step only.
    assert base - penalized == pytest.approx(10.0)


def test_r_unplaced_penalty_full_park_not_penalized_vs_abandon():
    """T5: both terminal branches feed terminal_fraction into the penalty. A FULLY-placed
    Park episode (frac=1, via the Park-done branch) is NOT penalized — identical reward
    with/without the knob — while an abandonment episode (frac=0, via the budget branch) is
    charged the full unplaced fraction. Guards against the penalty being wired into only one
    of the two terminal branches."""
    from ml.types import DifficultyConfig, Park, Primitive, RewardWeights

    diff = DifficultyConfig(max_objects=2, per_object_step_budget=2, total_step_budget=4)

    def run(penalty: float, mode: str) -> tuple[float, int]:
        env = HangarFitEnv(
            hangar=empty_hangar(),
            fleet=_fuji(),
            requested_ids=("fuji", "aviat_husky"),
            difficulty=diff,
            weights=RewardWeights(r_unplaced_penalty=penalty),
        )
        env.reset()
        total, done, info = 0.0, False, None
        while not done:
            act = Park() if mode == "park" else Primitive(kind="S", magnitude=1.0, gear=1)
            _, r, done, info = env.step(act)
            total += r
        assert info is not None
        return total, info.placed

    park0, p_placed = run(0.0, "park")
    parkR, _ = run(20.0, "park")
    aban0, a_placed = run(0.0, "abandon")
    abanR, _ = run(20.0, "abandon")
    assert p_placed == 2 and a_placed == 0  # Park-done (frac=1) vs budget-abandon (frac=0)
    assert parkR == pytest.approx(park0)  # full placement -> penalty term 0, unchanged by knob
    assert aban0 - abanR == pytest.approx(20.0)  # abandon -> charged the full unplaced fraction
