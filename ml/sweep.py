"""ml/sweep.py — a concurrent multi-seed gate/sweep runner (#749, throughput Wave 1).

Pure ORCHESTRATION over the existing torch-free building blocks: it spawns K
**unmodified** ``python -m ml.train`` subprocesses (one per seed/cell), each with its
own ``--seed`` plus distinct ``--metrics-out`` / ``--checkpoint-out`` / ``--save``
paths, runs them concurrently up to a cap, collects their exit codes, and emits an
aggregated pass/fail verdict. No training-loop edits — every child is **byte-identical**
to running it alone (the #749 determinism note: co-locating cells on one GPU adds
nothing beyond ``--device cuda``).

WHY a thin orchestrator (not a richer harness): the mastery deliverable is the two/three
-seed gate (the ``ml/README`` trio-box recipe), run serially today. One on-policy run is
throughput-capped by its synchronous step-dependency, so the ~26-core box sits idle.
Running K seeds **concurrently** cuts the whole-sweep wall-clock ~**2×** (NOT 3×: 5.5
cores is the time-averaged busy fraction of one bursty run, so K aligned rollout bursts
oversubscribe). ``--max-concurrency`` defaults to **2** and is **RAM-bound** (~10 GB/run
→ K=3 risks OOM on a 31 GB box), not core-bound.

LOUD by contract (the #749 risk): any non-zero child — *or a child that crashes* — makes
the runner exit non-zero. A silently-swallowed crash would corrupt a 2-seed verdict, so a
crashed cell is surfaced as a failed :class:`CellResult` with its error text, never hidden.

PURE: stdlib only (``argparse`` / ``subprocess`` / ``concurrent.futures`` /
``dataclasses``), **no torch** — so it runs under the ``[dev]``-only CI and collects on
the torch-free subset. The spawned children need ``[train]`` (torch); the orchestrator
does not.

Entry point::

    # Two-seed trio-box gate, two cells concurrently (cap 2), trio-box train args after `--`:
    python -m ml.sweep --seeds 0,1 --out-dir sweep-out --tag trio -- \
        --schedule curriculum --device cuda --n-envs 16 --rollout-len 512 \
        --max-iters-per-stage 300 --stop-after-rung trio-box --load ck-pairbox.pt ...

    python -m ml.gate sweep-out/metrics-trio-seed0.jsonl --rung trio-box   # torch-free roll-up
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

# A cell-runner takes a cell + its fully-built argv and returns the child's exit code. The
# default spawns a real subprocess; tests inject a fake so no torch/training is spawned.
RunCell = Callable[["CellSpec", "Sequence[str]"], int]


@dataclass(frozen=True, slots=True)
class CellSpec:
    """One sweep cell: a single ``ml.train`` run with a distinct ``--seed`` and distinct
    output paths so concurrent cells never clobber each other's artifacts."""

    seed: int
    metrics_out: str | None = None
    checkpoint_out: str | None = None
    save: str | None = None


@dataclass(frozen=True, slots=True)
class CellResult:
    """The outcome of one cell. ``exit_code`` is the child's exit status (0 = the rung
    ran cleanly to the trainer's own verdict — NOT the gate verdict, which ``ml.gate``
    reads post-hoc from ``metrics_out``). ``error`` is non-None only when the cell
    *crashed* (the runner raised) rather than exiting non-zero normally."""

    seed: int
    exit_code: int
    error: str | None = None

    @property
    def passed(self) -> bool:
        """A cell passes iff it exited zero and did not crash."""
        return self.exit_code == 0 and self.error is None


@dataclass(frozen=True, slots=True)
class SweepResult:
    """The aggregated verdict. ``cells`` is in deterministic seed order (NOT finish
    order). ``ok`` iff every cell passed; ``exit_code`` is 0 iff ``ok``."""

    cells: list[CellResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.cells)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def cells_for_seeds(
    seeds: Sequence[int],
    *,
    out_dir: str,
    tag: str,
    checkpoint_out: bool = True,
    save: bool = False,
) -> list[CellSpec]:
    """Build one :class:`CellSpec` per seed with distinct per-seed output paths under
    ``out_dir`` (the independent-outputs contract: no two cells share a metrics /
    checkpoint / save path). ``metrics_out`` is always set (it is what ``ml.gate`` reads
    to roll up the verdict); ``checkpoint_out`` is on by default (crash-survivable resume
    cells), ``save`` off (state_dict export is opt-in)."""
    out = Path(out_dir)
    cells: list[CellSpec] = []
    for seed in seeds:
        cells.append(
            CellSpec(
                seed=seed,
                metrics_out=str(out / f"metrics-{tag}-seed{seed}.jsonl"),
                checkpoint_out=str(out / f"ck-{tag}-seed{seed}.pt") if checkpoint_out else None,
                save=str(out / f"policy-{tag}-seed{seed}.pt") if save else None,
            )
        )
    return cells


def build_train_argv(cell: CellSpec, base_argv: Sequence[str]) -> list[str]:
    """Map one cell 1:1 to a ``python -m ml.train`` argv (the ``-m`` invocation prefix
    is the caller's; this returns the ``["-m", "ml.train", *args]`` tail).

    The cell's ``--seed`` and distinct output paths are injected; any ``--seed`` /
    ``--metrics-out`` / ``--checkpoint-out`` / ``--save`` already present in ``base_argv``
    is **stripped** first so the cell's values are the only ones the child sees (a
    surviving base ``--seed`` would mean two conflicting flags, where last-wins is luck,
    not contract)."""
    cell_owned = {"--seed", "--metrics-out", "--checkpoint-out", "--save"}
    filtered = _strip_flags(base_argv, cell_owned)

    argv: list[str] = ["-m", "ml.train", *filtered, "--seed", str(cell.seed)]
    if cell.metrics_out is not None:
        argv += ["--metrics-out", cell.metrics_out]
    if cell.checkpoint_out is not None:
        argv += ["--checkpoint-out", cell.checkpoint_out]
    if cell.save is not None:
        argv += ["--save", cell.save]
    return argv


def _strip_flags(argv: Sequence[str], flags: set[str]) -> list[str]:
    """Drop each cell-owned flag (and its value) whose flag name is in ``flags``, in
    BOTH argparse spellings: the space-separated ``--flag VALUE`` pair and the inline
    ``--flag=VALUE`` form. Assumes each is a value-taking option (matches the four
    cell-owned flags); leaves all other tokens untouched."""
    out: list[str] = []
    skip_next = False
    for tok in argv:
        if skip_next:
            skip_next = False  # this token is a stripped flag's value — drop it
            continue
        # tok.split("=", 1)[0] is the flag name for both `--seed 0` and `--seed=0`.
        name = tok.split("=", 1)[0]
        if name in flags:
            # The inline `--flag=value` carries its own value; the bare `--flag` does not,
            # so only the bare form consumes the next token.
            skip_next = "=" not in tok
            continue
        out.append(tok)
    return out


def _default_run_cell(cell: CellSpec, argv: Sequence[str]) -> int:
    """Spawn ``python <argv>`` (i.e. ``python -m ml.train ...``) as a subprocess and
    return its exit code, tee'ing the child's stderr through this process's stderr so a
    crashed/failing cell is loud. Inherits cwd (must be the repo root so the top-level
    ``ml`` package resolves) and the env (so a local CUDA torch / thread caps apply)."""
    cmd = [sys.executable, *argv]
    sys.stderr.write(f"[sweep] seed {cell.seed}: launching {' '.join(cmd)}\n")
    sys.stderr.flush()
    # No timeout= is deliberate: a hung trainer should be VISIBLY stuck (operator-killable),
    # not silently SIGKILL'd mid-rung into a half-written metrics file that the gate then
    # misreads as a clean negative.
    completed = subprocess.run(cmd, check=False)  # noqa: S603 — argv is built, not shell
    sys.stderr.write(f"[sweep] seed {cell.seed}: exited {completed.returncode}\n")
    sys.stderr.flush()
    return completed.returncode


def _run_one(cell: CellSpec, argv: list[str], run_cell: RunCell) -> CellResult:
    """Run a single cell, converting a *crash* (the runner raising) into a loud failed
    :class:`CellResult` rather than letting it escape and abort the whole sweep — a
    crashed child must fail loud, not silently corrupt the verdict (#749)."""
    try:
        code = run_cell(cell, argv)
    except Exception as exc:  # noqa: BLE001 — a crashing child must surface, not propagate
        sys.stderr.write(f"[sweep] seed {cell.seed}: CRASHED — {exc!r}\n")
        sys.stderr.flush()
        return CellResult(seed=cell.seed, exit_code=1, error=f"{type(exc).__name__}: {exc}")
    return CellResult(seed=cell.seed, exit_code=code)


def run_sweep(
    cells: Sequence[CellSpec],
    *,
    base_argv: Sequence[str],
    max_concurrency: int = 2,
    run_cell: RunCell | None = None,
) -> SweepResult:
    """Run every cell concurrently, at most ``max_concurrency`` at a time, and aggregate.

    Each cell's argv is built via :func:`build_train_argv` from the shared ``base_argv``
    plus the cell's seed/paths. Results are returned in **deterministic seed order** (the
    cells' input order), regardless of which child finished first, so the aggregated
    summary is reproducible. ``run_cell`` is the injectable job-spawning seam (default:
    :func:`_default_run_cell`, a real subprocess)."""
    if max_concurrency < 1:
        raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")
    runner = run_cell if run_cell is not None else _default_run_cell

    workers = min(max_concurrency, len(cells)) or 1
    # Submit in input order and read the futures back in the SAME order, so `results` is
    # seed-ordered (the deterministic-aggregation contract) even though completion is not.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_run_one, cell, build_train_argv(cell, base_argv), runner) for cell in cells
        ]
        results = [f.result() for f in futures]
    return SweepResult(cells=results)


def _summary(result: SweepResult) -> str:
    """A scannable per-cell + headline pass/fail block."""
    lines = [
        f"  seed {r.seed:>3}: {'PASS' if r.passed else 'FAIL'} "
        f"(exit {r.exit_code}{f', {r.error}' if r.error else ''})"
        for r in result.cells
    ]
    headline = "SWEEP PASS" if result.ok else "SWEEP FAIL"
    n_pass = sum(1 for r in result.cells if r.passed)
    lines.append(f"{headline} — {n_pass}/{len(result.cells)} cells passed")
    return "\n".join(lines)


def _parse_seeds(spec: str) -> list[int]:
    """Parse a comma-separated seed list (e.g. ``0,1,3``) into ints, preserving order.

    Rejects **duplicate** seeds loudly: two cells with the same seed would share the same
    per-seed metrics/checkpoint/save paths, so concurrent children would race-corrupt the
    shared gate input — silently producing one usable cell instead of the requested K."""
    seeds = [int(tok) for tok in spec.split(",") if tok.strip()]
    if not seeds:
        raise ValueError(f"--seeds parsed to no seeds: {spec!r}")
    if len(set(seeds)) != len(seeds):
        raise ValueError(f"--seeds has duplicates: {spec!r}")
    return seeds


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ml.sweep",
        description=(
            "Concurrent multi-seed sweep of `python -m ml.train` cells (#749). One cell per "
            "--seed, run up to --max-concurrency at once; any failed/crashed child -> non-zero. "
            "Pass per-cell train args after a `--` separator."
        ),
        epilog=(
            "--max-concurrency is RAM-bound (~10 GB/run -> K=3 risks OOM on a 31 GB box), "
            "not core-bound; expect ~2x sweep wall-clock, not Kx (aligned rollout bursts "
            "oversubscribe). Roll up the per-seed metrics with `python -m ml.gate`."
        ),
    )
    p.add_argument(
        "--seeds",
        required=True,
        help="comma-separated seed list, one cell per seed (e.g. 0,1 for the two-seed gate)",
    )
    p.add_argument(
        "--out-dir",
        required=True,
        help="directory for per-cell --metrics-out/--checkpoint-out/--save artifacts",
    )
    p.add_argument(
        "--tag",
        default="sweep",
        help="filename tag for per-cell artifacts (metrics-<tag>-seed<N>.jsonl etc.)",
    )
    p.add_argument(
        "--max-concurrency",
        type=int,
        default=2,
        help="max cells in flight at once (default 2; RAM-bound, NOT core-bound)",
    )
    p.add_argument(
        "--save",
        action="store_true",
        help="also pass a distinct per-cell --save state_dict path to each ml.train cell",
    )
    p.add_argument(
        "--no-checkpoint-out",
        action="store_true",
        help="do not pass a per-cell --checkpoint-out (default: a crash-survivable resume "
        "checkpoint per cell)",
    )
    return p


def main(argv: Sequence[str] | None = None, *, run_cell: RunCell | None = None) -> int:
    """``python -m ml.sweep --seeds 0,1 --out-dir D --tag trio -- <train args...>``.

    Splits argv on the first ``--`` into the sweep's own options and the pass-through
    ``ml.train`` args, builds one cell per seed, runs them concurrently, prints the
    aggregated summary, and returns the runner exit code (0 iff every cell passed).
    ``run_cell`` is the injectable seam (tests pass a fake; default = real subprocess)."""
    raw = list(sys.argv[1:] if argv is None else argv)
    sweep_args, base_argv = _split_on_separator(raw)

    args = build_argparser().parse_args(sweep_args)
    seeds = _parse_seeds(args.seeds)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    cells = cells_for_seeds(
        seeds,
        out_dir=args.out_dir,
        tag=args.tag,
        checkpoint_out=not args.no_checkpoint_out,
        save=args.save,
    )
    result = run_sweep(
        cells,
        base_argv=base_argv,
        max_concurrency=args.max_concurrency,
        run_cell=run_cell,
    )
    print(_summary(result))
    return result.exit_code


def _split_on_separator(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first ``--`` into (sweep options, pass-through train args). No
    ``--`` => everything is a sweep option and the pass-through is empty."""
    argv = list(argv)
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


if __name__ == "__main__":
    # Disjoint core blocks + per-child thread caps are an operator concern (set
    # OMP_NUM_THREADS / taskset / sched_setaffinity in the launching env); the
    # orchestrator inherits the env into each child unchanged.
    raise SystemExit(main())
