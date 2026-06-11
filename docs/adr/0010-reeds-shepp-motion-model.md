# ADR-0010: Reeds–Shepp motion model — towplanner v2

- **Status:** Accepted

- **Date:** 2026-05-27
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

[ADR-0007](0007-tow-path-planner-v1-scope.md) locked the tow-path planner's
motion model (its fork 2) to **forward-only Dubins** arcs. A forward-only car
cannot reverse: to reorient — e.g. to nose a plane *out* of a slot, or to
achieve a final heading that points back toward the door — it must drive a full
turning-circle loop. A UAT exposed exactly this waste: a plane parked nose-in
that needed to leave nose-out drove a ~32 m forward loop where an ~18 m
back-up-and-pull-forward would do. The question this ADR answers: *do we extend
the motion vocabulary to include reverse, and if so, with what model — keeping
the [ADR-0003](0003-rr-mc-solver-algorithm.md) byte-identical-plan determinism
contract intact?*

The label "v2" is the towplanner subsystem's scope tier (matching ADR-0007's
"v1" usage), not a `hangarfit` semver version.

## Decision Drivers

- **Eliminate the loop-to-reorient waste** the UAT exposed without giving up
  closed-form planning.
- **Preserve the ADR-0003 determinism contract end-to-end.** Same seed → same
  target layout → same plan, byte-identical. Any probabilistic planner
  (RRT-Connect) would break this; the motion model must stay closed-form.
- **Reuse the existing integrator and collision substrate.** The
  `DubinsArc.pose_at` walker, the `path_first_conflict` oracle, and the
  Hybrid-A\* search (#222) should carry over with the smallest possible delta.
- **Prefer forward motion.** Reverse is a tool for the cases that need it, not
  a free substitute — a plan that gratuitously backs up is harder for a human
  to execute and easier to get wrong.
- **Keep cart (`turn_radius_m = 0`) handling uniform.** A carted plane should
  gain "back straight out of a slot" for free, under the same model.

## Considered Options

1. **Full closed-form Reeds–Shepp** (Dubins + reverse arcs/straights),
   weighted-length word selection that prefers forward *(chosen)*.
2. **Keep forward-only Dubins** (the ADR-0007 status quo) and live with the
   loop-to-reorient waste.
3. **RRT-Connect** (sampling-based, bidirectional) — the general escape hatch
   for hard packings, ADR-0007's named v2 candidate.
4. **Ad-hoc "back up then re-plan forward" heuristic** bolted onto the Dubins
   planner.

## Decision Outcome

**Chosen option: full closed-form Reeds–Shepp**, because it removes the
loop-to-reorient waste while remaining closed-form and deterministic — so the
ADR-0003 contract holds unchanged — and it slots into the existing integrator
and search with a minimal, well-tested delta.

Concretely:

- **Data model.** `Segment` gains a `gear: Literal[1, -1] = 1` field
  (`+1` forward, `-1` reverse). `kind` (L/S/R) is *steering*; `gear` is *travel
  direction* — independent. The default `+1` keeps every pre-existing
  forward-only `Segment(kind, length)` call (and all Dubins-era tests) valid.
  `DubinsArc` is retained as the path container (renaming it would be churn for
  no behavioural gain); its `pose_at` integrator now applies `gear` to the
  translation step — a reverse straight drives −cos/−sin, a reverse arc retreats
  around the steering-determined turning centre.
- **Closed form.** `plan_reeds_shepp(start, end, *, turn_radius_m)` sits beside
  `plan_dubins`. It is built by the textbook **base-formula + symmetry-generation**
  method: a handful of base word-solvers (CSC, CCC, CCCC, CCSC, CCSCC) in the
  normalised math frame, enumerated under the **timeflip / reflect** goal
  symmetries to generate the classical Reeds–Shepp word family mechanically (no
  hand-transcription of all 48 ad-hoc formulae).
- **Cost model — prefer forward.** Word selection minimises
  Σ(`|leg_length|` × factor), where a **reverse** leg carries
  `_REVERSE_COST_FACTOR = 1.5`.
- **Search integration.** Own-gear (`r > 0`): `_primitives` returns **six**
  primitives in fixed order `Lf, Sf, Rf, Lr, Sr, Rr`. Cart (`r == 0`): **four**
  — `Lf, Sf, Rf, Sr` — the reverse pivots are omitted because a reverse pivot
  rotates heading the same way as the opposite forward pivot (an exact duplicate
  that always loses the `best_g` race), so only the reverse *straight* is a new
  cart move. `_seg_cost` multiplies a reverse leg by the factor; the Hybrid-A\*
  analytic-expansion shot is `plan_reeds_shepp` instead of `plan_dubins`.
- **Cart.** `plan_reeds_shepp` delegates the `turn_radius_m == 0` case to the
  shared `_plan_cart` helper with reverse enabled, so a carted plane may back
  straight out of a slot when that is cheaper.

This **supersedes [ADR-0007](0007-tow-path-planner-v1-scope.md) fork 2
("Dubins-only")**. The rest of ADR-0007 (empty-hangar-fill scope, cart =
own-gear with `turn_radius_m = 0`, the `effective_turn_radius_m()` accessor,
the door-as-motion-gate, the bounded greedy-order retry) stands.

### Why `_REVERSE_COST_FACTOR = 1.5`? (Superseded — see the [2026-06-07 #480 amendment](#amendment-2026-06-07--480-fewest-moves-cost-model--nose-out-back-in))

> **Superseded.** The multiplicative reverse-length factor was replaced by an
> additive **cusp** penalty in the #480 amendment below; this section is kept for
> the historical rationale.

A measured nose-out case is **18 m reverse vs 32 m forward**. At **1.5×** the
reverse weighs 27 m, still beating the 32 m forward loop, so the reverse win is
kept — while a gratuitous short reverse is discouraged relative to an equal
forward leg. At **2.0×** the same reverse would weigh 36 m and be *suppressed*
in favour of the longer forward path, defeating the point of adopting
Reeds–Shepp at all. 1.5 is the value that keeps the genuine win and still biases
toward forward; it is pinned by a unit test so a casual retune trips a guard and
forces an update to this ADR.

### Why not keep forward-only Dubins (option 2)?

That is the waste this ADR exists to remove. Forward-only is simpler, but the
UAT showed it produces plans a human would reject as obviously circuitous (a
full loop where a short back-up suffices). The simplicity is not worth shipping
plans the operator will not trust.

### Why not RRT-Connect (option 3)?

RRT-Connect remains the escape hatch for genuinely tight packings that *no*
closed-form vocabulary can route (it is still named as future work). But it is
sampling-based: its determinism story would have to be reconciled with
ADR-0003's seeded contract (per-tree RNG, deterministic merge order), it adds
300–500 lines, and — crucially — **it does not address the problem this ADR
targets.** The UAT waste is not an unsolvable-packing problem; it is a
*missing-reverse-vocabulary* problem, which Reeds–Shepp solves in closed form
with the determinism contract intact. RRT-Connect would be a much larger,
probabilistic answer to a question reverse arcs already answer cleanly.

### Why not an ad-hoc "back up then re-plan" heuristic (option 4)?

Bolting a special-case "if stuck, reverse a bit and try again" onto the Dubins
planner reintroduces exactly the two-mode complexity ADR-0007 worked to avoid,
and its behaviour at the seams (when does it trigger? how far does it back up?)
is hard to make deterministic and hard to test. Reeds–Shepp is the principled,
closed-form generalisation that makes reverse a first-class leg in the *same*
word algebra, not a bolt-on. One vocabulary, one cost model, one integrator.

## Consequences

### Positive

- The loop-to-reorient waste is gone: a plane can back up to reorient, and the
  cost model picks the shorter geared maneuver.
- The determinism contract (ADR-0003) holds: closed-form, RNG-free, fixed word
  iteration order + strict-`<` tie-break.
- Cart planes gain "back straight out of a slot" under the same model.
- Cases that were genuinely infeasible for forward-only Dubins (a turned goal a
  forward car could not reach in-bounds) are now routable — the search finds
  them via the reverse primitives, exact-oracle-clean.
- Minimal data-model delta: one defaulted `Segment.gear` field; all existing
  forward-only call sites and tests are untouched.

### Negative

- The own-gear search primitive fan **doubled** from 3 (forward L/S/R) to 6 (+
  reverse L/S/R), so each Hybrid-A\* expansion screens roughly twice the edges.
  On an un-towable layout the worst-case budget-exhaustion bail time roughly
  doubled (the six-plane fresh-fill perf gate went ~26 s → ~50 s; its `slow`
  ceiling was raised 30 s → 75 s with a documented rationale). The redundant
  reverse *cart* pivots were already pruned (the cart fan is four, not six — see
  Decision above); a future pass can further trim the own-gear fan or gate
  reverse edges behind a heuristic.
- Two coexisting closed-form planners (`plan_dubins`, `plan_reeds_shepp`). The
  Hybrid-A\* analytic shot now uses Reeds–Shepp; `plan_dubins` remains for the
  forward-only Dubins tests and as the historical reference. A future cleanup
  could retire `plan_dubins` once nothing depends on forward-only behaviour.

### Neutral

- **`DubinsArc` keeps its name** despite now carrying geared, possibly-reverse
  segments. Renaming it to `MotionArc`/`Path` would touch the public-ish type
  reference in `cli.py`/`visualize.py`/`models.py` (annotation-only, under
  `TYPE_CHECKING`) and every test, for no behavioural gain. The class docstring
  and the module docstring carry the "now Reeds–Shepp, segments carry gear"
  framing so a reader is not misled by the legacy name.
- The reverse **front-gap exemption (#222)** is free: `_mover_motion_bounds_conflict`
  is pose-only and gear-agnostic, so a plane backing out through the door at
  `y < 0` is exempt on the front wall but still bounded on the side/back walls,
  exactly as a forward mover is.

## Compliance

- **Integrator round-trip is the primary correctness oracle.**
  `tests/test_towplanner_reeds_shepp.py::test_reeds_shepp_roundtrip_grid` walks
  the geared segments `plan_reeds_shepp` emits via `DubinsArc.pose_at` across a
  grid of start/end poses and several radii, asserting the integrated endpoint
  reaches the goal. Mirrors the Dubins `test_dubins_roundtrip_grid`. A
  transcription error in any generated word surfaces here as a missed endpoint.
  In the solver itself, every generated word is gated by a closed-form-independent
  re-integration (`_rs_word_reaches`) before it can be chosen, so a base-formula
  sign error becomes a *missing* candidate, never a *wrong path that ships*.
- **The 45° heading canary** (the ADR-0007-mandated convention guard) gains a
  **reverse** case:
  `tests/test_towplanner_dubins.py::test_heading_45_reverse_path_advances_into_minus_x_minus_y`
  asserts a reverse leg at heading 45° lands in the (−x, −y) quadrant (the exact
  negation of the forward case). The symmetric word matrix would pass a CW/CCW
  sign flip ([ADR-0002](0002-determinant-minus-one-transform.md)) silently; this
  geometric assert is the real guard for the reverse direction.
- **Cost-prefers-forward** is pinned by
  `test_collinear_forward_goal_stays_pure_forward_straight` and
  `test_reverse_beats_forward_loop_for_short_backup`; the factor value itself by
  `test_reverse_cost_factor_value` (= 1.5), so a retune trips a guard and forces
  this ADR to be updated.
- **Determinism** (ADR-0003) by `test_reeds_shepp_is_deterministic`
  (byte-identical segments on repeat) plus the fixed primitive order pinned in
  `test_towplanner_search.py::test_primitives_own_gear_are_six_in_lf_sf_rf_lr_sr_rr_order`.
- **Reverse front-gap exemption** by
  `test_towplanner_motion.py::test_reverse_through_door_is_front_gap_exempt`
  (exempt on the front wall) and `test_reverse_into_side_wall_still_bounded`
  (still bounded on the side wall).
- **Cart reverse-straight** by `test_cart_reverse_straight_backs_out`.
- **Geometry-invariant-guard review.** Any PR touching this motion math (the
  integrator, the heading adapter, the collision-during-motion check) must be
  reviewed by the `geometry-invariant-guard` subagent per the standing CLAUDE.md
  rule — the determinant-(−1) / compass-vs-math sign-flip trap
  ([ADR-0002](0002-determinant-minus-one-transform.md)) is the hazard the reverse
  legs add a fresh sign to.

## Amendment (2026-06-07) — #480: fewest-moves cost model + nose-out back-in

**Status:** Accepted. **Context:** a UAT/Herrenteich observation — a plane whose
*parked* heading is nose-out (toward the door) was towed nose-first deep into the
hangar and spun ~160° in the cramped back corner, because the door entry cone was
inward-only and the cost model penalised reverse *distance*. The paths were valid
but low-quality (and pessimistic for routability). [#480](https://github.com/DocGerd/hangarfit/issues/480).

Three coordinated changes, all RNG-free — the ADR-0003 determinism contract holds
(verified: serial canaries + the `bench` `det` verdict). Cross-version
byte-identity is intentionally re-baselined where noted.

1. **Cost model: cusp penalty replaces the reverse-length factor.** Word /
   path selection now minimises **`Σ|leg| + CUSP_PENALTY × cusps`** — gear-agnostic
   length plus a fixed additive penalty per **cusp** (a forward↔reverse
   *travel-direction* change between consecutive *translating* legs; in-place cart
   pivots don't translate and are excluded). This makes the objective **fewest
   moves** (`moves = cusps + 1`), not least-reverse-distance. `_REVERSE_COST_FACTOR`
   is removed; it applied at three sites (`_rs_solve_normalised`, `_cart_seg_weight`,
   `_seg_cost`) — all three now use the cusp model (the search charges the cusp
   incrementally in the expansion loop via a `_SearchNode.last_drive_gear`). The
   normalised RS solver gets `CUSP_PENALTY / r` so its choice agrees with the
   metre-space objective. **Forward preference is now purely the
   enumeration-order tie-break** (forward primitives/words enumerated first →
   equal cost keeps forward), not a per-metre tax.

   **Why `CUSP_PENALTY = 10.0` m?** It must (a) keep a genuine nose-out win — a
   rear-entry back-in is 0 cusps and wins on length alone, and where a 1-cusp
   back-in (~18 m) replaces a forward loop (~32 m) we need `CUSP_PENALTY < 14`
   m — and (b) dominate the small length differences between equal-move
   alternatives so the planner doesn't trade a direction change for a couple of
   saved metres. 10 m (order of a plane length / the hangar's short dimension)
   satisfies both; pinned by `test_cusp_penalty_value`.

2. **Rear-entry cone is nose-out-gated, apron-independent.** `entry_poses` emits
   the rear cone `{150°,165°,180°,195°,210°}` iff the *target* parked heading is
   nose-out (`|wrap180(target.heading − 180)| ≤ _REAR_CONE_HALF_ANGLE_DEG ≈ 45°`),
   with or without an apron (previously it was emitted only when an apron existed).
   A nose-out slot can therefore be **backed in** through the door; a nose-in slot
   keeps the 5-heading forward cone only (no wasted seeds). This changes the
   depth-0 grid for **nose-out** targets, **superseding the [#412](https://github.com/DocGerd/hangarfit/issues/412)/[ADR-0021](0021-tow-planner-staging-apron.md)
   depth-0 cross-version byte-identity for that case** (the ADR-0003 same-input
   contract is intact; only the historical depth-0≡pre-apron equality is given up,
   and only for nose-out targets).

3. **Cost-aware start-seed analytic expansion.** The rear cone + cusp cost alone
   did *not* fix nose-out: the Hybrid-A\* analytic expansion returned the **first**
   collision-clean shot in pop order, and the forward cone is enumerated first, so
   a forward entry that pirouettes inside was returned before the cheaper back-in
   was evaluated. The fix evaluates **every surviving start seed's** closed-form
   completion up front and returns the **cheapest collision-clean** one (the back-in
   is a start-seed shot, so it now wins); if no seed closes cleanly (an obstructed
   approach) it falls through to the unchanged greedy node-level search.
   - *Considered and rejected:* making the **whole** search cost-aware (keep the
     cheapest analytic completion across all popped nodes, admissible f-cutoff).
     It is more general (optimises obstructed nose-out too) but, with the loose
     default euclidean heuristic, explores to ~`max_expansions` before the cutoff
     fires — **13–26 s per `plan_path`** (vs milliseconds), impractical for
     `solve` and the bench perf gate. The bounded start-seed variant fixes the
     measured open-hangar/clear-approach cases at ~unchanged speed; **obstructed
     nose-out needing mid-search maneuvering stays best-effort** (greedy), which is
     an accepted limitation.

**Acceptance:** a nose-out slot's in-hangar swept turning drops from ~162° to a
back-in (<45°), verified by `tests/test_towplanner_nose_out.py`; no validity /
path-validity / determinism regression (`bench`; serial canaries); design spec
`docs/superpowers/specs/2026-06-07-480-fewest-moves-tow-routing-design.md`.

**Relationship to [#263](https://github.com/DocGerd/hangarfit/issues/263)** (prefer
a nose-out *parked heading*): this amendment makes a nose-out slot **cheap to
reach** when the solver picks one; #263 (separate) makes the solver *prefer* to
pick them.

## Amendment (2026-06-12) — #602: car (own-gear RS) + towed-trailer (free-swivel cart) routing

**Status:** Accepted. **Context:** Epic A (ground objects, #600) introduced
`GroundObject` movers into the hangar — specifically the VW Caddy and towed glider
trailers. `plan_fill` must be able to route them using the existing planner
infrastructure; no new planner and no new `SegmentKind` should be required.
[#602](https://github.com/DocGerd/hangarfit/issues/602).

### (a) Car = own-gear Reeds–Shepp parameterisation

A self-driving steerable car (e.g. the VW Caddy, `turn_radius_m ≈ 5.5 m`) routes
exactly as an own-gear aircraft: `effective_turn_radius_m()` returns the catalog
`turn_radius_m`, `_primitives` emits the six-primitive fan `Lf, Sf, Rf, Lr, Sr,
Rr`, and `plan_reeds_shepp` handles the full forward/reverse word algebra. No
behavioural change to any aircraft — the machinery is reused verbatim.

### (b) Towed trailer = free-swivel cart (`r = 0`)

A towed glider trailer has no powered steering and is balanced at the drawbar:
ground crew push/pull the tongue and let the trailer swivel freely.
`effective_turn_radius_m()` returns `0.0`; the planner selects the existing
four-primitive cart fan `Lf, Sf, Rf, Sr` via `_plan_cart(allow_reverse=True)`,
giving forward arcs/straights plus a reverse straight — sufficient for
push/pull-and-swivel positioning inside the hangar.

**Why the cart model, not a positive tug-turning-circle?** (a) It models the
actual human motion: a balanced trailer at the tongue can be hand-positioned with
any lateral displacement by swivelling; a minimum-turning-circle constraint would
be an over-restriction for manual ground handling. (b) It reuses the cart path
verbatim — zero new code, zero new parameters. (c) It needs no new catalog data:
the trailer carries no `turn_radius_m`, and the absence of that field is the
data-driven selector. Richer trailer-jackknife kinematics remain a possible
later follow-on, gated on whether the cart approximation proves insufficient for
the real Herrenteich corridor.

**Data-driven selector:** `effective_turn_radius_m()` is present and positive →
own-gear RS fan; absent (returns `0.0`) → cart fan. Both branches already existed.

### (c) The decision — new object behaviours, not a new planner

`GroundObject.effective_turn_radius_m()` mirrors `Aircraft.effective_turn_radius_m()`:
the mover is fully characterised to the planner by the pair
`(effective_turn_radius_m, reverse-capability)`. The internal routines
`_primitives`, `_plan_cart`, and `plan_reeds_shepp` are reused **unchanged**.

The routing oracle (`path_first_conflict` / `plan_path` / `_motion_clear` /
`_mover_motion_bounds_conflict`) was widened from `Aircraft`-only to
`Aircraft | GroundObject`. A ground-object mover is injected into each per-sample
`Layout` as a `ground_object_placement` so the collision substrate can check it
against the static scene; aircraft routing is unaffected and remains byte-identical
(ADR-0003 contract holds).

### (d) Rejected alternative: dedicated kinematic-bicycle / trailer-jackknife planner

A separate planner that models the full tractor–trailer articulation (jackknife
constraint, separate front/rear swept paths) was considered and rejected:
closed-form RS already provides forward + reverse arcs and straights, and the cart
model already provides reverse-straight + free-pivot — both sufficient for v1
ground handling in the measured Herrenteich corridor. A bespoke kinematic-bicycle
model would add substantial complexity (new integrator, new collision sweep, new
cost algebra) while the cart approximation remains valid for the balanced,
manually-positioned trailers in scope. The byte-identical determinism contract
(ADR-0003) is preserved with zero changes to the existing word algebra. Richer
jackknife kinematics are a named later follow-on if the cart approximation proves
insufficient.

## More Information

- Amended by #602 (2026-06-12): car own-gear RS + free-swivel-cart trailer routing
  via `GroundObject.effective_turn_radius_m()`; routing oracle widened to
  `Aircraft | GroundObject` (see the amendment section above).
- Amended by #480 (2026-06-07): cusp-penalty cost model, nose-out-gated rear cone,
  cost-aware start-seed analytic expansion (see the amendment section above).
- Supersedes: [ADR-0007](0007-tow-path-planner-v1-scope.md) fork 2 ("Dubins-only").
  The rest of ADR-0007 stands.
- Related ADRs:
  [ADR-0002](0002-determinant-minus-one-transform.md) (the heading-convention
  sign-flip trap the reverse legs must respect),
  [ADR-0003](0003-rr-mc-solver-algorithm.md) (the determinism contract this
  closed-form motion model preserves),
  [ADR-0007](0007-tow-path-planner-v1-scope.md) (towplanner v1 scope).
- Implementation: [`src/hangarfit/towplanner.py`](../../src/hangarfit/towplanner.py)
  (`plan_reeds_shepp`, `_plan_cart`, the `_rs_*` base solvers + symmetry
  generation, `_primitives`, `_seg_cost`).
- Tests: [`tests/test_towplanner_reeds_shepp.py`](../../tests/test_towplanner_reeds_shepp.py),
  the reverse canary in [`tests/test_towplanner_dubins.py`](../../tests/test_towplanner_dubins.py),
  the reverse-motion cases in [`tests/test_towplanner_motion.py`](../../tests/test_towplanner_motion.py),
  and the six-primitive / canary updates in
  [`tests/test_towplanner_search.py`](../../tests/test_towplanner_search.py).
- Related issue: [#261](https://github.com/DocGerd/hangarfit/issues/261).
- External references: Reeds, J. A. & Shepp, L. A. (1990), *Optimal paths for a
  car that goes both forwards and backwards*, Pacific J. Math. 145(2); the
  base-formula + symmetry-generation presentation follows the widely-used
  PythonRobotics / OMPL implementations.
