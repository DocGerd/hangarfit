# Design Spec — Learned Proposer + Deterministic Verifier Backend

**Status:** Design / planning (2026-06-11). Audience: maintainer + future contributors.
**Companion artifact:** `docs/superpowers/research/2026-06-11-learned-backend-decision-and-rl-cnn-primer.md` (decision matrix + RL/CNN primer).

---

## Intent

`hangarfit`'s deterministic RR-MC solver ([`solver.py`](../../src/hangarfit/solver.py), ADR-0003 byte-identical contract) is the workhorse for placement search and tow bundling, but it provably **cannot find the dense, oblique, z-nested layouts** the real Airfield Herrenteich site demands — the #599 finding is that the model today cannot even fit the 8 aircraft in a *valid-and-routable* way (only broadside-on-gear packs fit eight, and those aren't tow-routable). This initiative adds a **learned proposer** in front of the existing **deterministic verifier**, as an *opt-in alternative backend* (`solve --backend learned`), and — as the prerequisite that makes any of it meaningful — first teaches the model the *full* real hangar floor (8 aircraft **plus** a fuel trailer, two glider trailers, and a rescue vehicle) and calibrates it until that real, known-good configuration is provably feasible. The verifier never changes and never enters the learning trust boundary; the learned part only *proposes* poses and order, and a deterministic refiner + verifier accept or reject. We deliver this as a **reversible four-stage ladder**, the first two stages of which ship real value with **zero ML**.

---

## Success definition ("best")

Lexicographic. The hard gate dominates absolutely; the soft preference only ever breaks ties among already-hard-valid layouts.

| Tier | Rule | Source |
|---|---|---|
| **HARD (gate)** | Every object fits (valid under `collisions.check`) **AND** every mover is routable (`towplanner.plan_fill` / `path_first_conflict`) **AND** the self-driving rescue vehicle is nearest the door with a clear egress lane. A layout violating any of these is **rejected — same tier as a geometric collision.** | the verifier |
| **SOFT (tie-break)** | When a scenario specifies a desired sequence / door-priority (e.g. "ctsl nearest the exit"), closer-to-requested order is better; position 2 when position 1 is geometrically impossible is acceptable, not a failure. | scenario data |

ADR-0008 spread is a **secondary** soft term and is likely out of scope for the learned backend's reward. The learned backend's bar is **reach, not beat**: produce valid+routable layouts in regimes RR-MC misses, at fast *amortized* inference. A slow deterministic solver may still win on any single instance.

---

## Ground-object taxonomy (general model — Herrenteich configures specifics, nothing hardcoded)

The **behaviors** live in code; **which concrete object is which** is pure catalog/scenario data (exactly as the Herrenteich office notch is data, not code). Nothing like "fuel trailer is on the left" or "the Caddy is the door object" may be hardcoded in `collisions.py` / `towplanner.py` / `solver.py`.

| Class | Placed? | Routed? | Position | Hard rule | Reuses | Herrenteich instance |
|---|---|---|---|---|---|---|
| **Aircraft** | yes | yes (full tow) | solver / learned-chosen | existing collision + tow-routability | parts model; movement modes | the 8 airframes |
| **Fixed obstacle** | no (static keep-out) | no | fixed in data | a forbidden polygon; constrains the door throat / routing only | `MaintenanceBay` / `StructuralNotch` keep-out path | "Maul Tankanhänger" fuel trailer (always left, just inside the door, never moves) |
| **Mover — soft region** | yes | yes (own path) | chosen, not fixed | **soft**: aligned to the right side | ADR-0010 towed (cart / Reeds–Shepp) | the 2 glider trailers (towed rigid bodies) |
| **Mover — hard door** | yes | yes (own path) | chosen, not fixed | **HARD**: always nearest the door with a clear egress lane; violation = REJECT | ADR-0010 steerable (arcs, self-driving) | VW Caddy rescue vehicle (powers itself) |

Movers are **new object behaviors, not a new planner** — a self-driving car maps onto own-gear Reeds–Shepp (positive `r_min`, six-primitive fan); a towed trailer maps onto the reverse-capable cart-style path (`effective_turn_radius_m == 0`). This is captured by an **ADR-0010 amendment**, not new search code.

---

## The four-stage ladder

Each stage is independently valuable and the order is load-bearing: a later stage cannot start until its predecessor removes a concrete blocker. The first two stages are fully deterministic and shippable with **no ML**; the third is the kill-switch; the fourth is the build.

| Stage | What | Delivers | Why this order |
|---|---|---|---|
| **0 — #595** (data refactor) | Per-object **catalog** of *physical objects* (aircraft is one `type:`), referenced by id from fleet/scenario. | A clean home for non-aircraft objects + a `type:` discriminator routing to per-type strict allowlists. | **First.** The `fleet.yaml` loader is aircraft-only (`load_fleet` requires a top-level `aircraft:` list; `_build_aircraft` enforces `_ALLOWED_AIRCRAFT_KEYS`). A trailer/car has no `parts`/`wheels`/`struts`/`wing_position` — without the catalog it would pollute the aircraft allowlist. |
| **A — Epic "Ground objects + Herrenteich calibration"** | The ground-object taxonomy above + the **calibration** prerequisite, all deterministic. Built **on the #595 catalog**. | A realism upgrade to `check`/`solve`/`view` *and* the substrate the learned backend needs: a model-feasible real Herrenteich set. | **Second.** A learned proposer can only train toward a config the verifier accepts. If the real layout is "impossible" in the model, there is nothing to clone and nothing to train toward (the verifier would reject the real layout; the teacher couldn't generate it). |
| **B — Spike (GO/NO-GO)** | The existing CNN spikes **#331 (layout) + #332 (tow) reframed and merged** into one joint feasibility probe. | A binary read on: *can a slow "teacher nester" produce dense oblique z-nested **and** single-door-routable layouts to behavior-clone from?* | **Third, gated on A.** No teacher exists in `src/` today; it is the single largest unproven precondition. A clean NO-GO shelves Stage C *before* any torch/onnxruntime/CI surface is reshaped. Joint (not decoupled) because the densest packs are exactly the un-routable ones — a placement-only spike would mis-calibrate the gate to a false GO. |
| **C — Epic "Learned hybrid backend"** | The implementation: proposer → refiner → verifier, hybrid training, packaging, determinism scope-amendment. | `solve --backend learned` reaching layouts RR-MC misses, at fast amortized inference. | **Last, gated on B GO.** Sub-issues are *named for legibility but deliberately not fully fleshed* until the spike validates the teacher — don't over-commit design before the kill-switch fires. |

### Critical prerequisite (top de-risk): the calibration pass inside Stage A

This is the cheapest, highest-leverage step in the whole initiative and is called out explicitly because everything downstream depends on it. The committed all-eight `examples/herrenteich/layout.yaml` passes `check` (exit 0) but was **found by a search that drives the checker directly**, not by the product solver — and the #599 finding shows the deterministic packers fit eight only broadside-on-gear, none routable. The four ground objects can't be expressed at all yet.

Two hypotheses for the gap between "reality fits all of it" and "the model can't fit even eight": **(1) pessimistic dimensions** (every Herrenteich entry is `measured: false`; part stations / most tail+fin chords / all strut attach points are *derived*), and **(2) too-generous clearances** (`clearance_m: 0.3`, `wing_layer_clearance_m: 0.2` are placeholders; the real club clears fins "~0.40 m by hand"). The calibration sub-issue **audits and adjusts dims + clearances** — with a sourced audit trail, never silently shrinking airframes — until `collisions.check` accepts a hand-authored real **all-11-object** layout and the movers route. Cheap measurement/spec work, not algorithm work, and the gate for Stage B and C.

---

## Learned-backend architecture (Stage C target)

```
        variable object set (aircraft + movers)        hangar keep-out MASK
                      │                                        │
                      ▼                                        ▼
            ┌──────────────────────┐                 ┌──────────────────┐
            │  Set-Transformer     │◄── context ─────│  small CNN        │
            │  (permutation-       │                 │  (mask = CONTEXT  │
            │   invariant encoder) │                 │   ONLY, not the   │
            └──────────────────────┘                 │   output frame)   │
              │          │          │                └──────────────────┘
        selection    coarse pose  feasibility
          head         head         head
       (order: incl.  (discrete    (soft-mask
        soft door-    pocket +      bad poses)
        priority)     heading-bin)
                      │
                      ▼
        ┌───────────────────────────────┐
        │ DETERMINISTIC REFINER          │   snaps coarse → continuous (x,y,heading)
        │ local search under             │   under collisions.check graded penetration
        │ collisions.check               │
        └───────────────────────────────┘
                      │
                      ▼
        ┌───────────────────────────────┐
        │ DETERMINISTIC VERIFIER         │   GROUND TRUTH — accept / REJECT
        │ collisions.check  +            │   (validity, routability of every mover,
        │ towplanner.plan_fill           │    hard Caddy nearest-door/egress)
        └───────────────────────────────┘
```

- **Proposer.** Permutation-invariant **Set-Transformer** over the variable object set, conditioned by a **small CNN encoding the hangar keep-out mask as CONTEXT ONLY**. The CNN must **not** emit the pose in pixel/image frame — that image-frame-output coupling is exactly the trap that sank the original grid-CNN spike (#332 falsification). Poses are produced in the world frame by the set heads. Three heads: **selection** (placement/tow order, where the soft door-priority is learned), **coarse pose** (discrete pocket + heading-bin — coarse on purpose, robust), **feasibility** (soft-masks bad poses to prune the refiner).
- **Refiner.** Deterministic local search snaps each coarse (pocket, heading-bin) to continuous `(x, y, heading)` under `collisions.check`, restoring the per-instance precision the coarse head deliberately gives up.
- **Verifier.** Strictly deterministic ground truth; sole arbiter. Rejects any hard-gate violation outright.
- **Training (hybrid).** (1) **Behavior-clone warm-start** from the slow teacher nester (Stage B artifact) to get a non-random policy; (2) **PPO fine-tune** with a **potential-based (Ng–Harada–Russell) dense shaping reward** read from the verifier — graded penetration + routability margin + sequence-deviation — while the **binary valid+routable verdict is kept as truth**. Potential-based shaping is policy-invariant, so dense shaping can't change the optimum → anti reward-hacking.
- **Learning sequence.** **Milestone 1 = placement-only** (learned proposer places; existing deterministic planner routes afterward). **Milestone 2 = joint routability-aware** (routability margin enters the reward), **with a spike-driven trapdoor** to jump to joint early if density and routability prove entangled.
- **Packaging.** Opt-in `solve --backend learned` (default stays `rrmc`); branches at the `SearchConfig` build in `cmd_solve`, returns the same `SolveResult` shape so render/`view`/`--write-yaml` are unchanged. Two new extras: **`[learned-infer]`** (onnxruntime, user-facing inference) and **`[train]`** (torch, contributor-only) — neither in the default install. A top-level **`ml/`** dir (mirrors `bench/`, `viewer/`) holds teacher / gym / training / export; **never in the wheel** (`packages.find where=["src"]` never discovers it). Trained ONNX weights ship as **signed Release assets** via the existing `release.yml` Sigstore keyless cosign (reuse, no new key).
- **Determinism scope-amendment.** A **new ADR amends ADR-0003's SCOPE**: the verifier stays strictly deterministic and byte-identical-bound; the learned **proposer gets a weaker, explicitly documented contract** and is **not** under ADR-0003 / `determinism-guard`. The learned path gets **its own canaries**: within-a-build double-run **bit-identical** (fixed weights + seed + pinned onnxruntime EP); **cross-machine = verifier-validity-only** (same valid+routable verdict, not byte-identical poses — float/EP hardware nondeterminism is expected below the verifier).

---

## Issue map

Existing issues: **#595** (data refactor / substrate), **#331** (CNN layout spike), **#332** (CNN tow spike). `#594` (canonicalize hardening) is **unrelated**. Sub-issue + blocked-by edges are wired by the orchestrator; the prose below states the dependency intent.

```
#595  Stage 0 — per-object catalog + `type:` discriminator   ◄── substrate, FIRST
  │  (comment appended: catalog of physical objects, aircraft is one type)
  ▼
EPIC A — Ground objects + Herrenteich calibration   (deterministic, ML-free)   Refs #595
  ├─ A1  Ground-object data model + loader (fixed obstacle / mover types)        blocked-by #595
  ├─ A2  Non-aircraft mover motion (steerable car + towed trailer) + ADR-0010 amendment   blocked-by #595, must NOT depend on feature/599 strafe WIP
  ├─ A3  Caddy HARD nearest-door + clear-egress verifier rule  (+ SOFT door-priority tie-break)   blocked-by #595, A1; follows calibration for its acceptance fixture
  ├─ A4  Glider-trailer placement + SOFT right-region term (ADR-0008-style post-pass)   blocked-by #595, A-epic, calibration
  ├─ A5  Fixed-obstacle (fuel-trailer) keep-out   blocked-by #595, A1   (may fold into A1)
  ├─ A6  ★ Herrenteich dims/clearance CALIBRATION → feasible real all-11 layout   blocked-by #595, A1/A2/A5; Phase-1 (aircraft-only) can go standalone   ◄── TOP DE-RISK
  └─ A7  Render ground objects (2D PNG + scene/v2 + 3D view)   blocked-by A-model/collision/routing sub-issues
       │
       ▼ (gated on Epic A, esp. A6 calibration)
SPIKE B — Joint GO/NO-GO feasibility   ←reframes & MERGES #331 + #332   Refs #595
  │  central Q: can a slow teacher nester emit dense oblique z-nested + routable layouts to clone?
  │  GO iff: teacher emits valid+routable dense packs on a hard N=7–8 single-door instance,
  │          AND a BC probe reproduces them above a stated verifier-acceptance rate
  ▼ (gated on B = GO)
EPIC C — Learned hybrid backend   (label: later)   Blocked-by B GO; builds on #595 + Epic A
  ├─ C1  ADR (amends ADR-0003 scope) + `--backend {rrmc,learned}` seam (learned stubbed)   dep: B GO
  ├─ C2  Teacher nester → reproducible BC dataset generator   dep: C1
  ├─ C3  Gym/RL environment wrapping the verifier   dep: C1
  ├─ C4  Set-Transformer + refiner, BC→PPO, PLACEMENT-ONLY (Milestone 1)   dep: C2, C3
  ├─ C5  Routability head + JOINT reward (Milestone 2) + trapdoor decision   dep: C4
  ├─ C6  ONNX export + `[learned-infer]` inference wiring behind the seam   dep: C4 (extended after C5)
  ├─ C7  Packaging: `[learned-infer]`/`[train]` extras, `ml/` dir, signed Release weights, learned canaries   dep: C6
  └─ C8  Reach-not-beat evaluation + acceptance bar   dep: C6 (extended after C5/C7)
```

Notable supporting edges:
- **A7 (render)** depends on the A model/collision/routing sub-issues — it draws their output; it cannot land before ground objects exist. It is the only sub-issue carrying `area:frontend` (touches `visualize.py` + `scene/v2` + `viewer/`).
- **A6 (calibration)** *blocks* Spike B (a model-feasible real set is the precondition for the teacher to have anything to clone) and is the acceptance fixture (zero-`caddy_egress`-conflict) for **A3**.
- **A2 (motion)** must **not** depend on the un-merged `feature/599-lateral-broadside-tow-entry` strafe WIP; develop's `SegmentKind` is still `Literal["L","S","R"]`.
- ADRs touched: new ADR amends ADR-0003 scope (C1); ADR-0010 amended (A2); ADR-0008 amended (A4); ADR-0006/0018 precedents reused for keep-outs (A1/A3/A5).

---

## Risk register

| # | Risk | Stage | Mitigation |
|---|---|---|---|
| R1 | **Real Herrenteich set is infeasible in the model** — if calibration can't make the all-11 layout valid+routable, there is nothing to train toward and the whole learned-backend premise collapses. | A6 | Top de-risk; do it first and cheaply (dims/clearance audit, sourced). Allow aircraft-only Phase-1 as a standalone gate. A NO here halts Stage B before any ML cost. |
| R2 | **No teacher exists** — if a long-budget teacher nester *also* can't produce dense oblique z-nested + routable layouts, BC has no corpus and Stage C is dead. | B | This *is* the spike's central question and master kill-switch; a clean NO-GO shelves Stage C with zero torch/onnxruntime/CI surface added (mirrors how the original #331/#332 NO-GO already saved an epic). |
| R3 | **Density ⊥ routability false GO** — a placement-only spike would re-create RR-MC's single-door deadlock and mis-calibrate the gate. | B | Merge #331+#332 into one **joint** spike; require ≥1 hard N=7–8 instance that stresses single-door routability; run every candidate through `check` *and* `plan_fill`. Record a trapdoor for Stage C. |
| R4 | **CNN frame-coupling trap** — emitting the pose in the CNN's image/grid frame is what sank the original spike. | C | Architecturally fenced: CNN encodes the keep-out **mask as context only**; poses come from the world-frame Set-Transformer heads. |
| R5 | **Reward hacking** — dense shaping reward gamed without real validity. | C | Potential-based (NHR) shaping is policy-invariant; the binary verifier verdict is kept as truth, so shaping can't change the optimal policy. |
| R6 | **Determinism contract violated** — learned float/EP nondeterminism contaminating the byte-identical guarantees. | C | Verifier stays strictly deterministic; new ADR scopes a *weaker, documented* contract for the proposer with its own canaries (within-build bit-identical; cross-machine verifier-validity-only); proposer explicitly outside ADR-0003 / `determinism-guard`. |
| R7 | **Regression in the deterministic path** — ground objects / new soft terms / catalog refactor silently change existing `check`/`solve`/tow output. | 0, A | Every additive feature is **inert-when-empty ⇒ byte-identical** (the notch/apron pattern): empty catalog, zero ground objects, unset right-region weight, unset door-order all reproduce today's output bit-for-bit; `determinism-guard` runs on solver/towplanner changes. |
| R8 | **Scope creep into a new planner** — temptation to fork the tow planner for cars/trailers. | A | Hard non-goal: mover motion is a *parameterization* of existing ADR-0010 primitives (car = positive-radius RS six-primitive fan; trailer = reverse-capable cart). If a mover can't be expressed via the existing fan, that is a finding to escalate, not a license to fork. |
| R9 | **Calibration overfitting** — quietly shrinking airframes to pass. | A6 | Sourced audit trail: every dimension change cites TCDS / manual / on-site tape or is labelled an explicit modelling assumption; confirm a clearance change doesn't newly break the synthetic `data/` fixtures. |
| R10 | **Premature Stage-C design lock-in** before the spike validates the teacher. | C | Stage-C sub-issues are named for legibility but **deliberately not fully fleshed**; concrete acceptance criteria are authored per-issue only after Stage B GO. `later` label on the epic. |
