# Why the Herrenteich all-8 would not auto-tow-route — root-cause investigation

**Status:** root cause #1 **confirmed and fixed** (#842, PR #843); a second, separable
factor **characterised** and handed to a follow-up. **Date:** 2026-06-26.

> The real Airfield Herrenteich hangar parks all eight usual occupants every day, by a
> *monotone fill* — planes go in one at a time and an already-parked plane is never moved.
> The tool agreed the final layout is **statically valid** (`hangarfit check` passes), yet it
> could **not** auto-tow-route the fill (`plan_fill` / `view --animate` failed or fell back to
> static). Because that use case is the reason the project exists, the brief was: find out why,
> to ≥95 % confidence, with a proposed fix and an honest comparison to the options ruled out.

This writeup records the method, the two factors found, every hypothesis falsified along the
way, and the confidence in each conclusion.

---

## TL;DR

| | Finding | Status | Confidence |
|---|---|---|---|
| **Factor 1** | The Scheibe SF-25 (a real **low-wing** glider) had its 18 m wing modelled in the **high-wing z-layer** (`z[1.9,2.1]`), the same layer as the genuine high-wingers' wings. Towing a high-winger (e.g. `aviat_husky`, wing `z[2.0,2.3]`) past the parked Scheibe then hit a **phantom wing-vs-wing collision** with no real-world counterpart. | **Fixed** — wing re-modelled as a thin band `z[1.72,1.78]` below the high-wing layer (#842, PR #843). | **~95 %** |
| **Factor 2** | Even with Factor 1 fixed, the **full** all-8 does not route in `plan_fill`'s natural *deepest-first* order: a front-door cluster (`aviat_husky`, `fk9_mkii`, `cessna_140`) mutually blocks, and the planner's order-backtracking does not converge in reasonable time (>20 min). | **Characterised**, not solved — handed to a follow-up. It is an **order / planner** problem, separable from the fidelity bug. | medium |

The headline: **at least one definite model-fidelity bug made the use case impossible**, and it
is fixed. A second factor — finding a *feasible monotone order* under the deployed planner —
remains and is a planner/search problem, not (so far as measured) another physics error.

---

## Method

Per the brief ("use agent teams for everything and question everything"), every hypothesis was
treated as falsifiable and tested empirically, not argued. Concretely:

- **Measure-first.** Each candidate cause got a dedicated experiment that could *kill* it. The
  32-core machine ran experiments in parallel (forkserver workers, BLAS pinned to 1 thread per
  worker, per-candidate wall caps).
- **Per-pair before whole-fill.** The two-body interactions were isolated first (mover P vs a
  fixed predecessor set) so a failure could be attributed to a specific pair, not lost in the
  8-body fill.
- **Feasibility-grounded.** The real `examples/herrenteich/layout.yaml` is a *proven-valid*
  witness (it passes `collisions.check`), so "the planner can't route into it" is a genuine
  planner gap, never a vacuous test of an impossible layout.

---

## Factor 1 — the Scheibe wing was in the wrong z-layer (CONFIRMED, FIXED)

### The mechanism

`hangarfit`'s collision model is a **parts model with z-layering**: a high wing may overhang a
lower part if their z-ranges do not overlap (within `wing_layer_clearance_m`). The Scheibe
SF-25E is a real **low-wing** motor glider, but its catalog entry modelled the 18 m wing at
`z[1.9,2.1]` — explicitly as a "monowheel-tilt approximation" — which placed an 18 m wing in the
**same vertical layer** as the genuine high-wingers' wings (`aviat_husky` `z[2.0,2.3]`).

Statically this is fine: in the parked layout the Scheibe and husky wings don't overlap in plan
view, so no conflict fires. **During the tow it is fatal:** the husky tow path crosses over the
Scheibe's parked footprint, the two wings overlap in plan view, and — both being in the same
z-layer — the checker reports a wing-vs-wing collision. In reality the husky's high wing simply
**overhangs** the Scheibe's low wing; the model manufactured a block that does not physically
exist, and it made the valid all-8 **un-tow-routable**.

### Evidence (measured)

| Experiment | Result |
|---|---|
| `aviat_husky` tow past `{zlin, scheibe@HIGH z[1.9,2.1]}` | **BLOCKED** — search space-exhausted, even at a finer **0.2 m / 5°** grid (so *not* a resolution wall) |
| `aviat_husky` tow past `{zlin, scheibe@LOW}` | **ROUTABLE** (~1 k node expansions) |
| Fleet-wide wing-z audit | `scheibe_falke` is the **only** wing-z outlier; every other "high" wing belongs to a genuine high-wing aircraft |
| Static validity of both bundled layouts with the lowered wing | **valid** (`layout.yaml` and the dense `layout_today.yaml`) |

### The fix (shipped, PR #843)

Model the wing as a **thin keep-out band `z[1.72,1.78]`**, centred at 1.75 m in the narrow
vertical corridor:

- **Floor 1.72 m** clears the 1.5 m fuselage tops it overhangs (in the dense `layout_today.yaml`
  the wing overhangs the *aft* section of the zlin/husky fuselages) by 0.22 m.
- **Ceiling 1.78 m** sits 0.22 m below the high-wing layer (`aviat_husky` wing bottom 2.0 m), so
  high-wingers correctly overhang it and tow past.

The band is **thin and centred** rather than 0.20 m thick because this catalog entry is *shared*
between the Herrenteich hangar (`wing_layer_clearance_m: 0.15`) and the synthetic `data/hangar.yaml`
(`0.20`); a band with exactly-0.20 m gaps would false-conflict under the synthetic hangar on
float (`1.70 - 1.50 == 0.19999…96 < 0.20`, and the checker compares `gap < clearance` with no
tolerance). The 0.22 m gaps clear both. A new regression test
(`test_scheibe_wing_sits_below_the_high_wing_layer`) pins the z-layering invariant, which is
static-validity-neutral and so would not be caught by the existing layout tests.

The taper planform (ADR-0024 / #541) and the render colour (`wing_position: high`) are unchanged
— collision z-layering reads the explicit part z-values, not the `wing_position` label.

---

## Factor 2 — a feasible monotone order is not found by the deployed planner (CHARACTERISED)

With Factor 1 fixed, the husky↔Scheibe block is gone, but the **whole** all-8 still does not
auto-route under `plan_fill`. The evidence:

- **Natural deepest-first order (`back_first_order`).** Testing each plane against its exact
  predecessors in that order (8 parallel `plan_path` calls): **5 of 8 route** (zlin, scheibe,
  wild_thing, stemme, ctsl — ctsl even routes against all 7 predecessors), but **`aviat_husky`,
  `fk9_mkii`, `cessna_140` block** (space-exhausted at the default 0.5 m/15° grid). These are all
  **front-door** planes (low *y*).
- **It is an order interaction, not (visibly) more physics.** `aviat_husky` routes past
  `{zlin, scheibe}` but **blocks** once `wild_thing` is added — i.e. `wild_thing` walls the husky
  corridor in this order. A different relative order of the front cluster is needed.
- **`plan_fill` backtracks over order (#667) but does not converge.** Given a generous budget it
  ran **>20 min** without completing — the front-cluster planes are expensive both to route and
  to *disprove*, so the order-backtracking burns budget on failed branches.
- **Resolution is inconclusive for the front cluster.** A grid-resolution sweep on the confirmed
  blocker (`cessna_140` vs its 6 predecessors) space-exhausted at 0.5 m/15° but the finer grids
  (0.33 → 0.15 m) did not *finish* within a 500 s wall — so finer resolution is neither confirmed
  nor refuted there; it is simply too slow to settle this way.

**Interpretation.** Physically a monotone order exists (the club parks all eight every day). The
gap is the deployed planner *finding* it: `back_first_order` is the wrong order for the front
cluster, and the order-backtracking + per-plane budget are not tuned to converge on the tight
front corridors. This is a **planner/search** problem. It is handed to a follow-up; candidate
directions (in rough order of expected value):

1. **A better entry order than strict deepest-first** for the front cluster (e.g. order by corridor
   tightness, or a front-cluster-aware tiebreak), so greedy routes without deep backtracking.
2. **Per-plane budget / wall tuning** so a tight-but-feasible front plane actually routes instead
   of spuriously failing and triggering backtracking.
3. **Finer grid *only* where needed** (adaptive resolution near tight corridors) — the user's
   "a few cm / finer heading" intuition, which the per-cluster sweeps could not yet settle because
   a uniform fine grid is too slow.

A learned tow-motion policy (the original spike direction, #840) is the longer-term lever for
exactly this "thread the tight corridor / pick the order" search; this investigation sharpens its
target to the front-door cluster.

---

## Hypotheses tested and discarded

Every one of these was a live suspect; each was **falsified** by a dedicated experiment. This is
the "comparison to other options discarded" the brief asked for.

| Hypothesis | How tested | Verdict |
|---|---|---|
| **Turn-radius model wrong** (the user's first suspicion) | All eight Herrenteich movers are `R=0` (cart/pivotable); checked the pivot primitives are faithful at `R=0` | **Discarded** — `R=0` pivot is faithful; not the block |
| **Pivot point / cart strafe / reverse** mis-modelled | Exercised cart-strafe and reverse seeds in isolation | **Discarded** — present and correct |
| **Aircraft dimensions wrong** | Re-checked spans/lengths against EASA TCDS / published specs | **Discarded** — dimensions match sources |
| **Clearances too strict** | Swept clearance; the all-8 is valid at the calibrated 0.10/0.15 m (and motion 0.05) | **Discarded** — not the binding constraint |
| **Search resolution too coarse** (the user's second suspicion) | Re-ran the *husky↔scheibe* block at 0.2 m/5° | **Discarded for that pair** (still space-exhausted with the wing high → it was fidelity, not resolution). **Open for the front cluster** (finer-grid runs too slow to settle — see Factor 2). |
| **Single-tree RRT / RRT-Connect oracle** would route it | Prototyped both | **Discarded** — weaker than the shipped `plan_path` Hybrid-A* in this clutter (goal tree trapped; ~2 % extend success) |
| **Multi-body "move aside" needed** | Checked whether a parked plane must move | **Discarded** — the club never moves parked planes; the fill is monotone. The block was fidelity, not a need to relocate |
| **A second wing-z fidelity bug** in the front cluster | Fleet-wide wing-z audit | **Not found** — scheibe is the only outlier; the front-cluster block reads as order/search, not another physics error |

---

## Confidence

- **Factor 1 (fidelity bug + fix): ~95 %.** The mechanism is understood, the per-pair experiments
  are clean and reproducible, the fix is shipped with a regression test, and both real layouts stay
  valid. The 5 % is residual model risk (the synthetic hangar is still a placeholder; the wing-band
  height is a tilt *approximation*, defensible but not tape-measured).
- **Factor 2 (front-cluster order): medium and explicitly open.** A feasible monotone order
  provably exists (real-world daily use), but the deployed planner does not yet find it in
  reasonable time. This is a planner/search task, handed to a follow-up — not a claim that the
  all-8 now routes end-to-end.

**Bottom line:** the use case was blocked by a real, fixable model bug, now fixed. Making the full
all-8 route end-to-end additionally needs a planner-order improvement, which is scoped and handed
off rather than overclaimed.
