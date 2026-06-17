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

## Design
See `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`
and ADR-0027 (learned-path determinism scope).
