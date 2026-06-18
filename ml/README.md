# ml/ — learned-backend RL workspace (#607)

Dev/CI-only, never shipped in the wheel. Sub-project #1: the cold-joint RL
environment + reward (`HangarFitEnv`), reusing `hangarfit`'s geometry oracle.

## Run the tests
    pytest tests/ml/

## Entry points

- `python -m ml.train --save P` — train the policy (trivial stage or curriculum)
  and export its state_dict to `P` (needs the `[train]` extra / torch).
- `python -m ml.benchmark --record` — re-derive the RR-MC→tow reach baseline and
  write the committed fixture `tests/fixtures/ml/bench_baseline.json`
  (OFFLINE/dev-only, slow). `ml/benchmark.py` itself is torch-free.
- `python -m ml.eval --checkpoint P` — roll a trained policy (from `--save P`
  above) across the frozen reach-not-beat benchmark set and print the
  side-by-side both-rates table against the recorded RR-MC baseline (needs the
  `[train]` extra / torch).

### Vectorized training (#708)

`train_curriculum` supports `n_envs > 1` via two backends:

```bash
# 8 parallel envs, subprocess workers (recommended for throughput):
python -m ml.train --schedule curriculum --n-envs 8 --vec-backend subproc

# 4 in-process envs (CI-safe, no spawn overhead):
python -m ml.train --schedule curriculum --n-envs 4 --vec-backend sync
```

- `--n-envs 1` (default) keeps the legacy single-stream path byte-identical.
- `--vec-backend subproc` spawns N torch-free worker processes (`spawn` start method) for geometry + encoding;
  the main process holds the single batched policy forward + PPO update.
- `--vec-backend sync` runs the same N workers in-process (no spawn overhead; useful
  in CI or when the stage geometry is cheap).
- `Sync(seed, N)` and `Subproc(seed, N)` are **byte-identical**: workers are torch-free,
  so there is no cross-process torch nondeterminism.

### Inference (#5)

Export a trained policy to ONNX and run it torch-free via the deterministic
verifier. Exporting needs the `[train]` extra (torch **and** `onnx>=1.16`, which
`ml/export.py` uses to serialize the proto); inference needs only the
`[learned-infer]` extra (`pip install -e ".[learned-infer]"` installs onnxruntime;
no torch required at inference time).

```bash
# 1. Train (trivial schedule) and export both a state_dict and the ONNX model:
python -m ml.train --schedule trivial --save model.pt --save-onnx model.onnx  # [train] (torch + onnx)

# 2. Run the learned backend (torch-free at inference time):
hangarfit solve <scenario.yaml> --backend learned --weights model.onnx
hangarfit solve <scenario.yaml> --backend learned --weights model.onnx --render out.png --render-paths
```

Note: with weights from the **trivial** schedule (an undertrained policy) the
verifier will usually reject the proposal, so `solve` returns a no-layout result
(not an error) — the inference *plumbing* is what #5 delivers; reaching valid
dense layouts is the train-to-mastery work (#698 / #7).

The verifier (`collisions.check` + Caddy egress) is the sole arbiter of validity
— an invalid or incomplete proposal returns a no-layout result (never an
exception). Wheel distribution, CI lane, and signed Release-asset weights are
deferred to sub-project #6.

The benchmark judges validity via the product deterministic checker
`collisions.check` + Caddy egress (the spec's prime directive), **not** the env
oracle — the single shared `layout_valid` oracle is now used by both the env
reward and `ml/eval` (the prior over-enforcement of the inert maintenance bay
was fixed in 4c-ii, #694). Fixed-obstacle pre-placements from
`Scenario.fixed_obstacle_placements` are honoured by the env since 4c-ii (#693).

## Training knobs (4c-ii)

Four optional basin-escape knobs were added in sub-project #4c-ii (#693). All
are **default-neutral** — omitting them produces byte-identical results to prior
runs. Recommended treatment values for curriculum training runs:

| Flag | Default (neutral) | Recommended treatment | Effect |
|---|---|---|---|
| `--r-valid-park R` | `0.0` | `2.0` | Bonus per Park step when `layout_valid` passes; gates the reward on the product checker so only conflict-free placements are rewarded |
| `--dense-slot-potential` | off | on | Adds in-hangar nearest-free-pocket potential shaping; guides the agent toward open space while it is still placing |
| `--entropy-start S` | `None` (fixed coef) | `0.05` | Entropy coefficient anneal start; pairs with `--entropy-end` and `--entropy-anneal-iters`. With `--schedule curriculum` the schedule resets per rung (per-rung decay); with `--schedule trivial` it decays once over the run |
| `--entropy-end E` | `None` | `0.005` | Entropy anneal end value (consulted only when `--entropy-start` is set) |
| `--entropy-anneal-iters N` | `0` | `40` | Iterations over which to anneal entropy from start→end (0 = no schedule) |
| `--normalize-returns` | off | on | Std-only Welford return normalization before GAE; stabilises training across rungs with different reward scales. The running std is shared **run-level** across all rungs (not reset per rung) — a deliberate global-scale choice; revisit per-rung resets in the deferred run-to-mastery study if rung reward scales diverge sharply |

### One-line A/B validation command

```bash
# Control (neutral — no knobs):
python -m ml.train --schedule curriculum --max-iters-per-stage 30 --seed 0 --rollout-len 1024

# Treatment (all four knobs active):
python -m ml.train --schedule curriculum --max-iters-per-stage 30 --seed 0 --rollout-len 1024 \
  --r-valid-park 2.0 --dense-slot-potential \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns
```

Primary **eval-time** signal: `valid_placed` rising in treatment where control stalls
near 0. Leading indicators: `terminal_fraction` leaving ~0 (escapes place-nothing
basin); `fraction_placed − valid_placed` gap shrinking (escapes place-invalid basin);
entropy starting higher and decaying across rungs. **Note:** `valid_placed` /
`terminal_fraction` are *not* printed by `python -m ml.train` (it logs `mean_ep_reward`
+ promotions, plus `entropy` in the `trivial` schedule) — measure them via
`python -m ml.eval` on a saved checkpoint; the `/ml-ab` skill wraps the *train-time*
read of this A/B.

**Note:** A full run-to-mastery study and statistical reach-rate measurement
against the benchmark are deferred to the second half of #693. The knobs are
wired, unit-tested, and default-neutral; the A/B here is a smoke-level
demonstration that they move the easy-rung metric in the expected direction.

## Mastery-run levers (#710)

The #710 train-to-mastery work added run-enablement knobs and one reward fix. **Why a
reward fix and not the originally-planned "dense collision-progress reward":** a code-level
diagnosis found `valid_placed=0` is a **Park/drive-out economics** problem, not a
sparse-reward one. The `−w_col` collision penalty is charged **only** at a Park, while a
budget-exhaustion stop still pays `terminal_fraction` over already-parked objects with **no**
penalty on the abandoned one — so "drive until the step budget runs out" dodges the cliff
nearly free, which is the `fraction_placed` 0.991→0.476 collapse seen in the #697 baseline. A
new dense overlap reward would **not** fix this: it duplicates the already-shipped
`--dense-slot-potential` (its `active_misfit_m2` already enters Φ, so `γΦ(s′)−Φ(s)` *is* a
per-step active-overlap gradient) and, being potential-based shaping, is **policy-invariant**
(Ng–Harada–Russell) — it cannot move the optimum. So item 4 was **skipped** in favour of:

| Flag | Default (neutral) | Effect |
|---|---|---|
| `--r-unplaced-penalty R` | `0.0` | Terminal penalty per **unplaced** fraction: `terminal = r_terminal·frac − R·(1−frac)`. Charges abandonment so a valid Park out-earns driving to budget exhaustion. Pair with `--r-valid-park` for the positive pull. |
| `--checkpoint-out PATH` | off | Write a resume checkpoint after each rung (policy + optimizer + return-normalizer + architecture + completed rungs). |
| `--load PATH` | off | Resume: restore the above and **skip completed rungs**. Reuses the checkpoint's architecture (a conflicting `--d-model`/etc. raises). |
| `--d-model` / `--n-layers` / `--n-heads` | own defaults | Policy size (omitting keeps `HangarFitPolicy` defaults 128/2/4). |
| `--epochs` / `--minibatch-size` | `PPOConfig` defaults | PPO update epochs / minibatch size. |
| `--device {cpu,cuda}` | `cpu` | Opt-in GPU (non-deterministic fast path; cpu stays byte-identical). |
| `--metrics-out PATH` | off | Per-iter per-rung JSONL incl. the `valid_placed` curve. |

`--load`/`--checkpoint-out`/`--metrics-out`/`--promotion-*` are curriculum-only (fail loud
under `--schedule trivial`). The resume checkpoint (`ml/checkpoint.py`) is distinct from
`--save` (a bare `state_dict` for the ONNX/`ml.eval` consumer) and loads with
`weights_only=True`.

### Box-rung mastery gate recipe

```bash
# GPU, box rungs only, HONEST valid_placed promotion, resumable, 2 seeds:
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --max-iters-per-stage 50 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 2.0 --r-unplaced-penalty 25.0 \
  --metrics-out metrics-seed0.jsonl --checkpoint-out ck-seed0.pt --seed 0
```

Watch **both** `fraction_placed` (recovering off ~0.476 = the perverse incentive is breaking)
and `valid_placed` (the mastery axis). Guard against the opposite failure — `valid_rate`
dropping while `fraction_placed` spikes is the commit-anything pathology (penalty too high).
Flat `valid_placed` across both seeds is the empirical signal that reachability (the #712
start-state graft) is the next lever.

## Design
See `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`
and ADR-0027 (learned-path determinism scope).
