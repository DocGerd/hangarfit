# Bound-Aware Tow-Path Planner — Design Spec

> **Status:** Approved design (brainstorming output), 2026-05-25. Pulls forward the
> spike's v2 obstacle/bound-aware path planning ([docs/spikes/tow-path-planning.md](../../spikes/tow-path-planning.md) Q3)
> because the shipped v1 single-Dubins planner cannot tow the real fleet.
> Next step: `superpowers:writing-plans` → implementation plan.

## 1. Problem & motivation

Wiring `plan_fill` into `solver.solve` (#197) exposed that the merged v1 planner
(#189 `plan_dubins`, #191 `path_first_conflict`, #196 `plan_fill`) does **not**
produce tow-able paths for realistic geometry. Two root causes, found at integration:

1. **Front-door protrusion (fixed).** `entry_pose` puts the plane's reference at
   `y = 0`, so a fuselage modelled about its centre has its rear half at `y < 0`
   (the plane straddles the door while being towed in). `path_first_conflict`
   sampled the start pose first and reused the **static** `collisions.check`,
   whose hangar-bounds rule rejects any `y < 0` vertex — so *every* plane failed on
   its first sample. The spike's Q6 ("hard door, no apron") wrongly assumed reusing
   `collisions.check` during motion was safe. **Resolved** via the front-gap
   exemption (see §3).
2. **Wide-wing turning excursions (this spec).** Even with the front gap exempt, the
   *shortest* Dubins arc from the door to a slot parked at a turned heading
   (the solver routinely emits headings like 234°) makes a large turning loop, and
   the fleet's long wings (e.g. Aviat Husky ≈ 10.8 m span) sweep **5–10 m outside the
   side walls** during that loop (measured: husky 5.7 m over, cessna 9.6 m over). This
   is not the spike's anticipated *grazing*-miss; it is gross metre-scale excursion.
   A single shortest-Dubins arc cannot avoid walls/obstacles — robust planning needs
   a search.

With the locked **Decision-3** ("fail the whole solve if any returned layout is
un-towable"), cause (2) makes `solve` return `no_feasible_plan` for essentially every
real scenario. The fix is to make the planner produce **in-bounds, obstacle-free**
paths.

## 2. Locked decisions (resolved with the user 2026-05-25)

Recorded with rejected alternatives so each choice is auditable.

| Decision | Chosen | Rejected & why-not |
|---|---|---|
| **Front-door bounds during motion** | **Front-gap exemption.** The mover may occupy `y < 0` in front of the door (conceptual apron, spike Q6); side walls (`0 ≤ x ≤ width`), back wall (`y ≤ length`), bay, and placed-plane overlap stay enforced. Implemented as a motion-bounds helper in the towplanner; `collisions.check` unchanged. *(Already implemented + tested.)* | *Skip all mover wall-checks* — also loses side/back-wall protection (a turn could clip a wall undetected). *Model an apron / `hangar.yaml` schema change* — contradicts the spike's deliberate "no apron" v1 scope; v2. |
| **Un-towable layout** | **Keep Decision-3 strict** (fail-whole-solve). | *Soften to best-effort plans (layout valid, plan=None)* — keeps solve useful but drops the "every returned layout is tow-able" guarantee; user chose strict + a real planner. |
| **Determinism** | **RNG-free by construction.** Deterministic search; no randomness in the towplanner; ADR-0003 holds trivially. | *Seeded RNG (RRT/RRT-Connect)* — more general but introduces randomized planning + a determinism-via-seed contract into the RNG-free towplanner. |
| **Acceptance bar** | **Robust** — find a path whenever a reasonable one exists; only fail when a plane is genuinely boxed in. | *Pragmatic waypoint-Dubins* (incomplete on hard layouts); *minimal fixture-hack* (brittle). |
| **Algorithm** | **Hybrid-A\*** over Dubins motion primitives. | *Pure state-lattice* — fiddly primitive design, coarser paths, similar payoff. *Waypoint-augmented Dubins* — lighter but not complete. |

## 3. Architecture & algorithm

New deterministic search `plan_path` in `src/hangarfit/towplanner.py`, replacing the
single-shot `plan_dubins(entry, goal)` inside `plan_fill`'s feasibility loop.

- **Signature (proposed):** `plan_path(mover: Aircraft, entry: Pose, goal: Pose, *, hangar: Hangar, placed: Layout, mover_on_carts: bool, <tuning kwargs>) -> DubinsArc` — raises `NoFeasiblePlanError` when no in-bounds path is found within the node budget.
- **State:** continuous pose `(x, y, heading)`. Start = `entry_pose(slot, hangar)`; goal = `Pose.from_placement(slot)`.
- **Motion primitives:** a fixed fan from each pose. Own-gear: `{hard-left, straight, hard-right}` short arcs of length `Δs` at `mover.effective_turn_radius_m()`. Cart (`r = 0`): `{pivot-left Δθ, straight Δs, pivot-right Δθ}` (in-place rotation allowed, ADR-0007). Each primitive is `Segment`(s) integrated via the existing `DubinsArc.pose_at`.
- **State binning (the "hybrid"):** visited poses bin into an `(x, y, θ)` grid (default ~0.5 m / ~15°); each cell keeps its best g-cost, keeping the continuous search finite.
- **Cost `g`** = accumulated path length (+ a small per-radian turn penalty). **Heuristic `h`** = straight-line Euclidean distance to the goal (`math.hypot`). *Implementation note:* this design originally proposed `plan_dubins(pose, goal, r).length_m` (tighter); the shipped planner uses the simpler/cheaper Euclidean distance, which is still admissible — `Euclidean ≤ Dubins length ≤ true cost` and the turn penalty is ≥ 0 — so it may expand a few more nodes but never fewer. See the `plan_path` docstring. RNG-free.
- **Analytic expansion (key Hybrid-A\* trick):** at each popped node, *first* try a direct `plan_dubins(node.pose, goal, r)` shot; if `path_first_conflict` clears it, finish. An unobstructed plane therefore completes in one cheap shot (today's behaviour, preserved); the primitive fan only does hard maneuvering when the direct shot is blocked.
- **Validity oracle:** the front-gap-exempt `path_first_conflict` (already built), used for both primitive edges and the analytic arc.
- **Determinism (ADR-0003):** fixed primitive order `(L, S, R)`; priority queue tie-broken by a monotonic insertion counter (then a deterministic state key). No RNG. Same inputs → identical path.
- **Failure:** open set empty or node-expansion budget exhausted → `NoFeasiblePlanError(plane_id, conflict)` (last blocking conflict as diagnostic).

### Path representation & integration (no data-model change)

A Hybrid-A\* path is multi-segment but needs **no** new path type and **no** change to
`Move`/`MovesPlan`. All primitives and the final analytic arc share one
`turn_radius_m` (the plane's `r`, or `0` for a cart), and `DubinsArc.pose_at`/`sample`
already walk an arbitrary segment list from the start pose. So `plan_path` concatenates
all chosen segments into a single `DubinsArc(start=entry, end=goal, turn_radius_m=r,
segments=(s1…sN))`. `Move.path` stays `DubinsArc`; the #197 bundle and the future #193
CLI rendering are unaffected.

`plan_fill` changes only in how it obtains the arc: `arc = plan_path(...)` (which
searches internally) instead of `plan_dubins(...)` + a single conflict check. Feasible ⇔
`plan_path` returns an arc; blocked ⇔ it raises. `back_first_order`, the deepest-first
scan, and the bail logic are untouched.

## 4. Performance design & failure semantics

Per-edge validity is the cost driver; the unoptimised search (rebuild `Layout` + full
Shapely `collisions.check` per sample, over thousands of expansions) is ~minutes per
plane — unshippable. Mitigations, by leverage:

1. **Analytic expansion carries most cases** — unobstructed planes finish in one Dubins shot; only blocked planes pay for search.
2. **Precompute the obstacle set once per plane-search** — placed planes don't move while routing one plane: compute their world polygons + AABBs once; bay/walls are static. No per-sample `Layout` rebuild.
3. **Fast `_motion_clear(mover, pose, obstacles, hangar)`:** (a) front-gap-exempt bounds via cheap vertex arithmetic; (b) mover-AABB vs obstacle-AABB pre-filter; (c) Shapely intersection only on overlapping AABB pairs, honouring the parts-model layer/clearance rules; (d) rectangle bay test.
4. **Exact-oracle safety net:** after the search returns, validate the full concatenated arc once with the canonical `path_first_conflict` (`collisions.check`). A divergence between the fast checker and the exact oracle surfaces as a failing test, never a silently-shipped invalid path.
5. **Node-expansion budget** per plane → exceeded ⇒ `NoFeasiblePlanError`; bounds worst-case time. `solve`'s `budget_s` still applies on top.

**Risk (called out):** performance is make-or-break, and `_motion_clear` is the
correctness-risk surface (it must mirror `collisions.check`'s single-mover-vs-static
semantics; the exact-oracle net guards it). If even optimised the fixture matrix can't
route within budget, that is the signal to revisit (coarser model, or fall back toward
the waypoint-Dubins approach). The plan **gates on a performance test** so this is found
early.

**Failure semantics (unchanged):** `plan_path` → `NoFeasiblePlanError` → `plan_fill`
propagates → `solve` returns `no_feasible_plan` (Decision-3).

## 5. Testing strategy

- **`plan_path` unit tests:** clear straight shot → one analytic arc; the
  wide-wing/turned-heading case that defeats single-Dubins → finds an in-bounds
  multi-segment path (`path_first_conflict(result) is None`, endpoint ≈ goal); a
  genuinely boxed-in plane → `NoFeasiblePlanError` within budget; **determinism**
  (`plan_path(...) == plan_path(...)`, identical segments); **in-bounds invariant**
  (every sample of the returned arc passes the exact oracle).
- **Determinism canary** pinning a known non-trivial path (cf. the 45° Dubins canary
  #189), so an accidental tie-break/ordering change is caught.
- **Integration / acceptance:** the currently-failing solver fixture matrix returns to
  `found` with bundled, exact-validated plans — the real acceptance test.
- **Performance test** (`slow`-marked): `solve` over the fixture matrix completes within
  budget.
- **Keep** the front-gap-exemption motion tests already added in
  `tests/test_towplanner_motion.py`.

## 6. Scope, sequencing & relationship to in-flight PR B (#197)

This planner is a **prerequisite** for #197 (solve integration) to go green/merge. The
work already done on the `feature/197-solve-bundled-movesplan` branch is consistent with
this design and is **kept**:

- **Task 4** (`SolveResult.plans`, `no_feasible_plan` status, `unplannable_plane`) — correct as-is.
- **Task 5** (solver wires `plan_fill`, fail-whole-solve) — correct; exactly what "keep strict" wants.
- **Front-gap exemption** (`_mover_motion_bounds_conflict` + `path_first_conflict` change) — correct; any planner needs it.

The Hybrid-A\* planner is new tracked work (its own issue/PR off `develop`, in milestone
#16 Phase 3a, pulling forward spike v2 item). `plan_path` lands first; then #197's
remaining Task 6 (CLI) + the PR review arc complete on top, with the fixture matrix green.

## 7. Determinism & ADR alignment

- **ADR-0003 (determinism):** no RNG in the towplanner; deterministic primitive order +
  tie-break ⇒ same inputs → byte-identical path. Preserved end-to-end through `solve`.
- **ADR-0002 (heading convention / determinant-−1 transform):** unchanged — `plan_path`
  consumes the existing `compass_to_math_rad` / `aircraft_parts_world`; it does not
  re-derive the transform.
- **ADR-0007 (cart = own-gear with `r = 0`):** preserved — carts use the pivot/straight
  primitive fan; one motion model, no cart special-case in the planner body.
- **Spike Q3:** this implements the obstacle/bound-aware planner the spike deferred to
  v2, because v1's Dubins-only model proved inadequate for the real fleet.

## 8. Open questions for the implementation plan

- Concrete defaults for `Δs`, grid cell sizes, `Δθ`, and the node-expansion budget
  (tune against the fixture matrix + performance test).
- Exact decomposition of `_motion_clear` vs reusing pieces of `collisions.check` to
  minimise divergence risk while hitting the performance target.
- Whether to add a light segment-merge pass (collapse consecutive same-kind segments) on
  the emitted `DubinsArc` for cleaner output/rendering (cosmetic; optional).
