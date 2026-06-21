"""#733: activate ``pose_cache_scope`` + ``cached_parts_world`` in the ml/ rollout.

Two contracts are guarded here:

* **Routing / perf** — every ml/ geometry consumer (the oracle's intrusion / swept /
  active-misfit loops and the encoder's rasterizer) goes through ``cached_parts_world``,
  so inside an active scope a *repeated* ``(body, pose)`` rebuilds zero shapely parts
  (cache hit). A worker step with ``pose_cache=True`` therefore does strictly fewer raw
  ``aircraft_parts_world`` builds than ``pose_cache=False`` (the un-cached baseline).
* **Byte-identity (ADR-0003)** — turning the cache on never changes a number: the worker
  reward stream + encoded observations and the single-env ``collect_rollout`` buffer are
  bit-identical with the cache on vs off. Verified via the established ml/ pattern (fixed
  action stream → reward/obs diff), NOT checkpoint hashes (torch-CPU is cross-process
  nondeterministic). The leak-loop AABB pre-filter is pinned against a brute-force
  reference of the exact unfiltered intersects.
"""

from __future__ import annotations

import numpy as np
import pytest

import hangarfit.geometry as geo
from hangarfit.geometry import aircraft_parts_world, pose_cache_scope
from hangarfit.models import Placement
from hangarfit.towplanner import Pose, _motion_clear
from ml import geometry_oracle as go
from ml.action_space import PARK_INDEX
from ml.encoding import EncoderConfig, encode
from ml.env import HangarFitEnv
from ml.vector_env import _EnvWorker
from tests.ml.conftest import _fuji, empty_hangar, single_object_layout, two_object_layout


def _trivial_env() -> HangarFitEnv:
    """A torch-free single-object env (no ml.train import, so this module collects on the
    torch-free CI)."""
    return HangarFitEnv(hangar=empty_hangar(), fleet=_fuji(), requested_ids=("fuji",))


def _count_raw_builds(monkeypatch) -> dict[str, int]:
    """Patch the underlying ``aircraft_parts_world`` (the function ``cached_parts_world``
    delegates to on a miss, and the one collisions/encoder call) with a counter, so the
    count == number of ACTUAL polygon builds = cache misses."""
    seen = {"n": 0}
    orig = geo.aircraft_parts_world

    def _counting(obj, placement):
        seen["n"] += 1
        return orig(obj, placement)

    monkeypatch.setattr(geo, "aircraft_parts_world", _counting)
    return seen


# ---------------------------------------------------------------------------
# Routing: each ml/ consumer goes through cached_parts_world.
# A repeated identical call inside a scope must rebuild ZERO parts.
# ---------------------------------------------------------------------------


def test_intrusion_routes_through_pose_cache(monkeypatch):
    seen = _count_raw_builds(monkeypatch)
    layout = single_object_layout(x_m=5.0, y_m=8.0)
    body, pl = layout.fleet["fuji"], layout.placements[0]
    with pose_cache_scope():
        go.intrusion_area_m2(body, pl, layout.hangar)  # warm the cache
        warm = seen["n"]
        assert warm > 0  # builds went through the (patchable) cached_parts_world path
        go.intrusion_area_m2(body, pl, layout.hangar)  # identical repeat
        assert seen["n"] == warm  # second call was a pure cache hit


def test_active_misfit_routes_through_pose_cache(monkeypatch):
    seen = _count_raw_builds(monkeypatch)
    layout, active, _id = two_object_layout(parked_y_m=12.0, active_y_m=12.2)
    pose = Pose(x_m=5.0, y_m=12.2, heading_deg=0.0)
    with pose_cache_scope():
        go.active_misfit_m2(active, pose, layout, layout.hangar)  # warm
        warm = seen["n"]
        assert warm > 0  # builds went through the (patchable) cached_parts_world path
        go.active_misfit_m2(active, pose, layout, layout.hangar)  # repeat
        assert seen["n"] == warm  # parked obstacles + active all served from cache


def test_encode_routes_through_pose_cache(monkeypatch):
    seen = _count_raw_builds(monkeypatch)
    enc = EncoderConfig()
    env = _trivial_env()
    obs = env.reset()
    bodies = {**env.fleet, **env.ground_objects}
    with pose_cache_scope():
        encode(obs, env.hangar, bodies, enc)  # warm (parked + active + body-dims)
        warm = seen["n"]
        assert warm > 0  # builds went through the (patchable) cached_parts_world path
        encode(obs, env.hangar, bodies, enc)  # repeat
        assert seen["n"] == warm  # rasterizer fully served from cache


# ---------------------------------------------------------------------------
# Perf at the worker seam: the scope is actually opened around step + encode
# ---------------------------------------------------------------------------


def test_envworker_step_with_pose_cache_builds_fewer_parts(monkeypatch):
    """One worker.step (PARK) rebuilds the active body's parts across check + intrusion +
    encode; with the pose cache those collapse to a single build, so pose_cache=True does
    strictly fewer raw builds than pose_cache=False."""
    enc = EncoderConfig()

    seen_off = _count_raw_builds(monkeypatch)
    w_off = _EnvWorker(_trivial_env(), enc, next_request=None, pose_cache=False)
    w_off.reset()
    base = seen_off["n"]
    w_off.step(PARK_INDEX, 0)
    builds_off = seen_off["n"] - base

    seen_on = _count_raw_builds(monkeypatch)
    w_on = _EnvWorker(_trivial_env(), enc, next_request=None, pose_cache=True)
    w_on.reset()
    base = seen_on["n"]
    w_on.step(PARK_INDEX, 0)
    builds_on = seen_on["n"] - base

    assert builds_on > 0  # sanity: the step did build geometry
    assert builds_on < builds_off  # the cache deduplicated repeated poses


# ---------------------------------------------------------------------------
# Byte-identity: pose_cache on vs off produces bit-identical rewards + encoded obs
# ---------------------------------------------------------------------------


def _drive(worker: _EnvWorker, actions):
    out = []
    for kind, mag in actions:
        obs, reward, done, info, _ep = worker.step(kind, mag)
        out.append((obs, reward, done, info.placed, info.total))
    return out


def test_envworker_pose_cache_byte_identical_reward_and_obs():
    """Same fixed action stream (two moves, then PARK) yields a bit-identical reward
    stream + encoded observations with the cache on vs off (ADR-0003)."""
    enc = EncoderConfig()
    actions = [(0, 1), (0, 1), (PARK_INDEX, 0)]

    w_off = _EnvWorker(_trivial_env(), enc, next_request=None, pose_cache=False)
    w_off.reset()
    rec_off = _drive(w_off, actions)

    w_on = _EnvWorker(_trivial_env(), enc, next_request=None, pose_cache=True)
    w_on.reset()
    rec_on = _drive(w_on, actions)

    for (o_off, r_off, d_off, p_off, t_off), (o_on, r_on, d_on, p_on, t_on) in zip(
        rec_off, rec_on, strict=True
    ):
        assert r_on == r_off
        assert d_on == d_off and p_on == p_off and t_on == t_off
        assert np.array_equal(o_on.raster, o_off.raster)
        assert np.array_equal(o_on.tokens, o_off.tokens)
        assert np.array_equal(o_on.token_mask, o_off.token_mask)
        assert np.array_equal(o_on.legal_action_mask, o_off.legal_action_mask)
        assert o_on.active_index == o_off.active_index


def test_envworker_reset_byte_identical_encoded_obs():
    """reset()'s encoded obs is bit-identical with the cache on vs off."""
    enc = EncoderConfig()
    o_off = _EnvWorker(_trivial_env(), enc, next_request=None, pose_cache=False).reset()
    o_on = _EnvWorker(_trivial_env(), enc, next_request=None, pose_cache=True).reset()
    assert np.array_equal(o_on.raster, o_off.raster)
    assert np.array_equal(o_on.tokens, o_off.tokens)
    assert o_on.active_index == o_off.active_index


# ---------------------------------------------------------------------------
# Fold-in #3: leak-loop AABB pre-filter is byte-identical to the exact intersects
# ---------------------------------------------------------------------------


def _bruteforce_swept_intrusion(body, swept, *, active_id, obstacles, hangar) -> float:
    """The exact unfiltered leak loop (no AABB pre-filter) — the byte-identity oracle."""
    worst = 0.0
    for pose in swept:
        if _motion_clear(body, pose, obstacles, hangar):
            continue
        pl = Placement(
            plane_id=active_id,
            x_m=pose.x_m,
            y_m=pose.y_m,
            heading_deg=pose.heading_deg,
            on_carts=False,
        )
        leak = 0.0
        for wp in aircraft_parts_world(body, pl):
            for op in obstacles.world_parts:
                if wp.polygon.intersects(op.polygon):
                    leak += wp.polygon.intersection(op.polygon).area
        worst = max(worst, leak)
    return worst


@pytest.mark.parametrize("under_scope", [False, True])
def test_swept_intrusion_aabb_prefilter_byte_identical(under_scope):
    """swept_intrusion_m2 with the AABB pre-filter == the brute-force exact intersects,
    both inside and outside a pose-cache scope, on a sweep that genuinely intrudes."""
    layout, active, active_id = two_object_layout(parked_y_m=15.0, active_y_m=8.0)
    obstacles = go.build_obstacles(layout, active_id)
    # Poses placed on/around the parked fuji at (5, 15) => the husky overlaps it.
    swept = tuple(Pose(x_m=5.0, y_m=15.0 + d, heading_deg=0.0) for d in (-0.4, 0.0, 0.4))

    expected = _bruteforce_swept_intrusion(
        active, swept, active_id=active_id, obstacles=obstacles, hangar=layout.hangar
    )

    def _call() -> float:
        return go.swept_intrusion_m2(
            active, swept, parked_layout=layout, active_id=active_id, obstacles=obstacles
        )

    if under_scope:
        with pose_cache_scope():
            actual = _call()
    else:
        actual = _call()
    assert actual == expected
    assert actual > 0.0  # the leak path actually ran (non-vacuous)


# ---------------------------------------------------------------------------
# Single-env collect_rollout: pose_cache toggle is byte-identical (torch path)
# ---------------------------------------------------------------------------


def test_collect_rollout_pose_cache_byte_identical():
    """collect_rollout's buffer (rewards + dones) is bit-identical with the cache on vs
    off over a fixed-seed rollout."""
    torch = pytest.importorskip("torch")
    from ml.policy import HangarFitPolicy
    from ml.train import build_trivial_env, collect_rollout

    enc = EncoderConfig()

    def _run(pose_cache: bool):
        torch.manual_seed(0)
        policy = HangarFitPolicy(d_model=32, n_layers=1, n_heads=2)
        buf, stats = collect_rollout(
            build_trivial_env(seed=0), policy, enc, rollout_len=12, pose_cache=pose_cache
        )
        return list(buf.reward), list(buf.done), [(s.fraction_placed, s.valid) for s in stats]

    assert _run(True) == _run(False)
