# Herrenteich routing-ceiling baseline (#667 Rung B)

**Status:** baseline measured — **complete**. **Date:** 2026-06-27.
**Issue:** [#861](https://github.com/DocGerd/hangarfit/issues/861) (Rung B of the
[#667](https://github.com/DocGerd/hangarfit/issues/667) shuffle-aware tow-routing program).

> The objective routing-ceiling baseline every later #667 rung (C/D/E) is graded against.
> Spec: [`docs/superpowers/specs/2026-06-27-667-shuffle-aware-tow-routing-design.md`](../superpowers/specs/2026-06-27-667-shuffle-aware-tow-routing-design.md) §4 Rung B.

---

## TL;DR

The real Herrenteich dense fleet is **statically valid parked** (`hangarfit check` exits 0)
but does **not** tow-route within a bounded expansion budget. Routing the known-valid
witness layouts directly (no solve) via `plan_fill`:

| witness regime | aircraft | hand-placed | global cap | routes? | bail | wall (local) |
|---|---|---|---|---|---|---|
| `herrenteich_all_eight` | 8 | 2 (Scheibe + Stemme) | 8 000 | **NO** | budget-exhausted, deepest-unplaced `zlin_savage` | ~64.6 s |
| `herrenteich_today` | 9 (+ Fuji) | 2 | 8 000 | **NO** | budget-exhausted, deepest-unplaced `zlin_savage` | ~65.4 s |

(`n_planes` counts aircraft; `layout_today.yaml` also carries ground objects — Caddy, fuel + glider trailers.)

Measured via `python -m bench.profile_pipeline --regime herrenteich_all_eight --regime herrenteich_today`
(2026-06-27, local): both report `place_s 0.000`, `routed 0/1`, `valid ok`, `paths ok`, `det ok`, and the
bail note `un-routable: zlin_savage (no_feasible_path: global fill expansion budget (8000) exhausted)`.

Move-aside (Rung E) must flip the `routes?` verdict. This baseline is RNG-free and
deterministic (ADR-0003), so it is a stable regression anchor.

## Why route the witness, not solve

`solve` (RR-MC) **does not reproduce** the dense all-8 nest within budget — `examples/herrenteich/scenario.yaml`'s
own header records it, and it is the founding observation of the 2026-06 spike series. So a
*solve-then-route* bench regime would measure **placement-failure**, not the **routing** ceiling
that move-aside is built to raise — vacuous by the feasibility-witness principle (the #832 lesson:
a "method reaches what the baseline misses" claim is only meaningful against a layout *proven* to
exist). The hand-authored `layout.yaml` / `layout_today.yaml` **are** that proof (a valid all-8 the
solver can't find), so Rung B routes them directly: `placement_s == 0.0`, the route is the whole
measurement.

## Why the verdict is binary (not a routed-body count)

`plan_fill` is **all-or-nothing**: it returns a complete `MovesPlan` or raises
`NoFeasiblePlanError` naming the *deepest unplaceable* body. There is no partial "routed N of 8"
return. So Rung B reports the honest binary baseline — *routes-fully? · bail-body · conflict-kind ·
conflict-detail · bounded wall-clock · determinism*. A partial routed-body **count** (e.g. the
husky-gate's "6 routed vs 4") needs the reverse-teardown traversal of **Rung C**, not this baseline.

## The bail is budget-exhaustion, not an early geometric wall

A control: doubling the global cap (8 000 → 16 000, the `solve` default) **roughly doubled the wall-clock**
for the all-8 (~65 s → ~130 s; an earlier scout run) and bailed on the **same** body (`zlin_savage`). An early *geometric* dead-end
would bail in roughly constant time regardless of cap; instead the planner grinds the full budget
threading the dense nest and then names the deepest body it never reached. So the operative ceiling
is **expansion budget**: the all-8 fill does not route within any *affordable* budget. (This is
consistent with the known `fk9↔cessna` corridor needing ~97 k expansions for that pair alone — the
whole fill is far beyond a shippable cap; see
[`herrenteich-fk9-cessna-lateral-shuffle.md`](herrenteich-fk9-cessna-lateral-shuffle.md).) The
`conflict.detail` string (`global fill expansion budget (8000) exhausted`) makes this explicit in
the bench note; `8000` is the affordable cap (matches `full_nine_spread_on`) and bounds the
un-routable disprove.

## How to reproduce

```bash
# the two witness regimes (heavy — excluded from the default fast set)
python -m bench.profile_pipeline --regime herrenteich_all_eight --regime herrenteich_today

# the regression guard (slow — excluded from the default pytest run + CI)
pytest -m slow tests/bench/test_profile_pipeline_witness.py
```

The witness regimes are `heavy=True`, so the default fast `--gate` (the CI bench-gates job, fast
set only) never grinds the multi-minute route. Their `_SPEED_CEILING_S` entries are **documentary**
(they arm a manual `--heavy --gate`).

## What this buys / what it does not

- **Buys:** a deterministic, feasibility-grounded number that says *the dense Herrenteich fill does
  not tow-route* — the wall Rungs C–E are measured against, and the anchor the slow regression test
  guards.
- **Does not:** touch the wall. Rung B is pure measurement (bench-only; no `src/hangarfit/` change).
  Raising the ceiling is Rung E (move-aside), and even then the `fk9↔cessna` cm-scale parallel-park
  may remain a documented manual-insertion case (spec §2 honest caveat).
