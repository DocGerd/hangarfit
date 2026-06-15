# Spike #332 — Can a learned model produce a valid *tow path*?

**Status:** CONCLUDED — verdict **DEFER**. A learned cost-to-go *heuristic* is a sound, low-risk optimization for the routing *search expense*, but it does **not** address the actual dense-fleet blocker, which is **single-door multi-body sequencing / monotonic placement** (a search-*structure* problem), not heuristic quality. The real routability levers are **shuffle-aware search (#667)** and **joint place-and-route (#607 Milestone 2)** — neither of which is a drop-in A\* heuristic. Pure-feasibility spike; no shippable code.

**TL;DR.** The original premise — "a learned A\* heuristic beats the `_MAX_EXPANSIONS` cap and makes un-routable fills routable" — was **partly invalidated by what we learned since.** The multi-body un-routability turned out to be (a) a tow-*motion abstraction* over-strictness (now fixed: #645/#646/#647/#648) and (b) **monotonic placement through a single door**: each body is routed once to its final pose and never moved again, so a body whose corridor passes through an already-parked body has no path — *regardless of heuristic quality*. A better cost-to-go field reorders A\* node expansion; it cannot dissolve a true cyclic lock. So the learned tow heuristic is **deferred** behind the structural fixes that actually move the routability ceiling.

---

## Evidence base

- Two-probe gate **#642** (closed 2026-06-15) — Probe B (routability) results below.
- Today's root-cause: **#667** (shuffle-aware tow routing) and its merged increment **#668** (deterministic order-search in `plan_fill._place_rest`); the #667 increment-2 (move-aside) analysis (see the #667 comment thread).
- Motion-abstraction fixes that overturned the interim "fundamentally un-routable" verdict: **#645** (Stemme dolly / cart turn radius), **#646** (motion clearance ≠ parked clearance), **#647** (lateral strafe + free-swivel pivot), **#648** (0.05 m calibration).
- Incumbent planner: `src/hangarfit/towplanner.py` — Hybrid-A\* + Reeds–Shepp (ADR-0007 v1 scope, ADR-0010 v2 reverse-capable motion); determinism ADR-0003 (guarded by `determinism-guard`).
- Sibling spike: **#331** ([`cnn-layout.md`](cnn-layout.md)) — the layout half (verdict GO, placement-only).

---

## Q1 — Is it possible?

Yes, technically. Candidate formulations:

| Formulation | Fit | Verdict |
|---|---|---|
| **Learned cost-to-go heuristic** (CNN: occupancy grid + goal → cost field used as the A\* heuristic) | Kinematic search + feasibility unchanged; only node ordering changes; preserves all guarantees | **Best-conditioned** — but see Q2 |
| Neural A\* / VIN (end-to-end differentiable planner) | Higher ceiling | Higher risk; harder to keep kinematically valid |
| MPNet-style waypoint sampler | Biases search to promising waypoints | Plausible auxiliary |
| Direct path regression (emit full polyline) | Highest risk | Must still be post-validated for kinematics + collisions |

**The hard part — non-holonomic kinematics.** Tows are reverse-capable Reeds–Shepp curves (ADR-0010) with an entry-cone constraint (#262), a `_REVERSE_COST_FACTOR`, plus the #647 cart **strafe** (`Segment "T"`) and **free-swivel pivot** primitives. A grid-CNN reasons naturally in (x, y); encoding heading + reverse cost + the entry cone + the cart primitive set is the central feasibility risk. Feasible (heading channels, multi-channel goal), but non-trivial — and, crucially, **orthogonal to the blocker** (Q2).

## Q2 — Is it beneficial?

**This is where the spike's premise changed.** The original benefit story was concrete: multi-plane fills were "genuinely un-routable" because Hybrid-A\* hit its expansion cap. Two findings since invalidate that framing:

**(a) The cap was not the wall — the motion abstraction was.** Probe B + follow-ups showed the interim "FUNDAMENTAL — dense all-8 proven tow-unroutable" reading was an **over-strict tow-motion model**, not physics. Two code-confirmed bugs each independently dissolved the 2-body wall: motion clearance was pinned to the full *parked* margin (reality hand-clears ~5 cm during motion), and an in-hangar body used its *taxi* turn radius instead of being dolly-pivoted. Both fixed (#645/#646/#647/#648). So a learned heuristic is **not** needed to make the *2-body* case routable — the corrected verifier already does.

**(b) The remaining dense-fleet blocker is structural, not heuristic.** With the abstraction corrected, `plan_fill` is still a **monotonic greedy fill through a single 13.46 m door**: each body routes once to its final pose against the already-committed set and never moves again (#667 root cause). #668 added a deterministic *order* search (`_place_rest`) — permuting *when* each body tows — but that proved a **true cyclic lock** on the full Herrenteich fleet (no static order seats all 9; 120 k expansions exhausted ~525 s). A learned cost-to-go field changes A\*'s node *ordering*; it **cannot** break a cyclic lock where a body's only corridor passes through another body's final pose. The increment-2 *move-aside* analysis (#667) further found that even a deterministic shuffle is gated by the staging-pose model, not by heuristic quality.

**Baseline (quantified).** Routability of valid layouts at the default `_MAX_FILL_EXPANSIONS = 16000` global budget (per-plane `_MAX_EXPANSIONS = 8000`), measured on solver-found valid subsets:

| subset size | routable | wall-clock |
|---|---|---|
| 4 aircraft | **1/1** | 32 s |
| 5 aircraft | **0/1** (aviat_husky / fk9 blocked, budget exhausted) | 345 s |
| 6 aircraft | **0/1** | 316 s |
| lone broadside Scheibe (18 m) | ✅ routes (0.5 s with strafe) | — |
| Scheibe + Stemme (N=2, deep poses) | ❌ no order routes both into an *empty* hangar | — |

The routing **search** is genuinely expensive at 5+ bodies (hundreds of seconds), and the ceiling is ~4 at the default budget. **Where a learned heuristic *could* help:** cutting node expansions on the already-routable cases and pushing a few more bodies inside the budget on fills that are *order-routable but expensive*. **Where it cannot help:** the cyclic-lock / single-door-sequencing cases that are the actual dense-fleet wall.

**Versus RRT-Connect** (the classical alternative named in the issue): same verdict — a different sampler/search also does not address monotonic placement; it would help the search-expense axis, not the structural one.

## Q3 — How would it integrate, modularly?

Same non-negotiable as #331: **proposer + verifier.** Sketch (if pursued later):

- A pluggable **heuristic seam** in `towplanner.py` behind the Hybrid-A\* interface — default = analytic heuristic, optional = learned. An *inadmissible* learned heuristic may yield sub-optimal but still-valid paths; the kinematic-feasibility + `collisions.check`/`path_first_conflict` oracle guarantees safety regardless. The spike must state whether admissibility is preserved or knowingly traded for speed.
- **Graceful fallback** preserving the best-effort `plans[i] = None` contract: if inference is unavailable or a learned path fails validation, fall back to the deterministic planner.
- **Determinism (ADR-0003):** the byte-identical contract extends to `towplanner.py` (guarded by `determinism-guard`). A learned heuristic would need the same scope amendment as #331's proposer (within-build bit-identical; cross-machine verifier-validity-only), or be scoped out of the determinism contract explicitly.
- **Packaging:** ONNX-runtime-only inference; no training deps shipped.

## Q4 — How would it be trained?

- **The deterministic planner is the data generator** — run `towplanner.py` over many (grid, start, goal) instances → optimal path / expanded cost-to-go field as imitation labels. Free, abundant, on-distribution.
- **Free analytic label:** the closed-form Reeds–Shepp distance is an obstacle-free lower bound; train the CNN to predict the *obstacle-aware correction* on top of RS distance — an easier target than the full field.
- Input: occupancy-grid raster (placed planes = obstacles), goal-pose heading channels; encode reverse cost + entry cone + cart primitives; bounded grid resolution vs the continuous planner.

This is sound and low-risk *if* the goal is reducing routing-search expense. It is **not** what unblocks dense fleets.

---

## Verdict & recommendation

**DEFER the learned tow path.** Rationale:

1. The original "beat the expansion cap → routability" premise was **superseded**: the 2-body wall was an abstraction artifact (fixed), and the dense-fleet wall is **single-door sequencing / monotonic placement** (#667) — a search-*structure* problem a learned A\* heuristic cannot dissolve.
2. The **right routability levers first** are structural, not learned: shuffle-aware search (**#667**, deterministic move-aside — increment 1 shipped in #668, increment 2 paused) and/or **joint place-and-route by construction** (**#607 Milestone 2**, the cold-joint RL agent that co-designs poses *and* order, avoiding packings that box out later bodies — exactly the failure Probe B exhibited).
3. A learned cost-to-go heuristic (or RRT-Connect) remains a **legitimate later optimization** for routing-search *expense* on already-routable fills — re-open as a follow-up *after* the structural routability work, when "expensive but feasible" rather than "structurally locked" is the dominant cost. It is not the increment that moves the ceiling.

So #332's contribution to the learned-backend epic is: the routability dimension of #607 is **Milestone 2 (joint, routability-aware reward)**, gated behind the structural routing work — consistent with #331's placement-only **Milestone 1** GO and the #642 SPLIT verdict.

## Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Solving the wrong problem (heuristic quality vs structural lock) | High | Defer; do structural routability (#667 / #607 M2) first; re-open only for search-expense |
| Non-holonomic encoding (heading / reverse / entry-cone / cart primitives) on a grid-CNN | Medium | Predict obstacle-aware correction on RS-distance lower bound; keep kinematic search + oracle unchanged |
| Determinism contract (towplanner is `determinism-guard`-gated) | Medium | Same ADR-0003 scope amendment as #331; or scope learned heuristic out explicitly with its own canaries |
| Dependency weight | Low | ONNX-only inference; never in wheel |

> Companion verdict for the placement half is in [`cnn-layout.md`](cnn-layout.md) (#331): **GO** for a learned placement *proposer* scoped to routable daily subsets (the dense static-optimal all-8 is a needle and tow-unreachable).
