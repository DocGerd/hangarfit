# Solver spread robustness — best-of-all-basins selection

**Issue:** #267 — *solver: spread post-pass is seed-fragile — ~1/3 of seeds emit a nested-pair layout*
**Milestone:** Phase 2c — Solver polish
**Status:** Design approved (brainstorming), pending implementation plan
**Date:** 2026-05-27

---

## Problem

`solve()` returns, for `alternatives=1`, the **first** valid basin its restart loop
lands in (`solver.py:194-232`): it finds a valid layout, runs the `_spread`
post-pass on it, accepts, and breaks the outer restart loop (`solver.py:263`).
`_spread` (`solver.py:599-717`) is a greedy single-plane hill-climb on
`_inter_plane_energy` (`Σ exp(−gap/scale)`, a smooth maximin surrogate). It can
only polish *whichever* basin the restart handed it — a validity-preserving
single-plane nudge cannot cross the validity barrier out of a nested pair (a low
fuselage legally tucked under a high wing, min plan-view gap 0.0 m).

Consequence: spread quality is decided by which basin the loop happened to reach
first, i.e. by the seed. A seed sweep on a representative 5-plane fill showed
~1/3 of seeds settle with a nested pair (min gap 0.0 m) while the rest spread to
1.7–2.5 m — same scenario, same fleet, same hangar, only the restart seed differs.
The layouts are **genuinely valid** (height-aware collision model permits the
nesting); this is a **spread-quality / robustness** gap, not a correctness bug.

This is orthogonal to milestone #23 (towplanner v2): #23 lives in `towplanner.py`
and addresses tow-routability of tight layouts; it does nothing for the solver
inconsistently emitting the well-spread layout it is already capable of producing.

## Locked decisions

| Axis | Decision |
|---|---|
| **Cost model** | Full budget, best-of-all. `alternatives=1` now uses the whole `budget_s` / `max_restarts` to find the best-spread valid layout (accepted wall-clock change for the common case; layout quality > latency for an on-demand exception tool). |
| **Selection metric** | Maximin gap, energy tiebreak. Primary key = maximize the minimum pairwise plan-view gap (the acceptance metric); near-ties broken by lower `_inter_plane_energy`. |
| **Scope** | All alternatives (K>1 too) — unified collect-then-select pool. |
| **Diagnostic** | Surface achieved min-gap in `SolverDiagnostics` + human CLI + `--json`. |

## Approach: collect-then-select pool

Chosen over two alternatives:

- **Sub-seed wrapper** (run `solve()` N times with `seed, seed+1, …`, keep best):
  re-runs infeasibility checks, needs sub-seed plumbing, N is arbitrary. Rejected.
- **Incremental best-tracking** (running best across restarts): works for K=1 but
  tangles with the K>1 diversity gate. Rejected once scope = all alternatives.

The **pool model** unifies both. The restart loop already produces a multi-restart
trajectory from one rng; we stop discarding it. K=1 is just the K=1 case of the
same selection.

### Data flow

1. **Restart loop** (`solver.py:155-264`): remove the early break on first
   valid-accept and on `len(accepted) >= alternatives`. The loop runs to
   `budget_s` / `max_restarts`. Each time a restart's descent reaches a valid
   layout (`score == (0, 0.0)`), run `_spread` as today, compute
   `(min_gap, energy)`, append a pool entry
   `(layout, min_gap, energy, restart_index)`, and restart — instead of
   accept-and-stop.
2. **Selection** (new `_select_spread_diverse`): sort the pool by
   `(−min_gap, energy, restart_index)` — best maximin gap first, energy tiebreak,
   `restart_index` as the deterministic final key. Greedily pick entries that pass
   `_is_diverse_enough` against those already picked, until `alternatives` chosen.
   K=1 trivially picks the top-spread basin (diversity vacuously true on the empty
   accepted set). **Behavior change for K>1:** returned `layouts` are now ordered
   best-spread-first (sorted), not discovery-order — beneficial (`layouts[0]` is the
   roomiest), but the plan must check existing K>1 tests that may assume
   discovery-order.
3. **Status** (meaning unchanged): `found` (K selected) / `found_partial`
   (0 < n < K) / `exhausted_budget` (empty pool — no valid basin found).
   `best_partial` tracking for the exhausted case is unchanged. Tow-planning runs
   on the selected K, unchanged (RNG-free, best-effort `plans[i]=None` per #197).

## Components & touch-points

- **`_spread_quality(placements, scenario, scale) -> tuple[float, float]`** *(new)* —
  returns `(min_gap, energy)` in one pass over plane-pairs, sharing the shapely
  world-parts distance logic with `_inter_plane_energy` (`solver.py:569-596`).
  `min_gap = math.inf` for <2 planes.
- **`_select_spread_diverse(pool, alternatives, diversity) -> list[...]`** *(new)* —
  pure, deterministic selection; unit-testable in isolation with no solving.
- **`solve()`** (`solver.py:43-337`) — restart loop restructured to build the pool
  and call selection; assemble `layouts` + aligned `min_pairwise_gap_m`.
- **`SolverDiagnostics`** (`models.py:653-707`) — add:
  - `min_pairwise_gap_m: tuple[float, ...] = ()` — index-aligned with `layouts`;
    achieved min plan-view gap per returned layout (`math.inf` for <2 planes,
    surfaced as `null` in JSON / "n/a" in human text).
  - `valid_basins_found: int = 0` — pool size; shows how much choice best-of-all
    had.
  - `diversity_rejected_count` keeps its field; **semantics shift** to "pool
    entries examined and rejected by the diversity gate during selection." Stays
    `0` for K=1 (selection breaks after the first, vacuous pick). Documented on the
    field.
- **CLI** — `_emit_solve_human` (`cli.py:209`) adds a per-layout min-gap line;
  `_emit_solve_json` (`cli.py:564-590`) adds `min_pairwise_gap_m` +
  `valid_basins_found` to the diagnostics dict.

## Determinism (ADR-0003)

Partially preserved — the scope depends on the termination gate:

- **`max_restarts`-bounded:** fully reproducible across runs and machines. The
  same single `rng`, same restart order, pool built in restart order, and a
  fully-ordered selection sort (`restart_index` is the final tiebreak so no two
  entries compare equal). Tow-planning is RNG-free. Same seed + same
  `max_restarts` → byte-identical output. The canary tests in
  `tests/test_solver_canaries.py` use this path and remain the
  cross-machine determinism canaries.
- **Pure wall-clock `budget_s`-bounded:** the number of restarts that complete
  before the timer fires depends on machine speed and load. Pool size therefore
  varies between machines (or runs under different load). When two basins are
  near-tied on maximin gap the selected layout can differ — same seed does NOT
  guarantee byte-identical output across machines in this mode.

The default CLI path uses `budget_s` for responsiveness. This timing-dependence
is an accepted tradeoff; see the ADR-0003 amendment dated 2026-05-27 for the
full rationale. For guaranteed cross-machine reproducibility, bound by
`max_restarts`.

## Testing strategy

Correctness is pushed into pure functions so the default suite stays fast and the
core guarantee is non-flaky.

- **Pure unit (primary regression, instant, non-flaky):**
  - `_select_spread_diverse` — a hand-built pool with a nested entry
    (`min_gap=0.0`) and a spread entry (`min_gap=2.0`): assert the spread entry
    wins. This directly encodes "a nested basin never wins" without depending on
    the RNG ever producing one. Also: diversity enforcement, K-partial selection,
    empty pool, deterministic tiebreak (energy then `restart_index`).
  - `_spread_quality` — min-gap math on hand-placed layouts; `<2 planes → inf`.
- **Integration (default suite, ~seconds — proves wiring):** a 2–3-plane
  nested-prone fixture under `tests/fixtures/` (one low-fuselage + one high-wing in
  a roomy hangar, reproducing the nesting pathology), bounded `max_restarts`
  (~15–25, deterministic, sub-second/solve), seeds `{3, 7, 8, 9}` (issue-flagged
  nesters); assert `min_pairwise_gap_m ≥ 0.5` for each. (0.5 m is the issue's
  `pairs<0.5m` nested-pair indicator.) Confirms the loop builds the pool, selection
  is invoked, and diagnostics align.
- **`@slow` (on demand / nightly, excluded by default per `pyproject.toml`):** full
  seeds 1–30 sweep on the larger fixture asserting the threshold for every seed —
  deep confidence without taxing the default run.

The issue cited `scenarios/demo_5planes.yaml`, which was an ad-hoc demo file (never
committed); the plan adds a committed `tests/fixtures/` equivalent instead.

## Out of scope (YAGNI)

- No new `SearchConfig` knob (full-budget best-of-all reuses `budget_s` /
  `max_restarts`).
- No change to `_spread`'s internals, `_inter_plane_energy`, the `_score` tuple, or
  the diversity thresholds.

## Docs

- **ADR-0008** (inter-plane spread soft preference) gets an addendum: the post-pass
  is now applied across *all* valid basins found within budget, with best-maximin-gap
  selection (subject to the diversity gate), rather than to the single first-found
  basin. The greedy single-basin `_spread` itself is unchanged; the robustness comes
  from selecting among basins.
- The solver spec section referenced by `_spread` / the restart loop is updated to
  describe the pool + selection.
