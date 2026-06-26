# Design: Analytic parallel-park macro for the fk9↔cessna front-door corridor

**Issue:** #844 (linchpin sub-problem) · **Date:** 2026-06-26 · **Status:** ⛔ **REFUTED — implemented & measured NO-GO (2026-06-26)**

> **OUTCOME:** The macro was implemented (geometry + default-OFF injection, both reviewed and
> determinism-guard-clean) and gated at the PoC. It **does not** route the fk9↔cessna pair at the
> deployed 0.5 m/15° grid at any budget ≤32 k, and adds little even at the witness's fine 0.25 m/10°
> grid. Root cause: #844 is a **fine-resolution search-efficiency wall** — the macro adds *longer-range
> moves on a lattice, not resolution*, so it can't represent the sub-grid nook maneuver the corridor
> needs. The implementation was **discarded**; the team **pivoted to #840 learned/guided motion**.
> Full gate numbers and the root-cause probes are in
> [`docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md`](../../spikes/herrenteich-fk9-cessna-lateral-shuffle.md)
> (§ "Parallel-park macro: implemented and refuted"). This spec is kept as design provenance.

Grounding evidence (do **not** re-investigate):
[`docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md`](../../spikes/herrenteich-fk9-cessna-lateral-shuffle.md)
and [`docs/spikes/herrenteich-all8-tow-routing-rootcause.md`](../../spikes/herrenteich-all8-tow-routing-rootcause.md).

---

## 1. Problem

The real Herrenteich all-8 is statically valid and provably routable in real life (the
club parks all eight daily by monotone fill), but the deployed tow planner cannot
auto-route the full fill. Root-causing split the failure into **two independent
blockers**; this spec scopes **only the hard linchpin**:

> **fk9_mkii ↔ cessna_140 front-door corridor** — two genuine high-wingers that
> mutually space-exhaust *in isolation* at the deployed **0.5 m / 15°** own-gear grid.
> No fill order helps the pair (they exhaust with nothing else present), so it is a
> search-**efficiency** problem, not an ordering or physics problem.

A feasible **own-gear** path provably **exists**: own-gear A\* at a finer **0.25 m / 10°**
grid finds a collision-free, no-carts path — **96,949 expansions / 39 min (~12 exp/s)**,
exact-oracle-validated. The deployed 0.5 m/15° grid is simply too coarse to *represent*
the deep multi-cusp lateral "parallel-park" shuffle that threads the corridor, and a
uniformly finer grid finds it far too slowly to ship.

The second blocker (husky front-cluster **ordering** — an order-search-efficiency problem,
likely cheaper) is **out of scope** here and becomes a separate follow-up spec.

### Ruled out (do not re-investigate — see the spike docs)

Carts / `on_carts: true` (the *diagnostic*, not a faithful fix — the club hand-shuffles on
own gear); pivot-point fidelity (mains ≈ pose origin); infeasibility (refuted by the witness);
wing-taper / wing-tip shape; removing the Scheibe; turn-radius / pivot / reverse / strafe
modelling; aircraft dimensions; clearance budget; **finer-grid-everywhere and raised-budget-
alone** (96,949 exp / 39 min is far past any shippable wall — a budget bump alone cannot fix
this; it must be *paired* with a lever that cuts the cost, not just the cap).

## 2. Strategy

Give the search a **closed-form lateral-displacement macro-action** so it no longer has to
*rediscover* the deep parallel-park cell-by-cell. The macro compresses what would be ~10
grid micro-steps of expensive discovery into a single deterministic, oracle-validated
successor, attacking the search **depth** directly (the lever the spike's analysis points at).

Both planes are car-like (minimum turn radius `r > 0`), so a heading-preserving lateral shift
is an **alternating-cusp Reeds–Shepp word**. The planner already enumerates a single *optimal*
RS word (node→goal) at every expansion (`towplanner.py:2645`), but the optimal short word for a
small lateral offset has a swept polygon that collides with the corridor walls and is discarded.
The deep parallel-park is a **longer, tighter-envelope** RS word the planner never tries. The
macro injects exactly that family.

## 3. The macro family

A **small fixed family**, enumerated in deterministic order, each member a closed-form
`Segment` list producing one successor pose (shifted laterally, **same final heading**,
net-forward ≈ 0):

| Parameter | Values | Rationale |
|---|---|---|
| direction | {left, right} | shift either way along the corridor |
| lateral amount Δ | {1 cell, 2 cells} ≈ 0.5–1.0 m | the displacement step |
| tightness | {2-cusp, 4-cusp} | more cusps → shorter arcs → **smaller swept envelope** — the lever that threads a corridor a short word cannot |

⇒ a handful of candidate macros per node (2 × 2 × 2 = 8 max). **Granularity is a Phase-1
empirical decision** — the family may be pruned or extended once the PoC measures what the
corridor actually needs; §3 is the *starting* hypothesis, not a frozen contract.

Each macro's resulting pose is computed in closed form (the macro's own turn radius `r` is the
mover's `effective_turn_radius_m()`). Intermediate macro poses are swept-collision-checked but
are **not** bucketed into the closed set — only the resulting pose is `_cell()`-bucketed.

> **Key structural property:** the macro adds **long-range edges to the existing grid graph**
> without changing the node set, the `_cell()` discretization, or the determinism anchor. The
> state space is untouched; the macro is purely additional, deterministically-ordered edges.
> This is why OFF is trivially byte-identical and ON stays deterministic.

## 4. Where it plugs in (`towplanner.py`)

| Seam | Location | Change |
|---|---|---|
| Motion primitives | `_primitives()` ~`:1920–1969` | append a **flag-gated** macro set *after* the 6 fixed own-gear micro-primitives (preserve fixed fan order) |
| Expansion loop | ~`:2690–2721` | each macro's swept path validated by the existing `path_first_conflict` oracle — same checker as micro-primitives and RS shots (**no surrogate**; honors the #694 product-checker contract) |
| Cost / tie-break | `_seg_cost` ~`:1981`, `CUSP_PENALTY` `:619`, heap counter `:2712` | g-cost = Σ segment costs + cusp penalties; equal-`f` ties broken by the existing monotonic counter (no new tie-break surface) |
| Flag plumbing | `plan_path` `:2418`, `plan_fill` `:1524`, CLI `solve`/`view` | a `park_macro: bool` param threaded through, surfaced as `--park-macro` (name provisional) |

## 5. Delivery & gating

**Opt-in flag, default-OFF, byte-identical.** When OFF, no macro edges are enumerated, so the
search tree — and every chosen plan — is **bit-identical to current behavior** on all existing
fixtures and canaries. This matches the project's "every lever is default-neutral" convention
(apron depth-0, `--workers`≡serial, `--spatial-tokens`). The spec writes an explicit
**promotion-to-default criterion** (§8) so the deployed default has a path forward.

## 6. Build phases

### Phase 1 — Probe & PoC (no production change; de-risks the geometry first)

1. **Expansion-profile measurement.** Instrument the reproducible witness probe (reconstructable
   from the spike doc) to classify each expanded node as *open-space approach* vs
   *in-corridor shuffle* (by clearance-to-nearest-obstacle, or by corridor `y`-region), and report
   the split of the ~97 k witness expansions. Confirms the macro targets the dominant cost.
2. **Macro PoC.** Derive the lateral-park macro family; drop it into the probe; verify the
   **isolated fk9↔cessna pair routes own-gear at the deployed 0.5 m/15° grid within a stated
   per-plane budget** (target: at or modestly above the current `_MAX_EXPANSIONS = 8000`; the
   exact number is an output of this phase).
3. **Go/no-go.** If the PoC routes within a shippable budget → proceed to Phase 2. If not →
   stop and reconsider (adaptive grid is the documented fallback) **without** having paid the
   integration cost.

### Phase 2 — Production integration (TDD)

- Macro added to `_primitives()` behind the flag; flag threaded through `plan_path`/`plan_fill`
  and the `solve`/`view` CLI.
- **Tests:**
  - **OFF byte-identity** — flag OFF reproduces the pre-change plan on representative fixtures
    (the determinism-guard contract surface).
  - **ON routing** — flag ON routes the isolated fk9↔cessna pair within the Phase-1 budget.
  - **ON determinism** — flag ON, same scenario + seed → byte-identical `MovesPlan`.
  - **Oracle validation** — every macro path is `path_first_conflict`-checked (no surrogate).
- **Review arc:** `determinism-guard` (mandatory for `towplanner.py`) + `code-reviewer`, plus
  any specialist the diff triggers.

### Phase 3 — Promotion (criterion only; **not** executed in this spec)

See §8.

## 7. Success criteria

- The **isolated fk9↔cessna pair routes own-gear** at the deployed 0.5 m/15° grid within a
  stated per-plane expansion budget, flag ON.
- Flag **OFF is byte-identical** across all existing fixtures/canaries (no determinism-canary churn).
- **Determinism:** flag ON, same scenario + seed → byte-identical plan (ADR-0003 contract).
- All macro paths validated by the production `path_first_conflict`/`collisions.check` oracle.

## 8. Promotion-to-default criterion (written now, executed later)

Flip the default to ON in a **separate PR** once macro-ON is, across the **full fixture suite**:
(a) byte-stable (same scenario+seed → identical plan), (b) non-regressive (routes ≥ what OFF
routes; no fixture that currently routes regresses), and (c) within the wall-clock budget. That
PR re-baselines every determinism canary in the same change.

## 9. Out of scope

- **Husky front-cluster ordering** — the second all-8 blocker (order-search efficiency); separate
  follow-up spec.
- **Promotion execution** — §8 is a written criterion only.
- **#840 learned/guided motion** — the separate learned-motion epic; this fix is deterministic.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| The bounded macro family may not fully thread the corridor (the witness path may need richer maneuvers than a single lateral shift expresses). | Phase-1 PoC **de-risks before integration**; adaptive grid (#844 direction 3) is the documented fallback. |
| Per-macro oracle validation is costlier per expansion (multi-segment sweep). | Far fewer expansions overall; Phase-1 measures **net** wall-clock, not just expansion count. |
| The macro may need a *modest* per-plane budget bump alongside it (bump-alone provably fails). | Acceptable if documented and the **combination** routes where bump-alone does not; Phase-1 measures the pair. |

## 11. Determinism contract (ADR-0003) — invariants this design must hold

- Same scenario + same seed → **bit-identical** `MovesPlan` (with the flag in a fixed state).
- Macro members enumerated in a **fixed order**, appended after the fixed micro-primitive fan.
- `_cell()` discretization and the closed-set keying are **unchanged** (macro intermediate poses
  are not bucketed; only resulting poses are).
- Equal-`f` ties broken by the existing **monotonic counter** — no new tie-break surface.
- Flag **OFF** ⇒ zero macro edges ⇒ byte-identical to the pre-change planner.
- `determinism-guard` validates ON-reproducibility and OFF-identity in the Phase-2 review arc.
