# #480 Fewest-moves tow routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, supervised — this is determinism-sensitive solver/planner code). Steps use `- [ ]` checkboxes.

**Goal:** Replace the multiplicative reverse-length penalty (`_REVERSE_COST_FACTOR=1.5`) with an additive cusp penalty (`cost = length + CUSP_PENALTY·cusps`) and emit the rear-entry cone for nose-out targets independent of apron, so nose-out slots are *backed in* instead of pirouetted in the back corner.

**Architecture:** Single module — `src/hangarfit/towplanner.py`. The cost objective changes at three sites that must agree: the Hybrid-A\* search g-cost (`_seg_cost` + expansion loop), the analytic Reeds–Shepp word selection (`_rs_solve_normalised`), and the cart planner (`_plan_cart`/`_cart_seg_weight`). The entry grid (`entry_poses`) gains a nose-out-gated rear cone. Determinism contract (ADR-0003) preserved; cross-version byte-identity (incl. #412 depth-0) intentionally re-baselined.

**Tech stack:** Python 3.12, pytest (incl. `serial`/`slow` markers), `determinism-guard`, `bench`.

**Design source:** `docs/superpowers/specs/2026-06-07-480-fewest-moves-tow-routing-design.md`.

**Reality note on sequencing:** this is a cross-cutting cost change. New behavior gets new unit tests (red→green per task). Existing *behavioral* tests that encode the old ×1.5 weighting (RS word choice, specific costs, the apron rear-cone test) and any path-shape assertions will go RED when the model flips — they are deliberately re-baselined together in **Task 8** with inspected, justified new expectations, NOT silently. The double-solve **determinism canaries are self-comparison (two runs, same version)**, so they stay GREEN throughout as long as determinism holds.

---

### Task 0: `_wrap180` heading helper

**Files:** Modify `src/hangarfit/towplanner.py` (near the heading utilities ~line 240); Test `tests/test_towplanner.py` (or `test_towplanner_reeds_shepp.py`).

- [ ] **Step 1 — failing test**
```python
def test_wrap180_folds_to_half_open_interval():
    from hangarfit.towplanner import _wrap180
    assert _wrap180(0.0) == 0.0
    assert _wrap180(180.0) == 180.0
    assert _wrap180(181.0) == -179.0
    assert _wrap180(-180.0) == 180.0       # canonical: (-180,180]
    assert _wrap180(360.0) == 0.0
    assert _wrap180(540.0) == 180.0
```
- [ ] **Step 2 — run, expect ImportError/fail:** `pytest tests/test_towplanner_reeds_shepp.py -k wrap180 -q`
- [ ] **Step 3 — implement** (beside the other heading helpers):
```python
def _wrap180(deg: float) -> float:
    """Fold an angle (degrees) into the half-open interval ``(-180, 180]``."""
    return (deg + 180.0) % 360.0 - 180.0
```
Note `(-180,180]`: `(-180+180)%360-180 = -180`? → `0%360-180=-180`; adjust: use `180.0 - (180.0 - deg) % 360.0` form OR accept the standard `((deg + 180) % 360) - 180` which yields `[-180,180)` and special-case. **Verify the exact boundary against the test**; pick the formula that passes (`180.0` maps to `180.0`, `-180.0` maps to `180.0`). The robust form:
```python
def _wrap180(deg: float) -> float:
    w = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if w == -180.0 else w
```
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(towplanner): add _wrap180 heading helper (#480)`

---

### Task 1: nose-out-gated rear-entry cone in `entry_poses`

**Files:** Modify `src/hangarfit/towplanner.py:906-995` (`entry_poses`) + add `_REAR_CONE_HALF_ANGLE_DEG` near `_REVERSE_CONE_HEADINGS:903`; Test `tests/test_towplanner_apron.py` (+ `tests/test_towplanner.py`).

- [ ] **Step 1 — failing tests** (new): nose-out target ⇒ rear headings present (apron or not); nose-in target ⇒ forward cone only.
```python
def test_entry_poses_rear_cone_for_nose_out_target_no_apron(small_hangar_no_apron):
    target = _placement(heading_deg=180.0)   # nose-out
    headings = {p.heading_deg for p in entry_poses(target, small_hangar_no_apron)}
    assert {150.0, 165.0, 180.0, 195.0, 210.0} <= headings        # rear cone present
    assert {330.0, 345.0, 0.0, 15.0, 30.0} <= headings            # forward cone too

def test_entry_poses_no_rear_cone_for_nose_in_target_no_apron(small_hangar_no_apron):
    target = _placement(heading_deg=0.0)      # nose-in
    headings = {p.heading_deg for p in entry_poses(target, small_hangar_no_apron)}
    assert headings == {330.0, 345.0, 0.0, 15.0, 30.0}            # forward cone ONLY

def test_entry_poses_no_rear_cone_for_nose_in_target_with_apron(small_hangar_apron):
    target = _placement(heading_deg=0.0)
    headings = {p.heading_deg for p in entry_poses(target, small_hangar_apron)}
    assert not ({150.0,165.0,180.0,195.0,210.0} & headings)        # nose-in keeps fwd-only even with apron
```
(Use/define `_placement(heading_deg)` and the hangar fixtures to match existing test conventions — read `tests/test_towplanner_apron.py` for the existing helpers, e.g. how a `Placement`/`Hangar(apron_depth_m=...)` is built.)
- [ ] **Step 2 — run, expect fail** (rear cone currently apron-gated): `pytest tests/test_towplanner_apron.py -k "rear_cone or nose_in" -q`
- [ ] **Step 3 — implement.** Replace the depth branch in `entry_poses`:
```python
    depth = hangar.apron_depth_m
    nose_out = abs(_wrap180(target.heading_deg - 180.0)) <= _REAR_CONE_HALF_ANGLE_DEG
    headings = _CONE_HEADINGS + (_REVERSE_CONE_HEADINGS if nose_out else ())
    y_samples = (-depth / 2.0, -depth) if depth > 0.0 else (0.0,)
```
Add near `_REVERSE_CONE_HEADINGS`:
```python
# Rear cone is emitted iff the target parked heading is nose-out-ish (#480):
# |wrap180(h-180)| <= this. The rear cone's own +/-30 span plus margin -> ~45.
_REAR_CONE_HALF_ANGLE_DEG = 45.0
```
Update the `entry_poses` docstring (Headings section) to state the new nose-out gate (apron no longer gates the rear cone; it gates only the y-samples).
- [ ] **Step 4 — update the existing apron test** `tests/test_towplanner_apron.py:186` (currently asserts the rear cone appears with an apron): give its target a nose-out heading so the assertion still holds under the new gate (and add a sibling asserting a nose-in target with the same apron has NO rear cone). Justify in the commit.
- [ ] **Step 5 — run apron + new tests, expect PASS:** `pytest tests/test_towplanner_apron.py -q`
- [ ] **Step 6 — commit:** `feat(towplanner): emit rear-entry cone for nose-out targets, apron-independent (#480)`

---

### Task 2: `CUSP_PENALTY` constant + `_count_cusps` helper

**Files:** Modify `src/hangarfit/towplanner.py` (replace the `_REVERSE_COST_FACTOR` block ~513-519 region — but keep `_REVERSE_COST_FACTOR` defined until Task 6 to avoid breaking the other two sites mid-sequence); Test `tests/test_towplanner_reeds_shepp.py`.

- [ ] **Step 1 — failing unit tests for cusp counting** over a sequence of `(gear, translates)` legs:
```python
def test_count_cusps_pure_forward_is_zero():
    assert _count_cusps([(1, True), (1, True), (1, True)]) == 0
def test_count_cusps_pure_reverse_is_zero():
    assert _count_cusps([(-1, True), (-1, True)]) == 0
def test_count_cusps_one_reversal():
    assert _count_cusps([(1, True), (-1, True)]) == 1
def test_count_cusps_forward_reverse_forward_is_two():
    assert _count_cusps([(1, True), (-1, True), (1, True)]) == 2
def test_count_cusps_excludes_nontranslating_pivots():
    # cart: pivot(non-translating), reverse straight, pivot -> 0 cusps (single reverse drive)
    assert _count_cusps([(1, False), (-1, True), (1, False)]) == 0
```
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement** `_count_cusps` (counts gear sign-changes between consecutive *translating* legs) + add the constant:
```python
# Additive per-cusp penalty (metres). A "cusp" is a travel-direction reversal
# (forward<->reverse) between consecutive TRANSLATING legs; moves = cusps + 1.
# Replaces the old multiplicative _REVERSE_COST_FACTOR: reverse is no longer
# taxed per-metre; instead each direction change costs a fixed, large-but-finite
# amount, so the planner minimises *moves*. Forward preference survives as the
# enumeration-order tie-break (forward primitives/words enumerated first).
# Pinned by test_cusp_penalty_value; changing it requires an ADR-0010 update.
CUSP_PENALTY = 10.0  # PROVISIONAL — finalise empirically in Task 8 (spec §6)

def _count_cusps(legs: "list[tuple[int, bool]]") -> int:
    """Number of travel-direction reversals among the TRANSLATING legs.

    ``legs`` is ``(gear, translates)`` in travel order. Non-translating legs
    (in-place cart pivots) are skipped — they are free reorientations, not moves.
    """
    cusps = 0
    prev: int | None = None
    for gear, translates in legs:
        if not translates:
            continue
        if prev is not None and gear != prev:
            cusps += 1
        prev = gear
    return cusps
```
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(towplanner): add CUSP_PENALTY + _count_cusps (#480)`

---

### Task 3: cusp-weighted Reeds–Shepp word selection

**Files:** Modify `src/hangarfit/towplanner.py:731-769` (`_rs_solve_normalised`) + `:799-835` (`plan_reeds_shepp`); Test `tests/test_towplanner_reeds_shepp.py`.

- [ ] **Step 1 — failing test:** a goal reachable by a 0-cusp reverse word vs a longer forward word — assert the planner now picks the (possibly longer-in-old-metric) fewer-cusp word; and assert a unit-consistency property (passing `cusp_penalty_normalised=0` reproduces shortest-length selection).
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.** `_rs_solve_normalised(x, y, phi, *, cusp_penalty_normalised: float)`:
```python
            word_cusps = _count_cusps([(e.gear, True) for e in word])  # RS legs all translate
            cost = math.fsum(e.t for e in word) + cusp_penalty_normalised * word_cusps
            if best is None or cost < best[0]:
                best = (cost, word)
```
`plan_reeds_shepp` computes and passes it (delegates carts to `_plan_cart` before this, so `r > 0`):
```python
    word = _rs_solve_normalised(x, y, phi, cusp_penalty_normalised=CUSP_PENALTY / r)
```
- [ ] **Step 4 — update existing RS tests** that referenced the ×1.5 weighting (`test_towplanner_reeds_shepp.py:169` comment + any cost assertion; line 212 docstring) with the new metric. Leave `test_reverse_cost_factor_value` for Task 6.
- [ ] **Step 5 — run, expect PASS:** `pytest tests/test_towplanner_reeds_shepp.py -q` (some path-choice tests may need re-baselining → defer to Task 8 if they encode old behavior; note which.)
- [ ] **Step 6 — commit:** `feat(towplanner): cusp-weighted Reeds–Shepp word selection (#480)`

---

### Task 4: drop the reverse tax from the cart planner

**Files:** Modify `src/hangarfit/towplanner.py:479-485` (`_cart_seg_weight`); Test `tests/test_towplanner*.py`.

- [ ] **Step 1 — failing/locking test:** a cart back-in that is *equal length* to a forward alternative is now chosen by length+tie-break (not suppressed by ×1.5); a strictly-shorter reverse is chosen.
- [ ] **Step 2 — run, expect fail** (old ×1.5 suppresses the equal-length reverse).
- [ ] **Step 3 — implement** — drop the factor:
```python
def _cart_seg_weight(seg: Segment) -> float:
    """Unweighted cart segment cost: a pivot's radians (gear-agnostic) or a
    straight's metres. Reverse is no longer taxed per-metre (#480); both the
    forward and reverse pivot-straight-pivot candidates are single drives
    (0 cusps), so _plan_cart's choice reduces to length with forward as the
    deterministic tie-break (forward enumerated first)."""
    if seg.kind != "S":
        return seg.length_m  # pivot: length_m is radians
    return seg.length_m
```
(`min(forward, reverse)` in `_plan_cart` already keeps forward on a tie.)
- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(towplanner): drop reverse per-metre tax from cart planner (#480)`

---

### Task 5: cusp-aware Hybrid-A\* search g-cost

**Files:** Modify `src/hangarfit/towplanner.py` — `_seg_cost:1450-1470`, `_SearchNode:1486-1501`, start node `:1944`, expansion/child `:2013-2031`; Test `tests/test_towplanner.py` / `test_towplanner_grid_heuristic.py`.

- [ ] **Step 1 — failing test:** drive the search on a nose-out fixture and assert the returned path backs in (reverse legs reaching the slot) rather than a long forward in-hangar turn; assert determinism (two calls identical).
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.**
  - `_seg_cost`: drop the `factor`; return the unweighted cost.
```python
def _seg_cost(seg: Segment, turn_radius_m: float) -> float:
    if seg.kind == "S":
        return seg.length_m
    if turn_radius_m > 0.0:
        return seg.length_m + _TURN_PENALTY * (seg.length_m / turn_radius_m)
    return _TURN_PENALTY * seg.length_m  # cart pivot: length_m is radians
```
  - `_SearchNode`: add `last_drive_gear: int` (0 = none yet).
```python
@dataclass(frozen=True, slots=True)
class _SearchNode:
    pose: Pose
    g: float
    seg: Segment | None
    parent: "_SearchNode | None"
    last_drive_gear: int  # 1/-1 of the last TRANSLATING leg; 0 at the root
```
  - Start node `:1944`: `_SearchNode(start_pose, 0.0, None, None, 0)`.
  - Child construction `:2031` (compute cusp + carry gear). Around the expansion where `seg`/`child_pose`/`child_g` are formed:
```python
            translates = not (r == 0.0 and seg.kind in ("L", "R"))
            cusp = 1 if (translates and node.last_drive_gear != 0 and node.last_drive_gear != seg.gear) else 0
            child_drive_gear = seg.gear if translates else node.last_drive_gear
            child_g = node.g + _seg_cost(seg, r) + (CUSP_PENALTY if cusp else 0.0)
            ...
            (child_g + h, counter, _SearchNode(child_pose, child_g, seg, node, child_drive_gear)),
```
  (Confirm the exact local var names in the loop — `r`, `seg`, `child_pose`, `child_g`, `h` — and adapt.)
- [ ] **Step 4 — run, expect PASS** (incl. determinism: `pytest tests/test_towplanner.py -q`).
- [ ] **Step 5 — note the deliberate approximation** in a comment: the cell key `(x,y,heading)` does NOT include `last_drive_gear`, so the domination check may occasionally prune a higher-g node whose gear would avoid a downstream cusp — an accepted Hybrid-A\* approximation (same spirit as pose-binning), kept to bound the state space / expansions; the dominant nose-out win comes from the rear-entry seed (0-cusp reverse), not mid-search gear switches.
- [ ] **Step 6 — commit:** `feat(towplanner): cusp-aware Hybrid-A* g-cost + last_drive_gear node state (#480)`

---

### Task 6: remove `_REVERSE_COST_FACTOR`; pin `CUSP_PENALTY`

**Files:** Modify `src/hangarfit/towplanner.py` (delete `:519` + docstring refs at `:11`, `:426`, `:480`-ish, `:513-518`, `:736`, `:805`, `:1457-1463`); `tests/test_towplanner_reeds_shepp.py` (replace `test_reverse_cost_factor_value` + import).

- [ ] **Step 1 — grep:** `grep -rn _REVERSE_COST_FACTOR src/ tests/` — every hit must be removed/replaced.
- [ ] **Step 2 — replace the value test:**
```python
def test_cusp_penalty_value() -> None:
    """CUSP_PENALTY is pinned; changing it requires an ADR-0010 update (spec §6)."""
    assert CUSP_PENALTY == 10.0   # or the Task-8-calibrated value
```
- [ ] **Step 3 — delete the constant + scrub docstrings** (replace "reverse legs cost 1.5×…" prose with the cusp model; update the module header docstring at `:11-12`).
- [ ] **Step 4 — run:** `pytest tests/test_towplanner_reeds_shepp.py -q` + `grep` clean.
- [ ] **Step 5 — commit:** `refactor(towplanner): remove _REVERSE_COST_FACTOR, pin CUSP_PENALTY (#480)`

---

### Task 7: swept-turning acceptance metric (the issue's own measure)

**Files:** Test `tests/test_towplanner.py` (or a new `tests/test_towplanner_nose_out.py`).

- [ ] **Step 1 — helper + test:** `Σ|Δheading|` sampled along the path while `y_m > 0`, on a nose-out fixture; assert it is small (backs in) — snapshot the number with a tolerance, and assert it's far below the pre-change value (document the before/after in the test docstring).
```python
def _swept_turning_inside(arc) -> float:
    samples = list(arc.sample(step_m=0.25, step_deg=5.0))
    total = 0.0
    for a, b in zip(samples, samples[1:]):
        if a.y_m > 0.0 and b.y_m > 0.0:
            total += abs(_wrap180(b.heading_deg - a.heading_deg))
    return total
```
- [ ] **Step 2 — run, expect PASS** (this is the real acceptance check — keep it non-`slow` for codecov).
- [ ] **Step 3 — commit:** `test(towplanner): swept-in-hangar-turning acceptance metric for nose-out (#480)`

---

### Task 8: re-baseline, determinism, calibration, bench

**Files:** whatever the full suite flags; `src/hangarfit/towplanner.py` (finalise `CUSP_PENALTY`).

- [ ] **Step 1 — full suite:** `pytest -m "not slow and not serial" -q` then `pytest -m "serial and not slow" -q`. Triage every failure: is the new behavior *better/equal* (less swept turning, fewer moves)? If yes, update the expectation with a one-line justification in the test. If a path got *worse* or a fixture hit `budget_exhausted`, STOP — that's a real regression, investigate (don't paper over by raising the cap).
- [ ] **Step 2 — calibrate `CUSP_PENALTY`:** with the Herrenteich nose-out fixtures (`zlin_savage`, `fk9_mkii`) + the synthetic regression fixture, confirm the chosen value keeps the genuine back-in win and doesn't over-trade length for cusps (spec §6: lower bound `< 14 m`). Update the constant + `test_cusp_penalty_value` if the empirics move it off 10.0; record the justification.
- [ ] **Step 3 — determinism:** run `determinism-guard` (double-solve byte-identity on a fixed seed; the max_restarts-scoped check). Must PASS.
- [ ] **Step 4 — bench:** `python -m bench.profile_pipeline` — validity / path-validity / determinism verdicts green per regime; note any expansion-budget movement.
- [ ] **Step 5 — commit:** `test(towplanner): re-baseline tow goldens for cusp cost model (#480)` with the inspected-diff summary in the body.

---

### Task 9: ADR-0010 amendment + spec close-out

**Files:** Modify `docs/adr/0010-reeds-shepp-motion-model.md`; mark the spec status done.

- [ ] **Step 1 — amend ADR-0010:** add a dated amendment section: the cost model is now `length + CUSP_PENALTY·cusps` (was Σ|leg|×1.5^reverse); "prefer forward" is now an enumeration-order tie-break; the rear cone is nose-out-gated (apron-independent); **this supersedes the #412 depth-0 cross-version byte-identity** (the ADR-0003 *contract* is intact). Replace the "Why `_REVERSE_COST_FACTOR = 1.5`?" section with "Why `CUSP_PENALTY = <value>`?" carrying the §6 justification.
- [ ] **Step 2 — CHANGELOG** entry under Unreleased (Changed): fewest-moves tow routing / nose-out back-in.
- [ ] **Step 3 — commit:** `docs(adr): ADR-0010 amendment — cusp-penalty cost model (#480)`

---

### Final: open the PR
- [ ] Push `feature/480-fewest-moves-tow-routing`; `gh pr create --draft --base develop --body "Closes #480 …"` calling out the determinism re-baseline + the superseded #412 depth-0 byte-identity.
- [ ] Review arc: `code-reviewer` + **`determinism-guard`** (mandated) + `geometry-invariant-guard` only if geometry helpers were touched (they weren't → skip) + `comment-analyzer` (ADR/docstrings).
