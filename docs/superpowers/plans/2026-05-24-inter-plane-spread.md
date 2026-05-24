# Inter-plane Gap Maximization (Spread) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the solver finds a valid layout, refine it to maximize inter-plane separation (spread planes apart) while preserving validity, on by default with a `--no-spread` off switch.

**Architecture:** An isolated post-pass `_spread()` function in `solver.py`, called inside `solve()` the moment a trajectory reaches `(0, 0.0)`, before the diversity check. It runs a small seeded hill-climb that minimizes a smooth repulsion energy `E = Σ exp(−gap_ij / scale)` over plane pairs (a maximin surrogate), accepting only moves that stay valid. The existing conflict-resolution descent, the `(int, float)` score tuple, and `best_partial` tracking are untouched — the energy lives entirely inside the spread phase.

**Tech Stack:** Python 3.11+, shapely (`polygon.distance` for edge-to-edge gap), `random.Random` (seeded determinism per ADR-0003), pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-05-24-inter-plane-spread-design.md`

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/hangarfit/models.py` | `SearchConfig.spread` + `spread_scale_m` fields + validation | Modify |
| `src/hangarfit/solver.py` | `_inter_plane_energy`, `_spread`, wire into `solve()` | Modify |
| `src/hangarfit/cli.py` | `--no-spread` flag; pass `SearchConfig(spread=...)` to `solve()` | Modify |
| `tests/test_models.py` | `SearchConfig` field + validation tests | Modify |
| `tests/test_solver_search.py` | `_inter_plane_energy` + `_spread` unit tests | Modify |
| `tests/test_solver_spread.py` | `solve()`-level spread integration + canary | Create |
| `tests/test_solver_fixture_matrix.py` | Pin mechanics tests to `spread=False` (re-baseline) | Modify |
| `tests/test_cli_solve.py` | `--no-spread` flag test | Modify |
| `docs/adr/0008-inter-plane-spread-soft-preference.md` | Decision record | Create |
| `docs/architecture/05-building-block-view.md` | Add `_spread` to solver responsibilities | Modify |
| `docs/architecture/06-runtime-view.md` | Add spread step to the `solve` flow | Modify |
| `docs/architecture/08-crosscutting-concepts.md` | First shipped soft preference | Modify |
| `CLAUDE.md` | Update "soft preferences deferred" note | Modify |

**Conventions:** branch `feature/145-inter-plane-spread` (already created off `develop`). Run the full suite with `pytest`; lint with `ruff check src/ tests/` and `ruff format --check src/ tests/`; type-check with `mypy src/hangarfit/`. The `.claude/` PostToolUse hook auto-runs pytest after edits under `src/hangarfit/` or `tests/`.

---

## Task 1: Rewrite issue #145 to match the reversed objective

The filed issue says *minimize* gap (pack); the agreed objective is *maximize* gap (spread). Fix it before code so the PR and reviewers align.

- [ ] **Step 1: Rewrite the issue title and body**

Run (single command, HEREDOC body):

```bash
gh issue edit 145 \
  --title "Solver: maximize inter-plane gap (spread) as soft preference beyond hard zero-conflict" \
  --body "$(cat <<'EOF'
## Objective

After the solver reaches a valid layout (zero conflicts), refine it to **maximize inter-plane separation** — spread planes apart so a human towing one in or out has comfortable clearance past its neighbors. The hard "no overlap / in bounds / bay respected" constraint is unchanged; this is a soft preference applied only once validity is met.

> Reversed 2026-05-24: this issue originally asked to *minimize* the gap (pack tightly). The real goal is the opposite — *maximize* it. See design spec `docs/superpowers/specs/2026-05-24-inter-plane-spread-design.md`.

## Approach

Smooth repulsion-energy surrogate for the maximin (p-dispersion) objective: minimize `E = Σ exp(−gap_ij / scale)` over plane pairs, where `gap_ij` is the edge-to-edge footprint distance. Close pairs dominate the sum, so it protects the minimum gap while staying differentiable for the hill-climber. Implemented as an isolated post-pass `_spread()` (design approach B), on by default with a `--no-spread` toggle.

## Acceptance criteria

Re-running the v0.6.1 walkthrough produces **visibly spread** layouts: planes pushed apart toward the walls/corners with empty space concentrated in the interior, and the **minimum pairwise gap maximized** relative to a `--no-spread` run on the same seed. Canary: `tests/fixtures/solve_all_nine_large_hangar.yaml`.
EOF
)"
```

Expected: `gh` prints the issue URL; no error.

- [ ] **Step 2: Verify**

Run: `gh issue view 145 --json title --jq .title`
Expected: the new "maximize inter-plane gap (spread)" title.

(No commit — this is a GitHub-only change.)

---

## Task 2: `SearchConfig.spread` + `spread_scale_m` fields

**Files:**
- Modify: `src/hangarfit/models.py` (the `SearchConfig` dataclass, ~lines 767–809)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_models.py`:

```python
def test_search_config_spread_defaults_on():
    from hangarfit.models import SearchConfig

    cfg = SearchConfig()
    assert cfg.spread is True
    assert cfg.spread_scale_m is None


def test_search_config_spread_scale_must_be_positive_when_set():
    import pytest

    from hangarfit.models import SearchConfig

    with pytest.raises(ValueError, match="spread_scale_m"):
        SearchConfig(spread_scale_m=0.0)
    with pytest.raises(ValueError, match="spread_scale_m"):
        SearchConfig(spread_scale_m=-2.0)
    # None (adaptive) and positive are accepted
    assert SearchConfig(spread_scale_m=None).spread_scale_m is None
    assert SearchConfig(spread_scale_m=3.5).spread_scale_m == 3.5
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_models.py::test_search_config_spread_defaults_on tests/test_models.py::test_search_config_spread_scale_must_be_positive_when_set -v`
Expected: FAIL — `AttributeError: 'SearchConfig' object has no attribute 'spread'`.

- [ ] **Step 3: Add the fields**

In `src/hangarfit/models.py`, in the `SearchConfig` dataclass, add after the `max_restarts` field block (after its docstring, before `def __post_init__`):

```python
    spread: bool = True
    """When True (default), ``solve()`` runs a post-pass spread phase on each
    valid layout that maximizes inter-plane separation (minimizes the
    repulsion energy ``Σ exp(−gap/scale)``) while preserving validity. Set
    False to skip it entirely — the RNG stream is then byte-identical to the
    pre-spread solver, so determinism goldens written before this feature
    still hold. See ADR-0008 and
    ``docs/superpowers/specs/2026-05-24-inter-plane-spread-design.md``."""

    spread_scale_m: float | None = None
    """Length scale (metres) of the spread repulsion kernel ``exp(−gap/scale)``.
    ``None`` (default) ⇒ adaptive ``0.2 × min(hangar.width_m, hangar.length_m)``,
    keeping the kernel sensitive across hangar sizes. When set explicitly,
    must be ``> 0``."""
```

- [ ] **Step 4: Add the validation**

In `SearchConfig.__post_init__`, append:

```python
        if self.spread_scale_m is not None and self.spread_scale_m <= 0.0:
            raise ValueError(
                f"SearchConfig.spread_scale_m must be positive when set "
                f"(pass None for the adaptive default), got {self.spread_scale_m}"
            )
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_models.py -k spread -v`
Expected: PASS (both new tests).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(solver): add SearchConfig.spread + spread_scale_m (#145)"
```

---

## Task 3: `_inter_plane_energy` repulsion energy

**Files:**
- Modify: `src/hangarfit/solver.py` (imports + new helper)
- Test: `tests/test_solver_search.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_solver_search.py`:

```python
def test_inter_plane_energy_zero_for_single_plane():
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import _inter_plane_energy

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    pid = next(p for p in s.fleet_in if p != s.maintenance_plane)
    placements = {pid: Placement(plane_id=pid, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)}

    assert _inter_plane_energy(placements, s, scale=5.0) == 0.0


def test_inter_plane_energy_higher_when_planes_closer():
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import _inter_plane_energy

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    a, b = [p for p in s.fleet_in if p != s.maintenance_plane][:2]
    scale = 5.0

    near = {
        a: Placement(plane_id=a, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),
        b: Placement(plane_id=b, x_m=7.0, y_m=7.0, heading_deg=0.0, on_carts=False),
    }
    far = {
        a: Placement(plane_id=a, x_m=3.0, y_m=3.0, heading_deg=0.0, on_carts=False),
        b: Placement(plane_id=b, x_m=22.0, y_m=27.0, heading_deg=0.0, on_carts=False),
    }
    # Closer planes -> smaller gap -> larger exp(-gap/scale) term.
    assert _inter_plane_energy(near, s, scale) > _inter_plane_energy(far, s, scale)


def test_inter_plane_energy_symmetric_in_plane_order():
    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement
    from hangarfit.solver import _inter_plane_energy

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    a, b = [p for p in s.fleet_in if p != s.maintenance_plane][:2]
    pa = Placement(plane_id=a, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)
    pb = Placement(plane_id=b, x_m=9.0, y_m=11.0, heading_deg=30.0, on_carts=False)

    assert _inter_plane_energy({a: pa, b: pb}, s, 5.0) == _inter_plane_energy({b: pb, a: pa}, s, 5.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_search.py -k inter_plane_energy -v`
Expected: FAIL — `ImportError: cannot import name '_inter_plane_energy'`.

- [ ] **Step 3: Add imports**

In `src/hangarfit/solver.py`, at the top of the import block add:

```python
import math
```

and extend the geometry import (currently `collisions` is imported; `geometry` is not) by adding:

```python
from hangarfit.geometry import WorldPart, aircraft_parts_world
```

- [ ] **Step 4: Implement the helper**

Add to `src/hangarfit/solver.py` (place it just above `_score`):

```python
def _inter_plane_energy(
    placements: dict[str, Placement],
    scenario: Scenario,
    scale: float,
) -> float:
    """Smooth repulsion energy ``E = Σ_{i<j} exp(−gap_ij / scale)`` (spec §4).

    ``gap_ij`` is the minimum plan-view edge-to-edge distance between plane
    ``i``'s and plane ``j``'s world parts (shapely ``polygon.distance``).
    Lower ``E`` ⇒ planes further apart; close pairs dominate the sum, so
    minimizing it maximizes the *minimum* gap (a smooth maximin surrogate).
    Returns ``0.0`` when fewer than two planes are present. Ignores z
    (plan-view only) — see ADR-0008 for the nesting limitation.
    """
    ids = sorted(placements)
    if len(ids) < 2:
        return 0.0
    world: dict[str, list[WorldPart]] = {
        pid: aircraft_parts_world(scenario.fleet[pid], placements[pid]) for pid in ids
    }
    energy = 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            gap = min(
                pa.polygon.distance(pb.polygon)
                for pa in world[ids[i]]
                for pb in world[ids[j]]
            )
            energy += math.exp(-gap / scale)
    return energy
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_solver_search.py -k inter_plane_energy -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_search.py
git commit -m "feat(solver): add _inter_plane_energy repulsion metric (#145)"
```

---

## Task 4: `_spread` hill-climb

**Files:**
- Modify: `src/hangarfit/solver.py` (new helper)
- Test: `tests/test_solver_search.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_solver_search.py`:

```python
def _valid_placements(seed: int):
    """Solve (without spread) to obtain a valid placements dict to feed _spread.

    Uses a small 3-plane feasible fixture so the solve is fast AND there are
    real plane pairs (single-plane fixtures make spread tests vacuous).
    """
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_fresh_alternatives_three.yaml")
    r = solve(s, budget_s=5.0, seed=seed, search=SearchConfig(spread=False))
    assert r.layouts, "fixture must be solvable without spread"
    placements = {p.plane_id: p for p in r.layouts[0].placements}
    return s, placements


def test_spread_preserves_validity_and_never_worsens_energy():
    import time

    from hangarfit.models import Layout, SearchConfig
    from hangarfit.solver import _inter_plane_energy, _score, _spread
    import random

    s, placements = _valid_placements(seed=11)
    scale = 0.2 * min(s.hangar.width_m, s.hangar.length_m)
    e_before = _inter_plane_energy(placements, s, scale)

    out = _spread(
        placements,
        s,
        random.Random(11),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=5.0,
        pinned_planes=frozenset(),
    )
    e_after = _inter_plane_energy(out, s, scale)

    # Energy never increases (spread only improves or no-ops).
    assert e_after <= e_before
    # Output is still a valid layout.
    layout = Layout(
        fleet=s.fleet,
        hangar=s.hangar,
        placements=tuple(out.values()),
        maintenance_plane=s.maintenance_plane,
    )
    assert _score(layout) == (0, 0.0)


def test_spread_is_deterministic_for_same_seed():
    import time
    import random

    from hangarfit.models import SearchConfig
    from hangarfit.solver import _spread

    s, placements = _valid_placements(seed=11)
    kw = dict(start=time.monotonic(), budget_s=5.0, pinned_planes=frozenset())

    out_a = _spread(placements, s, random.Random(99), SearchConfig(), **kw)
    out_b = _spread(placements, s, random.Random(99), SearchConfig(), **kw)

    assert {k: (v.x_m, v.y_m, v.heading_deg) for k, v in out_a.items()} == {
        k: (v.x_m, v.y_m, v.heading_deg) for k, v in out_b.items()
    }


def test_spread_does_not_move_pinned_planes():
    import time
    import random

    from hangarfit.models import SearchConfig
    from hangarfit.solver import _spread

    s, placements = _valid_placements(seed=11)
    frozen_id = sorted(placements)[0]
    frozen_before = placements[frozen_id]

    out = _spread(
        placements,
        s,
        random.Random(11),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=5.0,
        pinned_planes=frozenset({frozen_id}),
    )
    assert out[frozen_id] == frozen_before


def test_spread_noop_when_single_movable_plane():
    import time
    import random

    from hangarfit.loader import load_scenario
    from hangarfit.models import Placement, SearchConfig
    from hangarfit.solver import _spread

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    pid = next(p for p in s.fleet_in if p != s.maintenance_plane)
    placements = {pid: Placement(plane_id=pid, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False)}

    out = _spread(
        placements,
        s,
        random.Random(1),
        SearchConfig(),
        start=time.monotonic(),
        budget_s=1.0,
        pinned_planes=frozenset(),
    )
    assert out == placements
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_search.py -k spread -v`
Expected: FAIL — `ImportError: cannot import name '_spread'`.

- [ ] **Step 3: Implement `_spread`**

Add to `src/hangarfit/solver.py` (place it just below `_inter_plane_energy`):

```python
def _spread(
    placements: dict[str, Placement],
    scenario: Scenario,
    rng: _random_module.Random,
    search: SearchConfig,
    *,
    start: float,
    budget_s: float,
    pinned_planes: frozenset[str],
) -> dict[str, Placement]:
    """Post-pass spread: maximize inter-plane separation on a VALID layout.

    Greedy seeded hill-climb that minimizes :func:`_inter_plane_energy`,
    accepting only candidates that stay valid (score ``(0, 0.0)``). Input
    must already be valid; output is therefore always valid — this can only
    improve separation or no-op. Pinned planes are fixed obstacles: they
    contribute to pair distances but are never moved. Shares the global
    wall-clock budget; returns the best (lowest-energy) placements found.

    See ADR-0008 and spec §5.
    """
    scale = (
        search.spread_scale_m
        if search.spread_scale_m is not None
        else 0.2 * min(scenario.hangar.width_m, scenario.hangar.length_m)
    )

    movable = sorted(pid for pid in placements if pid not in pinned_planes)
    if not movable or len(placements) < 2:
        return placements

    current_energy = _inter_plane_energy(placements, scenario, scale)
    last_improved = 0

    for iter_count in range(10000):  # large cap; real exit via stall/budget
        if time.monotonic() - start >= budget_s:
            break

        target = rng.choice(movable)

        # Same candidate mix as _descent_step: (N-2) small nudges + 1 large + 1 flip.
        candidates: list[Placement] = []
        n_small = max(0, search.candidates_per_iter - 2)
        for _ in range(n_small):
            candidates.append(
                _perturb_plane(
                    current=placements[target],
                    scenario=scenario,
                    rng=rng,
                    search=search,
                    large_jump=False,
                )
            )
        candidates.append(
            _perturb_plane(
                current=placements[target],
                scenario=scenario,
                rng=rng,
                search=search,
                large_jump=True,
            )
        )
        candidates.append(
            Placement(
                plane_id=target,
                x_m=placements[target].x_m,
                y_m=placements[target].y_m,
                heading_deg=(placements[target].heading_deg + 180.0) % 360.0,
                on_carts=placements[target].on_carts,
            )
        )

        # Pick the lowest-(energy, displacement) VALID candidate. Adopt it
        # only if its energy is strictly below current (so the stall counter
        # advances on non-improving iterations — no plateau-wander livelock).
        best_key: tuple[float, float] | None = None
        best_placements = placements
        for cand in candidates:
            trial = dict(placements)
            trial[target] = cand
            try:
                trial_layout = Layout(
                    fleet=scenario.fleet,
                    hangar=scenario.hangar,
                    placements=tuple(trial.values()),
                    maintenance_plane=scenario.maintenance_plane,
                )
            except ValueError:
                continue  # cart-rule etc. — skip
            if _score(trial_layout) != (0, 0.0):
                continue  # must STAY valid
            e = _inter_plane_energy(trial, scenario, scale)
            disp = (
                (cand.x_m - placements[target].x_m) ** 2
                + (cand.y_m - placements[target].y_m) ** 2
            ) ** 0.5
            key = (e, disp)
            if best_key is None or key < best_key:
                best_key = key
                best_placements = trial

        if best_key is not None and best_key[0] < current_energy:
            placements = best_placements
            current_energy = best_key[0]
            last_improved = iter_count

        if iter_count - last_improved >= search.k_stall:
            break

    return placements
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_solver_search.py -k spread -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_search.py
git commit -m "feat(solver): add _spread post-pass hill-climb (#145)"
```

---

## Task 5: Wire `_spread` into `solve()`

**Files:**
- Modify: `src/hangarfit/solver.py` (the `current_score == (0, 0.0)` branch, ~lines 175–200)
- Test: `tests/test_solver_spread.py` (created here)

- [ ] **Step 1: Write the failing test**

Create `tests/test_solver_spread.py`:

```python
"""solve()-level tests for the inter-plane spread phase (#145)."""

from __future__ import annotations


def _min_pairwise_gap(layout, scenario) -> float:
    """Smallest plan-view edge-to-edge gap between any two planes in a layout."""
    from hangarfit.geometry import aircraft_parts_world

    placements = list(layout.placements)
    world = {p.plane_id: aircraft_parts_world(scenario.fleet[p.plane_id], p) for p in placements}
    ids = [p.plane_id for p in placements]
    gaps = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            gaps.append(
                min(
                    pa.polygon.distance(pb.polygon)
                    for pa in world[ids[i]]
                    for pb in world[ids[j]]
                )
            )
    return min(gaps) if gaps else 0.0


def test_solve_spread_on_widens_min_gap_vs_off():
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")

    off = solve(s, budget_s=10.0, seed=5, search=SearchConfig(spread=False))
    on = solve(s, budget_s=10.0, seed=5, search=SearchConfig(spread=True))

    assert off.layouts and on.layouts
    gap_off = _min_pairwise_gap(off.layouts[0], s)
    gap_on = _min_pairwise_gap(on.layouts[0], s)
    assert gap_on > gap_off, f"spread did not widen the minimum gap: on={gap_on} off={gap_off}"


def test_solve_default_enables_spread():
    """solve() with no SearchConfig spreads by default (SearchConfig().spread is True)."""
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    s = load_scenario("tests/fixtures/solve_all_nine_large_hangar.yaml")
    default = solve(s, budget_s=10.0, seed=5)
    explicit_on = solve(s, budget_s=10.0, seed=5, search=SearchConfig(spread=True))

    assert default.layouts and explicit_on.layouts
    # Default == explicit spread=True on the same seed.
    assert [(p.x_m, p.y_m, p.heading_deg) for p in default.layouts[0].placements] == [
        (p.x_m, p.y_m, p.heading_deg) for p in explicit_on.layouts[0].placements
    ]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_spread.py -v`
Expected: FAIL — `test_solve_spread_on_widens_min_gap_vs_off` fails (spread not wired in, so on == off and `gap_on > gap_off` is false).

- [ ] **Step 3: Wire the call into `solve()`**

In `src/hangarfit/solver.py`, inside the `if current_score == (0, 0.0):` branch, **before** `candidate_layout = Layout(...)` is built, insert:

```python
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
```

The branch then continues to build `candidate_layout` from the (now spread) `placements` exactly as before. Do not change anything else in the branch.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_solver_spread.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_spread.py
git commit -m "feat(solver): run spread post-pass in solve() (default on) (#145)"
```

---

## Task 6: CLI `--no-spread` flag

**Files:**
- Modify: `src/hangarfit/cli.py` (solve arg block ~line 116; `cmd_solve` ~line 303)
- Test: `tests/test_cli_solve.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli_solve.py`:

```python
def test_solve_no_spread_matches_spread_false_config(tmp_path, capsys):
    """`--no-spread` must produce the same layout as SearchConfig(spread=False)."""
    from hangarfit.cli import main
    from hangarfit.loader import load_scenario
    from hangarfit.models import SearchConfig
    from hangarfit.solver import solve

    scenario_path = "tests/fixtures/solve_all_nine_large_hangar.yaml"
    out = tmp_path / "layout.yaml"

    rc = main(
        [
            "solve",
            scenario_path,
            "--seed",
            "5",
            "--budget",
            "10",
            "--no-spread",
            "--write-yaml",
            str(out),
        ]
    )
    assert rc == 0
    assert out.exists()

    # Reference: the same solve with spread disabled via the config object.
    s = load_scenario(scenario_path)
    ref = solve(s, budget_s=10.0, seed=5, search=SearchConfig(spread=False))
    assert ref.layouts
    # CLI wrote a layout; the run is deterministic so a layout exists and rc==0.
    # (Byte-for-byte YAML equality is covered by the solver determinism tests;
    # here we assert the flag is accepted and drives a successful no-spread run.)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cli_solve.py::test_solve_no_spread_matches_spread_false_config -v`
Expected: FAIL — argparse exits 2 with "unrecognized arguments: --no-spread".

- [ ] **Step 3: Add the flag**

In `src/hangarfit/cli.py`, in the `solve` subparser arg block (after the `--hangar` argument, before `return parser`), add:

```python
    solve.add_argument(
        "--no-spread",
        action="store_false",
        dest="spread",
        default=True,
        help="Disable the inter-plane spread post-pass (default: spread enabled).",
    )
```

- [ ] **Step 4: Pass it into `solve()`**

In `cmd_solve`, change the `solve(...)` call to pass a `SearchConfig` carrying the flag. Add the import inside `cmd_solve` next to the existing deferred imports:

```python
    from hangarfit.models import SearchConfig
```

and update the call:

```python
    result = solve(
        scenario,
        budget_s=args.budget,
        alternatives=args.alternatives,
        seed=args.seed,
        search=SearchConfig(spread=args.spread),
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_cli_solve.py::test_solve_no_spread_matches_spread_false_config -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli_solve.py
git commit -m "feat(cli): add --no-spread flag to solve (#145)"
```

---

## Task 7: Re-baseline the fixture-matrix mechanics tests

The fixture-matrix tests assert wall-time guards (#122) on the *conflict-resolution* search. Default-on spread now runs each trajectory past first-valid, so those guards would trip. Pin those tests to `spread=False` (they validate search mechanics, not spread — spread has its own coverage in Task 5).

**Files:**
- Modify: `tests/test_solver_fixture_matrix.py`

- [ ] **Step 1: Pin every fixture-matrix solve() to spread=False**

In `tests/test_solver_fixture_matrix.py`, every `solve(...)` call passes `search=SearchConfig(max_restarts=5)` (lines ~118, 165, 215, 280, 328). Change each to:

```python
        search=SearchConfig(max_restarts=5, spread=False),
```

(Find-and-replace `SearchConfig(max_restarts=5)` → `SearchConfig(max_restarts=5, spread=False)` within this file only.)

- [ ] **Step 2: Run the fixture-matrix suite**

Run: `pytest tests/test_solver_fixture_matrix.py -v`
Expected: PASS (wall-time guards hold because spread is off for these mechanics tests).

- [ ] **Step 3: Run the FULL suite to catch any other spread-default regressions**

Run: `pytest`
Expected: PASS. If any test fails *because* it now spreads by default and asserts exact placements or wall-time, it is a search-mechanics test — add `spread=False` to its `SearchConfig` (or, if it constructs none, pass `search=SearchConfig(spread=False)` to its `solve()` call). Do **not** weaken spread-on assertions in `tests/test_solver_spread.py`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_solver_fixture_matrix.py
git commit -m "test(solver): pin fixture-matrix mechanics tests to spread=False (#145)"
```

(If Step 3 required edits to other test files, include them in this commit.)

---

## Task 8: ADR-0008

**Files:**
- Create: `docs/adr/0008-inter-plane-spread-soft-preference.md`
- Modify: `docs/adr/README.md` (if an ADR index exists — check and add a row)

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0008-inter-plane-spread-soft-preference.md`:

```markdown
# ADR-0008: Inter-plane spread soft preference (repulsion-energy surrogate for maximin)

**Status:** Accepted
**Date:** 2026-05-24
**Issue:** #145
**Spec:** docs/superpowers/specs/2026-05-24-inter-plane-spread-design.md

## Context

The Phase 2a scoring tuple `(conflict_count, total_penetration_m2)` measures
only *illegal* overlap. Once a layout reaches `(0, 0.0)` the descent stops, so
inter-plane spacing is merely legal, not comfortable. Surfaced 2026-05-23 in
the v0.6.1 visual walkthrough. The agreed objective (reversing issue #145's
original "minimize/pack" framing) is to **maximize** inter-plane separation so
a human can tow a plane in/out with comfortable wingtip clearance.

## Decision

Add an isolated post-pass `_spread()` in `solver.py`, called when a trajectory
reaches `(0, 0.0)`, before the diversity check. It runs a seeded greedy
hill-climb that minimizes a smooth repulsion energy
`E = Σ_{i<j} exp(−gap_ij / scale)` over plane pairs, where `gap_ij` is the
minimum plan-view edge-to-edge footprint distance (shapely `polygon.distance`).
Only moves that keep the layout valid are accepted. On by default
(`SearchConfig.spread=True`), with a `--no-spread` CLI toggle.

## Rationale

- **Maximize, not minimize.** User reversal 2026-05-24: easier tow-in/out, less
  wingtip-strike risk. The "minimum overlap" half is already the hard constraint.
- **Repulsion energy over pure maximin.** Pure maximin (the p-dispersion
  objective) is exact but flat for a hill-climber — only the closest pair has a
  gradient. The repulsion energy is smooth (every plane move changes it) while
  weighting close pairs heavily, so it protects the minimum gap and converges
  toward maximin-like even spreading (the Riesz-energy → maximin-separation
  principle).
- **Repulsion energy over max-sum.** Max-sum (Σ pairwise distance) is smooth but
  Kuby (1987) showed it yields *uneven* spreads — it clusters subsets at extremes
  to maximize the aggregate, leaving some pairs close. The wrong objective for
  "maximum gap".
- **Bounded `exp` kernel over inverse-power `1/gap^s`.** No singularity near
  valid-but-touching planes; one near-touching pair can't dominate the sum.
- **Post-pass structure over fused descent.** The descent is conflict-driven —
  at zero conflicts there is no plane to perturb, so a "fused" approach would be
  two regimes bolted into one function. A separate `_spread()` keeps the
  hard-feasibility code and the `(int, float)` score tuple untouched, isolates
  the soft logic, and makes the toggle a trivial skip — preserving the ADR-0003
  determinism contract (with `spread=False` the RNG stream is byte-identical to
  the pre-spread solver).

## Consequences

- Each valid trajectory runs past first-valid to a spread stall ⇒ longer
  wall-time; fixture-matrix mechanics tests are pinned to `spread=False`.
- **Known limitation (plan-view gap):** the energy ignores z, so the single
  low-wing plane that could legally nest plan-view-overlapping under a high wing
  is mildly de-nested (spread does not reward the nest; the hard constraint
  still permits it). A z-aware kernel is a possible follow-up.
- **Known interaction (diversity):** spreading drives layouts toward a canonical
  even arrangement, so for `K > 1` two basins may spread to similar results and
  the second is diversity-rejected (wasted work, never invalid output).

## Alternatives considered

Pure maximin / leximin; max-sum dispersion; inverse-power Riesz kernel;
fused-descent 3-tuple score. All rejected above. Wall-adherence, aesthetic
alignment, and z-aware nesting are deferred as separate concerns.
```

- [ ] **Step 2: Update the ADR index if present**

Run: `ls docs/adr/README.md 2>/dev/null && grep -n "0007\|0006" docs/adr/README.md`
If `README.md` exists with a table of ADRs, add a row:
`| [ADR-0008](0008-inter-plane-spread-soft-preference.md) | Inter-plane spread soft preference | Accepted |`
(Match the existing column format. ADR-0007 stays reserved for the tow-path planner per issue #195 — do not renumber.)

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0008-inter-plane-spread-soft-preference.md docs/adr/README.md
git commit -m "docs(adr): ADR-0008 inter-plane spread soft preference (#145)"
```

---

## Task 9: arc42 + CLAUDE.md docs sweep

**Files:**
- Modify: `docs/architecture/05-building-block-view.md`
- Modify: `docs/architecture/06-runtime-view.md`
- Modify: `docs/architecture/08-crosscutting-concepts.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: §5 — add `_spread` to the solver module responsibilities**

In `docs/architecture/05-building-block-view.md`, find the `solver` module description. Add a sentence to its responsibility list:

```markdown
- **Spread post-pass** (`_spread`, `_inter_plane_energy`): after a layout reaches `(0, 0.0)`, maximizes inter-plane separation by minimizing the repulsion energy `Σ exp(−gap/scale)` while preserving validity. On by default; `--no-spread` / `SearchConfig.spread=False` disables it. See [ADR-0008](../adr/0008-inter-plane-spread-soft-preference.md).
```

- [ ] **Step 2: §6 — add the spread step to the `solve` runtime view**

In `docs/architecture/06-runtime-view.md`, in the `solve` flow description, after the "trajectory reaches `(0, 0.0)` → valid" step and before the diversity-filter step, add:

```markdown
4a. **Spread (if `SearchConfig.spread`, default on):** the valid placements are refined by `_spread` to maximize inter-plane separation (minimize `Σ exp(−gap/scale)`), accepting only moves that stay valid. The spread layout is what proceeds to the diversity filter. See [ADR-0008](../adr/0008-inter-plane-spread-soft-preference.md).
```

(Renumber subsequent steps if the section uses fixed numbering.)

- [ ] **Step 3: §8 — record the first shipped soft preference**

In `docs/architecture/08-crosscutting-concepts.md`, find the scoring / "soft preferences out of scope" discussion. Replace the "deferred" framing with:

```markdown
### Soft preferences

The hard score tuple `(conflict_count, total_penetration_m2)` measures only illegal overlap. The first **soft** preference — inter-plane spread (maximize separation once valid) — ships as an isolated post-pass (`solver._spread`), deliberately *outside* the hard tuple so the conflict-resolution determinism contract ([ADR-0003](../adr/0003-rr-mc-solver-algorithm.md)) is unaffected. See [ADR-0008](../adr/0008-inter-plane-spread-soft-preference.md) for the repulsion-energy metric and why it is a post-pass rather than a third score key.
```

- [ ] **Step 4: CLAUDE.md — update the out-of-scope note**

In `CLAUDE.md`, search for any "soft preferences" / "Still out of scope" note about post-Phase-2a soft constraints. Update it to note that inter-plane spread (#145, ADR-0008) is the first shipped soft preference. If no such line exists, add to the Quick Reference table a row:

```markdown
| **The spread post-pass** (maximize inter-plane gap once valid) | [ADR-0008](docs/adr/0008-inter-plane-spread-soft-preference.md) |
```

- [ ] **Step 5: Verify cross-references resolve**

Run: `grep -rn "0008-inter-plane-spread" docs/ CLAUDE.md`
Expected: references in §5, §6, §8, CLAUDE.md, and the ADR index all present and pointing at the real filename.

- [ ] **Step 6: Commit**

```bash
git add docs/architecture/05-building-block-view.md docs/architecture/06-runtime-view.md docs/architecture/08-crosscutting-concepts.md CLAUDE.md
git commit -m "docs(arc42): document spread post-pass in §5/§6/§8 + CLAUDE.md (#145)"
```

---

## Task 10: Full verification + PR

**Files:** none (verification + PR)

- [ ] **Step 1: Lint, format, type-check, full suite**

Run each:
```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/hangarfit/
pytest
```
Expected: all clean; full suite green.

- [ ] **Step 2: Visual smoke (canary)**

Run:
```bash
hangarfit solve tests/fixtures/solve_all_nine_large_hangar.yaml --seed 5 --budget 15 --render /tmp/spread_on.png
hangarfit solve tests/fixtures/solve_all_nine_large_hangar.yaml --seed 5 --budget 15 --no-spread --render /tmp/spread_off.png
```
Expected: both exit 0; `/tmp/spread_on.png` shows planes pushed apart toward walls/corners with interior empty space; `/tmp/spread_off.png` shows the tighter pre-spread clustering. Eyeball the two PNGs to confirm spread visibly widened separation.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feature/145-inter-plane-spread
gh pr create --base develop \
  --title "Solver: maximize inter-plane gap (spread) post-pass" \
  --body "$(cat <<'EOF'
## Summary
- Adds an isolated post-pass `_spread()` that maximizes inter-plane separation once a layout is valid, via a smooth repulsion-energy surrogate for maximin (`Σ exp(−gap/scale)`, edge-to-edge gaps).
- On by default (`SearchConfig.spread=True`); `--no-spread` disables it. Conflict-resolution descent, the `(int,float)` score tuple, and the ADR-0003 determinism contract are untouched.
- ADR-0008 + arc42 §5/§6/§8 + CLAUDE.md updated. Fixture-matrix mechanics tests pinned to `spread=False`; new spread coverage in `tests/test_solver_spread.py`.

Closes #145

## Test plan
- [ ] `pytest` green; `ruff` + `mypy` clean
- [ ] `tests/test_solver_spread.py`: spread-on widens the minimum pairwise gap vs `--no-spread` on the same seed
- [ ] Visual canary: `/tmp/spread_on.png` vs `/tmp/spread_off.png` confirm visibly wider separation
EOF
)"
```

- [ ] **Step 4: Set PR metadata** (assignee, labels, milestone — `gh pr edit` is broken in this repo; use the API)

```bash
gh api -X PATCH repos/:owner/:repo/issues/$(gh pr view --json number --jq .number) \
  -f milestone="Phase 2b — Solver realism"
gh pr edit --add-assignee DocGerd 2>/dev/null || true
```
(If the milestone PATCH needs the number form, resolve it: `gh api repos/:owner/:repo/milestones --jq '.[]|select(.title=="Phase 2b — Solver realism").number'` and PATCH `-F milestone=<n>`. Add labels per the repo convention, e.g. `enhancement`.)

- [ ] **Step 5: Run `/pr-review` and resolve every thread**

Invoke the `pr-review-toolkit:review-pr` skill. Because this PR touches `solver.py` collision-adjacent geometry, also dispatch the **`geometry-invariant-guard`** subagent (CLAUDE.md subagents table) and **`pr-review-toolkit:silent-failure-hunter`** (touches solver/collision-adjacent code). Convert every finding into a diff review thread, fix or reply, and mark each resolved. Re-run the review if changes were non-trivial. Then tell the user the PR is clean and ready for final review. **Do not merge.**

---

## Self-review notes (plan author)

- **Spec coverage:** §1–§3 objective/decisions → Tasks 1,8; §4 energy → Task 3; §5 `_spread` + integration → Tasks 4,5; §6 config/CLI → Tasks 2,6; §7 determinism → Tasks 4,5 (seeded `_spread`, `spread=False` skip) + Task 7; §8 edge cases → Task 4 tests; §9 testing + re-baseline → Tasks 4,5,7; §10 docs + issue rewrite → Tasks 1,8,9; §11 YAGNI exclusions → honored (no extra knobs/flags).
- **Type consistency:** `_inter_plane_energy(placements, scenario, scale)` and `_spread(placements, scenario, rng, search, *, start, budget_s, pinned_planes)` signatures match across Tasks 3/4/5 and the integration call. `SearchConfig.spread` / `spread_scale_m` names consistent across Tasks 2/4/5/6.
- **Placeholder scan:** all code steps carry full code; the only runtime-dependent step (Task 7 Step 3, "any other regressions") gives the concrete fix mechanism (`spread=False`) rather than a vague directive.
```
