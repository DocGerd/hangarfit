"""Tests for ml/sweep.py — the torch-free concurrent multi-seed gate/sweep runner (#749).

PURE: stdlib only, no torch import/skip — the runner is pure orchestration over
``python -m ml.train`` / ``python -m ml.gate`` subprocesses, so it is exercised under
the [dev]-only CI. Every test injects a FAKE cell-runner via the ``run_cell`` seam, so
no real multi-minute training (and no torch) is ever spawned.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Sequence

import pytest

from ml.sweep import (
    CellResult,
    CellSpec,
    build_train_argv,
    cells_for_seeds,
    main,
    run_sweep,
)

# --------------------------------------------------------------------------------------
# CellSpec -> ml.train argv mapping (1:1, distinct paths per cell)
# --------------------------------------------------------------------------------------


def test_build_train_argv_injects_seed_and_distinct_paths():
    base = ["--schedule", "curriculum", "--max-iters-per-stage", "30"]
    cell = CellSpec(
        seed=2,
        metrics_out="out/metrics-seed2.jsonl",
        checkpoint_out="out/ck-seed2.pt",
        save="out/policy-seed2.pt",
    )
    argv = build_train_argv(cell, base)
    # The runner spawns `python -m ml.train ...` — the module invocation is the prefix.
    assert argv[:3] == ["-m", "ml.train"] or argv[:1] == ["ml.train"] or "ml.train" in argv
    # Base args are passed through unchanged.
    for tok in base:
        assert tok in argv
    # The cell's seed + distinct paths are present exactly once each.
    assert argv.count("--seed") == 1
    assert argv[argv.index("--seed") + 1] == "2"
    assert argv[argv.index("--metrics-out") + 1] == "out/metrics-seed2.jsonl"
    assert argv[argv.index("--checkpoint-out") + 1] == "out/ck-seed2.pt"
    assert argv[argv.index("--save") + 1] == "out/policy-seed2.pt"


def test_build_train_argv_omits_unset_optional_paths():
    cell = CellSpec(seed=0, metrics_out="m0.jsonl")  # no checkpoint_out / save
    argv = build_train_argv(cell, [])
    assert "--checkpoint-out" not in argv
    assert "--save" not in argv
    assert argv[argv.index("--metrics-out") + 1] == "m0.jsonl"


def test_build_train_argv_does_not_duplicate_a_base_seed():
    # A --seed in the base args must not survive next to the cell's injected --seed,
    # or ml.train would see a conflicting double flag (last-wins is luck, not contract).
    cell = CellSpec(seed=7, metrics_out="m7.jsonl")
    argv = build_train_argv(cell, ["--seed", "0", "--rollout-len", "512"])
    assert argv.count("--seed") == 1
    assert argv[argv.index("--seed") + 1] == "7"
    assert "--rollout-len" in argv


def test_build_train_argv_strips_argparse_equals_form_of_cell_owned_flags():
    # argparse also accepts --flag=value; that inline form must be stripped too, or a base
    # `--seed=0` survives next to the cell's `--seed 7` (last-wins is luck, not contract).
    cell = CellSpec(seed=7, metrics_out="m7.jsonl", checkpoint_out="ck7.pt")
    argv = build_train_argv(
        cell,
        ["--seed=0", "--metrics-out=base.jsonl", "--checkpoint-out=base.pt", "--rollout-len=512"],
    )
    # No surviving equals-form of a cell-owned flag.
    assert not any(tok.startswith("--seed=") for tok in argv)
    assert not any(tok.startswith("--metrics-out=") for tok in argv)
    assert not any(tok.startswith("--checkpoint-out=") for tok in argv)
    # The cell's values are the only ones present, exactly once each.
    assert argv.count("--seed") == 1
    assert argv[argv.index("--seed") + 1] == "7"
    assert argv[argv.index("--metrics-out") + 1] == "m7.jsonl"
    assert argv[argv.index("--checkpoint-out") + 1] == "ck7.pt"
    # A non-cell-owned equals-form flag is left untouched.
    assert "--rollout-len=512" in argv


def test_cells_for_seeds_maps_one_cell_per_seed_with_distinct_paths(tmp_path):
    cells = cells_for_seeds([0, 1, 3], out_dir=str(tmp_path), tag="trio")
    assert [c.seed for c in cells] == [0, 1, 3]
    # Distinct metrics paths per seed — the independent-outputs acceptance criterion.
    paths = [c.metrics_out for c in cells]
    assert len(set(paths)) == len(paths)
    for c in cells:
        assert str(c.seed) in (c.metrics_out or "")


# --------------------------------------------------------------------------------------
# Concurrent orchestration + aggregation
# --------------------------------------------------------------------------------------


def _fake_runner(exit_by_seed: dict[int, int], *, record: list[int] | None = None):
    """A fake ``run_cell`` that returns a per-seed canned exit code (no subprocess)."""

    def run_cell(cell: CellSpec, argv: Sequence[str]) -> int:
        if record is not None:
            record.append(cell.seed)
        return exit_by_seed.get(cell.seed, 0)

    return run_cell


def test_run_sweep_all_pass_aggregates_to_zero():
    cells = cells_for_seeds([0, 1], out_dir="/tmp/x", tag="t")
    result = run_sweep(cells, base_argv=[], max_concurrency=2, run_cell=_fake_runner({0: 0, 1: 0}))
    assert result.ok is True
    assert result.exit_code == 0
    assert [r.exit_code for r in result.cells] == [0, 0]
    assert all(r.passed for r in result.cells)


def test_run_sweep_one_seed_fails_aggregates_to_nonzero():
    cells = cells_for_seeds([0, 1], out_dir="/tmp/x", tag="t")
    result = run_sweep(cells, base_argv=[], max_concurrency=2, run_cell=_fake_runner({0: 0, 1: 1}))
    assert result.ok is False
    assert result.exit_code != 0
    # The failing cell is identifiable.
    failed = [r for r in result.cells if not r.passed]
    assert [r.seed for r in failed] == [1]


def test_run_sweep_crashed_cell_is_loud_nonzero():
    # A child that raises (crash) MUST surface as a non-zero cell, not be swallowed —
    # a silent crash would corrupt a 2-seed verdict (the #749 risk).
    def crashing_runner(cell: CellSpec, argv: Sequence[str]) -> int:
        if cell.seed == 1:
            raise RuntimeError("boom in seed 1")
        return 0

    cells = cells_for_seeds([0, 1], out_dir="/tmp/x", tag="t")
    result = run_sweep(cells, base_argv=[], max_concurrency=2, run_cell=crashing_runner)
    assert result.ok is False
    assert result.exit_code != 0
    crashed = [r for r in result.cells if r.seed == 1][0]
    assert crashed.passed is False
    assert crashed.error is not None
    assert "boom" in crashed.error


def test_run_sweep_results_are_in_deterministic_seed_order_regardless_of_finish_order():
    # Seed 0 finishes LAST (sleeps), seed 1 finishes first — results must still be
    # ordered by seed, so the aggregated summary is reproducible.
    def staggered_runner(cell: CellSpec, argv: Sequence[str]) -> int:
        time.sleep(0.05 if cell.seed == 0 else 0.0)
        return 0

    cells = cells_for_seeds([0, 1], out_dir="/tmp/x", tag="t")
    result = run_sweep(cells, base_argv=[], max_concurrency=2, run_cell=staggered_runner)
    assert [r.seed for r in result.cells] == [0, 1]


def test_run_sweep_honors_concurrency_cap():
    # Track concurrent in-flight cells; with cap=2 it must never exceed 2.
    lock = threading.Lock()
    state = {"in_flight": 0, "max_seen": 0}

    def counting_runner(cell: CellSpec, argv: Sequence[str]) -> int:
        with lock:
            state["in_flight"] += 1
            state["max_seen"] = max(state["max_seen"], state["in_flight"])
        time.sleep(0.05)
        with lock:
            state["in_flight"] -= 1
        return 0

    cells = cells_for_seeds([0, 1, 2, 3], out_dir="/tmp/x", tag="t")
    result = run_sweep(cells, base_argv=[], max_concurrency=2, run_cell=counting_runner)
    assert result.ok is True
    assert state["max_seen"] <= 2


def test_run_sweep_passes_base_argv_and_per_cell_argv_to_runner():
    seen: list[tuple[int, list[str]]] = []

    def capturing_runner(cell: CellSpec, argv: Sequence[str]) -> int:
        seen.append((cell.seed, list(argv)))
        return 0

    cells = cells_for_seeds([0, 1], out_dir="/tmp/x", tag="t")
    base = ["--schedule", "curriculum", "--rollout-len", "512"]
    run_sweep(cells, base_argv=base, max_concurrency=2, run_cell=capturing_runner)
    seen.sort()
    for seed, argv in seen:
        assert "ml.train" in argv
        assert "--rollout-len" in argv
        assert argv[argv.index("--seed") + 1] == str(seed)


# --------------------------------------------------------------------------------------
# main() entry point
# --------------------------------------------------------------------------------------


def test_main_returns_zero_when_all_cells_pass(capsys):
    code = main(
        ["--seeds", "0,1", "--out-dir", "/tmp/sweep-x", "--tag", "t", "--"],
        run_cell=_fake_runner({0: 0, 1: 0}),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "PASS" in out.upper()


def test_main_returns_nonzero_when_any_cell_fails(capsys):
    code = main(
        ["--seeds", "0,1", "--out-dir", "/tmp/sweep-x", "--tag", "t"],
        run_cell=_fake_runner({0: 0, 1: 1}),
    )
    out = capsys.readouterr().out
    assert code != 0
    assert "FAIL" in out.upper()


def test_main_rejects_duplicate_seeds_loudly(capsys):
    # Duplicate seeds would build two cells with the SAME metrics/checkpoint/save paths,
    # so concurrent children race-corrupt the shared gate input — reject it loudly.
    with pytest.raises(ValueError, match="duplicate"):
        main(
            ["--seeds", "0,1,1", "--out-dir", "/tmp/sweep-x", "--tag", "t"],
            run_cell=_fake_runner({0: 0, 1: 0}),
        )


def test_main_parses_seed_list_into_one_cell_each(capsys):
    record: list[int] = []
    code = main(
        ["--seeds", "0,1,2", "--out-dir", "/tmp/sweep-x", "--tag", "t"],
        run_cell=_fake_runner({0: 0, 1: 0, 2: 0}, record=record),
    )
    assert code == 0
    assert sorted(record) == [0, 1, 2]


def test_main_forwards_extra_train_args_after_separator():
    seen: list[list[str]] = []

    def capturing_runner(cell: CellSpec, argv: Sequence[str]) -> int:
        seen.append(list(argv))
        return 0

    code = main(
        ["--seeds", "0", "--out-dir", "/tmp/sweep-x", "--tag", "t", "--", "--rollout-len", "512"],
        run_cell=capturing_runner,
    )
    assert code == 0
    assert "--rollout-len" in seen[0]
    assert seen[0][seen[0].index("--rollout-len") + 1] == "512"


def test_cell_result_is_immutable():
    r = CellResult(seed=0, exit_code=0)
    assert r.passed is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.exit_code = 1  # type: ignore[misc]
