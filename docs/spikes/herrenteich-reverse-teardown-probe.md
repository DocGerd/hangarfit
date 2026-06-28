# Herrenteich reverse-teardown feasibility probe (#667 Rung C)

**Status:** probe built + run — **complete**. **Date:** 2026-06-28.
**Issue:** [#863](https://github.com/DocGerd/hangarfit/issues/863) (Rung C of the
[#667](https://github.com/DocGerd/hangarfit/issues/667) shuffle-aware tow-routing program).

> A read-only diagnostic that decides, *before* investing in the move-aside writer (Rungs D/E),
> whether the Rung-B routing wall is a search-*efficiency* limit or a true *no-monotone-order* lock.
> Spec: [`docs/superpowers/specs/2026-06-27-667-shuffle-aware-tow-routing-design.md`](../superpowers/specs/2026-06-27-667-shuffle-aware-tow-routing-design.md) §4 Rung C.

---

## TL;DR

The all-8 Herrenteich witness admits **no monotone tow order** at the deployed grid + affordable
budget. The reverse-teardown probe peels exactly **one** of six tow-routable aircraft, then stalls
on a five-body mutually-blocking core:

| witness | extractable aircraft | order (peeled) | stuck core | verdict | budget |
|---|---|---|---|---|---|
| `layout.yaml` (all-8, **no ground objects**) | 6 | `ctsl` | `aviat_husky`, `cessna_140`, `fk9_mkii`, `wild_thing`, `zlin_savage` | **STUCK** | 8 000 |

Only the door-adjacent `ctsl` can be extracted; removing it frees **none** of the other five — each
still exhausts the 8 000-expansion per-plane budget against the rest. By the completeness argument
below, a stall means **no monotone fill order exists** at this grid + budget. Since reverse-teardown
**only removes, never relocates**, it hits the *same ceiling* as the forward monotone fill — the
**predicted dual**, confirmed. So the wall is **not** a reverse-vs-forward framing artifact, and **not**
a budget a finer search can afford ([#840/#844](herrenteich-fk9-cessna-lateral-shuffle.md): even the
`fk9↔cessna` pair alone is a ~97 k-expansion near-C\* A\* plateau no heuristic shrinks). The indicated
lever is **move-aside's relocation (Rung E)**, not better search.

Run with `reverse_teardown_probe(load_layout("examples/herrenteich/layout.yaml"))` (2026-06-28, local,
~30 min at the full 8 000 budget). RNG-free + id-sorted ⇒ deterministic (ADR-0003).

## What the probe is

`reverse_teardown_probe(target)` (`src/hangarfit/towplanner.py`) generalises the single-body
`egress_first_conflict` into a **whole-fill teardown**: greedily extract every tow-routable aircraft
slot → door against the bodies still parked, and report whether a full teardown order **exists**, plus
the canonical mutually-blocking residual when it does not. It is **read-only** — no plan output, no
data-model change, no production caller — so every existing plan stays byte-identical (ADR-0003).

By **Reeds–Shepp reversibility** (ADR-0010) an egress (slot → out the door) is feasible iff an entry
(door-cone → slot) path exists against the same parked scene. So a reverse-teardown order is exactly a
**monotone fill** order run backwards: if the fleet can be torn down one body at a time, it can be
filled one body at a time, and vice-versa.

## Why greedy peel is the exact oracle (not a heuristic)

For *ideal* (unbounded) egress feasibility, feasibility is **monotone in obstacles**: a body that can
drive out past a set of obstacles can drive out past any *subset* (removing bodies only opens paths). That
gives a confluence property — repeatedly removing *every* currently-egressable body reaches the empty set
**iff** some full teardown order exists, and the residual it stalls at is the **unique, order-independent**
mutually-blocking core.

*Proof of the hard direction (contrapositive).* Suppose a teardown order π exists but the peel stalls at
a non-empty residual R (no body in R can egress against R minus itself). Take the earliest body of π that
lies in R, call it b\*. Because b\* is the *earliest* π-body in R, every π-body removed before it is outside
R, so when π removes b\* the still-parked set S is a **superset of R**. π removes b\* successfully ⇒ b\*
egresses against S minus {b\*} ⊇ R minus {b\*}; egress past a superset of obstacles being feasible implies
egress past the subset R minus {b\*} is feasible too — so b\* *can* egress against the core, contradicting
the stall. ∎

The probe evaluates feasibility with a *finite* per-plane budget, under which monotonicity is an
approximation (freeing previously-blocked states can add `f≤C*` nodes to expand and exhaust the cap), so
the verdict is **planner/budget-relative**: **CLEAR** proves a monotone order exists at this grid + budget;
**STUCK** names the core no monotone order can seat *at this grid + budget* — apples-to-apples with the
forward fill (same budget), not an unconditional existence disproof. The per-body `[budget-exhausted]` vs
`[space-exhausted]` tag (below) distinguishes "might route with a bigger budget" from "wedged at this
discretization". Determinism: id-sorted peel + RNG-free closed-form routing at a fixed per-plane budget
(the full `_MAX_EXPANSIONS = 8000`, the authoritative budget `egress_first_conflict` uses — *not* the
globally-capped fill budget, so an exhausted fill can never falsely declare a body trapped).

## Modelling choice

Only **tow-routable aircraft** are extracted. Hand-placed (dolly) gliders and all ground objects stay as
**fixed obstacles** in every partial state — they go in/out by hand, never towed. This is the faithful
dual of the Rung-A forward fill, which keeps the same keep-outs and routes the same towable set, so the
probe is apples-to-apples with the Rung-B forward-fill ceiling.

## What the probe found

**The peel (deployed grid, 8 000 budget).** Round 1 tests all six extractable aircraft against the full
parked scene: `ctsl` (parked at the door corner) drives out in **1.7 s**; `aviat_husky`, `cessna_140`,
`fk9_mkii`, `wild_thing`, `zlin_savage` each **exhaust the budget** (~96–425 s apiece — a full A\* threading
the dense nest, never reaching the door). So round 1 peels only `ctsl`. Round 2 re-tests the remaining five
against the *sparser* scene (`ctsl` gone) — and **all five still exhaust the budget**. Zero egressable ⇒
**stall**. The five form the canonical core.

**Three findings.**

1. **No monotone order exists at the deployed grid + budget.** One of six towable aircraft comes out; the
   rest cannot, even after it leaves. This is the **reverse-direction confirmation** of the Rung-B
   forward-fill wall — the dense fill is unroutable from *both* directions.

2. **The blocking core is broader than the documented `fk9↔cessna` pair** — it is a five-body lock
   (`husky`, `cessna`, `fk9`, `wild_thing`, `zlin`). The `fk9↔cessna` front-door corridor is the *residual*
   blocker isolated after husky-ordering at a finer analysis ([lateral-shuffle spike](herrenteich-fk9-cessna-lateral-shuffle.md));
   at the **deployed budget**, the whole back-cluster is budget-locked, not just that pair. All five bail
   **`[budget-exhausted]`** — they grind the full 8000 expansions (~96–425 s each) without reaching the
   door, *not* the early `[space-exhausted]` drain (which bails in <1 s, as every `layout_today` aircraft
   does — see Limitation). So at the deployed grid a path *may* exist beyond the affordable budget
   (consistent with the ~97 k near-C\* plateau the `fk9↔cessna` pair alone needs, [#840/#844](herrenteich-fk9-cessna-lateral-shuffle.md)),
   but it is unshippable — finer grid and bigger budget were both refuted NO-GO. The probe tags each
   blocking conflict with its exhaustion mode, so this regime call is read from structured output, not
   eyeballed timings. This is the same budget-exhaustion mechanism Rung-B's forward fill hit.

3. **Reverse framing adds no reachability.** Because the probe only *removes*, never *relocates*, it has —
   by the monotonicity argument — the *same* ceiling as the forward monotone fill. The stall confirms a
   teardown-order *writer* (a hypothetical "reverse Rung E") would buy nothing the forward fill doesn't
   already. The only lever that changes reachability is **move-aside (Rung E)**: temporarily displacing a
   committed body to break the lock, which neither monotone direction can express.

**Decision.** Rung C's verdict gates the program: **STUCK ⇒ proceed to the move-aside relocation (Rung
D seam → Rung E core).** It also closes the door on a reverse-only writer and re-confirms, from a third
independent angle, that *search-guidance* cannot help (the #840 heading-aware heuristic and #844 trajopt
gates already returned NO-GO; the ~97 k near-C\* plateau is intrinsic). The honest caveat from the spec
§2 stands: even fully-built depth-1 move-aside may not seat the cm-scale `fk9↔cessna` parallel-park, which
may remain a documented manual-insertion case.

## Limitation — `layout_today.yaml` is confounded by its ground objects

The denser "today" witness (`layout_today.yaml`: 9 aircraft + 3 ground objects — VW Caddy, fuel trailer,
glider trailer) is **not** a clean test under this probe's model. With the routed movers held as **fixed
obstacles**, the door-congested real arrangement traps **every** aircraft in round 1 (a uniform
budget-exhausted block), so the probe stalls immediately with all seven in the core. But a faithful
teardown would remove the door-adjacent Caddy (parked *last*, nearest the door) **first**, not hold it
fixed — so this verdict conflates ground-object congestion with aircraft locking and is discarded. The
clean monotone-order verdict is `layout.yaml`'s (no ground objects ⇒ no door-plug confound). Extending the
probe to tear down routed movers **door-first** before the aircraft is future work (and only *adds*
reachability, so it cannot turn `layout.yaml`'s STUCK into a CLEAR).

## How to reproduce

```bash
# fast unit + determinism + greedy-peel traversal tests (default set)
pytest tests/test_towplanner_teardown.py

# the real-witness structural-invariant guard (slow lane; budget-capped for runtime)
pytest -m slow tests/test_towplanner_teardown.py
```

```python
# the authoritative ~30-min verdict (full 8000 budget), for the record:
from hangarfit.loader import load_layout
from hangarfit.towplanner import reverse_teardown_probe
r = reverse_teardown_probe(load_layout("examples/herrenteich/layout.yaml"))
# r.cleared  -> False        (a derived property: True iff r.stuck is empty)
# r.order    -> ('ctsl',)
# r.stuck    -> ('aviat_husky','cessna_140','fk9_mkii','wild_thing','zlin_savage')
# r.blocking -> 5 teardown_egress Conflicts, each detail tagged "[budget-exhausted]"
```

The committed `@slow` test caps the per-plane budget (`max_expansions=600`) so the slow lane stays ~1 min;
its assertions are verdict-agnostic (the order ∪ core partition the towable fleet, one blocking conflict
per stuck body), so the cap trades the authoritative 8000-budget verdict for runtime without weakening the
contract under test. The headline 8000-budget verdict lives here, in the spike, where it can be re-run.

## What this buys / what it does not

- **Buys:** a deterministic, feasibility-grounded verdict that the dense Herrenteich fill admits **no
  monotone tow order** at the deployed grid + budget — the decision that move-aside's relocation (Rung E)
  is genuinely required, and the closing of the reverse-only-writer and search-guidance branches.
- **Does not:** route anything or change any plan. The probe only *removes*, never *relocates*; by the
  monotonicity argument it shares the forward fill's ceiling, so it cannot itself seat a body the fill
  can't. Raising the ceiling is Rung E (move-aside), and even then the `fk9↔cessna` cm-scale parallel-park
  may remain a documented manual-insertion case (spec §2 caveat).
