# Spike: speeding up the always-run CI test job (#476)

**Status:** concluded (2026-06-07) · **Issue:** [#476](https://github.com/DocGerd/hangarfit/issues/476) · **Milestone:** Spikes & exploration (#15)

/ **Deliverable:** measured before/after per lever + a go/no-go decision. This is a
spike — **no production change ships from it**; the one GO lever is filed as a
separate implementation issue.

---

## TL;DR

The `test (Python 3.12)` job is the dominant per-PR wall-clock (~569 s median,
493–634 s). **`pytest` itself is essentially the entire job** — in the slowest
(634 s) run it was 601 s = 94.8 %, with install + lint + mypy + upload only
~20–30 s. So only a lever that attacks the test run matters.

| Lever | Verdict | Why |
|---|---|---|
| **1. `pytest-xdist`** | **GO** | Measured **3.0×** (`352 s → 117 s`) at 4 workers; the suite parallelises near-linearly at CI's core count. One hard constraint (the wall-clock determinism canaries) has a clean, robust mitigation. Filed as a follow-up impl issue. |
| **2. install speed / `uv`** | **NO-GO** | Install is ~12 s = **2 %** of the job; `actions/setup-python` pip-cache is already on. `uv` would shave a few seconds at the cost of the `--require-hashes` / `--no-build-isolation` integrity story. Not worth it. |
| **3. coverage cost** | **NO-GO (keep `--cov`)** | Coverage adds only ~21 s and removing it forfeits the `codecov/patch` signal. Under xdist the whole run is ~117 s; coverage is a minor slice. |

**Projected impact of the GO lever:** the `pytest` step drops `~601 s → ~200 s`
(3.0× on 4 vCPU, projected from the `taskset -c 0-3` 118 s proxy — not yet a
measured CI run), taking the **whole job from ~569 s median (~634 s worst) to
~235 s — roughly 2.4–2.7× (~330–400 s saved) on every PR**.

---

## Baseline (measured, not estimated)

### Real CI (5 recent `develop` runs, `gh api …/actions/runs/<id>/jobs`)

| | seconds |
|---|---|
| `test (Python 3.12)` job wall-clock | min 493 · **median 569** · max 634 |
| — `pytest --cov …` step (slowest, 634 s run) | **601 (94.8 %)** |
| — install dev+build deps (hash-pinned) | ~12 |
| — mypy | ~6 · ruff ~0 · cov upload ~3 |

GitHub-hosted `ubuntu-latest` = **4 vCPU**. CI currently runs `pytest` **serially**
(no `-n`), so the 601 s is single-core-on-a-4-vCPU-runner.

### Local timing matrix

Machine: 32 cores (so a naive local `-n auto` spins 32 workers — **not**
CI-representative). To mirror CI's 4 vCPU honestly, the binding row pins 4 cores
with `taskset -c 0-3 -n 4`. develop @ `48def1c`, 1277 tests (8 `@slow` deselected).

| config | wall | vs serial | note |
|---|---|---|---|
| `pytest --cov` (serial — CI's exact command) | **352 s** | 1.0× | local baseline |
| `pytest --cov -n 4` | **117 s** | **3.0×** | 32-core box, 4 workers |
| `pytest -n 4` (no `--cov`) | 96 s | 3.7× | coverage = **+21 s** (~18 % of the parallel wall) |
| `taskset -c 0-3 pytest --cov -n 4` | **118 s** | 3.0× | **CI-representative** (4 cores saturated) — equals the un-pinned `-n 4`, confirming it was already worker-bound |
| `pytest -n auto` (32 workers, no cov) | 54 s | ~6.5×† | parallel **ceiling**; 4→32 workers buys only 1.8× (`96→54`, both no-cov) ⇒ an Amdahl floor set by a handful of heavy `budget_s` solver tests |

† vs serial **+cov** (`352/54`); a cov-vs-no-cov ratio, hence approximate. The
clean apples-to-apples scaling figure is the no-cov `96 s → 54 s` (1.8×) in the
same row's note.

The key read: at **CI's 4 cores we are well above the ~54 s floor** (96–118 s), so
4-way parallelism gets the near-full 3× — the floor only bites far past 4 workers.

---

## Lever 1 — `pytest-xdist`: **GO**

**Speedup is real and large:** `352 s → 117/118 s` ≈ **3.0×** at 4 workers, byte-for-byte
the suite (`1277 passed`, coverage 98 % preserved via pytest-cov's per-worker
combine).

### The hard constraint — the wall-clock determinism canaries

`tests/test_solver_canaries.py::test_solve_deterministic_given_seed` (3 params)
runs `solve()` **twice in-process** under a **wall-clock `budget_s = 5.0`** and
asserts bit-identical output. Under CPU starvation the two solves can complete
**different restart counts**, diverge, and **fail** — a false determinism
violation. `conftest.py` and `CLAUDE.md` both document this; it is the whole reason
the spike is a spike rather than a one-line `-n auto`.

**Empirical flake probe.** Ran this canary **12×** while a full `-n 4` suite
saturated the same 4 pinned cores (the documented trigger): **12/12 PASS** — I
could *not* reproduce the flake. Conclusion: it is **rare, not impossible**. For a
*determinism canary* specifically, a rare-but-nonzero false failure is
unacceptable (it cries wolf on the exact contract it guards), and CI's shared
runners add noisy-neighbour jitter beyond our control. So the canaries must not run
in the parallel pool — independent of the (un-pin-downable) flake probability.

### Mitigation — and a pitfall

- **❌ `@pytest.mark.xdist_group` + `--dist loadgroup` is INSUFFICIENT.** It pins
  the grouped canaries to *one* worker (so they don't run parallel to *each
  other*), but the **other** workers still hammer the cores while a canary's two
  in-process solves race their 5 s budgets. The contention is from *sibling
  workers*, which grouping does nothing about.
- **✅ Run the wall-clock canaries SERIALLY, outside the xdist pool.** Mark them
  (e.g. `@pytest.mark.serial`) and split the CI step in two:

  ```bash
  pytest -n auto -m "not serial" --cov=hangarfit --cov-report=xml
  pytest        -m "serial"      --cov=hangarfit --cov-append --cov-report=xml --cov-report=term-missing
  ```

  The serial step runs only the ~3 canaries (~10 s); coverage is stitched with
  `--cov-append`, and **both** passes must emit `--cov-report=xml` so the
  `coverage.xml` Codecov consumes includes the serial canaries (the second pass's
  `xml` overwrites the first with the appended total). This makes xdist
  correctness **independent** of the timing race
  and preserves *all* current coverage. (An alternative — converting the canary
  from `budget_s` to a `max_restarts` bound — would make it load-independent but
  duplicate the existing `test_solve_deterministic_best_partial_under_max_restarts`
  and drop the wall-clock-budget determinism coverage; the serial-split keeps both.)

### Determinism (ADR-0003) is untouched

xdist parallelises **test execution across processes**; it does **not** touch
`solver.py` / `towplanner.py` or any solver internal, so the byte-identical
`SolveResult` / `MovesPlan` contract is unaffected — this is a **CI-config change,
not a solver change**, so `determinism-guard` is not in scope. The only
determinism-adjacent risk is the *test-harness* wall-clock race above, removed by
keeping those canaries serial. The `bench-gates` workflow's determinism assertion
(which *does* exercise solver byte-identity) is a separate job and unaffected.

### Projected CI impact

`pytest` step `~601 s → ~200 s` (3.0× projected from the `taskset` proxy, to be
confirmed on a real CI run) + a ~10 s serial-canary step ⇒ job **~569 s median
(~634 s worst) → ~235 s (~2.4–2.7×)**, on **every** PR. Implementation cost: add `pytest-xdist`
to the dev extra (+ regenerate `requirements-dev.txt`), mark the canaries `serial`,
split the CI step. Filed as **[#492](https://github.com/DocGerd/hangarfit/issues/492)**.

---

## Lever 2 — install speed / `uv`: **NO-GO**

Install is **~12 s = 2 %** of the 569 s job. `actions/setup-python` already caches
pip keyed on the three lockfiles, so unchanged-dep PRs hit the cache. `uv pip`
could cut a cold install to ~5 s, but: (a) it saves seconds on a 2 % slice while
xdist saves ~400 s; (b) it would complicate the deliberate
`--require-hashes` + `--no-build-isolation` + hash-pinned-host-setuptools integrity
chain (#162/#224) for negligible gain. Revisit only if install ever grows (e.g. a
heavy new dep) or the project adopts `uv` project-wide for other reasons.

## Lever 3 — coverage cost: **NO-GO (keep `--cov`)**

Coverage instrumentation adds **~21 s** (`-n 4`: 96 s → 117 s; ~18 % of the
parallel wall, ~6 % of serial). Dropping it would save that ~21 s but forfeit the
`codecov/patch` PR signal (non-required, but the project's only patch-coverage
visibility). Under xdist the run is already ~117 s; coverage is a minor, worthwhile
slice. `--cov-report=term-missing` is the cheap part (formatting), not worth
special-casing.

---

## Recommendation

Implement **Lever 1 only**: `pytest-xdist -n auto` with the **serial-canary split**.
Levers 2 and 3 are NO-GO. Expected: the always-run job drops from ~634 s to ~235 s
on every PR with **zero** change to what is tested, coverage preserved, and the
determinism canaries kept serial (so no new flake surface).

**Follow-up implementation issue:** [#492](https://github.com/DocGerd/hangarfit/issues/492).
Acceptance there: add `pytest-xdist` to the dev extra + regenerate the
hash-pinned lockfile; mark the wall-clock canaries `@pytest.mark.serial`; split the
CI `Run tests` step into a parallel `-m "not serial"` pass and a serial `-m serial`
pass with `--cov-append`; confirm on a real CI run that the job time drops and the
canaries pass; document the `xdist_group`-is-insufficient pitfall in the test code.
