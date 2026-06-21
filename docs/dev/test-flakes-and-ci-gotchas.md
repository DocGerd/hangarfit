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

`make test` runs exactly this two-pass split for you (the root `Makefile`, #624),
so the recipe and the dev shortcut stay in sync; `make test-fast` runs only the
parallel bulk pass. Or just re-run a flagged canary in isolation before treating
a failure as a regression. The `max_restarts`-scoped companion
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
the `codecov/patch` number dips for that path (the check is *informational* since
#589 — it reports, never fails; see §5) — still keep **≥ 1 non-slow test per new
path** so the signal stays honest.

## 4. ProcessPool/spawn workers are a coverage blind spot

`coverage.py` does **not** measure ProcessPool/spawn **worker** subprocesses, so
code that only runs inside a worker (e.g. `solver._run_restart_worker`, #544)
reads as uncovered even when tests *do* exercise it via the pool → it drags the
`codecov/patch` number down (informational, see §5). #561 closed this for the #544
worker by calling the worker fn **in-process** in a unit test (the worker is
usually a thin wrapper; or wire coverage `concurrency=multiprocessing` +
`COVERAGE_PROCESS_START`).

## 5. Codecov statuses are informational, not gates

There is a committed [`codecov.yml`](../../codecov.yml) that sets both
`coverage.status.patch` and `coverage.status.project` to `informational: true`,
so **the `codecov/patch` and `codecov/project` checks always pass** while still
posting the coverage numbers on every PR. Two reasons (#589):

1. Neither status was ever a *required* check on `develop`/`main` (required on
   `develop` = `test (3.12)`, the three lockfile-drift jobs, `Analyze (Python)`
   CodeQL, `bench correctness`; `main` is the same minus `Analyze`). They are a
   signal, not a merge gate — `informational: true` just stops them rendering a
   misleading red **X** on a check that never blocked.
2. Under the default auto-target, every **release PR** went red: its cumulative
   `vX.Y.Z..develop` diff vs `main` lands a couple of points under the project
   target (e.g. PR #587: 95.09 % patch vs 97.17 %). Structural noise, not a
   regression — and it merged anyway.

So: **read the patch number** (it still shows new-code coverage), but a green/grey
codecov status is not a quality claim and a low number does not block. The §3/§4
guidance above keeps that number honest.

## 6. The `from module import name` monkeypatch rebinding trap

A test that patches a *source* module's attribute cannot intercept a consumer that
imported the name **by value** (`from geometry import aircraft_parts_world`) — the
consumer holds its own binding. A byte-identity / routing test that monkeypatches
`geometry.aircraft_parts_world` to count builds then sees ZERO calls from such a
consumer and passes *vacuously* (`0 == 0`).

This bites this repo because byte-identity / routing tests are a recurring pattern
(ADR-0003, `ml-rl-guard`). Fix: patch the deepest shared function the consumer
actually reaches at call time (e.g. the one `cached_parts_world` delegates to via a
module-global lookup), AND assert the warm-up did real work (`assert builds > 0`) so a
vacuous pass fails loud. (#733: the `ml/` pose-cache routing tests in
`tests/ml/test_pose_cache.py`.)
