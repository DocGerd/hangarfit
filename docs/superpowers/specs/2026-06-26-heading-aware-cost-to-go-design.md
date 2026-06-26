# Design spec — heading-aware cost-to-go for the fk9↔cessna nook (deterministic-first)

**Status:** Proposed · **Date:** 2026-06-26 · **Owner:** Patrick Kuhn
**Issue:** #840 (re-scoped) · **Supersedes:** [`2026-06-25-learned-motion-policy-spike-design.md`](2026-06-25-learned-motion-policy-spike-design.md) (the end-to-end learned-policy framing — its core methods are rejected/deferred here; see §3)
**Relates to:** [ADR-0003](../../adr/0003-rr-mc-solver-algorithm.md) (determinism contract), [ADR-0028](../../adr/0028-learned-backend-train-to-mastery-resolved-negative.md) (train-to-mastery resolved-negative), [ADR-0010](../../adr/0010-reeds-shepp-motion-model.md) (motion model), the spike record [`docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md`](../../spikes/herrenteich-fk9-cessna-lateral-shuffle.md) and [`docs/spikes/herrenteich-all8-tow-routing-rootcause.md`](../../spikes/herrenteich-all8-tow-routing-rootcause.md)

> This is a **brainstorming spec**, not an implementation plan. It defines the
> problem, the reframe, the gated sequence, the success/kill criteria, and the
> constraints. The implementation plan (Step 0 + Step 1) is produced separately
> (writing-plans) after this spec is approved.

---

## 1. Motivation — the measured problem (recap)

The real Airfield Herrenteich all-8 layout ([`examples/herrenteich/layout.yaml`](../../../examples/herrenteich/layout.yaml)) is **statically valid** and is parked daily by the club via **monotone fill** (no parked aircraft is ever relocated), so a valid tow order provably exists. Yet `hangarfit solve examples/herrenteich/layout.yaml --render-paths` (and `view`) cannot auto-route the full fill. Two **separable** blockers remain:

- **PRIMARY (#844): the `fk9_mkii` ↔ `cessna_140` front-door corridor.** Two genuine high-wingers that *mutually* space-exhaust at the deployed `0.5 m / 15°` tow grid — no monotone order places both. This is the cm-precision "parallel-park" nook insertion. **This spec targets this blocker.**
- **SECONDARY (separable): `aviat_husky` front-cluster ordering** — an order-search problem, not the fine-grid nook. **Out of scope here** (see §8).

### 1.1 What the witness probe already proved (2026-06-26)

A feasibility-first witness probe (newer than the superseded 2026-06-25 spec) established:

- **Feasibility is proven.** Own-gear Hybrid-A* at `0.25 m / 10°` **finds** the fk9↔cessna path — **96 949 expansions, ~39 min, exact-oracle-validated**. The corridor is threadable on own gear; the club's hand-shuffle is real and faithful (both movers are R=0 pivot-in-place, no strafe).
- **Therefore a correct-but-slow teacher already exists**: the deployed planner at fine resolution. The original spike's Phase-0 question ("does *any* complete planner produce single-body demos?") is **answered yes** and no longer needs an RRT oracle built to settle it.
- **The live bottleneck is fine-resolution search *efficiency*, not feasibility.** Coarse grid cannot represent the sub-grid nook maneuver; fine grid can but is ~40 min/insertion — far over the shippable budget.

### 1.2 The reframe — the lever is heading-awareness, confirmed in the code

The deployed search is a Hybrid-A* over **continuous SE(2)** `(x, y, heading)` (`towplanner.py::plan_path`, primitive fan `_primitives` = forward L/S/R + reverse L/S/R, grid-binned by `_cell(pose) → (ix, iy, iheading)`). But its cost-to-go heuristic is **position-only**:

```python
# towplanner.py:2515  (heuristic="grid", the plan_fill default)
def _h(p: Pose) -> float:
    cell = (round(p.x_m / _GRID_XY_M), round(p.y_m / _GRID_XY_M))   # x,y ONLY — heading absent
    g = _field.get(cell)
    if g is None:
        return math.hypot(goal.x_m - p.x_m, goal.y_m - p.y_m)       # euclidean fallback, also position-only
    return g
```

`_build_grid_heuristic` (`towplanner.py:2327`) is an **8-connected holonomic point-robot Dijkstra** from the goal cell: it ignores heading *and* the nonholonomic R=0 pivot cost. Consequences for the nook:

- Two poses at the same `(x, y)` but opposite heading get the **same** `h`.
- A pose positionally near the goal column but **mis-oriented** — exactly the states a deep pivot-and-shuffle must pass through — is told "you are ~1 m away" when its true SE(2) cost-to-go is many maneuvering cusps. The search is mis-prioritised into flooding that pocket.

This matches the macro-probe evidence: the search reaches the **correct goal-x** but stalls **2.5 m short** even at fine `0.25 m/10°` / 40 k expansions — the residual cost is concentrated where heading guidance is missing, not where position guidance is.

> **Hypothesis under test:** replacing the position-only cost-to-go with a **heading-aware** one (a consistent SE(2) lower bound that respects the real primitive moves) collapses the expansion count enough to route the nook within the shippable budget. The strongest realization of that lever is **deterministic** (an exact backward-SE(2) Dijkstra), with a learned approximation as a deferred fallback — see §3.

This is **not** the ADR-0028 failure shape. #607 failed at *inventing a layout from scratch* (sparse, flat-in-clearance reward, no teacher). Here the signal is dense (SE(2) distance-to-goal is never flat) and a correct teacher exists — but, crucially, the recommended primary fix needs **no learning at all**, which is what makes it cheap and determinism-safe.

---

## 2. The question this spec answers

> Does a **heading-aware cost-to-go** make the deployed Hybrid-A* route the real
> `fk9_mkii ↔ cessna_140` insertion within the shippable per-fill expansion
> budget (`_MAX_FILL_EXPANSIONS = 16 000`) — deterministically, without shipping
> torch — and thereby unblock the Herrenteich all-8 auto-routing?

This is settled by a **cheap gated experiment first** (§4 Step 0), not by a build commitment. The deliverable of the spike is a yes/no with measured evidence.

---

## 3. Candidate analysis (why deterministic-first, ML-deferred)

An agent-team panel (4 lens-judges + adversarial refutation, 2026-06-26) weighed four methods. Summary of the verdict:

| Method | Verdict | Why |
|---|---|---|
| **Deterministic heading-aware field** — exact backward-SE(2) Dijkstra from the goal over the real primitive moves; used as the A* heuristic. **Primary pick.** | **BUILD (gated)** | Realizes the only mechanism with a confirmed lever (heading-awareness) with **zero ML**: RNG-free, no torch/ONNX, byte-identical-determinism-trivial, ships flag-gated like the just-refuted macro. A learned net can at best *approximate* this field, so the exact field both **bounds the headroom** and **is the better ship**. |
| **M1 — learned cost-to-go in the A\* loop** | **DEFERRED (and cannot ship in-loop)** | `f = g + h` breaks ties on `h`; onnxruntime CPU floats are not bit-identical across machines/runtime versions, so a learned `h` flips equal-`f` ordering and **violates ADR-0003 cross-machine byte-identity** — no golden re-base fixes a machine-dependent value. Learned guidance earns its keep only at *broad* generalization scope, or **offline / find-then-cache** (never in the shipped loop). |
| **M2 — learned policy as path generator (BC/DAgger)** | **REJECTED** | The ADR-0028 relapse: open-loop reproduction of a 97 k-expansion multi-cusp parallel-park is the long-horizon cold-start control skill #607 measured as unlearnable; "abstain" reproduces the `vp ≈ 0.333` place-then-give-up signature. ADR-0028 lists BC/DAgger on a narrow witness family as a do-not-reattempt oracle-masquerade axis. |
| **M3 — learned sampling bias / neural RRT** | **REJECTED** | Stacks an *unbuilt* RNG sampling planner (a new nondeterminism source in an RNG-free module) under ONNX-in-loop, in exactly the narrow-passage cm-precision regime where samplers are weakest. |

**Scope:** *narrow-de-risk-first* (unanimous). Crack the Herrenteich nook; defer any generalizing learned-motion stack — broad scope is precisely where the learned approach re-incurs ADR-0028 risk and a per-example 39-min-witness corpus.

### 3.1 The honest open question (the dissent, at full strength)

Even a **perfect** heuristic only *re-orders* the frontier — A* completeness still forces expansion of every state with `f* ≤ C*`. The residual ~57 k expansions **might be an irreducible plateau of near-C\* cusp states** inside the cm-precision R=0 parallel-park envelope, not heading-blind misranking. If so, **the entire heuristic class — deterministic field *and* learned M1 — is dead**, and the only narrow wins are caching the proven witness (brittle, hardcoded) or documenting a manual-insertion pair. **This is exactly why Step 0 is a ~1–2 h gate that runs before any build is funded.** Confidence: ~0.85 on the process (gate, scope, M2/M3 rejection, learned-in-loop-can't-ship); ~0.5 on whether the field actually cracks the nook — genuinely undecided pending the probe.

---

## 4. The gated, sequenced plan

### Step 0 — the gate: perfect-heuristic headroom probe (~1–2 h, NO ML)

Author a small **synthetic** 2-plane "parallel-park nook" fixture that reproduces the fk9↔cessna shape (goal pose laterally adjacent behind one parked high-winger in a short corridor) **in a tiny hangar so fine-grid A* routes in seconds, not the ~40-min real case**. Reuse the read-only spike harness that monkeypatches `_GRID_XY_M` / `_GRID_DEG` to `0.25 / 10°`. Run `plan_path` three ways and read `stats["expansions"]`:

- **(a)** today's 2D position-only `grid` geodesic (`_build_grid_heuristic`) — the deployed baseline;
- **(b)** an exact **backward-SE(2) Dijkstra** computed from the goal over the **same fine lattice** using the **reverse of `_primitives`**, keyed on the full 3D `_cell` `(ix, iy, iheading)` — the perfect consistent heading-aware lower bound (constructible because the pivot fan is finite and the primitive set is direction-symmetric);
- **(c)** the `euclidean` baseline (sanity).

Add a short confirmatory **frontier-composition log** (per expansion: on-final-path?, `g`, `h`, heading-vs-goal) to confirm the *mechanism* (position-close / heading-wrong floods) vs the *dissent* (a dense plateau of near-C\* on-path cusp states).

**Do NOT run the real ~40-min fk9/cessna/husky case in Step 0.**

#### Go / No-Go gate

The Step-0 ratio is a **cheap predictor**; Step 1's "routes the real pair under 16 k expansions" is the **ground-truth** gate. The probe sorts into three bands:

- **GO** — (b) collapses expansions by **≳ 50×** vs (a) on the synthetic nook ⇒ heading-awareness is the missing ingredient. Proceed to Step 1 with high confidence.
- **PARTIAL** (~5×–50×) — heading-awareness helps but may not suffice alone. **Proceed to Step 1, but the efficacy bar (route the real pair < 16 k) is the real arbiter** — do not over-read the synthetic ratio. If Step 1 misses the budget, treat it as an Efficacy kill (§5, kill condition 2), and consider whether a coarse-global / fine-local *adaptive* pass (the runner-up) closes the residual gap before abandoning.
- **NO-GO** — (b) only **~halves** (≲2×) expansions ⇒ the cost is intrinsic fine-SE(2) plateau volume inside the nook that no heuristic (learned or perfect) can prune. The deterministic field **and** learned M1 are both dead; fall to the kill-branch options (§6).

### Step 1 — if GO: build the deterministic heading-aware field (~1 day, no torch/ONNX)

Implement the exact backward-SE(2)-Dijkstra cost-to-go as a new `heuristic` mode (e.g. `"se2"`) in `plan_path` / `plan_fill`, **flag-gated, default-OFF** so every existing plan stays byte-identical (the ADR-0003 canaries are untouched). Validate on the **isolated real `fk9_mkii ↔ cessna_140` pair**:

- **Efficacy bar:** routes the pair under the shippable `_MAX_FILL_EXPANSIONS = 16 000` (ideally low-thousands), exact-oracle-validated by `path_first_conflict`.
- **Determinism:** double-solve byte-identity canary (the `determinism-guard` contract) — the field is RNG-free with a monotonic-counter tie-break, so this should be trivially clean.

If it clears both bars, **this is the win — ship it.** Then confirm the all-8 routes end-to-end (subject to §8).

### Step 2 — deferred: learned cost-to-go (only if the exact field is too slow to *build* at ship time)

A learned net *approximating* the Step-1 field, run **dev-only / offline** to find-then-cache a deterministic `MovesPlan` for the fixed corridor, re-validated by `path_first_conflict`. ONNX never enters the shipped loop; ADR-0003 preserved. This is the **only** place learning re-enters at narrow scope, and only if Step 1's exact field is intractable to compute rather than to *run*.

### Step 3 — deferred: broad generalization

A generalizing heuristic for unseen layouts (the superseded spec's S1/S2 ambition). Out of scope until the narrow case is cracked and generalization is separately chartered; gated by the same Step-0-style probe per new family.

---

## 5. Success & kill criteria (falsifiable)

**Narrow success (the spike verdict):** `hangarfit solve examples/herrenteich/layout.yaml --render-paths` (and `view`) auto-routes the **full all-8**, primarily by the new heading-aware field cracking the fk9↔cessna nook — deterministically, shipping without torch in the wheel.

**Kill conditions** (any one ends the corresponding track):

1. **Gate kill** — Step-0 probe: the exact backward-SE(2) field cuts expansions only ~2× (not ≳50×) ⇒ the ~97 k is intrinsic plateau volume, not the unguided heading dimension. A learned `h` can never beat the exact field, so the whole heuristic class is dead.
2. **Efficacy kill** — Step 1: even with Step-0 headroom, the field does **not** route the isolated real pair under ~16 k expansions ⇒ corridor-confined cost still exceeds the shippable budget.
3. **Learned-in-loop kill** (already established) — a learned `h` running inside the shipped planner loop cannot preserve ADR-0003 cross-machine byte-identity (onnxruntime float ties). Learned `h` may ship only offline/find-then-cache.
4. **Redundancy kill** (narrow-scope learned M1) — for the *fixed* Herrenteich scenario the only label source for a learned cost-to-go is the 39-min witness, the same run that already *produces* the deployable path; caching that witness dominates narrow-scope learned M1 with zero ML.
5. **Scope kill** — do not broaden to BC/DAgger / scenario-generator / held-out criteria until the narrow case is cracked (ADR-0028 do-not-reattempt axis).

---

## 6. Kill-branch fallback (if Step 0 returns NO-GO)

The heuristic class is dead. The honest narrow-scope options, in preference order, become:

1. **Cache the proven witness** — store the 39-min fine-grid `MovesPlan` for the fk9↔cessna pair, re-validate it with `path_first_conflict` at load. Zero ML, ships clean, but **brittle/hardcoded** to the fixed scenario and does not advance the planner.
2. **Document the manual-insertion pair** — accept that the club hand-shuffles this pair on own gear and record the all-8 as a known manual-insertion case in `CLAUDE.md` / the spike doc.

These are *fallbacks*, not the plan. The reason Step 0 runs first is precisely to learn — for ~1–2 h — whether a principled, generalizable deterministic fix is cheaply within reach before settling for either.

---

## 7. Determinism & shippability constraints (standing requirements)

- **ADR-0003 byte-identity.** Any change to `towplanner.py` is reviewed by the `determinism-guard` subagent. The new heuristic mode is default-OFF and RNG-free; the existing canaries must stay byte-identical, and the new mode gets its own double-solve canary.
- **No torch in the wheel.** `ml/` is a top-level dev/CI-only package; a learned component reaches the product only via the #706-style ONNX seam (verifier-gated, `[learned-infer]` extra). The Step-1 deterministic field needs **none** of that.
- **Validity oracle = product checker.** `collisions.check` + `path_first_conflict` (+ Caddy egress where relevant) remain the single source of truth; the heuristic only re-prioritises the frontier and never makes a pose un-expandable or alters which paths are *valid*.

---

## 8. The separable husky ordering blocker (success-incompleteness guard)

None of the methods here touches the `aviat_husky` front-cluster **ordering** blocker — it is an order-search problem, not the fine-grid nook. Faster per-plane routing only *indirectly* funds more order permutations. **Before declaring #840 success**, confirm via `plan_fill` on the all-8 (with the fk9↔cessna route stubbed-instant) that the husky permutation search also resolves. If it does not, that is a separate, clearly-scoped follow-up — not a regression of this work.

---

## 9. Decisions captured (from the panel + this brainstorm)

- **Primary method:** deterministic exact backward-SE(2)-Dijkstra heading-aware cost-to-go (chosen over learned guidance, which cannot ship in-loop and is dominated by the exact field at narrow scope).
- **Mechanism, not resolution:** the lever is the unguided heading dimension, confirmed in `towplanner.py:2515` + `_build_grid_heuristic`. Plain resolution-refinement (the runner-up) attacks search-depth *outside* the nook — the wrong cost term — so it is folded into the same Step-0 gate rather than pursued separately.
- **Discipline:** de-risk gate (~1–2 h, no ML) before any build; narrow scope; learning deferred.
- **Explicitly NOT done:** re-running the refuted analytic parallel-park **macro** (it added longer-range *lattice* moves, not heading-awareness — already a measured NO-GO at 8 k/16 k/32 k budgets).

---

## 10. Open questions for the implementation plan

1. **Backward-primitive construction.** The exact inverse of each `_primitives` leg under `_step_pose` (forward L/S/R + reverse L/S/R) and the heading-bin resolution for the backward lattice — must be a *consistent admissible* lower bound (cost ≤ true) so the heuristic only re-prioritises (mirrors the `_build_grid_heuristic` admissibility guarantee).
2. **Field memory/compute cost.** A 3D `(ix, iy, iheading)` Dijkstra over the fine lattice is `_HEADING_BINS`× the 2D field's states — bound the build cost and confirm it is paid **once per goal** (as the 2D field already is), not per node.
3. **Synthetic fixture fidelity.** The toy nook must reproduce the *binding* geometry (lateral offset behind a parked high-winger, corridor width) closely enough that the Step-0 ratio transfers to the real pair — without inheriting the real case's ~40-min cost.
4. **Heuristic-mode naming & seam.** New `heuristic="se2"` (or similar) vs extending `"grid"`; CLI exposure (currently grid/budget are module constants, not flags) — decide whether the new mode needs a `solve`/`view` flag or stays an internal default-OFF seam for now.
