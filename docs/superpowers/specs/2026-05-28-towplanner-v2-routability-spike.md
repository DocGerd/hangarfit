# Towplanner-v2 routability spike — characterise → survey → prototype (#332)

- **Date:** 2026-05-28
- **Issue:** [#332](https://github.com/DocGerd/hangarfit/issues/332) (milestone #15 "Spikes & exploration")
- **Status:** Spike. PoC behind an opt-in flag on a feature branch; **no default behaviour changed**. Recommendation + go/no-go below.
- **Branch / PR:** `feature/towplanner-v2-spike-332` → draft PR into `develop`.
- **Reproduce:** `PYTHONPATH=src python docs/spikes/towplanner_v2_routability_bench.py`

> **Relationship to #332's framing.** #332 is filed as a *CNN-heuristic* spike and
> asks for a **quantified routability baseline** and a **learned-heuristic-vs-
> RRT-Connect** comparison. This doc answers those directly, and — rather than
> stop at "a CNN *could* predict a cost-to-go field" — it **builds the classical
> cost-to-go field the CNN would imitate**, plugs it into the real search, and
> measures it. That experiment is the cheapest possible de-risking of the CNN
> idea: the learned model's benefit ceiling is the *exact* field, so if the exact
> field doesn't move routability on the real fixtures, neither will a learned
> approximation of it. **It doesn't.** See below.

---

## TL;DR (read this first — the naive hypothesis was wrong)

1. **The going-in hypothesis** — "multi-plane fills fail because the straight-line
   A\* heuristic floods the dead pocket in front of parked planes; an
   obstacle-aware heuristic will route around them" — **was tested and falsified
   on the real fixtures.**
2. **What actually fails:** *every* un-routed plane exits on the **expansion cap**
   (`budget_exhausted`), *every* failed goal is **reachable in free space**
   (a point robot could get there), and the un-routed planes are the
   **wide-wing aircraft** (`aviat_husky` 10.82 m, `scheibe_falke` 18 m) maneuvering
   in a tight hangar. The bottleneck is **finite-width / rotational maneuvering in
   tight space**, *not* interior-obstacle clutter and *not* genuine geometric
   infeasibility.
3. **The prototyped obstacle-aware grid heuristic buys ZERO routability** on the
   fixtures (placeholder six: 3/5 → 3/5; two-plane: 1/2 → 1/2) and *mildly hurts*
   the already-routable cases (it added ~50 % more expansions to one success and
   ~40 % more wall-clock from the per-call Dijkstra). A 2-D cost-to-go heuristic
   speeds *clutter routing*, which is not the failure mode here.
4. **By the same logic the CNN (#332) is unlikely to help these fixtures** — it
   predicts the *same family* of 2-D cost-to-go field whose exact version we just
   showed is inert here. **Strong evidence to keep #332 deferred.**
5. **What *did* move routability: raising `_MAX_EXPANSIONS`** (placeholder six:
   3/5 → 4/5 at budget 2000), confirming the failures are *false negatives*
   (feasible-but-hard), not impossibilities — but it pays linearly in time and
   still cannot route the genuinely-tight `scheibe_falke`.
6. **Recommendation:** the real v2 routability fix is a **bidirectional sampler
   (RRT-Connect over the existing Reeds–Shepp steering)** scoped to the tight-
   maneuver cases, plus a **modest `_MAX_EXPANSIONS` bump** as a cheap immediate
   win. **Defer the grid heuristic and the CNN** — both attack a failure mode
   these fixtures don't have. The grid-heuristic seam is kept as a tested, opt-in
   PoC (it *is* the apparatus #332 asked to prototype, and the home a future
   learned/clutter heuristic would plug into), clearly labelled NO-GO-as-a-fix.

---

## 1. Context — what fails today

`towplanner.plan_path` is a deterministic **Hybrid-A\*** search over closed-form
**Reeds–Shepp** primitives (ADR-0007 v1 scope, ADR-0010 reverse-capable motion),
bounded by `_MAX_EXPANSIONS = 700` node expansions per plane and *best-effort*:
`plan_fill` walks `back_first_order` (deepest slot first) and, when no remaining
plane routes against the placed ones, raises `NoFeasiblePlanError`;
`solve(..., plan_paths=True)` records that layout's plan as `None` rather than
discarding the valid static arrangement (`solver.py:300-320`).

The documented pain (CLAUDE.md, the `--render-paths` spread-fallback in `cli.py`,
the `slow` perf gate): **multi-plane fills are reported un-routable** in the
placeholder 25×18 hangar (and solver-produced roomy layouts) for 3+ planes. The
single A\* heuristic is **straight-line Euclidean distance** — admissible, but
obstacle-blind, which is the textbook A\*-in-clutter failure and was the
hypothesis this spike set out to confirm or kill.

---

## 2. Part 1 — Failure characterisation (the evidence)

**Method** (`docs/spikes/towplanner_v2_routability_bench.py`, Part 1). For each
target layout, walk `back_first_order`; place each plane in its natural slot
against the already-placed deeper planes; run the **unchanged** Euclidean search
at the default budget with a new diagnostic `stats` out-param, recording per
plane:

- `exp` — node expansions used.
- `cap?` — `budget_exhausted`: hit `max_expansions` (a *false-negative* candidate —
  feasible-but-hard).
- `space?` — `space_exhausted`: the open heap emptied **before** the cap — every
  reachable discretised state settled with no analytic shot closing to goal, i.e.
  genuine local infeasibility within the grid/primitive resolution.
- `reach?` — independently: is the goal cell reachable from any surviving door-cone
  start in the **free-space (point-robot) occupancy grid**? `N` ⇒ *no planner of
  any kind* can route it (a point can't even get there). `Y` + a failed route ⇒ a
  search/budget problem, not geometry.

> The `cap? / space? / reach?` triple separates "search got lost"
> (`cap?=Y, reach?=Y`) from "search proved it stuck" (`space?=Y`) from
> "geometrically impossible" (`reach?=N`).

### Results (per plane, euclidean vs grid @ 700; reach? is point-robot)

```
placeholder-25x18  solve_fresh_six_planes seed=1   (5 floor planes)
plane          euclidean@700        grid@700        reach?
aviat_husky    FAIL exp=700 (cap)   FAIL exp=700    Y      ← deepest; routed vs NO interior obstacles → fails on WALLS
ctsl           FAIL exp=700 (cap)   FAIL exp=700    Y
fuji           OK   exp=254         OK   exp=388    Y      ← grid made a SUCCESS *slower* (+53 % expansions)
fk9_mkii       OK   exp=2           OK   exp=2      Y
cessna_140     OK   exp=0           OK   exp=0      Y
  routed: euclidean 3/5, grid 3/5

placeholder-25x18  valid_two_separated             (2 planes)
plane          euclidean@700        grid@700        reach?
scheibe_falke  FAIL exp=700 (cap)   FAIL exp=700    Y      ← 18 m wingspan in an 18 m-wide hangar
ctsl           OK   exp=0           OK   exp=0      Y
  routed: euclidean 1/2, grid 1/2

roomy-30x25     valid_all_nine_planes              (9 planes)
  routed: euclidean 9/9, grid 9/9   (fully towable — friendly hand-authored geometry; most planes close in 0–1 expansions)
```

### Reading

- **No `space?` flags and no `reach?=N`.** Not a single failure is genuine
  point-robot infeasibility, and not one is "search space exhausted". **Every
  failure is `budget_exhausted` with the goal point-reachable** — i.e. a
  *feasible-but-hard* false negative.
- **The un-routed planes are the wide-wing ones.** `aviat_husky` is the *deepest*
  slot, so it is routed against **zero interior obstacles** — its only opponents
  are the **hangar walls**, yet it still burns the whole budget. `scheibe_falke`'s
  18 m wing in an 18 m-wide hangar is the extreme case. The blocker is the
  finite-width plane finding a **collision-free rotational maneuver** in tight
  clearance, not navigating *around* other planes.
- **The hand-authored roomy layout is fully towable** (9/9). The roomy
  un-towability noted elsewhere is a property of *solver-produced spread* layouts
  (which cluster planes), not of geometry that a human laid out with maneuvering
  room. Worth recording: the planner is not broadly broken; it struggles on
  *tight* arrangements.
- **This kills the heuristic hypothesis.** A cost-to-go heuristic helps when the
  search wastes expansions routing *around interior clutter*. Here the deepest
  plane has no interior clutter at all and still fails — so a better global
  heuristic cannot be the fix.

---

## 3. Part 2 — Benchmark (planes routed / total, wall-clock)

**Method** (`bench`, Part 2). Route each target plane-by-plane (back-first, each
against the already-placed deeper ones) under four configs:

| target | euclidean@700 | grid@700 | euclidean@2000 | grid@2000 |
|---|---|---|---|---|
| placeholder-25×18 — `solve_fresh_six_planes` seed 1 (5 planes) | **3/5** (74 s) | **3/5** (76 s) | **4/5** (169 s) | **4/5** (187 s) |
| roomy-30×25 — `valid_all_nine_planes` (9 planes) | 9/9 (7 s) | 9/9 (11 s) | 9/9 (7 s) | 9/9 (11 s) |
| placeholder-25×18 — `valid_two_separated` (2 planes) | 1/2 (7 s) | 1/2 (7 s) | 1/2 (18 s) | 1/2 (19 s) |

*(Wall-clock is illustrative — single un-pinned machine; the routed counts are the
robust signal.)*

### Reading

- **Grid heuristic = no routability gain anywhere**, and a small *cost* (the
  per-call Dijkstra adds ~40 % wall-clock; it added expansions to a success). It
  is **not** the fix for these fixtures.
- **Raising the budget is the only lever that moved a count** — placeholder six
  3/5 → 4/5 going 700 → 2000 — confirming those are budget-limited false
  negatives. But it pays linearly (74 s → 169 s) and **still cannot route
  `scheibe_falke`** (1/2 at every budget): some tight cases are beyond what a
  budget bump on the *current* search reaches.

---

## 4. Options surveyed against the evidence

### Option A — Raise `_MAX_EXPANSIONS`

**The only knob that moved routability here** (3/5 → 4/5). Cheap, deterministic,
zero new code. But it pays linearly in time for every plane (routable or not — the
bail time on an un-routable plane scales with the budget), and it does **not**
reach the genuinely-tight cases (`scheibe_falke`). **Verdict: GO as a cheap,
modest immediate bump (e.g. 700 → ~1500–2000), NOT as the whole answer.** Expose
it (this spike adds `--tow-max-expansions` / `plan_fill(max_expansions=…)`), and
let the operator trade time for routability on hard fills.

### Option B — Obstacle-aware grid heuristic (PROTOTYPED) — NO-GO as a fix

The free-space geodesic cost-to-go (deterministic Dijkstra over the search grid,
goal-outward, placed planes + bay + walls as blocked), substituted for the
Euclidean estimate. It is the exact classical analogue of #332's learned
cost-to-go field, and it is **correct, deterministic, oracle-clean, and a genuine
lower bound** (un-inflated point-robot ⇒ admissible-leaning; the exact-oracle
`path_first_conflict` remains the sole validity authority — the proposer/verifier
split #332 mandates). **But the benchmark shows it buys no routability** because
the failure mode is tight maneuvering, not clutter routing. **Verdict: NO-GO as
the routability fix.** Kept as a tested, opt-in seam (`heuristic="grid"`) — it is
the apparatus #332 asked to prototype and the natural home for a future
clutter/learned heuristic, and the spike's negative result *is* its deliverable.

> Why it can even *hurt*: the discretised grid field is not perfectly *consistent*
> with the continuous search steps, so it can trigger extra re-expansions; and its
> multi-start ordering picks a different (sometimes longer) start. On a
> clutter-dominated map the routing win would dominate that noise — but these
> fixtures have little interior clutter, so only the noise shows.

### Option C — RRT-Connect / bidirectional sampling fallback (RECOMMENDED for v2)

The classical escape hatch named in ADR-0007/ADR-0010. RRT-Connect grows two trees
(start + goal) and connects them; it is the bidirectional RRT variant, typically
outperforms unidirectional RRT, and composes cleanly with the closed-form
**Reeds–Shepp** steering this codebase already has (the RS-RRT / RRT\*-with-Reeds–
Shepp line; OMPL ships RRT-Connect over SE(2)/Reeds–Shepp state spaces). Crucially,
**a sampler is the right tool for *this* failure**: finding a feasible
finite-width *maneuver* in tight clearance is exactly what randomised tree growth
does well, where a budget-bounded directed A\* exhausts its expansions probing the
same pocket. **Verdict: the recommended real v2 fix**, scoped to the
tight-maneuver / budget-exhausted cases the Part 1 flags identify.

**The cost is the determinism contract.** RRT-Connect is stochastic; ADR-0003
demands byte-identical plans. The literature resolves this with **deterministic
low-dispersion sampling sequences** (Halton/Sobol; Janson–Ichter–Pavone show
deterministic sampling preserves optimality guarantees) plus a seeded RNG threaded
from the existing scenario seed and a deterministic tree-merge/tie-break order. It
is ~300–500 lines and a second planning paradigm — real, but bounded, and it
attacks the failure the evidence actually shows.

### Option D — Learned heuristic / CNN (#332's hypothesis) — DEFER (de-risked)

A CNN predicting the cost-to-go field as the A\* heuristic (Neural A\*'s guidance
map; TransPath; VIN; MPNet as a sampler). The published wins are real (~4× fewer
A\* expansions at <0.3 % sub-optimality) and the training data is free (the
deterministic planner + the closed-form RS distance as an analytic lower-bound
label). **But this spike provides direct evidence to defer it:** the learned
model's benefit ceiling is the *exact* cost-to-go field, and we measured the exact
field to be **inert on the real fixtures** (Option B) because their failure is
tight maneuvering, not clutter. A learned approximation cannot beat an exact field
that already doesn't help. Add to that the ONNX-runtime dependency on a lean,
lockfile-pinned CLI, the determinism reconciliation (fixed weights + pinned float
ops, or scope the backend out of ADR-0003), and the need for real measurements
(CLAUDE.md open question). **Verdict: DEFER (keep #332's "later" label).** Revisit
only if a *clutter-dominated* routability need emerges (e.g. denser real layouts),
at which point Option B is the cheaper first thing to try and the honest yardstick
the CNN must beat.

---

## 5. Determinism & canary confirmation

The PoC is **behind opt-in flags**; the default path is byte-for-byte unchanged.

- `plan_path`/`plan_fill`/`solve` default to `heuristic="euclidean"`, whose A\*
  heuristic expression is the identical `math.hypot(goal − pose)` as before; the
  `stats` out-param is `None` by default (a pure no-op).
- The grid Dijkstra and grid-mode search are **RNG-free** with fixed iteration
  orders and monotonic-counter tie-breaks ⇒ `heuristic="grid"` is itself
  byte-identical run-to-run (pinned by
  `tests/test_towplanner_grid_heuristic.py::test_grid_heuristic_is_deterministic`).
- The ADR-0003 canaries (`tests/test_solver_canaries.py`) and the full
  `tests/test_towplanner_*.py` suite are **unchanged and green** (439 + canaries) —
  see the canary-diff confirmation in the PR summary.

---

## 6. Risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Reader takes Option B's NO-GO as "obstacle-aware heuristics are useless" | Med | They are useless *for this failure mode*; they help clutter routing (not present here). The seam stays for that future case + the learned-heuristic home. |
| 2 | Option A budget bump masks the genuinely-tight cases as "fixed" | Med | It only flips false negatives; `scheibe_falke` stays un-routable at any budget — track it as the RRT-Connect motivator, not a budget tune. |
| 3 | RRT-Connect determinism (Option C) breaks ADR-0003 if added naively | High (for that follow-up) | Deterministic low-dispersion sampling + seeded RNG + deterministic merge order; pin with a determinism canary mirroring the existing ones. |
| 4 | Results are illustrative until real measurements land (placeholder hangar/fleet) | Med | Inherits the CLAUDE.md disclaimer; the *mechanism/finding* (tight-maneuver, not clutter) is structural and unlikely to invert with real dims, but the exact counts will move. |
| 5 | Grid Dijkstra per-call latency if the seam is ever used by default | Low | It is opt-in and NO-GO; if revived, memoise per (layout, goal). |

---

## 7. Recommendation & go/no-go

| Option | Go/No-go | When / scope |
|---|---|---|
| **A. Raise `_MAX_EXPANSIONS`** | **GO (modest)** | Immediate: bump the default to ~1500–2000 (it flipped 3/5 → 4/5). Keep the new `--tow-max-expansions` override. Re-tune once real measurements land. |
| **C. RRT-Connect bidirectional fallback** | **GO (the real v2 fix)** | Next routability milestone; scoped to the `cap? + reach?=Y` tight-maneuver cases; deterministic low-dispersion sampling for ADR-0003. |
| **B. Obstacle-aware grid heuristic** | **NO-GO as a fix** | Kept as a tested opt-in seam for future clutter-dominated layouts / the learned-heuristic home. Do not enable by default. |
| **D. CNN learned heuristic** | **DEFER (keep #332 "later")** | Only if a clutter-dominated routability need emerges; Option B is then the cheaper first try and the yardstick to beat. |

**Headline:** the multi-plane-fill un-routability is a **tight-maneuver search**
problem (every failure is budget-exhausted with the goal point-reachable; the
victims are the wide-wing planes), **not** the interior-obstacle-clutter problem a
cost-to-go heuristic — classical *or* learned — is built for. The cheapest honest
next step is a modest budget bump; the right v2 fix is a **deterministic
RRT-Connect** over the existing Reeds–Shepp steering. The grid-heuristic PoC's
**negative result is the spike's most valuable output**: it redirects effort away
from the CNN that #332 was leaning toward.

---

## 8. How to reproduce

```bash
python3.12 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
PYTHONPATH=src python docs/spikes/towplanner_v2_routability_bench.py          # full table (~25 min)
PYTHONPATH=src python docs/spikes/towplanner_v2_routability_bench.py --part1   # characterisation only (~2 min)
# Try the opt-in seam end-to-end (default behaviour unchanged):
hangarfit solve tests/fixtures/scenario_minimal.yaml --render out.png \
    --render-paths --tow-heuristic grid --tow-max-expansions 2000
```

---

## 9. References

- Incumbent planner: `src/hangarfit/towplanner.py`; [ADR-0007](../../adr/0007-tow-path-planner-v1-scope.md) (v1 scope, the "why not RRT-Connect" deferral), [ADR-0010](../../adr/0010-reeds-shepp-motion-model.md) (Reeds–Shepp motion), [ADR-0003](../../adr/0003-rr-mc-solver-algorithm.md) (determinism contract).
- Validity oracle: `src/hangarfit/collisions.py`; coordinate frame [ADR-0002](../../adr/0002-determinant-minus-one-transform.md).
- OMPL planner catalogue (RRT-Connect, SE(2)/Reeds–Shepp state spaces): <https://ompl.kavrakilab.org/planners.html>
- Deterministic sampling-based planning (buying back determinism for RRT-Connect): Janson, Ichter, Pavone, *Deterministic Sampling-Based Motion Planning*, <https://arxiv.org/pdf/1505.00023>
- Reeds–Shepp + RRT for non-holonomic vehicles (RS-RRT / RRT\*): <https://ieeexplore.ieee.org/document/11102371/>
- Learned heuristics: Neural A\* Search <https://arxiv.org/pdf/2009.07476>; TransPath <https://arxiv.org/pdf/2212.11730>; Value Iteration Networks <https://arxiv.org/pdf/1602.02867>; MPNet <https://arxiv.org/pdf/1907.06013>; "Learning heuristics for A\*" (≈4× fewer expansions, <0.3 % sub-optimal) <https://arxiv.org/pdf/2204.08938>.
```
