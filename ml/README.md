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
- `python -m ml.reach_rate [--policy P]` — the **statistical reach-rate harness**
  (#711): sample a population of fill scenarios and report **reach-rate ± Wilson CI**
  per scenario-kind for multi-alternative RR-MC and (with `--policy P`) a trained
  policy. The RR-MC arm is torch-free; `--policy` needs `[train]`. See "Statistical
  reach-rate (#711)" below for the methodology + budget.

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

## Statistical reach-rate (#711)

`ml/benchmark.py` + `ml/eval.py` answer a **binary** question on **4 frozen** scenarios:
"does RR-MC / the policy reach *these*?" `ml/reach_rate.py` lifts that to a **rate** over a
**sampled population**: reach-rate ± **Wilson** CI per scenario-kind, for both arms. Both
arms judge reach by the **same** predicate the bench uses — the product checker
(`geometry_oracle.layout_valid` = `collisions.check` + Caddy egress, #694) **and**
routable-by-construction (`plan_fill` gives every placeable body a real tow path) — never
the env oracle's parked-score validity.

- **Population (v1):** `sample_population` draws fill scenarios that vary the **fleet subset**
  (`k ∈ [k_min, k_max]` aircraft from a pool) on a fixed roomy hangar, deterministic in `seed`.
  Varying hangar geometry / GO placements (the issue's other axes) is a documented extension.
- **Multi-alternative RR-MC:** `rrmc_reach_multi` solves for `--alternatives N` and counts
  RR-MC-reached if **any** candidate is valid + fully routable — strictly stronger than
  `benchmark.rrmc_reach` (`alternatives=1`, best-spread only). Load-bearing the moment the
  solver yields valid-but-**un**routable dense layouts.
- **Multi-sample policy:** `policy_reach_count` rolls the policy out `--samples M` times per
  scenario (stochastic, seeded per sample) so its rate carries variance for the CI.
- **Wilson CI** (not normal-approx) because a reach-rate sits at the 0/1 extremes, where the
  normal interval gives negative / zero-width bounds.

```bash
# RR-MC reach-rate over a small sampled population (torch-free).
python -m ml.reach_rate --scenarios 8 --k-min 2 --k-max 4 --alternatives 4 --max-restarts 16
# …plus the policy arm from a trained checkpoint (needs [train]).
python -m ml.reach_rate --scenarios 8 --policy model.pt --samples 16
```

**Budget (the issue's cost caveat).** RR-MC reach is the expensive arm (a `solve` + `plan_fill`
per scenario, ≈10 s/restart on dense anchors — 200 restarts was "unrunnable", ≈30–50 min/anchor).
So the harness defaults to a **small** population at a **modest** restart budget, and a
large-population RR-MC baseline is meant to be recorded once and **frozen** (mirroring the
`bench_baseline.json` freeze, spec D4); pre-register the population `seed` + budgets before
measuring so the policy-vs-RR-MC comparison can't go circular. The policy arm is cheap (rollouts,
no solver), so it affords a larger population × more samples.

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
| `--anchor-trio-notch` | off | Insert an opt-in `trio-notch-anchored` rung **before** `trio-notch`: 1 of its 3 notch-witness objects is pre-parked at a committed pose (`seed_anchor_k=1`) and the agent drives the other two in — the trio analogue of `--seed-anchor`, on the **real notch hangar**. Targets the diagnosed cold-start *coverage minimum* (the policy validly parks one aircraft then abandons the other two, vp~0.25 on both notch seeds). Curriculum-only. (#736) |
| `--stop-after-rung NAME` | off | Truncate the ladder after `NAME` (that rung is the last trained; every rung after it is dropped). Applied **after** the graft flags above, so a name they introduce (`pair-mixed`) is valid. The #722 sweep lever: `--stop-after-rung pair-box` lets a resumed cell stop cleanly instead of grinding on into `trio-*`. Unknown rung → loud `ValueError`. Curriculum-only; absent ⇒ byte-identical. |
| `--promotion-metric` / `--promotion-threshold` / `--promotion-window` | schedule policy (`valid_placed` / `0.9` / `3`) | Competency-gate overrides. A rung promotes when the mean of the last `--promotion-window` **iterations'** honest per-rollout `--promotion-metric` (the same `valid_placed` the `--metrics-out` JSONL and `ml.gate` report) clears `--promotion-threshold`. `--promotion-window` counts **iterations**, not episodes — the #742 fix: the gate previously thresholded only the last ~20 *episodes* of the latest rollout (a `deque(maxlen=window)` tail), a noisy estimator that false-promoted `by competency` on a lucky autocorrelated streak while the honest per-iteration mean was well below threshold. Curriculum-only. |
| `--auto-budget` (+ `--auto-budget-max-iters N` ⌀1000, `--auto-budget-min-iters N` ⌀30, `--auto-budget-min-level L` ⌀0.05) | off | Slope-aware per-rung budget (#734): replace the fixed `--max-iters-per-stage` cap with a closed loop — a Theil–Sen slope over the **honest per-iteration** promotion-metric series (the same `valid_placed` the gate reads, #742) **extends** the rung while it climbs and **stops early** on a plateau (or the ceiling `N`). The `--auto-budget-min-level` **floor-guard** (#743) refuses to plateau-stop while the recent level is below `L` — a flat-at-floor *warmup* is not convergence, and slope alone cannot tell the two apart (both ~0); `0` disables it. Raise `--auto-budget-min-iters` for a hard rung with a long pre-climb. Distinct from the manual `--stop-after-rung`. Curriculum-only; absent ⇒ the fixed cap, byte-identical. |

`--load`/`--checkpoint-out`/`--metrics-out`/`--promotion-*`/`--solo-box-rung`/`--seed-anchor`/`--mixed-anchor`/`--anchor-trio-notch`/`--stop-after-rung`/`--auto-budget`
are curriculum-only (fail loud under `--schedule trivial`). The resume checkpoint
(`ml/checkpoint.py`) is distinct from `--save` (a bare `state_dict` for the ONNX/`ml.eval`
consumer) and loads with `weights_only=True`.

**Honest competency gate (#742/#743).** The trainer's `promoted by competency` and the
`--auto-budget` slope are now fit on **one** per-iteration series — each rung iteration
contributes its full-rollout `valid_placed` mean, the exact signal `ml.gate` re-reads from the
JSONL — instead of a per-episode `deque` tail. The trainer's verdict is therefore as trustworthy
as `ml.gate`'s (still re-grade with `python -m ml.gate` as a torch-free cross-check, but the two
no longer disagree by construction). The companion `--auto-budget` floor-guard stops a hard rung
from being truncated during its flat pre-climb warmup.

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
The #720 levers shift that argmax (L5) and tame the resulting sawtooth (L4). The **L5 economics**
knobs (`--r-valid-park`, `--r-unplaced-penalty`, `--w-col`, `--valid-park-grade-scale`,
`--r-first-valid`) stay default-neutral (0/None ⇒ byte-identical) and layer onto the recipe above.
The three **L4 trust-region** knobs **graduated to the default in #728** (`--reward-clip 50`,
`--value-clip-eps 0.2`, `--target-kl 0.03` — the two-seed-validated values): an unflagged run now
carries them, so they are shown explicitly in the recipe below only for reproducibility. Pass
`--no-reward-clip` / `--no-value-clip-eps` / `--no-target-kl` to disable any of them — that is the
only way to reach the unclipped behavior (e.g. the seed-1 clip-OFF A/B control), since there is no
in-band "off" value (`--reward-clip 0` zeroes all rewards; `--target-kl 0` stops after the first
epoch).

```bash
# Above mixed-anchor config, plus the #720 L5 economics + L4 PPO trust-region knobs.
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --max-iters-per-stage 80 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 25.0 --dense-slot-potential \
  --w-col 20.0 --valid-park-grade-scale 4.0 --r-first-valid 15.0 \
  --reward-clip 50.0 --value-clip-eps 0.2 --target-kl 0.03 \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal --solo-box-rung \
  --seed-anchor --mixed-anchor \
  --metrics-out metrics-seed0-l5l4.jsonl --checkpoint-out ck-seed0-l5l4.pt --seed 0
```

**GATE RESULT (#722 checkpoint-resume sweep, 2026-06-19): two-seed PASS — the empty-start
`pair-box` cliff is broken.** Run as a sweep with the #722 `--stop-after-rung` tooling (train the
ladder once through `pair-mixed`, then `--load` and sweep only the empty-start `pair-box` rung).
The empty-start `pair-box` — `valid_placed=0.000` in every prior gate — now **promotes by
competency** on both seeds (seed 0 at iter 27, `vp` 0.80; seed 1 at iter 19, `vp` 0.85), placing
*both* objects validly with `valid_rate` rising (no piling — `--validity-conditional-terminal`
holds).

**L4 trust-region clipping is load-bearing, not optional.** The sweep tested dropping it (the
main-grid hypothesis that `--validity-conditional-terminal` + `--normalize-returns` would cover
stability). A controlled A/B settled it: same upstream checkpoint, same `--seed 0`, byte-identical
iter 0, the *only* difference the three L4 flags — clip **off** collapses to place-nothing (`vp`
peaks 0.24 then decays to ~0 as `fraction_placed` 0.79→0.02), clip **on** masters. The residual
deep-penalty episodes (`mean_ep_reward` ≈−1400 in the clip-off run, down from the −5000…−12000 band
before L5) are a `−w_col·overlap` gradient outlier that drives PPO into the place-nothing absorbing
state; clamping the per-step reward in the update is what lets the policy stay in the *placing*
regime long enough to learn 2-object joint placement. The full ladder needs all four ingredients — L5 graded economics (start off 0.000) +
`--seed-anchor`/`--mixed-anchor` (keep empty-start episodes in the training distribution) + **L4
clipping** (don't flee to place-nothing) + `--validity-conditional-terminal` (place *validly*, not
pile).

**`--reward-clip 50` (not 10):** `reward_clip` clamps the *total* per-step reward to ±50. `50` keeps
the per-step **graded** valid-park bonus (`r_valid_park 30 + r_first_valid 15 = 45`) below the clip
so the L5 near-miss gradient survives, while clamping the deep `−w_col·overlap` spikes to −50. (The
episode-*completing* valid park step also books `r_terminal·fraction ≈ 50`, so that one step does
saturate the clamp — the intent is preserving the graded near-miss gradient, not the terminal
credit.) `reward_clip 10` would clip even the graded bonus (45 > 10), flattening the L5 gradient;
`50` is the validated value (two-seed mastery). The
no-upstream-regression check still holds (`trivial`/`solo-box`/`pair-anchored` all promote by
competency at `w_col=20`). Read `valid_placed`, NOT `valid_rate` (an empty layout is vacuously
"valid", so `valid_rate→1` under place-nothing is the *failure* signature).

### Trio-box gate recipe (#730 — does the four-lever ladder generalize past N=2?)

The two-seed `pair-box` PASS above broke the *2-object* cliff. The open question is whether the
same four-lever ladder clears the **3-object** `trio-box` rung (`max_objects=3`, already in
`DEFAULT_LADDER`) — historically every ≥2-object rung collapsed, and `trio-box` is the first
≥2-object rung after the validated `pair-box`. This is a **checkpoint-resume sweep**: take a
checkpoint whose `completed_stages` **end at `pair-box`**, then train **only** `trio-box` with
`--stop-after-rung trio-box`. Produce that pair-box-ending checkpoint by re-running the L5+L4
pair-box recipe above **with `--stop-after-rung pair-box --checkpoint-out ck-seed0-pairbox.pt`** —
the truncation is what makes `pair-box` the last completed rung (the un-truncated recipe trains the
whole ladder, so its checkpoint would already contain `trio-box` and the resume below would skip it).

> **Verify the resume source first** (the single most common way to silently corrupt the gate):
> the checkpoint's last completed rung MUST be `pair-box`. If `trio-box` (or anything after it) is
> already in `completed_stages`, the resumed run **skips `trio-box` and trains nothing**.
> ```bash
> python -c "from ml.checkpoint import load_checkpoint; print(load_checkpoint('ck-seed0-pairbox.pt').completed_stages)"
> # expect: [..., 'pair-mixed', 'pair-box']   (ends at pair-box)
> ```

The L4 clip knobs are now the default (#728) but are kept explicit below for reproducibility.

```bash
# Per seed (run twice, --seed 0 and --seed 1, with the matching pair-box checkpoint).
# --load resumes; the loop SKIPS trivial…pair-box (already in completed_stages) and trains trio-box.
# The cap is 300, not 80: trio-box's honest valid_placed was still CLIMBING at iter 136 (peak ~0.71)
# when an undersized cap last cut it off — give it room. The competency gate stops the rung early
# the moment it genuinely masters (`--promotion-window` iterations averaging >= the threshold).
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --max-iters-per-stage 300 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 25.0 --dense-slot-potential \
  --w-col 20.0 --valid-park-grade-scale 4.0 --r-first-valid 15.0 \
  --reward-clip 50.0 --value-clip-eps 0.2 --target-kl 0.03 \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal --solo-box-rung \
  --seed-anchor --mixed-anchor --stop-after-rung trio-box \
  --load ck-seed0-pairbox.pt \
  --metrics-out metrics-seed0-trio.jsonl --checkpoint-out ck-seed0-trio.pt --seed 0
```

**Fixed cap vs `--auto-budget`.** The command above is the **fixed-cap** variant (`--max-iters-per-stage 300`).
To let the rung size its own budget instead, pass `--auto-budget` (+ optionally `--auto-budget-max-iters N`,
`--auto-budget-min-iters N`, `--auto-budget-min-level L`) and **drop `--max-iters-per-stage`** — it is
*ignored* under `--auto-budget` (the loop bound becomes `--auto-budget-max-iters`, default 1000). The #743
floor-guard now keeps `--auto-budget` from truncating trio-box's flat pre-climb warmup, so it is safe for
this late-climbing rung; the fixed cap stays the simplest, most reproducible default.

**Resume gotchas (the single most likely way to corrupt the gate):**
- **Re-pass `--solo-box-rung --seed-anchor --mixed-anchor` on every resumed cell.** The checkpoint
  stores completed rung *names*, not the schedule shape — omit a graft and the rebuilt ladder no
  longer matches, so the skip-completed logic silently re-trains or reshapes rungs.
- **Do not `pip install .[train]`** if you have a local CUDA torch — it clobbers your `~/.local`
  build. Run `ml.train` from the repo root (the top-level `ml/` package is not on the editable
  install's path).
- Gate scratch (`metrics-*.jsonl`, `ck-*.pt`, and run logs redirected to `train-*.log`) is
  gitignored (#717) — don't commit run artifacts.

**Read the result with the gate harness** (torch-free, `ml/gate.py`) instead of eyeballing the
JSONL — it headlines `valid_placed` (never `valid_rate`) and flags the piling basin. Since #742 the
trainer's own `promoted by competency` reads the **same** honest per-iteration `valid_placed` the
gate does (no longer a noisy last-20-episode tail that false-positived), so the two agree by
construction — `ml.gate` remains the torch-free cross-check and the canonical verdict:

```bash
python -m ml.gate metrics-seed0-trio.jsonl --rung trio-box   # exit 0=mastered, 1=not, 2=no-data
python -m ml.gate metrics-seed1-trio.jsonl --rung trio-box
```

Outcomes: **`mastered`** (`valid_placed ≥ 0.9` — the WIN) · **`piling`** (placed much but validly
little: committing objects invalidly, *not* a win — distrust any apparent progress) · **`place-nothing`**
(fled to do-nothing: a *clean* collapse that routes to an L6a pose-scaffold rung) · **`in-progress`**
(placing validly and climbing, just under threshold — give it more iters).

**WIN = `trio-box` mastered on BOTH seeds.** A clean two-seed `place-nothing` is a valid negative
(the four-lever ladder does *not* generalize to N=3 → pose-scaffold). A `piling` verdict means the
ladder is unstable at N=3 and needs the economics re-tuned before re-gating.

### Trio-notch-anchored gate recipe (#736 — break the notch cold-start coverage minimum)

The notch trio is the real frontier: on the **real** Herrenteich notch hangar, the empty-start
`trio-notch` rung stalls at `valid_placed`~0.25 on **both** seeds — not place-nothing, not
piling, but a **coverage minimum**: the policy validly parks **one** aircraft (`valid_rate`~0.9)
and abandons the other two, because a 2nd/3rd commitment risks the hard `w_col`/`w_oob` penalty
(diagnosed from `metrics-notch-s0/s1.jsonl`). `trio-box` itself proves the trio is learnable
(seed-0 reached 0.93), and `examples/herrenteich/layout.yaml` is a *valid 8-object witness on
that exact hangar* — so the trio physically fits; the wall is joint multi-body discovery from a
cold start. `--anchor-trio-notch` inserts a `trio-notch-anchored` rung before `trio-notch`: it
pre-parks 1 of 3 notch-witness objects (k=1) and the agent drives the other two in, converting
the cliff into a ramp (the trio analogue of the #712 `--seed-anchor` scaffold that reached 0.94
on the box).

```bash
# Two seeds. The #730 trio-box economics, plus --anchor-trio-notch (the new scaffold rung).
# Stop after the scaffold + its un-anchored successor so the run answers the transfer question
# without grinding into trio-notch-strict.
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --auto-budget --auto-budget-max-iters 120 --auto-budget-min-level 0.1 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 25.0 --dense-slot-potential \
  --w-col 20.0 --valid-park-grade-scale 4.0 --r-first-valid 15.0 \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal \
  --solo-box-rung --seed-anchor --mixed-anchor --anchor-trio-notch \
  --stop-after-rung trio-notch \
  --metrics-out metrics-notch-anchored-s0.jsonl --checkpoint-out ck-notch-anchored-s0.pt --seed 0
# …then --seed 1 with the s1 file names.
```

**Pre-registered kill-criteria** (both seeds, honest `valid_placed` gate, window=iterations per
#742/#743):
- **WIN:** the `trio-notch-anchored` rung reaches `valid_placed ≥ 0.9` AND the follow-on
  *un-anchored* `trio-notch` climbs materially above the measured **0.34 ceiling** (target ≥ 0.6)
  on **both** seeds — i.e. the scaffold *transfers*, it does not just memorize the anchor.
- **KILL:** by the rung's iteration budget either (a) the anchored rung itself stalls < 0.9, or
  (b) it masters anchored but un-anchored `trio-notch` stays ≤ ~0.34 on either seed. Either
  refutes the lever for this rung; fall back to the Section-A representation knobs (aux-heads /
  critic-pretrain), which target a *different* failure mode (representation/variance, not the
  discovery cliff). Grade with `python -m ml.gate metrics-notch-anchored-s*.jsonl --rung
  trio-notch-anchored` (and `--rung trio-notch` for transfer).

The witness is `tests/fixtures/ml/witness_notch.yaml` (a committed valid 3-object subset of the
real notch layout; every k-prefix is validated by
`tests/ml/test_stage_builder.py::test_witness_notch_*`).

**Result (2026-06-23 two-seed run) — KILL, lever refuted.** The `trio-notch-anchored` rung
plateaued at **peak vp 0.333 on both seeds** (well below 0.9), in the *two faces* of the coverage
minimum: seed-0 converged to **place-nothing-new** (`fp=0.333, vr=1.000` — keeps only the freebie
k=1 anchor, drives in nothing), seed-1 to **piling** (`fp≈0.8, vr≈0.3` — drives objects in but
invalidly). The downstream un-anchored `trio-notch` then **collapsed to vp≈0.000** on both seeds
(no transfer). The sharpened diagnosis: a *valid 1-object start does not teach the policy to add a
commitment* — so the notch wall is **not** cold-start joint discovery (which this scaffold
addresses) but the **marginal-commitment economics** (place-nothing-new vs invalid-pile). Per the
pre-registration, the next lever is the Section-A representation knobs (aux-heads / critic-pretrain)
or harder per-commitment economics — **not** more start-state scaffolding. The rung itself stays as
opt-in, default-neutral infrastructure for a future combined attempt.

### Spatial-token representation A/B (#809/#810 — does richer spatial vision break the notch plateau?)

The #736 KILL pre-registered the **Section-A representation knobs** as the next lever, on the theory
that the policy's `AdaptiveAvgPool2d(1)` global-average-pools the occupancy raster and is therefore
*spatially blind* on dense packing (it sees "how full" but not "where the gaps are"). `--spatial-tokens`
(#810) tests that directly: it replaces the single pooled summary with object tokens that **cross-attend
to per-cell free-space tokens**. The A/B is the **#736 notch recipe above with `--spatial-tokens` added**
(toggle only the architecture; default-off is byte-identical), trained full-ladder-from-scratch — the new
net cannot `--load` a global-pool checkpoint (architecture mismatch), so it re-climbs every rung.

```bash
# Identical to the trio-notch-anchored recipe above, plus --spatial-tokens (the only change).
python -u -m ml.train --schedule curriculum --device cuda --n-envs 16 \
  --rollout-len 512 --auto-budget --auto-budget-max-iters 120 --auto-budget-min-level 0.1 \
  --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 25.0 --dense-slot-potential \
  --w-col 20.0 --valid-park-grade-scale 4.0 --r-first-valid 15.0 \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal \
  --solo-box-rung --seed-anchor --mixed-anchor --anchor-trio-notch \
  --stop-after-rung trio-notch --spatial-tokens \
  --metrics-out metrics-notch-spatial-s0.jsonl --checkpoint-out ck-notch-spatial-s0.pt --seed 0
```

**Result (2026-06-23 seed-0 run) — KILL, lever refuted.** The spatial-token net climbs the lower ladder
normally (trivial 0.95 / solo-box 0.93 / pair-anchored 0.97 / pair-mixed 0.92 promote by competency;
pair-box peak 0.84 by budget-plateau) but then lands on the **identical frontier plateau as global-pool**:

| Rung | Spatial-token (seed 0) | Global-pool control |
|---|---|---|
| `trio-box` | peak vp **0.31** — coverage-minimum (`valid_rate≈0.97 × fraction≈0.32`, park-one-abandon-two) | n/a — `trio-box 0.93` was the *fixed-cap-300* #730 recipe, not this auto-budget-120 ladder; the same-recipe control value is undocumented |
| `trio-notch-anchored` | vp **0.333** exactly (`valid_rate 1.000 × fraction 0.333` = place-nothing-new fixed point) | **0.333** |
| `trio-notch` (transfer) | ~**0.000** (place-nothing) | ~0.000 |

The frontier rung converged to vp **0.333 — the same value as the documented control** — and sat there
flat: it reached the *place-nothing-new economic fixed point*, **not** a budget-truncated climb. (Seed-0
only; unlike the #736 two-seed gate a confirming seed-1 was not run — the place-nothing-new plateau is a
*deterministic economic attractor* and the control reached 0.333 on **both** seeds, so a seed flip is
implausible.) A representation change **cannot move a reward-economics argmax**, so this **deconfounds
spatial-blindness from the notch wall**: the **marginal-commitment economics** diagnosis stands (the cost
of a 2nd/3rd commitment, not the policy's spatial vision, is the bottleneck). The 1 m-tap (48×24, double
the default 24×12 raster) escalation pre-registered in #810 was **not** tried — pointless against an
economic fixed point. `--spatial-tokens` remains opt-in, default-neutral infrastructure; **do not re-run
it on the notch.** The next lever is **per-commitment economics** (the marginal cost/credit of adding the
2nd/3rd object). This A/B refutes the *spatial-blindness* sub-hypothesis specifically — the #736 fork's
other representation knobs (aux-heads / critic-pretrain) target representation *variance* and were not
tested here.

### Concurrent sweep runner (#749 — run the two/three-seed gate in one launch)

The gate recipes above are **per-seed** (`--seed 0`, then `--seed 1`), run serially today — one
launch per seed, babysat by hand. `python -m ml.sweep` is a **torch-free orchestrator** that
spawns the K `python -m ml.train` cells **concurrently** (one per seed, each with a distinct
`--seed` + per-cell `--metrics-out`/`--checkpoint-out`/`--save` path) and aggregates their exit
codes into a single pass/fail verdict — **any failed *or crashed* child → runner exit non-zero**
(a silently-swallowed crash would corrupt a 2-seed verdict). The per-cell `ml.train` args go
**after a `--` separator**; everything before it is the sweep's own options:

```bash
# Two-seed trio-box gate, both cells concurrent (cap 2). The args after `--` are the
# trio-box recipe above, MINUS --seed/--metrics-out/--checkpoint-out/--save (the sweep
# strips and injects a distinct one per cell, in both the `--flag value` and `--flag=value`
# spellings); --load is shared (each seed resumes the same pair-box checkpoint).
python -u -m ml.sweep --seeds 0,1 --out-dir sweep-trio --tag trio --max-concurrency 2 -- \
  --schedule curriculum --device cuda --n-envs 16 --rollout-len 512 \
  --max-iters-per-stage 300 --promotion-metric valid_placed --promotion-threshold 0.9 \
  --r-valid-park 30.0 --r-unplaced-penalty 25.0 --dense-slot-potential \
  --w-col 20.0 --valid-park-grade-scale 4.0 --r-first-valid 15.0 \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns --validity-conditional-terminal --solo-box-rung \
  --seed-anchor --mixed-anchor --stop-after-rung trio-box --load ck-pairbox.pt

# Then roll up each cell's metrics with the same torch-free gate (exit 0=mastered, 1=not, 2=no-data):
python -m ml.gate sweep-trio/metrics-trio-seed0.jsonl --rung trio-box
python -m ml.gate sweep-trio/metrics-trio-seed1.jsonl --rung trio-box
```

By default each cell also gets a distinct per-seed `--checkpoint-out` (a crash-survivable resume
checkpoint); pass **`--no-checkpoint-out`** to skip it, or **`--save`** to additionally hand each
cell a distinct per-seed `--save` state_dict path.

**Determinism:** byte-identical (no flag) — each child is bit-identical to running it alone, so
co-locating cells on one GPU adds nothing beyond `--device cuda`.

**Expect ~2× sweep wall-clock, NOT Kx** — 5.5 cores is the time-averaged busy fraction of one
bursty run, so K aligned rollout bursts oversubscribe. `--max-concurrency` (default 2) is
**RAM-bound, not core-bound**: ~10 GB/run → **K=3 risks OOM on a 31 GB box**. Disjoint core
blocks + per-child thread caps (`OMP_NUM_THREADS`, `taskset`, `sched_setaffinity`) are an operator
concern set in the launching env; the orchestrator inherits the env into each child unchanged
(pairs with #747's per-worker BLAS/OMP thread cap so aligned rollout bursts don't oversubscribe
cores).

## Design
See `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`
and ADR-0027 (learned-path determinism scope).
