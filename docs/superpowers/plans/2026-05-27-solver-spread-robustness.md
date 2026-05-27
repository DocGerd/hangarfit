# Solver Spread Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `solve()` consistently return a well-spread valid layout regardless of seed, by collecting all valid spread-polished basins within budget and selecting the best by maximin plan-view gap (subject to the diversity gate).

**Architecture:** Replace the first-valid-basin-then-break behavior with a collect-then-select pool: the restart loop runs to budget/`max_restarts`, recording every valid (spread-polished) basin; a new pure `_select_spread_diverse` then picks `alternatives` candidates ordered by `(−min_gap, energy, restart_index)`, greedily enforcing pairwise diversity. Achieved min-gap is surfaced in `SolverDiagnostics`, the human CLI, and `--json`.

**Tech Stack:** Python 3.12, shapely (plan-view part polygons), pytest, ruff, mypy. Single supported Python (ADR-0009).

**Design spec:** `docs/superpowers/specs/2026-05-27-solver-spread-robustness-design.md`. Issue #267.

**Commit convention:** end every commit body with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` (project rule). The PostToolUse hook auto-runs ruff+pytest after each `src/`/`tests/` edit — read its tail for fast feedback.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/hangarfit/solver.py` | search + spread + selection | Add `_resolve_spread_scale`, `_spread_quality`, `_SpreadCandidate`, `_select_spread_diverse`; restructure `solve()` loop; refactor `_spread` to use `_resolve_spread_scale`. |
| `src/hangarfit/models.py` | dataclasses | Add 2 fields to `SolverDiagnostics` + validation + docstring. |
| `src/hangarfit/cli.py` | output rendering | Surface min-gap in `_emit_solve_human` + `_emit_solve_json`. |
| `tests/test_solver_spread.py` | spread-robustness tests | New file: pure-unit (primary regression) + wiring integration + `@slow` sweep. |
| `tests/test_models.py` | dataclass tests | Add `SolverDiagnostics` new-field test (append if file exists; else create). |
| `tests/test_cli.py` | CLI output tests | Add assertions for the new human line + JSON keys. |
| `docs/adr/0008-inter-plane-spread-soft-preference.md` | ADR | Addendum: best-of-all-basins selection. |

---

## Task 1: `_resolve_spread_scale` + `_spread_quality` helpers

**Files:**
- Modify: `src/hangarfit/solver.py` (add helpers near `_inter_plane_energy`, ~line 569; refactor `_spread` scale block at `solver.py:620-624`)
- Test: `tests/test_solver_spread.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_solver_spread.py`:

```python
"""Spread-robustness: best-of-all-basins selection (#267)."""

import math

from hangarfit.loader import load_scenario
from hangarfit.solver import (
    _inter_plane_energy,
    _resolve_spread_scale,
    _spread_quality,
)
from hangarfit.models import Placement, SearchConfig


def _placements(scenario, specs):
    """Build a {plane_id: Placement} from (plane_id, x, y, heading) specs."""
    return {
        pid: Placement(plane_id=pid, x_m=x, y_m=y, heading_deg=h, on_carts=False)
        for (pid, x, y, h) in specs
    }


def test_spread_quality_energy_matches_inter_plane_energy():
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    pids = list(scenario.fleet_in)
    placements = _placements(scenario, [(pids[0], 2.0, 2.0, 0.0), (pids[1], 12.0, 9.0, 0.0)])
    scale = _resolve_spread_scale(scenario, SearchConfig())
    min_gap, energy = _spread_quality(placements, scenario, scale)
    assert energy == _inter_plane_energy(placements, scenario, scale)
    assert math.isfinite(min_gap) and min_gap >= 0.0


def test_spread_quality_single_plane_is_inf_zero():
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    pid = scenario.fleet_in[0]
    placements = _placements(scenario, [(pid, 2.0, 2.0, 0.0)])
    scale = _resolve_spread_scale(scenario, SearchConfig())
    assert _spread_quality(placements, scenario, scale) == (math.inf, 0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_solver_spread.py -q`
Expected: FAIL with `ImportError: cannot import name '_resolve_spread_scale'` (and `_spread_quality`).

- [ ] **Step 3: Write minimal implementation**

In `src/hangarfit/solver.py`, immediately after `_inter_plane_energy` (after `solver.py:596`):

```python
def _resolve_spread_scale(scenario: Scenario, search: SearchConfig) -> float:
    """Repulsion length-scale for spread (spec §4): explicit override or
    20% of the smaller hangar dimension. Single source so ``_spread`` and
    ``_spread_quality`` always agree."""
    if search.spread_scale_m is not None:
        return search.spread_scale_m
    return 0.2 * min(scenario.hangar.width_m, scenario.hangar.length_m)


def _spread_quality(
    placements: dict[str, Placement],
    scenario: Scenario,
    scale: float,
) -> tuple[float, float]:
    """Return ``(min_gap, energy)`` for a layout in one pass over plane-pairs.

    ``min_gap`` is the minimum plan-view edge-to-edge distance between any two
    planes' world parts (``math.inf`` when <2 planes — no pairs). ``energy``
    is the same ``Σ exp(−gap/scale)`` repulsion :func:`_inter_plane_energy`
    computes; returning both from one pairwise sweep avoids paying the
    (expensive) shapely distances twice when scoring a candidate basin. The
    hot ``_spread`` loop keeps using the energy-only :func:`_inter_plane_energy`
    — this is called once per accepted basin, not per perturbation.
    """
    ids = sorted(placements)
    if len(ids) < 2:
        return (math.inf, 0.0)
    world: dict[str, list[WorldPart]] = {
        pid: aircraft_parts_world(scenario.fleet[pid], placements[pid]) for pid in ids
    }
    min_gap = math.inf
    energy = 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            gap = min(
                pa.polygon.distance(pb.polygon) for pa in world[ids[i]] for pb in world[ids[j]]
            )
            min_gap = min(min_gap, gap)
            energy += math.exp(-gap / scale)
    return (min_gap, energy)
```

Then refactor `_spread`'s scale block (`solver.py:620-624`) to:

```python
    scale = _resolve_spread_scale(scenario, search)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_solver_spread.py -q`
Expected: PASS (2 passed). Also run `pytest tests/test_solver.py tests/test_solver_spread_postpass.py -q` if those exist, to confirm the `_spread` refactor is behavior-preserving (grep first: `ls tests/ | grep -i spread`).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_spread.py
git commit -m "feat(solver): add _spread_quality + _resolve_spread_scale helpers (#267)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `_select_spread_diverse` pure selection (PRIMARY REGRESSION)

**Files:**
- Modify: `src/hangarfit/solver.py` (add `_SpreadCandidate` NamedTuple + `_select_spread_diverse` near `_is_diverse_enough`, ~`solver.py:1072`)
- Test: `tests/test_solver_spread.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_solver_spread.py`:

```python
from hangarfit.models import DiversityConfig, Layout
from hangarfit.solver import _SpreadCandidate, _select_spread_diverse


def _layout(scenario, specs):
    """A valid Layout from (plane_id, x, y, heading) specs. Layout.__post_init__
    enforces only structural invariants (no collision check), so arbitrary
    positions are fine for selection/diversity tests."""
    return Layout(
        fleet=scenario.fleet,
        hangar=scenario.hangar,
        placements=tuple(_placements(scenario, specs).values()),
        maintenance_plane=None,
    )


def test_select_nested_pair_loses_to_spread():
    """The core regression: a nested basin (min_gap 0.0) must never be chosen
    over a well-spread one (min_gap 2.0), even with a worse restart_index."""
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    p = list(scenario.fleet_in)
    nested = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 2.0, 2.0, 0.0)])
    spread = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 12.0, 9.0, 0.0)])
    pool = [
        _SpreadCandidate(layout=nested, min_gap=0.0, energy=5.0, restart_index=0),
        _SpreadCandidate(layout=spread, min_gap=2.0, energy=1.0, restart_index=1),
    ]
    selected, rejected = _select_spread_diverse(pool, alternatives=1, diversity=DiversityConfig())
    assert [c.min_gap for c in selected] == [2.0]
    assert rejected == 0


def test_select_energy_breaks_min_gap_ties():
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    p = list(scenario.fleet_in)
    a = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 12.0, 9.0, 0.0)])
    b = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 12.0, 9.0, 0.0)])
    pool = [
        _SpreadCandidate(layout=a, min_gap=2.0, energy=3.0, restart_index=0),
        _SpreadCandidate(layout=b, min_gap=2.0, energy=1.0, restart_index=1),
    ]
    selected, _ = _select_spread_diverse(pool, alternatives=1, diversity=DiversityConfig())
    assert selected[0].energy == 1.0  # lower energy wins the tie


def test_select_enforces_diversity_and_counts_rejects():
    """K=2: the top-spread basin is picked; a near-identical second basin is
    rejected by the diversity gate; a genuinely different one is accepted."""
    scenario = load_scenario("tests/fixtures/scenario_minimal.yaml")
    p = list(scenario.fleet_in)
    # base layout
    base = _layout(scenario, [(p[0], 2.0, 2.0, 0.0), (p[1], 12.0, 9.0, 0.0)])
    # near-identical: both planes within position_threshold_m (0.5) of base
    twin = _layout(scenario, [(p[0], 2.1, 2.0, 0.0), (p[1], 12.1, 9.0, 0.0)])
    # different: both planes moved well beyond threshold
    diff = _layout(scenario, [(p[0], 6.0, 5.0, 0.0), (p[1], 16.0, 12.0, 0.0)])
    pool = [
        _SpreadCandidate(layout=base, min_gap=3.0, energy=1.0, restart_index=0),
        _SpreadCandidate(layout=twin, min_gap=2.5, energy=1.0, restart_index=1),
        _SpreadCandidate(layout=diff, min_gap=2.0, energy=1.0, restart_index=2),
    ]
    selected, rejected = _select_spread_diverse(pool, alternatives=2, diversity=DiversityConfig())
    assert [c.min_gap for c in selected] == [3.0, 2.0]  # base, then diff; twin skipped
    assert rejected == 1


def test_select_empty_pool_returns_empty():
    selected, rejected = _select_spread_diverse([], alternatives=2, diversity=DiversityConfig())
    assert selected == [] and rejected == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_solver_spread.py -q`
Expected: FAIL with `ImportError: cannot import name '_SpreadCandidate'`.

- [ ] **Step 3: Write minimal implementation**

In `src/hangarfit/solver.py`, just before `_is_diverse_enough` (`solver.py:1072`). Ensure `from typing import NamedTuple` is imported at the top (add if absent):

```python
class _SpreadCandidate(NamedTuple):
    """A valid, spread-polished basin found during search, with its quality."""

    layout: Layout
    min_gap: float
    energy: float
    restart_index: int


def _select_spread_diverse(
    pool: list[_SpreadCandidate],
    alternatives: int,
    diversity: DiversityConfig,
) -> tuple[list[_SpreadCandidate], int]:
    """Select up to ``alternatives`` best-spread, pairwise-diverse candidates.

    Order the pool by ``(−min_gap, energy, restart_index)``: largest minimum
    plan-view gap first, ties broken by lower repulsion energy, then by restart
    order for a *total* (so deterministic — ADR-0003) ordering. Greedily accept
    a candidate iff it is diverse enough (ADR-0004) against everything already
    selected; the first pick is always accepted (diversity is vacuous on the
    empty selection). Returns ``(selected, diversity_rejected)`` in best-spread
    order, where ``diversity_rejected`` counts candidates *examined* (before the
    ``alternatives`` quota was met) that the diversity gate turned away. For
    ``alternatives == 1`` this is always 0 — selection stops after the first,
    vacuous pick.
    """
    ordered = sorted(pool, key=lambda c: (-c.min_gap, c.energy, c.restart_index))
    selected: list[_SpreadCandidate] = []
    diversity_rejected = 0
    for cand in ordered:
        if _is_diverse_enough(cand.layout, [c.layout for c in selected], diversity):
            selected.append(cand)
            if len(selected) >= alternatives:
                break
        else:
            diversity_rejected += 1
    return selected, diversity_rejected
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_solver_spread.py -q`
Expected: PASS (6 passed total).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_spread.py
git commit -m "feat(solver): _select_spread_diverse best-of-all-basins selection (#267)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `SolverDiagnostics` new fields

**Files:**
- Modify: `src/hangarfit/models.py` (`SolverDiagnostics` `solver.py`→`models.py:653-727`)
- Test: `tests/test_models.py` (grep `class TestSolverDiagnostics` / `SolverDiagnostics(` to find the section; create file if none)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py` (mirror existing `SolverDiagnostics` construction in that file for required-arg values):

```python
def test_solver_diagnostics_spread_fields_default_and_validate():
    from hangarfit.models import SolverDiagnostics

    d = SolverDiagnostics(
        restarts_attempted=3,
        wall_time_s=1.0,
        best_partial=None,
        best_partial_layout=None,
        seed=7,
    )
    assert d.min_pairwise_gap_m == ()
    assert d.valid_basins_found == 0

    d2 = SolverDiagnostics(
        restarts_attempted=3,
        wall_time_s=1.0,
        best_partial=None,
        best_partial_layout=None,
        seed=7,
        min_pairwise_gap_m=(2.5,),
        valid_basins_found=12,
    )
    assert d2.min_pairwise_gap_m == (2.5,)
    assert d2.valid_basins_found == 12

    import pytest

    with pytest.raises(ValueError, match="valid_basins_found"):
        SolverDiagnostics(
            restarts_attempted=0,
            wall_time_s=0.0,
            best_partial=None,
            best_partial_layout=None,
            seed=0,
            valid_basins_found=-1,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -q -k spread_fields`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'min_pairwise_gap_m'`.

- [ ] **Step 3: Write minimal implementation**

In `src/hangarfit/models.py`, add two fields after `unroutable_planes` (`models.py:707`):

```python
    min_pairwise_gap_m: tuple[float, ...] = ()
    valid_basins_found: int = 0
```

Append to the `SolverDiagnostics` docstring (after the `unroutable_planes` paragraph, before the closing `"""`):

```
    ``min_pairwise_gap_m`` is index-aligned with :attr:`SolveResult.layouts`:
    the achieved minimum plan-view gap (m) between any two planes in that
    returned layout — the quality the best-of-all-basins spread selection
    maximizes (#267, ADR-0008). ``math.inf`` for a layout with <2 planes
    (no pairs). ``valid_basins_found`` is the number of valid spread-polished
    basins the search collected before selection — how much choice best-of-all
    had. Both are advisory.
```

Add to `__post_init__` (after the `diversity_rejected_count` check, `models.py:727`):

```python
        if self.valid_basins_found < 0:
            raise ValueError(
                f"SolverDiagnostics.valid_basins_found must be >= 0, "
                f"got {self.valid_basins_found}"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -q -k spread_fields`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(models): SolverDiagnostics min_pairwise_gap_m + valid_basins_found (#267)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Wire `solve()` — build pool, select, populate diagnostics

**Files:**
- Modify: `src/hangarfit/solver.py` (`solve()` restart loop `solver.py:142-337`)
- Test: `tests/test_solver_spread.py`

> **Behavior change to flag at review:** best-of-all engages on every solve (the loop now always runs to `budget_s`/`max_restarts` rather than breaking on the first valid layout) — including `--no-spread`, which now also runs full budget but selects the best *raw* basin (no polish). This matches the approved "full budget, best-of-all" decision. The fast first-valid path is gone; if a speed escape hatch is wanted later, that's a follow-up.

- [ ] **Step 1: Write the failing test (wiring + determinism)**

Append to `tests/test_solver_spread.py`:

```python
from hangarfit.solver import solve


def test_solve_populates_spread_diagnostics():
    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    r = solve(scenario, seed=7, search=SearchConfig(max_restarts=20), plan_paths=False)
    assert r.status in ("found", "found_partial")
    d = r.diagnostics
    # min_pairwise_gap_m is index-aligned with layouts and populated.
    assert len(d.min_pairwise_gap_m) == len(r.layouts)
    assert d.valid_basins_found >= len(r.layouts)
    # the returned layout cleared the nested-pair pathology
    assert all(g > 0.0 for g in d.min_pairwise_gap_m)


def test_solve_spread_selection_is_deterministic():
    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    cfg = SearchConfig(max_restarts=20)
    a = solve(scenario, seed=7, search=cfg, plan_paths=False)
    b = solve(scenario, seed=7, search=cfg, plan_paths=False)
    assert a.diagnostics.min_pairwise_gap_m == b.diagnostics.min_pairwise_gap_m
    assert [_layout_key(l) for l in a.layouts] == [_layout_key(l) for l in b.layouts]


def _layout_key(layout):
    return tuple(
        (p.plane_id, round(p.x_m, 6), round(p.y_m, 6), round(p.heading_deg, 6))
        for p in layout.placements
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_solver_spread.py -q -k "diagnostics or deterministic"`
Expected: FAIL — `AssertionError` on `len(d.min_pairwise_gap_m) == len(r.layouts)` (currently always `()`).

- [ ] **Step 3: Write the implementation**

Restructure `solve()`'s search section. Replace the accumulator declarations (`solver.py:140-144`) — drop `accepted_layouts` / `diversity_rejected_count` running counters in favor of a pool:

```python
    best_partial_score: tuple[int, float] = (sys.maxsize, float("inf"))
    best_partial_layout: Layout | None = None
    pool: list[_SpreadCandidate] = []
    restart_index = 0
    spread_scale = _resolve_spread_scale(scenario, search)
```

In the inner loop, replace the valid-found block (`solver.py:194-232`) with: on valid, spread (if enabled), score quality, append to pool, and restart (do **not** break the outer loop):

```python
            if current_score == (0, 0.0):
                if search.spread:
                    placements = _spread(
                        placements,
                        scenario,
                        rng,
                        search,
                        start=start,
                        budget_s=budget_s,
                        pinned_planes=pinned_planes,
                    )
                # _spread preserves every Layout invariant; a ValueError here
                # would be a structural bug, so let it propagate.
                candidate_layout = Layout(
                    fleet=scenario.fleet,
                    hangar=scenario.hangar,
                    placements=tuple(placements.values()),
                    maintenance_plane=scenario.maintenance_plane,
                )
                min_gap, energy = _spread_quality(placements, scenario, spread_scale)
                pool.append(
                    _SpreadCandidate(
                        layout=candidate_layout,
                        min_gap=min_gap,
                        energy=energy,
                        restart_index=restart_index,
                    )
                )
                break  # restart to seek a different basin
```

Delete the now-removed early-exit `if len(accepted_layouts) >= alternatives: break` (`solver.py:263`). The loop now exits only on budget / `max_restarts`.

After the loop, replace the result-assembly (`solver.py:266-337`). Select, then build the result:

```python
    elapsed = time.monotonic() - start

    selected, diversity_rejected_count = _select_spread_diverse(pool, alternatives, diversity)

    if selected:
        accepted_layouts = [c.layout for c in selected]
        min_gaps = tuple(c.min_gap for c in selected)
        status: SolveStatus = "found" if len(accepted_layouts) >= alternatives else "found_partial"
        plans: tuple[MovesPlan | None, ...]
        unroutable: list[str] = []
        if plan_paths:
            built: list[MovesPlan | None] = []
            for layout in accepted_layouts:
                try:
                    built.append(plan_fill(layout))
                except NoFeasiblePlanError as e:
                    built.append(None)
                    unroutable.append(e.plane_id)
                    _logger.warning(
                        "layout not tow-routable by the v1 planner: plane %r blocked "
                        "(%s: %s); returning the valid static layout without a tow plan",
                        e.plane_id,
                        e.conflict.kind,
                        e.conflict.detail,
                    )
            plans = tuple(built)
        else:
            plans = (None,) * len(accepted_layouts)
        return SolveResult(
            status=status,
            layouts=tuple(accepted_layouts),
            plans=plans,
            diagnostics=SolverDiagnostics(
                restarts_attempted=restart_index,
                wall_time_s=elapsed,
                best_partial=None,
                best_partial_layout=None,
                seed=resolved_seed,
                diversity_impossible=diversity_impossible,
                diversity_rejected_count=diversity_rejected_count,
                unroutable_planes=tuple(unroutable),
                min_pairwise_gap_m=min_gaps,
                valid_basins_found=len(pool),
            ),
        )
    bp = check_layout(best_partial_layout) if best_partial_layout is not None else None
    return SolveResult(
        status="exhausted_budget",
        layouts=(),
        diagnostics=SolverDiagnostics(
            restarts_attempted=restart_index,
            wall_time_s=elapsed,
            best_partial=bp,
            best_partial_layout=best_partial_layout,
            seed=resolved_seed,
            diversity_impossible=diversity_impossible,
            diversity_rejected_count=diversity_rejected_count,
            valid_basins_found=len(pool),
        ),
    )
```

Update `solve()`'s docstring (`solver.py:53-69`) to note: returns the best-spread valid layout(s) found across all restarts within budget (best-of-all-basins, #267), not the first valid one.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_solver_spread.py -q`
Expected: PASS (all). Then the full default solver suite to catch regressions in diversity/alternatives behavior:
Run: `pytest tests/ -q -k solver`
Expected: PASS. **If a K>1 test asserts discovery-order layout sequence, update it** — layouts are now best-spread-first (documented behavior change). Inspect any failure before editing the test; do not weaken a real assertion.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_spread.py
git commit -m "feat(solver): best-of-all-basins spread selection in solve() (#267)

Replaces first-valid-basin-then-break with a collect-then-select pool: run
restarts to budget, record every valid spread-polished basin, then select K
by maximin gap subject to the diversity gate. Surfaces min_pairwise_gap_m and
valid_basins_found in diagnostics. Returned layouts are now best-spread-first.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Surface min-gap in the human CLI

**Files:**
- Modify: `src/hangarfit/cli.py` (`_emit_solve_human` `cli.py:247-258`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (follow the existing CLI invocation pattern in that file — capture stdout from a `solve` run on `tests/fixtures/solve_trivial_single_plane.yaml` and a multi-plane fixture):

```python
def test_solve_human_output_shows_min_gap(capsys):
    from hangarfit.cli import main

    rc = main(["solve", "tests/fixtures/solve_fresh_six_planes.yaml",
               "--seed", "7", "--budget", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "min gap" in out  # per-layout spread quality line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -q -k min_gap`
Expected: FAIL — `assert "min gap" in out`.

- [ ] **Step 3: Write minimal implementation**

In `_emit_solve_human`, modify the per-layout loop (`cli.py:247-258`) to append a min-gap suffix. Replace the loop body with:

```python
    for i, layout in enumerate(result.layouts, start=1):
        gap = d.min_pairwise_gap_m[i - 1] if i - 1 < len(d.min_pairwise_gap_m) else math.inf
        gap_str = f"{gap:.2f} m" if math.isfinite(gap) else "n/a (single plane)"
        if i > 1:
            parts = []
            for j in range(i - 1):
                moved, avg_shift = _placement_delta(result.layouts[j], layout)
                total = len(layout.placements)
                parts.append(
                    f"{moved} of {total} planes shifted vs #{j + 1} (avg shift {avg_shift:.1f} m)"
                )
            line = f"  #{i}: {'; '.join(parts)}; min gap {gap_str}"
        else:
            line = (
                f"  #{i}: {len(layout.placements)} planes placed; 0 conflicts; "
                f"score=(0, 0.0); min gap {gap_str}"
            )
        print(line)
```

Ensure `import math` is present at the top of `cli.py` (add if absent).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -q -k min_gap`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "feat(cli): show achieved min gap per layout in solve summary (#267)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Surface min-gap + basin count in `--json`

**Files:**
- Modify: `src/hangarfit/cli.py` (`_emit_solve_json` `cli.py:572-588`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_solve_json_output_has_spread_diagnostics(capsys):
    import json
    from hangarfit.cli import main

    rc = main(["solve", "tests/fixtures/solve_fresh_six_planes.yaml",
               "--seed", "7", "--budget", "5", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    diag = payload["diagnostics"]
    assert "min_pairwise_gap_m" in diag
    assert "valid_basins_found" in diag
    assert len(diag["min_pairwise_gap_m"]) == len(payload["layouts"])
    # finite floats stay numbers; single-plane inf becomes null
    assert all(g is None or isinstance(g, (int, float)) for g in diag["min_pairwise_gap_m"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -q -k json_output_has_spread`
Expected: FAIL — `KeyError`/`assert "min_pairwise_gap_m" in diag`.

- [ ] **Step 3: Write minimal implementation**

In `_emit_solve_json`, add to the `"diagnostics"` dict after `"unroutable_planes"` (`cli.py:587`). `json.dumps` cannot emit `Infinity` as valid JSON, so map non-finite gaps to `null`:

```python
            # Additive (#267): achieved min plan-view gap per returned layout
            # (null where <2 planes, i.e. math.inf) + basins the search had to
            # choose from. Backward-compatible — no schema bump.
            "min_pairwise_gap_m": [
                g if math.isfinite(g) else None for g in d.min_pairwise_gap_m
            ],
            "valid_basins_found": d.valid_basins_found,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -q -k json_output_has_spread`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "feat(cli): add min_pairwise_gap_m + valid_basins_found to solve --json (#267)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `@slow` deep seed sweep

**Files:**
- Test: `tests/test_solver_spread.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_solver_spread.py`:

```python
import pytest


@pytest.mark.slow
def test_no_seed_emits_nested_pair_over_sweep():
    """Deep confidence: across seeds 1-30, the best-of-all selection returns a
    layout with no nested pair (min gap > 0). Excluded from the default run
    (slow); the pure _select_spread_diverse tests are the fast regression."""
    scenario = load_scenario("tests/fixtures/solve_fresh_six_planes.yaml")
    cfg = SearchConfig(max_restarts=40)
    offenders = []
    for seed in range(1, 31):
        r = solve(scenario, seed=seed, search=cfg, plan_paths=False)
        if r.status in ("found", "found_partial"):
            worst = min(r.diagnostics.min_pairwise_gap_m, default=math.inf)
            if worst <= 0.0:
                offenders.append((seed, worst))
    assert not offenders, f"seeds still emitting a nested pair: {offenders}"
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_solver_spread.py -q -m slow -k nested_pair`
Expected: PASS. If it FAILS for some seeds, raise `max_restarts` (the pool needs at least one non-nested basin per seed) before adjusting the threshold; record findings in the PR. If it PASSES even with `max_restarts=1`, note that `solve_fresh_six_planes.yaml` does not reproduce the pathology — the pure-unit test remains the true regression; consider a tighter fixture as a follow-up.

- [ ] **Step 3: Commit**

```bash
git add tests/test_solver_spread.py
git commit -m "test(solver): @slow seed-sweep confidence for spread robustness (#267)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Docs — ADR-0008 addendum

**Files:**
- Modify: `docs/adr/0008-inter-plane-spread-soft-preference.md`

- [ ] **Step 1: Add the addendum**

Append a section to the ADR:

```markdown
## Addendum (2026-05, #267): best-of-all-basins selection

ADR-0008's `_spread` post-pass is a greedy single-plane hill-climb that polishes
only the basin handed to it. Because `solve()` originally accepted the *first*
valid basin, spread quality was seed-luck: ~1/3 of seeds settled with a nested
pair (0.0 m plan-view gap) the single-plane climb could not escape.

The solver now runs all restarts within budget, records every valid
spread-polished basin, and **selects** the layout(s) with the largest minimum
plan-view gap (energy tiebreak), subject to the existing diversity gate
(ADR-0004). `_spread` itself is unchanged — the robustness comes from choosing
among basins rather than from a smarter climb. Determinism (ADR-0003) is
preserved: same single RNG, restart-ordered pool, total selection ordering.
The achieved min gap is reported in `SolverDiagnostics.min_pairwise_gap_m`, the
CLI summary, and `--json`. Returned alternatives are now ordered best-spread-first.
```

- [ ] **Step 2: Commit**

```bash
git add docs/adr/0008-inter-plane-spread-soft-preference.md
git commit -m "docs(adr): ADR-0008 addendum — best-of-all-basins spread selection (#267)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Full verification + PR

- [ ] **Step 1: Run the whole default suite + lint + types**

```bash
pytest -q
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/hangarfit
```
Expected: all green. Fix any finding at the source (the lockfile guard / hooks are active).

- [ ] **Step 2: Run the slow set once**

```bash
pytest -q -m slow
```
Expected: PASS (includes the seed sweep).

- [ ] **Step 3: Manual smoke (observe the new output)**

```bash
hangarfit solve tests/fixtures/solve_fresh_six_planes.yaml --seed 7 --budget 5
hangarfit solve tests/fixtures/solve_fresh_six_planes.yaml --seed 7 --budget 5 --json
```
Expected: human summary shows `min gap N.NN m` per layout; JSON `diagnostics` has `min_pairwise_gap_m` + `valid_basins_found`.

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin feature/267-solver-spread-robustness
gh pr create --base develop \
  --title "feat(solver): best-of-all-basins spread selection (#267)" \
  --body-file <(printf '%s\n' "Closes #267" "" "Best-of-all-basins spread selection — see docs/superpowers/specs/2026-05-27-solver-spread-robustness-design.md.") \
  --assignee DocGerd --label enhancement --milestone "Phase 2c — Solver polish"
```

- [ ] **Step 5: Review per CLAUDE.md**

Run `/pr-review` (code-reviewer + type-design-analyzer since `models.py` changed; **geometry-invariant-guard is NOT required** — no `geometry.py`/`collisions.py` change). Convert findings to diff threads, resolve each, confirm CI green, hand off to the user for final review/merge. Do not merge.

---

## Self-review notes (filled by plan author)

- **Spec coverage:** pool model (T4) · maximin+energy+restart_index selection (T2) · `_spread_quality` min-gap/inf (T1) · diagnostics fields + semantics shift (T3) · CLI human (T5) + JSON inf→null (T6) · determinism (T4 test) · pure-unit primary regression (T2) + wiring integration (T4) + `@slow` sweep (T7) · ADR addendum (T8). All spec sections map to a task.
- **Behavior changes flagged:** full-budget on every solve incl. `--no-spread` (T4 note); best-spread-first ordering for K>1 (T4 Step 4 test-update guard).
- **Type consistency:** `_SpreadCandidate(layout, min_gap, energy, restart_index)` used identically in T2 and T4; `_select_spread_diverse -> (list, int)` consumed as `selected, diversity_rejected_count` in T4; `_spread_quality -> (min_gap, energy)` consumed in T1 test and T4 wiring; `min_pairwise_gap_m: tuple[float, ...]` set in T4, read in T5/T6.
- **No placeholders:** every code/test step shows real code; commands have expected output.
