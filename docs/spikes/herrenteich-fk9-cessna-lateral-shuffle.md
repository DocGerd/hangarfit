# Factor 2 (fk9↔cessna front-door corridor): a **search-efficiency** problem — a feasible own-gear path exists, too deep for the deployed grid

**Status:** witness-first feasibility probe — **complete**; #844 reframed from a speculative
"resolution" suspicion to a **grounded** search problem. **Date:** 2026-06-26. **Issue:** #844.

> Follow-up to [`herrenteich-all8-tow-routing-rootcause.md`](herrenteich-all8-tow-routing-rootcause.md).
> Factor 1 (the Scheibe wing-z miscoding) is fixed (#842, PR #843). Factor 2 is the residual: the two
> genuine high-wingers `fk9_mkii` and `cessna_140` **mutually space-exhaust** at the door on the
> deployed 0.5 m/15° tow grid, so no monotone fill order places both. The root-cause doc named **grid
> resolution** the *prime suspect* and handed it to #844 **without proof**. This probe tested that
> suspicion — feasibility-first, on the real oracle — and found the binding constraint is more
> specific, and the cheap "just refine the grid" fix is **not** it.

---

## TL;DR

A **feasible own-gear tow path provably exists** — finer-grid A* finds one (no carts) — so #844 is a
**search-*efficiency*** problem: the deployed 0.5 m/15° grid is too coarse to represent the deep
pivot+forward shuffle that threads the corridor, and finer grid finds it but **far too slowly to
ship**. Not a phantom collision, not a fidelity bug, not infeasibility.

| # | Finding | Evidence | Confidence |
|---|---|---|---|
| 1 | The corridor is **geometrically open** | A holonomic (lateral-allowed) flood-fill reaches the door from `fk9`'s goal at 0.2 m/15° with a 64-pose witness that densely re-validates | high |
| 2 | The **missing capability is lateral displacement** | `fk9`/`cessna` **on carts** (strafe-capable) route the isolated pair **in either order at the coarse grid**: cessna 196 exp / 2.5 s, fk9 1613 exp / 18 s — vs **no path** on own gear at that grid | high |
| 3 | The club **hand-shuffles fk9/cessna on own gear** (no dollies) | user-confirmed (2026-06-26); `layout_today.yaml` models them `on_carts: false` | given |
| 4 | On own gear, lateral displacement is a **deep pivot+forward "parallel-park" shuffle** | castering gear has fixed main wheels → physically cannot strafe; the model's pivot-only own-gear motion is *correct* | high |
| 5 | **A feasible own-gear path EXISTS — finer grid finds it, but impractically slowly** | own-gear A* at **0.25 m/10° FINDS a path** (exact-oracle-validated): 96 949 expansions, 39 min — vs the coarse 0.5 m/15° grid's no-path and carts' 1613 exp. Path exists; it is just very **deep** | high (feasibility); the cost is a lower bound |
| 6 | **Pivot-point fidelity is not the gap** | mains within ~0.5 m of the pose reference origin → model pivot center is a faithful approximation | high |
| 7 | The charter all-8 has a **second** deep-search blocker | `plan_fill` (baseline and cart{fk9,cessna}) both bail on **husky** (an ordering issue), not the pair | high |

**Headline:** the carts experiment is the **diagnostic** (it isolates *lateral displacement* as the
crux); the finer-grid own-gear find is the **feasibility witness** (a real no-carts path exists). So
the fix is a **search-efficiency** improvement that makes the proven-but-deep own-gear shuffle
findable *fast* — the spike doc's "resolution" hypothesis, now **grounded and vindicated**, with the
open work being speed, not existence.

---

## Method (feasibility-grounded, on the real oracle)

A read-only scratch harness drives the **production tow oracle** (`plan_path` — Hybrid-A* +
Reeds–Shepp, the same entry cone, 0.05 m motion clearance, and grid heuristic as `plan_fill`) on the
**isolated 2-body subproblem** (`fk9` vs `cessna`, against their goal poses in
`examples/herrenteich/layout.yaml` — the tool's valid all-8 *nested* arrangement, a proven-valid
witness, not a record of the club's actual parking; the club's own-gear handling is corroborated
separately by `layout_today.yaml`, where fk9/cessna are likewise `on_carts: false`). Grid step
(`_GRID_XY_M`/`_GRID_DEG`) and budget are monkeypatched per run; nothing shipped changes (the
determinism contract is untouched). The strafe lever is clean: `mover_on_carts=True` adds Reeds–Shepp
**lateral** primitives but does **not** change the footprint geometry (`aircraft_parts_world` derives
parts from pose only; `_motion_clear` fixes `on_carts=False` for geometry), so carts-vs-own-gear
isolates *motion model* against *identical collision geometry*.

**Harness fidelity anchor.** The harness reproduces the root-cause doc's **fast** known case
*exactly* — `fk9` own-gear vs `{zlin, scheibe}` routes in **0 expansions** (immediate analytic shot)
— and reproduces its **verdicts** (husky routable; the own-gear pair: no path). Exact expansion
counts on the slower cases drift (≈2.4× on the husky case, ≈0.7× on `fk9` vs `{zlin,scheibe,husky}`)
in both directions — a heuristic/tie-break config detail. **All conclusions rest on verdicts
(found / no-path), not counts.**

## The probes

**Probe A — holonomic corridor open.** A free-neighbour `(x,y,θ)` flood-fill (the relaxation of
`plan_path` with the kinematic constraint removed, using the same `_motion_clear` oracle) reaches the
door from the goal at 0.2 m/15°, with a 64-pose witness that re-validates at 5× sub-grid sampling.
The corridor is not geometrically sealed. (Holonomic allows world-axis strafe, so *reachable* here is
not by itself a no-strafe feasibility proof; its value is the negative direction, which did not fire.)

**Probe B/C — carts route, own gear does not.** Isolated pair, 0.5 m/15° grid:

| Mover (vs the other parked) | Motion model | Verdict |
|---|---|---|
| `cessna_140` **on carts** vs parked `fk9` | strafe-capable | **routes** — 196 exp, 2.5 s |
| `fk9_mkii` **on carts** vs parked `cessna` | strafe-capable | **routes** — 1613 exp, 18 s |
| either, **own gear** (`tow_pivotable`, pivot-only) | no strafe, coarse 0.5 m/15° | **no path** (own gear exhausts; root-cause doc: 10184 / 11174 complete) |
| `fk9` **own gear** vs parked `cessna` | no strafe, **finer 0.25 m/10°** | **routes** — 96 949 exp, 39 min (a *feasibility witness*: the no-carts shuffle exists) |

Both the carts paths and the finer-grid own-gear path are exact-oracle-validated (`found=True` ⇒
`path_first_conflict` accepted the arc): genuine collision-free tow paths under the 0.05 m motion
clearance. The own-gear find at 0.25 m is the decisive result — it proves a feasible monotone-fill
path exists **without carts**, so the problem is search *efficiency*, not feasibility. (Finer 0.15 m
and the reverse `cessna`-mover order were also launched; the 0.25 m `fk9` find already settles
existence, and its 97 k-expansion cost is a lower bound on how deep the shuffle is.)

## Why the fix is a search problem (the physics)

`fk9` (nosewheel) and `cessna` (tailwheel) have **fixed main wheels**: on their own gear they roll
fore/aft and pivot about the main gear, but sideways motion would skid the mains — they **physically
cannot strafe**. The model's pivot-only own-gear motion is therefore *correct*, not over-conservative.
Lateral displacement on own gear is achievable only as a **deep pivot+forward parallel-park shuffle**
— already in the planner's Reeds–Shepp repertoire (R=0 pivot + straight + reverse), but a long
multi-cusp move-sequence the deployed grid cannot find. The carts result shows the corridor *accepts*
a direct lateral slide; the own-gear search has to *emulate* that slide with a deep shuffle, and that
is the wall.

The probe **eliminates the cheap exits**:

- **Carts / `on_carts: true`** — rejected: the club hand-shuffles on own gear (Finding 3); marking
  them carted would route the tool but **misrepresent reality**.
- **Pivot-point fidelity** — ruled out (Finding 6): the R=0 pivot rotates about the pose reference
  origin while the real tail-/nose-lift shuffle pivots about the main gear, but the mains sit within
  ~0.5 m of the reference (`fk9` `main_offset_x_m = -0.10`, `cessna` `+0.50`), so the approximation is
  faithful enough — no cheap fidelity fix hides here.
- **Infeasibility** — ruled out (Finding 5): own-gear A* at 0.25 m/10° **finds** a real no-carts path
  (96 949 exp, 39 min). The own-gear shuffle exists; it is just **deep** (a long move-sequence the
  coarse 0.5 m/15° grid can't represent), so the open problem is search **speed/efficiency**, not
  existence.

## The all-8 has a second blocker (husky ordering)

Running production `plan_fill` on the full all-8 (grid heuristic, per-plane 20 k, global 80 k):
baseline **and** cart{fk9,cessna} both fail, bailing on **`aviat_husky`** (`global fill budget
exhausted`, 18 / 22 min) — *before* the carted pair is cleanly exercised. Husky is `always_own_gear`
(can't be carted) and is the root-cause doc's "purely an order issue" (routes if placed before
`wild_thing`); the #667 backtracking order search can't find that order within 80 k expansions because
per-plane routing is slow (tens of s each), so the budget funds only a few permutations. **Routing
the charter all-8 needs both the fk9↔cessna lateral shuffle and a faster/smarter order search.** The
isolated-pair experiments remain the clean evidence for the scoped Factor-2 linchpin.

---

## What this means for #844 (the fix space)

The faithful fix makes the planner **find the proven-but-deep own-gear shuffle *fast***. A feasible
path exists (Finding 5) but costs ~97 k expansions / 39 min at 0.25 m — far above the 8000 default
per-plane budget and impractical for `solve`/`view`. The candidates, all `towplanner` changes that
**determinism-guard binds** (ADR-0003 byte-identical plan):

1. **Adaptive grid near tight corridors** (deterministic) — refine resolution *only* in the corridor,
   so the deep shuffle is representable without exploding the global state space. Directly motivated
   by Finding 5 (finer grid finds it; the cost is paying for fine resolution *everywhere*).
2. **Analytic parallel-park maneuver injection** (deterministic) — give the search a closed-form
   multi-cusp lateral shuffle it tries at each node, instead of discovering the 97 k-expansion
   sequence cell-by-cell. Direct attack on the *depth* cost; bounded; no training. Complements (1).
3. **#840 learned / guided motion** — a learned heuristic/policy steering A* toward the shuffle.
   Matches the standing preference for the hard search; heavier, and needs its own grounding (note
   ADR-0028 is about *packing*, not *motion*, so its negative does not transfer directly).

A raised per-plane budget alone is **not** a fix (97 k exp / 39 min is far past any shippable wall);
it must be paired with (1) and/or (2) to cut the cost, not just the cap.

The order-search blocker (husky) is a **separable** efficiency problem for the all-8 charter goal and
is tracked alongside #844.

---

## Hypotheses tested and discarded (this probe)

| Hypothesis | How tested | Verdict |
|---|---|---|
| The corridor is **unthreadable / infeasible on own gear** | holonomic flood + carts route the pair + own-gear A* at 0.25 m finds a no-carts path | **Refuted** — a feasible own-gear path provably exists |
| **Grid coarseness** is the deployed-grid blocker | own-gear A* at 0.5 m (no path) vs 0.25 m (finds it, 97 k exp) | **Confirmed** — the coarse grid can't represent the deep shuffle; finer grid finds it. Open work is *efficiency* (the find is too slow to ship), not existence |
| **Carts / `on_carts`** is the fix | user-confirmed ops + `layout_today` modelling | **Rejected** — unfaithful (club hand-shuffles on own gear); carts was the *diagnostic* |
| **Pivot-point** fidelity (mains vs reference) | wheel geometry vs reference origin | **Ruled out** — mains within ~0.5 m of reference |
| The all-8 fails **only** on fk9↔cessna | `plan_fill` baseline + cart{fk9,cessna} | **Refuted** — both bail on husky (a second, ordering blocker) |

## Confidence

- **The block is lateral-displacement / search-depth (Findings 1–4, 6): high.** Clean, reproducible
  isolated-pair experiments on the real oracle; the carts-vs-own-gear contrast is decisive and the
  physics is unambiguous.
- **A feasible own-gear path exists and finer grid finds it (Finding 5): high.** The 0.25 m/10° find
  is exact-oracle-validated — a real no-carts tow path. Its 97 k-expansion / 39-min cost is a concrete
  lower bound on the shuffle depth; the *efficiency* gap (making it shippable) is the open problem.
- **The fix is a search-efficiency improvement, not a data/fidelity change: high** given Findings
  3–6.

The harness (`probe.py`) and per-run results are reproducible from this writeup; the experiment is
read-only and does not touch shipped solver/towplanner code.
