---
name: new-fixture
description: This skill should be used when the user invokes "/new-fixture" to scaffold a new collision-test fixture YAML file from the canonical valid or invalid template. Accepts kind=valid|invalid, slug=<snake_case>, and rationale="..." arguments.
disable-model-invocation: true
argument-hint: kind=valid|invalid slug=<snake_case_name> rationale="<why this case matters>"
---

Scaffold a new collision-test fixture file for the hangarfit project.

**Arguments from the invocation**: $ARGUMENTS

## Step 1 — Parse and validate arguments

Parse the following named arguments from `$ARGUMENTS`. Each is in `key=value` form; `rationale` may be quoted with `"..."` and can span a sentence.

| Arg | Required | Valid values |
|-----|----------|--------------|
| `kind` | yes | `valid` or `invalid` |
| `slug` | yes | snake_case identifier (letters, digits, underscores only — no spaces, no hyphens) |
| `rationale` | yes | non-empty string explaining what geometric scenario this fixture tests and why it matters |

If any argument is missing or invalid, stop immediately and print a clear error message explaining which argument failed validation and the expected format. Do NOT proceed to write any file.

Validation rules:
- `kind` must be exactly `valid` or `invalid` — nothing else.
- `slug` must match `^[a-z][a-z0-9_]*$` — start with a lowercase letter, contain only lowercase letters, digits, and underscores.
- `rationale` must be a non-empty string (after stripping quotes).

## Step 2 — Determine output path and check for collisions

Construct the output path:
```
tests/fixtures/<kind>_<slug>.yaml
```

Check whether that file already exists (use the Read tool to attempt a read, or Bash `test -f`). If it exists, stop immediately and print:
```
Error: tests/fixtures/<kind>_<slug>.yaml already exists. Delete it first or choose a different slug.
```
Do NOT overwrite an existing fixture.

## Step 3 — Choose the template and build the new fixture content

**For `kind=valid`** — base on `tests/fixtures/valid_two_separated.yaml`:

```yaml
# <kind>_<slug> — <first line of rationale>. <kind capitalized>.
#
# <Full rationale paragraph, wrapped at ~80 chars, explaining the geometric
# scenario: which planes, which parts, which heights, why the expected
# result is valid.>
#
# TODO: edit placements below. Run:
#   pytest tests/test_collisions.py -v
# to verify after filling in real values.

fleet: ../../data/fleet.yaml
hangar: ../../data/hangar.yaml

placements:
  # TODO: replace with real placement(s).
  # Each entry must have: plane, x_m, y_m, heading_deg, on_carts.
  # Example:
  #   - plane: ctsl
  #     x_m: 5.0
  #     y_m: 3.5
  #     heading_deg: 0.0
  #     on_carts: false
```

**For `kind=invalid`** — base on `tests/fixtures/invalid_strut_blocks_nesting.yaml`:

```yaml
# <kind>_<slug> — <first line of rationale>. <kind capitalized>.
#
# <Full rationale paragraph, wrapped at ~80 chars, explaining the geometric
# scenario: which planes, which parts, which heights, and which conflict
# kind(s) the checker must emit (e.g. strut_wing_overlap,
# wing_wing_overlap, fuselage_wing_overlap, hangar_bounds, etc.).>
#
# TODO: edit placements below. Run:
#   pytest tests/test_collisions.py -v
# to verify after filling in real values.

fleet: ../../data/fleet.yaml
hangar: ../../data/hangar.yaml

placements:
  # TODO: replace with real placement(s) that trigger the expected conflict.
  # Each entry must have: plane, x_m, y_m, heading_deg, on_carts.
  # Example:
  #   - plane: cessna_150
  #     x_m: 9.0
  #     y_m: 5.0
  #     heading_deg: 0.0
  #     on_carts: false
```

Populate the header comment block as follows:
- First line after `#`: `<kind>_<slug> — <opening clause from rationale>. <Kind capitalized>.`
- Blank `#` line.
- Subsequent `#` lines: the full rationale text wrapped at ~80 chars.
- Blank `#` line.
- The `# TODO:` nudge lines.

The `fleet:` and `hangar:` keys must remain exactly `../../data/fleet.yaml` and `../../data/hangar.yaml` (relative paths that work from inside `tests/fixtures/`).

If the rationale mentions a non-standard hangar (e.g. needs more space), add a note in the header explaining why `test_hangar_large.yaml` is used and change `hangar:` accordingly — but only do this if the user explicitly specifies it in the rationale; otherwise always use `../../data/hangar.yaml`.

## Step 4 — Write the file

Use the Write tool to write the constructed YAML to `tests/fixtures/<kind>_<slug>.yaml`.

The file must:
1. Start with the rationale header comment block (as constructed in Step 3).
2. Have exactly one blank line between the comment block and `fleet:`.
3. Contain `fleet:` and `hangar:` keys.
4. Contain a `placements:` key with the `# TODO` comment placeholder.
5. NOT contain any real placement values (those are for the user to fill in).

## Step 5 — Print confirmation

After writing the file, print exactly:

```
Created: tests/fixtures/<kind>_<slug>.yaml

Next steps:
1. Open tests/fixtures/<kind>_<slug>.yaml and replace the TODO placements
   with real aircraft positions that exercise the scenario.
2. Add a test method in tests/test_collisions.py that loads this fixture
   and asserts the expected outcome.
3. Run: pytest tests/test_collisions.py -v
```

Do not add anything else — no praise, no summary, no emoji.

## Rationale-in-header convention

Every fixture file in `tests/fixtures/` follows the convention established in `test_hangar_large.yaml`: the top of the file is a comment block that explains in plain English *why the fixture exists* — what geometric scenario it tests, which parts are involved, and what outcome the checker must produce. This is the single most important convention to enforce.

The rationale lives in the YAML comment header, NOT in a separate README or in the test file. If the header is missing or terse, the fixture is incomplete.

## Fixture discovery

The existing test suite in `tests/test_collisions.py` discovers fixtures by name:
```python
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

def _load(name: str):
    return load_layout(FIXTURES_DIR / f"{name}.yaml")
```

Each test method calls `_load("valid_two_separated")` etc. by the bare stem (no `.yaml`). To make a new fixture exercisable, the user must:
1. Write the fixture at `tests/fixtures/<kind>_<slug>.yaml` (this skill does that).
2. Add a test method that calls `_load("<kind>_<slug>")`.

The skill creates the file; adding the test method is a manual step the user must perform.

## Constraints

- Never overwrite an existing file.
- Never create any file other than `tests/fixtures/<kind>_<slug>.yaml`.
- Never modify Python source under `src/` or `tests/`.
- Never run `pytest` automatically — only print the nudge to run it.
- The `placements:` section must always contain a `# TODO` marker; never fill in real geometry values.
