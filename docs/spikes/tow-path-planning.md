# Spike: Tow-path planning — collision-free entry sequence from empty hangar to solver target

- **Status:** Recommendation. No production code yet; follow-up implementation issues to be filed after this doc is reviewed and the direction is locked.
- **Date:** 2026-05-24
- **Spike issue:** [#180](https://github.com/DocGerd/hangarfit/issues/180)
- **Sibling spike:** [#172](https://github.com/DocGerd/hangarfit/issues/172) (AR-preview viewer)
- **Soft prerequisite:** [#145](https://github.com/DocGerd/hangarfit/issues/145) / [milestone #14](https://github.com/DocGerd/hangarfit/milestone/14) (Phase 2b solver realism — wall-pushed target layouts)

---

## Summary recommendation

Build a **tool-as-planner** for the **empty-hangar fill** case. Given a `Scenario`, the existing RR-MC solver produces a target `Layout`; a new `src/hangarfit/towplanner.py` module then computes (a) a deterministic entry order placing the deepest-y slots first and (b) a Dubins arc per plane from the door cone to its target pose, retrying the order on Dubins collision. Multi-candidate output bundles `(Layout, MovesPlan)` pairs across the existing diversity output (ADR-0004) and across alternative orderings per layout. Visualization is a polyline overlay on the existing PNG renderer.

This v1 is small (~10 PRs) and ships entirely from existing primitives plus closed-form Dubins arithmetic. Cart-mode is folded into the same path-planning machinery by treating cart-borne planes as own-gear with `turn_radius_m = 0`. RRT-Connect fallback, sweep-volume collision check, true cart-lift primitive, user overrides, the rearrangement scenario, and the YAML moves-file format are all explicit v2 work, deferred with rationale below.

> **Note on "v1 / v2" usage in this doc.** These labels refer to **scope tiers of the towplanner subsystem** — *not* `hangarfit` semver versions. v1 = the Phase 3a milestone proposed at the end of this doc; v2 = a Phase 3b milestone to be sized after v1 ships.

---

## Scope reframing vs the spike issue body

Issue #180 framed the spike around the **rearrangement** use case — "the layout the solver suggests requires me to pull three planes out and put two back in a different order." The brainstorm that produced this recommendation reframed it to the **empty-hangar fill** case instead: every plane starts outside, each plane is moved into the hangar exactly once, and the question is only what order they enter in and what path each takes from the door to its target slot.

This is a deliberate scope reduction, not a scope drift. The fill case is operationally common, mathematically simpler (no pull-out-and-repark cycles, no apron juggling, every plane moves once in one direction), and a strict prerequisite for the rearrangement case — a planner that cannot fill an empty hangar certainly cannot rearrange a full one. The rearrangement case is preserved as a v2 follow-up at the end of this doc; the apron rectangle, the `--current-layout` input, and the bidirectional move primitives that the rearrangement case needs are listed there explicitly so they are not forgotten.

The eight questions in the spike issue body still drive the recommendation structure below — each answer simply reflects the reduced scope.

---

## Recommendations per question

Each section restates the alternatives from the spike issue, the recommendation, and the reasoning. The alternatives matter: the recommendation is only credible if you can see what it beat.

### Q1. Initial-state input — where do the planes start?

**Alternatives.** Empty hangar (all start on apron) / user-supplied current-layout YAML / inferred from an outdoor annotation on `fleet_in`.

**Recommendation.** **Empty hangar only.** No `--current-layout` argument. The starting state is implicit and identical for every run: every plane in the scenario's `fleet_in` is outside, and each plane enters through the door exactly once during the sequence.

**Why.** This is the scope reframing called out above. The current-layout YAML, the outdoor-annotation hybrid, and the apron rectangle that PR #186 introduced are all in service of the rearrangement case; reducing scope to the fill case lets all three drop out together. The fill case is the strict prerequisite — building the rearrangement planner before the fill planner would force us to design the harder problem's data model before we have ground truth on what the simpler problem actually looks like in practice.

### Q2. Sequencing — what algorithm picks the move order?

**Alternatives.** Greedy peel-back / backwards-from-goal (warehouse) / A* over partial layouts / classical planner (PDDL) / user-supplied order.

**Recommendation.** **Greedy back-first.** Given the target `Layout`, sort placements by `target.y_m` descending (deepest slot first) with a deterministic tie-break (e.g. lexicographic on `(target.y_m desc, target.x_m asc, plane_id)`). Walk that order; for each plane, compute the Dubins path (Q3) and run the sampled collision check (Q4) against the already-placed subset of the fleet.

**Why.** Empty-hangar fill has a natural ordering: deeper slots first, because shallower slots become obstacles for anything that needs to reach the back. This is the classical warehouse "backwards from goal" pattern, but trivial to implement at our scale because the goal layout is already known (we don't need to reason about partial layouts the way a true backwards-search would). User-supplied order was the v1 in the verifier framing PR #186 chose; here the tool computes the order, which removes one layer of authoring friction. A* over orderings is overkill at fleet sizes ≤ 12 and adds an exponential state space we have no evidence of needing. PDDL would force an external dependency we have no other use for.

**Order-retry on Dubins collision.** Greedy back-first is not provably collision-free for every target layout — two same-depth planes whose Dubins arcs cross is a real failure mode. The mitigation is a bounded retry loop: when plane *P*'s Dubins path collides with an already-placed plane, swap *P* with the next-feasible plane in the order and re-try. After K failed swaps, fail with a structured error pointing at the offending plane and recommending the user adjust the scenario (or, in v2, RRT-Connect resolves it).

### Q3. Per-plane motion model — what does "moving" mean geometrically?

**Alternatives.** Straight-line teleport / Bezier spline / **Dubins paths** / RRT-Connect with Dubins primitives / hand-authored waypoints.

**Recommendation.** **Dubins paths only.** Each plane's path from the door cone to its target slot is a closed-form Dubins arc (shortest arc-line-arc path between two oriented poses under a minimum turn radius). The starting pose is constrained to lie inside the door interval with heading near +y (Q6); the ending pose is the target placement. The turn radius is the plane's `turn_radius_m` (which becomes a consumed field for the first time — see [crosscutting §8](../architecture/08-crosscutting-concepts.md) on the existing movement-modes hook).

**Why.** Dubins is the textbook nonholonomic primitive: closed-form, ~80 lines of well-understood code, consumes `turn_radius_m` honestly, naturally extends to fancier motion planning if v2 commits to RRT-Connect (which is itself just RRT over Dubins primitives). Straight-line is collision-meaningless and disrespects turn radius; Bezier offers no kinematic guarantee and still needs a turn-radius check it cannot honestly satisfy; hand-authored waypoints push authoring back onto the user and conflict with the "tool computes" framing. RRT-Connect itself is the v2 escape hatch — it adds ~300–500 lines and probabilistic determinism concerns we don't want to take on until Dubins-only + order-retry has been shown empirically insufficient.

The honest weakness of Dubins-only is that it ignores intervening obstacles — if the closed-form arc from the door to slot X passes through plane Y that's already placed, Dubins gives back a colliding path with no recourse other than re-ordering (Q2's retry loop). On tight packings this loop may fail to converge; that failure is honest and surfaces as a structured error, which v2 RRT-Connect can then resolve.

### Q4. Collision-during-motion — how is it checked?

**Alternatives.** **Path sampling** / sweep volume / continuous-collision detection (CCD).

**Recommendation.** **Path sampling** at a fixed step (start with 0.05 m of translation or 1° of heading change along the arc, whichever comes first; revisit if real authored paths show grazing collisions being missed). Each sample reconstructs the moving plane's parts in world coordinates via `geometry.aircraft_parts_world` and runs `collisions.check` against the already-placed subset.

**Why.** The collision oracle (`collisions.check` in `src/hangarfit/collisions.py`) already exists, already handles every existing rule (parts overlap, hangar bounds, bay intrusion), and already respects the parts-model granularity from [ADR-0001](../adr/0001-aircraft-parts-model.md). Sampling is the smallest delta to the codebase — the verifier becomes "iterate samples along the Dubins arc, call check, accumulate any conflict." Sweep-volume would need a new collision primitive (no `Layout`-shaped input for the existing oracle), and CCD would need yet another. Both are credible v2 upgrades if sampling's grazing-miss failure mode ever materially bites; neither is justified day-one. Tow speeds are slow and part rectangles are large relative to a 5 cm step, so the worst plausible miss is a part-thickness shave that real operators would correct visually.

### Q5. Cart-mode handling — does cart change anything?

**Alternatives.** Ignore cart-mode in v1 / two-mode motion with sequence-level cart cap / cart-specific lift-walk-place / **cart = own-gear with infinite-radius pivot**.

**Recommendation.** **Cart = own-gear with `turn_radius_m = 0`.** Cart-borne planes (the three `always_cart` planes Scheibe Falke, Wild Thing, Zlin Savage, and `cart_eligible` planes when the existing single-cart-eligible rule places them on a cart) feed the same Dubins-arc machinery as own-gear planes, just with a zero radius — which mathematically becomes a pivot-in-place.

**Why.** Uniform path-planning is the strongest argument: one motion primitive, one collision check, no special-casing of cart planes anywhere in the towplanner. A zero-radius "turn" is honest for cart-on-cart motion — operators do pivot a carted plane in place to align it with a slot. The alternative true-cart primitive (`lift_pose`, `place_pose` with no path between) is more realistic but multiplies the move-data shape and the renderer surface for a v1 that doesn't yet need it.

The honest weakness is **sequence-level cart cap**: the existing rule (at most one `cart_eligible` plane on a cart in any single layout, enforced in `Layout.__post_init__`) constrains layouts but not sequences. If the user authors a scenario with two `cart_eligible` planes that the solver places on a cart for *different* candidate layouts, the v1 towplanner happily plans both — it never re-checks the cap across moves of the same plan because each plan's target layout already satisfies the per-layout cap. This is correct under the current rule; whether the rule should be tightened to "at most one cart-eligible plane on the cart across the sequence" is a separate question that ADR-0007 (Q8) should call out and defer.

### Q6. Door constraint — how does the door figure into motion?

**Alternatives.** Soft door / hard door / hard door + apron holding area / **hard door, no apron**.

**Recommendation.** **Hard door with no apron.** Each plane's path starts at a pose `(x, y, heading)` where `x` is inside the door interval, `y = 0` (the front boundary), and `heading` is near +y (the plane is pointed into the hangar). The path ends at the target placement. No region exists outside the hangar at negative y; planes are conceptually "on the apron" before the move, but the apron is not modelled geometrically because nothing in v1 happens there.

**Why.** Empty-hangar fill needs the door as a motion gate (entry pose is constrained), but does **not** need the apron geometry that PR #186 introduced. The apron mattered there because the rearrangement case needs somewhere to park a temporarily-pulled-out plane during sequencing — that's the use case being deferred to v2. Skipping the apron rectangle in v1 means: no `hangar.yaml` schema change, no `Hangar.__post_init__` extension, no new bounds-checking against negative-y poses, no risk of `collisions.check` misbehaving on poses outside the current convention. Significant simplification.

This does promote the door from "visual marker only" (the current arc42 §8 contract, [docs/architecture/08-crosscutting-concepts.md](../architecture/08-crosscutting-concepts.md)) to "motion gate" — but only inside the towplanner. The `collisions.check` semantics are untouched: nothing in the layout checker now cares about the door beyond the hangar-bounds rule it already enforces. The arc42 update for v1 should note the towplanner-level promotion explicitly so future readers don't expect `collisions.check` to enforce door constraints on placements.

### Q7. Output representation — what does the user receive?

**Alternatives.** JSON / YAML moves file / **polyline overlay on the existing PNG** / per-move PNG sequence / Markdown move list.

**Recommendation.** **Polyline overlay on the final PNG** as the v1 visualization, backed by an in-memory `MovesPlan` data structure (sketched below) that is rich enough to support per-move PNG sequence and animation as additive v2 features without breaking the data shape. **No YAML serialization format in v1.**

**Why.** Two facts drive this. First, the user's stated v1 expectation is "polyline overlay on final PNG, but prepared or extendable in the future to per move and animated" — so the v1 ship target is the overlay and the data shape must not foreclose the extensions. Second, the YAML moves-file format that PR #186 proposed has only one named consumer outside hangarfit (AR spike #172), and that consumer has not started implementation. Specifying a file format before its sole consumer is ready risks freezing decisions we will want to revise; deferring the format to a joint PR with #172's first implementation is the honest way to share contract authorship between the two spikes.

For the overlay itself, the existing `_draw_conflict_overlay` in `src/hangarfit/visualize.py` already establishes the overlay z-order pattern; a companion `_draw_tow_paths` at the same tier with one colour per plane is a few-dozen-line addition.

### Q8. Where does the algorithm live?

**Alternatives.** **New `src/hangarfit/towplanner.py`** / extension of `solver.py` / external library bundled as an optional dep.

**Recommendation.** **New `src/hangarfit/towplanner.py`** beside `solver.py`. **No new CLI subcommand in v1** — `hangarfit solve` calls the towplanner internally and returns bundled `(Layout, MovesPlan)` candidates. A future `hangarfit plan-moves` subcommand is deferred to a follow-up issue gated on AR spike #172 having a concrete need for the standalone entry point.

**Why.** `solver.py` answers *where*; the towplanner answers *how*. They have different mathematical structure (stochastic discrete search vs. deterministic continuous motion planning), different inputs (`Scenario` vs. target `Layout`), and different testing patterns. Merging them into `solver.py` would be the largest module-discipline violation in the codebase, which (per [arc42 §5](../architecture/05-building-block-view.md)) maps one verb to one module. The cost of separation is one extra file and roughly twenty lines of integration glue in the existing `solve` entry point — negligible.

Deferring the `plan-moves` subcommand keeps the v1 CLI surface unchanged: users continue to call `hangarfit solve` and get a richer output bundle. The standalone subcommand earns its keep only when AR spike #172 (or a debugging workflow that re-runs sequencing on a saved target layout without re-solving) creates a real second caller. Filing it speculatively would be premature.

The devil's-advocate position is YAGNI: if `towplanner.py` has only one v1 caller, why not inline it into `solver.py` and split later? The answer is that RRT-Connect, if it ever lands, is a 300–500-line addition with its own probabilistic-determinism contract; putting it in `solver.py` from day one would force a future refactor at exactly the moment the codebase can least afford one. Module separation now is cheaper than module separation later.

---

## Internal data-model sketch

The towplanner exposes a small dataclass surface. **No YAML serialization in v1** — these live in memory and feed the renderer directly.

```python
@dataclass(frozen=True)
class Pose:
    x_m: float
    y_m: float
    heading_deg: float

@dataclass(frozen=True)
class DubinsArc:
    """Closed-form shortest path between two oriented poses under min turn radius."""
    start: Pose
    end: Pose
    turn_radius_m: float
    # internal segment representation (CSC / CCC type + segment lengths) elided

@dataclass(frozen=True)
class Move:
    plane_id: str
    target_slot: Pose      # final pose, equals the placement in target_layout
    path: DubinsArc        # door-cone entry pose → target_slot

@dataclass(frozen=True)
class MovesPlan:
    target_layout: Layout
    moves: tuple[Move, ...]  # in execution order
```

When a YAML serialization is added (joint with AR spike #172's first implementation), it carries an explicit `schema_version` field — the first YAML in the repo to do so, deliberately, because the AR spike will consume the same format and the two sides need a versioning hook for breaking changes.

---

## Risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Dubins-only + greedy back-first fails on tight packings; order-retry doesn't converge | Medium | Bail with structured failure pointing at the offending plane; user re-runs with adjusted scenario. v2 RRT-Connect resolves it |
| 2 | `turn_radius_m` values are eyeballed placeholders (per `CLAUDE.md` "Open questions / TBD") | Low for spike | The *mechanism* of the kinematic check is correct even if the *numbers* are illustrative; real values slot in unchanged |
| 3 | Phase 2b "wall-pushed" target layouts ([#145](https://github.com/DocGerd/hangarfit/issues/145)) is a soft prereq | Medium | Sequence the milestones: Phase 2b before Phase 3a. A tow plan that targets a centre-clustered layout is technically valid but operationally weak |
| 4 | Cart-as-own-gear (`turn_radius_m = 0`) may produce geometrically nonsense paths for `always_cart` planes | Low | A zero-radius turn is a pivot-in-place — honest for cart-on-cart motion. v2 cart-lift primitive replaces it if needed |
| 5 | Sequence-level cart cap is not enforced (per-layout cap remains) | Low | The existing per-layout cap is already enforced; whether sequences need a tighter cap is open and deferred. ADR-0007 should call it out |
| 6 | Sampling step (0.05 m / 1°) may miss a part-thickness-grazing collision | Low | Step configurable; sweep-volume is the v2 upgrade if this ever bites |
| 7 | No YAML format in v1 means AR spike [#172](https://github.com/DocGerd/hangarfit/issues/172) cannot consume tow-paths until the deferred `plan-moves` issue ships | High **if** #172 starts before that issue | Joint-PR contract: the `plan-moves` issue must ship with or before #172's first implementation PR. File the dependency explicitly when both milestones are sized |
| 8 | Verifier PASS/FAIL verdicts are only as trustworthy as the input data — placeholder `hangar.yaml` + placeholder `turn_radius_m` mean v1 results are **illustrative, not authoritative** | Medium | Inherit the project-wide `CLAUDE.md` "Open questions / TBD" disclaimer; surface it in the `solve` CLI output banner when paths are rendered, until real measurements land |
| 9 | The scope reframing from "rearrangement" (issue body) to "empty fill" (this doc) may surprise readers expecting the original scope | Low | The "Scope reframing" section above names the change and the deferred rearrangement work explicitly; the v2 follow-ups list keeps it visible |

---

## Proposed follow-up implementation issues

To be filed under a new milestone **"Phase 3a — Tow-path planner v1 (empty-hangar fill)"** after this doc is reviewed and the direction is locked. Filing happens *after* review so that any direction change from the review flows into the issue descriptions, mirroring the pattern PR #186 established for spike deliverable (2).

1. **`Pose` / `DubinsArc` / `Move` / `MovesPlan` dataclasses + tests** — pure-data foundation.
2. **Dubins arc primitive** — closed-form shortest path between two oriented poses under a minimum turn radius. ~80 lines. Standalone-tested with the standard CSC / CCC case matrix.
3. **Greedy back-first ordering** — sort target placements by `(y_m desc, x_m asc, plane_id)` with a deterministic tie-break.
4. **Sampled collision-during-motion check** — discretize each Dubins arc at a fixed step; call `collisions.check` per sample against the already-placed subset. Reuses `geometry.aircraft_parts_world` for the moving plane's parts at each sample pose.
5. **Towplanner module + order-retry loop** — wire (3) and (4) into `src/hangarfit/towplanner.py`; on Dubins collision, swap the failing plane with the next-feasible one; bail with structured failure after K retries.
6. **`solve` integration** — `hangarfit solve` returns bundled `(Layout, MovesPlan)` candidates; bundle covers existing RR-MC diversity output (ADR-0004) and alternative orderings per layout.
7. **Polyline overlay in `visualize.py`** — `_draw_tow_paths` companion to `_draw_conflict_overlay` at the same z-tier; one colour per plane.
8. **CLI flags + exit-code semantics** — `--render-paths` opt-in flag; non-zero exit when no feasible order exists for any candidate; structured conflict messages name the offending plane and Dubins-arc segment.
9. **Docs: arc42 §5 + §8 updates** — register the `towplanner` module in §5; in §8, note that `turn_radius_m` is now consumed and the door is a towplanner-level motion gate (still a visual marker only at the `collisions.check` level).
10. **ADR-0007 — Tow-path planner v1 scope** — capture the empty-fill scope, the Dubins-only-with-retry choice, the cart-as-own-gear choice, and the sequence-level-cart-cap open question.

### v2 follow-ups (file later, separate milestone — not in Phase 3a)

- **User overrides** — pin a plane to a specific slot; pin a plane to a specific position in the entry order. Scenario-YAML-level for up-front pins; pick-then-edit for post-hoc tweaks.
- **RRT-Connect fallback** — for tight packings where Dubins-only + order-retry fails. Adds probabilistic determinism contract (must align with ADR-0003's existing determinism contract for solver runs).
- **Sweep-volume collision check** — if and when the sampling step's grazing-miss failure mode actually bites in real authored scenarios.
- **True cart-lift primitive** — `lift_pose` / `place_pose` move with no path between, replacing the cart-as-own-gear approximation. Comes with the sequence-level cart-cap decision from ADR-0007.
- **Rearrangement scenario** — the original use case from #180's issue body. Brings back the `--current-layout` input, the apron rectangle in `hangar.yaml`, and bidirectional move primitives.
- **Per-move PNG sequence + animated GIF** — additive renderer modes on top of the existing `MovesPlan` data model.
- **`plan-moves` CLI subcommand + YAML moves-file schema** — jointly with AR spike #172's first implementation PR; the shared schema lives in `docs/contracts/moves-schema.yaml` so both sides agree by reference, not by goodwill. `schema_version` field on the YAML.

---

## On the optional PoC

Skipped. The v1 work is small (~10 PRs, each small) and built almost entirely from existing primitives (`collisions.check`, `geometry.aircraft_parts_world`, the visualizer overlay pattern from `_draw_conflict_overlay`) plus closed-form Dubins arithmetic. A throwaway PoC would not materially de-risk the implementation milestone because the only genuinely uncertain question — does Dubins-only + greedy back-first + order-retry converge for the realistic target layouts the Phase 2b realism work produces? — is one the PoC would have to wait for Phase 2b to answer anyway. If a specific narrow concern surfaces during this doc's review (e.g. "is the sampling step actually safe at our part sizes?"), that concern is best answered by its own targeted micro-spike, not a general PoC.

---

## References

- Existing pipeline: [`src/hangarfit/solver.py`](../../src/hangarfit/solver.py) (target `Layout`) · [`src/hangarfit/collisions.py`](../../src/hangarfit/collisions.py) `check(layout)` (collision oracle) · [`src/hangarfit/geometry.py`](../../src/hangarfit/geometry.py) `aircraft_parts_world` (world-frame parts for a placed plane) · [`src/hangarfit/visualize.py`](../../src/hangarfit/visualize.py) `_draw_conflict_overlay` (overlay z-order pattern)
- Parts model: [§8 Crosscutting Concepts](../architecture/08-crosscutting-concepts.md#the-parts-model) · [ADR-0001](../adr/0001-aircraft-parts-model.md)
- Coordinate convention: [ADR-0002](../adr/0002-determinant-minus-one-transform.md) — any new geometry caller (e.g. the sampled-pose collision check) must respect the sign-flip trap
- Solver algorithm: [ADR-0003](../adr/0003-rr-mc-solver-algorithm.md) — determinism contract the towplanner's order-retry loop must align with
- Diversity metric: [ADR-0004](../adr/0004-diversity-metric.md) — supplies multiple target layouts to the bundled output
- Door semantics today: [§8 Crosscutting Concepts](../architecture/08-crosscutting-concepts.md) — "The door is a visual marker only"; v1 promotes the door to a towplanner-level motion gate, leaving `collisions.check` semantics untouched
- Sibling spike: [#172 — AR-preview viewer](https://github.com/DocGerd/hangarfit/issues/172) — eventual consumer of the deferred YAML moves-file format
- Soft prerequisite: [#145 — inter-plane gap minimization](https://github.com/DocGerd/hangarfit/issues/145) under [milestone #14 — Phase 2b solver realism](https://github.com/DocGerd/hangarfit/milestone/14)
