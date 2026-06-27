# Spike — fk9_mkii ↔ cessna_140 trajopt determinism pre-check (Gate 0)

**Issue:** #844 follow-up (b) — the parked **continuous-trajectory-optimization** spike,
the only method class that survived the #840 NO-GO refutation (every heuristic-class
method — deterministic field *and* learned guidance — is measured dead against the
intrinsic near-C\* A\* plateau; see
[`herrenteich-fk9-cessna-lateral-shuffle.md`](herrenteich-fk9-cessna-lateral-shuffle.md)
and the #840 spike section).

**Context.** Before anyone builds a continuous trajectory optimizer for the
fk9_mkii↔cessna_140 front-door tow nook, this spike runs a determinism-first
**"Gate 0"** pre-check: *could* an iterative continuous optimizer's output **ever**
ship under the project's ADR-0003 byte-identity determinism contract? The artifact
that must come out byte-identical is the **`MovesPlan`** (the tow-motion plan
`towplanner.py` emits; the existing planner is RNG-free and bit-deterministic by
construction). The full pre-check design — the gate's steps, the GO/NO-GO criteria,
and how Task 4 turns this section into a severity verdict — lives in the spec
[`docs/superpowers/specs/2026-06-27-fk9-cessna-trajopt-precheck-design.md`](../superpowers/specs/2026-06-27-fk9-cessna-trajopt-precheck-design.md).
This document is the spike's running record; the section below is **Task 1 (Step 0.1)**:
pinning the *exact* determinism bar a trajopt optimizer would have to clear.

---

## Step 0.1 — determinism requirement (same- vs cross-machine)

### Verdict: **cross-machine** (the binding contract wording requires it for the deterministic, `max_restarts`-bounded path) — with a recorded test-coverage gap and an un-guarded transcendental hazard, both of which *raise* the gate's severity.

The contract **wording** is unambiguous: for a `max_restarts`-bounded (deterministic
restart count) solve, byte-identity is promised **across machines**, not merely
across re-runs on one machine. But the **canary test** that the ADR cites as the
contract's enforcement only *exercises* a same-process / same-machine re-run-and-diff.
That wording-vs-test gap, plus the contract's complete silence on
transcendental/`libm` cross-build variance, is exactly the material the gate's
severity turns on (per Task 1 brief Step 2). Details and citations follow.

### Evidence A — the contract *wording* explicitly claims cross-machine

The base contract states bit-identity under a seed, scoped to the deterministic
verifier/solver (`solver.py`, `towplanner.py`):

> "Reproducibility is a contract, not a nice-to-have. Same scenario + same seed →
> bit-identical `SolveResult`."
> — `docs/adr/0003-rr-mc-solver-algorithm.md:51-52`

> "The byte-identical contract below binds the **deterministic verifier/solver**
> (`solver.py`, `towplanner.py`)."
> — `docs/adr/0003-rr-mc-solver-algorithm.md:6-7`

The **2026-05-27 (#267) amendment** is where "machines" becomes explicit — and it
is explicitly scoped to the `max_restarts`-bounded path:

> "A run bounded by `max_restarts` (a deterministic restart count) is **fully
> reproducible across runs and machines** — same seed → same pool → same selected
> layout."
> — `docs/adr/0003-rr-mc-solver-algorithm.md:380-382`

> "**Guidance.** For guaranteed cross-machine reproducibility, bound the search by
> `max_restarts` rather than (or in addition to) wall-clock `budget_s`."
> — `docs/adr/0003-rr-mc-solver-algorithm.md:396-398`

The flip side is also written down — a **wall-clock `budget_s` + spread-ON** run is
*allowed* to differ across machines (variable restart count, near-tie maximin-gap
resolution), so cross-machine identity is a property of the **deterministic-count
mode**, not of the default interactive mode:

> "the selected layout can differ between machines (or the same machine under
> different load) — the pool size varies and a near-tie may resolve differently."
> — `docs/adr/0003-rr-mc-solver-algorithm.md:386-388`

The **2026-06-09 (#544) amendment** strengthens the deterministic-mode claim to
**cross-process** identity by construction (the seed becomes a pure function of the
restart index via a hash-stable string seed):

> "Each restart now seeds its own RNG from its index (`_restart_rng` keys on
> `(seed, restart_index)` via a SHA-512-stable string seed, so it is deterministic
> and identical *across processes* even under hash randomization). … the serial and
> parallel paths produce **byte-identical** output."
> — `docs/adr/0003-rr-mc-solver-algorithm.md:332-335`

The `determinism-guard` agent restates the same cross-machine bar as authoritative
and treats a `max_restarts`-bounded cross-machine divergence as a regression:

> "A run bounded by `max_restarts` … is **fully reproducible across runs and
> machines** — same seed → same pool → same selected layout."
> — `.claude/agents/determinism-guard.md:25`

> "A `max_restarts`-bounded run that the amendment promises is cross-machine
> reproducible can now select a different layout on a faster machine." (Example 2 =
> FAIL)
> — `.claude/agents/determinism-guard.md:118`

### Evidence B — the canary only *tests* same-machine (the gap)

What the ADR cites as the contract's enforcement (`docs/adr/0003-…:274-280`,
`230-231`) is `tests/test_solver_canaries.py`. Every assertion there is a
**two-solves-in-one-process, same-machine** re-run-and-diff — there is **no**
cross-machine golden, **no** pinned literal carried across hosts, and **no**
`PYTHONHASHSEED=random` subprocess:

- `test_solve_deterministic_given_seed` loads the fixture twice and solves twice
  **in the same interpreter**, then diffs `placements` — same process, same machine:
  - `tests/test_solver_canaries.py:82-103` (the two in-process `solve(...)` calls
    and the element-wise `la.placements == lb.placements` diff).
- `test_solve_deterministic_best_partial_under_max_restarts` — the *one*
  `max_restarts`-bounded canary, the mode the contract promises is cross-machine —
  is **also** two in-process solves on one machine:
  - `tests/test_solver_canaries.py:217-233` (both `solve(... max_restarts=3 ...)`
    calls), `:268-269` (`bpl1.placements == bpl2.placements`).
- `test_solve_deterministic_polygon_taper_fleet` — same two-in-process shape
  (`tests/test_solver_canaries.py:281-290`).

The strongest existing *multi-process* evidence is `tests/test_solver_parallel.py`
(byte-identity of `workers=4` vs `workers=1`, bound on `max_restarts`, eligible
regime — `tests/test_solver_parallel.py:64-117`). That genuinely crosses **process**
boundaries (spawned worker subprocesses, `:142-145`) and is the empirical backing
for the #544 "identical across processes" claim — but it is still **same machine**
(same `libm`, CPU, Python build). The `determinism-guard`'s own empirical step is
likewise a **single-machine** twice-and-diff, and the agent explicitly notes a
single process may *not* catch a set-ordering leak — it leans on
`PYTHONHASHSEED=random` / CI-machine variance and the canary suite to expose it:

> "step 4's twice-and-diff may or may not catch it in a single process (hash seed is
> fixed per process) but across CI machines / a re-run with `PYTHONHASHSEED=random`
> it diverges …"
> — `.claude/agents/determinism-guard.md:105`

**Gap, stated plainly:** the contract *requires* cross-machine byte-identity (for the
`max_restarts` mode) but the test net only *demonstrates* same-machine (and
cross-process same-machine) reproducibility. Nothing in the suite would catch a
cross-machine-only divergence — e.g. one rooted in floating-point results that
differ between platform math libraries.

### Evidence C — transcendental / `libm` variance is un-acknowledged and un-guarded

Neither ADR-0003 (body or any amendment) nor `determinism-guard.md` anywhere
acknowledges that `math.sin` / `math.cos` / `math.atan2` (and friends) may return
**different bits on different `libm` builds / platforms**. The determinism mechanisms
the guard enumerates are all about *ordering and RNG* (single seeded `rng`,
`sorted()`-before-`choice`, total-order selection sort, fixed word/primitive tuples,
monotonic heap counter — `.claude/agents/determinism-guard.md:35-50`); none address
bit-level float reproducibility of transcendental functions across toolchains.

This matters directly for **this gate** because the byte-identity artifact in scope is
the **`MovesPlan`**, produced by the trig-heavy `towplanner.py` (Reeds–Shepp / Dubins
closed-form word selection, kept-min with a strict `<` tie-break —
`.claude/agents/determinism-guard.md:46-49`). The existing tow planner's *cross-machine*
determinism therefore already rests on an **unstated assumption** that `libm`
transcendentals are bit-stable across hosts — an assumption the contract never makes
explicit and the test net never checks (the roundtrip/word tests are same-machine).

### What this implies for the gate (one sentence)

Because the binding contract requires **cross-machine** byte-identity for any
deterministic (`max_restarts`-bounded) mode a shipping feature would have to use, a
continuous trajectory optimizer's **raw iterative float output is almost certainly
un-shippable** (it would have to be bit-identical across `libm`/BLAS/CPU/platform
builds — which the contract never even guarantees for the *existing* trig-based
`MovesPlan`), so under this bar the **only** plausibly-survivable design is a
**discrete-word reduction** (quantise the optimized trajectory back onto a finite,
exactly-comparable vocabulary — the same shape as towplanner's existing Reeds–Shepp
word enumeration), and even that inherits the un-guarded `libm` hazard the gate must
flag.
