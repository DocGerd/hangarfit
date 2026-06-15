# ml/ — learned-backend RL workspace (#607)

Dev/CI-only, never shipped in the wheel. Sub-project #1: the cold-joint RL
environment + reward (`HangarFitEnv`), reusing `hangarfit`'s geometry oracle.

## Run the tests
    pytest tests/ml/

## Design
See `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`
and ADR-0027 (learned-path determinism scope).
