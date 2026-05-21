# `hangarfit` CLI — design (issue #7)

**Status:** approved 2026-05-21
**Tracks:** [#7 CLI](https://github.com/DocGerd/hangarfit/issues/7) in milestone v0.3.0 — User-facing tooling
**Author:** Claude (Opus 4.7), reviewed by @DocGerd

The last Phase-1 deliverable. Wires the existing substrate (`loader`, `models`, `collisions`, `visualize`) into a runnable command so a human can check and render a hand-authored layout.

---

## 1. Goals & non-goals

**Goals**

- One command, `hangarfit check <layout.yaml>`, that returns an exit code a script can act on.
- Visual debugging for invalid layouts (rendering should run *even when* the layout has conflicts — that's exactly when the picture is most useful).
- Machine-readable output via opt-in `--json`, so future scripts (and downstream tools like the planner in later phases) have a stable contract.
- Tests that exercise the CLI in-process via `main(argv=...)`; no subprocess overhead.

**Non-goals (Phase 1)**

- No `plan`, `render`, `list-fleet` subcommands yet. Subparser scaffolding is in place so they can land later without a breaking CLI rename, but they are not implemented.
- No package-data discovery of `data/fleet.yaml` / `data/hangar.yaml` relative to the install location. The CLI is run from a checkout (or with explicit paths).
- No `--verbose` / `-v` flag. Output is fixed; if it turns out to be noisy in practice, add a `--quiet` later.

---

## 2. Command surface

```
hangarfit check LAYOUT [--render OUT.png] [--fleet PATH] [--hangar PATH] [--json]
```

- `LAYOUT` — positional, required. Path to the layout YAML to check.
- `--render OUT.png` — optional. Write a top-down PNG of the layout. Runs even on invalid layouts (conflicts are highlighted in red by `visualize.render_layout(..., check_result=CheckResult)`).
- `--fleet PATH` — optional **override**. When absent, the layout's own embedded `fleet:` field is used (loader resolves it relative to the layout YAML's directory). When supplied, the layout YAML *must not* also have an embedded `fleet:` field — the loader rejects double sources of truth (`loader.py:160`). **No default value** — the embedded-vs-override choice is the user's, not implicit.
- `--hangar PATH` — same semantic as `--fleet`, for `hangar:`.
- `--json` — switch human-readable stdout to JSON. Errors still go to stderr unchanged.

**Important:** all existing fixtures and `layouts/example.yaml` embed `fleet:` / `hangar:` fields. So `hangarfit check layouts/example.yaml` (no overrides) is the normal usage and resolves correctly. `--fleet` / `--hangar` exist for users who maintain layouts without embedded refs (e.g., portable, fleet-agnostic layout templates) — not as a way to swap data files for arbitrary layouts.

Subparser shape (chosen to leave room for `plan` / `render` / `list-fleet` later — the issue body's wording `hangarfit check ...` already implies it):

```
hangarfit
└── check    (only subcommand in Phase 1)
```

Bare `hangarfit` (no subcommand) prints help and exits non-zero (argparse default = 2).

---

## 3. Exit codes

| Code | Meaning | Stream |
|---|---|---|
| 0 | Valid layout. | stdout: `valid` (or JSON `{"valid": true, ...}`) |
| 1 | Invalid layout — collision/bounds/maintenance conflicts found by `collisions.check()`. | stdout: conflict list (or JSON `{"valid": false, "conflicts": [...]}`) |
| 2 | User/usage error. We couldn't even check. | stderr: `error: <message>` |

Exit-2 sub-cases:

| Stage | Trigger | How the CLI sees it | Message shape |
|---|---|---|---|
| argparse | unknown flag, missing positional | `SystemExit(2)` from argparse | argparse default usage on stderr |
| file open | layout/fleet/hangar path missing | `LoaderError` (wrapped) | `error: file not found: <path>` |
| YAML parse | malformed YAML | `LoaderError` (wrapped) | `error: <path>: YAML parse error: <details>` |
| loader schema | missing keys, unknown enum, struts-block expansion | `LoaderError` (native) | `error: <path>: <message>` |
| `Layout.__post_init__` | cart-rule, `movement_mode` ↔ `on_carts` mismatch, maintenance plane not in fleet | `LoaderError` (loader wraps `ValueError` at `loader.py:212`) | `error: <path>: <invariant message>` |

**Key implementation note:** `loader.py` already catches `FileNotFoundError`, `yaml.YAMLError`, and the `ValueError` from `Layout.__post_init__`, and re-raises each as `LoaderError`. So the CLI needs **one** `except LoaderError` clause (plus letting `argparse`'s own `SystemExit(2)` propagate). Don't catch `FileNotFoundError` / `yaml.YAMLError` / `ValueError` separately in `cli.py` — that would duplicate the loader's already-correct boundary and risk a behavior divergence.

**Rationale for splitting 0/1/2 rather than collapsing into 0/1** (as the issue body literally says): argparse already exits 2 on its own usage errors, so a 0/1-only policy would require overriding `argparse.ArgumentParser.error()`. More importantly, scripts wrapping the CLI benefit from telling apart "the layout you gave me has conflicts" (1, expected business outcome) from "I couldn't read your file" (2, plumbing problem). This is the standard Unix convention; deviating from it costs more than it saves.

---

## 4. Data flow

```python
def cmd_check(args) -> int:
    try:
        fleet_override  = load_fleet(args.fleet)   if args.fleet  else None
        hangar_override = load_hangar(args.hangar) if args.hangar else None
        layout = load_layout(args.layout, fleet=fleet_override, hangar=hangar_override)
    except LoaderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    result = collisions.check(layout)

    if args.json:
        _emit_json(args.layout, result)
    else:
        _emit_human(result)

    if args.render is not None:
        visualize.render_layout(layout, args.render, check_result=result)

    return 0 if result.valid else 1
```

Notes:

- `load_layout` is kwarg-only for `fleet`/`hangar` (signature in `loader.py:144`).
- The `if args.fleet else None` pattern is load-bearing: passing `None` is the signal to the loader to resolve from the layout YAML's embedded `fleet:` field. Passing a non-None dict triggers the conflict check against the embedded field (and fails if both are present). Both halves of this contract are the loader's, not the CLI's, and we mirror it intentionally.
- All three loader calls are inside a single `try` block — any of them failing means we have nothing to check or render.
- `LoaderError` is the **only** load-time exception class we need to catch. The loader has already collapsed `FileNotFoundError` / `yaml.YAMLError` / `Layout.__post_init__`'s `ValueError` into it (see `loader.py:374-377` and `loader.py:212`).
- `collisions.check()` returns a `CheckResult`; it does not raise for invalid layouts (invalid is a value, not an exception). So no `try` around it.
- Rendering runs only after a successful load. Any structural error (cart-rule, missing maintenance plane, etc.) short-circuits at exit 2 with no PNG written. See §3 and tests 9/10.

`main(argv: list[str] | None = None) -> int`:
- Returns the integer; does not call `sys.exit`. Makes `main()` directly testable with `capsys`.
- The `pyproject.toml` console-script entry `hangarfit = "hangarfit.cli:main"` relies on setuptools' standard behavior of `sys.exit(main())` when the entry-point return value is non-None.
- `argparse`'s own usage errors raise `SystemExit(2)` — we **don't** catch this; let it propagate (test 11 / test 12 cover this via `pytest.raises(SystemExit)` checking `.code == 2`).

---

## 5. Output format

The `Conflict` dataclass in `models.py` is intentionally lean: `kind: str`, `planes: tuple[str, ...]` (1 or 2 IDs), `detail: str`. Specifics like z-gaps, vertex coordinates, and clearance numbers are baked into `detail` by `collisions.py`. The CLI's job is to surface what's there — **not** to re-parse `detail` into structured fields. Inventing a richer schema in `cli.py` would either silently lie (if it stayed empty) or duplicate `collisions.py`'s formatting logic.

The pairwise `kind` is `"<part_a>_<part_b>_overlap"` with the part names sorted alphabetically (so `fuselage_wing_overlap`, `strut_wing_overlap`, etc., are stable regardless of plane iteration order). Layout-wide `kind`s today: `hangar_bounds`, `maintenance_position`, `maintenance_no_fuselage`.

### 5.1 Human (default)

Valid:
```
valid
```

Invalid:
```
invalid: 2 conflicts
  - fuselage_wing_overlap [cessna_140, cessna_150]: part 'fuselage' (z=0..1.5) and part 'wing' (z=2.1..2.3) within horizontal clearance 0.3 m and z-gap 0.1 m (< 0.2 m)
  - hangar_bounds [husky]: part 'tail' vertex (12.500, -0.300) outside hangar 0..18 x 0..25
```

The conflict-line renderer is a private `_format_conflict(c: Conflict) -> str` inside `cli.py` of shape:

```python
def _format_conflict(c: Conflict) -> str:
    return f"  - {c.kind} [{', '.join(c.planes)}]: {c.detail}"
```

That's it — no destructuring of `detail`. **It is not added as `Conflict.__str__`** — keeping `models.py` free of presentation concerns is a deliberate boundary, even when the formatter is this small.

### 5.2 `--json`

Valid:
```json
{
  "schema": "hangarfit.check/v1",
  "layout": "layouts/example.yaml",
  "valid": true,
  "conflicts": []
}
```

Invalid:
```json
{
  "schema": "hangarfit.check/v1",
  "layout": "layouts/bad.yaml",
  "valid": false,
  "conflicts": [
    {
      "kind": "fuselage_wing_overlap",
      "planes": ["cessna_140", "cessna_150"],
      "detail": "part 'fuselage' (z=0..1.5) and part 'wing' (z=2.1..2.3) within horizontal clearance 0.3 m and z-gap 0.1 m (< 0.2 m)"
    },
    {
      "kind": "hangar_bounds",
      "planes": ["husky"],
      "detail": "part 'tail' vertex (12.500, -0.300) outside hangar 0..18 x 0..25"
    }
  ]
}
```

The conflict object is a one-to-one dump of the `Conflict` dataclass: `{kind, planes, detail}`. The `planes` array is always 1 or 2 strings (enforced by `Conflict.__post_init__`). The implementation is a one-liner:

```python
def _conflict_to_dict(c: Conflict) -> dict:
    return {"kind": c.kind, "planes": list(c.planes), "detail": c.detail}
```

The `schema` key is the forward-compat handle — `v1` is the only valid value today. If we ever enrich `Conflict` itself to carry numeric fields, the CLI bumps to `v2` and consumers pin accordingly.

The `--json` path also goes to stdout (not a separate stream). Errors remain on stderr regardless.

---

## 6. Tests

`tests/test_cli.py`, all in-process via `main(argv=...)` and `capsys`:

| # | Test | Assertion |
|---|---|---|
| 1 | `test_check_valid_layout_returns_0` | `main(["check", "tests/fixtures/valid_two_separated.yaml"])` → 0; stdout starts with `valid` |
| 2 | `test_check_invalid_layout_returns_1` | one `invalid_*` fixture → 1; stdout has `invalid:` and at least one conflict line |
| 3 | `test_check_missing_file_returns_2` | nonexistent layout → 2; stderr contains `not found` |
| 4 | `test_check_malformed_yaml_returns_2` | tmp file with `:::` content → 2; stderr |
| 5 | `test_check_invariant_violation_returns_2` | `invalid_cart_rule.yaml` (loader wraps the cart-rule `ValueError` into `LoaderError`) → 2; stderr `error: ... cart ...` |
| 6 | `test_check_json_valid_emits_schema_v1` | `--json` on valid layout → stdout parses as JSON with `valid: true, schema: hangarfit.check/v1, conflicts: []` |
| 7 | `test_check_json_invalid_lists_conflicts` | `--json` on invalid → `valid: false`, each item has exactly the keys `kind`, `planes` (list of str, length 1 or 2), `detail` (str); no extra keys |
| 8 | `test_check_render_writes_png` | `--render tmp_path/out.png` → file exists, size > 0 |
| 9 | `test_check_render_on_invalid_writes_png` | invalid layout with `--render` → exit 1, PNG written, conflicts highlighted (smoke-checked via file size, not pixel inspection) |
| 10 | `test_check_render_skipped_on_structural_error` | `invalid_cart_rule.yaml` with `--render` → exit 2, no PNG written at the requested path |
| 11 | `test_subparser_no_command_shows_help` | `main([])` → `SystemExit(2)`; argparse usage on stderr (use `pytest.raises(SystemExit) as ei; assert ei.value.code == 2`) |
| 12 | `test_unknown_subcommand_returns_2` | `main(["nope"])` → `SystemExit(2)` (same idiom) |
| 13 | `test_fleet_override_with_clean_layout` | tmp layout YAML (no embedded `fleet:`) + `--fleet PATH` to tmp fleet → load succeeds, override is used |
| 14 | `test_fleet_override_with_embedded_fleet_errors` | tmp layout with embedded `fleet:` + `--fleet PATH` → exit 2 (loader's double-source-of-truth rule) |
| 15 | `test_no_override_uses_embedded` | existing fixture with embedded `fleet:` and no `--fleet` flag → load succeeds (regression guard for the override-vs-embedded contract) |

Tests 1, 2, 5, 15 reuse existing fixtures. Tests 4, 13 create their own tmp files via `tmp_path`. Test 14 reuses an existing fixture (it passes `--fleet` alongside a layout that embeds `fleet:`, triggering the loader's double-source-of-truth rejection). No new permanent fixtures are required.

---

## 7. README

A new "Usage" section under the existing intro:

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

---

## 8. Files touched

| File | Change |
|---|---|
| `src/hangarfit/cli.py` | **new** — argparse, dispatch, formatters |
| `tests/test_cli.py` | **new** — the 15 tests in §6 |
| `pyproject.toml` | add `[project.scripts] hangarfit = "hangarfit.cli:main"` |
| `README.md` | add Usage section per §7 |
| `docs/superpowers/specs/2026-05-21-cli-design.md` | **new** (this file) |

`models.py`, `loader.py`, `geometry.py`, `collisions.py`, `visualize.py` are **not** modified by this work. Any pressure to add `__str__` to `Conflict`, expose new public helpers, or shift schema concerns into the data layer is out of scope and should be rejected at review time — that pressure typically means the formatter in `cli.py` is in the wrong place, and the right answer is to fix the formatter, not the model.

---

## 9. Open questions resolved during design

- **Subparsers vs flat command:** subparsers. Phase 2's planner will land as a sibling subcommand. Renaming the entry-point shape later would be a breaking CLI change.
- **`--json` now or later:** now. The schema-v1 commitment is a faithful dump of `Conflict` (`kind`/`planes`/`detail`) — no extra inference, no re-parsing of `detail`. Implementation is two trivial helpers.
- **Exit-code taxonomy:** 0/1/2 split (per §3), not 0/1. Issue body language was indicative, not binding.
- **Render on invalid:** yes; render on *structural* error: no (no Layout object exists). See §3 and test 10.
- **`Conflict.__str__`:** rejected. Formatting lives in `cli.py`.
- **`logging` module vs `print`:** `print` for stdout, `print(..., file=sys.stderr)` for stderr. The `logging` module is overkill for a single-command CLI with no log levels.
- **`--fleet` / `--hangar` defaults:** none. Discovered during self-review that the loader rejects `fleet=` kwarg + embedded `fleet:` field (`loader.py:160`); since every existing fixture and `layouts/example.yaml` embed those refs, an argparse default would make all of them fail to load. Flags are explicit overrides only — see §2 and test 14.
- **Exception handling shape:** one `except LoaderError` clause covers file-not-found, YAML parse error, schema error, and `Layout.__post_init__` invariants. The loader already collapsed all four into `LoaderError`; duplicating that funnel in `cli.py` would risk behavior drift if loader's exception policy evolves.
