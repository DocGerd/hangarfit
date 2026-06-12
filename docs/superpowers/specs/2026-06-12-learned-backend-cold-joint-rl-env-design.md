# Learned backend — Cold-joint end-to-end RL: Environment + Reward

**Status:** Draft (design under review)
**Date:** 2026-06-12
**Scope:** Sub-project #1 of the learned-backend epic — the RL **environment + reward** only.
**Supersedes (in part):** the learning approach in [`2026-06-11-learned-backend-and-ground-objects-design.md`](2026-06-11-learned-backend-and-ground-objects-design.md) and the proposer/BC framing in epic **#607**. The ground-object taxonomy and the deterministic verifier from that document are **kept**; the behavior-cloning teacher-nester and the "propose-pose-then-deterministically-route" split are **replaced** by the cold-joint RL formulation below.

---

## 1. Context & why this design exists

`hangarfit`'s deterministic RR-MC solver cannot find dense oblique z-nested layouts, and the Stage B probe (2026-06-12) confirmed the gap is real and two-sided:

- A dense **valid** all-8 Herrenteich layout exists and is checked in (`examples/herrenteich/layout.yaml`, passes `collisions.check`), and the deterministic solver **cannot reproduce it** (it was found by a search driving the checker directly, not by `hangarfit solve`). → placement is exactly where the deterministic search fails.
- That same valid layout is **not tow-routable** on `develop` (it dies on the broadside 18 m Scheibe), because the cart motion model cannot strafe. → routability is gated on a **motion primitive**, not on geometry (every body in the *current Herrenteich fleet* has a minimum doorway slice ≤ the 13.46 m door).

The prior epic (#607) proposed a learned **proposer** that emits poses, warm-started by **behavior-cloning a slow "teacher nester,"** with the deterministic planner routing afterward. The Stage B finding made the teacher-nester the blocker (no routable all-8 teacher target exists yet), and the maintainer has chosen a different, **reward-driven** direction.

**This design pursues that direction: a single agent that learns to place *and* tow every object end-to-end, from reward alone, with no teacher and no deterministic search in the loop.** The deterministic code is retained **only as geometry** — the reward oracle and the final safety gate.

## 2. Goals & non-goals

**Goals**
- Define an RL **environment** (`gym`-style) and a **reward** function for a cold-joint agent that, autoregressively, drives each object in from the apron and parks it.
- Reuse the existing **geometry** (`collisions.check` graded penetration, swept-path clearance, bounds/notch/keep-out, the ADR-0010 movement primitives incl. lateral strafe) as the reward oracle.
- Keep the formulation **parameterizable for a curriculum** (object count, hangar shape, clearance) and for **generalization across hangars + fleets**.
- Preserve the project's safety invariant: **no invalid layout is ever returned to a human** — the deterministic checker is the final gate regardless of what the policy proposes.

**Non-goals (deferred to sibling sub-projects)**
- The **network architecture** and the **observation tensorization** (hangar raster + object-set encoding) → sub-project #2.
- The **policy/training algorithm**, hyper-parameters, and the **curriculum schedule** → sub-project #3/#4.
- **ONNX export, `solve --backend learned`, determinism contract for inference, packaging** → sub-project #5.
- Any claim that this *beats* the deterministic solver on easy instances. The aim is **reach** (valid + routable in regimes RR-MC misses), per #607's "reach, not beat."

## 3. The epic at a glance (this is sub-project #1 of 5)

1. **Environment + reward** ← *this document*
2. Observation encoding (variable hangar polygon + variable object set → tensors)
3. Policy architecture + curriculum
4. Training + evaluation harness
5. ONNX export + `--backend learned` + determinism/packaging

Each gets its own spec → plan → implementation cycle. Nothing here commits the later four.

## 4. MDP formulation

**Decision:** autoregressive, **one object at a time**; within an object's turn the agent **drives it in from the apron** primitive-by-primitive (routability by construction). Approved in design dialogue 2026-06-12.

### 4.1 Episode structure
- The episode is over a **requested set** of objects (aircraft + ground objects) and a **hangar**.
- Objects are placed **one at a time**. The active object **spawns on the staging apron** (`y < 0`, depth = `hangar.apron_depth_m`, ADR-0021) so the agent has room to orient it before threading the door.
- Each timestep the agent applies one **movement primitive** to the active object (or `park`). When `park` is taken, the object's pose is **frozen** and the next object spawns on the apron.
- The episode ends when the requested set is exhausted, an object is **unplaceable** (see termination), or a global step budget is hit.

### 4.2 Observation (semantic — tensorization is sub-project #2)
What the agent can see at each step:
- **Hangar geometry:** outer footprint (incl. L-shape notch), walls, door span/position, maintenance bay, fixed keep-outs (e.g. the fuel trailer), apron depth.
- **Parked objects:** each already-frozen object's footprint + pose (immovable obstacles).
- **Active object:** its parts/footprint, movement mode + legal primitive set, current pose on the apron / in transit.
- **Unplaced set:** the remaining objects (variable-length), with any per-object constraints (requested door-order index, region preference, hard-door flag for the Caddy).

### 4.3 Action space
- The action is a **movement primitive on the active object**, drawn from that object's **legal primitive set by movement mode** (ADR-0010):
  - **cart** (`turn_radius_m == 0`): pivot-L, pivot-R, straight-fwd, straight-**reverse** (on `develop` reverse is `gear=-1` on an `L/S/R` `Segment`, not a distinct kind — `SegmentKind = Literal["L","S","R"]`), plus **strafe** — the lateral primitive that does **not yet exist** (the #599 capability, pending the clean ADR-0010 amendment of §6.1; *required* for broadside bodies or broadside entry is inexpressible).
  - **steerable / own-gear** (`turn_radius_m > 0`): Reeds–Shepp arcs (fwd/rev × left/straight/right).
  - plus **`park`** (commit + advance).
- Each motion primitive carries a **magnitude** (arc length / pivot angle / strafe distance). Continuous vs discretized magnitude is a **sub-project #2/#3** decision; the env exposes both a continuous and a binned interface so the policy spec can choose.
- The env **integrates** the primitive with the existing `pose_at` / `DubinsArc.sample()` machinery, so the resulting motion is identical to what the renderers/towplanner already consume.

### 4.4 Transition & legality
- Applying a primitive sweeps the active object's footprint; the env computes the **swept-path clearance** against walls, parked objects, notch, and keep-outs (reusing the towplanner's motion-clear geometry, *not* its search).
- A primitive that would leave the hangar footprint or penetrate an obstacle is **not forbidden outright** — it is allowed but **graded-penalized** (Section 5), so the agent feels a gradient rather than a wall. (A hard mask option is left to sub-project #3 if penalty-only proves too permissive.)
- Routability is **by construction**: a parked object reached the apron→door→slot via legal collision-free moves, so the hard routability gate is satisfied without a separate planner call.

### 4.5 Termination
- **Success:** every requested object parked valid + in-bounds.
- **Partial stop:** the active object cannot be parked validly within its per-object step budget → episode ends with the **fraction placed** as the terminal signal (the real exception-tool objective: best partial when the full set will not fit).
- **Budget:** a global per-episode primitive cap (deterministic, like the towplanner's `max_total_expansions`) bounds runaway episodes.

## 5. Reward — graded-lexicographic + potential-based shaping

Hard constraints **dominate** (large, **graded** so there is a gradient toward feasibility); soft preferences are **tie-breakers** that only matter once the hard terms are ≈ 0; movement cost keeps tows efficient; a terminal term encodes the real objective.

| Tier | Term | Shape | Reuses |
|---|---|---|---|
| **Hard (large, graded)** | collision overlap (active vs parked + walls) | `−w_col · Σ overlap_area` (graded m², not binary) | `total_penetration_m2` — pairwise overlap **area** (the solver's secondary tie-breaker) |
| **Hard (large, graded)** | out-of-bounds / notch / fixed-keep-out intrusion | `−w_oob · intrusion_area` (graded magnitude **to be added** — see note) | bounds/notch *geometry* (`_hangar_bounds_conflicts`, `structural_notches`); the checks themselves are **binary** today |
| **Hard (large)** | Caddy hard-door egress violation | large negative if the door object isn't nearest-door with a clear egress lane | the #603 / ADR-0026 egress oracle |
| **Movement** | per-primitive cost; per-cusp (direction-reversal) penalty | small negative → efficient, forward-preferring tows | mirrors `CUSP_PENALTY` (per-cusp, #480); note there is **no** per-metre reverse tax |
| **Soft (small)** | inter-object gap / spread | `+w_gap · min_pairwise_gap` | ADR-0008 spread |
| **Soft (small)** | requested door-order deviation | `+w_seq · (−deviation)` when a sequence is specified | #614 door-order (**planned** — open, unbuilt) |
| **Soft (small)** | region preference (e.g. trailers right) | `+w_region · region_match` | #604 RegionPreference |
| **Terminal** | **fraction of the set parked** (+ all-placed bonus, + optional density) | `+R · placed/total` | — |

**On "Reuses" (important — the table reuses *geometry*, not off-the-shelf graded penalties).** Today only the **pairwise collision area** (`total_penetration_m2`) exists as a graded signal, and even there the RR-MC objective is the *lexicographic tuple* `(conflict_count, total_penetration_m2)` (`solver._score`) — an integer conflict count first, area only as the tie-breaker, **not** a single graded penetration the solver "descends on." The **out-of-bounds / notch** checks are **binary** in the codebase (they emit a first-violating-vertex `Conflict`, no magnitude); this design **adds** their graded `intrusion_area`, computed from the same Shapely polygons. So each "Reuses" cell means *reuses the geometry to compute the term*, not "an existing penalty is wired up." Units are **area (m²)** throughout (overlap area / intrusion area), not a penetration *depth*.

**Potential-based shaping (the trainability lever).** Add `γ·Φ(s′) − Φ(s)` to every step, with a potential such as
`Φ(s) = −(remaining_overlap) − (distance_of_active_object_to_a_valid_parking_region) − (unplaced_count)`.
Per Ng–Harada–Russell this shaping is **policy-invariant** (it cannot change the optimal policy, so it cannot be reward-hacked) yet supplies a **dense gradient on every primitive** instead of a single sparse "valid!" at episode end — which is what makes a from-scratch agent able to learn the door-threading maneuver at all.

**Weights** (`w_col`, `w_oob`, `w_gap`, …) are tuned in sub-project #4; the **ordering invariant** is fixed here: any hard violation must outweigh any achievable soft bonus, so a "beautifully spread but overlapping" layout always scores below a tight valid one (the graded-lexicographic property).

## 6. The geometry oracle (what is reused, what is not)
- **Reused (geometry only):** `collisions.check` graded penetration (ADR-0001 parts model, ADR-0002 transform), bounds/notch/keep-out tests, swept-path clearance, the ADR-0010 movement primitives + `pose_at`/`sample`, the apron model (ADR-0021), the Caddy egress oracle (ADR-0026).
- **NOT reused (the point of the re-architecture):** the RR-MC placement **search** (`solver.py`) and the Hybrid-A\* tow **search** (`towplanner.plan_fill`). The agent *is* the search now.
- This keeps the design honestly "agnostic from the algorithm": the deterministic *rules of physics* stay; the deterministic *search* is gone.

### 6.1 Hard dependency: the strafe motion primitive
The action space **requires the lateral-strafe `T` primitive** (and the free-swivel pivot) to exist in the deterministic motion model — otherwise a broadside-parked body (e.g. the 18 m Scheibe) cannot enter through the door and is **inexpressible** in the env, exactly the wall the Stage B probe (2026-06-12) identified. This capability is **tracked by #599** (an early WIP lives in the now-stale PR #608). Per the 2026-06-11 design note, it should land as a **clean ADR-0010 amendment**, *not* by reviving that WIP. **This sub-project is blocked on that motion capability landing** (or a deliberate scope cut to non-broadside objects for early curriculum stages). It is a shared prerequisite for *any* routable broadside body — learned or deterministic — not a cost this RL design introduces.

## 7. Curriculum hooks (parameterization for #3/#4)
The env constructor must accept difficulty knobs so a curriculum can ramp:
- object **count** and mix (1 box in an empty rect → many objects → the full Herrenteich + ground objects);
- hangar **shape** (empty rectangle → walls/door → notch/keep-outs → randomized footprints for cross-hangar generalization);
- **clearance** budget (loose → tight);
- optional **fixed seed layout** to start near a known-valid anchor (curriculum only — not behavior cloning; no teacher labels are consumed).

## 8. Determinism & the verifier relationship
- The **environment reward** is a deterministic function of (state, action) — geometry only, RNG-free — so episodes are reproducible given the policy's actions.
- The **policy** is **not** under the ADR-0003 byte-identical contract (that contract stays on `collisions.check` / `towplanner` as the deterministic verifier). The learned-path determinism story (within-build bit-identical vs cross-machine validity-only) is a **sub-project #5** decision and is explicitly out of scope here.
- **Final gate:** whatever the policy outputs is run through `collisions.check` before it is ever surfaced; an invalid layout is never returned. The agent's by-construction routability is additionally re-verified by the same geometry. Safety does not depend on the policy being correct.

## 9. Environment interface (sketch — refined in implementation)
A `gym`-style API, in a new top-level **`ml/`** dir (never in the wheel, like `bench/` and `viewer/`):
```
HangarFitEnv(hangar, requested_set, *, difficulty=..., reward_weights=..., seed=...)
  .reset() -> Observation
  .step(action: Primitive | Park) -> (Observation, reward: float, done: bool, info)
  .render() -> reuse visualize / scene for episode debugging
```
`info` exposes the reward term breakdown (for #4 reward debugging) and the live validity/routability verdict.

## 10. Open questions (resolve in later sub-projects, not here)
- Continuous vs binned primitive **magnitudes** (#2/#3).
- Penalty-only vs hard **action masking** for illegal moves (#3).
- Exact **potential** Φ and reward **weights** (#4).
- Whether cross-hangar generalization needs a **synthetic hangar generator** and how realistic it must be (#4) — and whether cross-*hangar* (vs cross-*fleet* on the fixed Herrenteich hangar) is worth its cost (revisit with measured training results).
- The **inference determinism** contract and packaging (#5).

## 11. Success criteria for sub-project #1
- A committed, documented env interface + reward spec (this doc) the maintainer approves.
- Enough precision that sub-project #2 (encoding) and #3 (policy) can start without re-litigating the MDP or reward tiers.
- No implementation in this sub-project beyond, at most, a thin `ml/` env scaffold *if and when* the maintainer approves moving to `writing-plans`.
