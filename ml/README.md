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
oracle — the oracle's over-strict treatment of the inert placeholder maintenance
bay is tracked as #694. The Herrenteich anchors' policy column (env
fixed-obstacle pre-placement) is deferred to 4c-ii (#693).

## Design
See `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`
and ADR-0027 (learned-path determinism scope).
