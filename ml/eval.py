"""python -m ml.eval — roll a trained HangarFitPolicy out across the frozen benchmark set
and print the side-by-side both-rates table (sub-project #4c-i, #607). Requires the [train]
extra. The torch-free machinery (scenarios, predicate, RR-MC baseline) lives in ml.benchmark."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import torch

from ml.benchmark import (
    BENCH_SET,
    BenchScenario,
    ReachVerdict,
    _layout_valid,
    _verdict_from,
    build_scenario_env,
    load_baseline,
    validate_baseline,
)
from ml.encoding import EncoderConfig, encode
from ml.policy import HangarFitPolicy


def load_policy(
    checkpoint_path: str | Path, *, policy_kwargs: dict | None = None
) -> HangarFitPolicy:
    """Construct a policy, load a saved state_dict, and put it in eval() mode (required for
    deterministic argmax action selection)."""
    policy = HangarFitPolicy(**(policy_kwargs or {}))
    # weights_only=True: the checkpoint is a pure tensor state_dict, so refuse to unpickle
    # arbitrary Python objects (torch.load's default is an arbitrary-code-execution vector).
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    policy.load_state_dict(state)
    policy.eval()
    return policy


def policy_reach(
    scenario: BenchScenario, policy: HangarFitPolicy, *, encoder: EncoderConfig | None = None
) -> ReachVerdict:
    """Roll `policy` out deterministically (argmax) on `scenario` and apply the success
    predicate (spec §4): valid (product checker `_layout_valid` on the terminal layout) +
    routable-by-construction (no swept intrusion on any leg). Raises NotImplementedError for
    fixed-obstacle scenarios (deferred to 4c-ii)."""
    # #827: the encoder's ego-frame follows the policy's own architecture (single source of
    # truth), so an ego policy gets 28-wide ego tokens instead of a 24-vs-28 shape crash.
    enc = encoder or EncoderConfig(ego_centric=getattr(policy, "relative_encoder", False))
    env = build_scenario_env(scenario)  # raises on fixed-obstacle scenarios
    bodies = {**env.fleet, **env.ground_objects}
    policy.eval()
    obs = env.reset()
    max_swept = 0.0
    info = None
    done = False
    with torch.no_grad():
        while not done and obs.active is not None:
            obs_t = encode(obs, env.hangar, bodies, enc)
            tr = obs.active.body.effective_turn_radius_m()
            _idx, _logprob, action = policy.act(obs_t, turn_radius_m=tr, deterministic=True)
            obs, _reward, done, info = env.step(action)
            max_swept = max(max_swept, info.terms.get("hard_swept", 0.0))
    if info is None:
        raise ValueError(f"policy_reach: episode produced no steps for {scenario.name!r}")
    final_valid = _layout_valid(env._layout()) if done else False
    return _verdict_from(
        parked=info.placed,
        total=info.total,
        done=done,
        final_valid=final_valid,
        max_swept=max_swept,
    )


def run_benchmark(policy: HangarFitPolicy) -> list[dict[str, str]]:
    """Assemble the both-rates rows: RR-MC from the committed fixture, policy live."""
    baseline = load_baseline()
    validate_baseline(baseline)  # loud on missing/stale rows
    rows: list[dict[str, str]] = []
    for sc in BENCH_SET:
        rrmc_cell = "reached" if baseline[sc.name]["reached"] else "missed"
        try:
            verdict = policy_reach(sc, policy)
            policy_cell = "reached" if verdict.reached else f"missed ({verdict.reason})"
        except NotImplementedError:
            policy_cell = "n/a (GO env -> 4c-ii)"
        except Exception as exc:  # reporting tool: surface one row's failure, don't abort
            policy_cell = f"error ({type(exc).__name__})"
        rows.append({"name": sc.name, "kind": sc.kind, "rrmc": rrmc_cell, "policy": policy_cell})
    return rows


def _print_table(rows: list[dict[str, str]]) -> None:
    header = f"{'scenario':24}  {'kind':8}  {'RR-MC':8}  policy"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['name']:24}  {r['kind']:8}  {r['rrmc']:8}  {r['policy']}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the reach-not-beat eval benchmark.")
    parser.add_argument("--checkpoint", required=True, help="path to a torch state_dict .pt")
    parser.add_argument("--d-model", type=int, default=128)
    parser.add_argument("--n-layers", type=int, default=2)
    parser.add_argument("--n-heads", type=int, default=4)
    args = parser.parse_args(argv)
    policy = load_policy(
        args.checkpoint,
        policy_kwargs={
            "d_model": args.d_model,
            "n_layers": args.n_layers,
            "n_heads": args.n_heads,
        },
    )
    _print_table(run_benchmark(policy))


if __name__ == "__main__":
    main()
