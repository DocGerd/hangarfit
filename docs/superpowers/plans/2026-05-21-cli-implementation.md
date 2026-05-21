# `hangarfit` CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `hangarfit check ...` CLI per `docs/superpowers/specs/2026-05-21-cli-design.md` so Phase 1 ships.

**Architecture:** One new module `src/hangarfit/cli.py` (~120 lines: argparse subparser, dispatch, output formatters). One new test module `tests/test_cli.py` (15 tests, all in-process via `main(argv=...)` and `capsys`). One console-script entry in `pyproject.toml`. One Usage section in `README.md`. No changes to `models.py` / `loader.py` / `geometry.py` / `collisions.py` / `visualize.py`.

**Tech Stack:** Python 3.11+, argparse (stdlib), pytest, `capsys` for stream capture, `tmp_path` for ephemeral fixtures. The CLI relies on the existing `loader.py` (kwarg-only `load_layout(path, *, fleet=None, hangar=None)`), `collisions.check(layout) -> CheckResult`, and `visualize.render(layout, path, result=...)`.

**Working directory:** All commands assume cwd = `/home/pkuhn/hangarfit-feature-7-cli` (the `feature/7-cli` worktree off `develop`). If a subagent is dispatched here, its cwd IS this worktree; use bare `pytest` / `git` per the project's worktree conventions.

**Spec reference:** `docs/superpowers/specs/2026-05-21-cli-design.md` is the source of truth. Disagreements between this plan and the spec should be resolved by updating the plan, not by ad-libbing.

---

## Task 1: Scaffold `cli.py` + console-script entry

**Files:**
- Create: `src/hangarfit/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts]` section)
- Test: ad-hoc smoke (no pytest test yet — that comes in Task 2)

- [ ] **Step 1: Create the minimal `cli.py` stub**

Write the new file `src/hangarfit/cli.py`:

```python
"""Command-line interface for hangarfit.

Implements:
    hangarfit check LAYOUT [--render OUT.png] [--fleet PATH] [--hangar PATH] [--json]

See ``docs/superpowers/specs/2026-05-21-cli-design.md`` for the design.

JSON output schema: ``hangarfit.check/v1`` — a faithful dump of the
:class:`hangarfit.models.Conflict` dataclass (``kind`` / ``planes`` /
``detail``). Bump the schema version if and only if ``Conflict``
itself grows new fields.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with the ``check`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="hangarfit",
        description="Check a hand-authored hangar layout for validity.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="Check a layout YAML.")
    check.add_argument("layout", help="Path to the layout YAML.")
    check.add_argument(
        "--render",
        metavar="OUT.png",
        default=None,
        help="Write a top-down PNG (runs even when the layout is invalid).",
    )
    check.add_argument(
        "--fleet",
        metavar="PATH",
        default=None,
        help="Override the layout's embedded fleet: ref. Cannot be combined with a layout that has an embedded fleet: field.",
    )
    check.add_argument(
        "--hangar",
        metavar="PATH",
        default=None,
        help="Override the layout's embedded hangar: ref. Cannot be combined with a layout that has an embedded hangar: field.",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON on stdout (schema: hangarfit.check/v1).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns an exit code; does not call ``sys.exit``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    # Dispatch will be added in Task 3.
    raise NotImplementedError("Task 3 will wire up cmd_check dispatch.")
```

- [ ] **Step 2: Add the console-script entry to `pyproject.toml`**

Find the existing `[tool.setuptools.packages.find]` block and insert a new `[project.scripts]` section above it. The current `pyproject.toml` has:

```toml
[tool.setuptools.packages.find]
where = ["src"]
```

Use Edit to insert this block immediately before `[tool.setuptools.packages.find]`:

```toml
[project.scripts]
hangarfit = "hangarfit.cli:main"

```

(Trailing blank line is intentional — preserves the spacing convention before the next section.)

- [ ] **Step 3: Reinstall in editable mode so the console-script entry registers**

Run: `pip install -e ".[dev]"`
Expected: `Successfully installed hangarfit-0.0.1` (or similar) and no error about the missing `cli` module.

- [ ] **Step 4: Verify the script entry resolves to our `main`**

Run: `hangarfit --help`
Expected: argparse usage output listing the `check` subcommand. (Exits 0 because `--help` is handled by argparse and never reaches the `NotImplementedError`.)

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py pyproject.toml
git commit -m "feat(cli): scaffold cli.py and add console-script entry

Stubs out main()/build_parser() with the check subcommand shape from
the design spec. Dispatch is intentionally NotImplemented — Task 3
wires it up. The console-script entry makes 'hangarfit --help' work
after pip install -e .

Refs #7"
```

---

## Task 2: Tests 11 & 12 — argparse usage errors propagate as `SystemExit(2)`

**Files:**
- Create: `tests/test_cli.py`
- Modify: `src/hangarfit/cli.py` (only if needed — argparse default behavior should already give us what tests need)

- [ ] **Step 1: Create `tests/test_cli.py` with the first two failing tests**

```python
"""Tests for the hangarfit CLI."""

from __future__ import annotations

import pytest

from hangarfit.cli import main


class TestArgparseUsageErrors:
    """Bare / unknown commands fall through to argparse's own SystemExit(2)."""

    def test_subparser_no_command_shows_help(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2
        # argparse writes its usage error to stderr
        captured = capsys.readouterr()
        assert "usage:" in captured.err.lower()

    def test_unknown_subcommand_returns_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["nope"])
        assert exc_info.value.code == 2
```

- [ ] **Step 2: Run the tests — expect them to PASS (argparse already does this)**

Run: `pytest tests/test_cli.py -v`
Expected: both tests PASS. Reason: `add_subparsers(required=True)` already causes `parse_args([])` to call `parser.error(...)`, which in argparse exits via `SystemExit(2)` after printing usage to stderr — before our `NotImplementedError` would fire.

If either test fails, **stop** and reconcile against the spec — don't proceed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): assert argparse usage errors raise SystemExit(2)

Tests 11 and 12 from the design spec. argparse's own error path is
exercised before our dispatch — these are regression guards that
require subparsers stay required=True and that we don't accidentally
catch SystemExit in main().

Refs #7"
```

---

## Task 3: Test 1 — valid layout → exit 0 and `valid` on stdout

**Files:**
- Modify: `tests/test_cli.py` (add new test class)
- Modify: `src/hangarfit/cli.py` (replace `NotImplementedError` with real dispatch)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_cli.py`:

```python
class TestCheckHappyPath:
    """Valid layouts exit 0 with a 'valid' line on stdout."""

    def test_check_valid_layout_returns_0(self, capsys):
        exit_code = main(["check", "tests/fixtures/valid_two_separated.yaml"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "valid"
        assert captured.err == ""
```

- [ ] **Step 2: Run the test — expect FAIL with NotImplementedError**

Run: `pytest tests/test_cli.py::TestCheckHappyPath::test_check_valid_layout_returns_0 -v`
Expected: FAIL, raises `NotImplementedError("Task 3 will wire up cmd_check dispatch.")`.

- [ ] **Step 3: Implement `cmd_check` happy path and `_emit_human`**

Replace the contents of `src/hangarfit/cli.py` with:

```python
"""Command-line interface for hangarfit.

Implements:
    hangarfit check LAYOUT [--render OUT.png] [--fleet PATH] [--hangar PATH] [--json]

See ``docs/superpowers/specs/2026-05-21-cli-design.md`` for the design.

JSON output schema: ``hangarfit.check/v1`` — a faithful dump of the
:class:`hangarfit.models.Conflict` dataclass (``kind`` / ``planes`` /
``detail``). Bump the schema version if and only if ``Conflict``
itself grows new fields.
"""

from __future__ import annotations

import argparse
import sys

from hangarfit import collisions
from hangarfit.loader import load_fleet, load_hangar, load_layout
from hangarfit.models import CheckResult, Conflict


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with the ``check`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="hangarfit",
        description="Check a hand-authored hangar layout for validity.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="Check a layout YAML.")
    check.add_argument("layout", help="Path to the layout YAML.")
    check.add_argument(
        "--render",
        metavar="OUT.png",
        default=None,
        help="Write a top-down PNG (runs even when the layout is invalid).",
    )
    check.add_argument(
        "--fleet",
        metavar="PATH",
        default=None,
        help="Override the layout's embedded fleet: ref. Cannot be combined with a layout that has an embedded fleet: field.",
    )
    check.add_argument(
        "--hangar",
        metavar="PATH",
        default=None,
        help="Override the layout's embedded hangar: ref. Cannot be combined with a layout that has an embedded hangar: field.",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON on stdout (schema: hangarfit.check/v1).",
    )

    return parser


def _emit_human(result: CheckResult) -> None:
    """Write the human-readable summary to stdout."""
    if result.valid:
        print("valid")
        return
    n = len(result.conflicts)
    print(f"invalid: {n} conflict{'s' if n != 1 else ''}")
    for c in result.conflicts:
        print(_format_conflict(c))


def _format_conflict(c: Conflict) -> str:
    """One-line human render of a Conflict. No destructuring of ``detail``."""
    return f"  - {c.kind} [{', '.join(c.planes)}]: {c.detail}"


def cmd_check(args: argparse.Namespace) -> int:
    """Run the ``check`` subcommand. See spec §4 for the data flow."""
    fleet_override = load_fleet(args.fleet) if args.fleet else None
    hangar_override = load_hangar(args.hangar) if args.hangar else None
    layout = load_layout(args.layout, fleet=fleet_override, hangar=hangar_override)

    result = collisions.check(layout)
    _emit_human(result)
    return 0 if result.valid else 1


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns an exit code; does not call ``sys.exit``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args)
    # argparse with required=True should make this unreachable.
    parser.error(f"unknown command: {args.cmd!r}")
```

Note: Tasks 5–9 will add `try/except LoaderError`, `--json`, `--render`, and override semantics. Right now this is the happy path only.

- [ ] **Step 4: Run the test — expect PASS**

Run: `pytest tests/test_cli.py::TestCheckHappyPath::test_check_valid_layout_returns_0 -v`
Expected: PASS.

Also run the full suite to confirm no regression: `pytest -q`
Expected: all existing tests + 3 new ones PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "feat(cli): implement check happy path

cmd_check loads fleet/hangar overrides (only when supplied), loads
the layout, runs collisions.check, and prints a single 'valid' line
on success. Test 1 from the spec passes.

Refs #7"
```

---

## Task 4: Test 2 — invalid layout → exit 1, conflict list on stdout

**Files:**
- Modify: `tests/test_cli.py`

(No code change needed — Task 3's `_emit_human` already handles invalid layouts. This task verifies it works.)

- [ ] **Step 1: Pick a fixture and inspect its expected conflict shape**

Run: `grep -l '' tests/fixtures/invalid_fuselage_wing_overlap.yaml` (sanity: file exists)
Expected: prints the path.

The fixture is a pairwise overlap, so the conflict's `kind` will be `fuselage_wing_overlap` and `planes` will have two entries.

- [ ] **Step 2: Add the failing test (which won't actually fail — Task 3 already handles invalid)**

Append to `TestCheckHappyPath`:

```python
    def test_check_invalid_layout_returns_1(self, capsys):
        exit_code = main(["check", "tests/fixtures/invalid_fuselage_wing_overlap.yaml"])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out.startswith("invalid:")
        # Conflict line uses the spec's format: "  - <kind> [<plane>[, <plane>]]: <detail>"
        assert "fuselage_wing_overlap" in captured.out
        # Every conflict line starts with the two-space-dash prefix
        for line in captured.out.strip().split("\n")[1:]:
            assert line.startswith("  - ")
        assert captured.err == ""
```

- [ ] **Step 3: Run — expect PASS**

Run: `pytest tests/test_cli.py::TestCheckHappyPath::test_check_invalid_layout_returns_1 -v`
Expected: PASS.

If it fails because the fixture is structurally invalid (loader rejects it before collisions runs), pick a different `invalid_*.yaml` that gets past loader validation — `invalid_fuselage_wing_overlap.yaml`, `invalid_fuselage_fuselage.yaml`, `invalid_wing_wing_same_height.yaml`, `invalid_strut_blocks_nesting.yaml`, `invalid_hangar_bounds.yaml`, or `invalid_maintenance_position.yaml` are all post-load conflicts.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): assert invalid layout exits 1 with conflict list

Test 2 from the spec. The conflict-line format is locked in here
(two-space indent, dash, kind, planes, detail) — future tweaks to
_format_conflict need to update this assertion deliberately.

Refs #7"
```

---

## Task 5: Tests 3, 4, 5 — `LoaderError` → exit 2 on stderr

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/hangarfit/cli.py` (add the `try/except LoaderError` clause)

- [ ] **Step 1: Add the three failing tests**

Append to `tests/test_cli.py`:

```python
class TestCheckLoadErrors:
    """LoaderError (file not found, bad YAML, invariant violation) → exit 2 on stderr."""

    def test_check_missing_file_returns_2(self, capsys):
        exit_code = main(["check", "definitely/does/not/exist.yaml"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
        assert "not found" in captured.err

    def test_check_malformed_yaml_returns_2(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":::not valid yaml:::\n", encoding="utf-8")
        exit_code = main(["check", str(bad)])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err

    def test_check_invariant_violation_returns_2(self, capsys):
        # invalid_cart_rule.yaml puts two cart_eligible planes on_carts.
        # Layout.__post_init__ raises ValueError; loader wraps it in LoaderError.
        exit_code = main(["check", "tests/fixtures/invalid_cart_rule.yaml"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
        assert "cart" in captured.err.lower()
```

- [ ] **Step 2: Run — expect three FAILs (LoaderError propagates, no exit 2 yet)**

Run: `pytest tests/test_cli.py::TestCheckLoadErrors -v`
Expected: all three FAIL — `LoaderError` is not caught; pytest will show the raised exception.

- [ ] **Step 3: Add the `try/except LoaderError` clause in `cmd_check`**

Use Edit to replace the body of `cmd_check` in `src/hangarfit/cli.py`. Locate the existing block:

```python
def cmd_check(args: argparse.Namespace) -> int:
    """Run the ``check`` subcommand. See spec §4 for the data flow."""
    fleet_override = load_fleet(args.fleet) if args.fleet else None
    hangar_override = load_hangar(args.hangar) if args.hangar else None
    layout = load_layout(args.layout, fleet=fleet_override, hangar=hangar_override)

    result = collisions.check(layout)
    _emit_human(result)
    return 0 if result.valid else 1
```

Replace with:

```python
def cmd_check(args: argparse.Namespace) -> int:
    """Run the ``check`` subcommand. See spec §4 for the data flow."""
    try:
        fleet_override = load_fleet(args.fleet) if args.fleet else None
        hangar_override = load_hangar(args.hangar) if args.hangar else None
        layout = load_layout(args.layout, fleet=fleet_override, hangar=hangar_override)
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    result = collisions.check(layout)
    _emit_human(result)
    return 0 if result.valid else 1
```

Also add `LoaderError` to the imports at the top — find the existing line:

```python
from hangarfit.loader import load_fleet, load_hangar, load_layout
```

Replace with:

```python
from hangarfit.loader import LoaderError, load_fleet, load_hangar, load_layout
```

- [ ] **Step 4: Run — expect all three PASS**

Run: `pytest tests/test_cli.py::TestCheckLoadErrors -v`
Expected: 3 PASS.

Also run full suite: `pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "feat(cli): map LoaderError to exit 2 on stderr

Tests 3, 4, 5 from the spec. One except clause covers file-not-found,
YAML parse errors, schema errors, AND Layout invariant violations —
the loader has already collapsed those four exception classes into
LoaderError, so we don't duplicate the funnel here.

Refs #7"
```

---

## Task 6: Tests 6, 7 — `--json` flag

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/hangarfit/cli.py` (add `_conflict_to_dict`, `_emit_json`, wire `--json` in `cmd_check`)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_cli.py`:

```python
import json


class TestCheckJsonOutput:
    """--json emits the hangarfit.check/v1 schema on stdout."""

    def test_check_json_valid_emits_schema_v1(self, capsys):
        exit_code = main(["check", "--json", "tests/fixtures/valid_two_separated.yaml"])
        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "hangarfit.check/v1"
        assert payload["valid"] is True
        assert payload["conflicts"] == []
        assert payload["layout"] == "tests/fixtures/valid_two_separated.yaml"

    def test_check_json_invalid_lists_conflicts(self, capsys):
        exit_code = main(["check", "--json", "tests/fixtures/invalid_fuselage_wing_overlap.yaml"])
        assert exit_code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "hangarfit.check/v1"
        assert payload["valid"] is False
        assert len(payload["conflicts"]) >= 1
        for c in payload["conflicts"]:
            # Faithful dump of Conflict — exactly these three keys, nothing else.
            assert set(c.keys()) == {"kind", "planes", "detail"}
            assert isinstance(c["kind"], str)
            assert isinstance(c["planes"], list)
            assert 1 <= len(c["planes"]) <= 2
            assert all(isinstance(p, str) for p in c["planes"])
            assert isinstance(c["detail"], str)
```

(Move the `import json` line to the top of the file with the other imports if your editor prefers — both placements work.)

- [ ] **Step 2: Run — expect FAILs (cmd_check ignores `--json` for now)**

Run: `pytest tests/test_cli.py::TestCheckJsonOutput -v`
Expected: both FAIL because the current `cmd_check` always calls `_emit_human`, so stdout will not parse as JSON.

- [ ] **Step 3: Add `_conflict_to_dict` and `_emit_json`, then wire them in**

Use Edit to add the two helpers above `cmd_check`. After `_format_conflict`, insert:

```python


def _conflict_to_dict(c: Conflict) -> dict:
    """One-to-one dump of Conflict for the v1 JSON schema."""
    return {"kind": c.kind, "planes": list(c.planes), "detail": c.detail}


def _emit_json(layout_path: str, result: CheckResult) -> None:
    """Write the v1 JSON payload to stdout."""
    payload = {
        "schema": "hangarfit.check/v1",
        "layout": layout_path,
        "valid": result.valid,
        "conflicts": [_conflict_to_dict(c) for c in result.conflicts],
    }
    print(json.dumps(payload, indent=2))
```

Also add `import json` near the top of `src/hangarfit/cli.py` (group with stdlib imports, before `import sys`).

Now update `cmd_check` to choose the emitter based on `args.json`. Find:

```python
    result = collisions.check(layout)
    _emit_human(result)
    return 0 if result.valid else 1
```

Replace with:

```python
    result = collisions.check(layout)
    if args.json:
        _emit_json(args.layout, result)
    else:
        _emit_human(result)
    return 0 if result.valid else 1
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_cli.py::TestCheckJsonOutput -v`
Expected: 2 PASS.

Also: `pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "feat(cli): add --json output (schema hangarfit.check/v1)

Tests 6 and 7. The conflict object is a faithful three-field dump
of the Conflict dataclass — kind, planes, detail. No re-parsing of
detail into structured numerics; that would duplicate collisions.py
formatting logic and silently lie when collisions.py changes.

Refs #7"
```

---

## Task 7: Tests 8, 9 — `--render OUT.png`

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/hangarfit/cli.py` (wire `visualize.render` call)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_cli.py`:

```python
class TestCheckRender:
    """--render writes a PNG; works on both valid and invalid layouts."""

    def test_check_render_writes_png(self, tmp_path, capsys):
        out = tmp_path / "valid.png"
        exit_code = main(["check", "tests/fixtures/valid_two_separated.yaml", "--render", str(out)])
        assert exit_code == 0
        assert out.exists()
        assert out.stat().st_size > 0

    def test_check_render_on_invalid_writes_png(self, tmp_path, capsys):
        out = tmp_path / "invalid.png"
        exit_code = main(["check", "tests/fixtures/invalid_fuselage_wing_overlap.yaml", "--render", str(out)])
        assert exit_code == 1
        assert out.exists()
        assert out.stat().st_size > 0
```

- [ ] **Step 2: Run — expect FAILs (no PNG written yet)**

Run: `pytest tests/test_cli.py::TestCheckRender -v`
Expected: both FAIL — `out.exists()` is False.

- [ ] **Step 3: Wire `visualize.render` into `cmd_check`**

Add to the imports at the top of `src/hangarfit/cli.py`:

```python
from hangarfit import visualize
```

(Group with the existing `from hangarfit import collisions` line — either combine or keep separate, your call. Both are acceptable.)

Update `cmd_check`. Find:

```python
    result = collisions.check(layout)
    if args.json:
        _emit_json(args.layout, result)
    else:
        _emit_human(result)
    return 0 if result.valid else 1
```

Replace with:

```python
    result = collisions.check(layout)
    if args.json:
        _emit_json(args.layout, result)
    else:
        _emit_human(result)

    if args.render is not None:
        visualize.render(layout, args.render, result=result)

    return 0 if result.valid else 1
```

**Sanity-check the `visualize.render` signature.** Before this step, run:

```bash
grep -n "^def render" src/hangarfit/visualize.py
```

The result tells you the parameter names. If `render` takes `result=` as a keyword (per CLAUDE.md / spec §2), the call above is right. If the parameter is named differently (e.g., `check_result=`), update the call to match — and add a note to the spec's open-questions section after the task.

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_cli.py::TestCheckRender -v`
Expected: 2 PASS.

Also: `pytest -q`
Expected: all green. (Matplotlib's headless backend is already forced at import time in `visualize.py`, per CLAUDE.md.)

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "feat(cli): wire --render (works on invalid layouts too)

Tests 8 and 9. Passing result=CheckResult lets visualize.render
overdraw the conflicting parts in red — that's exactly when the
picture is most useful, so we render even after exit 1.

Refs #7"
```

---

## Task 8: Test 10 — `--render` skipped on structural error

**Files:**
- Modify: `tests/test_cli.py`

(No code change. The `try/except LoaderError` in Task 5 already returns 2 before the render branch is reached. This task is a regression guard.)

- [ ] **Step 1: Add the failing-by-default test (it will pass immediately)**

Append to `TestCheckRender`:

```python
    def test_check_render_skipped_on_structural_error(self, tmp_path, capsys):
        out = tmp_path / "should_not_exist.png"
        exit_code = main(["check", "tests/fixtures/invalid_cart_rule.yaml", "--render", str(out)])
        assert exit_code == 2
        assert not out.exists()
```

- [ ] **Step 2: Run — expect PASS**

Run: `pytest tests/test_cli.py::TestCheckRender::test_check_render_skipped_on_structural_error -v`
Expected: PASS. (If it fails, something in the code flow rearranged the order — investigate before proceeding.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): assert --render is skipped on structural errors

Test 10. Regression guard — the LoaderError early-return in
cmd_check must continue to short-circuit before the render branch,
otherwise users get a PNG for a layout that never loaded.

Refs #7"
```

---

## Task 9: Tests 13, 14, 15 — `--fleet` / `--hangar` overrides

**Files:**
- Modify: `tests/test_cli.py`

(No code change. Task 3's `if args.fleet else None` already implements the override contract correctly. These tests lock in the behavior.)

- [ ] **Step 1: Add the three tests**

Append to `tests/test_cli.py`:

```python
class TestFleetHangarOverrides:
    """--fleet / --hangar work only when the layout has no embedded ref."""

    def test_no_override_uses_embedded(self, capsys):
        # Existing fixtures all embed fleet:/hangar: — no override given,
        # the loader resolves from the YAML. This is the regression guard.
        exit_code = main(["check", "tests/fixtures/valid_two_separated.yaml"])
        assert exit_code == 0
        assert capsys.readouterr().out.strip() == "valid"

    def test_fleet_override_with_clean_layout(self, tmp_path, capsys):
        # A layout that does NOT embed fleet:/hangar: — both must come from --fleet/--hangar.
        # We copy an existing fixture but strip the embedded refs.
        import shutil
        src = "tests/fixtures/valid_two_separated.yaml"
        clean = tmp_path / "clean_layout.yaml"
        original = open(src, encoding="utf-8").read()
        stripped = "\n".join(
            line for line in original.splitlines()
            if not (line.startswith("fleet:") or line.startswith("hangar:"))
        )
        clean.write_text(stripped + "\n", encoding="utf-8")

        exit_code = main([
            "check", str(clean),
            "--fleet", "data/fleet.yaml",
            "--hangar", "data/hangar.yaml",
        ])
        assert exit_code == 0
        assert capsys.readouterr().out.strip() == "valid"

    def test_fleet_override_with_embedded_fleet_errors(self, capsys):
        # Both kwarg and embedded are present — loader rejects this.
        exit_code = main([
            "check", "tests/fixtures/valid_two_separated.yaml",
            "--fleet", "data/fleet.yaml",
        ])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err
```

- [ ] **Step 2: Run — expect three PASS (existing code already implements the contract)**

Run: `pytest tests/test_cli.py::TestFleetHangarOverrides -v`
Expected: 3 PASS.

If `test_fleet_override_with_clean_layout` fails because the `valid_two_separated.yaml` fixture has additional structural requirements that don't survive stripping `fleet:` / `hangar:`, fall back to: don't strip — instead, build a minimal layout YAML in `tmp_path` containing only `placements:` (use the placements from the existing fixture verbatim). Re-run.

If `test_fleet_override_with_embedded_fleet_errors` fails because the loader's double-source-of-truth check has changed, **stop** — the spec needs revisiting (see spec §4 "load-bearing" note).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): lock in --fleet/--hangar override semantics

Tests 13, 14, 15. The 'if args.fleet else None' pattern in cmd_check
is load-bearing — passing None signals the loader to resolve from
the layout YAML's embedded ref. These tests pin both halves of the
contract: omitted = embedded; supplied = override; both = error.

Refs #7"
```

---

## Task 10: README — Usage section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current README intro to find the right insertion point**

Run: `head -40 README.md`
Expected: shows the project title, the elevator pitch, and an existing section (probably "Install" or "Status"). Find the location that matches the spec's §7 — insert the new "Usage" section *after* "Install" (or after the intro paragraph if there's no Install section yet), but *before* the more detailed sections like "Coordinate convention" or "Phase 1 scope".

- [ ] **Step 2: Insert the Usage section**

Use Edit to insert this block at the chosen point. The exact `old_string` depends on what you see in step 1 — pick a unique line just before the insertion point. The new content:

````markdown
## Usage

```bash
# Install from a checkout
pip install -e .

# Check a hand-authored layout
hangarfit check layouts/example.yaml

# Render the layout (works on invalid layouts too — conflicts highlighted in red)
hangarfit check layouts/example.yaml --render out.png

# Machine-readable output
hangarfit check layouts/example.yaml --json

# Override the fleet/hangar (advanced — for layouts without embedded fleet:/hangar: refs)
hangarfit check my_portable_layout.yaml --fleet path/to/fleet.yaml --hangar path/to/hangar.yaml
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Valid layout |
| 1 | Invalid layout (conflicts found) |
| 2 | Could not check (file not found, bad YAML, invariant violation, bad usage) |

````

- [ ] **Step 3: Smoke-render the README locally**

Run: `python -m markdown README.md > /tmp/readme.html 2>&1 || true; head -50 /tmp/readme.html`
Expected: HTML is produced and the new section is present.

(If `markdown` isn't installed, fall back to: `grep -A 30 '^## Usage' README.md` and visually verify the section reads correctly.)

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): add CLI Usage section and exit-code table

Mirrors the spec's §7 verbatim so the spec doc and the user-facing
README stay in sync without duplication of intent.

Refs #7"
```

---

## Task 11: End-to-end smoke + push

**Files:**
- None modified (verification only)

- [ ] **Step 1: Run the full test suite once more**

Run: `pytest -q`
Expected: all green, 15 new tests in `test_cli.py`, no regressions in existing modules.

- [ ] **Step 2: Run the CLI against the example layout**

Run: `hangarfit check layouts/example.yaml; echo "exit=$?"`
Expected: `valid` on stdout and `exit=0`, OR `invalid: N conflict(s)` with conflict lines and `exit=1` (either is fine — the example layout uses placeholder dimensions, so the *content* of the output is illustrative, not pass/fail).

If the command exits 2 with "error: ...": investigate before proceeding. Likely causes:
- Worktree not pip-installed (run `pip install -e ".[dev]"`).
- `data/fleet.yaml` or `data/hangar.yaml` accidentally renamed.

- [ ] **Step 3: Run the JSON variant**

Run: `hangarfit check layouts/example.yaml --json | python -m json.tool | head -20`
Expected: parseable JSON with the four top-level keys (`schema`, `layout`, `valid`, `conflicts`).

- [ ] **Step 4: Run the render**

Run: `hangarfit check layouts/example.yaml --render /tmp/example.png; ls -la /tmp/example.png`
Expected: file exists, size > 0.

- [ ] **Step 5: Verify the commit history is clean**

Run: `git log --oneline origin/develop..HEAD`
Expected: 10 commits (one per Tasks 1–10), each with a `feat(cli):` / `test(cli):` / `docs(readme):` / `docs(spec):` prefix and `Refs #7` in the body.

- [ ] **Step 6: Push the branch**

Run: `git push -u origin feature/7-cli`
Expected: branch published to remote.

- [ ] **Step 7: Hand back to the orchestrator**

The next step is **opening the PR** via the orchestrator session — NOT from this plan. The plan stops here so the orchestrator can run the PR-review pipeline (`pr-review-toolkit:code-reviewer`, `pr-review-toolkit:silent-failure-hunter`, `pr-review-toolkit:comment-analyzer` if README changed meaningfully) before the user sees it.

---

## Self-review notes (writing-plans skill — done before handoff)

- **Spec coverage:** every requirement in spec §§2–8 maps to at least one task:
  - §2 command surface → Task 1 (parser shape) + Task 9 (override semantics)
  - §3 exit codes → Tasks 3 (0), 4 (1), 5 (2 from LoaderError), 8 (render-skipped-on-2), 2 (argparse 2)
  - §4 data flow → Tasks 3 + 5 + 6 + 7
  - §5 output format (human + JSON) → Tasks 3 + 4 (human), Task 6 (JSON)
  - §6 tests 1–15 → Tasks 2 (11, 12), 3 (1), 4 (2), 5 (3, 4, 5), 6 (6, 7), 7 (8, 9), 8 (10), 9 (13, 14, 15)
  - §7 README → Task 10
  - §8 files touched → all created/modified across Tasks 1, 3, 5, 6, 7, 10
- **No placeholders:** every step has concrete code, exact paths, and named commands. Step 2 in Task 7 includes a runtime sanity-check for the `render(..., result=...)` signature so a parameter-name surprise doesn't silently break the test.
- **Type consistency:** `Conflict`/`CheckResult` imports added in Task 3 are reused in Tasks 5/6 without renaming. `_format_conflict`, `_emit_human`, `_conflict_to_dict`, `_emit_json` names are consistent between definition (Task 3 / Task 6) and reference (the JSON tests in Task 6).
- **TDD ordering:** every code-adding task starts with a red test (or notes when the test passes immediately as a regression guard, with reasoning). Order: scaffolding → argparse → happy → invalid → errors → JSON → render → render-skip → overrides → README → smoke.
