# fk9↔cessna Parallel-Park Macro Implementation Plan

> ⛔ **OUTCOME: REFUTED (2026-06-26).** Tasks 1–3 were executed; the Task-3 PoC GATE returned **NO-GO** —
> the macro does not route the pair at the deployed grid (≤32 k expansions) and adds little even at the
> witness's fine grid. Tasks 4–7 were **not** run; the implementation was **discarded** and the team
> pivoted to **#840 learned/guided motion**. Root cause + gate numbers:
> [`docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md`](../../spikes/herrenteich-fk9-cessna-lateral-shuffle.md).
> Kept as design provenance.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Hybrid-A* tow planner an opt-in, default-OFF analytic lateral "parallel-park" macro-action so it can route the fk9_mkii↔cessna_140 front-door corridor on own gear at the deployed 0.5 m/15° grid — the proven-hard search-efficiency linchpin of the Herrenteich all-8 (#844).

**Architecture:** The macro is realized by reusing the planner's existing `plan_reeds_shepp` solver: a macro successor is the optimal Reeds–Shepp word connecting the current pose to a *laterally-shifted waypoint* (same heading, perpendicular offset Δ). It is injected as a small fixed set of **long-range edges** into the existing grid graph — the node set, the `_cell()` discretization, and all tie-breaking are untouched; only additional, deterministically-ordered successor edges are added. A `park_macro: bool` flag gates it; OFF enumerates zero macro edges, so the planner is byte-identical to today. Each macro path is validated by the production `path_first_conflict` oracle (no surrogate).

**Tech Stack:** Python 3.12, `src/hangarfit/towplanner.py` (Hybrid-A* + Reeds–Shepp), `shapely` (collision oracle), `pytest`.

## Global Constraints

- **Determinism (ADR-0003):** same scenario + same seed → **bit-identical** `MovesPlan`. Macro members enumerated in fixed order; equal-`f` ties broken by the existing monotonic counter; `_cell()` keying unchanged. Reviewed by the `determinism-guard` subagent (mandatory for any `towplanner.py` change).
- **Default-neutral lever:** flag OFF ⇒ zero macro edges ⇒ byte-identical to the pre-change planner across every existing fixture/canary. Matches the project convention (apron depth-0, `--workers`≡serial, `--spatial-tokens`).
- **Validity oracle:** every macro path validated by `path_first_conflict` / `collisions.check` — the production checker, no surrogate (the #694 contract).
- **Python 3.12 only.** No `--no-verify`, no force-push, no auto-merge. GitFlow: this branch is `feature/844-parallel-park-macro` off `develop`; deliver via a draft PR with `Refs #844` (it does not fully close #844 — husky ordering remains).
- **Scope:** the isolated fk9↔cessna pair only. Husky front-cluster ordering and #840 learned motion are out of scope.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/hangarfit/towplanner.py` | macro geometry (`_lateral_waypoint`, `_lateral_park_words`), macro injection in the expansion loop, `park_macro` param on `plan_path`/`plan_fill` | Modify |
| `src/hangarfit/cli.py` | `--park-macro` flag on `solve` and `view`, threaded to the planner | Modify |
| `tests/test_towplanner_park_macro.py` | macro-geometry unit tests, ON/OFF behavior, ON-determinism, the isolated-pair routing gate (`@slow`) | Create |
| `tests/fixtures/fk9_cessna_pair.yaml` | the isolated two-plane front-door scenario (fk9 + cessna only) | Create |
| `CHANGELOG.md` | `[Unreleased]` entry for the user-facing `--park-macro` flag | Modify |
| `docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md` | cross-reference the shipped macro + the recorded gate result | Modify |
| `CLAUDE.md` | one-line `--park-macro` note in "Useful commands" | Modify |

**Phasing / the de-risk GATE:** Tasks 1–3 are the cheap-to-prove core (does the macro thread the corridor?). **Task 3 ends in a GO/NO-GO gate.** Tasks 4–6 are the expensive hardening (CLI, full byte-identity matrix, docs) and run **only if the gate passes**. If it fails, only Tasks 1–3 are spent and the spec's adaptive-grid fallback is revisited.

---

### Task 1: Macro geometry — lateral waypoint + Reeds–Shepp park words

**Files:**
- Modify: `src/hangarfit/towplanner.py` (add `_lateral_waypoint` and `_lateral_park_words` near `_primitives`, ~`:1920`)
- Test: `tests/test_towplanner_park_macro.py` (create)

**Interfaces:**
- Consumes (existing — **read the real signatures before coding**): `Pose` (has `x_m`, `y_m`, `heading_deg`), `Segment`, `plan_reeds_shepp(start_pose: Pose, goal: Pose, *, turn_radius_m: float, lateral: bool = False)` returning an object with a `.segments: list[Segment]` (or `None` if unreachable), and `_rs_word_reaches(...)` / `pose_at(...)` for re-integration. Verify these names in `towplanner.py`; adjust the calls if they differ.
- Produces: `_lateral_waypoint(pose: Pose, delta_m: float, to_left: bool) -> Pose` and `_lateral_park_words(pose: Pose, turn_radius_m: float) -> list[tuple[Pose, list[Segment]]]` (fixed enumeration order).

- [ ] **Step 1: Write the failing test — lateral waypoint geometry**

```python
# tests/test_towplanner_park_macro.py
import math
import pytest
from hangarfit.towplanner import _lateral_waypoint
from hangarfit.towplanner import Pose  # adjust import to the real Pose location


def test_lateral_waypoint_left_at_zero_heading():
    # Heading 0° points +x; the left normal is +y.
    p = Pose(x_m=10.0, y_m=5.0, heading_deg=0.0)
    wp = _lateral_waypoint(p, delta_m=0.5, to_left=True)
    assert wp.heading_deg == pytest.approx(0.0)
    assert wp.x_m == pytest.approx(10.0, abs=1e-9)
    assert wp.y_m == pytest.approx(5.5, abs=1e-9)


def test_lateral_waypoint_right_at_ninety_heading():
    # Heading 90° points +y; the right normal is +x.
    p = Pose(x_m=0.0, y_m=0.0, heading_deg=90.0)
    wp = _lateral_waypoint(p, delta_m=1.0, to_left=False)
    assert wp.heading_deg == pytest.approx(90.0)
    assert wp.x_m == pytest.approx(1.0, abs=1e-9)
    assert wp.y_m == pytest.approx(0.0, abs=1e-9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_towplanner_park_macro.py -k lateral_waypoint -v`
Expected: FAIL with `ImportError`/`AttributeError` (`_lateral_waypoint` not defined).

- [ ] **Step 3: Implement `_lateral_waypoint`**

```python
# in towplanner.py, near _primitives
def _lateral_waypoint(pose: Pose, delta_m: float, to_left: bool) -> Pose:
    """Pose shifted perpendicular to its heading by delta_m, heading unchanged.

    The left unit normal of a heading theta is (-sin theta, cos theta);
    `to_left=False` shifts to the right by negating it.
    """
    theta = math.radians(pose.heading_deg)
    sign = 1.0 if to_left else -1.0
    nx = -math.sin(theta) * sign
    ny = math.cos(theta) * sign
    return Pose(
        x_m=pose.x_m + delta_m * nx,
        y_m=pose.y_m + delta_m * ny,
        heading_deg=pose.heading_deg,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_towplanner_park_macro.py -k lateral_waypoint -v`
Expected: PASS (both).

- [ ] **Step 5: Write the failing test — park words reach their waypoint and are ordered**

```python
def test_lateral_park_words_reach_waypoints_deterministically():
    from hangarfit.towplanner import _lateral_park_words, pose_at  # adjust pose_at import
    p = Pose(x_m=10.0, y_m=10.0, heading_deg=0.0)
    r = 6.0  # representative own-gear turn radius (metres)

    words = _lateral_park_words(p, turn_radius_m=r)
    assert len(words) >= 1

    for waypoint, segments in words:
        # Re-integrating the word from p must land on its waypoint.
        end = pose_at(p, segments)  # integrate the whole segment list
        assert end.x_m == pytest.approx(waypoint.x_m, abs=0.05)
        assert end.y_m == pytest.approx(waypoint.y_m, abs=0.05)
        assert ((end.heading_deg - waypoint.heading_deg + 180.0) % 360.0 - 180.0) == pytest.approx(0.0, abs=1.0)

    # Determinism: same inputs → identical waypoint order and segment lists.
    again = _lateral_park_words(p, turn_radius_m=r)
    assert [w.x_m for w, _ in words] == [w.x_m for w, _ in again]
    assert [w.y_m for w, _ in words] == [w.y_m for w, _ in again]
    assert [[(s.kind, s.length_m) for s in segs] for _, segs in words] \
        == [[(s.kind, s.length_m) for s in segs] for _, segs in again]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_towplanner_park_macro.py -k park_words -v`
Expected: FAIL (`_lateral_park_words` not defined).

- [ ] **Step 7: Implement `_lateral_park_words`**

```python
# Fixed family: (to_left, delta_m) enumerated in a fixed order for determinism.
# Granularity is the spec's Phase-1 knob; start with these and tune in Task 3.
_PARK_MACRO_DELTAS_M: tuple[float, ...] = (0.5, 1.0)


def _lateral_park_words(
    pose: Pose, turn_radius_m: float
) -> list[tuple[Pose, list[Segment]]]:
    """Optimal Reeds-Shepp words to small lateral waypoints (the park macro).

    Returns (waypoint, segments) pairs in a FIXED order. A car-like vehicle
    cannot strafe, so the optimal RS word to a same-heading lateral offset is a
    tight cusped wiggle; small delta keeps the swept envelope compact. Skips any
    (direction, delta) the RS solver cannot connect.
    """
    out: list[tuple[Pose, list[Segment]]] = []
    for to_left in (True, False):  # fixed order: left before right
        for delta_m in _PARK_MACRO_DELTAS_M:
            wp = _lateral_waypoint(pose, delta_m, to_left)
            word = plan_reeds_shepp(pose, wp, turn_radius_m=turn_radius_m)
            if word is None:
                continue
            out.append((wp, list(word.segments)))
    return out
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_towplanner_park_macro.py -k park_words -v`
Expected: PASS. If the reach assertion fails, the RS-word `.segments`/`pose_at` integration names are wrong — fix to the real API, do not loosen the tolerance.

- [ ] **Step 9: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_park_macro.py
git commit -m "feat(844): lateral-park macro geometry (RS word to a lateral waypoint)

Refs #844

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Inject the macro into the search behind a default-OFF flag

**Files:**
- Modify: `src/hangarfit/towplanner.py` — `plan_path` signature (~`:2418`) gains `park_macro: bool = False`; the node-expansion loop (~`:2690`) appends macro successors when `park_macro` is True
- Test: `tests/test_towplanner_park_macro.py`

**Interfaces:**
- Consumes: `_lateral_park_words` (Task 1); existing `path_first_conflict(candidate, ...)`, the per-segment cost helper `_seg_cost`, `CUSP_PENALTY`, `_cell`, and the heap-push site — **read the loop to mirror exactly how a micro-primitive successor is costed, cusp-penalized, oracle-checked, deduped via `best_g[_cell(...)]`, and pushed with the monotonic counter.**
- Produces: `plan_path(..., park_macro: bool = False)`; macro successors added *after* the fixed micro-primitive fan when `park_macro` is True.

- [ ] **Step 1: Write the failing test — OFF is identical, ON adds successors**

```python
def test_park_macro_off_matches_baseline_and_on_adds_successors(monkeypatch):
    """A pose where micro-primitives and macros both apply: ON expands strictly
    more children than OFF, and OFF reproduces the baseline successor set."""
    from hangarfit.towplanner import _expand_node, Pose  # adjust to the real expansion entry point
    # Use whatever helper enumerates a node's valid successors; if expansion is
    # inlined in plan_path, factor a small _macro_successors(pose, r) helper in
    # Step 3 and test THAT directly instead.
    p = Pose(x_m=10.0, y_m=10.0, heading_deg=0.0)
    r = 6.0
    off = _macro_successors(p, r, park_macro=False)
    on = _macro_successors(p, r, park_macro=True)
    assert off == []                      # OFF adds nothing
    assert len(on) >= 1                   # ON adds macro edges
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_towplanner_park_macro.py -k park_macro_off -v`
Expected: FAIL (`_macro_successors` not defined).

- [ ] **Step 3: Implement the macro-successor helper and wire it in**

```python
def _macro_successors(
    pose: Pose, turn_radius_m: float, *, park_macro: bool
) -> list[tuple[Pose, list[Segment]]]:
    """Macro edges for a node: empty unless park_macro is enabled (default-neutral)."""
    if not park_macro:
        return []
    return _lateral_park_words(pose, turn_radius_m)
```

Then in the expansion loop, after the existing micro-primitive iteration, add (mirroring the existing successor handling exactly — cost, cusp penalty, oracle check, `best_g`/`_cell` dedupe, counter, heap push):

```python
for waypoint, segments in _macro_successors(node.pose, r, park_macro=park_macro):
    g_inc = sum(_seg_cost(s, r) for s in segments)  # mirror the real _seg_cost arity
    g_inc += _cusp_penalty_for(segments)            # mirror how micro-steps count cusps
    # ... identical path_first_conflict(...) swept-collision check on `segments` ...
    # ... identical best_g[_cell(waypoint)] dedupe, counter increment, heappush ...
```

Thread `park_macro` from `plan_path`'s signature into this loop. **OFF must add zero edges** (the early return guarantees byte-identity).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_towplanner_park_macro.py -k park_macro_off -v`
Expected: PASS.

- [ ] **Step 5: Add a focused OFF byte-identity smoke**

```python
def test_park_macro_off_byte_identical_on_a_routable_fixture():
    """plan_path(park_macro=False) reproduces the default plan on a known fixture."""
    from hangarfit.towplanner import plan_path
    # Load a small fixture that already routes (e.g. scenario_minimal-derived single mover).
    base = plan_path(... , park_macro=False)
    again = plan_path(...)  # default arg
    assert base == again  # same MovesPlan / segments / order
```

- [ ] **Step 6: Run the focused smoke + the full towplanner suite**

Run: `pytest tests/test_towplanner_park_macro.py tests/ -k towplanner -v`
Expected: PASS, no existing towplanner test perturbed.

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_park_macro.py
git commit -m "feat(844): inject park macro as default-OFF long-range search edges

Refs #844

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Isolated fk9↔cessna PoC — the GO/NO-GO gate

**Files:**
- Create: `tests/fixtures/fk9_cessna_pair.yaml` (the two front-door planes only, on the real Herrenteich hangar)
- Test: `tests/test_towplanner_park_macro.py` (add the `@slow` routing gate + the cheap expansion-profile assertion)

**Interfaces:**
- Consumes: `plan_path(..., park_macro=True, max_expansions=...)`, the loader for a layout/scenario fixture, `effective_turn_radius_m()`.
- Produces: the recorded gate verdict (routed? / expansions / per-plane budget) and, on PASS, a committed `@slow` regression test.

- [ ] **Step 1: Build the isolated-pair fixture**

Create `tests/fixtures/fk9_cessna_pair.yaml`: the real Herrenteich hangar with **only** `fk9_mkii` and `cessna_140` at their `examples/herrenteich/layout.yaml` parked poses, own gear (`on_carts: false`). Derive the exact poses from `examples/herrenteich/layout.yaml`. This is the scenario the witness probe proved routable on own gear at 0.25 m/10°.

- [ ] **Step 2: Run the macro-enabled search interactively to find the budget (MEASUREMENT, not yet a test)**

Run a one-off measurement (REPL or a scratch script in the session scratchpad — do **not** commit a scratch script):

```python
from hangarfit.towplanner import plan_path
# load fk9_cessna_pair.yaml; route cessna against the parked fk9 (the harder direction),
# at the DEPLOYED 0.5 m/15° grid, with the macro ON.
plan = plan_path(..., park_macro=True, max_expansions=8000)
print("routed:", plan is not None, "expansions:", plan.stats.expansions if plan else "n/a")
```

Record `routed?` and the expansion count. If it does not route at 8000, retry at a *modestly* raised budget (e.g. 8000 → 16000 → 32000) and record the smallest budget that routes. Also bucket the run's expanded nodes by clearance-to-nearest-obstacle (instrument `plan_path` with an optional debug hook) and record the open-space vs in-corridor split — the cheap profile that confirms the macro spends its budget in the corridor.

- [ ] **Step 3: THE GATE — evaluate go/no-go**

- **GO** if the isolated pair routes own-gear at 0.5 m/15° within a defensible budget (target ≤ ~32 k per-plane; the current default is 8000). Proceed to Step 4, then Tasks 4–6.
- **NO-GO** if it does not route within a sane budget, or the profile shows the macro is not threading the corridor. **Stop.** Record the negative in the spike doc and revisit the spec's adaptive-grid fallback (§10). Tasks 4–6 are NOT done.

Write the verdict + numbers into `docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md` either way.

- [ ] **Step 4 (GO only): Encode the gate as a `@slow` regression test**

```python
import pytest

@pytest.mark.slow
def test_fk9_cessna_pair_routes_with_park_macro():
    """The proven-hard isolated pair routes own-gear at the deployed grid once
    the park macro is enabled (the #844 linchpin)."""
    from hangarfit.towplanner import plan_path
    plan = plan_path(... , park_macro=True, max_expansions=<budget-from-step-2>)
    assert plan is not None
    # And it must NOT route with the macro OFF (proves the macro is what fixed it):
    assert plan_path(... , park_macro=False, max_expansions=<budget-from-step-2>) is None
```

- [ ] **Step 5: Run the gate test**

Run: `pytest tests/test_towplanner_park_macro.py -k fk9_cessna_pair_routes -m slow -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/fk9_cessna_pair.yaml tests/test_towplanner_park_macro.py docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md
git commit -m "test(844): isolated fk9<->cessna pair routes with park macro (gate PASS)

Refs #844

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

> **GATE CHECKPOINT — Tasks 4–6 run only if Task 3 returned GO.**

---

### Task 4: CLI surface — `--park-macro` on `solve` and `view`, `plan_fill` plumbing

**Files:**
- Modify: `src/hangarfit/towplanner.py` — `plan_fill(...)` (~`:1524`) gains `park_macro: bool = False`, forwarded to every `plan_path` call
- Modify: `src/hangarfit/cli.py` — add `--park-macro` to the `solve` and `view` subparsers, default `False`, threaded to `plan_fill`/`plan_path`
- Test: `tests/test_towplanner_park_macro.py` (plan_fill forwarding) + a CLI smoke

**Interfaces:**
- Consumes: `plan_path(..., park_macro=...)` (Task 2).
- Produces: `plan_fill(..., park_macro: bool = False)`; CLI `--park-macro`.

- [ ] **Step 1: Write the failing test — plan_fill forwards the flag**

```python
def test_plan_fill_forwards_park_macro(monkeypatch):
    import hangarfit.towplanner as tp
    seen = []
    real = tp.plan_path
    def spy(*args, **kwargs):
        seen.append(kwargs.get("park_macro"))
        return real(*args, **kwargs)
    monkeypatch.setattr(tp, "plan_path", spy)
    tp.plan_fill(... , park_macro=True)   # minimal routable fixture
    assert any(v is True for v in seen)   # at least one plan_path saw park_macro=True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_towplanner_park_macro.py -k plan_fill_forwards -v`
Expected: FAIL (`plan_fill` has no `park_macro` kwarg).

- [ ] **Step 3: Add `park_macro` to `plan_fill` and forward to every `plan_path` call**

Add `park_macro: bool = False` to `plan_fill`'s signature and pass `park_macro=park_macro` at each internal `plan_path(...)` call site.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_towplanner_park_macro.py -k plan_fill_forwards -v`
Expected: PASS.

- [ ] **Step 5: Add `--park-macro` to the CLI and a smoke test**

In `cli.py`, add to the `solve` and `view` subparsers:

```python
sub.add_argument(
    "--park-macro",
    action="store_true",
    help="enable the analytic lateral parallel-park macro (default off; #844)",
)
```

Thread `park_macro=args.park_macro` into the `plan_fill`/`plan_path` call. Add a CLI smoke that asserts the flag parses and reaches the planner (mirror an existing `solve` CLI test).

- [ ] **Step 6: Run the CLI smoke + lint/type**

Run: `pytest tests/test_towplanner_park_macro.py -k "plan_fill_forwards or cli" -v && ruff check src/ && mypy src/hangarfit/`
Expected: PASS / clean.

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/towplanner.py src/hangarfit/cli.py tests/test_towplanner_park_macro.py
git commit -m "feat(844): --park-macro CLI flag + plan_fill plumbing

Refs #844

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Determinism — full OFF byte-identity matrix + ON reproducibility

**Files:**
- Test: `tests/test_towplanner_park_macro.py`

**Interfaces:**
- Consumes: `plan_path`/`plan_fill` with the flag; existing canary fixtures.

- [ ] **Step 1: Write the failing test — ON is reproducible (same seed → identical plan)**

```python
def test_park_macro_on_is_deterministic():
    from hangarfit.towplanner import plan_fill
    a = plan_fill(... , park_macro=True, seed=12345)
    b = plan_fill(... , park_macro=True, seed=12345)
    assert a == b  # byte-identical MovesPlan
```

- [ ] **Step 2: Write the OFF byte-identity test across representative canary fixtures**

```python
import pytest

@pytest.mark.parametrize("fixture", [
    "tests/fixtures/scenario_minimal.yaml",
    "tests/fixtures/valid_left_side_nesting.yaml",
    # add the fixtures the existing determinism canaries use
])
def test_park_macro_off_byte_identical(fixture):
    from hangarfit.towplanner import plan_fill
    assert plan_fill(load(fixture), park_macro=False, seed=7) \
        == plan_fill(load(fixture), seed=7)  # default park_macro
```

- [ ] **Step 3: Run both, verify they pass**

Run: `pytest tests/test_towplanner_park_macro.py -k "deterministic or byte_identical" -v`
Expected: PASS. A failure in the OFF test means a macro edge leaked into the OFF path — fix the gate, do not adjust the test.

- [ ] **Step 4: Run the determinism-guard's own double-solve locally**

Run: `pytest tests/test_solver_canaries.py tests/test_towplanner_*.py -v`
Expected: PASS — no existing canary perturbed.

- [ ] **Step 5: Commit**

```bash
git add tests/test_towplanner_park_macro.py
git commit -m "test(844): park-macro OFF byte-identity + ON determinism

Refs #844

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Docs — CHANGELOG, command reference, spike cross-ref, promotion criterion

**Files:**
- Modify: `CHANGELOG.md`, `CLAUDE.md`, `docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md`

- [ ] **Step 1: CHANGELOG `[Unreleased]` entry**

Under `### Added`:

```markdown
- `solve`/`view` `--park-macro` flag: opt-in analytic lateral parallel-park
  macro-action that routes the fk9↔cessna front-door corridor on own gear
  (#844). Default off; byte-identical to prior behaviour when unset.
```

- [ ] **Step 2: CLAUDE.md "Useful commands" one-liner**

Add next to the `solve --render-paths` example:

```bash
# #844 analytic parallel-park macro (opt-in; default off = byte-identical). Routes
# the fk9<->cessna front-door corridor on own gear at the deployed 0.5 m/15° grid.
hangarfit solve examples/herrenteich/layout.yaml --render-paths --park-macro
```

- [ ] **Step 3: Spike doc — record the shipped fix + the written promotion-to-default criterion**

In `docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md`, add a "Resolution" note: the macro ships behind `--park-macro` (gate result + budget from Task 3), and the promotion-to-default criterion (spec §8): flip default-ON in a separate PR once macro-ON is byte-stable, non-regressive (routes ≥ OFF, no fixture regresses), and within wall-clock across the full fixture suite — re-baselining canaries in that PR. Note the husky-ordering blocker remains for the full all-8.

- [ ] **Step 4: Run the full fast suite + lint/type once more**

Run: `make test-fast && ruff check src/ tests/ && mypy src/hangarfit/`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md
git commit -m "docs(844): document --park-macro flag + promotion criterion

Refs #844

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: PR + review arc

- [ ] **Step 1:** Push `feature/844-parallel-park-macro`; open a **draft** PR, base `develop`, body `Refs #844` (not `Closes` — husky ordering remains), summarizing the macro, the gate result, and the determinism story.
- [ ] **Step 2:** Run the review arc per the project workflow: `pr-review-toolkit:code-reviewer` (mandatory) **plus** `determinism-guard` (mandatory for `towplanner.py`). One inline review thread per finding; fix + resolve each.
- [ ] **Step 3:** Re-run the review if changes were non-trivial; when clean, `gh pr ready` and tell the user it is ready for final review. **Do not merge.**

---

## Self-Review

**Spec coverage:** §1 problem → Tasks 1–3. §2 strategy (RS-to-lateral-waypoint) → Task 1. §3 macro family (direction × Δ) → `_PARK_MACRO_DELTAS_M`, Task 1; granularity-as-Phase-1-knob → Task 3 Step 2. §4 seams → Tasks 1–2 (exact `towplanner.py` sites). §5 gating (opt-in, default-OFF) → Task 2 + Task 5. §6 phases → Tasks 1–3 (PoC gate) then 4–6 (hardening), gate marker present. §7 success criteria → Task 3 (routing), Task 5 (OFF identity + ON determinism), oracle validation in Task 2. §8 promotion criterion → Task 6 Step 3. §9 out-of-scope (husky, #840) → Global Constraints + `Refs` not `Closes`. §10 risks (fallback on NO-GO) → Task 3 Step 3. §11 determinism invariants → Global Constraints + Task 5. **No gaps.**

**Placeholder scan:** The only deferred values are `<budget-from-step-2>` and the `...` fixture-load arguments — these are **named outputs of Task 3 Step 2** and concrete fixture paths the implementer fills from the real loader API, not open-ended TBDs. Every code step shows real code; every test shows real assertions.

**Type consistency:** `_lateral_waypoint(pose, delta_m, to_left) -> Pose`, `_lateral_park_words(pose, turn_radius_m) -> list[tuple[Pose, list[Segment]]]`, `_macro_successors(pose, turn_radius_m, *, park_macro) -> list[...]`, `plan_path(..., park_macro=False)`, `plan_fill(..., park_macro=False)`, `--park-macro` — names consistent across Tasks 1, 2, 4, 5. Note flagged for the implementer: verify `Pose`/`Segment`/`plan_reeds_shepp`/`pose_at`/`_seg_cost` real signatures before coding (Task 1 Interfaces) — adjust calls if they differ.
