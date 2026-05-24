# Inter-plane gap maximization (spread) — Design Spec

**Date:** 2026-05-24
**Status:** Drafted (brainstorming complete; pending user spec review then plan writing)
**Issue:** #145 (milestone #14 — Phase 2b — Solver realism)
**Predecessor:** v0.6.1 (Solver polish follow-ups, shipped 2026-05-23)
**Touches:** `src/hangarfit/solver.py`, `src/hangarfit/models.py`, `src/hangarfit/cli.py`, `tests/test_solver_search.py`, `tests/test_solver_fixture_matrix.py`, `tests/test_cli_solve.py`, `docs/adr/0008-*.md`, `docs/architecture/05-*.md`, `docs/architecture/06-*.md`, `docs/architecture/08-*.md`, `CLAUDE.md`

---

## 1. Problem statement

When `solve()` finds a valid layout, the planes land wherever the conflict-resolution descent happened to leave them — typically clustered, with inter-plane spacing that is merely *legal* (≥ `clearance_m`), not *comfortable*. Surfaced 2026-05-23 during the v0.6.1 visual walkthrough.

The scoring tuple `(conflict_count, total_penetration_m2)` measures only **illegal** overlap. Once a layout reaches `(0, 0.0)` the descent stops; it has no concept of *legal* inter-plane gap, so two planes parked 4 m apart and two parked 0.4 m apart are scored identically.

This spec adds a soft preference that, after the hard zero-conflict constraint is met, **maximizes inter-plane separation** so a human towing a plane in or out has comfortable clearance past its neighbors.

> **Objective-direction note.** Issue #145 as originally filed asked to *minimize* inter-plane gap (pack planes tightly against walls). During brainstorming on 2026-05-24 the user reversed this: the real goal is to **maximize** the gap (spread planes apart) subject to zero overlap. The "minimum overlap" half of the user's framing is already the solver's hard constraint. Issue #145's title, body, and acceptance criteria are rewritten to match as step 0 of implementation (see §10).

## 2. Goals and non-goals

### Goals

1. After a trajectory reaches validity, refine it toward **maximum inter-plane separation** while preserving validity (zero conflicts, in-bounds, bay respected).
2. Use a metric that protects the **minimum** pairwise gap (the clearance that actually bites when towing), not merely an aggregate.
3. Keep the change **isolated** from the conflict-resolution descent and the determinism contract (ADR-0003).
4. On by default (the tool produces realistic layouts out of the box), with an explicit off switch.

### Non-goals

- **No change to conflict resolution.** The RR-MC descent, its `(int, float)` score tuple, the `best_partial` tracking, and the score sentinel are untouched.
- **No wall-adherence term.** Spreading already pushes planes wallward incidentally; an explicit "pull to wall" term is a separate concern (file a sibling issue if wanted).
- **No aesthetic alignment** (parallel headings, straight rows).
- **No movement-sequence / tow-path planning** — out of scope per CLAUDE.md; that is Phase 3a (milestone #16).
- **No z-aware nesting reward** (see §3 the plan-view gap limitation).
- **No new CLI surface beyond a single off switch** (no `--spread-scale` until a concrete need surfaces).

## 3. Decision log (brainstorming outcomes)

| Question | Decision | Why |
|---|---|---|
| Optimize direction? | **Maximize** inter-plane gap (spread), not minimize (pack). | User reversed issue #145 during brainstorming 2026-05-24: easier tow-in/out, less wingtip-strike risk. |
| Gap metric? | **Smooth repulsion-energy surrogate for maximin**: minimize `E = Σ exp(−gap/scale)` over plane pairs. | Pure maximin (p-dispersion) is the exact objective but is *flat* for a hill-climber (only the closest pair has gradient). Max-sum is smooth but Kuby (1987) shows it gives **uneven** spreads (clusters subsets at extremes). A repulsion energy is smooth *and* weights close pairs heavily — it protects the minimum gap and gives a gradient everywhere (the Riesz-energy → maximin-separation principle). |
| Edge-to-edge or centroid gap? | **Edge-to-edge** (shapely `polygon.distance`), plan-view. | Shape-aware — honors wingtip-to-wingtip clearance, which is the towing-relevant quantity. Centroid distance is shape-blind. |
| Kernel form? | Bounded `exp(−gap/scale)`. | No singularity (vs. inverse-power `1/gap^s`); a single near-touching pair can't dominate; has a natural length scale. |
| Structure? | **Post-pass `_spread()` function** (approach B), not a fused descent (approach A). | The descent is *conflict-driven* — at zero conflicts there is no plane to perturb, so A is two regimes bolted into one function, not "one clean algorithm". B keeps the hard-feasibility code and the `(int,float)` tuple untouched, isolates the soft logic, and makes the toggle a trivial skip. Lower risk **and** more readable. |
| Activation? | **Default ON** + `SearchConfig.spread` field + CLI `--no-spread`. | The tool should produce realistic layouts without the user knowing a flag exists. Off-switch preserves old behavior for tests/power users. |
| Determinism? | Shared seeded `rng`; `spread=False` skips the call so the RNG stream is byte-identical to today. | Preserves ADR-0003; existing determinism tests hold unchanged under `spread=False`. |
| `scale` default? | Adaptive: `0.2 × min(hangar.width_m, hangar.length_m)`, overridable via `spread_scale_m`. | Keeps the kernel in its sensitive range across hangar sizes. The constant is validated visually on the canary; it is a default, not a hardcode. |
| Stall knob? | Reuse `search.k_stall` for the spread phase. | Fewer knobs (YAGNI). A dedicated `spread_k_stall` can be added if tuning demands it. |

## 4. The repulsion energy

New pure helper in `solver.py`:

```
_inter_plane_energy(placements: dict[str, Placement], scenario: Scenario,
                    scale: float) -> float
```

- For each unordered plane pair `(i, j)` drawn from `placements`, compute
  `gap_ij` = **minimum plan-view edge-to-edge distance** over all part-pair
  combinations, via shapely `polygon.distance()` on the world-part polygons
  (reuse `geometry.aircraft_parts_world`, as `collisions.check` does).
- `E = Σ_{i < j} exp(−gap_ij / scale)`.
- Lower `E` = planes further apart. Bounded per pair in `(0, 1]`; `E = 0.0`
  when fewer than two planes are present.

**Properties (these become unit-test assertions):** symmetric in `i, j`;
strictly decreasing in each `gap_ij`; `0.0` for ≤ 1 plane; finite and
non-negative always.

**Known v1 limitation — plan-view only (no z).** The gap ignores height, so
the single low-wing plane that could legally nest plan-view-overlapping under
a strut-braced high wing is mildly *de-nested* (spread does not reward the
nest). The **hard constraint still permits** nesting; spread merely does not
prefer it. Documented in ADR-0008 as a deliberate v1 simplification; a
z-aware kernel is a possible follow-up.

**Alternative considered — scale-free inverse-power `1/(gap+ε)^s`.** Rejected
for v1: the near-singular behavior lets one near-touching pair dominate the
sum and complicates the budget/stall behavior. The bounded exponential is
more predictable. Recorded in ADR-0008.

## 5. The `_spread` hill-climb

```
_spread(placements, scenario, rng, search, *, start, budget_s,
        pinned_planes) -> dict[str, Placement]
```

Input is a **valid** layout (score `(0, 0.0)`). Mirrors the descent's shape
but is driven by energy:

1. `scale` = `search.spread_scale_m` if set else `0.2 × min(width, length)`.
2. `current_energy = _inter_plane_energy(placements, scenario, scale)`.
3. Loop (large iteration cap; real exit via stall/budget):
   - Budget check: `time.monotonic() - start >= budget_s` → break.
   - `movable` = non-pinned, non-maintenance plane ids. If empty → break.
   - `target = rng.choice(sorted(movable))` (deterministic ordering before
     the RNG draw, matching `_descent_step`).
   - Generate candidates by reusing the existing `_descent_step` generation
     via `_perturb_plane`: `candidates_per_iter − 2` small Gaussian nudges +
     one large jump + one 180° flip (same `pos_sigma_m` / `heading_sigma_deg`).
   - For each candidate: build the trial `Layout` (skip on `ValueError` —
     cart rule etc.); **require it stays valid** (`_score(trial) == (0, 0.0)`,
     reject any move that re-introduces a conflict, leaves bounds, or intrudes
     the bay); compute trial energy; track the best by **strictly lower
     energy**, tie-broken by **smallest displacement** (same smooth-trajectory
     tiebreak as `_descent_step`).
   - If a candidate improved: adopt it, update `current_energy`, reset stall
     counter. Else advance the stall counter.
   - Stall: `iters_since_improved >= search.k_stall` → break.
4. Return the best (lowest-energy, still-valid) placements.

**Validity guarantee:** input is valid and only valid candidates are ever
adopted ⇒ output is always valid. `_spread` can only improve or no-op; it can
never turn a valid layout invalid.

**Integration point** — `solve()`'s descent loop, the `current_score == (0, 0.0)`
branch (currently `solver.py:175`), before the diversity check:

```
if current_score == (0, 0.0):
    if search.spread:
        placements = _spread(placements, scenario, rng, search,
                             start=start, budget_s=budget_s,
                             pinned_planes=pinned_planes)
    candidate_layout = Layout(fleet=..., hangar=...,
                             placements=tuple(placements.values()),
                             maintenance_plane=...)
    if _is_diverse_enough(candidate_layout, accepted_layouts, diversity):
        accepted_layouts.append(candidate_layout)
    else:
        diversity_rejected_count += 1
    break
```

Each accepted layout is spread before the diversity check. **Known
interaction:** spreading drives layouts toward a canonical even arrangement,
so for `K > 1` two different basins may spread to similar results and the
second is then diversity-rejected (wasted work, but correct — never an
invalid output). Acceptable for v1; noted in ADR-0008.

## 6. Config & CLI

`SearchConfig` (`models.py`) gains:

- `spread: bool = True` — master on/off.
- `spread_scale_m: float | None = None` — `None` ⇒ adaptive default;
  `__post_init__` validates `> 0` when set (mirrors the `pos_sigma_m`
  validation).

CLI (`cli.py`): a `--no-spread` flag on `solve` that sets `spread=False`.
No `--spread-scale` flag in v1.

## 7. Determinism & budget

- The single shared `random.Random` (ADR-0003 / spec §4.8) is threaded into
  `_spread`; every iteration that feeds the RNG sorts first, so no set-order
  leaks into RNG state.
- **`spread=False` ⇒ the call is skipped entirely**, so the RNG draw sequence
  is byte-identical to pre-change behavior. All existing determinism tests
  hold unchanged under `spread=False`.
- `_spread` shares the global `budget_s` (takes `start`, `budget_s`). If the
  budget expires mid-spread it returns the best-so-far valid layout. The outer
  loop's existing budget gate continues to govern overall termination.

## 8. Edge cases

| Case | Behavior |
|---|---|
| ≤ 1 movable plane (no pairs) | `_inter_plane_energy` = 0.0; `_spread` no-ops immediately. |
| All planes pinned | No movable planes → `_spread` no-ops. |
| Some pinned | Pinned planes are fixed obstacles: included in pair-distance terms, never selected as `target`. Movable planes spread around them. |
| Maintenance plane | Absent from placements (Layout invariant); bay keep-out enforced by the per-trial validity check. |
| Budget exhausted mid-spread | Return best-so-far valid placements. |
| Cart-rule-violating candidate | `Layout.__post_init__` raises `ValueError`; candidate skipped (same as `_descent_step`). |

## 9. Testing

**Unit — `_inter_plane_energy`:** far pair → low; close pair → high;
monotonic decreasing in gap; symmetric; `0.0` for ≤ 1 plane; finite/non-negative.

**Unit — `_spread`:** valid clustered input → output still valid **and**
`E_out < E_in`; determinism (same seed → identical placements); pinned planes
unmoved; no-op for ≤ 1 movable / all-pinned; never returns an invalid layout
even under a tight `budget_s`.

**Integration (canary):** `solve(spread=True)` vs `solve(spread=False)` on
`tests/fixtures/solve_all_nine_large_hangar.yaml` (9 planes, `scheibe_falke`
in the bay) with the same seed → both valid; the spread run has a strictly
larger **minimum pairwise gap**.

**Re-baseline `test_solver_fixture_matrix.py`:** the per-fixture
`max_wall_time_s` guards (#122) and any restart-count assertions are
updated for default-on spread (each trajectory now runs past first-valid to a
spread stall). Where the old exact assertions remain valuable, add explicit
`spread=False` variants rather than deleting them.

**CLI:** `--no-spread` disables spreading (assert output matches a
`spread=False` `SearchConfig` run on the same seed).

## 10. Documentation & issue rewrite

- **ADR-0008** — "Inter-plane spread soft preference (repulsion-energy
  surrogate for maximin)". Records: the objective-direction reversal; why
  energy over pure-maximin and over max-sum (Kuby 1987 / Riesz-energy
  reasoning); the post-pass structure; the plan-view-gap and diversity
  interaction limitations. ADR-0007 stays reserved for the tow-path planner
  (issue #195).
- **arc42 §5** (solver responsibilities — add `_spread`), **§6** (add the
  spread step to the `solve` runtime view), **§8** (scoring / soft-preferences
  crosscutting — this is the first shipped soft preference).
- **CLAUDE.md** — update the "soft preferences deferred / out of scope" notes.
- **Issue #145 rewrite (implementation step 0):** retitle to "Solver:
  *maximize* inter-plane gap (spread) as soft preference beyond hard
  zero-conflict"; invert the body and acceptance criteria
  (visibly *spread* layouts — planes pushed apart toward walls/corners, empty
  space in the interior, minimum pairwise gap maximized). Done under the normal
  PR flow, not silently.

## 11. Out of scope (YAGNI)

Wall-adherence term; aesthetic row-alignment; per-plane spread weights;
dedicated `spread_k_stall`; `--spread-scale` CLI flag; z-aware nesting reward;
energy-weighted (vs. random) target selection; directed "away-from-neighbor"
perturbation. Each is a clean follow-up if a concrete need surfaces.
