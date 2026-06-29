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

## Rung E (move-aside): re-baseline — the wall is unmoved on the budget-bound all-8

Rung E adds **move-aside**: when the non-displacing fill search deadlocks *within budget*, a
second phase temporarily relocates a parked aircraft to an apron-out staging pose, routes the stuck
aircraft past it, and returns it — a depth-1, apron-gated relocation emitting a valid multi-leg plan
(byte-identical when no shuffle is needed; ADR-0003). Re-running the all-8 witness route (the same
direct `plan_fill`, budget 8000) **with** `apron_depth=auto` (which is what *enables* move-aside)
vs **without**:

| run | result | bail body | reason | wall-clock |
|---|---|---|---|---|
| no apron (Rung B) | **BAILS** | `zlin_savage` | global fill budget (8000) exhausted | ~70 s |
| `apron=auto` (Rung E) | **BAILS** | `zlin_savage` | global fill budget (8000) exhausted | ~114 s |

**The ceiling is unmoved: same body, same budget-exhaustion reason.** The bail message is the
budget *raise* (`...budget (8000) exhausted`), not the in-budget `no feasible tow order`
deadlock — so **phase 1 raises before phase 2 ever runs**, and move-aside never engages on the
dense all-8. This is the predicted behaviour: the all-8 corridor is *budget-bound* (it grinds the
full cap threading the nest, see "the bail is budget-exhaustion" above), and move-aside only adds
reachability for an **in-budget** deadlock (a cyclic block the search *disproves* cheaply). The
`zlin_savage`/`fk9↔cessna` parallel-park stays a documented manual-insertion case.

The ~70 s → ~114 s rise (≈1.6×) is **the apron's own routing cost** (#499: the apron-out forward
+ reverse cones × the y-samples enlarge each route's start set), **not** Rung E's two-phase change:
because phase 1 raises, the two-phase driver's `if result is None` block is never reached, so the
move-aside path is provably inert on this regime. Without an apron, Rung E is byte-identical and
same-speed (move-aside is gated off when `apron_depth_m <= 0`).

**Perf gate (no regression).** The fast `--gate` set is unchanged (correctness + determinism `ok`
on every fast regime; `roomy_three_apron` ≈ 27 s, on par with its non-apron sibling
`roomy_three_spread_on`). The phase-2-sensitive heavy disprove `tight_six_apron` likewise bails at
phase-1 budget exhaustion (`budget (4000) exhausted`, ~80 s, `det ok`) — phase 2 never runs, so the
two-phase change adds no cost there either. (Reproduce the all-8 re-baseline by routing
`examples/herrenteich/layout.yaml` via `plan_fill(..., max_total_expansions=8000)` with
`load_layout(..., apron_depth="auto")` vs `None`.)

**Why no small fixture proves the *capability*.** A targeted search of ~480 synthetic 2–3 plane
configs found **zero** move-aside resolutions. In a monotone fill any plane can be placed first into
an empty hangar, so a *small* deadlock is necessarily a symmetric mutual block — and the displaced
body's return leg is then blocked for the very same reason the stuck body was, so depth-1 move-aside
cannot resolve it. Move-aside adds reachability only for **larger cyclic cores** (the ≥5-body core
the Rung C reverse-teardown probe found), which a unit fixture can't capture. Rung E's capability is
therefore exercised by the real phase-2 *execution* on real geometry (the `@slow`
`test_move_aside_real_geometry_unresolvable_block_runs_phase2_and_bails`: the real `_staging_poses`
+ `plan_path` return legs run and bail cleanly) plus the control-flow unit tests
(`tests/test_towplanner_fill.py`); it is **not** a guaranteed all-8 route.
