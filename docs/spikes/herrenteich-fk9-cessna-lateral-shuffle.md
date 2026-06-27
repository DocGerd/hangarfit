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

### Husky-ordering gate (#844a, 2026-06-27) — isolates the ordering effect from the wall

The #844a follow-up asks whether a smarter `_place_rest` candidate enumeration (seat the constrained
own-gear husky *before* the carted `wild_thing`) is a cheap, separable win. A witness-first read-only
probe answers it at the **deployed** grid (0.5 m / 15°) and budgets (per-plane 8 k, global 16 k) — the
shippable regime, sharper than the 20 k / 80 k run above. It instruments `plan_path` to trace per-plane
expansions and runs two arms (natural `back_first_order` vs a forced husky-before-`wild_thing` order),
plus an independent fk9↔cessna wall check.

| | Baseline (natural `back_first_order`) | Husky-early (forced) |
|---|---|---|
| `aviat_husky` | **exhausts** (8000, retried 6573) | **routes (2538 exp)** |
| `wild_thing` | routes (1427) | routes (1294) — still seats after husky |
| distinct planes routed (all but the blocker) | 4 (zlin, scheibe, wild_thing, stemme) | **6** (zlin, scheibe, husky, wild_thing, stemme, cessna) |
| bails on | `aviat_husky` — **false blame** (husky is routable) | `fk9_mkii` — **the genuine wall** |
| all-8 routed? | no (`global fill budget (16000) exhausted`) | no (`global fill budget (16000) exhausted`) |

**Mechanism.** In the natural order `wild_thing` (deeper, y 16.95) precedes `aviat_husky` (y 16.40),
so the greedy commits `wild_thing` first and `husky` then cannot route past it (the root-cause doc's
husky-ordering finding — husky routes if placed before `wild_thing`, which the husky-early arm now
directly confirms: `husky ok 2538`). The natural-order run consumes the **entire** 16 k global budget
on `wild_thing` (1427) plus two `aviat_husky` exhaustions (8000 + 6573 = 14.6 k) and bails on
`aviat_husky`. Whether the #667 backtracking *would* reach the husky-early order given a larger budget
is a hypothesis this gate did **not** test (no larger-budget baseline arm was run); what it shows is
that within the deployed budget the natural order never gets there, while forcing husky-early seats
husky (2538), still seats `wild_thing` (1294), and relocates the bail to the **genuine** fk9↔cessna
wall.

**Wall check (independent, one `plan_path` call each, deployed grid + 8 k per-plane cap):** `fk9_mkii`
vs parked `cessna_140` → **EXHAUSTED (8000)**; `cessna_140` vs parked `fk9_mkii` → **EXHAUSTED (8000)**.
Neither routes past the other within the per-plane cap — confirming fk9↔cessna is **grid-geometry-locked**
at the deployed 0.5 m/15° lattice (consistent with Finding 5 + the #840 Step-0 NO-GO: the coarse grid
can't represent the parallel-park at *any* budget, not merely the global cap).

**Verdict — husky-ordering is a genuine but *insufficient* effect.** It is provably a pure ordering
problem (forcing husky-early seats it cleanly and strictly dominates: 6 planes seated vs 4, and the
bail names the real blocker). But it **cannot route the all-8**: the residual fk9↔cessna pair is
grid-geometry-locked, stronger than the predicted "budget-bound" — no global-budget bump (the rejected
#480/#512 tradeoff) reaches it. So a `_place_rest` ordering heuristic is a *diagnostic-clarity /
partial-fill* improvement (correct bail blame, more planes in a best-effort render), **not** a routing
fix, and shipping it touches determinism-sensitive `_place_rest` (binds determinism-guard + the
`back_first_order` timeline-order divergence in `scene.py`'s whole-fill timeline builder — currently
`scene.py:298` — + a perf rebaseline). The ship-vs-kill decision for #844a is
**deferred** pending that cost/benefit call; #844a stays OPEN.

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

## Parallel-park macro (candidate 2): implemented and **refuted** (2026-06-26)

Candidate 2 above was the first fix attempted. It was implemented (macro geometry + a default-OFF
injection of macro edges into the Hybrid-A\* loop — both reviewed and **determinism-guard PASS**,
byte-identical with the flag off) and gated by a PoC before any hardening. The macro was realized as
the optimal Reeds–Shepp word to a small **laterally-shifted waypoint** (same heading, offset Δ ∈
{0.5, 1.0} m, left/right). The PoC routes `cessna_140` (parks last) past the parked `fk9_mkii`, own
gear, at the deployed 0.5 m/15° grid — `plan_fill`-faithful (22-pose entry cone, grid heuristic).

**Gate result — NO-GO.** The macro does **not** route the pair at the deployed grid at any tested
budget:

| Run | Routed? | Expansions | Wall |
|---|---|---|---|
| OFF (setup validation) | no — budget-exhausted at every tested budget (500 / 2 000 / 32 000); never space-exhausts (matches #840) | up to 32 k | — |
| ON @ 8 000 | **no** — budget-exhausted | 8 000 | 444 s |
| ON @ 16 000 | **no** — budget-exhausted | 16 000 | 952 s |
| ON @ 32 000 | **no** — budget-exhausted | 32 000 | 2 051 s (34 min) |

**Root-cause probes (why it failed):**

- **Macro geometry is sound, not the problem.** Both Herrenteich movers are **R = 0 (pivot-in-place)**,
  so the macro words are ideal tight parallel-parks — `pivot 90° · drive 0.5 m · pivot back`, **zero
  forward excursion** (`fwd_span = 0.00 m`). (This refutes the "the optimal RS word is a big forward
  S-curve that won't fit" worry, which assumed a car-like turn radius.)
- **Probe — guidance vs resolution (coarse):** tracking min Euclidean dist-to-goal over the coarse ON
  search, the closest pose ever reached is **3.103 m** from goal at the *wrong x* (9.77 vs goal 12.8).
  It can't get *near* the goal nook — not "reached the goal but couldn't close." With the obstacle-aware
  grid geodesic heuristic (which pulls toward the goal around obstacles), this points to a
  **geometric/resolution** wall, not heuristic mis-guidance.
- **Probe — resolution + macro salvageability (fine 0.25 m/10°, the witness resolution, with fine macro
  deltas):** ON @ 40 k reaches **2.501 m** from goal — at the *correct goal-x (12.81)*, heading 80°
  (goal heading 90°), 2.5 m short in y — closer than coarse, but still no route (40 k < the witness's
  96 949-exp budget). So
  **finer resolution makes more progress** (a valid path exists at fine grid = the witness), and the
  macro did **not** slash the fine-grid cost.

**Conclusion.** #844 is a **fine-resolution search-efficiency wall**: the valid nook maneuver lives at
sub-coarse resolution and is *expensive to find even at fine grid* (39 min). The macro adds
**longer-range moves on a lattice, not resolution**, so it cannot represent the sub-grid maneuver — it
fails at the coarse grid and adds little at the fine grid. Its one contribution (longer-range lattice
moves) would be **subsumed** by the resolution improvement an adaptive grid itself provides, so it adds
no advantage over adaptive grid alone (which remains an open candidate — direction 1 above). **The
implementation was discarded** (the design provenance is kept at
`docs/superpowers/specs/2026-06-26-fk9-cessna-parallel-park-macro-design.md` + its plan). **Direction
pivoted to candidate 3 — #840 learned/guided motion** — which attacks the grounded bottleneck
(efficiently searching the fine-resolution space) and has a proven teacher (the fine-grid A\* witness).
A cheap deterministic shortcut (the macro) has now been ruled out, sharpening #840's case.

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

---

## Step-0 result — SE(2) heading-aware heuristic headroom probe (#840, initiated 2026-06-26, result 2026-06-27) → **NO-GO**

**Question (pre-registered, spec §4):** does a heading-aware cost-to-go heuristic collapse the
fine-grid A\* expansion count enough (GO ≥50×, PARTIAL ≥5×, NO-GO <5×) to make the fk9↔cessna nook
shippable? **Answer: NO-GO** — a heading-aware heuristic does *not* help; the cost is an intrinsic
A\* plateau.

**Tool:** `bench/se2_heuristic_probe.py` — an exact **backward-SE(2) Dijkstra** cost-to-go field
(`build_se2_field`), injected into `plan_path` via the additive `heuristic_fn` seam (Task 2) and
compared against the deployed **position-only** `grid` heuristic at the fine 0.25 m/10° grid.

> **A first toy-fixture run was discarded as VACUOUS (the feasibility-first trap).** A tiny synthetic
> arena (18 m deep × 14 m wide) wedged the fk9 (9.85 m wingspan, ≈2 m wall clearance), so the backward flood
> reached only **78 cells** around the goal and never reached the door — the field silently degraded
> to euclidean and all three heuristics exhausted at 16 000 without finding a route. That tells us
> nothing about headroom (the toy was near-*infeasible*, not hard). The trustworthy gate below runs
> on the **real** witness-grounded pair instead.

**Trustworthy gate — the real isolated fk9↔cessna pair** (the exact witness subproblem: cessna parked
at its Herrenteich goal, fk9 routed to its goal via the door cone, fine 0.25 m/10° grid, own gear):

| heuristic | result |
|---|---|
| `grid` (deployed, position-only geodesic) | **FOUND at 96 949 expansions** |
| `se2` (exact backward-SE(2) Dijkstra, heading-aware) | **FOUND at 108 991 expansions** |
| ratio `grid / se2` | **0.89×** — se2 is ~12% **worse** ⇒ **NO-GO** |

**Why this is airtight (not a second vacuous result):**
- **Harness validated:** the `grid` run reproduced the witness's **exact 96 949** expansions, confirming
  the setup *is* the faithful witness subproblem.
- **The 150 k-cell field cap is provably not a confound:** the field's max cost-to-go `C_cap = 41.6`
  exceeds `h(cone[0]) ≈ 17.2` (an upper bound on the optimal cost-to-go `C*` from the start), so the
  field holds its heading-aware cost-to-go for the bulk of the region A\* explores. Where A\* *does*
  reach beyond the cap (a positionally-near, maneuver-far pose), `h` falls back to euclidean — an
  *under*-estimate that can only admit **more** expansions. So 108 991 is an **upper bound** on the
  uncapped-exact field's count; an uncapped rebuild could only *lower* it, never raise it. The verdict
  is robust either way: that upper bound already loses to `grid`, and reaching even the **PARTIAL** bar
  (≥5×) would require an uncapped field to collapse expansions ~5.6× below the measured value — which
  the intrinsic-plateau argument below rules out.

**Interpretation — the pre-registered plateau dissent (spec §3.1), confirmed.** A *perfect* admissible
heuristic losing to a looser one is the textbook signature of an **intrinsic near-C\* A\* plateau**:
completeness forces expansion of *every* state with `f* ≤ C*`, regardless of heuristic quality. The
~97 k expansions are the volume of that plateau in the cm-precision R=0 parallel-park, not a
heading-guidance deficiency. Heading-awareness is **not** the lever.

**Consequence — the heuristic class is dead for this nook.**
- **Deterministic field (spec Step 1):** killed — the exact field, the best an admissible heuristic
  can be, is already ~12% worse than the deployed one.
- **Learned heuristic (learned-M1):** killed transitively — a learned `h` can only *approximate* the
  exact field that already lost, and an ONNX-in-loop `h` would break ADR-0003 byte-identity anyway.
- **No heuristic-class / search-guidance method survives.** Cracking the nook would require a
  fundamentally different method (continuous trajectory optimization), a far larger bet (deferred).
- One bounded caveat noted and not pursued: the field is **cusp-free** (omits direction-reversal
  penalties) while the nook is cusp-heavy; a 4D cusp-aware field *might* guide marginally better, but
  the same plateau bound caps the upside — a larger bet, not a smaller one.

**Disposition (#844 stays OPEN):** the fk9↔cessna pair is recorded as a **known manual-insertion case**
(see below) — the club hand-shuffles it on own gear, so `on_carts: true` would be unfaithful and
caching the 39-min witness plan would be brittle/hardcoded ("worst-faithfulness"). Two separable,
cheaper-than-the-nook follow-ups remain on #844: **(a)** the husky front-cluster entry-**ordering**
quick win (a pure order-search problem, *not* the dead nook), and **(b)** a parked, clearly-scoped
**continuous-trajectory-optimization** spike (the only surviving method class; defer).

> **Follow-up (a):** The witness-first ordering gate (2026-06-27) confirms husky is a pure, separable
> ordering issue but that fixing it **cannot route the all-8** (the residual fk9↔cessna pair is
> grid-geometry-locked) — see "Husky-ordering gate (#844a)" above. The effect is genuine and strictly
> dominates (diagnostic clarity + more planes in partial renders), so the **ship-vs-kill decision is
> deferred**; #844a stays OPEN.
>
> **Follow-up (b):** Gate 0 (determinism-first pre-check) returned **NO-GO (dominated)** (2026-06-27) — see [`herrenteich-fk9-cessna-trajopt-determinism-precheck.md`](herrenteich-fk9-cessna-trajopt-determinism-precheck.md) ("Verdict (Gate 0)").

### Known manual-insertion case — fk9_mkii ↔ cessna_140

The `solve --render-paths` / `view` auto-router cannot route the fk9↔cessna front-door pair, and
**this is expected, not a bug**: the cm-precision own-gear parallel-park is an intrinsic A\* plateau
(~97 k expansions / 39 min at the fine grid the maneuver requires), and no search-guidance heuristic
shrinks it (Step-0 NO-GO above). In real operation the club inserts this pair by **hand-shuffling on
own gear**, never on dollies. Treat the pair as a hand-placed exception in the all-8 fill until/unless
a continuous-optimization planner (#844 follow-up b) is built.
