"""#754 Lever A: whole-leg swept-envelope AABB early-out for ``swept_intrusion_m2``.

``swept_intrusion_m2`` returns 0.0 iff NO sampled pose's mover parts overlap (positive
area) any parked obstacle part (wall/notch/bay only gate ``_motion_clear``, they never
contribute leak area). Lever A proves that for a whole leg in ONE conservative test —
a footprint-radius-inflated bbox of the sampled poses, tested vs the precomputed obstacle
AABBs — so a clearly-clear leg short-circuits to 0.0 with zero per-pose Polygon builds.

The early-out is a conservative LOWER-BOUND filter (like the existing per-pose AABB
prefilter), so it is byte-identical: it fires only when the per-pose loop would also
return 0.0, and it must NEVER mask a real intrusion.
"""

from __future__ import annotations

import hangarfit.geometry as geo
from hangarfit.geometry import aircraft_parts_world, pose_cache_scope
from hangarfit.models import Placement
from hangarfit.towplanner import Pose, _motion_clear
from ml import geometry_oracle as go
from tests.ml.conftest import two_object_layout


def _bruteforce_swept_intrusion(body, swept, *, active_id, obstacles, hangar) -> float:
    """The exact unfiltered leak loop (no early-out, no prefilter) — the byte-identity
    oracle Lever A must reproduce."""
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


def _clear_leg():
    """A straight leg in open space far from the single parked obstacle (no overlap)."""
    layout, active, active_id = two_object_layout(parked_y_m=22.0, active_y_m=6.0)
    obstacles = go.build_obstacles(layout, active_id)
    swept = tuple(Pose(x_m=5.0, y_m=6.0 + d, heading_deg=0.0) for d in (0.0, 0.2, 0.4, 0.6, 0.8))
    return layout, active, active_id, obstacles, swept


def _intruding_leg():
    """A leg whose poses sit on/around the parked obstacle (genuine overlap)."""
    layout, active, active_id = two_object_layout(parked_y_m=15.0, active_y_m=8.0)
    obstacles = go.build_obstacles(layout, active_id)
    swept = tuple(Pose(x_m=5.0, y_m=15.0 + d, heading_deg=0.0) for d in (-0.4, 0.0, 0.4))
    return layout, active, active_id, obstacles, swept


def test_clear_leg_byte_identical_to_bruteforce_and_zero():
    """A clearly-clear leg returns exactly 0.0 — identical to the unfiltered reference."""
    layout, active, active_id, obstacles, swept = _clear_leg()
    actual = go.swept_intrusion_m2(
        active, swept, parked_layout=layout, active_id=active_id, obstacles=obstacles
    )
    expected = _bruteforce_swept_intrusion(
        active, swept, active_id=active_id, obstacles=obstacles, hangar=layout.hangar
    )
    assert expected == 0.0  # sanity: the leg really is clear
    assert actual == expected == 0.0


def test_intruding_leg_byte_identical_to_bruteforce_nonzero():
    """An intruding leg must NOT be masked by the early-out: the result equals the exact
    unfiltered reference and is strictly positive (the conservative filter did not fire)."""
    layout, active, active_id, obstacles, swept = _intruding_leg()
    actual = go.swept_intrusion_m2(
        active, swept, parked_layout=layout, active_id=active_id, obstacles=obstacles
    )
    expected = _bruteforce_swept_intrusion(
        active, swept, active_id=active_id, obstacles=obstacles, hangar=layout.hangar
    )
    assert expected > 0.0  # sanity: the leg really intrudes
    assert actual == expected


def test_clear_leg_does_no_per_pose_builds(monkeypatch):
    """Lever A: a clearly-clear leg short-circuits BEFORE the per-pose loop, so it builds the
    mover's parts at most once (the heading-independent footprint radius), not ~once per pose
    via the per-pose _motion_clear. Obstacles are pre-built so only mover builds are counted."""
    layout, active, active_id, obstacles, swept = _clear_leg()
    seen = {"n": 0}
    orig = geo.aircraft_parts_world

    def _counting(obj, placement):
        if getattr(obj, "id", None) == active_id:
            seen["n"] += 1
        return orig(obj, placement)

    monkeypatch.setattr(geo, "aircraft_parts_world", _counting)
    with pose_cache_scope():
        result = go.swept_intrusion_m2(
            active, swept, parked_layout=layout, active_id=active_id, obstacles=obstacles
        )
    assert result == 0.0
    assert seen["n"] <= 1, f"clear leg should skip the per-pose loop; built mover {seen['n']}x"


def test_fuzzed_legs_byte_identical_to_bruteforce():
    """Adversarial: across many random legs (varied start, heading, length — exercising R's
    heading-independence) and parked positions spanning clear/near/overlapping, the Lever-A
    result must equal the exact unfiltered reference on EVERY leg. A too-tight envelope that
    masked an intrusion (returned 0.0 where the loop is >0) would fail here."""
    import random

    from ml.action_space import decode

    rng = random.Random(0xA1B2C3)
    n_clear = n_intrude = n_fired = 0
    for _ in range(80):
        parked_y = rng.uniform(6.0, 24.0)
        active_x = rng.uniform(2.0, 13.0)
        active_y = rng.uniform(4.0, 26.0)
        layout, active, active_id = two_object_layout(parked_y_m=parked_y, active_y_m=active_y)
        obstacles = go.build_obstacles(layout, active_id)
        tr = active.effective_turn_radius_m()
        kind_idx = rng.randint(0, 5)  # L/S/R forward/back (own-gear arcs)
        mag_idx = rng.randint(0, 4)
        prim = decode(kind_idx, mag_idx, turn_radius_m=tr)
        start = Pose(x_m=active_x, y_m=active_y, heading_deg=rng.uniform(0.0, 360.0))
        _end, swept = go.apply_primitive(start, prim, turn_radius_m=tr)

        actual = go.swept_intrusion_m2(
            active, swept, parked_layout=layout, active_id=active_id, obstacles=obstacles
        )
        expected = _bruteforce_swept_intrusion(
            active, swept, active_id=active_id, obstacles=obstacles, hangar=layout.hangar
        )
        assert actual == expected, f"diverged: {actual} != {expected} (parked_y={parked_y})"
        if expected > 0.0:
            n_intrude += 1
        else:
            n_clear += 1
            if go._swept_envelope_clear(active, swept, obstacles):
                n_fired += 1  # the conservative early-out actually short-circuited this leg
    # non-vacuous: the corpus exercised BOTH the early-out (clear) and the full loop (intrude)
    assert n_clear > 0 and n_intrude > 0, f"vacuous corpus: clear={n_clear} intrude={n_intrude}"
    # the early-out must do REAL work: a regression silently disabling it would still pass the
    # byte-identity asserts above (the loop just runs), but n_fired would drop to 0.
    assert n_fired > 0, "the swept-envelope early-out never fired on any clear leg"


def test_swept_envelope_clear_predicate_fires_on_clear_not_intruding():
    """Directly pin the early-out predicate both ways: True on a clearly-separated leg,
    False when poses sit on the obstacle (so it cannot mask the intruding case)."""
    _layout, active, _id, obstacles, swept = _clear_leg()
    assert go._swept_envelope_clear(active, swept, obstacles) is True
    _layout2, active2, _id2, obstacles2, swept2 = _intruding_leg()
    assert go._swept_envelope_clear(active2, swept2, obstacles2) is False
