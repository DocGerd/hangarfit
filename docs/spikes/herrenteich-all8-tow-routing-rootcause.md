# Why the Herrenteich all-8 would not auto-tow-route — root-cause investigation

**Status:** root cause #1 **confirmed and fixed** (#842, PR #843); a second, separable
factor **characterised** and handed to a follow-up (#844) — now **closed NO-GO (2026-06-27)**: a
documented manual-insertion case, with the dense-fleet relaxation tracked on #667. **Date:**
2026-06-26 (investigation); 2026-06-27 (Factor-2 closeout).

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
| **Factor 2** | Even with Factor 1 fixed, the **full** all-8 does not auto-route. Narrowed to one pair: `fk9_mkii` and `cessna_140` (both genuine high-wingers) **mutually block** near the door — each space-exhausts against the other at the 0.5 m/15° grid, so no monotone order places both. A finer-grid sweep walled inconclusively (search too slow at the time), making **search resolution** the prime suspect. **Update (#844): confirmed** — a 0.25 m/10° own-gear A\* later **found** a no-carts path (96 949 exp, 39 min), so a feasible own-gear path provably exists and the deployed coarse grid simply can't represent the deep lateral shuffle. See [`herrenteich-fk9-cessna-lateral-shuffle.md`](herrenteich-fk9-cessna-lateral-shuffle.md). | **Characterised**, then **RESOLVED → NO-GO (#844 closed 2026-06-27)** — a **search-efficiency** problem (feasible path exists, too deep/slow at the deployed grid) that no shippable fix closes (#840 / #844a / #844b all NO-GO); now a documented manual-insertion case, dense-fleet relaxation on #667. Separable from the fidelity bug; not another physics error. | medium → high |

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

> **Update (2026-06-26, #844):** a follow-up witness-first probe has **grounded** Factor 2 — see
> [`herrenteich-fk9-cessna-lateral-shuffle.md`](herrenteich-fk9-cessna-lateral-shuffle.md). The
> "resolution is the prime suspect" framing below is **VINDICATED, not refuted**: own-gear A\* at
> **0.25 m/10° finds a no-carts path** (96 949 exp, 39 min, exact-oracle-validated) while the deployed
> 0.5 m/15° grid finds none — so a feasible own-gear path provably **exists** and the coarse grid
> simply can't represent the deep lateral-displacement ("parallel-park") shuffle that threads the
> corridor. #844 is therefore a search-**efficiency** problem (the find is too deep/slow to ship), not
> a feasibility or fidelity gap. Carts route the pair cheaply at the coarse grid — the **diagnostic**
> that isolates *lateral displacement* as the crux, not the fix (the club hand-shuffles on own gear).
> Ruled out as the ship-fix: carts/`on_carts` (unfaithful), pivot-point fidelity, and
> finer-grid-*everywhere* / raised-budget-alone (too slow). The all-8 also has a *second* blocker
> (husky ordering). The Factor 2 narrative below is the **historical investigation** as it stood
> before that find; read the follow-up doc for the current conclusion.

With Factor 1 fixed, the husky↔Scheibe block is gone, but the **whole** all-8 still does not
auto-route under `plan_fill`. A long series of targeted experiments narrowed the residue to **one
specific pair**:

- **Natural deepest-first order (`back_first_order`).** Each plane tested against its exact
  predecessors (8 parallel `plan_path` calls): **5 of 8 route** (zlin, scheibe, wild_thing, stemme,
  ctsl — ctsl even routes against all 7), but the **front-door** planes `aviat_husky`, `fk9_mkii`,
  `cessna_140` block.
- **All eight are R=0 (pivot).** husky/fk9/cessna are `tow_pivotable`, so even off-carts their
  effective turn radius is 0 — the front-cluster block is **not** a turn-radius problem.
- **husky is purely an ORDER issue.** It routes past `{zlin, scheibe}` (1066 expansions) but
  exhausts once `wild_thing` is also placed; placing husky earlier fixes it.
- **The linchpin: `fk9_mkii` and `cessna_140` MUTUALLY block.** Run to completion (no time wall):
  `fk9` routes past `{zlin, scheibe}` instantly (0 exp) and past `{zlin, scheibe, husky}` (6720 exp,
  ~6 min) — but **genuinely space-exhausts** (10184 exp, complete search) against any predecessor
  set that contains `cessna`; symmetrically `cessna` exhausts (11174 exp) against any set containing
  `fk9`. So at the 0.5 m/15° grid **no monotone order can place both** — the second one in is always
  walled by the first.
- **The block is intrinsic to the pair.** It holds with **nothing else present** — `fk9` exhausts
  against `cessna` alone (10184) and vice-versa (11174), independent of the Scheibe and of crowding.
  Running the all-7 **without** the Scheibe does **not** route the front cluster either.
- **Both are genuine high-wingers** (`fk9` wing z[1.9,2.2], `cessna` z[2.0,2.3]) — a fleet audit
  confirms neither is a Scheibe-style miscoding. Their tow-time wing interaction is **physical**:
  two real high wings cannot overlap, and threading one tug-path past the other near the door needs
  fine lateral precision.
- **Not a coarse wing model.** Unlike the Scheibe, fk9/cessna wings are plain rectangles, so a
  natural suspect was tip overstatement. But tapering **both** wings rectangle→glider (tip ratio
  1.0 → 0.45) leaves them exhausting (~10184–10638) — the wing-tip shape is not the binding
  geometry. So this is a *search* limit at the corridor, not a fixable *model* coarseness.
- **Resolution is the suspected lever but unconfirmed.** `fk9` *provably* has no path at 0.5 m/15°
  (exhausted), but a sweep at 0.25 / 0.20 / 0.15 m **walled at 600 s without settling** — at a finer
  grid A* would usually find an existing path fast, so a wall is "no path **or** search too slow",
  not a proof either way.
- **The deployed planner can't brute-force it.** `plan_fill` with a 7.5×-raised per-plane budget
  (60 k) and a 250×-raised global budget (4 M) **walled at 40 min** without routing — its
  deepest-first order is wrong for the front cluster, and the fk9↔cessna mutual block sends the
  order-backtracking into an expensive, non-converging search.

**Interpretation.** The dominant blocker was Factor 1 (now fixed). The residual is a single tight
maneuvering corridor between two real high-wingers at the door. Physically a monotone fill exists
(the club parks all eight daily, "with only a few cm between planes at certain parts"), and the
tow **motion** clearance is just 0.05 m — so the real maneuver threads with ~cm precision that a
0.5 m / 15° grid simply **cannot represent**. The most likely truth is that finer resolution would
route it, but the current Hybrid-A* is too slow to confirm that at a cm-scale grid within practical
time. This is a **search-efficiency** problem, handed to #844; candidate directions:

1. **Adaptive / finer grid near tight corridors** (the user's "few cm / finer heading" intuition) —
   the prime suspect, blocked only by search speed today.
2. **A front-cluster-aware entry order** (husky before wild_thing; fk9/cessna ordered to avoid the
   mutual block) rather than strict deepest-first.
3. **Per-plane budget tuning** (the default 8000 is far below the ~7–15 k these searches need).

A learned tow-motion policy (the original spike direction, #840) is the longer-term lever for
exactly this "thread the cm-scale corridor" search; this investigation sharpens its target to the
front-door `fk9`↔`cessna` pair.

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
| **Search resolution too coarse** (the user's second suspicion) | Re-ran the *husky↔scheibe* block at 0.2 m/5°; swept the *fk9↔cessna* block at 0.25 / 0.20 / 0.15 m | **Discarded for husky↔scheibe** (still space-exhausted with the wing high → that was fidelity, not resolution). **Confirmed the lever for fk9↔cessna** (proven no path at 0.5 m/15°; **0.25 m/10° later found a no-carts path**, #844 — so finer grid represents the deep shuffle, the search is just too slow to ship). See Factor 2 + the follow-up doc. |
| **Single-tree RRT / RRT-Connect oracle** would route it | Prototyped both | **Discarded** — weaker than the shipped `plan_path` Hybrid-A* in this clutter (goal tree trapped; ~2 % extend success) |
| **Multi-body "move aside" needed** | Checked whether a parked plane must move | **Discarded** — the club never moves parked planes; the fill is monotone. The block was fidelity, not a need to relocate |
| **A second wing-z fidelity bug** in the front cluster | Fleet-wide wing-z audit | **Not found** — scheibe is the only outlier; the front-cluster block reads as order/search, not another physics error |
| **Coarse rectangular wing model** (fk9/cessna wings are plain rectangles, not tapered like scheibe — tip overstate causing a false conflict?) | Tapered **both** fk9 & cessna wings rectangle→glider (tip ratio 1.0 → 0.85 → 0.70 → 0.55 → 0.45, the ADR-0024 hexagon) and re-ran the isolated pair | **Refuted** — every taper still space-exhausts (~10184–10638, vs 10184 for the rectangle). The wing-tip shape is not the binding geometry; tapering does not open the corridor |
| **The Scheibe is the complication** (its up/down wing ambiguity blocks the fill) | Ran the all-7 **without** scheibe (deepest-first), and the fk9↔cessna pair in **isolation** | **Falsified as the residual cause** — without scheibe the front cluster (husky/fk9/cessna) **still blocks**, and fk9↔cessna mutually exhaust with **nothing else present**. Scheibe (Factor 1) was a *separate* earlier blocker, now fixed; the residual is independent of it |

---

## Confidence

- **Factor 1 (fidelity bug + fix): ~95 %.** The mechanism is understood, the per-pair experiments
  are clean and reproducible, the fix is shipped with a regression test, and both real layouts stay
  valid. The 5 % is residual model risk (the synthetic hangar is still a placeholder; the wing-band
  height is a tilt *approximation*, defensible but not tape-measured).
- **Factor 2 (fk9↔cessna front-door corridor): medium; subsequently RESOLVED → NO-GO (#844 closed
  2026-06-27).** A feasible monotone fill provably exists (real-world daily use, "a few cm" spacing,
  0.05 m tow clearance), but the deployed planner cannot find it: the two real high-wingers mutually
  block at 0.5 m/15°, and a finer grid — the prime suspect — is too slow for the current Hybrid-A* to
  settle. Every follow-up gate returned NO-GO — the heading-aware SE(2) heuristic (#840: ~12% *worse*,
  an intrinsic near-C\* A\* plateau), the husky-ordering gate (#844a: genuine but insufficient — KILLED,
  the determinism-contract / `scene.py` timeline-order / perf-rebaseline cost outweighing a
  diagnostic-only, non-routing gain), and the continuous-traj-opt Gate 0 (#844b: dominated). The pair is now a **documented
  manual-insertion case**, and the architectural relaxation (lift monotone fill for dense fleets) is
  tracked under #667. This was a **search-efficiency** task (resolution / order / budget), **not** a
  claim that the all-8 routes end-to-end, and **not** evidence of another physics error (both planes
  are correctly modelled).

**Bottom line:** the use case was blocked by a real, fixable model bug (Factor 1), now fixed and the
dominant cause. The remaining gap is a single tight front-door maneuvering corridor that the deployed
grid search can't thread — a planner-efficiency problem that proved intractable to every search-side
fix (all NO-GO; #844 closed 2026-06-27), so the pair is a documented manual-insertion case and the
dense-fleet relaxation is parked on #667, rather than overclaimed as solved.
