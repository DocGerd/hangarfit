# Design spec — feasibility spike: end-to-end learned goal-conditioned tow-motion policy

**Status:** Proposed (feasibility spike) · **Date:** 2026-06-25 · **Owner:** Patrick Kuhn
**Relates to:** epic #607 (learned backend), [ADR-0028](../../adr/0028-learned-backend-train-to-mastery-resolved-negative.md) (train-to-mastery resolved-negative), [ADR-0007](../../adr/0007-tow-path-planner-v1-scope.md)/[ADR-0010](../../adr/0010-reeds-shepp-motion-model.md) (tow planner & motion model)

> This is a **brainstorming spec**, not an implementation plan. It defines the spike's
> question, scope, architecture, success criteria, and go/no-go gates. The
> implementation plan is produced separately (writing-plans) after this spec is approved.

---

## 1. Motivation — the measured problem

The Herrenteich all-8 layout is **statically valid** (`hangarfit check examples/herrenteich/layout.yaml`
passes) and is **achievable in real life** — the club *normally* parks all eight without relocating
already-parked aircraft (monotone fill; relocation is the rare exception). Yet the
deterministic tow planner (`solve --render-paths`,
`plan_fill`) cannot auto-route the full fill: three aircraft cannot be threaded into
their slots past their neighbours within a tractable search budget.

A controlled grid-resolution sweep (single body → its slot, against the other placed
bodies; 31-core parallel; 80–100k expansion budget) makes the failure mode precise:

| Aircraft | Result across xy ∈ {0.05 … 0.5 m}, deg ∈ {2 … 15°} | Stat |
|---|---|---|
| scheibe_falke, zlin_savage, stemme_s10, ctsl, wild_thing | **ROUTED at every resolution** (often `exp=0`, solved by the analytic Reeds–Shepp shot) | — |
| **aviat_husky, cessna_140, fk9_mkii** | **NO_PATH at every resolution** (0.05 m/2° through 0.35 m/10°) | always `budget_exhausted=True`, `space_exhausted=False` |

**Reading:** the hard cases are **budget-bound, never space-exhausted** — the search never
*proves* no path exists; it runs out of expansions. Refining the grid makes it *worse*
(larger state space, same budget). The bottleneck is **search guidance over a uniform
grid**, not resolution, not the motion model, not the geometry.

Prior controlled experiments this session already falsified the other candidates: turn
radius (all Herrenteich movers are R=0 pivot-capable, faithful), pivot point (≈ main gear,
faithful), cart strafe (implemented + engaged), reverse motion (present), dimensions
(spec-exact; shrinking didn't help), clearances (zeroing all four didn't help), and
z-layer nesting (already modelled). The static layout is valid; only **auto-tow-routing**
fails, and it fails because uniform Hybrid-A* is intractable for threading a body into a
nested slot.

**Hypothesis under test:** a learned, goal-conditioned motion policy — trained to imitate
a *complete* (slow but correct) planner — can route the cases uniform A* cannot, **fast**,
and **generalise to layouts it never saw in training**.

> This is consistent with [ADR-0028](../../adr/0028-learned-backend-train-to-mastery-resolved-negative.md):
> #607's train-to-mastery failed at *inventing a whole layout from scratch* (sparse,
> flat-in-clearance reward, no teacher). This spike attacks a **different, narrower**
> problem — *route a single body to a given target pose* — which is goal-conditioned
> control with a **dense** signal (SE(2) distance-to-goal is never flat) and a **correct
> teacher** (the deterministic planner is slow, not wrong). Those two properties are
> exactly what #607 lacked.

---

## 2. The spike question

> Can a goal-conditioned policy, trained by **imitating a complete planner**, route a
> single rigid body (own-gear or on-cart) to a given target pose past a fixed obstacle
> field — **generalising to layouts it never saw** — and in particular solve the real
> insertions (Husky / Cessna / FK9) that the fair-budget deterministic A* fails to find,
> within a tractable inference budget?

This is a **feasibility** spike: the deliverable is a yes/no answer with evidence, not a
production model. ML role = **produce the path end-to-end** (chosen over "guide the
search"). Scope = **single-body** routing primitive.

---

## 3. Success criteria (falsifiable)

Measured on a **held-out test set** of scenarios never seen in training:

- **S1 — Generalisation.** Policy produces **oracle-verified** collision-free paths for a
  clear majority of held-out *solvable* scenarios. *Proposed bar: ≥ 70%.*
- **S2 — Beats the deployed planner where it loses (the real bar).** On the subset where
  fair-budget A* (8 000 expansions) `budget_exhausted`s but the complete planner *can*
  solve, the policy solves a meaningful fraction. *Proposed bar: ≥ 50%.*
- **S3 — The motivating case.** Solves the real Herrenteich **Husky** insertion — *iff*
  Phase 0 confirms it is single-body-solvable under some insertion order (see §6).

All three are **oracle-verified**: a "solve" means `collisions.check` /
`path_first_conflict` certify the produced path collision-free. The net is never trusted.

The 70% / 50% thresholds are proposals to tune; the invariant spirit is *"generalises, and
wins where uniform A* loses."*

---

## 4. Scope & non-goals

**In scope**
- One rigid body, one target pose, a **fixed** obstacle field.
- SE(2) motion using the **existing** model: `effective_turn_radius_m()` semantics
  (R=0 → pivot-in-place + straight + reverse + cart-strafe for the Herrenteich fleet;
  finite-radius arcs where applicable), so learned paths are executable by the same model.
- Verification by the **existing** `collisions.check` / `path_first_conflict` oracle.

**Out of scope (follow-ons iff the spike succeeds)**
- Multi-body **move-aside** / the "stack" interlock; full all-8 **sequencing**.
- CLI / `solve` integration; a production-grade trained model.
- RL fine-tuning (optional Phase 3, explicitly out of the spike).

The spike isolates the **single-body routing primitive** — the unit that, if fast +
generalising, is what makes the whole monotone fill tractable. Proving (or refuting) the
learning feasibility of *that unit* is the entire point.

---

## 5. Architecture

New **isolated** subpackage `ml/motion/` (dev/CI-only, like the rest of `ml/`; never
shipped in the wheel).

| Component | File | Responsibility |
|---|---|---|
| **Scenario generator** | `ml/motion/scenarios.py` | Procedurally sample `(hangar ± notch, K catalog obstacle bodies at valid poses, one mover, one target pose)`. Domain-randomised. **Every scenario solvable by construction** (see §5.1). |
| **Oracle / teacher** | `ml/motion/oracle.py` | A **complete** planner — bidirectional RRT* in SE(2) over the existing primitives, or huge-budget Hybrid-A*. Steers / validates with `collisions.check` (the *same* oracle that verifies the policy → no surrogate gap). Emits a verified pose/primitive sequence. |
| **Observation encoder** | `ml/motion/encoding.py` | **Ego-centric, goal-conditioned**: obstacle field as polygon **tokens** (set-transformer, matching existing `ml/policy.py` infra) + relative current/goal pose. Ego/relative framing is the generalisation lever. |
| **Policy net** | `ml/motion/policy.py` | obs → next motion action (primitive selection / bounded SE(2) step). Reuses existing architecture patterns. |
| **BC + DAgger trainer** | `ml/motion/train_bc.py` | Supervised clone of oracle actions, then **DAgger**: roll out policy → query oracle on the states the policy actually visits → aggregate → retrain. Kills compounding error. |
| **Evaluator** | `ml/motion/eval.py` | Greedy / small-beam rollout → deterministic-oracle verify → solve-rate on held-out + the A*-fails subset + the Husky case. Seeded, reproducible. |

**Data flow**

```
scenario-gen ──► oracle solves ──► (obs, oracle-action) pairs ──► BC train
                                                                     │
                          ┌──────────── DAgger loop ────────────────┘
                          ▼
            policy rollout ──► oracle relabel ──► aggregate ──► retrain
                          │
                          ▼
                   eval (held-out + A*-fails subset + Husky) ──► oracle-verified paths
```

### 5.1 Solvability-by-construction (the honesty guarantee)

Every training scenario carries a **proof** it is solvable, by one of:
1. **Reverse-from-parked.** Place the mover *at* its target pose, then route it *out* the
   door with the complete planner; the reverse of that trajectory is a known-solvable
   insertion.
2. **Generate-then-filter.** Sample a target + obstacle field, run the complete planner,
   and **keep only scenarios it solves** (with its verified path as the demonstration).

A policy failure is then unambiguously a **learning** failure — never "the task was
impossible." This is the feasibility-witness discipline that retracted #832 / #835
(`feedback_witness_absent_needs_feasibility_witness`): *"solver fails" ≠ infeasible*; a
valid test needs a provable witness.

### 5.2 The validity oracle is the product checker

`collisions.check` (+ Caddy egress where relevant) is the **single source of truth** for
both teacher steering and policy verification — never a learned surrogate. This is the
#694 product-checker contract the `ml-rl-guard` enforces.

---

## 6. Phased plan with go/no-go gates

**Phase 0 — de-risk the teacher (riskiest assumption first).**
Build/borrow the complete planner. Establish:
- **(a)** It finds a **monotone single-body insertion order + paths for the real
  Herrenteich all-8.** This directly answers the original "why does auto-fill fail?":
  the deployed planner's *fixed back-first order + fair budget*, not physics. (Earlier
  experiments showed Husky needs move-aside *in strict back-first order*; Phase 0 searches
  over **orders** to find one where every single-body insertion is solvable — which the
  real-world monotone fill implies exists.)
- **(b)** It solves a batch of procedural hard scenarios (provides the first demos).

> **GATE 0:** If no complete planner can produce single-body demonstrations for the hard
> cases (incl. Husky under *any* order), **STOP**. The gap is then **multi-body**
> (move-aside / interlock), not ML-feasibility. That is itself a decisive, reportable
> finding — and it re-scopes the real planner work, not the ML spike.

**Phase 1 — data + behavioral cloning.**
Scenario generator + a dataset of N oracle-solved scenarios; train BC; measure held-out
solve-rate.
> **GATE 1:** BC shows non-trivial generalisation on easy/medium held-out (sanity that the
> representation + cloning learn *anything* transferable) before investing in DAgger.

**Phase 2 — DAgger + hard cases.**
DAgger to close the narrow-passage gap; evaluate on the A*-fails subset + the Husky case.
> **GATE 2 = the S1 / S2 / S3 success criteria.** This is the spike verdict.

*(Phase 3 — optional RL fine-tune on top of BC+DAgger. Explicitly out of the spike;
listed only as the natural follow-on if Phase 2 nearly clears the bar.)*

---

## 7. Testing & verification

- **Every** policy path is verified by the deterministic oracle; the net is never trusted.
- **Seeded, reproducible** eval harness (fixed seed → fixed held-out set + verdict).
- Unit tests:
  - scenario generator — **every emitted scenario is genuinely solvable** (the oracle
    re-solves it);
  - encoder — ego/relative **invariances** (translating + rotating the whole scene leaves
    the action distribution equivariant);
  - eval harness — verdict reproducibility.
- `collisions.check` stays the single source of truth (no surrogate validity).

---

## 8. CPU / parallelism (standing requirement)

Scenario-generation, oracle solving, DAgger rollouts, and evaluation are **embarrassingly
parallel** → `ProcessPoolExecutor` across **all 32 cores**, reusing the #758 **forkserver +
parent-side BLAS-cap** pattern to avoid oversubscription. CPU torch is fine for a
spike-sized net. **Max core saturation on every batch stage** — no idle cores on any
multi-job step.

---

## 9. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | Teacher can't solve the hard cases | **GATE 0** — decisive STOP + re-scope to multi-body. |
| 2 | BC compounding error (drift off the demo manifold) | **DAgger** — relabel the states the policy actually visits. |
| 3 | Generalisation fails | That *is* the measured question; modest single-body scope + held-out test keep the verdict honest. |
| 4 | Single-body framing too narrow for the real Husky | Phase 0 **order-search**; if even that fails, scope-flag to multi-body (still a clean finding). |
| 5 | Representation over-engineering | Pick the **set-transformer** (existing infra); don't gold-plate; raster is a fallback only if tokens underperform. |
| 6 | Oracle (RRT* vs huge-budget A*) too slow to make a dataset | Parallelise across 32 cores (§8); cap per-scenario time; the dataset is built **offline, once**. |

---

## 10. Decisions captured (from brainstorming)

- **ML role:** produce the path **end-to-end** (chosen over "guide the search", which is
  recorded as the discarded lower-risk alternative).
- **Success bar:** **generalise to new layouts** (the strongest of {route-at-all,
  fast-many, generalise}).
- **Training signal:** **imitate a slow complete oracle + DAgger** (chosen over pure
  goal-conditioned RL and over BC-pretrain→RL-from-the-start). Precedent: Motion Planning
  Networks (MPNet) — a net trained on RRT* demonstrations that produces paths end-to-end
  and generalises to unseen environments.
- **Scope:** **feasibility spike first**, single-body, one hard scenario family.

---

## 11. Open questions for the implementation plan

1. Oracle choice — bidirectional RRT* vs huge-budget Hybrid-A* (Phase 0 may pick by which
   actually solves the Husky case at acceptable offline cost).
2. Action parameterisation — discrete primitive selection vs bounded continuous SE(2) step.
3. Dataset size N and the domain-randomisation distribution (hangar dims, obstacle count,
   clearance range) needed for the generalisation bar.
4. Exact S1/S2 thresholds (the proposed 70% / 50% to be confirmed).
