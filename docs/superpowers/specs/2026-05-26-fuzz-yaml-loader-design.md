# Design — Polyglot fuzzing for the YAML loader (#143)

**Date:** 2026-05-26
**Issue:** #143 (to be rewritten in place — see "Issue & governance")
**Status:** Approved design, pending implementation plan

---

## 1. Problem & the corrected premise

Issue #143 set out to move the OpenSSF **Scorecard "Fuzzing"** check from 0 → 10
by adding a [Hypothesis](https://hypothesis.readthedocs.io/) property-test
harness for the YAML loader. During design we verified the actual Scorecard
detection logic and found the issue's plan rests on a **false premise**.

Scorecard's Python fuzzing probe (`checks/raw/fuzzing.go`) is a single grep:

```go
clients.Python: { filePatterns: []string{"*.py"}, funcPattern: `import atheris` }
```

It recognises **only** the literal regex `import atheris` in any `*.py` file.
It has property-based-testing probes for other languages (QuickCheck, fast-check,
PropCheck, FsCheck…) but **none for Python**. Hypothesis is invisible to it.

**Consequence:** a pure-Hypothesis harness leaves Fuzzing at **0**. The issue's
claim "Scorecard recognises the `from hypothesis` import" is wrong.

The escape hatch is the documented **Hypothesis ↔ Atheris bridge**: a
`@given`-decorated test exposes `.hypothesis.fuzz_one_input`, which
[Atheris](https://github.com/google/atheris) drives via
`atheris.Setup(sys.argv, test.hypothesis.fuzz_one_input)`. One set of property
tests can therefore serve **both** normal pytest CI **and** a coverage-guided
Atheris run — and the `import atheris` line is what flips the badge. Atheris
ships `cp312` wheels with a bundled libFuzzer, so it installs on this project's
single supported Python (3.12) without clang.

## 2. Goal

Deliver **both** real defensive value **and** the badge (the "polyglot" pattern):

- **Value:** Hypothesis property tests that catch loader crashes on malformed /
  near-valid YAML — the loader is the named in-scope attack surface in
  `SECURITY.md`.
- **Badge:** a thin Atheris harness containing `import atheris` so the next
  weekly Scorecard run flips Fuzzing 0 → 10.

Non-goals (YAGNI): fuzzing solver/geometry/visualize; OSS-Fuzz enrolment
(already rejected in #143); a native `FuzzedDataProvider` harness (would
duplicate strategy logic — the `fuzz_one_input` bridge is DRY).

## 3. Architecture

One set of Hypothesis property tests is the engine, consumed two ways:

```
                         tests/fuzz/test_loader_fuzz.py
                         (@given property functions)
                          /                        \
            pytest (@given generates)        atheris (fuzz_one_input
            — every PR, ci profile,           replays libFuzzer bytes
              ~50 examples, fast)              through the SAME strategies)
                                                — nightly, time-boxed
                                                — `import atheris` flips badge
```

No duplicated input-construction logic: the Atheris harness is a thin `__main__`
wrapper that imports the property functions and hands their `fuzz_one_input`
attribute to `atheris.Setup`.

## 4. The property invariant (the heart)

For every generated input, each loader entry point must do **exactly one of**:

1. Return the correct model type
   (`dict[str, Aircraft]` / `Hangar` / `Layout` / `Scenario`), **or**
2. Raise `LoaderError`.

**Anything else fails the test** — a bare `KeyError` / `AttributeError` /
`IndexError` / `TypeError` / `RecursionError`, **or even a raw `ValueError`**.

This is deliberately **strict** and is not a tautology. The loader's contract is
"I wrap model `ValueError`s into `LoaderError` at every boundary"
(`except ValueError as e: raise LoaderError` in `load_hangar` / `load_layout` /
`load_scenario`; `except (ValueError, KeyError, TypeError, LoaderError)` in
`_build_aircraft`). If a fuzzed input ever lets a raw `ValueError` (or any other
exception type) escape, that is a **real missing-wrap bug** — exactly what the
suite exists to catch. We therefore do **not** accept bare `ValueError` as
"ok"; if the suite flushes one out, the fix is to add the missing wrap, not to
loosen the test.

Implementation: each property catches `LoaderError` and `pass`es; lets every
other exception propagate so Hypothesis shrinks it to a minimal repro.

## 5. Input strategies — structured + tiny raw guard

Loaders take a *path*, so each property builds a Python object →
`yaml.safe_dump` to a `tmp_path` file → calls the loader.

**Primary — structured near-valid documents** (one strategy per entry point):

- `load_fleet`: `{aircraft: [ {id, name, wing_position, gear, movement_mode,
  parts: [...], struts?: {...}, turn_radius_m?, measured?, notes?}, ... ]}`.
- `load_hangar`: `{length_m, width_m, door: {center_x_m, width_m},
  maintenance_bay: {center_x_m, width_m, depth_m}, clearance_m?,
  wing_layer_clearance_m?}`.
- `load_layout`: `{placements: [{plane, x_m, y_m, heading_deg, on_carts?}],
  maintenance?: {plane}}`, called with valid in-memory `fleet=`/`hangar=`
  overrides so the fuzzer concentrates on placement/maintenance logic rather
  than re-fuzzing path resolution.
- `load_scenario`: `{fleet_in: [...], maintenance?: {plane}, constraints:
  {pid: {pin?: {...}, force_on_carts?}}}`, likewise with valid overrides — its
  pin/constraint logic is the richest target.

Adversarial field values that matter for *this* loader (drawn via shared
helper strategies): floats including `NaN`/`inf`/huge/negative/zero; empty &
unicode strings; wrong types (int where str expected); missing required keys;
extra keys; empty lists; duplicate aircraft ids; near-miss plane ids (exercise
the `difflib` "did you mean" path); strut specs that violate the
`wing.z_bottom_m > fuselage_attach_z_m` and positive-span guards in
`_expand_struts`.

**Secondary — raw parse-layer guard** (one small property): feed
`st.text()` / `st.binary()` straight to a loader to exercise `_read_yaml` and
the top-level-shape guards. Cheap backstop; most inputs die at the PyYAML parse
stage (correctly surfaced as `LoaderError` from the `yaml.YAMLError` handler).

**Coverage:** all four entry points — `load_fleet`, `load_hangar`,
`load_layout`, `load_scenario`. (The issue named only the first three;
`load_scenario` gets the same treatment.)

## 6. Hypothesis settings profiles

In `tests/fuzz/conftest.py`, register and select profiles via the
`HYPOTHESIS_PROFILE` env var (default `ci`):

| Profile   | `max_examples` | `deadline` | Used by |
|-----------|----------------|------------|---------|
| `ci`      | 50             | `None`     | every PR (default) |
| `nightly` | 2000           | `None`     | nightly deep run |
| `dev`     | 100            | `None`     | local opt-in |

`deadline=None` avoids the classic flaky per-example-deadline failures on CI
cold starts (this project has fixed a Hypothesis-unrelated flake before and is
deadline-sensitive).

## 7. File layout

```
tests/fuzz/__init__.py
tests/fuzz/conftest.py                 # profiles + shared strategies
tests/fuzz/test_loader_fuzz.py         # @given property tests — pytest-collected, runs on PRs
tests/fuzz/atheris_loader_harness.py   # `import atheris` + Setup/Fuzz — NOT pytest-collected
```

- The harness is named `atheris_*` (not `test_*`) so pytest never collects it
  and **PR CI never needs atheris installed**.
- `mypy` checks only `src/hangarfit`, so the harness needs no atheris type stub.
- `ruff` does not resolve imports, so `import atheris` lints clean even
  uninstalled; `atheris` is genuinely *used* (`Setup`/`Fuzz`), so no unused-import
  warning.

## 8. Dependencies (respecting the lockfile discipline)

This repo already maintains three hash-pinned `.in` → `.txt` lockfiles
(`requirements-dev`, `requirements-build`, `requirements-pip-tools`), two of
them with drift-guard CI jobs. The fuzzing work adds:

- **hypothesis** → added to `[project.optional-dependencies] dev` in
  `pyproject.toml` → `requirements-dev.txt` regenerated → `hypothesis` added to
  the declared-dep allow-list loop in the `lockfile-drift` job in `ci.yml`.
  Stays inside the existing hash-pinned, drift-guarded flow; installed on every
  PR (pure-Python, fast).
- **atheris** → a new standalone hash-pinned `requirements-fuzz.txt` generated
  from a new `requirements-fuzz.in`. Installed **only** in the nightly job.
  Kept out of `pyproject.toml` so contributors never install a native libFuzzer
  wheel for routine work.
- **Drift guard for `requirements-fuzz.txt`** → a new `fuzz-lockfile-drift` CI
  job mirroring `build-lockfile-drift` (regenerate against committed pins,
  compare `package==version` sets, assert `atheris` survived). Keeps full
  consistency with the repo's lockfile discipline.

*Implementation checkpoint:* confirm a `cp312` manylinux atheris wheel exists so
the hash-pinned install needs no clang on `ubuntu-latest`.

## 9. Nightly workflow

New `.github/workflows/fuzz.yml`:

- `on: schedule` (nightly cron) `+ workflow_dispatch`.
- Steps: checkout → setup Python 3.12 → install hash-pinned dev deps +
  `requirements-fuzz.txt` + project (mirroring `ci.yml`'s install dance) → then
  1. `HYPOTHESIS_PROFILE=nightly pytest tests/fuzz/` (deep Hypothesis run, the
     primary bug-finding engine).
  2. Run the Atheris harness time-boxed (libFuzzer `-max_total_time=<N>`) so CI
     minutes stay bounded.
- Job failure simply goes red (no auto-issue-filing in v1).

**The badge does not depend on this workflow ever running** — Scorecard's probe
only greps source for `import atheris`. Nightly flakiness can therefore never
break the Fuzzing score; the job exists purely for our own bug-finding and to
keep the harness from rotting.

**Honest caveat:** the Hypothesis → Atheris `fuzz_one_input` bridge has
imperfect coverage-guidance (google/atheris#20) — libFuzzer's byte mutations
don't map cleanly onto Hypothesis's example space. So the *real* bug-finding
engine is the nightly **Hypothesis** deep run (2000 examples); Atheris is there
for the badge plus opportunistic coverage, not as the primary fuzzer.

## 10. Issue & governance

- **Rewrite #143 in place** (do not split): retitle and rewrite the body to this
  corrected polyglot plan; remove the `later` label; deliver in a **single
  feature PR** off `feature/143-fuzz-loader`. The Atheris harness reuses the
  same `@given` functions, so the work is tightly coupled — splitting would add
  GitFlow overhead disproportionate to a ~half-day task.
- **New follow-up milestone** (e.g. `v0.7.1 — Security follow-ups`), since
  v0.7.0 is already closed-out and #143 was deliberately parked out of it.
- Per the repo workflow: PR body `Closes #143`; assignee `DocGerd`; labels
  `security`, `scorecard`; milestone set via the issues REST API (number);
  `/pr-review` pass; convert findings to diff threads; resolve all threads;
  hand back clean for the user to merge.

## 11. Acceptance criteria

- [ ] `tests/fuzz/` with structured Hypothesis strategies for all four loader
      entry points + the raw parse-layer guard, enforcing the strict
      "valid model XOR `LoaderError`" invariant.
- [ ] Property tests run green on every PR under the `ci` profile (default
      `pytest`, not `slow`-marked).
- [ ] `tests/fuzz/atheris_loader_harness.py` contains `import atheris` and runs
      under the nightly job.
- [ ] `hypothesis` in `requirements-dev.txt` (+ drift-guard allow-list);
      `atheris` in hash-pinned `requirements-fuzz.txt` (+ new drift-guard job).
- [ ] Nightly `fuzz.yml` workflow passes (deep Hypothesis run + time-boxed
      Atheris run).
- [ ] CLAUDE.md "Useful commands" documents the `requirements-fuzz.txt` regen
      command, mirroring the other lockfiles.
- [ ] After the next weekly Scorecard run, **Fuzzing moves 0 → 10**.

## 12. Decisions log (this brainstorm)

| Decision | Choice |
|---|---|
| Objective | Both value + badge (polyglot) |
| CI model | PR-fast (Hypothesis ci profile) + nightly-deep (Hypothesis nightly + Atheris) |
| Issue structure | Rewrite #143 in place, one PR |
| Milestone | New follow-up milestone (post-v0.7.0) |
| Input strategy | Structured near-valid + tiny raw parse-layer guard |
| Fuzz lockfile | Hash-pinned `requirements-fuzz.txt` **with** a drift-guard CI job |
| Property strictness | Strict — `LoaderError` only; raw `ValueError` escaping = bug to fix |
| Atheris harness style | `fuzz_one_input` bridge (DRY), not native `FuzzedDataProvider` |
