---
name: new-fixture
description: This skill should be used when the user invokes "/new-fixture" to scaffold a new collision-test fixture YAML file from the canonical valid or invalid template. Accepts kind=valid|invalid, slug=<snake_case>, and rationale="..." arguments.
disable-model-invocation: true
argument-hint: kind=valid|invalid slug=<snake_case_name> rationale="<why this case matters>"
---

Scaffold a new collision-test fixture file for the hangarfit project.

**Arguments from the invocation**: $ARGUMENTS

## Step 1 — Parse and validate arguments

Parse the following named arguments from `$ARGUMENTS`. Arguments are space-separated `key=value` pairs and may appear in any order. `rationale` must be wrapped in double-quotes (`"..."`) when it contains spaces; single-quote wrapping is NOT supported. A `=` character inside a double-quoted value is fine (e.g. `rationale="z-gap = 0.3 m"`). Unknown keys (any key other than `kind`, `slug`, `rationale`) are an error — stop immediately and name the unrecognised key.

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

## Step 3 — Determine the next case number

Before building the file content, compute the next available case number:

```bash
grep -h '^# Case ' tests/fixtures/*.yaml \
  | sed -n 's/^# Case \([0-9]*\).*/\1/p' \
  | sort -n | tail -1
```

If the command returns a number N, use `N+1`. If it returns nothing (no fixtures yet), use `1`. Call this value `NEXT_CASE`.

If `tests/fixtures/` does not exist, stop immediately and print:
```
Error: tests/fixtures/ directory not found. This skill requires the directory to exist before scaffolding a fixture.
```
Do NOT create the directory automatically.

## Step 4 — Choose the template and build the new fixture content

The canonical header style is established by the layout fixtures in `tests/fixtures/`. Study `tests/fixtures/valid_two_separated.yaml` (the valid exemplar) and `tests/fixtures/invalid_strut_blocks_nesting.yaml` (the invalid exemplar) to understand the expected structure before writing your output. Key elements:

1. First line: `# Case <NEXT_CASE> — <one-sentence summary>.`
2. Blank `#` line.
3. Body paragraphs (one or more), wrapped at ~80 chars, each line beginning with `#`. The body **must** include:
   - Which planes are placed and at what position/heading.
   - Bracket-form coordinate citations for relevant parts, e.g. `fuselage x [4.5, 5.5], y [0.35, 6.65]` or `wing x [0.7, 9.3], y [3.8, 5.2], z [1.9, 2.2]`.
   - Z-band citations for height-sensitive reasoning, e.g. `z [0.5, 2.0]`.
   - Explicit clearance arithmetic where the pass/fail margin matters, e.g. `z-gap = 1.9 − 1.6 = 0.3 m > 0.2 m clearance`.
   - For `kind=invalid`: which conflict kind(s) the checker must emit (e.g. `strut_wing_overlap`, `wing_wing_overlap`, `fuselage_wing_overlap`, `hangar_bounds`, etc.).
   - For `kind=valid`: why the expected result is valid (e.g. polygon distance vs clearance, height disjointness).
4. Blank `#` line.
5. The `# TODO:` nudge lines.

A single-paragraph narrative with no coordinate citations is **not** sufficient. The header is the primary documentation; it must let a reader reconstruct the geometry without running the code.

**For `kind=valid`** — base on `tests/fixtures/valid_two_separated.yaml`:

```yaml
# Case <NEXT_CASE> — <one-sentence summary>. Valid.
#
# <Plane A> at (<x>, <y>, <heading>, <on_carts>): fuselage x [a, b],
#   y [c, d]; wing x [e, f], y [g, h], z [z_bot, z_top].
# <Plane B> at (<x>, <y>, <heading>, <on_carts>): ...
#
# Wing-wing nearest approach: <distance> m → polygon distance ≈ X m,
# well above the 0.3 m horizontal clearance.
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
# Case <NEXT_CASE> — <one-sentence summary> → invalid: `<conflict_kind>`.
#
# <Plane A> at (<x>, <y>, <heading>, <on_carts>): <relevant part>
#   at x [a, b] / y [c, d], z [z_bot, z_top].
# <Plane B> at (<x>, <y>, <heading>, <on_carts>): <relevant part>
#   x [e, f], y [g, h], z [z_bot2, z_top2].
#   z-gap = <z_bot2> − <z_top> = <N> m <operator> 0.2 m clearance.
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

The `fleet:` and `hangar:` keys must remain exactly `../../data/fleet.yaml` and `../../data/hangar.yaml` (relative paths that work from inside `tests/fixtures/`).

**Non-standard-hangar override:** If and only if the `rationale` string contains the literal substring `test_hangar_large`, change `hangar:` to `test_hangar_large.yaml` (a sibling file in the same directory — the relative path is just the filename) and insert a comment line directly after the summary line explaining why the larger hangar is needed. In all other cases use `../../data/hangar.yaml` — do NOT infer the override from words like "large" or "space" alone.

## Step 5 — Write the file

Use the Write tool to write the constructed YAML to `tests/fixtures/<kind>_<slug>.yaml`. If the Write tool returns an error (permission denied, disk full, or any other failure), stop immediately and print the error text verbatim — do NOT proceed to the confirmation step.

The file must:
1. Start with the rationale header comment block (as constructed in Step 4), beginning with `# Case <NEXT_CASE> — …`.
2. Have exactly one blank line between the comment block and `fleet:`.
3. Contain `fleet:` and `hangar:` keys.
4. Contain a `placements:` key with the `# TODO` comment placeholder.
5. NOT contain any real placement values (those are for the user to fill in).

## Step 6 — Print confirmation

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

Every layout fixture in `tests/fixtures/` follows the convention established by `valid_two_separated.yaml` (valid exemplar) and `invalid_strut_blocks_nesting.yaml` (invalid exemplar). Note: `test_hangar_large.yaml` is a *hangar config* file, not a layout fixture — do not use it as a header convention exemplar.

The header is a YAML comment block at the top of the file. It must:
1. Begin with `# Case N — <one-sentence summary>.` where N is the sequential case number computed in Step 3.
2. Cite the relevant part coordinates in bracket form: `x [a, b]`, `y [c, d]`, `z [z_bot, z_top]`.
3. Show explicit clearance arithmetic where the pass/fail margin matters.
4. For invalid fixtures: name the expected conflict kind(s).
5. For valid fixtures: state why no conflict fires (polygon distance, height disjointness, etc.).

The rationale lives in the YAML comment header, NOT in a separate README or in the test file. A single-paragraph narrative with no coordinate citations is incomplete.

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
- If `tests/fixtures/` does not exist, stop and print an error — do NOT create the directory.
- If the Write tool fails for any reason, stop immediately and print the error verbatim — do NOT print the confirmation message.
