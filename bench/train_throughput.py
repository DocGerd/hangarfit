"""python -m bench.train_throughput — a transitions/sec canary for the ml/ training loop (#750).

`bench/profile_pipeline` measures only the **solver** pipeline; this module is its twin for the
dev/CI-only `ml/` RL training loop (epic #607). It runs a small, fixed, deterministic training
loop on a CPU `SyncVectorEnv` and reports **transitions/sec** (steps/sec) and **iters/sec** with
the per-phase split — rollout collection (`collect_rollout_vec`) vs the PPO update (`ppo_update`)
— so the rest of throughput Wave 1 (#735 geometry, IPC/cache work) is measured, not eyeballed,
and a silent per-iteration regression is catchable.

Like `profile_pipeline`, this **binds on a fixed step COUNT** (``iterations × rollout_len ×
n_envs`` transitions, mirroring the #381 `max_restarts` binding), NOT a wall-clock budget — so the
*work* is fixed and only machine speed varies run-to-run. It needs the `[train]` extra (torch);
`bench/` is dev/CI-only and never shipped in the wheel.

Examples::

    python -m bench.train_throughput                       # default fixed loop, table to stdout
    python -m bench.train_throughput --json                # machine-readable JSON
    python -m bench.train_throughput --iters 10 --rollout-len 128 --n-envs 4
    python -m bench.train_throughput --warmup-iters 1      # discard cold-start iters from the rate

NOT a CI gate. A throughput *ceiling* is jitter-prone on shared runners (see the #750 risk note),
so — unlike `profile_pipeline --gate` — there is no `--gate` here: it reports numbers, never
enforces them. The smoke test (`tests/ml/test_train_throughput_smoke.py`) asserts it RUNS and
emits the expected JSON keys with a positive rate, not a timing threshold.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass


@dataclass
class ThroughputResult:
    """One fixed-loop throughput measurement (timed phases + derived rates).

    All ``*_s`` are summed wall-clock seconds over the *timed* iterations (the warmup iterations
    are excluded from both the timings and the transition count, so the rates reflect the
    steady state, not the cold start)."""

    iterations: int  # timed iterations (after warmup)
    warmup_iters: int
    rollout_len: int
    n_envs: int
    transitions: int  # timed transitions = iterations * rollout_len * n_envs
    rollout_s: float  # summed collect_rollout_vec wall-clock
    update_s: float  # summed ppo_update wall-clock
    total_s: float  # rollout_s + update_s
    transitions_per_s: float  # transitions / total_s
    iters_per_s: float  # iterations / total_s
    rollout_frac: float  # rollout_s / total_s (the share spent collecting rollouts)


def run_throughput(
    *,
    seed: int = 0,
    iterations: int = 5,
    warmup_iters: int = 1,
    rollout_len: int = 128,
    n_envs: int = 2,
    d_model: int = 32,
    n_layers: int = 1,
    n_heads: int = 2,
) -> ThroughputResult:
    """Run a fixed deterministic training loop on CPU and time the two phases separately.

    Drives ``n_envs`` trivial-stage envs (the easiest curriculum rung) through a
    ``SyncVectorEnv`` — in-process, no multiprocessing, so it runs anywhere torch imports — for
    ``warmup_iters + iterations`` PPO iterations. Each iteration is one ``collect_rollout_vec``
    (``rollout_len * n_envs`` transitions) followed by one ``ppo_update``. The first
    ``warmup_iters`` iterations are run but EXCLUDED from the timings and the transition count, so
    the reported rates reflect the steady state (torch lazily allocates on the first forward).

    Imports torch transitively (via ``ml.train`` / ``ml.ppo``), so it raises ``ImportError``
    without the ``[train]`` extra — the smoke test ``importorskip('torch')``s accordingly."""
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1, got {iterations}")
    if warmup_iters < 0:
        raise ValueError(f"warmup_iters must be >= 0, got {warmup_iters}")
    if rollout_len < 1 or n_envs < 1:
        raise ValueError(f"rollout_len and n_envs must be >= 1, got {rollout_len}, {n_envs}")

    import torch

    from ml.encoding import EncoderConfig
    from ml.policy import HangarFitPolicy
    from ml.ppo import PPOConfig, ppo_update
    from ml.train import build_trivial_env, collect_rollout_vec
    from ml.vector_env import SyncVectorEnv, _EnvWorker

    torch.manual_seed(seed)
    enc = EncoderConfig()
    # n_envs identical trivial-stage workers (the trivial env has no RNG, so they are clones; the
    # benchmark measures throughput, not learning, so a fixed env set is exactly what we want).
    workers = [_EnvWorker(build_trivial_env(seed), enc, None) for _ in range(n_envs)]
    vec_env = SyncVectorEnv(workers)
    policy = HangarFitPolicy(d_model=d_model, n_layers=n_layers, n_heads=n_heads)
    cfg = PPOConfig(minibatch_size=min(PPOConfig().minibatch_size, rollout_len * n_envs))
    optimizer = torch.optim.Adam(policy.parameters(), lr=cfg.lr)

    rollout_s = 0.0
    update_s = 0.0
    for it in range(warmup_iters + iterations):
        timed = it >= warmup_iters
        t0 = time.perf_counter()
        buf, _ = collect_rollout_vec(vec_env, policy, enc, rollout_len)
        t1 = time.perf_counter()
        ppo_update(policy, optimizer, buf, cfg)
        t2 = time.perf_counter()
        if timed:
            rollout_s += t1 - t0
            update_s += t2 - t1

    total_s = rollout_s + update_s
    transitions = iterations * rollout_len * n_envs
    # total_s is wall-clock over real torch work, so it is > 0; guard the division defensively
    # rather than risk a ZeroDivisionError masking a degenerate (e.g. zero-iteration) config.
    if total_s <= 0.0:
        raise RuntimeError(f"non-positive timed wall-clock ({total_s} s) — measurement failed")
    return ThroughputResult(
        iterations=iterations,
        warmup_iters=warmup_iters,
        rollout_len=rollout_len,
        n_envs=n_envs,
        transitions=transitions,
        rollout_s=rollout_s,
        update_s=update_s,
        total_s=total_s,
        transitions_per_s=transitions / total_s,
        iters_per_s=iterations / total_s,
        rollout_frac=rollout_s / total_s,
    )


def _print_table(r: ThroughputResult) -> None:
    print("── ml/ training-loop throughput canary (#750) ──")
    print(
        f"  config        : n_envs={r.n_envs} rollout_len={r.rollout_len} "
        f"iters={r.iterations} (+{r.warmup_iters} warmup)"
    )
    print(f"  transitions   : {r.transitions:,}")
    print(f"  rollout       : {r.rollout_s:8.3f} s  ({100.0 * r.rollout_frac:5.1f}%)")
    print(f"  update        : {r.update_s:8.3f} s  ({100.0 * (1.0 - r.rollout_frac):5.1f}%)")
    print(f"  total         : {r.total_s:8.3f} s")
    print(f"  throughput    : {r.transitions_per_s:10.1f} transitions/s")
    print(f"  iters/s       : {r.iters_per_s:10.3f} iters/s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="ml/ training-loop transitions/sec canary (#750; needs the [train] extra)"
    )
    ap.add_argument("--seed", type=int, default=0, help="torch + env seed (default 0)")
    ap.add_argument("--iters", type=int, default=5, help="timed PPO iterations (default 5)")
    ap.add_argument(
        "--warmup-iters",
        type=int,
        default=1,
        help="iterations run but excluded from the rate (cold-start; default 1)",
    )
    ap.add_argument("--rollout-len", type=int, default=128, help="steps per env per iter (def 128)")
    ap.add_argument("--n-envs", type=int, default=2, help="SyncVectorEnv width (default 2)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    result = run_throughput(
        seed=args.seed,
        iterations=args.iters,
        warmup_iters=args.warmup_iters,
        rollout_len=args.rollout_len,
        n_envs=args.n_envs,
    )

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        _print_table(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
