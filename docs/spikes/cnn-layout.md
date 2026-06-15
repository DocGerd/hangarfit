# Spike #331 — Can a learned model produce a valid hangar *layout*?

**Status:** CONCLUDED — verdict **GO for the placement dimension of a single *joint* learned layout+path backend** (#607), scoped to the regime RR-MC misses (dense oblique z-nested, Herrenteich-class), behind the deterministic verifier. Layout and tow path are co-designed by one model (see the sibling #332) — placement is **not** a standalone product phase with routability deferred. Pure-feasibility spike; no shippable code.

**TL;DR.** For the small placeholder fleet the deterministic RR-MC solver already dominates and a learned model is *not* worth it. The learned proposer earns its place only in the **dense oblique z-nested** regime — exactly where the real Herrenteich set lives and where RR-MC fails to find *any* valid placement. The recommended formulation is **not a CNN that emits poses in the image frame** (that coupling sank the original PoC) but a **permutation-invariant set model (Set-Transformer / GNN) over the object set, conditioned by a small CNN that encodes the hangar keep-out mask as context only**, with poses produced in the world frame and the existing `collisions.check` parts-model oracle as the sole arbiter of validity. This is the Stage-C design captured in epic **#607**; this spike is its feasibility gate and returns GO for the **placement dimension of the joint backend** — co-designed with the tow path (#332), not a deferred-routability split — with one significant caveat on the *density* the joint model can realistically target (see Q4 / Probe A).

---

## Evidence base

This verdict consolidates the two-probe gate executed under **#642** (closed 2026-06-15) plus the design work in the learned-backend primer and the cold-joint RL env spec:

- Design depth: [`docs/superpowers/research/2026-06-11-learned-backend-decision-and-rl-cnn-primer.md`](../superpowers/research/2026-06-11-learned-backend-decision-and-rl-cnn-primer.md) (the long-form architecture/training primer) and [`docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md`](../superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md) (the gym env + reward design).
- Epic: **#607** (the opt-in learned backend), which this spike gates.
- Sibling spike: **#332** ([`cnn-tow-path.md`](cnn-tow-path.md)) — the tow-path half.

---

## Q1 — Is it possible?

Yes, with the right formulation. The layout task is **discrete placement of a variable-size set of objects under hard geometric constraints**, not an image→image map. Candidate formulations weighed:

| Formulation | Fit | Verdict |
|---|---|---|
| **Policy / heuristic net** (suggest next placement; RR-MC search consumes it) | Most modular, keeps determinism + oracle authoritative | Viable, lowest-risk warm-start |
| **Feasibility / value net** (P(partial extends to valid)) | Good for pruning search | Useful auxiliary head, not the whole answer |
| **Generative CNN raster → poses (image-frame output)** | Rasterises space, but **handles a variable plane *set* poorly** and couples output to the pixel grid | **Rejected** — this is precisely the coupling that falsified the original #332-era PoC (poses read off a grid, not the world frame) |
| **Set-Transformer / GNN over the object set** (poses in world frame) | Natural fit for "set of aircraft + 2D space"; permutation-invariant | **Chosen** |

**Chosen formulation (Stage-C, #607):** a Set-Transformer over the object tokens (aircraft + ground movers), **conditioned by a small CNN that encodes the hangar keep-out mask as context only** (door throat, maintenance bay, structural notch, fixed fuel-trailer keep-out). Three heads: a **selection head** (placement/tow order, where soft door-priority is learned), a **coarse pose head** (discrete pocket + heading-bin per object), and a **feasibility head** (soft-mask obviously-bad poses). A **deterministic refiner** snaps each coarse (pocket, heading-bin) to continuous `(x, y, heading)` by local search under `collisions.check` — restoring the per-instance precision the coarse head deliberately gives up. The CNN feeds context; it never emits the pose output in pixel frame.

## Q2 — Is it beneficial?

**Honestly: not for the small static fleet; yes for the dense regime.** The placeholder layout problem (~6–9 aircraft) is a tiny CSP the deterministic solver solves quickly, and ML's generalisation value proposition is weak there. The benefit is concentrated in **one measurable place**:

**Baseline (the case that matters).** On the calibrated dense all-8 Herrenteich `layout.yaml`, placement-only RR-MC at a high restart budget (3 seeds × 45 s) finds **0 valid placements** (`exhausted_budget`, 0 distinct basins) — the deterministic search **cannot locate any valid dense all-8 packing**. The only known valid dense packing is the hand-authored layout, found by an offline search "the product solver cannot generate" (its own header). That is the gap: **reach, not beat.** A learned proposer that reaches valid dense oblique z-nested layouts RR-MC never finds is the entire justification; on any single small instance a slow deterministic solver may still win.

Secondary potential wins (lower priority, not the bar): diverse/human-aesthetic layouts beyond the ADR-0004 edit-count metric; interactive "suggest as you drag"; warm-start to cut solver wall-clock.

## Q3 — How would it integrate, modularly?

**Proposer + verifier, non-negotiable.** The learned model only *proposes* poses and order; `collisions.check` + the parts model (ADR-0001) stay the ground-truth oracle, and **every proposed layout is run through the unchanged check before it is ever returned**. Sketch (matches #607):

- A `--backend {rrmc,learned}` CLI seam at the `SearchConfig` build in `cmd_solve()`; `solve()` keeps its deterministic contract and a sibling learned entry returns the **same `SolveResult` shape**, so render / `view` / `--write-yaml` are unchanged.
- Learned output feeds the **same** validity check + ADR-0008 spread post-pass in the **same** coordinate frame (ADR-0002 — the determinant-−1 trap stays owned by the existing geometry, the model never re-derives transforms).
- **Determinism (ADR-0003) tension:** a new ADR **amends ADR-0003's scope** — the verifier stays strictly byte-identical-bound; the learned *proposer* gets a weaker, explicitly documented contract (within-build double-run **bit-identical** with fixed weights + seed + pinned onnxruntime EP; cross-machine = **verifier-validity-only**, not byte-identical poses). The learned path is **not** under `determinism-guard`.
- **Packaging:** ONNX-runtime-only inference (`[learned-infer]` extra); torch is contributor-only (`[train]`); a top-level `ml/` dir never in the wheel. Default install unchanged.

## Q4 — How would it be trained?

**Behavior-clone from a slow "teacher nester", then PPO fine-tune** with a potential-based (Ng–Harada–Russell) dense shaping reward read from the verifier, keeping the binary valid(+routable) verdict as the truth signal (potential-based shaping is policy-invariant → anti reward-hacking). The teacher is the offline search that already produces dense valid layouts (the same family as the `/tmp` SA harness that produced the real `layout_today`).

**⚠️ The significant caveat — Probe A says the dense target is a *needle*.** Perturb-and-reject from the all-8 anchor (400 samples/scale, fixed seed):

| pos σ / head σ | valid fraction | mean displacement (valid) |
|---|---|---|
| 0.02 m / 0.5° | 96.8 % | 0.028 m |
| 0.05 m / 1.0° | 55.8 % | 0.068 m |
| 0.10 m / 2.0° | **6.0 %** | 0.117 m |
| 0.20 m / 5.0° | **0 %** | — |

Validity collapses to 0 % by ~20 cm mean displacement: the calibrated all-8 sits at a **feasibility corner** (expected — #605 tuned clearances down to 0.20/0.15 until it *just* fit). Combined with RR-MC finding 0 basins, the feasible region at the full all-8 is **thin AND hard to find**. (Caveat on the caveat: single-anchor jitter measures *one* basin and RR-MC failure is a search limit, not proof of absence — but together they read strongly needle-ish at the production peak.)

**Consequence for training scope:** a reward-driven / BC agent needs broad support in the feasible region. A needle at the static-dense peak means **the joint model should target the *routable* daily subsets** (the real "who fits — and can be towed in — today" question, usually < all-8), with the curriculum ramping toward the full static-dense all-8 as a stretch it **may plateau before**. The static-dense `layout.yaml` must **not** be used as the BC target — it is tow-unreachable by any method (see #332 / the #642 routability probe), which is exactly why layout and path are co-designed: the joint model picks a *routable* density rather than reproducing the static-dense peak.

---

## Verdict & recommendation

**GO for the placement dimension of the joint layout+path backend, behind the verifier — as specified in #607.** Specifically:

1. **Pick the set model, not a pose-emitting CNN.** The CNN is mask-context only; poses come from the set heads in the world frame.
2. **Reach, not beat.** Justify the backend solely on reaching dense oblique z-nested layouts RR-MC misses (0 basins found today), at fast amortized inference — not on per-instance speed.
3. **Scope the training target to routable daily subsets**, not the needle-thin static-dense all-8. Define the realistic ceiling empirically (the max routable count/density sweep — see #332).
4. **Keep the verifier authoritative and the determinism scope amended, not weakened.**

This spike does not flesh #607's sub-issues — those graduate to their own milestone per the spikes convention. The follow-on dependency chain is the #607 sub-issue list (ADR + seam → teacher dataset → gym env → Set-Transformer placement-only → routability head → ONNX + wiring → packaging → evaluation), each detailed only after this GO.

## Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| **Needle feasible region** (Probe A): BC corpus thin / RL exploration high-variance at the dense peak | High | Scope to routable subsets; curriculum from easy → dense; never BC the static-dense anchor |
| Dependency weight (torch/onnxruntime on a lean CLI) | Medium | ONNX-only inference extra; torch contributor-only; never in wheel |
| Determinism contract erosion | Medium | Amend ADR-0003 scope explicitly; verifier stays byte-identical; learned-path canaries (within-build bit-identical, cross-machine validity-only) |
| Image-frame output trap (re-introducing the old PoC failure) | Medium | Hard rule: CNN = context only; poses in world frame from set heads |
| "Not worth it" for small fleets | Low (by design) | Opt-in `--backend learned`; RR-MC stays default + fallback |

> Companion verdict for the path half is in [`cnn-tow-path.md`](cnn-tow-path.md) (#332): **GO**, learned *jointly* with layout (routability-by-construction). Only the narrow alternative of a standalone learned A\* *heuristic* bolted onto the existing monotonic planner is set aside — it cannot break single-door cyclic locks (#667) and keeps the wrong place-then-route decomposition.
