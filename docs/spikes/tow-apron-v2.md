# Spike: Tow-planner v2 — model a staging apron (start outside, slide in through the door)

- **Status:** Recommendation. Design-only; **no production code ships from this spike.** The implementation stays tracked by [#412](https://github.com/DocGerd/hangarfit/issues/412) and graduates to a release milestone once this doc is reviewed and the direction is locked.
- **Date:** 2026-06-07
- **Spike issue:** [#494](https://github.com/DocGerd/hangarfit/issues/494)
- **Implementation issue:** [#412](https://github.com/DocGerd/hangarfit/issues/412) (tow planner v2 — staging apron)
- **Foundation (merged):** [#411](https://github.com/DocGerd/hangarfit/issues/411) (centre the mover in the door opening + reject jamb-clipping entries — the minimal no-apron correctness fix)
- **Companion ADR (Proposed):** [ADR-0021](../adr/0021-tow-planner-staging-apron.md)

> **Note on "v1 / v2".** As in the [tow-path spike](tow-path-planning.md) and [ADR-0007](../adr/0007-tow-path-planner-v1-scope.md), these are **scope tiers of the towplanner subsystem**, *not* `hangarfit` semver versions. v1 = the shipped empty-hangar-fill planner (ADR-0007, ADR-0010). v2 = the apron work this spike scopes.

---

## Summary recommendation

Model the apron as a **bounded, fixed-depth entry-staging start-region** in the `y < 0` half-plane in front of the door, and route each plane **apron → door → slot** instead of starting its path *on* the door line (`y = 0`).

Concretely:

1. **Bounded depth, full frontage width.** The apron is the rectangle `x ∈ [0, width_m]`, `y ∈ [−apron_depth_m, 0)`. `apron_depth_m` is a new optional `hangar.yaml` scalar (default derived from the fleet's `max(turn_radius_m) + max(plane length)`, so a fresh dataset gets a sane apron with no authoring), overridable by a `--apron-depth` CLI flag. **Bounded, not an unbounded half-plane** — the Hybrid-A\* free-space grid must stay finite and deterministic.
2. **Entry-staging tier, not a holding area.** One plane is staged on the apron at a time; the staging order **is** the existing `back_first_order` tow order. No simultaneous multi-plane apron occupancy, no apron-capacity model, no `--current-layout` — those belong to the heavier **rearrangement** tier, deferred (see *Scope reframing*).
3. **Deterministic apron-pose grid.** Extend `entry_poses`' fixed 3×5 door-cone into the apron with a small fixed set of `y`-offset samples, enumerated in a fixed RNG-free order and seeded into the same multi-start Hybrid-A\* search. No `Date.now`, no RNG — the [ADR-0003](../adr/0003-rr-mc-solver-algorithm.md) planner-half byte-identity contract holds unchanged.
4. **Front wall stays a barrier; the door stays the gate.** The `#411` jamb-clip rejection is retained verbatim; the `#222`/`#411` front-gap exemption generalises from a transient mid-tow dip to *originating and manoeuvring* inside the apron rectangle. `collisions.check` (the static oracle) is **untouched** — the apron is a planner-level motion concept exactly as the door already is.
5. **scene/v1 is unchanged for the slide-in.** The first timeline sample affine simply sits at `ty < 0`; the viewer applies it verbatim and the plane visibly approaches from outside. An optional additive `hangar.apron` field (render-only, to draw apron ground) is a nice-to-have, not required.
6. **The apron unblocks #263 (nose-out) without deciding it.** Reverse-capable motion ([ADR-0010](../adr/0010-reeds-shepp-motion-model.md)) plus apron run-up room make nose-out parking *routable* (back the plane in tail-first); whether to *prefer* it stays [#263](https://github.com/DocGerd/hangarfit/issues/263)'s soft-tiebreak decision. The apron design must not bake in a hard nose-out rule.

This is a small, closed-form, deterministic extension of the existing planner — no new motion math, no solver change, no new probabilistic component. The companion [ADR-0021](../adr/0021-tow-planner-staging-apron.md) (Proposed) records the decision.

---

## Scope reframing vs the issue body

Issue [#412](https://github.com/DocGerd/hangarfit/issues/412) (and [ADR-0007](../adr/0007-tow-path-planner-v1-scope.md)'s deferral) frame the apron as part of the **rearrangement** scope — "pull three planes out and put two back in a different order," which also wants a `--current-layout` input and bidirectional move primitives. This spike **separates two things the original framing bundled**:

- **Entry staging (this spike's recommended first cut).** A `y < 0` start-region from which the *empty-hangar fill* slides each plane in through the door. This is what #412's stated *motivation* actually needs — it makes the entry read as real physical motion and unlocks the viewer "slide-in from outside" animation. It is a strict superset of the shipped fill planner (every plane still enters once, in `back_first_order`), differing only in *where each path begins*.
- **Rearrangement (still deferred).** Simultaneous multi-plane apron occupancy (planes parked on the apron while pulled out), apron-capacity/collision-among-staged-planes, the `--current-layout` input, and pull-out/repark bidirectional sequencing. This is genuinely larger and still has no ground truth.

This mirrors the original [tow-path spike](tow-path-planning.md)'s reduction of rearrangement → fill: ship the smaller, common, one-direction-per-plane case first; keep the harder problem named and deferred so it is not forgotten. The entry-staging apron is the strict prerequisite for the rearrangement apron — a planner that cannot slide a plane in from a start-region certainly cannot juggle several on a holding area.

The rest of this doc designs the **entry-staging** tier and explicitly lists what the rearrangement tier additionally needs.

---

## Recommendations per design question

Each question restates the alternatives, the recommendation, and the reasoning — the recommendation is only credible if you can see what it beat.

### Q1. Apron geometry & extent — how far does `y < 0` go, and is it bounded?

**Alternatives.** (A) Unbounded `y < 0` half-plane / (B) **fixed-depth bounded rectangle** / (C) per-fleet-derived depth with no stored field.

**Recommendation.** **(B) — a bounded rectangle `x ∈ [0, width_m]`, `y ∈ [−apron_depth_m, 0)`,** where `apron_depth_m` is a new optional `hangar.yaml` scalar (default `0` ⇒ today's no-apron behaviour reproduced byte-for-byte when absent; the loader supplies a *derived* default for real datasets, `≈ max_plane_length + max_turn_radius`, so the apron is big enough for one run-up-and-align manoeuvre and no bigger), overridable by `--apron-depth N`.

**Why bounded.** The grid heuristic (`_Obstacles`, the default since [#336](https://github.com/DocGerd/hangarfit/issues/336)) precomputes an obstacle-aware free-space geodesic over a finite grid; an unbounded apron means an unbounded grid, an undefined geodesic, and an unbounded expansion budget — directly hostile to the determinism and bounded-bail-time guarantees the planner ships. A bounded apron keeps the grid finite, the budget accounting (`_MAX_EXPANSIONS` / `_MAX_FILL_EXPANSIONS`) intact, and the search reproducible. The entry manoeuvre only needs about one turning-radius plus one plane-length of run-up, so a derived depth is *enough* without being arbitrary.

**Why a stored scalar (B) over a purely-derived depth (C).** Following the [#210 `max_carts` precedent](../adr/0007-tow-path-planner-v1-scope.md) (site equipment lives on the hangar floor plan, with a CLI override for what-if exploration), the apron is a property of the *site*, not the fleet: one hangar fronts onto a big apron, another onto a tight taxiway. A `hangar.yaml` scalar with a derived default gives both a sane zero-config experience and an authoring knob. A default of `0` (or absent) reproduces today's behaviour exactly — the no-apron model becomes the `apron_depth_m = 0` special case, which is the cleanest possible migration story.

**x-extent.** The apron spans the **full hangar frontage** `x ∈ [0, width_m]` (not just the door interval), so a plane can stage off to the side and enter at an angle. Keeping the apron x-range identical to the hangar x-range means the search grid only extends *south* (in `y`), not sideways — the simplest deterministic grid change. Widening the apron beyond the hangar frontage is a deferred refinement (real aprons are wider, but the start-region role does not need it).

**Coordinate convention reminder ([§8](../architecture/08-crosscutting-concepts.md#the-coordinate-convention), [ADR-0002](../adr/0002-determinant-minus-one-transform.md)).** World origin is the front-left corner; `+x` runs right along the door wall, `+y` runs *deeper into the hangar*, the front wall is at `y = 0`, and the apron is the region **`y < 0`** (in front of the door). `heading_deg` is compass-from-`+y`, clockwise-positive: `heading 0` ⇒ nose toward `+y` (nose-**in**), `heading 180` ⇒ nose toward `−y`/the door (nose-**out**). Every apron pose must be built through the canonical `geometry.aircraft_parts_world` / `local_to_world` map — the apron adds no new geometry derivation and must never re-derive the determinant-(−1) transform.

### Q2. Entry vs exit objective — enter nose-in, park nose-out? (the #263 tension)

**Alternatives.** (A) Goal heading taken verbatim from the layout (status quo) / (B) **goal heading verbatim, but the apron + reverse make any goal heading reachable, with nose-out left to #263's soft preference** / (C) bake a hard nose-out rule into the apron planner.

**Recommendation.** **(B).** The apron planner stays *agnostic* about the final heading: it routes to whatever goal pose the layout specifies (today, `Pose.from_placement` copies `placement.heading_deg` straight through). What the apron *changes* is feasibility — with reverse-capable Reeds–Shepp motion ([ADR-0010](../adr/0010-reeds-shepp-motion-model.md)) and apron run-up room, a plane can **back into a deep slot tail-first**, ending at `heading 180` (nose-out) without driving the wasteful ~180° loop the [#263](https://github.com/DocGerd/hangarfit/issues/263) discussion calls out.

**Why this resolves the #263 *coupling* but not the #263 *decision*.** #263 records nose-out as **soft only** (user decision, 2026-05-26): never override collision validity or feasibility, act only as a tiebreak, be per-plane overridable. It is *blocked-by* #261 (reverse — now shipped via ADR-0010) and #262 (entry-cone — shipped) precisely because "doing nose-out *well* needs manoeuvring room." The apron supplies exactly that room: it removes the last coupling that made nose-out expensive. So the apron is the **enabler** for #263, and #263's remaining open question — *which* soft mechanism (solver heading selection vs a 180° flip post-pass vs planner goal-pose preference) — stays #263's to settle. The apron design's only obligation is to **not foreclose** it: keep the goal heading an upstream input and never reject a layout for being nose-in.

**Entry-cone consequence.** With an apron, the rear-entry headings (near `180°`) that [§8](../architecture/08-crosscutting-concepts.md#the-door-is-a-visual-marker-only) and the [#262 amendment](../adr/0007-tow-path-planner-v1-scope.md) explicitly put *out of scope* ("they belong to the Reeds–Shepp motion issue #261") become geometrically sensible: a plane stages on the apron nose-out and backs in. The apron-pose grid (Q4) **may** therefore add reverse-entry start headings — but as additional *deterministic* seed poses for the search to choose among on cost, never as a forced orientation.

### Q3. Staging → fill-order mapping — how does apron order relate to the tow sequence?

**Alternatives.** (A) **Apron staging order ≡ the existing `back_first_order` tow order; one plane staged at a time** / (B) a separate apron-staging optimiser / (C) simultaneous multi-plane apron occupancy with its own ordering.

**Recommendation.** **(A).** The shipped planner walks `back_first_order` (deepest slot first) and commits the first plane in the not-yet-placed scan whose path is feasible against the already-placed subset ([`plan_fill`](../architecture/05-building-block-view.md)). The apron changes *where each plane's path begins*, not *which plane goes next*. So the apron staging order is simply the existing tow order, and **`MovesPlan`'s shape is unchanged** — each `Move` still carries one `DubinsArc`, whose `start` now sits at `y < 0` instead of `y = 0`.

**Why one-at-a-time.** Staging exactly one plane on the apron per move is the simplification that keeps the whole tier small and deterministic: there is no apron-capacity constraint, no collision *among* staged planes, and no need to reason about an apron that fills up. The moment two planes can occupy the apron simultaneously, you are in the rearrangement tier (you are holding pulled-out planes somewhere while you repark) — deferred. (C) is that tier; (B) is an optimiser with no evidence of need at fleet sizes ≤ 12 when the order is already fixed by depth.

### Q4. Determinism — the search space grows; how does byte-identity survive?

The apron enlarges the search space (paths now *start* at `y < 0`), so this section is the one a determinism-aware reviewer will scrutinise. The [ADR-0003](../adr/0003-rr-mc-solver-algorithm.md) **planner-half contract** — same target `Layout` → byte-identical `MovesPlan` — must hold, `max_restarts`-scoped (the planner is downstream of the seeded solver; the apron touches *only* the planner half).

**The plan, concretely:**

1. **A fixed, RNG-free apron-pose grid.** Generalise [`entry_poses`](../architecture/05-building-block-view.md) (today a deterministic 3 x-samples × 5 headings cone, all at `y = 0`) into a 3 × N_y × 5 grid by adding a small **fixed** set of `y`-offset samples in `[−apron_depth_m, 0]` (e.g. `{0, −apron_depth_m/2, −apron_depth_m}` — a fixed count, scaled to the stored depth). Enumerate in a **fixed order** (x-outer, y-middle, heading-inner), dedup `(x, y, heading)` by exact float equality on the second occurrence, exactly as `entry_poses` dedups today. A straight-in apron-centre pose stays the always-present fallback so the frontier is never empty.
2. **Multi-start seeding unchanged.** All surviving apron poses are seeded into the Hybrid-A\* frontier at `g = 0` simultaneously (the [#262](../adr/0007-tow-path-planner-v1-scope.md) pattern); the search returns the shortest path across the whole grid. The existing **monotonic-counter heap tie-break** keeps ties deterministic regardless of how many starts there are.
3. **Bounded, finite grid.** Because `apron_depth_m` is bounded (Q1), the grid heuristic's free-space cells extend south by a *fixed* number of rows; the geodesic precompute stays finite and reproducible. No floating apron means no undefined grid bound.
4. **No RNG, no clock.** Every new quantity (apron poses, grid bounds, derived default depth) is a pure function of the `Hangar` + `Layout` + fleet. There is no `random`, no `time`/`Date.now`, no wall-clock. The budgets (`_MAX_EXPANSIONS`, `_MAX_FILL_EXPANSIONS`) stay deterministic *counts*; a larger space may warrant re-tuning their values, but the *count* — and therefore the output bytes — is reproducible.
5. **Solver untouched.** The apron is purely planner-side; the RR-MC solver's seeded `SolveResult` and its canaries are unaffected. The `determinism-guard` subagent (runs the solver twice on a fixed seed and diffs) must still pass because the bundled `MovesPlan` is a deterministic function of the layout.

**Compliance hook for the implementation:** the existing planner byte-identity test (the analogue of `scene.py`'s `test_build_scene_is_byte_deterministic` for the planner) extends to assert an apron-started `MovesPlan` is byte-identical on repeat; the 45°-heading canary family ([ADR-0002](../adr/0002-determinant-minus-one-transform.md), [ADR-0010](../adr/0010-reeds-shepp-motion-model.md)) extends to a reverse-into-apron case so a CW/CCW sign flip on the new `y < 0` start poses fails loud rather than passing the symmetric word matrix.

### Q5. Geometry & collision implications — sweeping through the door from outside

**Reuse, don't rebuild.** The path the apron produces is still a `DubinsArc` walked by `path_first_conflict`, which samples poses along the arc and calls the static collision oracle against the already-placed subset (parts / bay / bounds honoured *during* the tow). That machinery is unchanged; the mover simply enters the sampling at a `y < 0` pose.

**The one real change — `_mover_motion_bounds_conflict` becomes apron-aware.** Today that wall check (`towplanner.py`) allows a `y < 0` vertex *only* through the door opening (`door_lo ≤ x ≤ door_hi`), treating everything else at `y < 0` as a clip of the solid front wall / jamb ([#411](https://github.com/DocGerd/hangarfit/issues/411)). With an apron, a `y < 0` vertex that lies **inside the apron rectangle** is open ground, not a wall clip. The rule generalises to:

- `y < 0` **and inside the apron** (`0 ≤ x ≤ width_m`, `y ≥ −apron_depth_m`) → free (open apron). *New.*
- The **front wall at `y = 0`** stays a solid barrier with the door gap: a footprint straddling the wall line beside the door (in the jamb x-ranges `[0, door_lo)` ∪ `(door_hi, width_m]`) is still a conflict. The **#411 jamb-clip rejection is retained verbatim** — it is what stops a wide wing overhanging the jamb, and it is what makes the door a true gate.
- Side walls (`0 ≤ x ≤ width_m`) and back wall (`y ≤ length_m`) are enforced unchanged.

So the generalisation is "the front-gap exemption now covers *originating and manoeuvring* in the apron, not just a transient dip" — a strict widening of the legal `y < 0` region from a door-width strip to the full apron rectangle, with the wall barrier and jamb rejection intact.

**`collisions.check` (static) is untouched.** It still forbids `y < 0` entirely; the apron lives **entirely in the planner**, exactly as the door does ([§8 — "the door is a visual marker only"](../architecture/08-crosscutting-concepts.md#the-door-is-a-visual-marker-only)). The final parked slot is still a fully-in-bounds static placement (`y ≥ 0`), so a layout's validity verdict never depends on the apron.

**Substrate note — ADR-0018.** The honest model of "walkable region = hangar interior ∪ apron rectangle, minus the two jamb keep-outs at the front wall" is naturally a *footprint-⊆-polygon containment* check — exactly the direction [ADR-0018](../adr/0018-non-rectangular-hangar-footprint.md) (Proposed; the L-shaped-hangar notch) takes with a Shapely floor polygon and a list of keep-out rectangles. The apron implementation should reuse that substrate **if ADR-0018 lands first**; otherwise the vertex-test generalisation above is sufficient and ADR-0018 can subsume it later. The two are complementary, not blocking.

**Geometry-invariant-guard.** Any PR touching `_mover_motion_bounds_conflict`, the entry-pose grid, or the sampled motion check must be reviewed by the `geometry-invariant-guard` subagent (standing CLAUDE.md rule) — the apron adds fresh `y < 0` start poses, a new sign surface for the determinant-(−1) trap.

### Q6. Viewer "slide-in" — does scene/v1 change?

**Answer: the `scene/v1` schema is unchanged for the slide-in to work.**

The timeline's `segments[].samples` are per-frame world affines from `DubinsArc.sample()` ([scene-v1-schema](../architecture/scene-v1-schema.md)). If the path now starts at `y < 0`, `samples[0]` is simply an affine with `ty < 0`; the viewer applies it verbatim (the viewer does **no transform math** — [ADR-0017](../adr/0017-3d-viewer-architecture.md)). The viewer state machine already hides a plane while `t < segment.start_s` and then animates from `samples[0]`; today `samples[0]` is at `y = 0` (the plane "appears at the threshold"), and with an apron it is at `y < 0`, so the plane **appears in the apron and visibly slides in** — which is precisely the requested animation, achieved with zero schema change.

The load-time `anchors` / `gear_anchors` self-check is computed at each plane's **final** placement (`y ≥ 0`) and is unaffected.

**Optional, additive, render-only.** To draw apron *ground* (so the plane does not appear to float in front of the hangar), a small additive field — e.g. `hangar.apron: { depth_m }` — could be added to `scene/v1`, mirroring how [#399/#400/#401](../adr/0017-3d-viewer-architecture.md) added render-only fields (`wheels`, `on_carts`, `placeholder`, `readouts`) without breaking the contract. This is a **nice-to-have, not required** — the motion slide-in works without it. If added, it is a one-line additive key the viewer reads to draw a translucent ground plane; the determinism and offline guarantees are unaffected.

### Q7. Build & sequencing — what does the implementation touch?

**Foundation (already merged):** [#411](https://github.com/DocGerd/hangarfit/issues/411) (door-gate + centred mover + jamb-clip rejection) and [ADR-0010](../adr/0010-reeds-shepp-motion-model.md) (reverse-capable motion). The apron builds directly on both — the jamb rejection becomes the apron's front-wall barrier, and reverse motion is what makes apron-staged nose-out cheap.

**The future implementation (tracked by #412) would touch, in dependency order:**

1. **`models.py`** — add an optional `Hangar.apron_depth_m: float` (default `0`/absent ⇒ today's behaviour), with loader validation (`>= 0`) and a derived default for real datasets. Smallest additive schema change; mirrors the `max_carts` shape ([#210](../adr/0007-tow-path-planner-v1-scope.md)).
2. **`towplanner.py`** — (a) `entry_poses` → an apron-aware pose grid (add fixed `y`-offset samples, optionally reverse-entry headings); (b) `_mover_motion_bounds_conflict` → the apron-aware front-wall rule (Q5); (c) `plan_path`/`plan_fill` → extend the grid-heuristic bounds south to `−apron_depth_m`; revisit the expansion budgets for the larger space.
3. **`cli.py`** — a `--apron-depth N` override (mirrors `--max-carts` / `--tow-max-expansions`).
4. **`scene.py` / viewer** — *no change required* for the slide-in; the optional additive `hangar.apron` ground field (Q6) is a separate, render-only follow-up.
5. **Docs sweep** — arc42 [§5](../architecture/05-building-block-view.md) (`towplanner` apron note), [§8](../architecture/08-crosscutting-concepts.md) (the door section: the apron generalises the front-gap exemption), `scene-v1-schema` (only if the optional ground field is added), the ADR index, and `data/hangar.yaml` + `examples/herrenteich/` comments for the new scalar.

**Still deferred (rearrangement tier):** `--current-layout` input, simultaneous multi-plane apron occupancy + apron-capacity/collision-among-staged-planes, and pull-out/repark bidirectional sequencing. Named here so the entry-staging cut does not silently foreclose them.

### Q8. Alternatives at the top level — and the committed recommendation

Three axes, each with a genuine alternative:

| Axis | Options | Recommendation |
|---|---|---|
| **Extent** | unbounded half-plane · **bounded fixed-depth rectangle** · per-fleet-derived (no field) | **Bounded rectangle**, `hangar.yaml` scalar + derived default + CLI override (Q1) |
| **Representation** | implicit `y < 0` start (no model) · **explicit deterministic apron-pose grid** · full holding-area with capacity | **Explicit pose grid** extending `entry_poses` (Q4) |
| **Tier** | **entry-staging start-region** · full rearrangement holding-area | **Entry-staging first**; rearrangement deferred (Scope reframing) |

**Why not the implicit `y < 0` start (no apron model)?** You *could* just move `entry_poses`' `y = 0` to a single fixed `y = −d` and ship the slide-in with almost no other change. Rejected because it gives no authoring knob, no honest collision model for manoeuvring in front of the door (the off-to-the-side run-up that eases angled / nose-out entries), and no clean default-of-zero migration — it would be a magic constant rather than a modelled site property. The explicit-grid + bounded-rectangle design costs little more and is the honest model.

**Why not the unbounded half-plane?** It breaks the finite-grid / bounded-budget determinism story (Q1/Q4) for a capability — manoeuvring arbitrarily far from the door — that the entry case never needs.

**Why not go straight to the rearrangement holding-area?** No ground truth on the data model (multi-plane apron occupancy, capacity, pull-out order), and it is a strict superset of the entry-staging case. Shipping the prerequisite first is the same discipline the original tow-path spike applied (fill before rearrangement).

---

## Risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Larger search space (apron starts) inflates expansion budgets; an un-routable fill gets more expensive to disprove | Medium | `apron_depth_m` is bounded (finite grid); the `_MAX_FILL_EXPANSIONS` global cap still bounds bail time; re-measure against the [profiling harness](solve-tow-profiling.md) and re-tune the budget *value* (the *count* stays deterministic) |
| 2 | The apron-pose grid silently introduces a non-deterministic enumeration | High **if** mishandled | Fixed x/y/heading sample sets, fixed emit order, exact-float dedup, monotonic-counter heap tie-break — all RNG-free (Q4); pinned by the planner byte-identity test |
| 3 | A CW/CCW sign flip on the new `y < 0` start poses passes the symmetric Reeds–Shepp word matrix | Medium | Extend the 45°-heading canary family with a reverse-into-apron case (ADR-0002 trap); `geometry-invariant-guard` review |
| 4 | The apron-aware front rule accidentally weakens the #411 jamb-clip rejection (wide wing overhangs jamb again) | Medium | The jamb rejection at the `y = 0` wall line is retained verbatim; the apron only widens the legal region *below* `y = 0`. A jamb-clip regression test from #411 must stay green |
| 5 | `apron_depth_m` default (derived) is wrong for a real site | Low | It is overridable per-site (`hangar.yaml`) and per-run (`--apron-depth`); default `0` reproduces today exactly |
| 6 | Viewer shows a plane floating in front of the hangar (no apron ground drawn) | Low (cosmetic) | The slide-in works without ground; the optional additive `hangar.apron` field (Q6) draws ground when wanted |
| 7 | Scope confusion: readers expect the full rearrangement apron from #412/ADR-0007 | Low | The *Scope reframing* section names the split; the rearrangement tier's extra needs are listed in Q7 |

---

## ADR plan — new ADR vs amendment to ADR-0007

**Decision: a new [ADR-0021](../adr/0021-tow-planner-staging-apron.md) (Proposed), referencing ADR-0007**, not an inline amendment.

**Why a new ADR.** ADR-0007 is already `Accepted` with its fork-2 superseded by ADR-0010 and carries three dated amendments (#262, #210, #336). The apron is a *new architectural decision* — it introduces a new geometric region, generalises the door motion-gate semantics, and adds a site scalar — substantial enough to earn its own record under the project's "one consequential decision = one ADR; an ADR without rejected options is a description, not a decision" discipline ([ADR-0000](../adr/0000-record-architecture-decisions.md) / [README](../adr/README.md)). The project's precedent is consistent: ADR-0010 recorded the reverse-motion change as a *new* ADR superseding ADR-0007's fork-2 rather than amending it inline, and ADR-0020 revisited ADR-0017 as a *new* ADR. Folding a scope-expanding decision into ADR-0007's already-amended body would bury it. ADR-0021 extends — does not supersede — ADR-0007: the empty-hangar-fill scope, cart-as-own-gear, the door-as-motion-gate, and the greedy ordering all still stand; the apron is an additive start-region on top.

---

## On a PoC

Skipped, for the same reason the original tow-path spike skipped one: the work is small and built entirely from existing primitives (`entry_poses`, `path_first_conflict`, `_mover_motion_bounds_conflict`, the multi-start Hybrid-A\* search, `aircraft_parts_world`) plus a bounded grid extension and a deterministic pose-grid widening — no new motion math, no probabilistic component. The one genuinely uncertain quantity (does the larger search space push the expansion budgets past their perf gate?) is best answered by the existing [profiling harness](solve-tow-profiling.md) during implementation, not a throwaway PoC. A narrow concern surfaced in review is better served by a targeted micro-spike.

---

## References

- Foundational spike: [Tow-path planning (#180)](tow-path-planning.md) — the eight-question exploration, esp. **Q6 (door, no apron)** this spike revisits.
- Profiling harness: [solve→tow profiling spike (#381)](solve-tow-profiling.md) — the substrate for re-measuring the apron's effect on routing time.
- ADRs:
  - [ADR-0007](../adr/0007-tow-path-planner-v1-scope.md) — v1 empty-hangar-fill scope this spike extends (apron was its deferred v2 item).
  - [ADR-0010](../adr/0010-reeds-shepp-motion-model.md) — reverse-capable motion that makes apron-staged nose-out cheap.
  - [ADR-0002](../adr/0002-determinant-minus-one-transform.md) — the coordinate / sign-flip trap every apron pose must respect.
  - [ADR-0003](../adr/0003-rr-mc-solver-algorithm.md) — the determinism contract the apron pose-grid preserves.
  - [ADR-0017](../adr/0017-3d-viewer-architecture.md) + [scene-v1-schema](../architecture/scene-v1-schema.md) — the viewer seam the slide-in flows through unchanged.
  - [ADR-0018](../adr/0018-non-rectangular-hangar-footprint.md) — the floor-polygon / keep-out substrate the apron+jamb model can reuse.
  - Companion decision: [ADR-0021](../adr/0021-tow-planner-staging-apron.md) (Proposed).
- arc42: [§5 — `towplanner.py`](../architecture/05-building-block-view.md) · [§8 — the door & the coordinate convention](../architecture/08-crosscutting-concepts.md).
- Issues: [#412](https://github.com/DocGerd/hangarfit/issues/412) (implementation) · [#494](https://github.com/DocGerd/hangarfit/issues/494) (this spike) · [#411](https://github.com/DocGerd/hangarfit/issues/411) (merged foundation) · [#263](https://github.com/DocGerd/hangarfit/issues/263) (nose-out, unblocked by the apron) · [#262](https://github.com/DocGerd/hangarfit/issues/262) (entry-cone) · [#222](https://github.com/DocGerd/hangarfit/issues/222) (front-gap exemption) · [#336](https://github.com/DocGerd/hangarfit/issues/336) (grid heuristic + fill cap).
