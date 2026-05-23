# Spike: Tow-path planning — collision-free push sequence from current layout to solver target

- **Status:** Recommendation. No production code yet; follow-up implementation issues filed.
- **Date:** 2026-05-23
- **Spike issue:** [#180](https://github.com/DocGerd/hangarfit/issues/180)
- **Sibling spike:** [#172](https://github.com/DocGerd/hangarfit/issues/172) (AR-preview viewer; consumes this spike's moves-file)
- **Soft prerequisite:** [#145](https://github.com/DocGerd/hangarfit/issues/145) / [milestone #14](https://github.com/DocGerd/hangarfit/milestone/14) (Phase 2b solver realism — wall-pushed target layouts)

---

## Summary recommendation

Build a separate two-stage tool: `hangarfit solve` produces a target `Layout`, then a new `hangarfit plan-moves` subcommand takes a *current* layout + the *target* layout + a **user-supplied move order** and produces a machine-readable moves file plus a polyline-overlay PNG. The tool's job in v1 is **verification, not planning** — given an authored sequence, certify each move is kinematically feasible (consumes the so-far-unused `turn_radius_m`) and collision-free at sampled poses along the path. Sequencing assistance (greedy peel-back), full motion planning (Dubins, then RRT-Connect), and cart-mode handling are explicit v2 work.

This v1 bar makes the spike a small Phase 3a milestone (~6 PRs). The v2 work, if pursued, is a multi-month Phase 3b/c effort that should be sized honestly when the v1 ships and we know what real users actually authored.

> **Note on "v1 / v2 / v3" usage in this doc.** These labels refer to **scope tiers of the towplanner subsystem** — *not* `hangarfit` semver versions. Map: v1 = Phase 3a; v2 = Phase 3b; v3 = Phase 3c. The first towplanner code will ship in whatever `hangarfit` semver is current when Phase 3a starts.

---

## Recommendations per question

Each section restates the alternatives from the spike issue, the recommendation, and the reasoning. The alternatives matter: the recommendation is only credible if you can see what it beat.

### Q1. Initial-state input — where do the planes start?

**Alternatives.** Empty hangar (all start on apron) / user-supplied current-layout YAML / inferred from an outdoor annotation on `fleet_in`.

**Recommendation.** **User-supplied current-layout YAML** via a new `--current-layout <path>` argument.

**Why.** The operational scenario is "an unexpected aircraft is arriving and we need to rearrange what is *already inside*" — empty-hangar throws that away. The current-layout YAML is symmetric with the existing `Layout` data model, so the loader and validators are reusable; the empty case is expressible as a layout with no placements; the "outdoor annotation" hybrid adds stateful inference (last-known layout) that we have no use for yet. Pick the most flexible primitive; add inference later only if usage data demands it.

### Q2. Sequencing — what algorithm picks the move order?

**Alternatives.** Greedy peel-back / backwards-from-goal (warehouse) / A* over partial layouts / classical planner (PDDL) / **user-supplied order**.

**Recommendation.** **User-supplied move order** for v1. Greedy peel-back as the v2 "suggest an order" assist. A* and PDDL deferred indefinitely.

**Why.** The planning problem and the verification problem are independent — and verification is both harder to get right and more immediately valuable than sequencing. A flying-club operator already knows a plausible push order; what they cannot eyeball is whether that order has any mid-move collisions or any segment that violates a plane's turn radius. Build the verifier first; let the user supply the order; observe what orders they actually author. If those orders cluster into recognisable patterns, codify them as the greedy assist in v2. A* is exponential in fleet size (fine at our scale ≤12, but premature); PDDL is overkill and would force an external dependency. Backwards-from-goal needs an apron model anyway — which Q6 commits us to — but the *ordering* discipline is a v2 follow-up, not a v1 prerequisite.

### Q3. Per-plane motion model — what does "moving" mean geometrically?

**Alternatives.** Straight-line teleport / Bezier spline / **Dubins paths** / RRT-Connect with Dubins primitives / **hand-authored waypoints**.

**Recommendation.** **Hand-authored polyline waypoints** in v1, with each segment kinematically checked against `turn_radius_m` (segment-to-segment heading change ≤ feasible for the radius). **Dubins primitive** as the natural v2 upgrade for shortest-path between two oriented poses.

**Why.** Symmetry with Q2: in v1 the tool verifies, the human authors. Hand-authored waypoints are tedious for ≥10-plane fleets — but at our scale, and on hangar geometry pilots already know, they are feasible and they keep the v1 implementation tiny. Dubins is the textbook nonholonomic primitive and naturally consumes `turn_radius_m`; once the verifier and the moves-file format exist, swapping waypoint segments for Dubins arcs is an additive change. RRT-Connect is the v3 step that handles obstacle-aware *planning* — only worth the ~200-400 lines once Dubins is in and users are actually authoring around obstacles. Straight-line teleport is collision-meaningless; Bezier offers no kinematic guarantee and would still need a turn-radius check it cannot honestly satisfy.

### Q4. Collision-during-motion — how is it checked?

**Alternatives.** **Path sampling** / sweep volume / continuous-collision detection (CCD).

**Recommendation.** **Path sampling** at a fixed step (start with 0.05 m of translation or 1° of heading change, whichever comes first; revisit once we see real authored paths). Each sample runs the existing `collisions.check()` from `src/hangarfit/collisions.py`.

**Why.** The collision oracle (`collisions.check`) already exists and handles every existing rule (parts overlap, hangar bounds, bay intrusion). Sampling reuses it verbatim — the verifier becomes "iterate samples, call check, accumulate any conflict". This is the smallest delta to the codebase. The robotics-orthodox concern with sampling is missed in-between collisions, but tow speeds are slow, the part rectangles are large relative to a 5 cm step, and the cost of sampling more densely is linear.

Sweep volume — computing the union of part positions along each segment as one polygon and intersecting against stationary parts — is the credible alternative and deserves to be named, not dismissed. For constant-heading segments (which is what hand-authored polyline waypoints reduce to between waypoints), the sweep is a parallelogram-shaped band and is genuinely cheap to compute. The reason to defer it to v2 is **integration cost, not algorithmic cost**: sweep volume needs a new collision primitive (no Layout-shaped input to feed `collisions.check`), whereas sampling reuses the existing oracle verbatim. Once the verifier and the moves-file format exist, swapping in sweep volume per-segment is an additive change. CCD is mathematically exact but a heavy bring-up that we have no evidence of needing.

### Q5. Cart-mode handling — does cart change anything?

**Alternatives.** **Ignore cart-mode in v1** / two-mode motion with sequence-level cart cap / cart-specific lift-walk-place.

**Recommendation.** **Ignore cart-mode in v1.** All moves are own-gear. File the cart-mode follow-up as a v2 blocker for `always_cart` planes.

**Why.** Cart-mode is a multi-axis change — it interacts with the turn-radius check (carts don't have one), with the cart cap (one cart-eligible plane on the cart at a time, which becomes a *sequence* constraint not a *layout* constraint), and possibly with a new "lift" primitive. None of that helps the v1 verifier prove out. For the fleet today, the three `always_cart` planes (`scheibe_falke`, `wild_thing`, `zlin_savage` — see [`data/fleet.yaml`](../../data/fleet.yaml)) are gated on cart-mode for placement; if a user authors a sequence that contains them as own-gear, v1 will reject with a turn-radius failure.

That failure is honest but *ambiguous*: it conflates "your sequence is infeasible at any radius" with "your sequence needs cart-mode to be feasible". The v1 verifier cannot distinguish the two, and the user must know the fleet's `movement_mode` to interpret the error. The v2 cart-mode follow-up resolves this by surfacing the second case explicitly.

### Q6. Door constraint — how does the door figure into motion?

**Alternatives.** Soft door / hard door / **hard door + outside-the-hangar apron holding area**.

**Recommendation.** **Hard door + apron region.** The apron is modelled as a single rectangle in front of the door, declared in `hangar.yaml` alongside the existing door geometry. Any move whose path crosses the front boundary must do so inside the door interval; planes parked on the apron are tracked positionally so their footprint is collision-checked against other apron-parked planes and against the door itself.

**Why.** Today the door is a render-only marker — arc42 §8 ["The door is a visual marker only"](../architecture/08-crosscutting-concepts.md#the-door-is-a-visual-marker-only) is explicit that the collision checker only enforces the hangar rectangle and that "whether a plane can be *moved* out through the door is a future-planner concern, deliberately out of scope here." This spike is that future planner. Making the door a hard motion gate is therefore a **new** constraint, not a tightening of an existing one — but it is the right new constraint: warehouse-style sequencing (and even v1 hand-authored sequencing where the user wants to swap two deep-stack planes) needs *some* way to express "plane is temporarily outside", which requires both a holding area and a constrained re-entry path. Without an apron, "pull plane A out, then pull plane B out, then push B back deep, then push A back front" cannot be expressed at all.

The apron rectangle is also a **coordinate-frame extension**, not just a new region. Today the convention puts `(0,0)` at the hangar's front-left corner with `+y` deeper into the hangar (arc42 §8 ["The coordinate convention"](../architecture/08-crosscutting-concepts.md#the-coordinate-convention)). The apron sits at *negative* y — outside the rectangle the existing bounds-check enforces. Two consequences: (a) bounds-checking an apron region must be added to `Hangar.__post_init__` next to the existing door + bay checks (see `src/hangarfit/models.py` `Hangar` class); (b) `collisions.check`'s behaviour on negative-y poses has not been exercised — a deliberate test pass before the apron region is trusted is part of the Phase 3a scope, not a freebie.

(Whether to extend `collisions.check` to know about the apron or write an `_apron_conflicts` companion is a v1 implementation detail, not a contract one.)

### Q7. Output representation — what does the user receive?

**Alternatives.** **JSON / YAML moves file** / polyline overlay on the existing PNG / per-move PNG sequence / Markdown move list.

**Recommendation.** **YAML moves file** as the primary contract, **plus a polyline overlay** as a new mode of the existing `visualize.py` renderer. The per-move PNG sequence and the Markdown narration are deferred indefinitely.

**Why.** Two consumers exist. **AR spike #172** needs a machine-readable moves file — it is the entire reason this spike defines a contract at all. **Existing CLI users** want a single static image they can print, like they already get from `hangarfit check`. YAML is consistent with `fleet.yaml` / `hangar.yaml` / layouts; JSON wins nothing here. The polyline overlay is small: `visualize.py` already has the overlay z-order pattern from `_draw_conflict_overlay` (see [`src/hangarfit/visualize.py`](../../src/hangarfit/visualize.py)) — a `_draw_tow_paths` companion at the same z-tier with one colour per plane is a few-dozen-line addition. Per-move PNG sequences and Markdown narration would be nice-to-have if users ever ask, but neither is on any consumer's critical path.

### Q8. Where does the algorithm live?

**Alternatives.** **New `src/hangarfit/towplanner.py`** + new CLI subcommand / extension of `solver.py` / external library bundled as an optional dep.

**Recommendation.** **New `src/hangarfit/towplanner.py`** beside `solver.py`. New CLI subcommand `hangarfit plan-moves`. No external dependency in v1.

**Why.** `solver.py` answers *where*; the towplanner answers *how*. They have different mathematical structure (search over discrete layouts vs. verification over continuous paths) and different inputs (Scenario vs. two Layouts + a sequence). Two-stage means each stage can be re-implemented or re-tuned independently. An external library only earns its keep when the chosen algorithm needs it; v1's verifier needs only stdlib + the existing `collisions.py` / `geometry.py` primitives. If v2 commits to Dubins arcs, that is a 50–100-line addition we should write ourselves to keep the dependency footprint minimal; only if v3 commits to a full RRT-Connect should we re-evaluate bundling an OMPL-class library.

---

## Moves-file contract sketch

The v1 moves file is YAML; one document per planning run. Strawman schema:

```yaml
# moves.yaml
schema_version: 1                       # bumped on breaking contract change. First YAML in
                                         # this repo to carry a version field — deliberate,
                                         # because the AR spike (#172) will consume it too.
current_layout: layouts/now.yaml         # path or inline
target_layout:  layouts/solver-out.yaml  # path or inline
apron:                                   # optional; if omitted, hangar.yaml provides it.
                                         # Coordinate frame extension: see Q6 — apron sits at
                                         # negative y, outside today's hangar-rectangle frame.
  x_m: 0.0                               # apron rectangle is in world coords
  y_m: -8.0                              # negative-y = in front of the door
  width_m: 25.0
  depth_m: 6.0
moves:
  - plane_id: D-EAAB
    primitive: own-gear                  # v1: only own-gear. v2 adds: dubins | cart-lift
    from:                                # full pose: x, y, heading_deg
      x_m: 12.5
      y_m: 4.0
      heading_deg: 90.0
    to:
      x_m: 8.0
      y_m: -3.0                          # parked on apron during sequencing
      heading_deg: 90.0
    waypoints:                           # ordered intermediate poses
      - { x_m: 12.5, y_m: 0.0, heading_deg: 90.0 }
      - { x_m:  8.0, y_m: -3.0, heading_deg: 90.0 }
  - plane_id: D-EAAC
    primitive: own-gear
    from: { ... }
    to:   { ... }
    waypoints: [ ... ]
```

The contract guarantees:

- `current_layout` is collision-clean *as a Layout* (i.e. `collisions.check(current)` returns no conflicts).
- `target_layout` is collision-clean (this is already the solver's invariant).
- After applying `moves` in order, the resulting Layout equals `target_layout`.
- For each move: every sampled pose along the path is collision-clean against the rest-of-fleet's then-current positions, and every consecutive waypoint pair satisfies the plane's `turn_radius_m`.

These four guarantees are the v1 *verifier*'s entire job. They factor cleanly: each is implementable as one ~50-line function reusing existing primitives.

---

## Risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Hand-authored waypoints tedious for ≥10-plane fleets | Medium | v2 greedy peel-back assist; v1 ships before this bites |
| 2 | Real hangar / fleet measurements still TBD (`measured: false` in `fleet.yaml`) | Low for spike | v1 verifier is honest about its inputs; real numbers slot in unchanged |
| 3 | `turn_radius_m` values are eyeballed placeholders | Low for spike | Same as #2; the *mechanism* of the kinematic check is correct even if the *numbers* are illustrative |
| 4 | Phase 2b "wall-pushed" target layouts ([#145](https://github.com/DocGerd/hangarfit/issues/145)) is a soft prereq | Medium | Sequence the milestones: Phase 2b before Phase 3a. A tow plan that targets a centre-clustered layout is technically valid but operationally weak |
| 5 | Cart-mode deferral produces infeasible sequences for `always_cart` planes | Low | Honest failure mode (turn-radius rejection); v2 follow-up issue explicit |
| 6 | Moves-file contract is also the AR spike's input ([#172](https://github.com/DocGerd/hangarfit/issues/172)) | High | The durable mitigation is a **shared schema file** (e.g. `docs/contracts/moves-schema.yaml`) referenced by both implementations — not goodwill alignment in PR-1. Bump `schema_version` if either side drives a breaking change. File the shared-schema PR before either implementation milestone starts |
| 7 | Sampling step (0.05 m / 1°) may miss a part-thickness-grazing collision | Low | Step size configurable; raise on report. Sweep-volume is the v2 upgrade if this ever bites |
| 8 | Apron rectangle is an abstraction over the real airfield (apron is bounded by taxiways, trees, parked cars) | Low for v1 | Document the assumption in `hangar.yaml`; user is responsible for picking a usable rectangle |
| 9 | Verifier PASS/FAIL verdicts are only as trustworthy as the input data — placeholder `hangar.yaml` + placeholder `turn_radius_m` mean v1 results are **illustrative, not authoritative** | Medium | Inherit the project-wide `CLAUDE.md` "Open questions / TBD" disclaimer; surface it in the `plan-moves` CLI output banner until real measurements land |

---

## Proposed follow-up implementation issues

To be filed under a new milestone **"Phase 3a — Motion planning verifier"** (target ~v0.9.0 or later, after Phase 2b). Each is one PR's worth of work; issues 7, 9, 10 are deliberately small.

1. **Apron region in `hangar.yaml`** — add `apron:` block to `Hangar` model + loader + tests
2. **Moves-file model + loader** — `Move`, `MovesFile` dataclasses; YAML loader; round-trip tests
3. **Towplanner module + CLI subcommand** — `src/hangarfit/towplanner.py` skeleton + `hangarfit plan-moves` wiring
4. **Kinematic verifier** — per-segment turn-radius check; standalone tested
5. **Collision-during-motion verifier** — sampled-pose `collisions.check` per move; rest-of-fleet positions tracked through the sequence
6. **End-state verifier** — assert post-sequence Layout equals `target_layout`
7. **Polyline overlay in `visualize.py`** — `_draw_tow_paths` companion to `_draw_conflict_overlay`
8. **CLI exit-code semantics + error reporting** — wire verifier results to non-zero exits; structured conflict messages
9. **Docs: arc42 §3 / §5 / §8 updates** — register towplanner module + the moves-file contract + the apron region
10. **ADR-0007: Tow-path verifier — verification-only v1** — capture the v1-vs-v2 split as a decision

Each issue will reference this spike doc in its body.

### v2 follow-ups (file later, separate milestone)

- Greedy peel-back order suggester
- Dubins primitive support
- Cart-mode (cart-lift primitive + sequence-level cart cap)
- Sweep-volume collision check (if/when sampling proves inadequate)
- AR spike #172 contract alignment PR (joint with #172's first implementation PR)

---

## On the optional PoC

Skipped. The v1 work is small enough (~6 PRs) and well-enough scoped from existing primitives (`collisions.check`, `geometry.aircraft_parts_world`, the visualizer overlay pattern) that a throwaway PoC would not materially de-risk the implementation milestone. The risk register is honest about what we do not yet know; none of those unknowns is the kind a one-off PoC would surface.

If the spike-review discussion surfaces a specific concern — e.g. "is the sampling step actually safe at our part sizes?" — that concern is best answered by its own targeted micro-spike, not a general PoC.

---

## References

- Existing pipeline: [`src/hangarfit/solver.py`](../../src/hangarfit/solver.py) (target `Layout`) · [`src/hangarfit/collisions.py`](../../src/hangarfit/collisions.py) `check(layout)` (collision oracle — also aliased as `check_layout` inside `solver.py`) · [`src/hangarfit/visualize.py`](../../src/hangarfit/visualize.py) `_draw_conflict_overlay` (overlay z-order pattern at `visualize.py:335`)
- Parts model: [§8 Crosscutting Concepts](../architecture/08-crosscutting-concepts.md#the-parts-model) · [ADR-0001](../adr/0001-aircraft-parts-model.md)
- Coordinate convention: [ADR-0002](../adr/0002-determinant-minus-one-transform.md)
- Solver algorithm: [ADR-0003](../adr/0003-rr-mc-solver-algorithm.md)
- Sibling spike: [#172 — AR-preview](https://github.com/DocGerd/hangarfit/issues/172)
- Soft prerequisite: [#145 — inter-plane gap minimization](https://github.com/DocGerd/hangarfit/issues/145) under [milestone #14 — Phase 2b solver realism](https://github.com/DocGerd/hangarfit/milestone/14)
