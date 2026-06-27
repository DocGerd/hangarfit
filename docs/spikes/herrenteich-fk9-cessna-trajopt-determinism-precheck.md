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

---

## Step 0.2 — the deterministic seam in today's pipeline

### Verdict: today's `MovesPlan` is **closed-form-only — no float in it comes from an iterative numerical method** (no Newton/fixed-point/gradient loop) — but it is **transcendental-heavy**, so its cross-machine byte-identity rests on the *exact same* un-guarded `libm` bit-stability assumption Step 0.1 flagged. The baseline is therefore **robustly same-machine and only *presumptively* cross-machine** (cross-machine identity is asserted by the contract but untested, and would break the instant a `libm`/toolchain returns a different ULP from `sin`/`cos`/`atan2`/`sqrt`/`acos`). The seam an optimizer's output would enter is the *segment-length list* of a `DubinsArc` consumed by `plan_fill`'s `Move(... path=arc)` assembly (`towplanner.py:1767`, `:1838`) and validated by `path_first_conflict` (`towplanner.py:1306`).

### Step 1 — the emit path (how arc parameters become a `MovesPlan`)

The byte-identity artifact is the `MovesPlan` (`towplanner.py:261-269`): `target_layout: Layout` plus an ordered `moves: tuple[Move, ...]`. Each `Move` (`:235-258`) is `(plane_id, target_slot: Pose, path: DubinsArc | None)`. **All the geometry floats live inside `path`** — a `DubinsArc` (`:112-133`) whose `segments: tuple[Segment, ...]` is the load-bearing payload; each `Segment` (`:86-109`) carries `(kind, length_m: float, gear)`. So the question "what floats does the `MovesPlan` carry?" reduces to "where do the `Segment.length_m` values and the `Pose` x/y/heading floats come from?"

The emit chain, end to end:

1. **`plan_fill` builds the plan** (`:1519`, assembly at `:1766-1841`). It loops the chosen `(slot, arc, …)` order and appends `Move(slot.plane_id, Pose.from_placement(slot), arc)` (`:1767`), then ground-object movers (`:1838`), and finally returns `MovesPlan(target_layout=target, moves=tuple(moves))` (`:1841`). `plan_fill` itself does no geometry — it concatenates `arc`s and `Pose`s.
2. **Each `arc` comes from `plan_path`** (`:2418`, called at `:1707`/`:1439` for aircraft, `:1813` for movers). `plan_path` is a Hybrid-A* search whose returned `DubinsArc` is `DubinsArc(arc_start, goal, r, segs)` where `segs = tuple(_reconstruct_segments(node)) + final_arc.segments` (`:2673`, `:2686`) — a **discrete concatenation** of (a) a *prefix* of fixed motion primitives chosen by the search and (b) a *suffix* analytic Reeds–Shepp shot.
3. **Prefix segment lengths are fixed constants.** `_primitives` (`:1920-1969`) emits `Segment`s whose `length_m` is `_GRID_XY_M = 0.5`, `math.radians(_GRID_DEG)` with `_GRID_DEG = 15.0`, or `max(_GRID_XY_M, turn_radius_m * math.radians(_GRID_DEG))` (`:1951-1968`). These are deterministic arithmetic on module constants and the per-mover `turn_radius_m` — **no transcendental enters the prefix *lengths*** (`math.radians` is exact-ish scaling by π/180, the only "transcendental" being the π constant, identical across builds). The search *selects which* primitives via A* (`best_g`, a `heapq` with a monotonic `counter` tie-break, `:2733-2743`) — an integer/discrete choice, RNG-free.
4. **Suffix segment lengths come from the closed-form Reeds–Shepp solver.** The analytic shot is `final_arc = plan_reeds_shepp(node.pose, goal, turn_radius_m=r, …)` (`:2668`). Inside `plan_reeds_shepp` (`:922`) the goal is expressed in the start frame using `math.cos/ math.sin` (`:957-960`), then `_rs_solve_normalised` (`:849`) enumerates the closed-form word family. The base solvers `_lsl/_rsr/_lsr/_rsl/_rlr/_lrl` (`:326-383`) compute leg lengths with `math.sin`, `math.cos`, `math.atan2`, `math.sqrt`, and `math.acos`; the result is scaled back to metres as `Segment(e.steering, e.t * r, gear=e.gear)` (`:966`). **These `Segment.length_m` values ARE transcendental-derived** — but each is a *single closed-form evaluation*, never the output of an iteration.
5. **The `Pose` floats** (`arc.start`, `arc.end`/`target_slot`) come from `Placement` data via `Pose.from_placement` (`:81-83`) and from `_root_pose`/`_step_pose` (`:2073`, `:1972`), which integrate via `DubinsArc.pose_at` (`:139-195`) — again `math.sin/cos` per leg, closed-form, no iteration.

**`path_first_conflict` validates, it does not compute the emitted floats** (`:1306-1383`). It samples the candidate arc (`arc.sample(...)`, `:1344`) and runs `collisions.check` at each pose; it returns a `Conflict | None` and **never mutates the arc**. So validation is downstream of emit and cannot introduce or launder a float into the plan. (In `plan_path` it is the safety-net gate at `:2692-2695` — the arc is *returned unchanged* iff it reports `None`.)

**Conclusion for Step 1:** no float in today's `MovesPlan` originates from an iterative computation. The A* loop *iterates*, but only to make the **discrete** choice of which fixed-length primitives to concatenate; every actual `length_m`/`Pose` float is a closed-form expression (fixed constant, or one-shot trig/sqrt/acos). This is exactly the "discrete-word reduction" shape Step 0.1 predicted would be the *only* plausibly-shippable design — today's planner already lives there.

### Step 2 — honest closed-form determinism assessment

Closed-form ≠ cross-machine-byte-stable. The emitted floats depend on `math.sin`, `math.cos`, `math.atan2`, `math.sqrt`, and `math.acos`:

- `plan_reeds_shepp` frame transform: `math.cos`, `math.sin` (`:957`).
- RS base solvers: `math.sin/cos` (`:327` etc.), `math.atan2` (`:331`, `:340`, `:350`, `:360`, `:370`, `:381`), `math.sqrt` (`:332`, `:341`, `:349`, `:359`), `math.acos` (`:369`, `:380`).
- `_dubins_shortest` / `plan_dubins`: `math.hypot`, `math.atan2` (`:412-414`).
- `DubinsArc.pose_at` integrator (feeds `Pose` floats): `math.cos`, `math.sin` (`:159-191`).

Python's `math.*` are thin wrappers over the platform C `libm`. IEEE-754 pins `+ - * / sqrt` to correctly-rounded results (so `math.sqrt`, plain arithmetic, and `math.hypot` *are* bit-reproducible across conforming builds), **but the standard does not require correctly-rounded `sin`/`cos`/`atan2`/`acos`** — different `libm` implementations (glibc vs musl vs macOS vs the Windows CRT, and even glibc versions) legally differ in the last ULP for these transcendentals. A one-ULP difference in any leg length or pose angle propagates verbatim into `Segment.length_m` / `Pose.heading_deg`, breaking *byte*-identity of the `MovesPlan` across machines.

Two mitigations in the code *reduce* but do **not** eliminate the hazard:

- The **word-selection tie-break** (`:386-390`, `:424`, `:888`) uses a strict `<`, so a *geometrically equal* word ties resolve to a deterministic winner *per machine* — but the comment at `:389-390` is explicit that a ULP cost difference "still resolve[s] deterministically — just to whichever rounded smaller, **not necessarily the earliest-listed**." Across two `libm` builds the *winning word itself* can therefore differ, not merely its float bits — a discrete divergence, not just a last-bit one.
- `_rs_word_reaches` (`:895-919`) and the `pose_at` endpoint re-integration gate words with a `1e-6` tolerance, catching *gross* sign errors — but `1e-6` is enormous next to a ULP, so it does nothing to enforce bit-identity.

**Therefore:** the baseline `MovesPlan` is **robustly reproducible same-machine / same-process / cross-process** (the existing canaries and `tests/test_solver_parallel.py` demonstrate this — Step 0.1 Evidence B), and the planner is RNG-free by construction (`plan_reeds_shepp` docstring `:931-932`), but its **cross-machine** byte-identity is an **untested presumption** resting on `libm` transcendental bit-stability that neither ADR-0003 nor `determinism-guard` acknowledges (Step 0.1 Evidence C). This is the precise sense in which the same-vs-cross distinction "bites even for today's baseline": *today's shipped Reeds–Shepp `MovesPlan` is not provably cross-machine byte-identical.* An iterative optimizer would not introduce a *new class* of hazard — it would *amplify* the existing one (more transcendental ops, plus genuinely iteration-dependent floats and BLAS/SIMD reduction-order variance), while losing the one property the current design keeps: a **finite, exactly-comparable discrete word vocabulary** as the thing that actually has to match.

### Step 3 — the exact seam (where an optimizer's output would enter)

An optimizer produces a continuous trajectory (a sequence of states / controls). To become a shippable `MovesPlan` under ADR-0003 it must be reduced to the **same artifact the current planner emits**: a `DubinsArc` whose `segments: tuple[Segment, ...]` is the payload, slotted into a `Move`. There are exactly two viable insertion points, both *upstream* of the unchanged assembly/validation:

- **Seam A (drop-in arc replacement) — `plan_path`'s return (`towplanner.py:2686`, returned at `:2705`).** Replace/augment the Hybrid-A* `DubinsArc(arc_start, goal, r, segs)` with an optimizer-derived `DubinsArc`. Everything downstream — `plan_fill`'s `Move(..., path=arc)` (`:1767`/`:1838`) and the `MovesPlan(...)` (`:1841`) — is untouched. The optimizer output **must already be a list of `Segment(kind, length_m, gear)`** at this point.
- **Seam B (post-hoc reduction) — between an optimizer and `Move` construction.** Run the optimizer outside `plan_path`, then quantise its trajectory onto the existing `Segment` vocabulary before building the `Move`. Functionally the same contract as Seam A.

**What must be deterministic at the seam** (for the `MovesPlan` to clear ADR-0003 cross-machine):

1. **The `Segment.length_m` floats must be bit-identical across machines** — which, per Step 2, means they cannot be raw iterative-optimizer outputs (those depend on convergence path, BLAS reduction order, SIMD/FMA contraction, and transcendental ULPs). They must be reduced to a **finite, exactly-comparable vocabulary** (quantised lengths/angles on a fixed grid, integer-counted), so the emitted plan is a *discrete word* whose equality is exact regardless of how the optimizer arrived at it. Even then, any `sin/cos/atan2` used in the reduction inherits the **un-guarded `libm` hazard** that already shadows the current Reeds–Shepp emit.
2. **The selection among candidate trajectories must be RNG-free with a total-order tie-break** — mirroring the existing fixed enumeration order + strict-`<` keep-min (`:386-390`, `:418-425`) and the `heapq` monotonic-`counter` tie-break (`:2735-2743`). A near-tie that resolves by float comparison reintroduces the discrete cross-machine divergence Step 2 describes.
3. **`path_first_conflict` (`:1306`) stays the validity oracle, unchanged** — it must accept the reduced arc exactly as it accepts a Reeds–Shepp arc. Because it only *reads* the arc (samples + `collisions.check`), it imposes no new determinism burden, but it does pin the seam: whatever the optimizer emits has to be a `DubinsArc` that `arc.sample()` (`DubinsArc.sample`, `:197`) can walk — i.e. expressible in the `Segment` `kind ∈ {L,S,R,T}` / `gear ∈ {±1}` / `length_m ≥ 0` vocabulary.

In one line: **the seam is the `Segment`-tuple of the `DubinsArc` returned by `plan_path` (`:2686`) and folded into `Move.path` by `plan_fill` (`:1767`); the optimizer must hand over a *discrete, quantised, exactly-comparable* word there, or its `MovesPlan` cannot be ADR-0003 cross-machine byte-identical.**

### Step 4 — API block (verbatim current signatures, for Task 3)

> Recorded from `src/hangarfit/towplanner.py` at the commit on `feature/844b-trajopt-determinism-precheck`. **Discrepancy with the brief's hints, recorded per instruction:** the brief lists "`MovesPlan.sample` (~:197)", but `MovesPlan` has **no** `sample` method — the sampler at `:197` belongs to **`DubinsArc`**. The byte-identity artifact `MovesPlan` is a plain frozen dataclass (no methods); arc sampling (which Task 3's validation walks) lives on `DubinsArc`, reached via `Move.path`. Both are recorded below so Task 3 calls the real API.

```python
# Pose — frozen dataclass (towplanner.py:70-83)
@dataclass(frozen=True, slots=True)
class Pose:
    x_m: float
    y_m: float
    heading_deg: float
    @classmethod
    def from_placement(cls, p: Placement) -> Pose: ...

# Segment — frozen dataclass (towplanner.py:86-109); the seam payload element
@dataclass(frozen=True, slots=True)
class Segment:
    kind: SegmentKind          # Literal["L", "S", "R", "T"]
    length_m: float            # always >= 0; gear applies the travel direction
    gear: Literal[1, -1] = 1   # +1 forward (default), -1 reverse

# DubinsArc — frozen dataclass holding the Segment tuple (towplanner.py:112-133)
@dataclass(frozen=True, slots=True)
class DubinsArc:
    start: Pose
    end: Pose
    turn_radius_m: float
    segments: tuple[Segment, ...]

# DubinsArc.sample — the arc sampler (towplanner.py:197); NOTE: lives on DubinsArc, not MovesPlan
def sample(self, *, step_m: float = 0.05, step_deg: float = 1.0) -> Iterator[Pose]: ...

# Move — one body's entry (towplanner.py:235-258)
@dataclass(frozen=True, slots=True)
class Move:
    plane_id: str
    target_slot: Pose
    path: DubinsArc | None     # None == deferred/unrouted (best-effort)

# MovesPlan — the ADR-0003 byte-identity artifact (towplanner.py:261-269); NO methods
@dataclass(frozen=True, slots=True)
class MovesPlan:
    target_layout: Layout
    moves: tuple[Move, ...]

# plan_reeds_shepp — closed-form fewest-moves RS path (towplanner.py:922-924)
def plan_reeds_shepp(
    start: Pose, end: Pose, *, turn_radius_m: float, lateral: bool = False
) -> DubinsArc: ...

# path_first_conflict — the validity oracle (towplanner.py:1306-1314)
def path_first_conflict(
    arc: DubinsArc,
    mover: Aircraft | GroundObject,
    *,
    mover_on_carts: bool,
    placed: Layout,
    step_m: float = 0.05,
    step_deg: float = 1.0,
) -> Conflict | None: ...
```

Supporting signatures Task 3 will likely also touch (recorded for completeness):

```python
# plan_dubins — forward-only closed-form arc (towplanner.py:431)
def plan_dubins(start: Pose, end: Pose, *, turn_radius_m: float) -> DubinsArc: ...

# plan_path — Hybrid-A* search; SEAM A return site is its DubinsArc (towplanner.py:2418-2431)
def plan_path(
    mover: Aircraft | GroundObject,
    entry: Pose,
    goal: Pose,
    *,
    hangar: Hangar,
    placed: Layout,
    mover_on_carts: bool,
    entries: tuple[Pose, ...] | None = None,
    max_expansions: int = _MAX_EXPANSIONS,
    heuristic: Literal["euclidean", "grid"] = "euclidean",
    heuristic_fn: Callable[[Pose], float] | None = None,
    stats: dict[str, object] | None = None,
) -> DubinsArc: ...
```

### Step 5 — verification (what this section establishes)

- **(a) Deterministic seam, with `file:line`:** the `Segment`-tuple of the `DubinsArc` returned by `plan_path` (`towplanner.py:2686`), folded into `Move.path` by `plan_fill` (`:1767`, `:1838`) and emitted as `MovesPlan` (`:1841`); validated read-only by `path_first_conflict` (`:1306`).
- **(b) Honest closed-form cross-machine assessment:** today's `MovesPlan` is closed-form-only (no iterative floats) but transcendental-derived (`sin/cos/atan2/sqrt/acos`); IEEE-754 pins `sqrt`/arithmetic/`hypot` but **not** the transcendentals, so the baseline is robustly same-/cross-process **same-machine** and only **presumptively** cross-machine — an untested assumption ADR-0003 never states (ties to Step 0.1 Evidence C).
- **(c) API block:** real signatures copied verbatim from source with line citations; the brief's "`MovesPlan.sample`" corrected to `DubinsArc.sample` (`:197`) — no placeholders.

---

## Step 0.3 — reduction / snap experiment

### Verdict: a discrete-word reduction is **mechanically real and byte-stable same-machine** — repeated (and separate-process, incl. `PYTHONHASHSEED=random`) construction of a snapped own-gear trajectory is **byte-identical**, and the read-only validity oracle (`path_first_conflict`, `towplanner.py:1306`) **consumes and accepts** it exactly as it does a native planner arc. So the Step 0.1 *"unshippable — no reduction exists"* failure mode is **ruled out**: a continuous own-gear optimum **does** reduce to the existing finite `Segment` word vocabulary that today's closed-form machinery already realizes. **But** the reduction inherits the baseline's cross-machine status *unchanged* (only *presumptively* cross-machine — same un-guarded `libm` hazard as Step 0.2), and the reduced artifact lives in the *exact* word space the existing A\* already searches, so a continuous optimizer adds nothing *deterministically bankable* over A\* for the shippable `MovesPlan`. The evidence therefore points **away from a clean PASS and toward the §5 NO-GO (dominated) band** (find-then-cache / "A\* already reaches it"), not the NO-GO (unshippable) band — Task 4 assigns the final band.

### The experiment (throwaway script, NOT committed)

A local scratch probe (`gate0_snap_probe.py`, deleted with the scratchpad — not in `bench/` or `tests/`, per §7) using the verified Step 0.2 API:

1. **Fabricated representative own-gear parallel-park.** A short list of `Pose` waypoints with equal start/end heading (90°), a net lateral offset, routed *via a deep cusp* — the shuffle shape the fk9↔cessna nook needs. The fk9 is `tow_pivotable`, so `Aircraft.effective_turn_radius_m()` returns **`0.0`** (confirmed live in the probe) — i.e. **R = 0 is the faithful own-gear motion model** for this exact aircraft, not a stand-in.
2. **Snap.** Chain closed-form `plan_reeds_shepp(a, b, turn_radius_m=0.0)` (`towplanner.py:922`) shots between consecutive waypoints. At R = 0 this delegates to `_plan_cart` (`:946-947` → `:477`), which emits a pivot–straight–pivot word in the `Segment` vocabulary (`kind ∈ {L,S,R,T}`, `gear ∈ {±1}`, `length_m ≥ 0`; `:86-109`) — the byte-identity payload Step 0.2 named as the seam.
3. **Serialize + diff.** `repr` of each arc's `start`/`end`/`turn_radius_m` and every `Segment` field (`repr(float)` is round-trip-exact, so this captures the bits). Built **twice** in one process; and built in **two separate interpreters** (one under `PYTHONHASHSEED=random`) and `diff`ed.
4. **Validity (best-effort).** Loaded the real `fk9_mkii` + `cessna_140` and the real 15.08 × 31.76 m Herrenteich hangar (`load_layout("examples/herrenteich/layout.yaml")`, the same recipe as `bench/se2_heuristic_probe.py:73-79,225-249`); built a *single* snapped own-gear connector (door entry → the real fk9 goal pose) and ran `path_first_conflict` against (a) an empty parked layout and (b) the cessna parked at its Herrenteich goal.

> **Step 1 (cheap witness check, per the brief):** there is **no cached fk9↔cessna witness pose sequence** to reuse — `bench/se2_heuristic_probe.py` re-runs the `plan_path` search at the fine 0.25 m/10° grid each time (it caches no path), and the only `witness*` artifacts under `tests/` are the **ML feasibility-witness *layouts*** (`tests/fixtures/ml/witness_*.yaml`), not a tow trajectory. The 39-min re-run was therefore the *only* way to obtain the real continuous path, and the fabricated representative made it **unnecessary** — the byte-stability property is a property of the *representation + snap*, which the representative exercises in full.

### Result 1 (CORE) — byte-stability: **`BYTE_IDENTICAL_REPEAT: True`**

- **Same-process, repeated construction:** `BYTE_IDENTICAL_REPEAT: True`.
- **Separate interpreters (same machine), incl. `PYTHONHASHSEED=random`:** `CROSS_PROCESS_BYTE_IDENTICAL: True` — matching the Step 0.2 evidence-class of `tests/test_solver_parallel.py` (cross-process, same machine). No cross-machine claim is made or testable here (single `libm`).

The serialized snap shows the expected cusp — a reverse straight leg at a waypoint join — confirming the parallel-park shape, e.g.:

```
arc[1] start=Pose(x_m=0.4, y_m=1.8, heading_deg=55.0) end=Pose(x_m=-0.3, y_m=1.2, heading_deg=130.0) r=0.0
  seg[0] kind=L length_m=0.09776103392965524 gear=1
  seg[1] kind=S length_m=0.9219544457292888 gear=-1     # <- reverse leg = the cusp
  seg[2] kind=R length_m=1.4067579729254025 gear=1
```

### Result 2 (best-effort) — validity oracle consumes the snapped arc

- **`fk9.effective_turn_radius_m()` = `0.0`** (own-gear pivot-in-place — R = 0 is faithful).
- **Acceptance (empty parked layout):** `path_first_conflict(...)` → **`None`** (accepted). A snapped own-gear `DubinsArc` is a first-class input to the validity oracle — `arc.sample()` walks it and `collisions.check` clears it exactly as for a native Reeds–Shepp arc.
- **Honest read (cessna parked):** → **`Conflict(kind='wing_wing_overlap', planes=('cessna_140','fk9_mkii'), …)`**. This is **expected and not a determinism finding**: a *naive direct* entry→goal connector clips the parked cessna's wing — which is precisely the nook problem (a direct own-gear shot does not avoid the cessna; that is why the corridor needs the shuffle/search). The point it establishes is that the oracle **reads the snapped arc and returns a faithful verdict**, so a reduced trajectory enters validation on identical terms to the existing planner output.

### Interpretation — reduction-escape vs dominance

**1. Is repeated snap construction byte-identical (same-machine)? Does it validate?** Yes and yes — `BYTE_IDENTICAL_REPEAT: True` (same- and cross-process, incl. hash-randomized), and the snapped arc is accepted by `path_first_conflict` (empty layout → `None`; obstacle case returns a faithful `Conflict`). The snap is deterministic enough same-machine.

**2. Reduction escape — supported, but only to the baseline's degree.** The experiment confirms the Step 0.1 prediction: a continuous own-gear (R = 0) optimum is expressible as a discrete `Segment` word (pivot/straight primitives, `:477`–`:574`) that the *existing* closed-form machinery realizes byte-identically same-machine. A continuous optimizer's role can be absorbed by this deterministic snap — its output need not be carried as raw iterative floats. **However**, the snap is *not* transcendental-free: even the R = 0 own-gear word derives its pivot-angle `length_m` from `math.atan2` (`_plan_cart:517`) (the straight leg uses `math.hypot`, `:501`, which *is* IEEE-correct). So the reduction inherits the **same un-guarded `libm` cross-machine hazard** Step 0.2 documented for the baseline — *no better, no worse*. The reduction escapes the *"raw-float-output"* unshippability, but **not** the cross-machine-bit-stability question Step 0.1's contract wording demands; under that bar the reduced plan is exactly as (un)provable cross-machine as today's shipped Reeds–Shepp `MovesPlan`.

**3. Dominance — this is where the evidence bites.** The reduced artifact lives in the *exact* finite word/primitive vocabulary the existing Hybrid-A\* already searches (`_primitives:1920-1969` + the analytic RS suffix; the ~97 k-expansion near-C\* plateau in `lateral-shuffle.md`). A continuous optimizer's *only* differentiator over A\* is **speed** — and that value **does not bank as a deterministic shippable artifact**, for two reasons the experiment + Step 0.2 make concrete:
   - To be ADR-0003-comparable the optimizer's continuous output must be **re-snapped to a canonical grid** (Result 1's deterministic word). But snapping a continuous trajectory onto a finite, exactly-comparable vocabulary **is** the A\*-style discretization — so the shippable artifact is a word A\* could also produce; traj-opt adds nothing *to the artifact*, only a (cross-machine-fragile) faster route to it.
   - The near-tie **word selection** among snapped candidates resolves by a strict-`<`/`min`-keeps-first float comparison (`_plan_cart:573`; RS word tie-break `:386-390`), which Step 0.2 showed can pick a *different winning word* across `libm` builds — so even the *discrete* choice is only presumptively cross-machine.

   Net: for the **shippable `MovesPlan`**, a continuous optimizer collapses to *"deterministically realize a word A\* already reaches"* — i.e. either it is **dominated by the existing search**, or (if its speed advantage is banked by storing the found trajectory) it reduces to **find-then-cache = the already-rejected witness-cache** for the fixed Herrenteich pair (the §5 dominance floor; design spec §9 decision 5).

**Plainly:** a discrete-word reduction is **plausible and byte-stable same-machine** (so the *unshippable* band is off the table), but it **does not** add deterministic value over A\* for the shippable artifact and **does not** improve on the baseline's untested cross-machine presumption. The evidence points toward the **§5 NO-GO (dominated)** band — not PASS, not NO-GO (unshippable). Task 4 finalizes the band and severity.

---

## Verdict (Gate 0) — **NO-GO (dominated)**

**Band (exactly one, per spec §5): NO-GO (dominated).** A continuous trajectory
optimizer for the fk9_mkii↔cessna_140 nook **could** produce a byte-stable
`MovesPlan` (the *unshippable* band is off the table), but the **only** deterministic
realization is a Reeds–Shepp word the existing Hybrid-A\* search already reaches — so
traj-opt's sole differentiator (speed) **cannot bank as a shippable deterministic
artifact** without collapsing to the already-rejected witness-cache. Gate 0 therefore
**does not unlock Gate 1 (convergence)**; the XL optimizer build is retired.

### Decisive evidence (each claim traces to Step 0.1 / 0.2 / 0.3)

1. **The bar is cross-machine (Step 0.1).** ADR-0003's binding *wording* promises
   byte-identity **across machines** for the `max_restarts`-bounded mode any shipping
   feature would use (`docs/adr/0003-…:380-382, 396-398`), even though the canary net
   only *tests* same-machine / same-process re-runs (`tests/test_solver_canaries.py`,
   Step 0.1 Evidence B), and neither the ADR nor `determinism-guard` guards the
   `libm`/transcendental cross-build ULP hazard (Step 0.1 Evidence C). So a raw
   iterative-float optimizer output is almost certainly un-shippable; only a
   **discrete-word reduction** could survive.

2. **The baseline already lives in that reduction — and is itself only *presumptively*
   cross-machine (Step 0.2).** Today's `MovesPlan` carries **no iterative float**: every
   `Segment.length_m`/`Pose` value is a closed-form constant or a one-shot
   `sin`/`cos`/`atan2`/`sqrt`/`acos` evaluation (Step 0.2 Step 1). It is robustly
   reproducible **same-machine / cross-process** but its **cross-machine** byte-identity
   is an *untested presumption* resting on `libm` transcendental bit-stability that
   ADR-0003 never states (Step 0.2 Step 2). The seam an optimizer's output would enter is
   the `Segment`-tuple of the `DubinsArc` returned by `plan_path` (`towplanner.py:2686`),
   folded into `Move.path` by `plan_fill` (`:1767`/`:1838`) (Step 0.2 Step 3).

3. **The reduction-escape is mechanically real — but adds nothing deterministically
   bankable (Step 0.3).** A fabricated own-gear (R = 0, the faithful fk9 motion model —
   `effective_turn_radius_m() == 0.0`) parallel-park snapped via chained closed-form
   `plan_reeds_shepp(...)` shots is **`BYTE_IDENTICAL_REPEAT: True`** both same-process
   and across separate interpreters (incl. `PYTHONHASHSEED=random`), and
   `path_first_conflict` **consumes and accepts** it exactly as a native arc (Step 0.3
   Results 1 & 2). This **rules out NO-GO (unshippable)**: a discrete-word reduction
   exists. **But** (i) the snapped artifact lives in the *exact* finite `Segment`/word
   vocabulary the existing A\* already searches (the ~97 k-expansion near-C\* plateau,
   `lateral-shuffle.md`), so re-snapping to a canonical grid **is** the A\*-style
   discretization — the shippable word is one A\* could also emit; and (ii) the snap
   inherits the baseline's *presumptively*-cross-machine status unchanged (it still calls
   `math.atan2` for the pivot angle — Step 0.3 Interpretation §2), so it is no more
   provably cross-machine than today's Reeds–Shepp emit.

### Why dominated, not PASS or unshippable

The optimizer's only edge over A\* is **speed**. To be ADR-0003-comparable its
continuous output must be **re-snapped** to the canonical word — which redoes the very
discretization A\* performs (so the artifact is **A\*-reachable**, the first §5
dominated clause). The only way to bank the speed is to **store the found trajectory**
— which is exactly **find-then-cache = the already-rejected witness-cache** for the
fixed Herrenteich pair (the second §5 dominated clause; spec §9 decision 5). Both
deterministic realizations are dominated; carrying raw iterative floats fails the
cross-machine bar (claim 1). No concrete, plausibly-bit-stable path that is *both*
not-A\*-reachable *and* not-witness-cache exists — so **PASS → Gate 1 is not earned.**

### Disposition

- **The fk9↔cessna corridor remains a documented manual-insertion case.** The auto-router
  cannot tow-route the front-door pair and that is expected, not a bug; the club hand-shuffles
  it on own gear (see `herrenteich-fk9-cessna-lateral-shuffle.md` "Known manual-insertion
  case"). Caching the 39-min witness plan stays rejected (brittle/unfaithful — spec §9
  decision 5).
- **No code, no dependency, no `src/` change** was produced by this gate (the Step 0.3 probe
  is a throwaway script, deleted with the scratchpad — §7).
- **The all-8 stays a manual-insertion arrangement** regardless of this verdict: the separable
  husky front-cluster ordering blocker (follow-up **a**) is unresolved (spec §8), so the
  headline does not change.

### The one surviving value angle (NOT funded by this gate)

The single argument Gate 0 does **not** kill is **generalization**: if continuous
trajectory optimization were chartered not for *this fixed pair* but for **arbitrary
tight nooks** the club may meet in future (so "find-then-cache" has no cache to fall
back to and the dominance floor does not apply), the method could carry value A\* and a
hand-shuffle cannot. **That bet is not funded here.** It would require an explicit user
**re-charter** (the current charter is the fixed fk9↔cessna pair, spec §9 decision 1),
**and** it still faces the unresolved **cross-machine wall** of claim 1 — the snapped
word is only *presumptively* cross-machine byte-identical, exactly like today's
baseline, an `libm`-ULP hazard ADR-0003 neither states nor guards. So even a
re-chartered generalization spike must clear that wall (e.g. by pinning or guarding
transcendental bit-stability across toolchains) before any optimizer output could ship.
This gate's NO-GO (dominated) is final for the chartered fk9↔cessna nook.
