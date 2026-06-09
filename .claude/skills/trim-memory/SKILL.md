---
name: trim-memory
description: This skill should be used when the user invokes "/trim-memory" to compact an over-budget auto-memory MEMORY.md index by LOSSLESSLY moving the overflow detail from over-long index lines into their matching topic files, leaving a short pointer behind. Accepts memory_dir=<absolute-path-to-memory>. Dry-run by default — shows every line it would rewrite, the topic-file appends, and the before/after byte count against the <24.4 KB target, and waits for the user to type YES before writing anything.
disable-model-invocation: true
argument-hint: memory_dir=<absolute-path-to-memory-dir>
---

Compact an over-budget Claude Code auto-memory index. The harness loads
`MEMORY.md` into context every session, but it has a soft size ceiling (~24.4 KB);
the `remember` skill appends index lines and never compacts them, so the file
grows past the ceiling and the harness silently truncates it — older memory drops
out of context. This skill brings it back under budget WITHOUT losing any
information: the overflow detail on each too-long index line is moved verbatim
into that entry's topic file, and the index line is rewritten to a short pointer
that still names the live status.

The target lives **outside this repo** (it is per-developer Claude Code state,
e.g. `~/.claude/projects/-home-pkuhn-hangarfit/memory/`), so the directory is a
required argument — this skill never hardcodes a path. It is **manual-invoke
only** (not scheduled): cron jobs are session-scoped and recurring ones expire
after 7 days, so they can't durably maintain this.

**Arguments from the invocation**: $ARGUMENTS

## Step 1 — Parse and validate arguments

Parse `memory_dir` from `$ARGUMENTS` using shell-style (shlex-like) tokenization.
Unknown keys (anything other than `memory_dir`) are an error — stop and name the
unrecognised key.

| Arg | Required | Valid values | Default |
|-----|----------|--------------|---------|
| `memory_dir` | yes | an **absolute** path to an existing auto-memory directory | — |

Validation:
- `memory_dir` must be non-empty and absolute (starts with `/` or `~` — expand a
  leading `~` to `$HOME`). A relative path is invalid: this operates on
  per-developer state outside any repo, so a repo-relative path is always wrong.
- The directory must exist. Check:
  ```bash
  test -d "<memory_dir>" && echo "dir ok"
  ```
If the argument is missing, relative, or the directory does not exist, stop
immediately with a clear error naming the problem. Do NOT read or write anything.

## Step 2 — Verify MEMORY.md and measure the current budget

Confirm the index exists and record its size:
```bash
test -f "<memory_dir>/MEMORY.md" && wc -c "<memory_dir>/MEMORY.md"
```
If `MEMORY.md` is missing, stop:
```
Error: <memory_dir>/MEMORY.md not found. Pass the directory that contains the auto-memory index.
```
Record `BYTES_BEFORE` (the `wc -c` value). The target is **< 24.4 KB (24,986
bytes)**. If `BYTES_BEFORE` is already under target, stop and say so — nothing to
do:
```
MEMORY.md is <BYTES_BEFORE> bytes, already under the 24,986-byte (24.4 KB) target. Nothing to compact.
```

## Step 3 — Read the index and find the over-long lines

Read `<memory_dir>/MEMORY.md` with the Read tool. Its body is one Markdown list
item per memory, each shaped like:
```
- [<Title>](<file>.md) — <detail>
```
(There may also be a top heading / intro lines and blank lines — those are NOT
index entries; leave them untouched.)

List every index line whose length exceeds **180 characters**, longest first, so
the biggest wins come first:
```bash
awk '{ if (length($0) > 180) printf "%4d  %s\n", length($0), $0 }' "<memory_dir>/MEMORY.md" | sort -rn
```
These are the compaction candidates. If there are none but the file is still over
budget, the bloat is not from over-long index lines (e.g. too MANY entries); stop
and report that — this skill only compacts over-long lines, it does not delete
entries:
```
MEMORY.md is over budget (<BYTES_BEFORE> bytes) but no index line exceeds 180 chars, so the size is from the NUMBER of entries, not over-long ones. This skill won't delete entries — compact by archiving stale topics by hand.
```

## Step 4 — Plan each move (no writes yet)

For each over-long index line, in the order above, plan a LOSSLESS move:

1. **Parse the line** into `Title`, `file` (the `(<file>.md)` link target), and
   `detail` (everything after the `— `). The topic file is `<memory_dir>/<file>`.
2. **Resolve the topic file.** It almost always exists (the index points at it).
   - If it exists, you will APPEND to its body — never touch its YAML frontmatter
     (the `---` … `---` block) and never edit existing body text.
   - If it is absent, you will CREATE it with minimal frontmatter (`name:` = the
     file's slug; `description:` = a one-line summary of the detail;
     `metadata:` with `type:` inferred from the filename prefix — `project_*` →
     `project`, `feedback_*` → `feedback`, else `reference`), then the moved
     detail as the body.
3. **Compose the rewritten index line.** Keep the `- [<Title>](<file>.md) — `
   prefix verbatim, then a NEW hook of ≤ ~120 chars distilled from `detail` that
   still names the **live status** (dates, the current PR/issue state, the key
   decision) — enough that the one-liner is still useful in the loaded index. The
   whole rewritten line must be < 180 characters. Do NOT invent facts: the hook is
   a faithful compression of the existing detail, nothing new.
4. **Compose the topic-file append.** The FULL original `detail` (verbatim, so
   nothing is lost) under a dated provenance heading appended to the topic file
   body:
   ```

   ## Index detail (moved from MEMORY.md by /trim-memory on <TODAY>)

   <the original detail, verbatim>
   ```
   Get `<TODAY>` dynamically: `date +%Y-%m-%d`. Never hardcode it.

Estimate `BYTES_AFTER` ≈ `BYTES_BEFORE` − Σ(old line length − new line length)
over the rewritten lines. If the estimate is still ≥ 24,986 bytes after
compacting every over-long line, say so in the plan (the user may need to also
prune entries by hand) but still offer to apply what you have.

## Step 5 — Show the dry-run plan and wait for confirmation

Print the plan verbatim, substituting real values (never a literal `<...>`):
```
trim-memory plan
================

No changes have been made yet.

Target: < 24,986 bytes (24.4 KB)
Now:    <BYTES_BEFORE> bytes
After:  ~<BYTES_AFTER> bytes (estimated)

<N> over-long index line(s) to compact (longest first):

[ ] <file>.md
      index line: <OLD_LEN> -> <NEW_LEN> chars
      old: - [<Title>](<file>.md) — <detail>
      new: - [<Title>](<file>.md) — <new hook>
      append to <file>.md body: the full original detail (verbatim), under a
        dated "Index detail (moved …)" heading. Frontmatter untouched.
        [topic file MISSING — will be CREATED with frontmatter]   <-- only if absent

  …(repeat per line)…
```
Then stop and print:
```
Confirm? Type YES to apply (rewrite MEMORY.md + append to the topic files), or anything else to abort.
```
**Wait for user input.** Do not write anything until the user replies with exactly
`YES` (case-sensitive). Any other response (`yes`, `y`, `ok`, …) aborts:
```
Aborted. No changes were made.
```

## Step 6 — Execute (only after YES)

For each planned move, in order:

1. **Append the detail to the topic file.** Use the Edit tool, anchoring on the
   last non-empty line currently in the topic file's body, and append the dated
   provenance heading + the verbatim detail after it. NEVER alter the frontmatter
   block. If the topic file is absent, use the Write tool to create it with the
   frontmatter + body from Step 4.2. If a topic-file write fails, stop
   immediately, print the error verbatim, and do NOT rewrite that line in
   MEMORY.md (so the index keeps pointing at the still-complete detail — no loss).
2. **Rewrite the index line in MEMORY.md.** Use the Edit tool with the exact old
   line as `old_string` and the new (compacted) line as `new_string`. Do this only
   AFTER that entry's detail is safely in its topic file.

After each entry, print `[x] <file>.md`.

## Step 7 — Verify and report

Re-measure and confirm the result:
```bash
wc -c "<memory_dir>/MEMORY.md"
```
Print exactly:
```
trim-memory done
================
MEMORY.md: <BYTES_BEFORE> -> <BYTES_AFTER_ACTUAL> bytes (target < 24,986).
<N> entr(y/ies) compacted; their full detail now lives in the matching topic files.
0 entries deleted, 0 links broken, frontmatter untouched.
```
If the actual size is still ≥ 24,986 bytes, add one line noting the index is still
over budget and the remaining size is from the number of entries, which this skill
does not prune.

Do not add anything else — no praise, no summary, no emoji.

## Constraints

- **Lossless, always.** Never delete an index entry, never drop the `[Title](file.md)`
  link, never discard detail — it is moved verbatim into the topic file, not
  summarised away. The index hook is a faithful compression of the moved detail.
- **Never alter a topic file's frontmatter** (the `---` … `---` block). Only ever
  APPEND to the body (or CREATE the file with fresh frontmatter if it is absent).
- **Only two kinds of write**: append to topic files, and rewrite over-long lines
  in `<memory_dir>/MEMORY.md`. Touch nothing else; never write inside this repo.
- Append the detail to a topic file BEFORE rewriting its index line, so an
  interrupted run never leaves a shortened pointer whose detail was not yet saved.
- Get today's date dynamically (`date +%Y-%m-%d`); never hardcode it.
- Dry-run by default: write nothing before the user types `YES`.

## Failure modes

Every abort condition in one place. Stop immediately and print the described
message. Step 7 (success) only runs after every step above has succeeded.

1. **Missing/relative `memory_dir`, or unknown argument key**: clear error naming
   the problem and the expected `memory_dir=<absolute-path>` form.
2. **`memory_dir` does not exist**: `Error: <memory_dir> not found or not a directory.`
3. **`MEMORY.md` missing**: `Error: <memory_dir>/MEMORY.md not found. Pass the directory that contains the auto-memory index.`
4. **Already under budget**: report it; nothing to do.
5. **Over budget but no over-long lines**: report that the size is from the number
   of entries; this skill won't delete entries.
6. **User does not confirm (any response other than `YES`)**: `Aborted. No changes were made.`
7. **A topic-file write fails**: print the error verbatim; do NOT rewrite that
   entry's index line (its detail is still intact in the index).
8. **An index-line Edit fails** (anchor not unique / not found): print the error
   verbatim plus the new line to apply by hand; that entry's detail is already
   safely appended to its topic file, so re-running on the remaining lines is safe.
