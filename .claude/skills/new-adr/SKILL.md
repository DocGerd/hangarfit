---
name: new-adr
description: This skill should be used when the user invokes "/new-adr" to scaffold a new Architecture Decision Record from the template, allocating the next ADR number and appending the matching index row to the ADR README. Accepts title="<decision title>" and status=Accepted|Proposed arguments. Dry-run by default — shows the planned number, filename, and index row, and waits for confirmation before writing anything.
disable-model-invocation: true
argument-hint: title="<decision title>" status=Accepted|Proposed
---

Scaffold a new Architecture Decision Record (ADR) for the hangarfit project from
`docs/adr/template.md`, allocate the next ADR number, write the new ADR file, and
append the matching index row to `docs/adr/README.md` — the easy-to-forget step.

**Arguments from the invocation**: $ARGUMENTS

## Step 1 — Parse and validate arguments

Parse the following named arguments from `$ARGUMENTS` using shell-style
(shlex-like) tokenization: text inside matching double quotes is a single token
even if it contains spaces. `title` must be wrapped in double-quotes (`"..."`)
when it contains spaces, which it almost always will; single-quote wrapping is
NOT supported. Embedded newlines in `title` are NOT supported — `$ARGUMENTS` is a
single line. Unknown keys (any key other than `title`, `status`) are an error —
stop immediately and name the unrecognised key.

| Arg | Required | Valid values | Default |
|-----|----------|--------------|---------|
| `title` | yes | non-empty string — the decision, stated declaratively (the decision, not the topic) | — |
| `status` | no | `Accepted` or `Proposed` (case-sensitive) | `Proposed` |

Validation rules:
- `title` must be a non-empty string after stripping the surrounding double
  quotes. A title consisting only of whitespace is invalid.
- `status`, if supplied, must be exactly `Accepted` or `Proposed` — nothing else.
  The template's Status field also allows `Deprecated` / `Superseded by …`, but a
  *brand-new* ADR is only ever `Proposed` (at PR-open) or `Accepted` (at
  PR-merge); this skill therefore only accepts those two. If `status` is omitted,
  default to `Proposed` — that is the canonical starting status per
  `docs/adr/README.md` ("ADR status starts at **Proposed**. It flips to
  **Accepted** when the PR is merged.").

If any argument is missing or invalid, stop immediately and print a clear error
message explaining which argument failed validation and the expected format. Do
NOT proceed to compute a number or write any file.

## Step 2 — Verify the source template and index exist

This skill copies the template and edits the index. Confirm both exist before
doing anything else:

```bash
test -f docs/adr/template.md && echo "template ok"
test -f docs/adr/README.md && echo "readme ok"
```

If `docs/adr/template.md` is missing, stop:
```
Error: docs/adr/template.md not found. This skill copies that template; it must exist.
```
If `docs/adr/README.md` is missing, stop:
```
Error: docs/adr/README.md not found. This skill appends the new ADR's index row to it.
```

## Step 3 — Determine the next ADR number

Allocate the next number as `current_highest + 1`, zero-padded to four digits.
**Take the max of the existing numbers — do NOT count files.** The numbering
convention (see `docs/adr/README.md`) is "four-digit, zero-padded, monotonically
increasing"; numbers are never reused, so a deprecated/superseded ADR leaves no
gap to reuse, and counting files would mis-allocate if a number were ever skipped.

```bash
ls docs/adr/[0-9][0-9][0-9][0-9]-*.md \
  | sed -n 's#.*/\([0-9][0-9][0-9][0-9]\)-.*#\1#p' \
  | sort -n | tail -1
```

This prints the current highest number (e.g. `0011`). Strip leading zeros, add 1,
and zero-pad the result to four digits. Call this `NEXT_NUMBER` (a 4-char string,
e.g. `0012`). If the command prints nothing (no ADRs at all — should never happen
in this repo, where ADR-0000 exists), use `0000`.

Do the increment carefully: `0011` → `0012`, `0099` → `0100`, `0009` → `0010`.
Treat the listed value as a decimal integer; never do string concatenation.

## Step 4 — Derive a kebab-case slug from the title

Derive `SLUG` from `title`:
1. Lowercase the whole title.
2. Replace every run of non-alphanumeric characters (spaces, punctuation,
   em-dashes `—`, slashes, etc.) with a single hyphen `-`.
3. Strip any leading or trailing hyphens.
4. Collapse any accidental double hyphens to single hyphens.

The slug must match `^[a-z0-9]+(-[a-z0-9]+)*$`. Example:
`"Reeds–Shepp motion model — towplanner v2"` → `reeds-shepp-motion-model-towplanner-v2`
(matching the real `0010-reeds-shepp-motion-model.md` style of short, hyphenated
slugs). Keep the slug reasonably short — if the title is long, trim the slug to the
distinctive words rather than encoding the full sentence, mirroring the existing
ADR filenames (e.g. `0011-linear-history-strategy-under-gitflow.md`, not the full
title). Show the slug you derived in the dry-run plan (Step 6) so the user can
veto a poor slug before anything is written.

## Step 5 — Compute today's date and build the filled ADR content

**Get today's date dynamically — do NOT hardcode it.** Run:
```bash
date +%Y-%m-%d
```
Call the result `TODAY` (ISO `YYYY-MM-DD`).

Read `docs/adr/template.md` and produce the new ADR content by filling these
fields (leave every other section's placeholder text exactly as the template has
it — the author fills the prose; this skill only stamps the header):

1. **Title line** (template line 1): replace
   `# ADR-NNNN: <Short, declarative title — the decision, not the topic>`
   with `# ADR-<NEXT_NUMBER>: <title>` (the real number and the user's title).
2. **Status:** replace the whole
   `Proposed | Accepted | Deprecated | Superseded by [ADR-XXXX](XXXX-slug.md)`
   value with the resolved `<status>` (from Step 1 — `Proposed` by default).
   Keep the explanatory HTML comment that follows it intact.
3. **Date:** replace `YYYY-MM-DD` with `<TODAY>`.
4. **Deciders:** replace `<names / GitHub handles>` with `Patrick Kuhn (DocGerd)`.

Leave the body sections (Context & Problem Statement, Decision Drivers, Considered
Options, Decision Outcome, Consequences, Compliance, More Information) with their
template placeholders — those are for the author to write. Honor the template's
"**At least two options must be listed**" rule by leaving its options scaffold in
place; do not strip it.

The output path is:
```
docs/adr/<NEXT_NUMBER>-<SLUG>.md
```

Check it does not already exist (`test -f`). If it does, stop:
```
Error: docs/adr/<NEXT_NUMBER>-<SLUG>.md already exists. Choose a different title or resolve the number collision.
```

## Step 6 — Build the index row and show the dry-run plan

Build the README index row. The exact format used by every existing data row in
the `## Index` table of `docs/adr/README.md` is (a leading and trailing pipe,
single spaces around each cell, the linked title, and the status as the third
column):
```
| <NEXT_NUMBER> | [<title>](<NEXT_NUMBER>-<SLUG>.md) | <status> |
```
Verbatim reference — the current last row in the table looks like:
```
| 0011 | [Linear history strategy under GitFlow — squash feature merges, accept release merge commits, target a clean first-parent mainline](0011-linear-history-strategy-under-gitflow.md) | Proposed |
```
Note: the table *header* row and its separator row use extra padding spaces to
align columns visually, but the **data rows use single spaces** — match the data
rows, not the header. The link target is the **bare filename** (no `docs/adr/`
prefix), because the README lives in the same directory. The new row is appended
as the **last row** of the table (after the current highest-numbered row), since
numbers increase monotonically.

Now print the dry-run plan verbatim (substituting the real resolved values
throughout — never print a literal `<...>` placeholder):

```
New ADR plan
============

No changes have been made yet.

[ ] 1. Allocate number:   <NEXT_NUMBER>   (current highest is <CURRENT_HIGHEST>)
[ ] 2. Write new file:    docs/adr/<NEXT_NUMBER>-<SLUG>.md
           Title:   ADR-<NEXT_NUMBER>: <title>
           Status:  <status>
           Date:    <TODAY>
           Deciders: Patrick Kuhn (DocGerd)
[ ] 3. Append index row to docs/adr/README.md:
           | <NEXT_NUMBER> | [<title>](<NEXT_NUMBER>-<SLUG>.md) | <status> |

The new ADR's body sections are left as template placeholders for you to fill in.
```

Then stop and print:
```
Confirm? Type YES to write the ADR and update the index, or anything else to abort.
```

**Wait for user input before proceeding.** Do not write any file or edit the
README until the user replies with exactly `YES` (case-sensitive). Any other
response (including `yes`, `y`, `Y`, `ok`, `sure`) must be treated as an abort:
```
Aborted. No changes were made.
```

## Step 7 — Execute (only after YES)

Execute the two writes in order. After each, print `[x] N. <description>`.

### Step 7.1 — Write the new ADR file

Use the Write tool to write the filled content (from Step 5) to
`docs/adr/<NEXT_NUMBER>-<SLUG>.md`. If the Write tool returns an error, stop
immediately and print the error text verbatim — do NOT touch the README, and do
NOT print the confirmation message.

### Step 7.2 — Append the index row to README.md

Use the Edit tool to append the new row to the `## Index` table in
`docs/adr/README.md`. Anchor on the current last data row of the table (the
highest-numbered existing row, read it fresh from the file) and replace it with
itself + a newline + the new row. For example, if the current last row is the
ADR-0011 row shown in Step 6, set:
- `old_string`: the exact ADR-0011 row line (read it from the file first to copy
  it byte-for-byte, including the title text and trailing ` |`).
- `new_string`: that same row line, then a newline, then the new ADR row.

If the Edit tool fails (anchor not unique, anchor not found, permission denied),
stop immediately and print the error verbatim plus:
```
The ADR file was written but the README index row was NOT added. Add it by hand:
  | <NEXT_NUMBER> | [<title>](<NEXT_NUMBER>-<SLUG>.md) | <status> |
```

## Step 8 — Print confirmation

After both writes succeed, print exactly:

```
Created: docs/adr/<NEXT_NUMBER>-<SLUG>.md
Index row appended to docs/adr/README.md.

Next steps:
1. Open docs/adr/<NEXT_NUMBER>-<SLUG>.md and fill in the body sections
   (Context & Problem Statement, Decision Drivers, Considered Options — at least
   two — Decision Outcome, Consequences, Compliance, More Information).
2. Open a PR per the GitFlow workflow; link the ADR from the PR.
3. When the PR merges, flip Status from Proposed to Accepted (if it isn't already).
```

Do not add anything else — no praise, no summary, no emoji.

## Constraints

- Never overwrite an existing ADR file.
- Never create any file other than `docs/adr/<NEXT_NUMBER>-<SLUG>.md`.
- The only edit to an existing file is appending one index row to
  `docs/adr/README.md`. Touch nothing else — not CLAUDE.md, not the arc42 docs.
- Allocate the next number as max-existing + 1 (robust to gaps), never as a file
  count.
- Get today's date dynamically (`date +%Y-%m-%d`); never hardcode it.
- Leave the ADR body sections as template placeholders — the author writes the
  prose; this skill only stamps the header (number, title, status, date,
  deciders) and the index row.
- Dry-run by default: never write or edit anything before the user types `YES`.

## Failure modes

Every abort condition in one place. In all cases, stop immediately and print the
described message. **Step 8 (the success confirmation) only runs after every step
above has succeeded — it is never printed following any abort.**

1. **Missing `title`**: print a clear error naming `title` and its expected
   `title="<decision title>"` format.
2. **Empty/whitespace-only `title`**: print a clear error stating the title must
   be a non-empty string.
3. **Invalid `status` value** (anything other than `Accepted` or `Proposed`):
   print a clear error stating `status` must be exactly `Accepted` or `Proposed`.
4. **Unknown argument key** (any key other than `title`, `status`): print a clear
   error naming the unrecognised key.
5. **`docs/adr/template.md` missing**: print
   `Error: docs/adr/template.md not found. This skill copies that template; it must exist.`
6. **`docs/adr/README.md` missing**: print
   `Error: docs/adr/README.md not found. This skill appends the new ADR's index row to it.`
7. **Computed output path already exists**: print
   `Error: docs/adr/<NEXT_NUMBER>-<SLUG>.md already exists. Choose a different title or resolve the number collision.`
8. **User does not confirm (any response other than `YES`)**: print
   `Aborted. No changes were made.`
9. **Write tool fails (new ADR file)**: print the error verbatim; do NOT touch
   the README.
10. **Edit tool fails (README index row)**: print the error verbatim plus the
    by-hand row to add; the ADR file is already written.
