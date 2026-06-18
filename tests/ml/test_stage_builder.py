"""Unit tests for ml/stage_builder.py — touches disk (loader) but NO torch, so it
runs in the no-torch CI."""

from __future__ import annotations

import dataclasses

import pytest

from hangarfit.loader import load_fleet, load_hangar, load_layout
from ml import geometry_oracle as go
from ml.curriculum import DEFAULT_LADDER, CurriculumSchedule, Stage, with_pair_anchored_rung
from ml.stage_builder import build_stage_env, effective_fleet_ids
from ml.types import DifficultyConfig

_WITNESS_BOX = "tests/fixtures/ml/witness_box.yaml"


def _load_witness_box(clearance_m: float = 0.05):
    """Load the committed box witness layout against the box rung's hangar (clearance
    override) + fleet — the same hangar/fleet the pair-anchored rung trains on."""
    hangar = dataclasses.replace(
        load_hangar("data/hangar.yaml"), clearance_m=clearance_m, apron_depth_m=8.0
    )
    fleet = load_fleet("data/fleet.yaml")
    return load_layout(_WITNESS_BOX, fleet=fleet, hangar=hangar)


def test_effective_fleet_ids_returns_explicit_pool_verbatim():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji", "aviat_husky"),
    )
    assert effective_fleet_ids(s) == ("fuji", "aviat_husky")


def test_effective_fleet_ids_resolves_whole_fleet_when_none():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=None,
    )
    ids = effective_fleet_ids(s)
    assert "fuji" in ids and "aviat_husky" in ids
    assert len(ids) >= 9  # the synthetic manifest lists 9 aircraft


def test_effective_fleet_ids_herrenteich_excludes_ground_objects():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="examples/herrenteich/hangar.yaml",
        fleet_path="examples/herrenteich/fleet.yaml",
        fleet_ids=None,
    )
    ids = effective_fleet_ids(s)
    # load_fleet returns aircraft only; the 4 ground objects must NOT appear
    assert "vw_caddy" not in ids
    assert "maul_fuel_trailer" not in ids


def test_every_default_ladder_rung_pool_covers_max_objects():
    for s in DEFAULT_LADDER:
        pool = effective_fleet_ids(s)
        assert s.difficulty.max_objects <= len(pool)


def test_build_stage_env_applies_clearance_and_apron_overrides():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1, per_object_step_budget=40, total_step_budget=40),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji",),
        clearance_m=0.05,
        apron_depth_m=8.0,
    )
    env = build_stage_env(s)
    assert env.hangar.clearance_m == 0.05
    assert env.hangar.apron_depth_m == 8.0
    assert env.difficulty.max_objects == 1
    assert "fuji" in env.fleet


def test_build_stage_env_strict_rung_inherits_file_clearance():
    strict = DEFAULT_LADDER[4]  # trio-notch-strict, clearance_m=None
    env = build_stage_env(strict)
    assert env.hangar.clearance_m == 0.10  # the herrenteich file value (#664)


def test_build_stage_env_raises_when_max_objects_exceeds_pool():
    s = Stage(
        name="toobig",
        difficulty=DifficultyConfig(max_objects=2),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji",),  # pool of 1, but want 2
    )
    with pytest.raises(ValueError):
        build_stage_env(s)


def test_build_stage_env_every_default_rung_constructs():
    for s in DEFAULT_LADDER:
        env = build_stage_env(s)
        assert len(env.requested_ids) == s.difficulty.max_objects


# ---------------------------------------------------------------------------
# #712 — the committed box witness layout (seed-anchor start-state graft)
# ---------------------------------------------------------------------------


def test_witness_box_is_a_valid_two_object_layout():
    # The committed witness is a valid N-object layout under the box rung's clearance
    # (0.05) — validated by the PRODUCT checker (collisions.check + Caddy egress), no solver.
    lay = _load_witness_box()
    assert len(lay.placements) == 2
    assert go.layout_valid(lay)


def test_witness_box_every_prefix_is_valid():
    # The #712 correctness property: a k-prefix of a valid witness is itself valid (removing
    # objects cannot create overlap/intrusion/egress conflicts), so any anchored subset is a
    # guaranteed-valid partial start with no per-reset validity search.
    lay = _load_witness_box()
    for k in range(len(lay.placements) + 1):
        prefix = dataclasses.replace(lay, placements=lay.placements[:k])
        assert go.layout_valid(prefix), f"witness prefix k={k} is invalid"


def test_witness_box_has_margin_at_strict_clearance():
    # Robustness: the witness is valid even at the file's strict 0.3 m clearance, so it has
    # real geometric margin at the rung's lenient 0.05 m (not a borderline wing-graze).
    assert go.layout_valid(_load_witness_box(clearance_m=0.3))


# ---------------------------------------------------------------------------
# #712 — stage_builder threads the witness into an anchored rung's env
# ---------------------------------------------------------------------------


def _anchored_stage(k: int = 1) -> Stage:
    return Stage(
        name="pair-anchored-test",
        difficulty=DifficultyConfig(
            max_objects=2, seed_anchor_k=k, per_object_step_budget=60, total_step_budget=60
        ),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        anchor_layout_path=_WITNESS_BOX,
        clearance_m=0.05,
    )


def test_stage_anchor_layout_path_defaults_none():
    s = Stage(
        name="x",
        difficulty=DifficultyConfig(max_objects=1),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
    )
    assert s.anchor_layout_path is None


def test_effective_fleet_ids_is_the_witness_set_when_anchored():
    # Q2: an anchored rung pins the pool to the witness's objects (so each episode's seeded
    # permutation draws exactly that set and the env anchors a k-prefix of it).
    assert set(effective_fleet_ids(_anchored_stage())) == {"fuji", "aviat_husky"}


def test_build_stage_env_threads_witness_placements_and_k():
    env = build_stage_env(_anchored_stage(k=1))
    assert set(env._anchor_by_id) == {"fuji", "aviat_husky"}
    assert env.difficulty.seed_anchor_k == 1
    assert set(env.requested_ids) == {"fuji", "aviat_husky"}


def test_build_stage_env_anchored_reset_starts_from_a_valid_partial():
    env = build_stage_env(_anchored_stage(k=1))
    env.reset(requested_ids=("fuji", "aviat_husky"))
    assert [p.plane_id for p in env._parked] == ["fuji"]  # k=1 prefix pre-parked
    assert env._layout_valid()  # the pre-parked partial is collision-free at reset


def test_build_stage_env_without_anchor_layout_has_empty_anchor_map():
    # Default-neutral: a rung with no anchor_layout_path builds an anchor-free env.
    env = build_stage_env(DEFAULT_LADDER[1])  # pair-box
    assert env._anchor_by_id == {}


def test_build_stage_env_anchored_requires_max_objects_equals_witness():
    # An anchored rung pins its pool to the witness set, so max_objects must equal the witness
    # object count — else the rung silently trains a truncated objective. Fail loud.
    s = Stage(
        name="mismatch",
        difficulty=DifficultyConfig(max_objects=1),  # the box witness has 2 objects
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        anchor_layout_path=_WITNESS_BOX,
        clearance_m=0.05,
    )
    with pytest.raises(ValueError, match="witness"):
        build_stage_env(s)


def test_committed_pair_anchored_rung_builds_and_resets_from_a_valid_partial():
    # End-to-end on the REAL rung: the committed pair-anchored rung's witness path resolves
    # and its env starts from a valid 1-object partial. Catches drift between the rung's
    # anchor_layout_path and the witness fixture.
    sched = with_pair_anchored_rung(CurriculumSchedule.default())
    rung = next(s for s in sched.stages if s.name == "pair-anchored")
    env = build_stage_env(rung)
    assert set(env._anchor_by_id) == set(env.requested_ids)
    env.reset(requested_ids=env.requested_ids)
    assert len(env._parked) == 1  # k=1 prefix pre-parked
    assert env._layout_valid()  # the partial start is collision-free


def test_anchored_subset_varies_across_episodes_and_is_reproducible():
    # The seeded-random k-subset claim, end-to-end: driving the env through the curriculum's
    # seeded per-episode sampler, the ANCHORED object varies episode-to-episode (it is not
    # pinned to one object) AND the sequence is reproducible for a fixed seed.
    from ml.curriculum import sample_request, stage_rng

    sched = with_pair_anchored_rung(CurriculumSchedule.default())
    rung = next(s for s in sched.stages if s.name == "pair-anchored")
    env = build_stage_env(rung)
    pool = effective_fleet_ids(rung)
    n = len(pool)  # anchored rung: max_objects == witness object count (enforced in build)

    def anchored_object_sequence(seed: int) -> list[str]:
        rng = stage_rng(seed, 0)
        seq = []
        for _ in range(12):
            env.reset(requested_ids=sample_request(pool, n, rng))
            seq.append(env._parked[0].plane_id)
        return seq

    seq = anchored_object_sequence(0)
    assert len(set(seq)) > 1, "the anchored object should vary across episodes (random k-subset)"
    assert seq == anchored_object_sequence(0), "the anchored sequence must be seed-reproducible"
