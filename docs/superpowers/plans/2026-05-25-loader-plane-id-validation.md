# Loader plane-id validation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject unknown/mis-cased plane ids at the loader boundary with a `did you mean 'X'?` suggestion, keeping ids case-sensitive.

**Architecture:** Two private helpers in `loader.py` — `_suggest_plane_id` (casefold-exact match first, then `difflib`) and `_resolve_known_plane_id` (the reject-with-hint gate) — wired into five plane-id reference sites across `load_layout` and `load_scenario`. The `Layout`/`Scenario` model invariants are kept untouched as the programmatic backstop.

**Tech Stack:** Python 3.12, stdlib `difflib`, pytest. Spec: `docs/superpowers/specs/2026-05-25-loader-plane-id-validation-design.md`.

**Worktree note:** This repo's editable install (`.pth`) points at a single worktree, so in any other worktree bare `pytest` imports the wrong source. **Run every test command as `PYTHONPATH=src python3 -m pytest …`** (note: `python3`, not `python`). See `feedback_editable_install_cross_worktree`.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/hangarfit/loader.py` | YAML→model adapter; error-message quality | add 2 helpers + `import difflib`/`collections.abc`; wire 5 call sites; replace the `:315` maintenance check; module-docstring note |
| `tests/test_loader.py` | loader failure-path tests | new `TestPlaneIdSuggestion` (helper units) + `TestUnknownPlaneId` (layout integration); update `test_unknown_plane_reference_propagates` |
| `tests/test_loader_scenario.py` | scenario loader tests | new scenario integration cases; update `test_load_scenario_rejects_maintenance_plane_not_in_fleet_in` |
| `docs/architecture/05-building-block-view.md` | arc42 module map | one-line case-sensitivity note |

---

## Task 1: `_suggest_plane_id` helper

**Files:**
- Modify: `src/hangarfit/loader.py` (imports near `:20`; new function after `_extract_maintenance_plane`, ~`:438`)
- Test: `tests/test_loader.py` (new class, append at end of file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loader.py`. Add `_suggest_plane_id` to the existing `from hangarfit.loader import (…)` block first.

```python
class TestPlaneIdSuggestion:
    """Unit tests for the _suggest_plane_id near-match helper."""

    def test_casefold_match_suggests_canonical_with_note(self) -> None:
        assert _suggest_plane_id("Foo", ["foo"]) == (
            "; did you mean 'foo'? (plane ids are case-sensitive)"
        )

    def test_all_caps_case_diff_still_suggests(self) -> None:
        # difflib alone scores 'FOO' vs 'foo' at 0.0 (SequenceMatcher is
        # case-sensitive); the casefold pass is what rescues this.
        assert "did you mean 'foo'?" in _suggest_plane_id("FOO", ["foo"])

    def test_typo_suggests_difflib_match(self) -> None:
        assert _suggest_plane_id("cesna_150", ["cessna_150", "cessna_140"]) == (
            "; did you mean 'cessna_150'?"
        )

    def test_novel_id_no_suggestion(self) -> None:
        assert _suggest_plane_id("zzz", ["foo", "bar"]) == ""

    def test_ambiguous_casefold_falls_through_to_no_suggestion(self) -> None:
        # Two valid ids share a casefold → the casefold pass is ambiguous
        # and is skipped; difflib finds no high-ratio match for 'FOO'.
        assert _suggest_plane_id("FOO", ["foo", "Foo"]) == ""

    def test_exact_match_returns_empty(self) -> None:
        assert _suggest_plane_id("foo", ["foo", "bar"]) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_loader.py::TestPlaneIdSuggestion -v`
Expected: FAIL at import (`ImportError: cannot import name '_suggest_plane_id'`).

- [ ] **Step 3: Add imports**

In `src/hangarfit/loader.py`, change the import block near the top (currently `from pathlib import Path` / `from typing import Any`) to add:

```python
import difflib
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any
```

(Keep `import difflib` with the other stdlib imports, alphabetical; `Collection`/`Iterable` come from `collections.abc`.)

- [ ] **Step 4: Implement `_suggest_plane_id`**

Add immediately after the `_extract_maintenance_plane` function in `src/hangarfit/loader.py`:

```python
def _suggest_plane_id(candidate: str, valid_ids: Iterable[str]) -> str:
    """Return a '; did you mean X?' fragment for a near-miss id, or '' if none.

    Two passes, because ``difflib`` alone misses the headline case:
    ``SequenceMatcher`` is case-sensitive, so ``'FOO'`` vs ``'foo'`` scores
    0.0 and would yield no suggestion.

    1. Case-insensitive exact match: if exactly one valid id equals the
       candidate under ``casefold()`` (and isn't the candidate itself),
       suggest it with the case-sensitivity note. If two valid ids share a
       casefold (only possible for a fleet that deliberately uses
       case-distinct ids), the pass is ambiguous and is skipped.
    2. ``difflib.get_close_matches(n=1, cutoff=0.6)`` for genuine typos.
    """
    valid = list(valid_ids)
    folded = candidate.casefold()
    ci_matches = [v for v in valid if v.casefold() == folded and v != candidate]
    if len(ci_matches) == 1:
        return f"; did you mean {ci_matches[0]!r}? (plane ids are case-sensitive)"
    close = difflib.get_close_matches(candidate, valid, n=1, cutoff=0.6)
    if close and close[0] != candidate:
        return f"; did you mean {close[0]!r}?"
    return ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_loader.py::TestPlaneIdSuggestion -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader.py
git commit -m "feat(loader): add _suggest_plane_id near-match helper (#176)"
```

---

## Task 2: `_resolve_known_plane_id` gate

**Files:**
- Modify: `src/hangarfit/loader.py` (new function after `_suggest_plane_id`)
- Test: `tests/test_loader.py` (extend `TestPlaneIdSuggestion` or add `TestResolveKnownPlaneId`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loader.py`. Add `_resolve_known_plane_id` to the import block.

```python
class TestResolveKnownPlaneId:
    """Unit tests for the _resolve_known_plane_id loader gate."""

    def test_known_id_does_not_raise(self) -> None:
        # Returns None, no exception.
        assert (
            _resolve_known_plane_id("foo", ["foo", "bar"], role="placement", path=Path("x.yaml"))
            is None
        )

    def test_case_mismatch_raises_with_suggestion(self) -> None:
        with pytest.raises(LoaderError) as exc:
            _resolve_known_plane_id("Foo", ["foo"], role="placement", path=Path("x.yaml"))
        msg = str(exc.value)
        assert "x.yaml" in msg
        assert "placement references unknown plane id 'Foo'" in msg
        assert "did you mean 'foo'?" in msg
        assert "case-sensitive" in msg

    def test_novel_id_with_fix_hint_shows_hint(self) -> None:
        with pytest.raises(LoaderError) as exc:
            _resolve_known_plane_id(
                "ghost", ["foo"], role="maintenance.plane", path=Path("s.yaml"),
                fix_hint="either add it to fleet_in ['foo'] or fix the plane id",
            )
        msg = str(exc.value)
        assert "maintenance.plane references unknown plane id 'ghost'" in msg
        assert "either add it to fleet_in ['foo'] or fix the plane id" in msg
        assert "did you mean" not in msg

    def test_novel_id_no_hint_is_bare(self) -> None:
        with pytest.raises(LoaderError) as exc:
            _resolve_known_plane_id("zzz", ["foo"], role="placement", path=Path("x.yaml"))
        msg = str(exc.value)
        assert "unknown plane id 'zzz'" in msg
        assert "did you mean" not in msg
        assert msg.rstrip().endswith("'zzz'")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_loader.py::TestResolveKnownPlaneId -v`
Expected: FAIL at import (`cannot import name '_resolve_known_plane_id'`).

- [ ] **Step 3: Implement `_resolve_known_plane_id`**

Add immediately after `_suggest_plane_id` in `src/hangarfit/loader.py`:

```python
def _resolve_known_plane_id(
    candidate: str,
    valid_ids: Collection[str],
    *,
    role: str,
    path: Path,
    fix_hint: str = "",
) -> None:
    """Raise :class:`LoaderError` if ``candidate`` is not in ``valid_ids``.

    The message is ``"{path}: {role} references unknown plane id
    {candidate!r}{tail}"`` where ``tail`` is, in priority order: a
    ``_suggest_plane_id`` fragment when there is a near match, else
    ``"; " + fix_hint`` when ``fix_hint`` is set, else empty. A near-match
    suggestion always wins over ``fix_hint`` — naming the likely-intended
    id beats generic guidance.

    This is an earlier, friendlier front door to the unknown-id checks in
    ``Layout``/``Scenario.__post_init__``; those invariants are kept as the
    backstop for callers that bypass the loader.
    """
    if candidate in valid_ids:
        return
    tail = _suggest_plane_id(candidate, valid_ids)
    if not tail and fix_hint:
        tail = f"; {fix_hint}"
    raise LoaderError(f"{path}: {role} references unknown plane id {candidate!r}{tail}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_loader.py::TestResolveKnownPlaneId -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader.py
git commit -m "feat(loader): add _resolve_known_plane_id gate (#176)"
```

---

## Task 3: Wire into `load_layout` (placements + maintenance)

**Files:**
- Modify: `src/hangarfit/loader.py` (`load_layout`, after maintenance extraction ~`:210`, before the occupant check ~`:212`)
- Test: `tests/test_loader.py` (new `TestUnknownPlaneIdLayout`; update `test_unknown_plane_reference_propagates`)

- [ ] **Step 1: Write the failing integration tests**

Append a class to `tests/test_loader.py`. It reuses the `_minimal_fleet_and_hangar` / `_write` helpers, which live on the test class that already holds `test_unknown_plane_reference_propagates` — copy that class's `_minimal_fleet_and_hangar` pattern, or place these methods in that same class. Standalone version using module helpers:

```python
class TestUnknownPlaneIdLayout:
    """Loader-boundary unknown/mis-cased plane id rejection for layouts."""

    def _fleet_and_hangar(self, dir_: Path) -> None:
        _write(
            dir_ / "fleet.yaml",
            _minimal_aircraft_yaml("foo", movement_mode="always_own_gear", turn_radius_m=5.0),
        )
        _write(
            dir_ / "hangar.yaml",
            """
length_m: 25.0
width_m: 18.0
door: {center_x_m: 9.0, width_m: 12.0}
maintenance_bay: {center_x_m: 13.5, width_m: 9, depth_m: 9}
""",
        )

    def test_miscased_placement_id_suggests_canonical(self, tmp_path: Path) -> None:
        self._fleet_and_hangar(tmp_path)
        layout = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: Foo, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
""",
        )
        with pytest.raises(LoaderError) as exc:
            load_layout(layout)
        msg = str(exc.value)
        assert "placement references unknown plane id 'Foo'" in msg
        assert "did you mean 'foo'?" in msg
        assert "case-sensitive" in msg

    def test_miscased_maintenance_id_suggests_canonical(self, tmp_path: Path) -> None:
        self._fleet_and_hangar(tmp_path)
        layout = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements: []
maintenance: {plane: Foo}
""",
        )
        with pytest.raises(LoaderError) as exc:
            load_layout(layout)
        msg = str(exc.value)
        assert "maintenance.plane references unknown plane id 'Foo'" in msg
        assert "did you mean 'foo'?" in msg

    def test_novel_placement_id_no_false_suggestion(self, tmp_path: Path) -> None:
        self._fleet_and_hangar(tmp_path)
        layout = _write(
            tmp_path / "layout.yaml",
            """
fleet: fleet.yaml
hangar: hangar.yaml
placements:
  - {plane: zzz, x_m: 5, y_m: 5, heading_deg: 0, on_carts: false}
""",
        )
        with pytest.raises(LoaderError) as exc:
            load_layout(layout)
        msg = str(exc.value)
        assert "unknown plane id 'zzz'" in msg
        assert "did you mean" not in msg
```

Confirm `_minimal_aircraft_yaml` is importable at the top of `tests/test_loader.py` (it is used by `_minimal_fleet_and_hangar`). If it is a module-level function in the test file, reuse it directly; if it is defined elsewhere, mirror the existing import.

- [ ] **Step 2: Update the existing message-change test**

In `tests/test_loader.py`, `test_unknown_plane_reference_propagates` (~`:763`): the loader gate now fires before the model. Change:

```python
        with pytest.raises(LoaderError, match="unknown plane_id 'ghost'"):
```
to:
```python
        with pytest.raises(LoaderError, match="unknown plane id 'ghost'"):
```

(`plane_id` → `plane id`: the new loader message uses a space; the underscored form was the model's wording, now shadowed by the earlier loader gate.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_loader.py::TestUnknownPlaneIdLayout tests/test_loader.py::TestRealDataFiles -v -k "unknown or miscased or novel"`
Expected: the new `TestUnknownPlaneIdLayout` tests FAIL (no loader gate yet → today's behavior raises the model's `plane_id` message or the wrong role text); `test_unknown_plane_reference_propagates` FAILs on the new `plane id` regex.

- [ ] **Step 4: Wire the gate into `load_layout`**

In `src/hangarfit/loader.py`, after `maintenance_plane = _extract_maintenance_plane(raw, path)` (~`:210`) and **before** the `# Pre-Layout boundary check…` occupant block (~`:212`), insert:

```python
    for p in placements:
        _resolve_known_plane_id(p.plane_id, fleet, role="placement", path=path)
    if maintenance_plane is not None:
        _resolve_known_plane_id(maintenance_plane, fleet, role="maintenance.plane", path=path)
```

(`fleet` is the already-loaded `dict[str, Aircraft]`; `candidate in fleet` checks keys.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_loader.py -v`
Expected: PASS (all, including the new class and the updated propagation test).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader.py
git commit -m "feat(loader): reject unknown plane ids in load_layout with suggestion (#176)"
```

---

## Task 4: Wire into `load_scenario` (fleet_in + maintenance + constraints)

**Files:**
- Modify: `src/hangarfit/loader.py` (`load_scenario`: fleet_in after fleet load; replace `:315` maintenance check; constraints loop ~`:332`)
- Test: `tests/test_loader_scenario.py` (new cases; update `test_load_scenario_rejects_maintenance_plane_not_in_fleet_in`)

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_loader_scenario.py`. These mirror the existing `shutil.copy` data-staging idiom (real fleet ids: `aviat_husky`, `ctsl`).

```python
def _stage_scenario(tmp_path, body: str):
    """Write a scenario YAML next to copied real data files; return its path."""
    import shutil

    (tmp_path / "data").mkdir(exist_ok=True)
    shutil.copy("data/fleet.yaml", tmp_path / "data" / "fleet.yaml")
    shutil.copy("data/hangar.yaml", tmp_path / "data" / "hangar.yaml")
    p = tmp_path / "scenario.yaml"
    p.write_text("fleet: data/fleet.yaml\nhangar: data/hangar.yaml\n" + body)
    return p


def test_load_scenario_miscased_fleet_in_entry_suggests(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(tmp_path, "fleet_in: [Aviat_husky, ctsl]\n")
    with pytest.raises(LoaderError) as exc:
        load_scenario(p)
    msg = str(exc.value)
    assert "fleet_in entry references unknown plane id 'Aviat_husky'" in msg
    assert "did you mean 'aviat_husky'?" in msg


def test_load_scenario_miscased_maintenance_suggests(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(tmp_path, "fleet_in: [aviat_husky, ctsl]\nmaintenance:\n  plane: Ctsl\n")
    with pytest.raises(LoaderError) as exc:
        load_scenario(p)
    msg = str(exc.value)
    assert "maintenance.plane references unknown plane id 'Ctsl'" in msg
    assert "did you mean 'ctsl'?" in msg


def test_load_scenario_miscased_constraint_key_suggests(tmp_path):
    from hangarfit.loader import load_scenario

    p = _stage_scenario(
        tmp_path,
        "fleet_in: [aviat_husky, ctsl]\nconstraints:\n  Ctsl:\n    force_on_carts: false\n",
    )
    with pytest.raises(LoaderError) as exc:
        load_scenario(p)
    msg = str(exc.value)
    assert "constraints key references unknown plane id 'Ctsl'" in msg
    assert "did you mean 'ctsl'?" in msg
```

- [ ] **Step 2: Update the existing message-change test**

In `tests/test_loader_scenario.py`, `test_load_scenario_rejects_maintenance_plane_not_in_fleet_in` (~`:81`) uses `maintenance.plane: ghost` (no near match). The message moves into the helper with the `fix_hint`. Replace the four assertions (lines ~107-116) with:

```python
    msg = str(exc_info.value)
    # Names the bad plane id
    assert "ghost" in msg, f"message should name the bad plane id; got: {msg!r}"
    # Includes the file path
    assert str(bad) in msg, f"message should include the file path; got: {msg!r}"
    # Enumerates the valid fleet_in so the user knows what is valid
    assert "aviat_husky" in msg, f"message should list valid fleet_in planes; got: {msg!r}"
    # Actionable guidance (the sorted fleet_in list is inserted, so assert the two halves)
    assert "either add it to fleet_in" in msg, f"missing add-to-fleet_in guidance; got: {msg!r}"
    assert "or fix the plane id" in msg, f"missing fix-the-id guidance; got: {msg!r}"
    # 'ghost' has no near match → no false suggestion
    assert "did you mean" not in msg, f"'ghost' should not get a suggestion; got: {msg!r}"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest tests/test_loader_scenario.py -v`
Expected: the three new tests FAIL (no scenario gate yet → today's behavior raises a `fleet has:`/`not in fleet_in`/`constraints has key` message without the new role text or suggestion); the updated `not_in_fleet_in` test FAILs on the new wording.

- [ ] **Step 4: Wire the gate into `load_scenario`**

Three edits in `src/hangarfit/loader.py`, inside `load_scenario`.

(a) **fleet_in** — after the fleet/hangar load block, immediately before `# maintenance (optional, same shape as load_layout)` (~`:308`), insert:

```python
    for pid in fleet_in:
        _resolve_known_plane_id(pid, fleet, role="fleet_in entry", path=path)
```

(b) **maintenance** — replace the existing block (~`:314-318`):

```python
    if maintenance_plane is not None and maintenance_plane not in fleet_in:
        raise LoaderError(
            f"{path}: maintenance_plane {maintenance_plane!r} is not in fleet_in "
            f"{list(fleet_in)}; either add it to fleet_in or fix the plane id."
        )
```
with:
```python
    if maintenance_plane is not None:
        _resolve_known_plane_id(
            maintenance_plane,
            fleet_in,
            role="maintenance.plane",
            path=path,
            fix_hint=f"either add it to fleet_in {sorted(fleet_in)} or fix the plane id",
        )
```

(c) **constraints** — in the constraints loop (~`:332`), validate the key **before** the existing `try`:

```python
    for plane_id, cdata in constraints_raw.items():
        _resolve_known_plane_id(
            plane_id,
            fleet_in,
            role="constraints key",
            path=path,
            fix_hint=f"either add it to fleet_in {sorted(fleet_in)} or fix the plane id",
        )
        try:
            constraints[plane_id] = _build_plane_constraint(plane_id, cdata)
        except (ValueError, KeyError, TypeError, LoaderError) as e:
            raise LoaderError(f"{path}: constraint {plane_id!r}: {e}") from e
```

(Call it outside the `try` so its message is not re-wrapped with the `constraint {plane_id!r}:` prefix.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest tests/test_loader_scenario.py -v`
Expected: PASS (all, including the three new tests and the updated `not_in_fleet_in` test).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_scenario.py
git commit -m "feat(loader): reject unknown plane ids in load_scenario with suggestion (#176)"
```

---

## Task 5: Docs + full verification + PR

**Files:**
- Modify: `src/hangarfit/loader.py` (module docstring)
- Modify: `docs/architecture/05-building-block-view.md` (loader note)

- [ ] **Step 1: Add the case-sensitivity contract to the loader module docstring**

In `src/hangarfit/loader.py`, append a paragraph to the module docstring (after the existing struts paragraph, before the closing `"""`):

```
Plane ids are **case-sensitive** and are not normalised. When a layout or
scenario names an id that does not match the fleet exactly, the loader
rejects it at parse time with a ``did you mean 'X'?`` suggestion (a
case-insensitive match, else a ``difflib`` near match) rather than letting
a mis-cased id slip through to a late, generic model-invariant error.
```

- [ ] **Step 2: Add the arc42 note**

In `docs/architecture/05-building-block-view.md`, find the `loader` row/section and add one sentence: *"Plane ids are case-sensitive; unknown/mis-cased ids are rejected at load time with a `did you mean…?` suggestion (see ADR / spec 2026-05-25)."* Then grep for any other stale claim:

Run: `grep -rni "case-sensit\|case sensit\|normali" docs/ src/hangarfit/loader.py`
Resolve anything that now contradicts the contract. (Per the doc-layer-sweep lesson, PRs #178/#179.)

- [ ] **Step 3: Full verification**

```bash
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 -m ruff check src/ tests/
PYTHONPATH=src python3 -m ruff format --check src/ tests/
PYTHONPATH=src python3 -m mypy src/hangarfit/
```
Expected: all pass; mypy clean (verify `Collection`/`Iterable` annotations resolve).

- [ ] **Step 4: Commit docs**

```bash
git add src/hangarfit/loader.py docs/architecture/05-building-block-view.md
git commit -m "docs(loader): document case-sensitive plane-id contract (#176)"
```

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feature/176-plane-id-validation
gh pr create --base develop \
  --title "feat(loader): validate plane ids at load boundary with did-you-mean suggestions" \
  --body "Closes #176

Implements Option C from the design spec: strict-validate plane ids at the
loader boundary with casefold-first + difflib suggestions, across all five
references (layout placements + maintenance; scenario fleet_in, maintenance,
constraints). Ids stay case-sensitive; model invariants kept as backstop.

Spec: docs/superpowers/specs/2026-05-25-loader-plane-id-validation-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 6: Set PR metadata** (gh pr edit is broken in this repo; use the REST API)

```bash
PR=$(gh pr view --json number --jq .number)
gh api -X PATCH /repos/DocGerd/hangarfit/issues/$PR \
  -f 'assignees[]=DocGerd' -f 'labels[]=enhancement'
```

Then run `/pr-review` per the project workflow (this PR touches the loader → include `silent-failure-hunter`; it adds no new types → `type-design-analyzer` not required).

---

## Self-review

**Spec coverage:**
- §3.1 helpers → Tasks 1, 2. ✓
- §3.2 message shapes → asserted in Tasks 1-4. ✓
- §3.3 five call sites → Task 3 (2 layout) + Task 4 (3 scenario). ✓
- §3.4 ordering (placements/maint before occupant check; constraints key before `try`) → Task 3 Step 4, Task 4 Step 4c. ✓
- §5 backstops unchanged → no model edits in any task; `test_models.py`/`test_scenario.py` left untouched and run green in Task 5 Step 3. ✓
- §6 tests (4 shapes × entry points + backstop + 3 message updates) → helper units (T1/T2) + integration (T3/T4) + the two updates (T3 S2, T4 S2). Backstop tests covered by the full-suite run. ✓
- §7 docs (docstring + arc42 + grep sweep) → Task 5. ✓

**Placeholder scan:** no TBD/TODO; every code step has complete code. ✓

**Type consistency:** `_suggest_plane_id(candidate: str, valid_ids: Iterable[str]) -> str` and `_resolve_known_plane_id(candidate, valid_ids: Collection[str], *, role, path, fix_hint="") -> None` are used with matching argument names/types at every call site (`fleet` is `Collection[str]` via its keys; `fleet_in` is a `tuple[str,...]`). Message substrings asserted in tests match the f-strings in the impl (`"{role} references unknown plane id {candidate!r}"`, `"; did you mean {x!r}? (plane ids are case-sensitive)"`, `"; " + fix_hint`). ✓

**Known follow-on:** `test_load_scenario_rejects_null_maintenance_plane` (added in #219) has a docstring mention of the "maintenance_plane not in fleet_in boundary check"; still accurate after the rename to a helper call — no assertion change, optional light reword only.
