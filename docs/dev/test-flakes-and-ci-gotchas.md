# Test flakes & CI gotchas

Operational reference for the wall-clock determinism flakes and the CI
coverage/two-pass quirks. Extracted from `CLAUDE.md` (#567) so the operating
manual stays scannable; the facts are unchanged.

---

## 1. The `serial` wall-clock determinism canaries

The wall-clock determinism canaries — the `serial`-marked double-solve tests in
`tests/test_solver_canaries.py`, `tests/test_solver_search.py`, and
`tests/test_solver_towplanner.py` — use a wall-clock `budget_s` (not
`max_restarts`) and run `solve()` twice in-process. Under heavy concurrent CPU
load the two solves can complete different restart counts within the same budget
and the result can diverge.

Since #492 they carry the `serial` marker and CI runs them in a dedicated serial
pass **outside** the `pytest -n auto` xdist pool. The marker only protects the CI
invocation: a local bare `pytest -n auto` still drops them into the parallel pool
and can re-expose the flake. To mirror CI locally:

```bash
pytest -n auto -m "not slow and not serial"
pytest -m "serial and not slow"
```

or just re-run a flagged canary in isolation before treating a failure as a
regression. The `max_restarts`-scoped companion
(`test_solve_deterministic_best_partial_under_max_restarts`) is the
load-independent determinism check.

## 2. The same wall-clock fragility bites non-serial tests too

It worsens as the model gains parts. A `solve`/`view` smoke test that runs a
**hard** scenario (6-/9-plane fill) under a wall-clock `--budget` can exhaust the
budget under `pytest -n auto` CI load → `rc=1` → flake (the #519/#520 empennage's
extra parts tipped four CLI solve tests over — fixed in #522).

For output-format / flag-acceptance smokes, use an **easy** fixture
(`scenario_minimal` / `solve_fresh_alternatives_three`): `solve` sets `rc=0` the
instant the first valid basin is found (sub-second), so the verdict no longer
races the clock.

The bench `--gate` **speed** ceilings (`bench/profile_pipeline.py`) are wall-clock
too and fail on slower CI runners as the model gets heavier — **or** when a
deliberate determinism re-base changes *which* layout a regime selects (a
different valid layout can tow-route slower). Re-baseline them (empennage→apron
ceiling #524; #544's per-restart-index reseed→spread-off ceiling 20→45 s) rather
than chase a phantom regression; the bench's validity/path/determinism verdicts
bind on `max_restarts` and stay reproducible.

## 3. Two-pass coverage

CI runs the suite in two passes (#492) —

```bash
pytest -n auto -m "not slow and not serial"
pytest -m "serial and not slow" --cov-append
```

— and derives coverage from the **combined** run, so marking a test `@slow` drops
it from coverage too. If a `@slow` test is the only one covering a new code path,
the `codecov/patch` PR check fails — keep **≥ 1 non-slow test per new path**.

## 4. ProcessPool/spawn workers are a coverage blind spot

`coverage.py` does **not** measure ProcessPool/spawn **worker** subprocesses, so
code that only runs inside a worker (e.g. `solver._run_restart_worker`, #544)
reads as uncovered even when tests *do* exercise it via the pool → `codecov/patch`
flags it (non-blocking). #561 closed this for the #544 worker by calling the
worker fn **in-process** in a unit test (the worker is usually a thin wrapper; or
wire coverage `concurrency=multiprocessing` + `COVERAGE_PROCESS_START`).
