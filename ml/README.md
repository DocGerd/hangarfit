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
| `--validity-conditional-terminal` | off | Terminal credits the **valid** placed fraction (invalid layout → 0), so an overlapping pile no longer books `+r_terminal`. The #714 multi-object fix; also closes the budget-exhaustion branch. |
| `--solo-box-rung` | off | Insert an opt-in `solo-box` rung (1 object, **whole fleet**) after `trivial` so single-object competency transfers before the 2-object jump (#714). Curriculum-only. |
| `--seed-anchor` | off | Insert an opt-in `pair-anchored` rung **before** `pair-box`: one of its 2 objects is pre-parked at a committed-witness pose (`seed_anchor_k=1`) and the agent only drives the other in — scaffolding 2-object joint discovery with a valid 1-object start (#712). Curriculum-only. |
| `--mixed-anchor` | off | Insert an opt-in `pair-mixed` rung **before** `pair-box`: each episode randomly starts anchored (k=1) or empty (k=0) with probability `anchor_prob=0.5`, drawn from the curriculum's seeded stream. Keeps empty-start episodes in the training mix so the policy does not collapse to the place-nothing pole on the empty-start `pair-box`. Pair with `--seed-anchor` so `pair-mixed` lands between `pair-anchored` and `pair-box` (not required — `--mixed-anchor` alone inserts it directly before `pair-box`). Curriculum-only. (#712 follow-up) |

`--load`/`--checkpoint-out`/`--metrics-out`/`--promotion-*`/`--solo-box-rung`/`--seed-anchor`/`--mixed-anchor`
are curriculum-only (fail loud under `--schedule trivial`). The resume checkpoint
(`ml/checkpoint.py`) is distinct from `--save` (a bare `state_dict` for the ONNX/`ml.eval`
consumer) and loads with `weights_only=True`.

### What the #710 levers achieved, and the #714 multi-object fix

The #710 economics rebalance (`--r-valid-park 30 --r-unplaced-penalty 8 --dense-slot-potential`
+ entropy anneal + `--normalize-returns`) **mastered the trivial (1-object) rung** — the
first competency promotion of the learned backend (valid_placed 0.018→0.936, fraction held,
reward positive). But every **≥2-object** rung still collapsed, oscillating between
place-nothing and *commit-everything-invalidly* (parking a heap of overlapping objects;
reward spikes of −9k to −37k). Root cause: the terminal credited `fraction_placed`
**regardless of validity** — invisible at N=1 (fraction is 0/1) but a free `+r_terminal` for
invalid piles at N≥2. The #714 fix is two default-neutral levers: `--validity-conditional-terminal`
(credit only the *valid* fraction) and `--solo-box-rung` (decouple the count jump from the
sampling-pool jump).

The #714 re-gate then **confirmed the 2-object joint-discovery wall**: `trivial` and `solo-box`
master by competency (single-object whole-fleet transfer works), but `pair-box` stalls at
`valid_placed ≈ 0.054` and the `--normalize-returns`-off control is strictly worse (so the
normalizer is load-bearing, not the blocker — the residual is genuine joint discovery). That
result satisfied the documented trigger for **#712 (`--seed-anchor` start-state graft)**, now
wired: pre-park a k-prefix of a committed witness layout (a k-prefix of a valid layout is
provably valid, so no runtime solver) and drive the remaining N−k in. Step 1 ships a single
k=1 rung (`pair-anchored`); later rungs can anneal k→0. See the pair-anchored gate recipe
below.

### Box-rung mastery gate recipe (#714 re-gate)

```bash
# GPU, HONEST valid_placed promotion, resumable, the #714 levers active. Run 2 seeds.
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --max-iters-per-stage 25 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 8.0 --dense-slot-potential \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal --solo-box-rung \
  --metrics-out metrics-seed0-v3.jsonl --checkpoint-out ck-seed0-v3.pt --seed 0
```

Read **`valid_placed`** (the honest mastery axis), **not** `valid_rate` (an empty layout is
trivially valid → inflated). Expected: `solo-box` masters like `trivial`; `pair-box` then
lifts off the place-nothing pole toward the **one-valid plateau (~0.5)** — that is the
win condition for this increment. Reaching 0.9 (two *simultaneously* valid) may need a
further lever (#712 start-state graft / a pose-curriculum). **Kill-criterion:** if by
~iter 20 a rung still produces commit-everything spikes (reward < −3000 with
`fraction_placed` > 0.5 and `valid_rate` < 0.1), the terminal fix did not bite — re-open
toward the return-normalizer (run with `--normalize-returns` off) before more discovery work.

### Pair-anchored gate recipe (#712 seed-anchor, step 1)

The proof-first step of #712: insert the single `pair-anchored` (k=1) rung before `pair-box`
and check whether a valid 1-object start lets the agent learn to place the **second** object.

```bash
# Same #714 economics as above, plus --seed-anchor (the pair-anchored rung before pair-box).
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --max-iters-per-stage 25 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 8.0 --dense-slot-potential \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal --solo-box-rung --seed-anchor \
  --metrics-out metrics-seed0-anchor.jsonl --checkpoint-out ck-seed0-anchor.pt --seed 0
```

The `pair-anchored` rung pre-parks 1 object and drives 1. An agent that **keeps the valid
1-object partial** (parks nothing, or parks object 2 validly) scores `valid_placed ≥ ~0.5`
(the anchor is a valid 1-object layout, counted in the denominator); committing object 2
*invalidly* still scores 0 for that episode, so the rung average only settles at ~0.5 once the
place-nothing behavior dominates. The **win condition** is the rung average *lifting above 0.5*
toward 0.9 — i.e. the agent learning to place object 2 *validly given* object 1 — and ideally
that competency transferring so the downstream empty-start `pair-box` lifts off its
place-nothing pole too. **If pair-anchored cannot exceed its 0.5 floor**, a valid start
alone is insufficient and the next lever is the full k=2→1→0 anneal (more scaffolding) or a
pose-curriculum. Read `valid_placed`, not `valid_rate`. The witness is
`tests/fixtures/ml/witness_box.yaml` (a committed valid 2-object box layout; every k-prefix is
validated by `tests/ml/test_stage_builder.py::test_witness_box_*`).

### Mixed-anchor gate recipe (#712 follow-up, step 2)

The #712 cap-80 pre-check confirmed k=1 masters but the empty-start `pair-box` still collapses
to place-nothing (the k=1→k=0 start-state cliff). The `pair-mixed` rung keeps empty-start
episodes in the training mix so the policy bridges the cliff.

```bash
# Same #714 economics + --seed-anchor, plus --mixed-anchor (pair-mixed before pair-box).
# cap 80 so each rung clears the 40-iter entropy warmup into exploitation.
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --max-iters-per-stage 80 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 8.0 --dense-slot-potential \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal --solo-box-rung \
  --seed-anchor --mixed-anchor \
  --metrics-out metrics-seed0-mixed.jsonl --checkpoint-out ck-seed0-mixed.pt --seed 0
```

WIN: `pair-mixed` lifts and ideally promotes by competency, AND the downstream all-empty
`pair-box` no longer collapses (lifts off 0.000). Read `valid_placed`, not `valid_rate`.

### Graded-economics + PPO-clipping gate recipe (#720, L5+L4)

The mixed-anchor gate failed seed-0 (`pair-mixed` capped oscillating ~0.2, `pair-box` collapsed
to `valid_placed 0.000`). A multi-agent diagnosis root-caused the cliff as *economics ×
discoverability*: from empty, do-nothing is a small bounded loss (≈−8 observed on the failed seed-0
gate run) while any exploratory mis-Park books the **unclipped** `−w_col·overlap` (−5000…−12000),
so place-nothing is the genuine reward argmax.
The #720 levers shift that argmax (L5) and tame the resulting sawtooth (L4); all knobs are
default-neutral (0/None ⇒ byte-identical), so they layer onto the recipe above.

```bash
# Above mixed-anchor config, plus the #720 L5 economics + L4 PPO trust-region knobs.
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --max-iters-per-stage 80 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 25.0 --dense-slot-potential \
  --w-col 20.0 --valid-park-grade-scale 4.0 --r-first-valid 15.0 \
  --reward-clip 10.0 --value-clip-eps 0.2 --target-kl 0.03 \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal --solo-box-rung \
  --seed-anchor --mixed-anchor \
  --metrics-out metrics-seed0-l5l4.jsonl --checkpoint-out ck-seed0-l5l4.pt --seed 0
```

WIN: `pair-box` `valid_placed` lifts decisively **off 0.000** (trending ≥0.1 within 80 iters,
climbing — read `valid_placed` NOT `valid_rate`), the −5000…−12000 sawtooth is gone (L4 did its
job), and `trivial`/`solo-box`/`pair-anchored` still promote-by-competency (no upstream regression
from the lower `w_col`). The `--w-col`/`--valid-park-grade-scale`/`--r-first-valid` magnitudes
above are a **starting point** — the open knife-edge is graded credit vs `w_col` at the near-miss
overlap (the grade must beat the collision penalty into the slot without making piling profitable),
so expect a short sweep. If `pair-box` stays pinned with the sawtooth gone, that isolates the
failure to pure discoverability → escalate to a pose-scaffold rung (L6a). Run a second seed.

## Design
See `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`
and ADR-0027 (learned-path determinism scope).
