"""Wave 4 (#755): the opt-in one-iteration-stale ``--pipeline-update`` pipeline.

While ``ppo_update`` runs on the live policy, the vectorized workers begin the
NEXT rollout under a frozen snapshot of the **pre-update** policy — overlapping
the CPU rollout with the (GPU) update. The flag defaults off (byte-identical
sequential training); on, it is a one-iteration-stale-policy departure that is
**re-gated on a two-seed ``ml.gate`` valid_placed delta** (a follow-up long-run,
NOT byte-identity).

These tests pin the MECHANISM (not the learning outcome): the snapshot is an
independent frozen copy, the flag genuinely gates the concurrent path, and the
pipeline preserves the rung run-structure (ceiling + gate) of the sequential path.
The exact per-iteration stats are NON-deterministic by design — the background
rollout and the main-thread update both draw from torch's global RNG, so their
interleaving is a (safe, mutex-guarded) race — which is why #755 is re-gated on a
two-seed valid_placed delta, never a byte-diff.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

import ml.train as train_mod  # noqa: E402
from ml.curriculum import (  # noqa: E402
    CurriculumSchedule,
    DifficultyConfig,
    PromotionPolicy,
    Stage,
)
from ml.policy import HangarFitPolicy  # noqa: E402
from ml.train import _clone_policy, build_argparser, train_curriculum  # noqa: E402


def _tiny_schedule(threshold: float = -1.0) -> CurriculumSchedule:
    """A single tiny rung so a vectorized run finishes in a few seconds."""
    stage = Stage(
        name="t0",
        difficulty=DifficultyConfig(max_objects=1, per_object_step_budget=12, total_step_budget=12),
        hangar_path="data/hangar.yaml",
        fleet_path="data/fleet.yaml",
        fleet_ids=("fuji",),
        clearance_m=0.05,
    )
    pol = PromotionPolicy(metric="fraction_placed", window=1, threshold=threshold, max_iters=3)
    return CurriculumSchedule(stages=(stage,), policy=pol)


_TINY_POLICY = {"d_model": 32, "n_layers": 1, "n_heads": 2}


def test_cli_pipeline_update_flag_defaults_off():
    """``--pipeline-update`` parses to ``args.pipeline_update`` (default off)."""
    parser = build_argparser()
    assert parser.parse_args(["--schedule", "curriculum"]).pipeline_update is False
    assert (
        parser.parse_args(["--schedule", "curriculum", "--pipeline-update"]).pipeline_update is True
    )


def test_clone_policy_is_independent_frozen_snapshot():
    """``_clone_policy`` returns a deep copy whose params do NOT alias the live
    policy — so the concurrent rollout reads pre-update weights while ``ppo_update``
    mutates the original in place."""
    policy = HangarFitPolicy(**_TINY_POLICY)
    snapshot = _clone_policy(policy)
    # Same values at snapshot time...
    p_live = next(policy.parameters())
    p_snap = next(snapshot.parameters())
    assert torch.equal(p_live, p_snap)
    assert p_live is not p_snap  # distinct storage
    # ...and mutating the live policy must NOT bleed into the snapshot.
    with torch.no_grad():
        p_live.add_(1.0)
    assert not torch.equal(p_live, p_snap)


def test_pipeline_update_off_does_not_spawn_executor(monkeypatch):
    """Flag OFF must never touch the concurrent machinery — proving the default
    path is the untouched sequential loop (byte-identical)."""

    def _boom(*_a, **_k):
        raise AssertionError("ThreadPoolExecutor created with --pipeline-update OFF")

    monkeypatch.setattr(train_mod, "ThreadPoolExecutor", _boom)
    # n_envs=2 sync exercises the vectorized loop; OFF must not create the executor.
    history = train_curriculum(
        seed=0,
        schedule=_tiny_schedule(),
        rollout_len=16,
        n_envs=2,
        vec_backend="sync",
        policy_kwargs=_TINY_POLICY,
        pipeline_update=False,
    )
    assert history.promotions  # ran to completion on the sequential path


def test_pipeline_update_on_uses_executor(monkeypatch):
    """Flag ON must route through the concurrent executor — proving the lever
    actually fires (not a silent no-op)."""
    sentinel = {"created": False}
    real_executor = train_mod.ThreadPoolExecutor

    def _spy(*a, **k):
        sentinel["created"] = True
        return real_executor(*a, **k)

    monkeypatch.setattr(train_mod, "ThreadPoolExecutor", _spy)
    train_curriculum(
        seed=0,
        schedule=_tiny_schedule(),
        rollout_len=16,
        n_envs=2,
        vec_backend="sync",
        policy_kwargs=_TINY_POLICY,
        pipeline_update=True,
    )
    assert sentinel["created"] is True


def test_pipeline_preserves_sequential_run_structure():
    """The pipeline must respect the rung ceiling + gate exactly like the sequential
    path: an unreachable threshold runs all ``max_iters`` iterations and promotes by
    ``"cap"``, recording one row per iteration. Only the run STRUCTURE is asserted —
    the exact ep_stats are non-deterministic by design (racy concurrent RNG + stale
    policy), so this never asserts float reproducibility."""
    sched = _tiny_schedule(threshold=2.0)  # unreachable -> cap at max_iters=3

    def _run(*, pipeline_update: bool):
        return train_curriculum(
            seed=0,
            schedule=sched,
            rollout_len=16,
            n_envs=2,
            vec_backend="sync",
            policy_kwargs=_TINY_POLICY,
            pipeline_update=pipeline_update,
        )

    seq = _run(pipeline_update=False)
    pipe = _run(pipeline_update=True)
    # Same promotion mechanics (stage + reason) and same number of recorded iterations
    # — the pipeline neither drops, duplicates, nor over-runs the ceiling.
    assert [(s, by) for s, _it, by in pipe.promotions] == [(s, by) for s, _it, by in seq.promotions]
    assert pipe.promotions[-1][2] == "cap"
    assert len(pipe.iterations) == len(seq.iterations) == 3
    # #816: the pipeline path records AFTER ppo_update (moved from before) — structure /
    # promotion-equivalence is covered by the asserts above; here we assert the per-iteration
    # telemetry now lands in the metrics JSONL like the serial/vec paths.
    from ml.curriculum import history_metric_records

    pipe_recs = history_metric_records(pipe)
    assert len(pipe_recs) == 3
    for r in pipe_recs:
        assert isinstance(r["epochs_run"], float) and r["epochs_run"] >= 1.0
        assert "entropy_coef" in r
