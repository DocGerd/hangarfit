# #908 Absolute Door-Distance Soft Term — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an absolute door-attraction soft term so the rank-1 plane in `Scenario.door_order` is actively pulled toward the door (`y=0`) during the spread hill-climb — best-effort, byte-identical by default.

**Architecture:** A new `_door_bias_energy` (sign-flipped mirror of `_back_bias_energy`) folded into `_spread`'s `_energy` closure, gated on a new `SearchConfig.door_bias_weight` (default `0.0` ⇒ inert). The `solve` CLI auto-arms a baked default weight when the scenario sets `door_order` (`--no-door-bias` opts out), mirroring the `--back-fill` lever exactly. To avoid a deep-vs-doorward tug-of-war, the rank-1 id is exempted from the back-fill sum while door-bias is active.

**Tech Stack:** Python 3.12, existing `hangarfit.solver` / `hangarfit.models` / `hangarfit.cli`; pytest.

**Design spec:** `docs/superpowers/specs/2026-07-03-908-door-bias-absolute-term-design.md`

## Global Constraints

- **ADR-0003 determinism:** same scenario + seed ⇒ byte-identical plan. New term is RNG-free, wall-clock-free, **division-only** (no `math.exp` — cross-machine byte-safe). Default `door_bias_weight=0.0` ⇒ term never summed ⇒ every `SearchConfig()`-default caller (canaries, `ml/`, `bench`) bit-identical.
- **`determinism-guard` review required** (touches `solver.py` scoring surface).
- **Mirror the `--back-fill` precedent** (`_BACK_FILL_DEFAULT_WEIGHT=1.0` cli.py:51; `--no-back-fill` argparse cli.py:255; `back_bias_weight: float = 0.0` + `>= 0` validation models.py:1846/1968).
- **Weight = rank1_only** (pull only `door_order[0]`); the shipped relative `_door_order_deviation` tiebreak stays untouched for the tail.
- Run `pytest`, `ruff check src/ tests/`, `ruff format`, `mypy src/hangarfit/` before each commit's completion.

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/hangarfit/solver.py` | `_door_bias_energy` (new); `_back_bias_energy` `exclude` param; `_spread._energy` gate + guard relax | Modify |
| `src/hangarfit/models.py` | `SearchConfig.door_bias_weight` field + `__post_init__` validation | Modify |
| `src/hangarfit/cli.py` | `--no-door-bias` argparse; `_DOOR_BIAS_DEFAULT_WEIGHT`; auto-arm at the `solve` SearchConfig site | Modify |
| `tests/test_solver_door_order.py` | unit tests for the energy term, steering, back-fill exemption, determinism canary | Modify |
| `tests/test_cli_solve.py` (or the existing solve-CLI test module) | `--no-door-bias` + auto-arm CLI tests | Modify |
| `docs/adr/0008-inter-plane-spread-soft-preference.md` | amend to record the door-bias steering term | Modify |
| `CHANGELOG.md` | `[Unreleased]` entry | Modify |

---

### Task 1: `_door_bias_energy` energy term

**Files:**
- Modify: `src/hangarfit/solver.py` (add function next to `_back_bias_energy` ~1343)
- Test: `tests/test_solver_door_order.py`

**Interfaces:**
- Produces: `_door_bias_energy(placements: Mapping[str, Placement], scenario: Scenario) -> float` — returns `y_{door_order[0]} / hangar.length_m` when `door_order` is truthy and `door_order[0]` is placed; else `0.0`. Weight is applied by the caller (mirrors `_back_bias_energy`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_solver_door_order.py` (import `_door_bias_energy` from `hangarfit.solver` in the existing import block):

```python
# --- _door_bias_energy absolute-attraction term (#908) ---------------------

def test_door_bias_energy_unset_is_zero():
    s = _scenario()  # door_order is None
    assert _door_bias_energy({"a": _pl("a", 5.0)}, s) == 0.0  # inert ⇒ byte-identical


def test_door_bias_energy_empty_order_is_zero():
    s = _scenario(door_order=())
    assert _door_bias_energy({"a": _pl("a", 5.0)}, s) == 0.0


def test_door_bias_energy_rank1_absent_is_zero():
    s = _scenario(door_order=("a", "b"))
    assert _door_bias_energy({"b": _pl("b", 5.0)}, s) == 0.0  # rank-1 'a' not placed


def test_door_bias_energy_is_rank1_y_over_length():
    s = _scenario(door_order=("a", "b"))  # only rank-1 'a' matters
    # length_m = 40.0 (see _hangar); a at y=10 ⇒ 10/40 = 0.25, independent of 'b'.
    assert _door_bias_energy({"a": _pl("a", 10.0), "b": _pl("b", 2.0)}, s) == 0.25


def test_door_bias_energy_minimized_at_the_door():
    s = _scenario(door_order=("a",))
    near = _door_bias_energy({"a": _pl("a", 1.0)}, s)
    far = _door_bias_energy({"a": _pl("a", 30.0)}, s)
    assert near < far  # smaller y (nearer the door at y=0) ⇒ lower energy
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_solver_door_order.py -k door_bias_energy -v`
Expected: FAIL — `ImportError: cannot import name '_door_bias_energy'`.

- [ ] **Step 3: Implement `_door_bias_energy`**

Add immediately after `_back_bias_energy` (~line 1343) in `src/hangarfit/solver.py`:

```python
def _door_bias_energy(placements: Mapping[str, Placement], scenario: Scenario) -> float:
    """Absolute door-attraction bias ``D = y_{rank1} / length_m`` (#908).

    The sign-flipped mirror of :func:`_back_bias_energy`: minimized when the
    rank-1 ``door_order`` body sits AT the door (``y = 0``), so the spread
    hill-climb actively pulls the top-priority plane doorward (the absolute
    steering the relative :func:`_door_order_deviation` tiebreak never supplies —
    it is provably inert for a lone ``#1``).

    Only ``door_order[0]`` is steered (rank1_only); ranks 2..N are left to the
    relative selection tiebreak. Returns ``0.0`` when ``door_order`` is falsy
    (``None`` or the loader-permitted empty ``()``) or the rank-1 body is not
    placed (e.g. a maintenance-away plane) — skipped exactly like
    ``_back_bias_energy`` / ``_region_energy`` / ``_door_order_deviation``.
    Division-only (no ``exp``) ⇒ cross-machine byte-safe. RNG-free. The
    ``door_bias_weight`` multiplier is applied at the ``_spread._energy`` call
    site (mirrors ``back_bias_weight * _back_bias_energy``).
    """
    order = scenario.door_order
    if not order:
        return 0.0
    rank1 = order[0]
    if rank1 not in placements:
        return 0.0
    return placements[rank1].y_m / scenario.hangar.length_m
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_solver_door_order.py -k door_bias_energy -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_door_order.py
git commit -m "feat(solver): _door_bias_energy absolute door-attraction term (#908)"
```

---

### Task 2: `SearchConfig.door_bias_weight` field + validation

**Files:**
- Modify: `src/hangarfit/models.py` (`SearchConfig` field near `back_bias_weight:1846`; validation near `back_bias_weight` check :1968)
- Test: `tests/test_models.py` (or the module holding the existing `SearchConfig` validation tests)

**Interfaces:**
- Produces: `SearchConfig.door_bias_weight: float = 0.0` (validated `>= 0`).

- [ ] **Step 1: Write the failing tests**

Add to the module that tests `SearchConfig` validation (mirror the existing `back_bias_weight` tests; find them with `grep -rn back_bias_weight tests/`):

```python
def test_search_config_door_bias_weight_default_is_zero():
    assert SearchConfig().door_bias_weight == 0.0  # inert default ⇒ byte-identical


def test_search_config_negative_door_bias_weight_rejected():
    import pytest
    with pytest.raises(ValueError, match="door_bias_weight must be >= 0"):
        SearchConfig(door_bias_weight=-1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/ -k door_bias_weight -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'door_bias_weight'`.

- [ ] **Step 3: Add the field + validation**

In `src/hangarfit/models.py`, add the field immediately after `back_bias_weight`'s docstring block (~after line 1875), before `spread_stall_restarts`:

```python
    door_bias_weight: float = 0.0
    """Strength of the absolute door-attraction bias folded into the spread
    post-pass (#908, ADR-0008 amendment). ``0.0`` (default) ⇒ no door steering —
    byte-identical to the pre-#908 solver (ADR-0003); the term is not even
    summed. When ``> 0`` AND ``scenario.door_order`` is set, the spread hill-climb
    additionally minimizes ``D = y_{door_order[0]} / hangar.length_m``, pulling
    the rank-1 (door-nearest) plane toward the door at ``y = 0``; the ``<2 planes``
    no-op guard is relaxed so a lone ``#1`` is still pulled. Only the rank-1 body
    is steered (rank1_only) — ranks 2..N keep the relative ``_door_order_deviation``
    selection tiebreak. While active, the rank-1 id is EXEMPTED from the
    back-of-hangar fill bias (:func:`_back_bias_energy`) so its own deep-pull does
    not cancel the doorward pull. Same dimensionless-weight semantics as
    ``back_bias_weight`` (``~1.0`` operating point); ``0.0`` is the exact identity
    of ``weight · D``, so — like ``back_bias_weight`` — it is ``float = 0.0`` not
    ``float | None``. No effect when ``spread=False`` (the bias lives in the spread
    post-pass). The CLI auto-arms it when ``door_order`` is set (``--no-door-bias``
    opts out)."""
```

Add the validation immediately after the `back_bias_weight < 0.0` check (~line 1971):

```python
        if self.door_bias_weight < 0.0:
            raise ValueError(
                f"SearchConfig.door_bias_weight must be >= 0 "
                f"(0 disables the absolute door-attraction bias), got {self.door_bias_weight}"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/ -k door_bias_weight -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/models.py tests/
git commit -m "feat(models): SearchConfig.door_bias_weight (#908)"
```

---

### Task 3: `_back_bias_energy` rank-1 exemption

**Files:**
- Modify: `src/hangarfit/solver.py` (`_back_bias_energy` ~1327)
- Test: `tests/test_solver_door_order.py`

**Interfaces:**
- Produces: `_back_bias_energy(placements, scenario, *, exclude: str | None = None) -> float` — same value as before when `exclude is None` (byte-identical default); skips `exclude` from the sum when given.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_solver_door_order.py`:

```python
def test_back_bias_energy_exclude_skips_one_id():
    from hangarfit.solver import _back_bias_energy
    s = _scenario()  # length_m = 40.0
    pls = {"a": _pl("a", 10.0), "b": _pl("b", 30.0)}
    # default: Σ (40−y)/40 = (30/40) + (10/40) = 1.0
    assert _back_bias_energy(pls, s) == 1.0
    # exclude 'a' ⇒ only 'b': (40−30)/40 = 0.25
    assert _back_bias_energy(pls, s, exclude="a") == 0.25
    # excluding an absent id is a no-op
    assert _back_bias_energy(pls, s, exclude="zzz") == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_solver_door_order.py -k back_bias_energy_exclude -v`
Expected: FAIL — `TypeError: _back_bias_energy() got an unexpected keyword argument 'exclude'`.

- [ ] **Step 3: Add the `exclude` param**

Replace `_back_bias_energy`'s signature and its `return` (src/hangarfit/solver.py ~1327/1342). Keep the existing docstring; append one sentence about `exclude`:

```python
def _back_bias_energy(
    placements: dict[str, Placement], scenario: Scenario, *, exclude: str | None = None
) -> float:
```

Append to the docstring (before the closing `"""`):

```
    ``exclude`` (default ``None``) drops one plane id from the sum — used by the
    #908 door-bias to exempt the rank-1 door-seeker from the deep-pull while it is
    steered toward the door; ``None`` sums every placement (byte-identical to the
    pre-#908 signature, ADR-0003).
```

Change the return to:

```python
    return sum(
        (length - placements[pid].y_m) / length
        for pid in sorted(placements)
        if pid != exclude
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_solver_door_order.py -k back_bias_energy_exclude -v`
Expected: PASS.
Run the back-fill regression net (byte-identity of the default path): `pytest tests/ -k back_bias -v`
Expected: PASS (existing back-fill tests unchanged — `exclude=None` default).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_door_order.py
git commit -m "feat(solver): _back_bias_energy exclude param for #908 rank-1 exemption"
```

---

### Task 4: Wire the term into `_spread` (gate + exemption + guard relax) + steering tests

**Files:**
- Modify: `src/hangarfit/solver.py` (`_spread` gate block ~1566; `_energy` closure ~1587)
- Test: `tests/test_solver_door_order.py`

**Interfaces:**
- Consumes: `_door_bias_energy` (Task 1), `SearchConfig.door_bias_weight` (Task 2), `_back_bias_energy(..., exclude=...)` (Task 3).

- [ ] **Step 1: Write the failing steering tests**

Add to `tests/test_solver_door_order.py` (helper + two tests):

```python
def _rank1_y(res, pid="a"):
    lay = res.layouts[0]
    return next(p.y_m for p in lay.placements if p.plane_id == pid)


def test_door_bias_pulls_rank1_toward_the_door():
    # Roomy 40x40 hangar (see _hangar) ⇒ door-side slack for rank-1 'a'.
    s = _scenario(door_order=("a",))
    off = SearchConfig(max_restarts=4, spread=True)                       # weight 0
    on = SearchConfig(max_restarts=4, spread=True, door_bias_weight=1.0)  # armed
    y_off = _rank1_y(solve(s, search=off, seed=0, budget_s=120.0, plan_paths=False))
    y_on = _rank1_y(solve(s, search=on, seed=0, budget_s=120.0, plan_paths=False))
    assert y_on < y_off  # 'a' parks strictly nearer the door with the term on


def test_door_bias_overcomes_back_fill_for_rank1():
    # With back-fill ON (pulls everyone deep), the rank-1 exemption + door-bias
    # still land 'a' nearer the door than with door-bias off.
    s = _scenario(door_order=("a",))
    off = SearchConfig(max_restarts=4, spread=True, back_bias_weight=1.0)
    on = SearchConfig(max_restarts=4, spread=True, back_bias_weight=1.0, door_bias_weight=1.0)
    y_off = _rank1_y(solve(s, search=off, seed=0, budget_s=120.0, plan_paths=False))
    y_on = _rank1_y(solve(s, search=on, seed=0, budget_s=120.0, plan_paths=False))
    assert y_on < y_off
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_solver_door_order.py -k "door_bias_pulls or overcomes" -v`
Expected: FAIL — `y_on == y_off` (door_bias_weight is accepted by SearchConfig but not yet consumed by `_spread`).

- [ ] **Step 3: Wire the term into `_spread`**

In `src/hangarfit/solver.py`, in `_spread`, extend the gate block (~1566-1573). After `region_active = bool(scenario.region_preferences)` add:

```python
    door_active = search.door_bias_weight > 0.0 and bool(scenario.door_order)
    # rank1_only (#908): exempt the door-seeker from the deep-pull so its own
    # back-bias does not cancel the doorward pull. None ⇒ no exemption.
    door_rank1 = scenario.door_order[0] if door_active else None
```

Extend the early-bail guard so a lone `#1` is still pulled:

```python
    if not movable or (
        len(placements) < 2 and not back_fill and not region_active and not door_active
    ):
        ...
        return placements
```

In the `_energy` closure (~1587-1592), change the back-fill line to pass the exemption and add the door term:

```python
        e = _inter_plane_energy(trial, scenario, scale, gap_cache=gap_cache, moved=moved)
        if back_fill:
            e += search.back_bias_weight * _back_bias_energy(trial, scenario, exclude=door_rank1)
        if region_active:
            e += _region_energy(trial, scenario)
        if door_active:
            e += search.door_bias_weight * _door_bias_energy(trial, scenario)
        return e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_solver_door_order.py -k "door_bias_pulls or overcomes" -v`
Expected: PASS.
Run the whole door-order + spread net: `pytest tests/test_solver_door_order.py tests/test_solver_search.py -v`
Expected: PASS (existing tests use default `door_bias_weight=0` ⇒ unchanged).

*If a steering assertion fails because spread repulsion swamps the pull, raise the test's `door_bias_weight` (e.g. `2.0`) to confirm direction, then set `_DOOR_BIAS_DEFAULT_WEIGHT` (Task 6) accordingly. Do NOT crank it so high that spread collapses — best-effort "if possible" is intended.*

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_door_order.py
git commit -m "feat(solver): steer rank-1 doorward via door_bias_weight in _spread (#908)"
```

---

### Task 5: Determinism canary + default byte-identity

**Files:**
- Test: `tests/test_solver_door_order.py`

- [ ] **Step 1: Write the failing/guard tests**

```python
def test_solve_door_bias_active_deterministic():
    # The steering path (door_bias_weight>0 + door_order) is double-solve identical —
    # today's determinism canaries never exercise door_order at all.
    s = _scenario(door_order=("a",))
    cfg = SearchConfig(max_restarts=4, spread=True, door_bias_weight=1.0)
    a = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    b = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert _key(a.layouts) == _key(b.layouts)


def test_solve_door_bias_default_is_byte_identical_to_weight_zero():
    # door_order set but weight 0 (the SearchConfig default) ⇒ NO steering ⇒
    # identical to a run that never mentioned door-bias.
    s = _scenario(door_order=("a",))
    default_cfg = SearchConfig(max_restarts=4, spread=True)
    explicit_zero = SearchConfig(max_restarts=4, spread=True, door_bias_weight=0.0)
    a = solve(s, search=default_cfg, seed=0, budget_s=120.0, plan_paths=False)
    b = solve(s, search=explicit_zero, seed=0, budget_s=120.0, plan_paths=False)
    assert _key(a.layouts) == _key(b.layouts)
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_solver_door_order.py -k "door_bias_active_deterministic or byte_identical" -v`
Expected: PASS (no new implementation — these guard Task 4's contract).

- [ ] **Step 3: Commit**

```bash
git add tests/test_solver_door_order.py
git commit -m "test(solver): #908 door-bias determinism canary + byte-identity guard"
```

---

### Task 6: CLI `--no-door-bias` + auto-arm on `door_order`

**Files:**
- Modify: `src/hangarfit/cli.py` (constant near `_BACK_FILL_DEFAULT_WEIGHT`:51; argparse near `--no-back-fill`:255; `solve` SearchConfig construction :823)
- Test: the solve-CLI test module (find with `grep -rln "back-fill\|back_fill\|def test.*solve" tests/test_cli*.py`)

**Interfaces:**
- Consumes: `SearchConfig.door_bias_weight` (Task 2), `scenario` (already loaded at cli.py:789 before the construction site).

- [ ] **Step 1: Write the failing CLI test**

Add to the solve-CLI test module (mirror the existing `--no-back-fill` test). Use the module's established fixture/helpers for invoking `cmd_solve` / the CLI on a scenario file; assert on the built `SearchConfig` if the module exposes it, else on solver behavior. Concrete behavioral form:

```python
def test_solve_auto_arms_door_bias_when_door_order_set(tmp_path, capsys):
    # A scenario WITH door_order: `solve` arms door-bias by default.
    # A scenario WITHOUT door_order, or --no-door-bias: weight stays 0.
    from hangarfit.cli import main
    # ... write a scenario fixture carrying door_order: [a] into tmp_path ...
    # Assert (via the module's chosen observation point) that the built
    # SearchConfig.door_bias_weight == _DOOR_BIAS_DEFAULT_WEIGHT for the
    # door_order scenario, 0.0 for a no-door_order scenario, and 0.0 when
    # --no-door-bias is passed.
```

*Author this against the module's existing pattern for the `--no-back-fill` test (same observation point). If that test asserts on a patched/captured `SearchConfig`, mirror it; if it asserts on rendered output, assert the rank-1 plane lands nearer the door with vs without `--no-door-bias`.*

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli*.py -k door_bias -v`
Expected: FAIL — `--no-door-bias` unrecognized / weight not armed.

- [ ] **Step 3: Add the constant, argparse flag, and auto-arm**

In `src/hangarfit/cli.py`, add near `_BACK_FILL_DEFAULT_WEIGHT` (~line 51):

```python
# #908 absolute door-attraction bias: the SearchConfig.door_bias_weight the CLI
# bakes when the scenario sets door_order (--no-door-bias forces 0.0). Same tuned
# operating point as the back-fill weight; the rank-1 exemption keeps them from
# cancelling.
_DOOR_BIAS_DEFAULT_WEIGHT = 1.0
```

Add the argparse flag next to `--no-back-fill` (~after line 262, on the same `solve` subparser):

```python
    solve.add_argument(
        "--no-door-bias",
        action="store_false",
        dest="door_bias",
        default=True,
        help=(
            "Disable the absolute door-attraction bias (#908). By default, when the "
            "scenario sets door_order, the spread post-pass pulls the top-ranked "
            "(#1) plane toward the door; pass this to keep only the relative "
            "door-order selection tiebreak. (No effect without door_order.)"
        ),
    )
```

At the `solve` SearchConfig construction (~line 823-830) add the argument (after `back_bias_weight=...`):

```python
                door_bias_weight=(
                    _DOOR_BIAS_DEFAULT_WEIGHT
                    if (args.door_bias and scenario.door_order)
                    else 0.0
                ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli*.py -k door_bias -v`
Expected: PASS.
Run the full CLI suite to confirm no existing solve-CLI test broke: `pytest tests/test_cli*.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli*.py
git commit -m "feat(cli): auto-arm door-bias on door_order, --no-door-bias opt-out (#908)"
```

---

### Task 7: ADR-0008 amendment + CHANGELOG

**Files:**
- Modify: `docs/adr/0008-inter-plane-spread-soft-preference.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Amend ADR-0008**

Add a subsection recording the door-bias steering term alongside the `back_bias` (#320) and `region` (#604) amendments: what it is (absolute `y_{rank1}/length_m` doorward pull, rank1_only), how it is gated (`door_bias_weight>0 AND door_order`, default inert ⇒ ADR-0003), the rank-1 back-fill exemption, and that it is division-only (cross-machine safe) and complements — does not replace — the relative `_door_order_deviation` selection tiebreak (#614).

- [ ] **Step 2: Add the CHANGELOG entry**

Under `## [Unreleased]` → `### Added`:

```markdown
- **Absolute door-proximity steering** — when a scenario sets `door_order`, `hangarfit solve` now actively pulls the #1 (door-nearest) plane toward the door during the spread post-pass, not just as a post-hoc tiebreak. Opt out with `--no-door-bias`. Off unless `door_order` is set; the library default (`SearchConfig.door_bias_weight=0.0`) and every plan without `door_order` are byte-identical (ADR-0003). (#908)
```

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0008-inter-plane-spread-soft-preference.md CHANGELOG.md
git commit -m "docs: record #908 door-bias steering term (ADR-0008 + CHANGELOG)"
```

---

## Self-Review

**Spec coverage:** hybrid activation → Task 2 (field) + Task 6 (CLI auto-arm/opt-out); rank1_only energy → Task 1; back-fill exemption → Task 3 + Task 4; `<2 planes` guard relax → Task 4; determinism/byte-identity → Task 5; ADR/CHANGELOG → Task 7. ✓ All spec sections covered.

**Placeholder scan:** Task 6's test body is described against the existing `--no-back-fill` test's observation point rather than shown verbatim, because that module's pattern (patched SearchConfig vs rendered-output assertion) must be matched — the implementer reads the sibling test first. All other steps carry complete code.

**Type consistency:** `_door_bias_energy(placements, scenario) -> float`, `_back_bias_energy(..., *, exclude: str | None = None)`, `door_bias_weight: float`, `_DOOR_BIAS_DEFAULT_WEIGHT: float`, `door_active`/`door_rank1` locals — names consistent across Tasks 1/3/4/6. `door_order[0]` guarded by the truthy check in both `_door_bias_energy` and the `door_active` gate.

**Determinism:** default (`door_bias_weight=0`) never sums the term (Task 4 gate); `exclude=None` default leaves `_back_bias_energy` byte-identical (Task 3); canary added (Task 5). `determinism-guard` review is a merge gate.
