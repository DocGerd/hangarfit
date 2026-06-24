"""#821 backplay reverse-curriculum — env-level spawn override. No torch (loader + oracle only)."""

from __future__ import annotations

import dataclasses

from hangarfit.loader import load_fleet, load_hangar, load_layout
from hangarfit.towplanner import Pose
from ml.env import HangarFitEnv, _backplay_corridor_pose, _lerp_heading_deg
from ml.types import DifficultyConfig, Park

_WITNESS_NOTCH = "tests/fixtures/ml/witness_notch.yaml"


def _backplay_env(seed_anchor_k: int = 2):
    """A trio-notch env with the full witness anchored and a k=N-1 prefix pre-parked, so the
    single driven object (requested[2]) has a witness park-pose for the backplay corridor."""
    hangar = dataclasses.replace(
        load_hangar("examples/herrenteich/hangar.yaml"), clearance_m=0.05, apron_depth_m=8.0
    )
    fleet = load_fleet("examples/herrenteich/fleet.yaml")
    lay = load_layout(_WITNESS_NOTCH, fleet=fleet, hangar=hangar)
    ids = tuple(p.plane_id for p in lay.placements)
    env = HangarFitEnv(
        hangar=hangar,
        fleet=fleet,
        requested_ids=ids,
        anchor_placements=tuple(lay.placements),
        difficulty=DifficultyConfig(
            max_objects=3,
            seed_anchor_k=seed_anchor_k,
            per_object_step_budget=80,
            total_step_budget=180,
        ),
    )
    return env, ids, lay


def _door_pose(env: HangarFitEnv) -> Pose:
    depth = env.hangar.apron_depth_m or 0.0
    return Pose(
        x_m=env.hangar.door.center_x_m, y_m=-(depth / 2.0 if depth else 0.0), heading_deg=0.0
    )


# --- pure helpers -----------------------------------------------------------------------------


def test_backplay_phi_cap_default_inert():
    assert DifficultyConfig().backplay_phi_cap is None


def test_lerp_heading_endpoints_and_shortest_arc():
    assert _lerp_heading_deg(90.0, 0.0, 0.0) == 90.0  # t=0 -> a
    assert _lerp_heading_deg(90.0, 0.0, 1.0) == 0.0  # t=1 -> b
    # shortest arc 350 -> 10 crosses 360, not the long way down through 180
    assert _lerp_heading_deg(350.0, 10.0, 0.5) == 0.0


def test_corridor_pose_endpoints_are_exact():
    witness = load_layout(
        _WITNESS_NOTCH,
        fleet=load_fleet("examples/herrenteich/fleet.yaml"),
        hangar=dataclasses.replace(
            load_hangar("examples/herrenteich/hangar.yaml"), apron_depth_m=8.0
        ),
    ).placements[2]
    door = Pose(x_m=6.73, y_m=-4.0, heading_deg=0.0)
    at0 = _backplay_corridor_pose(witness, door, 0.0)
    assert (at0.x_m, at0.y_m, at0.heading_deg) == (witness.x_m, witness.y_m, witness.heading_deg)
    at1 = _backplay_corridor_pose(witness, door, 1.0)
    assert (at1.x_m, at1.y_m, at1.heading_deg) == (door.x_m, door.y_m, door.heading_deg)


# --- env integration --------------------------------------------------------------------------


def test_backplay_none_is_byte_identical_door_spawn():
    env, ids, _ = _backplay_env()
    env.reset(requested_ids=ids)  # backplay_phi defaults None => inert
    assert env._active_pose == _door_pose(env)
    assert env._backplay_phi is None


def test_backplay_phi_one_coincides_with_door_spawn():
    env, ids, _ = _backplay_env()
    env.reset(requested_ids=ids, backplay_phi=1.0)
    assert env._active_pose == _door_pose(env)


def test_backplay_phi_zero_spawns_exactly_at_the_driven_witness_pose():
    env, ids, lay = _backplay_env()
    env.reset(requested_ids=ids, backplay_phi=0.0)
    driven = {p.plane_id: p for p in lay.placements}[ids[2]]  # requested[2] is the one driven
    assert env._active_id == ids[2]
    assert env._active_pose is not None
    assert (env._active_pose.x_m, env._active_pose.y_m, env._active_pose.heading_deg) == (
        driven.x_m,
        driven.y_m,
        driven.heading_deg,
    )


def test_backplay_phi_zero_parks_into_a_valid_full_layout():
    # At phi=0 the driven object starts AT its witness pose; a single Park freezes it there,
    # completing the full (valid) witness — the near-solved start the curriculum shows first.
    env, ids, _ = _backplay_env()
    env.reset(requested_ids=ids, backplay_phi=0.0)
    _obs, _r, done, info = env.step(Park())
    assert done  # k=2 prefix + 1 driven => queue empty after one Park
    assert info.placed == 3
    assert info.valid is True


def test_backplay_intermediate_phi_is_the_corridor_pose_or_the_door_fallback():
    # The spawn is the corridor pose when that start is collision-free, else the door fallback —
    # never some third location. (Robust to whichever the geometry yields at this phi.)
    env, ids, lay = _backplay_env()
    env.reset(requested_ids=ids, backplay_phi=0.3)
    driven = {p.plane_id: p for p in lay.placements}[ids[2]]
    corridor = _backplay_corridor_pose(driven, _door_pose(env), 0.3)
    assert env._active_pose in (corridor, _door_pose(env))


def test_backplay_admissibility_gates_on_overlap():
    # The corridor pose is accepted only if collision-free: the witness pose admits (full witness
    # is valid), but dropping the driven object on top of a pre-parked neighbour does not.
    env, ids, lay = _backplay_env()
    env.reset(requested_ids=ids, backplay_phi=0.0)  # _active_id = driven, two neighbours pre-parked
    by_id = {p.plane_id: p for p in lay.placements}
    driven, neighbour = by_id[ids[2]], by_id[ids[0]]
    assert env._backplay_admissible(Pose(driven.x_m, driven.y_m, driven.heading_deg))
    assert not env._backplay_admissible(Pose(neighbour.x_m, neighbour.y_m, neighbour.heading_deg))
