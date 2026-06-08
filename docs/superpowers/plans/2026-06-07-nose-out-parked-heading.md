# Nose-out parked heading (#263) + `tow_pivotable` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the RR-MC solver *prefer* nose-out (toward-the-door) parked headings via an RNG-free `_nose_out` 180°-flip post-pass (default ON), and add a per-plane `tow_pivotable` flag that routes flagged planes with a pivot-in-place tow motion.

**Architecture:** Two orthogonal units. (1) `_nose_out` is a pure, RNG-free transform in `solver.py` that runs after `_spread` on each valid basin, flipping a movable plane's heading to its antipode iff that is strictly more nose-out and the layout stays valid (`_score == (0,0.0)`). (2) `tow_pivotable: bool` on `Aircraft` makes `effective_turn_radius_m()` return `0.0`, reusing the existing zero-radius cart-pivot machinery — `towplanner.py` is untouched. Determinism (ADR-0003) is preserved: `_nose_out` draws no RNG, so output is byte-identical even with the feature on.

**Tech stack:** Python 3.12, frozen `@dataclass(slots=True)` models, `pytest` (markers `slow`/`serial`), `ruff`, `mypy`, `shapely` (collision geometry). Determinism is contractual — read `docs/adr/0003-rr-mc-solver-algorithm.md` and `.claude/agents/determinism-guard.md` before touching `solver.py`.

**Spec:** `docs/superpowers/specs/2026-06-07-nose-out-parked-heading-design.md`. **New ADR:** 0022.

**Convention reminder (ADR-0002):** `heading 0` = nose-IN (+y, deep); `heading 180` = nose-OUT (−y, door). "nose-out-ness" = `_heading_delta_short_arc(h, 180.0)` (smaller = more nose-out).

---

## File map

| File | Change |
|---|---|
| `src/hangarfit/models.py` | `Aircraft.tow_pivotable`, `effective_turn_radius_m`, `SearchConfig.nose_out`, `PlaneConstraint.nose_out`, `SolverDiagnostics.nose_out_flips` + validation, `SolveResult.__post_init__` length guard |
| `src/hangarfit/solver.py` | `_nose_out` function, call site, `_SpreadCandidate.nose_out_flips`, `_build_found_result` wiring |
| `src/hangarfit/loader.py` | parse `nose_out` (constraint), `tow_pivotable` (aircraft) |
| `src/hangarfit/cli.py` | `--no-nose-out` on `solve` + `view`, `SearchConfig(nose_out=...)` wiring, `--json` `nose_out_flips` |
| `data/fleet.yaml` | `tow_pivotable: true` for `aviat_husky`, `fk9_mkii`, `ctsl` |
| `tests/test_solver_nose_out.py` | NEW — behavior + determinism + diagnostics |
| `tests/test_solver_canaries.py`, `tests/test_solver_search.py` | pin `nose_out=False`; add `nose_out=True` canary |
| `tests/test_models*.py`, `tests/test_loader*.py`, `tests/test_cli*.py`, a towplanner test | `tow_pivotable` + `nose_out` + CLI/loader coverage |
| `docs/adr/0022-nose-out-parked-heading.md`, `docs/adr/README.md`, `docs/architecture/08-crosscutting-concepts.md`, `CHANGELOG.md`, `.claude/agents/determinism-guard.md` | docs + agent amendment |

**Two re-baseline checkpoints (the spec's flagged risks) — pause and surface to the user if large:**
- **CP-A (Task 3):** flagging the 3 fleet planes `tow_pivotable` changes their tow paths (arc→pivot) in every non-stubbed `plan_fill` fixture.
- **CP-B (Task 7):** default-ON `nose_out` changes parked headings in fixture-asserting solver tests.

---

## Task 1: `Aircraft.tow_pivotable` field + `effective_turn_radius_m`

**Files:**
- Modify: `src/hangarfit/models.py:234-243` (field), `src/hangarfit/models.py:299-310` (accessor)
- Test: `tests/test_models.py` (or the existing aircraft model test file — confirm with `ls tests/test_models*.py`)

- [ ] **Step 1: Write the failing test**

Add to the aircraft model test file:

```python
def test_tow_pivotable_defaults_false_and_overrides_effective_radius():
    from hangarfit.models import Aircraft
    base = _make_aircraft(  # use the file's existing aircraft factory/fixture
        movement_mode="always_own_gear", turn_radius_m=5.0
    )
    assert base.tow_pivotable is False
    assert base.effective_turn_radius_m() == 5.0

    pivot = replace(base, tow_pivotable=True)  # from dataclasses import replace
    assert pivot.effective_turn_radius_m() == 0.0
    # The declared turn radius is retained (powered-taxi semantics); only the
    # *tow* radius is overridden.
    assert pivot.turn_radius_m == 5.0
```

If the test file has no aircraft factory, build one inline mirroring an existing `Aircraft(...)` construction in that file (it needs `id, name, wing_position, gear, movement_mode, turn_radius_m, measured, parts, wheels`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -k tow_pivotable -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'tow_pivotable'` (or `AttributeError`).

- [ ] **Step 3: Add the field**

In `models.py`, after `notes: str = ""` (line 243):

```python
    notes: str = ""
    tow_pivotable: bool = False
    """(#263 / ADR-0022) When True, this plane is planned with the pivot-in-place
    *towing* motion: :meth:`effective_turn_radius_m` returns ``0.0`` so the tow
    planner routes it with the zero-radius cart-pivot fan (no new motion
    primitive). Models a free-castering tailwheel or a tail-down nose-lift pivot.
    Orthogonal to ``movement_mode`` — a flagged own-gear plane stays
    ``on_carts=False`` and the cart-pool accounting is untouched. The declared
    ``turn_radius_m`` is retained (powered-taxi semantics); only the tow radius is
    overridden. Default ``False``."""
```

- [ ] **Step 4: Update the accessor**

In `models.py:308-309`, change the `always_cart` branch:

```python
        if self.movement_mode == "always_cart" or self.tow_pivotable:
            return 0.0
        return self.required_turn_radius_m()
```

Also update the `effective_turn_radius_m` docstring's first line to mention the pivot flag (e.g. append: "or ``0.0`` for a ``tow_pivotable`` plane (pivot-in-place tow motion)").

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_models.py -k tow_pivotable -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(models): Aircraft.tow_pivotable -> zero tow radius (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Loader parses `tow_pivotable`

**Files:**
- Modify: `src/hangarfit/loader.py:865-876` (the `Aircraft(...)` construction in `_build_aircraft`)
- Test: `tests/test_loader.py` (confirm the fleet-loader test file with `ls tests/test_loader*.py`)

- [ ] **Step 1: Write the failing test**

```python
def test_loader_tow_pivotable_default_false_and_parsed():
    # _build_aircraft / load_fleet from a minimal in-memory fleet entry.
    # Reuse the file's existing fleet-entry fixture builder; assert:
    #   - an entry WITHOUT tow_pivotable -> aircraft.tow_pivotable is False
    #   - an entry with `tow_pivotable: true` -> aircraft.tow_pivotable is True
    #   - `tow_pivotable: "true"` (quoted) raises LoaderError (strict _to_bool)
    ...
```

Model it on the existing `measured`-field loader test (search `test_loader*.py` for `measured`), which already exercises `_to_bool` default + parse + strict-reject.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_loader.py -k tow_pivotable -v`
Expected: FAIL — aircraft has no `tow_pivotable` set from YAML (always default False, so the `true` case fails).

- [ ] **Step 3: Add the parse**

In `loader.py`, in the `Aircraft(...)` construction (line 865-876), add after `measured=...` (line 872):

```python
        measured=_to_bool(entry.get("measured", False), "measured"),
        tow_pivotable=_to_bool(entry.get("tow_pivotable", False), "tow_pivotable"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_loader.py -k tow_pivotable -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader.py
git commit -m "feat(loader): parse Aircraft.tow_pivotable from fleet.yaml (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Flag the fleet planes + measure tow-path blast radius (CHECKPOINT CP-A)

**Files:**
- Modify: `data/fleet.yaml` (entries `aviat_husky` ~L75, `ctsl` ~L287, `fk9_mkii` ~L318)

- [ ] **Step 1: Add the flags**

In `data/fleet.yaml`, add `tow_pivotable: true` to each of the three entries, next to `movement_mode`, with a one-line comment. Example for `aviat_husky`:

```yaml
  - id: aviat_husky
    ...
    movement_mode: always_own_gear
    tow_pivotable: true  # free-castering tailwheel — pivots in place when towed (#263)
    ...
```

For `ctsl` and `fk9_mkii` (light nosewheel): comment `# tail-down nose-lift pivot when towed (#263)`.

- [ ] **Step 2: Measure the blast radius**

Run the full suite and capture failures:

```bash
pytest -m "" -q 2>&1 | tail -40
```

Expected: some `tests/test_towplanner_*.py` / path-quality / `solve(plan_paths=True)` golden tests now differ (arc→pivot for the 3 planes). Count them.

- [ ] **Step 3: CHECKPOINT — assess and surface**

- If the failing set is **small and clearly path-shape re-baselines** (the asserted path changed from an arc to a pivot for a flagged plane, layout still valid, plan still routes): re-baseline each by updating the expected path/length to the new deterministic value, **verifying each is a legitimate pivot path** (not a regression — the plan must still pass `path_first_conflict`).
- If the failing set is **large or includes validity/determinism failures**: STOP and surface to the user with the count + a sample — they may prefer to keep the `tow_pivotable` *mechanism* (Tasks 1-2) but defer flipping the `data/fleet.yaml` flags to a follow-up to bound the re-baseline. (Per the spec's CP-A commitment.)

- [ ] **Step 4: Re-baseline the confirmed path goldens**

For each confirmed test, update the expected value and re-run that test to PASS. Do NOT blanket-regenerate — inspect each.

- [ ] **Step 5: Verify determinism still holds for a flagged plane's path**

Run a towplanner determinism check that routes a flagged plane twice and diffs (or rely on `bench`):

```bash
python -m bench.profile_pipeline 2>&1 | tail -20
```

Expected: every regime's `det` verdict is `ok` (RNG-free; static fleet property).

- [ ] **Step 6: Commit**

```bash
git add data/fleet.yaml tests/
git commit -m "feat(fleet): flag aviat_husky/ctsl/fk9_mkii tow_pivotable; re-baseline tow paths (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Models for nose-out (`SearchConfig`, `PlaneConstraint`, `SolverDiagnostics`, `SolveResult`)

**Files:**
- Modify: `src/hangarfit/models.py` — `SearchConfig` (after `spread_stall_epsilon_m`, before `__post_init__` ~L1161), `PlaneConstraint` (~L632-634), `SolverDiagnostics` (field list ~L935 + `__post_init__` ~L960), `SolveResult.__post_init__` (~L1014)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
def test_nose_out_model_fields():
    from hangarfit.models import SearchConfig, PlaneConstraint, SolverDiagnostics
    assert SearchConfig().nose_out is True
    assert SearchConfig(nose_out=False).nose_out is False
    assert PlaneConstraint().nose_out is None
    assert PlaneConstraint(nose_out=True).nose_out is True
    assert PlaneConstraint(nose_out=False).nose_out is False
    assert SolverDiagnostics(
        restarts_attempted=1, wall_time_s=0.0, best_partial=None,
        best_partial_layout=None, seed=1,
    ).nose_out_flips == ()


def test_nose_out_flips_must_be_non_negative():
    import pytest
    from hangarfit.models import SolverDiagnostics
    with pytest.raises(ValueError, match="nose_out_flips"):
        SolverDiagnostics(
            restarts_attempted=1, wall_time_s=0.0, best_partial=None,
            best_partial_layout=None, seed=1, nose_out_flips=(-1,),
        )


def test_solveresult_nose_out_flips_length_guard():
    import pytest
    from hangarfit.models import SolveResult, SolverDiagnostics
    # build a one-layout result whose diagnostics carry a 2-tuple -> ValueError
    # (reuse the file's existing SolveResult/layout fixtures; mirror the
    # min_pairwise_gap_m length-guard test if one exists)
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_models.py -k "nose_out" -v`
Expected: FAIL — fields don't exist.

- [ ] **Step 3: Add `SearchConfig.nose_out`**

In `models.py`, after the `spread_stall_epsilon_m` docstring (line 1161), before `def __post_init__` (1163):

```python
    nose_out: bool = True
    """(#263 / ADR-0022) When True (default), ``solve()`` runs the RNG-free
    ``_nose_out`` post-pass on each valid basin (after ``_spread``): it flips a
    movable plane's parked heading 180° toward nose-out (heading 180, toward the
    door) when that is strictly more nose-out AND keeps the layout valid. A soft
    preference — never overrides validity, never moves a plane, never un-parks one
    (ADR-0008's discipline). RNG-free ⇒ byte-identical determinism holds even with
    it ON (stronger than ``spread``); set False for the pre-feature heading
    behaviour. Runs independently of ``spread`` (a placement concern), so it also
    applies on the ``spread=False`` fast path and the spread→no-spread fallback.
    Per-plane override via :attr:`PlaneConstraint.nose_out`. A plain ``bool`` (not
    ``bool | None``): ``None`` stays reserved as the disable sentinel for
    ``max_restarts``/``spread_stall_restarts`` (see ``max_restarts``)."""
```

No `__post_init__` validation needed (a bool has no invalid value).

- [ ] **Step 4: Add `PlaneConstraint.nose_out`**

In `models.py`, the `PlaneConstraint` fields (line 632-634) become:

```python
    pin: Placement | None = None
    force_on_carts: bool | None = None
    priority: float | None = None
    nose_out: bool | None = None
```

Extend the `PlaneConstraint` docstring with a paragraph:

```
    ``nose_out`` (#263 / ADR-0022) is the per-plane override of the global
    ``SearchConfig.nose_out`` preference. **Its ``None`` semantics differ from its
    optional siblings:** ``pin``/``force_on_carts`` ``None`` means "free/unset",
    but ``nose_out`` ``None`` means "follow the global ``SearchConfig.nose_out``".
    ``True`` ⇒ always prefer nose-out for this plane; ``False`` ⇒ never flip it —
    the legitimate nose-IN exemption (e.g. a low-wing tucked under a high-wing's
    tail). Soft: it only re-orients, never overrides validity or a hard ``pin``.
```

- [ ] **Step 5: Add `SolverDiagnostics.nose_out_flips` + validation**

In `models.py`, after `apron_shallow_drops` (line 935):

```python
    apron_shallow_drops: tuple[ApronShallowDrop, ...] = ()
    nose_out_flips: tuple[int, ...] = ()
```

Add a docstring paragraph in the class docstring (near `min_pairwise_gap_m`):

```
    ``nose_out_flips`` is index-aligned with :attr:`SolveResult.layouts`: the
    number of nose-out heading flips the RNG-free ``_nose_out`` post-pass applied
    to that returned layout (#263, ADR-0022). ``0`` for a layout where no plane
    was flipped (or with ``nose_out`` disabled). Advisory / RNG-free.
```

In `SolverDiagnostics.__post_init__`, after the `min_pairwise_gap_m` check (line 960-964):

```python
        if any(n < 0 for n in self.nose_out_flips):
            raise ValueError(
                "SolverDiagnostics.nose_out_flips entries must be >= 0, "
                f"got {self.nose_out_flips!r}"
            )
```

- [ ] **Step 6: Add the `SolveResult` length guard**

In `SolveResult.__post_init__`, after the `min_pairwise_gap_m` guard (line 1014-1021):

```python
        if self.diagnostics.nose_out_flips and len(self.diagnostics.nose_out_flips) != len(
            self.layouts
        ):
            raise ValueError(
                "SolveResult.diagnostics.nose_out_flips, when populated, must be "
                f"index-aligned with layouts: got {len(self.diagnostics.nose_out_flips)} "
                f"counts for {len(self.layouts)} layouts"
            )
```

- [ ] **Step 7: Run to verify it passes**

Run: `pytest tests/test_models.py -k "nose_out" -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(models): nose_out config + per-plane override + flip-count diagnostic (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `_nose_out` post-pass + solver wiring

**Files:**
- Modify: `src/hangarfit/solver.py` — new `_nose_out` (near `_spread`, after `_spread_quality`), call site (~L294-322), `_SpreadCandidate` (~L1603), `_build_found_result` (~L592-618)
- Test: `tests/test_solver_nose_out.py` (NEW)

- [ ] **Step 1: Write the failing behavior tests**

Create `tests/test_solver_nose_out.py`. These call `_nose_out` directly with hand-built placements (fast, no search). Build a roomy hangar + 1-2 planes so a flip stays valid. Use `solve_fresh_alternatives_three.yaml`'s fleet/hangar or build programmatically (mirror `tests/test_towplanner_nose_out.py:26-88` which constructs a 30×20 hangar + plane in Python).

```python
import math
from hangarfit.models import Placement, SearchConfig, PlaneConstraint
from hangarfit.solver import _nose_out
# helpers to build a Scenario with a roomy hangar + given placements/constraints

def test_flips_nose_in_plane_toward_out():
    # one plane parked nose-IN (heading 0) in open space
    placements = {"p": Placement("p", x_m=10.0, y_m=12.0, heading_deg=0.0, on_carts=False)}
    scenario = _roomy_scenario(["p"])
    out, flips = _nose_out(placements, scenario, SearchConfig(), pinned_planes=frozenset())
    assert flips == 1
    assert math.isclose(out["p"].heading_deg, 180.0)
    # position unchanged
    assert (out["p"].x_m, out["p"].y_m) == (10.0, 12.0)

def test_already_nose_out_is_noop():
    placements = {"p": Placement("p", 10.0, 12.0, 180.0, on_carts=False)}
    scenario = _roomy_scenario(["p"])
    out, flips = _nose_out(placements, scenario, SearchConfig(), pinned_planes=frozenset())
    assert flips == 0 and out["p"].heading_deg == 180.0

def test_sideways_90_not_flipped():
    # short_arc(90,180)==90 == short_arc(270,180); strict < => no flip
    placements = {"p": Placement("p", 10.0, 12.0, 90.0, on_carts=False)}
    scenario = _roomy_scenario(["p"])
    out, flips = _nose_out(placements, scenario, SearchConfig(), pinned_planes=frozenset())
    assert flips == 0 and out["p"].heading_deg == 90.0

def test_flip_rejected_when_it_breaks_validity():
    # construct a layout where the nose-in heading is the ONLY valid one
    # (flipping collides with a wall/neighbour) -> stays nose-in, flips==0
    ...

def test_pinned_plane_never_flips():
    placements = {"p": Placement("p", 10.0, 12.0, 0.0, on_carts=False)}
    scenario = _roomy_scenario(["p"])
    out, flips = _nose_out(placements, scenario, SearchConfig(), pinned_planes=frozenset({"p"}))
    assert flips == 0 and out["p"].heading_deg == 0.0

def test_per_plane_false_excludes_even_when_global_on():
    placements = {"p": Placement("p", 10.0, 12.0, 0.0, on_carts=False)}
    scenario = _roomy_scenario(["p"], constraints={"p": PlaneConstraint(nose_out=False)})
    out, flips = _nose_out(placements, scenario, SearchConfig(nose_out=True), pinned_planes=frozenset())
    assert flips == 0 and out["p"].heading_deg == 0.0

def test_per_plane_true_flips_when_global_off():
    placements = {"p": Placement("p", 10.0, 12.0, 0.0, on_carts=False)}
    scenario = _roomy_scenario(["p"], constraints={"p": PlaneConstraint(nose_out=True)})
    out, flips = _nose_out(placements, scenario, SearchConfig(nose_out=False), pinned_planes=frozenset())
    assert flips == 1 and math.isclose(out["p"].heading_deg, 180.0)
```

(`_roomy_scenario` is a local helper building a `Scenario` with a 30×25 hangar and the named planes from a roomy fleet. Reuse the fleet/hangar from `tests/fixtures/test_hangar_large.yaml` + `data/fleet.yaml` via `load_scenario`, or construct directly. Use a single small plane so the flip is trivially valid in open space; use `aviat_husky` for the validity-rejection case in a tight spot.)

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_solver_nose_out.py -v`
Expected: FAIL — `_nose_out` not defined (ImportError).

- [ ] **Step 3: Implement `_nose_out`**

In `solver.py`, immediately after `_spread_quality` (ends ~L1087) or before `_spread`:

```python
def _nose_out(
    placements: dict[str, Placement],
    scenario: Scenario,
    search: SearchConfig,
    *,
    pinned_planes: frozenset[str],
) -> tuple[dict[str, Placement], int]:
    """RNG-free post-pass: flip movable planes' parked headings toward nose-out.

    For each movable plane in ``sorted(plane_id)`` order, apply the
    zero-displacement antipodal flip ``(h + 180) % 360`` (preserving x/y/on_carts)
    iff it is **strictly more nose-out** (closer to heading 180, the door) AND the
    layout stays valid (``_score == (0, 0.0)``). Re-validated against the CURRENT
    (possibly already-flipped) set, one plane at a time, so two individually-valid
    flips can never jointly invalidate. Returns ``(placements, n_flips)``.

    Soft preference (ADR-0022), mirroring ``_spread`` but **RNG-free** — it takes
    no ``rng`` and consumes no RNG draw, so the seeded stream (and thus the
    ADR-0003 byte-identical contract) is unchanged even with the feature ON.

    Per-plane override (``PlaneConstraint.nose_out``): ``None`` ⇒ follow the global
    ``search.nose_out``; ``True`` ⇒ prefer-out; ``False`` ⇒ never flip (nose-IN
    exemption).
    """
    movable = sorted(pid for pid in placements if pid not in pinned_planes)
    flips = 0
    for pid in movable:
        constraint = scenario.constraints.get(pid)
        want = (
            constraint.nose_out
            if constraint is not None and constraint.nose_out is not None
            else search.nose_out
        )
        if not want:
            continue
        current = placements[pid]
        flipped_heading = (current.heading_deg + 180.0) % 360.0
        # Strictly more nose-out. Because the flip is the exact antipode and
        # short_arc(antipode, 180) == 180 - short_arc(h, 180), this is identical
        # to "the flip lands in the nose-out hemisphere (< 90°)".
        if _heading_delta_short_arc(flipped_heading, 180.0) >= _heading_delta_short_arc(
            current.heading_deg, 180.0
        ):
            continue
        flipped = Placement(
            plane_id=current.plane_id,
            x_m=current.x_m,
            y_m=current.y_m,
            heading_deg=flipped_heading,
            on_carts=current.on_carts,
        )
        trial = dict(placements)
        trial[pid] = flipped
        try:
            trial_layout = Layout(
                fleet=scenario.fleet,
                hangar=scenario.hangar,
                placements=tuple(trial.values()),
                maintenance_plane=scenario.maintenance_plane,
            )
        except ValueError:
            # Defensive, mirroring _spread: the flip preserves on_carts so the cart
            # rule cannot trip; a ValueError would be a structural bug — skip it,
            # the layout is re-validated by the _score gate below regardless.
            continue
        if _score(trial_layout) != (0, 0.0):
            continue
        placements[pid] = flipped
        flips += 1
    return placements, flips
```

- [ ] **Step 4: Run behavior tests to verify they pass**

Run: `pytest tests/test_solver_nose_out.py -v`
Expected: PASS (the validity-rejection test needs a genuinely tight fixture; adjust until it exercises the `_score != (0,0.0)` branch).

- [ ] **Step 5: Wire the call site**

In `solver.py`, replace the valid-basin handler body (lines 294-322) so `_nose_out` runs after `_spread`, independent of `spread`, and the flip count rides into the pool:

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
                n_flips = 0
                if search.nose_out:
                    placements, n_flips = _nose_out(
                        placements, scenario, search, pinned_planes=pinned_planes
                    )
                # _spread / _nose_out preserve every Layout invariant; a ValueError
                # here would be a structural bug, so let it propagate.
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
                        nose_out_flips=n_flips,
                    )
                )
                break  # restart to seek a different basin
```

- [ ] **Step 6: Add `nose_out_flips` to `_SpreadCandidate`**

In `solver.py:1603-1609`:

```python
class _SpreadCandidate(NamedTuple):
    """A valid, spread-polished basin found during search, with its quality."""

    layout: Layout
    min_gap: float
    energy: float
    restart_index: int
    nose_out_flips: int = 0
```

- [ ] **Step 7: Thread the count into `_build_found_result`**

In `solver.py:592-618`, after `min_gaps = tuple(...)` (line 593):

```python
    accepted_layouts = [c.layout for c in selected]
    min_gaps = tuple(c.min_gap for c in selected)
    nose_out_flips = tuple(c.nose_out_flips for c in selected)
```

and in the `SolverDiagnostics(...)` construction (after `apron_shallow_drops=apron_drops,`):

```python
            apron_shallow_drops=apron_drops,
            nose_out_flips=nose_out_flips,
```

- [ ] **Step 8: Write a solve()-level diagnostics test**

Add to `tests/test_solver_nose_out.py`:

```python
def test_solve_reports_nose_out_flips_per_layout():
    from hangarfit.solver import solve, SearchConfig
    scenario = _load_roomy_three()  # solve_fresh_alternatives_three.yaml
    r = solve(scenario, budget_s=5.0, seed=1, search=SearchConfig(max_restarts=30),
              plan_paths=False)
    assert len(r.diagnostics.nose_out_flips) == len(r.layouts)
    # at least one plane lands within 90° of nose-out
    hs = [p.heading_deg for p in r.layouts[0].placements]
    assert any(_heading_delta_short_arc(h, 180.0) < 90.0 for h in hs)
```

- [ ] **Step 9: Run the new file to verify pass**

Run: `pytest tests/test_solver_nose_out.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_nose_out.py
git commit -m "feat(solver): RNG-free _nose_out 180-flip post-pass, default on (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Determinism canaries

**Files:**
- Modify: `tests/test_solver_canaries.py` (~L83/86 spread=False canary; the `max_restarts` canary already sets `spread=False`), `tests/test_solver_search.py` (the `spread=False` byte-identity canaries)
- Add: cross-process + nose_out-on canaries in `tests/test_solver_nose_out.py`

- [ ] **Step 1: Pin existing byte-identical canaries with `nose_out=False`**

The byte-identical canaries assert run-to-run equality. With `nose_out=True` (default) they still pass (RNG-free), but to keep them testing the *pure-RNG* path unambiguously, add `nose_out=False`:

- `tests/test_solver_canaries.py:83,86`: `SearchConfig(spread=False)` → `SearchConfig(spread=False, nose_out=False)`.
- `tests/test_solver_canaries.py:169,178`: `SearchConfig(max_restarts=max_restarts, spread=False)` → add `, nose_out=False`.
- `tests/test_solver_search.py` byte-identity canaries (`:385`, `:479`, `:841`, `:882`, `:914`, `:934`, `:1011`): add `nose_out=False` to each `SearchConfig(spread=False, ...)`.

(These are the determinism/golden canaries. Leave behavior tests that don't assert exact layouts alone — they're handled in CP-B.)

- [ ] **Step 2: Run to verify they still pass**

Run: `pytest -m "serial or not serial" tests/test_solver_canaries.py tests/test_solver_search.py -q`
Expected: PASS (no behavior change — `nose_out=False` is the pre-feature path).

- [ ] **Step 3: Add a `nose_out=True` determinism canary (RNG-free even ON)**

In `tests/test_solver_nose_out.py`:

```python
def test_nose_out_on_is_byte_identical_for_same_seed():
    """RNG-free ⇒ two nose_out=True solves are byte-identical (stronger than
    spread). max_restarts-bound (NOT budget_s) so it is load-independent."""
    from hangarfit.solver import solve, SearchConfig
    s1 = _load_roomy_three(); s2 = _load_roomy_three()
    cfg = SearchConfig(max_restarts=10, nose_out=True)
    r1 = solve(s1, budget_s=1000.0, alternatives=1, seed=42, search=cfg, plan_paths=False)
    r2 = solve(s2, budget_s=1000.0, alternatives=1, seed=42, search=cfg, plan_paths=False)
    assert [p for L in r1.layouts for p in L.placements] == [
        p for L in r2.layouts for p in L.placements
    ]
    assert r1.diagnostics.nose_out_flips == r2.diagnostics.nose_out_flips
```

- [ ] **Step 4: Add the cross-process `PYTHONHASHSEED`-varied canary**

Copy the subprocess pattern from `tests/test_towplanner_apron.py:444-483` (`test_apron_movesplan_byte_identical_across_processes`). Run the same `nose_out=True` solve in two subprocesses under `PYTHONHASHSEED=111` and `PYTHONHASHSEED=777`, print a digest of `(plane_id, x, y, heading, on_carts)` for the selected layout, and assert the two digests are equal. This pins the `sorted(...)` iteration in `_nose_out` (an in-process `==` can't catch a set-order leak since `PYTHONHASHSEED` is fixed within one process). Mark `@pytest.mark.serial` if it shells out.

- [ ] **Step 5: Run the determinism canaries**

Run: `pytest tests/test_solver_nose_out.py -k "byte_identical or across_processes" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test(solver): pin nose_out determinism (off-golden + on-byte-identical + cross-process) (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Loader per-plane `nose_out` + default-ON re-baseline (CHECKPOINT CP-B)

**Files:**
- Modify: `src/hangarfit/loader.py:570-574` (`_build_plane_constraint`)
- Test: `tests/test_loader.py`

- [ ] **Step 1: Write the failing loader test**

```python
def test_loader_constraint_nose_out_tristate():
    # _build_plane_constraint from a constraints entry:
    #   - no key            -> nose_out is None
    #   - nose_out: true     -> True
    #   - nose_out: false    -> False
    #   - nose_out: "true"  -> LoaderError (strict _to_bool)
    ...
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_loader.py -k nose_out -v`
Expected: FAIL.

- [ ] **Step 3: Add the parse**

In `loader.py`, after the `priority` parse (line 570-572), before the return:

```python
    nose_out = data.get("nose_out")
    if nose_out is not None:
        nose_out = _to_bool(nose_out, "nose_out")

    return PlaneConstraint(
        pin=pin, force_on_carts=force_on_carts, priority=priority, nose_out=nose_out
    )
```

Also document `nose_out: <bool>` in the `_build_plane_constraint` docstring YAML schema block (line 518-522).

- [ ] **Step 4: Run loader test to PASS**

Run: `pytest tests/test_loader.py -k nose_out -v`
Expected: PASS.

- [ ] **Step 5: CHECKPOINT — measure default-ON re-baseline**

Run the full suite:

```bash
pytest -m "" -q 2>&1 | tail -50
```

Default-ON `nose_out` now changes parked *headings* in solver tests that assert exact layouts. For each failure:
- Confirm it is a **heading-only** change (positions/status/`min_pairwise_gap_m`/validity UNCHANGED — only `heading_deg` flipped toward 180 on some plane).
- If so, re-baseline the expected heading.
- If a failure changes **position/status/gap/validity**, that is a BUG — STOP and debug (do not re-baseline).

If the heading-re-baseline set is **large**, surface the count to the user before mass-editing (CP-B commitment), and re-baseline in a focused pass.

- [ ] **Step 6: Re-baseline confirmed heading goldens; run suite green**

Run: `pytest -m "" -q`
Expected: all PASS (after legitimate heading re-baselines).

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/loader.py tests/
git commit -m "feat(loader): per-plane nose_out override; re-baseline default-on headings (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: CLI `--no-nose-out` (solve + view) + `--json`

**Files:**
- Modify: `src/hangarfit/cli.py` — solve parser (~L264, after `--no-back-fill`), solve `SearchConfig` (~L634), view parser (~L366, after `--spread`), view `SearchConfig` (~L1033), `--json` (~L980)
- Test: `tests/test_cli*.py`

- [ ] **Step 1: Write the failing CLI test**

```python
def test_solve_no_nose_out_flag_disables(monkeypatch, ...):
    # parse `solve scenario.yaml --no-nose-out` -> args.nose_out is False;
    # and `solve scenario.yaml` -> args.nose_out is True (default).
    # Plus: a --json solve run includes diagnostics.nose_out_flips (a list).
    ...
```

Model on the existing `--no-spread` / `--no-back-fill` CLI tests (search `tests/test_cli*.py` for `no_spread` / `back_fill`).

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_cli.py -k nose_out -v`
Expected: FAIL — flag/arg/JSON field absent.

- [ ] **Step 3: Add the `solve` flag**

In `cli.py`, after the `--no-back-fill` block (ends ~L264):

```python
    solve.add_argument(
        "--no-nose-out",
        action="store_false",
        dest="nose_out",
        default=True,
        help=(
            "Disable the nose-out parked-heading preference (#263). By default the "
            "solver flips each plane's parked heading toward the door (for an easy "
            "straight-out exit) when that stays collision-valid; pass this to keep "
            "the packing-chosen heading. Per-plane override: constraints.<id>.nose_out."
        ),
    )
```

- [ ] **Step 4: Wire `solve`'s `SearchConfig`**

In `cli.py:634-637`:

```python
        search=SearchConfig(
            spread=args.spread,
            nose_out=args.nose_out,
            back_bias_weight=_BACK_FILL_DEFAULT_WEIGHT if args.back_fill else 0.0,
        ),
```

- [ ] **Step 5: Add the `view` flag + wire it**

After the `view --spread` block (ends ~L366):

```python
    view.add_argument(
        "--no-nose-out",
        action="store_false",
        dest="nose_out",
        default=True,
        help="Solve mode: disable the nose-out parked-heading preference (#263).",
    )
```

In `cli.py:1033`:

```python
                search=SearchConfig(spread=args.spread, nose_out=args.nose_out),
```

- [ ] **Step 6: Add `nose_out_flips` to `--json`**

In `cli.py:980` (after the `apron_shallow_drops` list, inside the `diagnostics` dict):

```python
            # Additive (#263): nose-out flips applied per returned layout.
            # Backward-compatible — no schema bump.
            "nose_out_flips": list(d.nose_out_flips),
```

- [ ] **Step 7: Run CLI tests to PASS**

Run: `pytest tests/test_cli.py -k nose_out -v`
Expected: PASS.

- [ ] **Step 8: Smoke-test the CLI end to end**

```bash
hangarfit solve tests/fixtures/solve_fresh_alternatives_three.yaml --json 2>/dev/null | python -c "import json,sys; d=json.load(sys.stdin); print('nose_out_flips', d['diagnostics']['nose_out_flips'])"
hangarfit solve tests/fixtures/solve_fresh_alternatives_three.yaml --no-nose-out --json >/dev/null && echo "no-nose-out OK"
```

Expected: prints a `nose_out_flips` list; `--no-nose-out` exits 0.

- [ ] **Step 9: Commit**

```bash
git add src/hangarfit/cli.py tests/
git commit -m "feat(cli): --no-nose-out on solve+view; nose_out_flips in --json (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `tow_pivotable` path-quality + pivot-style-path tests

**Files:**
- Test: a towplanner test file (e.g. `tests/test_towplanner_nose_out.py` or a new `tests/test_towplanner_pivot.py`)

- [ ] **Step 1: Write the tests (RED)**

```python
def test_tow_pivotable_plane_routes_via_pivot_fan():
    # build a layout with a tow_pivotable own-gear plane; plan_path/plan_fill;
    # assert the path uses the r==0 cart fan (turn_radius_m == 0 on the arc),
    # i.e. it delegated to _plan_cart (no own-gear arcs).
    ...

def test_nose_out_flip_plans_shorter_as_pivot_than_arc_loop():
    # open-space 180° nose-out target: route it with the plane tow_pivotable=True
    # vs an equivalent non-pivotable plane; assert the pivot plan is shorter
    # (the measured ~15%). Keep this NON-slow (coverage two-pass, #492).
    ...
```

Reuse the programmatic hangar/plane builders in `tests/test_towplanner_nose_out.py:26-88`.

- [ ] **Step 2: Run to verify fail (or that they capture the new behavior)**

Run: `pytest tests/test_towplanner_pivot.py -v`
Expected: the tests fail until they assert the actual pivot path (or pass immediately if the behavior from Task 1/3 is already in place — in which case ensure the assertion is *distinguishing*, not trivially true).

- [ ] **Step 3: Adjust assertions to the real deterministic values; run to PASS**

Run: `pytest tests/test_towplanner_pivot.py -v`
Expected: PASS. Do **not** add a "6-plane becomes routable" assertion (falsified).

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test(towplanner): tow_pivotable pivot path + nose-out shorter-as-pivot (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Docs — ADR-0022, arc42, CHANGELOG, determinism-guard agent

**Files:**
- Create: `docs/adr/0022-nose-out-parked-heading.md`
- Modify: `docs/adr/README.md`, `docs/architecture/08-crosscutting-concepts.md` (~L526 "Soft preferences"), `CHANGELOG.md` (`[Unreleased] ### Added`), `.claude/agents/determinism-guard.md` (~L40, solver.py mechanisms)

- [ ] **Step 1: Write ADR-0022**

Mirror `docs/adr/0008-inter-plane-spread-soft-preference.md`'s structure (Status/Context/Decision/Consequences/Alternatives/Compliance). Cover: heading convention; the RNG-free `_nose_out` flip post-pass (default ON, runs after `_spread`, independent of `spread`); the strict-more-nose-out gate ≡ nose-out-hemisphere; tri-state `PlaneConstraint.nose_out`; the entry-vs-exit resolution via #480 (cite the 162°→<45° figure + the probe); the `tow_pivotable` sub-decision (orthogonal flag, datum-pivot approximation, towing-vs-powered-taxi, the falsified dense-fill premise); determinism (RNG-free ⇒ byte-identical even ON; canary plan); rejected alternatives (descent fusion — perturbs RNG; planner goal-pose — splits ownership). Status: **Accepted** (design ratified + implemented).

- [ ] **Step 2: Register the ADR**

Add the ADR-0022 row to `docs/adr/README.md` (follow the existing table/list format; check `docs/adr/README.md:101`).

- [ ] **Step 3: arc42 §8**

In `docs/architecture/08-crosscutting-concepts.md`, extend the "Soft preferences" section (~L526) with a nose-out paragraph next to spread; note `tow_pivotable` where the tow motion / parts model is discussed.

- [ ] **Step 4: CHANGELOG**

Under `[Unreleased] ### Added`:

```markdown
- **Nose-out parked heading preference (#263, ADR-0022).** The solver now prefers
  to park each plane pointing **out** (nose toward the door) for an easy
  straight-out exit: an RNG-free `_nose_out` post-pass flips a plane's parked
  heading 180° toward the door when that stays collision-valid (soft — never
  overrides fit or un-parks a plane). Default ON; `--no-nose-out` to disable, or a
  per-plane `constraints.<id>.nose_out: false` for the nose-in exemption (e.g. a
  low-wing under a high-wing tail). Byte-identical determinism is preserved even
  with the feature on (the post-pass draws no RNG). Builds on #480, which makes a
  nose-out slot cheap to back into. Adds `diagnostics.nose_out_flips` (`--json`).
- **`tow_pivotable` aircraft flag (#263, ADR-0022).** Per-plane flag marking a
  free-castering / nose-lift plane that pivots in place when towed
  (`effective_turn_radius_m() → 0`); set for `aviat_husky`, `ctsl`, `fk9_mkii`.
```

- [ ] **Step 5: determinism-guard agent amendment**

In `.claude/agents/determinism-guard.md`, in the `solver.py` mechanisms list (after the `frozenset` bullet, ~L40):

```markdown
- **RNG-free `_nose_out` post-pass.** `_nose_out` flips parked headings toward nose-out after `_spread`. It takes **no `rng`** and must consume **zero** RNG draws — verify it iterates `movable = sorted(...)` (never a raw set/dict), applies only the deterministic `(h+180)%360` flip + `_score`-gated accept, and contains no `rng.*`/`random.`/`secrets.` call. A draw added here shifts the seeded stream for every later restart (a determinism break). `tow_pivotable` (models.py) is a static-data radius override with no determinism impact (same input → same output).
```

- [ ] **Step 6: Verify docs build / links**

Run: `git diff --stat` and eyeball the ADR renders (no broken `[[ ]]`/relative links).

- [ ] **Step 7: Commit**

```bash
git add docs/ CHANGELOG.md .claude/agents/determinism-guard.md
git commit -m "docs(adr): ADR-0022 nose-out parked heading + tow_pivotable; arc42, CHANGELOG, guard (#263)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Full verification + lint/type + acceptance

**Files:** none (verification only)

- [ ] **Step 1: Lint + format**

Run:
```bash
ruff check src/ tests/ && ruff format --check src/ tests/
```
Expected: clean (run `ruff check --fix` / `ruff format` if not).

- [ ] **Step 2: Type check**

Run: `mypy src/hangarfit/`
Expected: clean. (mypy is the source of truth — ignore pyright `apron_depth_m`/stale-worktree false positives noted in the handoff.)

- [ ] **Step 3: Full test suite, mirroring CI's two-pass**

Run:
```bash
pytest -n auto -m "not slow and not serial" -q
pytest -m "serial and not slow" -q
pytest -m slow -q
```
Expected: all PASS. (The `serial` canaries must run outside the xdist pool — see CLAUDE.md.)

- [ ] **Step 4: Determinism guard double-solve (manual sanity)**

Run a fixed-seed nose_out-on solve twice and diff layouts:
```bash
for i in 1 2; do hangarfit solve tests/fixtures/solve_fresh_alternatives_three.yaml --json --seed 7 2>/dev/null | python -c "import json,sys;d=json.load(sys.stdin);print([ (p['plane_id'],round(p['x_m'],4),round(p['y_m'],4),round(p['heading_deg'],4)) for L in d['layouts'] for p in L['placements']])"; done
```
Expected: the two lines are identical.

- [ ] **Step 5: Acceptance — nose-out renders + still routes**

Run:
```bash
hangarfit solve tests/fixtures/solve_fresh_alternatives_three.yaml --render /tmp/nose_out.png --render-paths --seed 1
```
Expected: exit 0; at least one plane parked within 90° of nose-out; tow paths still drawn (no new un-routable). Eyeball the PNG.

- [ ] **Step 6: Bench gate (perf + determinism + validity)**

Run: `python -m bench.profile_pipeline 2>&1 | tail -25`
Expected: every fast regime `valid`/`paths`/`det` = ok; timings sane (nose-out adds O(planes) collision checks per basin — small).

- [ ] **Step 7: Final commit if anything changed**

```bash
git add -A && git commit -m "chore: lint/type/format pass for #263" --allow-empty
```

---

## Self-review notes (author)

- **Spec coverage:** `_nose_out` (T5) · default ON + `--no-nose-out` (T8) · tri-state per-plane (T4 model, T7 loader, T5 logic) · `tow_pivotable` (T1-3, T9) · diagnostics (T4, T5, T8) · determinism (T6) · ADR-0022 + docs + guard (T10) · re-baseline checkpoints CP-A/CP-B (T3/T7) · acceptance (T11). All spec sections map to a task.
- **Determinism:** `_nose_out` takes no `rng` (T5); canaries pinned `nose_out=False` + new on/cross-process canaries (T6); guard amended (T10).
- **Type consistency:** `_nose_out(...) -> tuple[dict[str, Placement], int]` (T5) consumed as `placements, n_flips = _nose_out(...)` (T5 call site); `_SpreadCandidate.nose_out_flips: int` (T5) → `SolverDiagnostics.nose_out_flips: tuple[int, ...]` (T4) → `SolveResult` guard (T4) → `--json list(...)` (T8). `SearchConfig.nose_out: bool` / `PlaneConstraint.nose_out: bool | None` used consistently.
- **Risks:** CP-A (tow path re-baseline) and CP-B (heading re-baseline) are real and measured before mass edits; both pause-and-surface if large.
