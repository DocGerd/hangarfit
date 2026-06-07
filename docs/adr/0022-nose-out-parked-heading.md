# ADR-0022: Nose-out parked heading — RNG-free 180° flip post-pass, plus the `tow_pivotable` towing-motion flag

- **Status:** Accepted

- **Date:** 2026-06-08
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

Owners want a parked aircraft pointing **out** — nose toward the door at `y = 0`
— so it can taxi straight out without a multi-point turn. Surfaced in the
2026-05-26 UAT (issue [#263](https://github.com/DocGerd/hangarfit/issues/263)).

Under the ADR-0002 convention `heading_deg` is the compass angle of the nose from
world **+y** (deeper into the hangar), CW positive: **`heading 0` = nose-IN**
(toward +y), **`heading 180` = nose-OUT** (toward the door at −y). "nose-out-ness"
= the short-arc heading distance to `180`.

Today the RR-MC solver chooses each parked heading purely for **packing density**
(`_initial_placement_for_plane` seeds `heading = rng.random()*360`, perturbed by
the descent and the `_spread` post-pass, ADR-0008), then hands it to the tow
planner verbatim. Nothing favours `heading ≈ 180`. This ADR answers: how do we
express a nose-out *preference* without touching hard feasibility or the ADR-0003
determinism contract?

### Why this was unblocked

#263 was deferred twice (2026-06-01, 2026-06-02) on the **entry-vs-exit
objective**: the tow planner only plans the empty-hangar **FILL = ENTRY** (door →
slot), so a nose-out goal could *raise* entry cost (you must reverse/pirouette the
plane in), trading exit ease for entry cost. **#480 (ADR-0010 amendment) cleared
this empirically**: the cusp-penalty cost model + nose-out-gated rear-entry cone +
cost-aware start-seed now **back a nose-out plane in** (tail-first) at ~zero extra
entry cost — a direct probe on the roomy-3 fixture routed the two flippable planes
at **−0.00 m** and **+0.17 m** vs. their nose-in cost (the cost-aware start-seed
closes the back-in analytically, so the probe saw `expansions = 0`); the ADR-0010
amendment pins the in-hangar swept turn dropping **162° → <45°**. #480
shipped the routing half ("make a nose-out slot cheap to *reach*"); this ADR is
the solver-preference half ("make the solver *prefer* to pick it").

## Decision Drivers

- **Soft preference, never a hard constraint** (user decision 2026-05-26): never
  override collision validity, fit, or the existence of a feasible plan; act only
  as a tiebreak; be overridable per-plane (some fleets legitimately want nose-IN,
  e.g. a low-wing tucked under a high-wing's tail).
- **Preserve the ADR-0003 determinism contract** — and ideally strengthen it.
- **Isolate the soft logic from the hard-feasibility code** (the ADR-0008
  precedent).
- **Don't split heading ownership** across solver and planner.

## Considered Options

1. **RNG-free `_nose_out` 180° flip post-pass** in the solver, after `_spread`.
   *(Chosen.)*
2. **Fold nose-out into the RNG-driven descent / a third candidate-scoring term** —
   perturbs the seeded RNG stream, forcing a re-baseline of every determinism
   golden (the fused-regime design ADR-0008 already rejected).
3. **Let the tow planner pick the goal orientation** — the planner knows the door,
   but heading is the *solver's* DOF; this splits ownership and conflates
   orientation with tow-routability.

## Decision Outcome

**Chosen: an RNG-free `_nose_out` post-pass** (`solver.py`), called when a
trajectory reaches `(0, 0.0)`, **after** `_spread` and **independently of it**
(also on the `spread=False` fast path and the spread→no-spread fallback). For each
movable plane in `sorted(plane_id)` order it applies the **zero-displacement
antipodal flip** `heading = (h + 180) % 360` (preserving x/y/on_carts) iff:

1. the flip is **strictly more nose-out** —
   `short_arc(flipped, 180) < short_arc(current, 180)`; and
2. the layout **stays valid** — `_score(trial) == (0, 0.0)`.

Each flip is re-validated against the **current** (possibly already-flipped) set,
one plane at a time, so two individually-valid flips can never jointly invalidate.
It is **on by default** (`SearchConfig.nose_out=True`, `--no-nose-out` to disable),
with a per-plane tri-state override `PlaneConstraint.nose_out: bool | None` (`None`
⇒ follow the global; `True` ⇒ prefer-out; `False` ⇒ never flip).

### The flip gate is one rule, not two

Because the flip is the **exact antipode** and
`short_arc(antipode, 180) = 180 − short_arc(h, 180)`, "strictly more nose-out" is
*identical* to "lands in the nose-out hemisphere (`short_arc(flipped, 180) < 90`)".
Both reduce to *flip iff the current heading is in the nose-in hemisphere*. There
is no near-sideways special case; a 95°-off plane flips to 85°-off under either
framing.

### Why a post-pass and not descent fusion (option 2)

The descent is conflict-driven and RNG-seeded; injecting a nose-out bias into its
candidate scoring would shift the seeded RNG stream, breaking byte-identity for
every fixture and forcing a golden re-baseline. A separate RNG-free post-pass
keeps the hard-feasibility code and the RNG stream untouched — and (below) is
byte-identical *even with the feature on*.

### Why not the planner goal-pose (option 3)

Heading is the solver's degree of freedom. Letting the planner pick the goal
orientation splits one concept across two modules and conflates "which way it
faces when parked" with "can the tow planner thread to it". Keeping the preference
in the solver and the cheap routing in the planner (#480) is the clean split:
**solver = preference, planner = routing**. Consequently `--no-nose-out` disables
only the solver preference; the #480 rear-entry cone stays geometry-gated and
simply never fires when the solver produces no nose-out targets.

## The `tow_pivotable` sub-decision

A per-plane **`Aircraft.tow_pivotable: bool` (default `False`)** marks a plane
that pivots in place when **towed** — a free-castering tailwheel (e.g. Aviat
Husky) or a tail-down nose-lift on the mains (light nosewheel types). A flagged
plane's `effective_turn_radius_m()` returns `0.0`, so the tow planner routes it
with the existing zero-radius **cart-pivot fan** (`_plan_cart`) — **no new motion
primitive, `towplanner.py` untouched**. It is **orthogonal to `movement_mode`**: a
flagged own-gear plane stays `on_carts=False`, so the `Layout` cart invariants and
cart-pool accounting are unchanged. The declared `turn_radius_m` is retained
(powered-taxi semantics); only the *tow* radius is overridden. Flagged in
`data/fleet.yaml` for `aviat_husky`, `fk9_mkii`, `ctsl` (datum-pivot approximation:
their main gear is ≤ 0.5 m from datum; the main-gear-offset pivot is deferred).

**It is a realism flag, not a routing-cost lever.** The original 2026-05-29
characterization motivated it as "a 180° nose-out flip plans ~15 % shorter as a
pivot than an arc *loop*". That premise was **superseded by #480**: the planner no
longer loops to a nose-out goal — it backs in via reverse — so for an open-space
nose-out slot the pivot is empirically **no shorter** (measured ~+0.1 m, the pivot
needs cusps where the arc sweeps smoothly). It is kept because these plane types
*do* pivot when towed; modelling that is more faithful than forcing an own-gear arc.
The dense-fill towability premise was independently **falsified** during
characterization (the block is wing-transit geometry, which turn radius does not
touch); `tow_pivotable` must **not** be sold as a dense-fill fix.

## Consequences

### Positive

- Planes park nose-out wherever space allows; combined with #480's cheap back-in,
  exits are a straight pull. Hard feasibility is untouched — `_nose_out` can only
  re-orient-or-no-op, never invalidate or un-park.
- **Byte-identical determinism even with the feature ON.** `_nose_out` draws no
  RNG (no `rng` argument, no draws), so two `nose_out=True` solves with the same
  seed are bit-identical — strictly stronger than `_spread`, which guarantees
  byte-identity only when *off*. With `nose_out=False` the pass is never called, so
  output is byte-identical to the pre-feature solver.
- **Gap-neutral.** The flip is zero-displacement, so `min_pairwise_gap_m` and the
  `_select_spread_diverse` ordering are computed on the post-flip poses and the
  spread quality is preserved; nose-out can never fight spread.

### Negative

- Reaches only the antipode `{h, h+180}`, not an arbitrary nose-out angle —
  acceptable, since *in-vs-out* is the stated benefit.
- One new ordered iteration (`sorted(movable)`) is pinned by a cross-process
  (`PYTHONHASHSEED`-varied) canary so a set-order leak cannot slip in.
- The `determinism-guard` agent gains a one-line mechanism entry for `_nose_out`.

### Neutral

- A flip changes the swept polygon and thus `min_pairwise_gap_m`, so it can change
  *which* basin `_select_spread_diverse` picks — a deterministic, validity-neutral
  re-rank, never an invalid output.
- Default-ON would re-baseline fixture *headings* only — in practice **no existing
  test's assertions changed** (the determinism canaries are run-twice-diff, and
  behaviour tests assert validity/gap/status, which the position-neutral flip
  preserves). Five determinism canaries were nonetheless pinned to `nose_out=False`
  so they keep exercising the pre-feature RNG path explicitly (they pass either
  way).

## Compliance

- **`tests/test_solver_nose_out.py`** — `_nose_out` unit behaviour (flips a
  nose-in plane, no-ops an already-out one, leaves a sideways `h=90`, rejects a
  flip that breaks validity, never flips a pinned plane, honours the per-plane
  tri-state); the solve()-level per-layout `nose_out_flips` diagnostic; and the
  determinism trio — `nose_out=True` byte-identical, `nose_out=False` ==
  pre-feature, and the cross-process `PYTHONHASHSEED` canary.
- **`tests/test_solver_canaries.py` / `tests/test_solver_search.py`** — the
  byte-identical + `max_restarts` determinism canaries pinned with
  `nose_out=False` (the pre-feature path).
- **`tests/test_towplanner_pivot.py`** — `tow_pivotable` routes via the
  `turn_radius_m == 0` pivot fan, reaches the goal collision-free, deterministic.
- **`tests/test_models.py` / `tests/test_loader*.py` / `tests/test_cli_solve.py`**
  — the new model fields + validation, the loader tri-state + strict bool, the
  strict unknown-constraint-key allowlist (a misspelled `nose_out` is rejected, not
  silently dropped — the silent drop would invert the nose-IN exemption), and the
  `--no-nose-out` flags + `--json` `nose_out_flips`.

## More Information

- Related ADRs: [ADR-0002 — coordinate transform / heading convention](0002-determinant-minus-one-transform.md),
  [ADR-0003 — RR-MC solver algorithm and determinism contract](0003-rr-mc-solver-algorithm.md),
  [ADR-0008 — inter-plane spread soft preference](0008-inter-plane-spread-soft-preference.md),
  [ADR-0010 — Reeds–Shepp motion model (the #480 amendment that backs nose-out slots in)](0010-reeds-shepp-motion-model.md)
- Related specs / plans:
  [`docs/superpowers/specs/2026-06-07-nose-out-parked-heading-design.md`](../superpowers/specs/2026-06-07-nose-out-parked-heading-design.md),
  [`docs/superpowers/plans/2026-06-07-nose-out-parked-heading.md`](../superpowers/plans/2026-06-07-nose-out-parked-heading.md)
- Related issues / PRs: [#263](https://github.com/DocGerd/hangarfit/issues/263),
  [#480](https://github.com/DocGerd/hangarfit/issues/480)
