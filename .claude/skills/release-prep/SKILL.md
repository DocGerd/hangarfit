---
name: release-prep
description: This skill should be used when the user invokes "/release-prep" to prepare the docs and CHANGELOG for a release BEFORE the cut. Accepts version=X.Y.Z. Runs on develop off a fresh feature/release-prep-X.Y.Z branch, promotes the CHANGELOG [Unreleased] block, prompts a manual doc-freshness audit, and opens a normal PR into develop for focused review. Run this first, merge it, then run /release-cut.
disable-model-invocation: true
argument-hint: version=<X.Y.Z>
---

Prepare the documentation and CHANGELOG for a hangarfit release. This is the
**first** of the two release skills: it lands the doc work via a normal feature
PR into `develop` so it gets focused review, separate from the release-state
plumbing. After this PR merges, run `/release-cut version=X.Y.Z` against the
already-prepped `develop`.

This skill does **not** create a release branch, bump `pyproject.toml`, push to
`main`, or tag anything — that is `/release-cut`'s job. This skill only touches
docs and `CHANGELOG.md` on a feature branch.

**Arguments from the invocation**: $ARGUMENTS

## Step 1 — Parse and validate arguments

Parse the following named argument from `$ARGUMENTS` using shell-style
(shlex-like) tokenization. Each argument takes the form `key=value`. Values that
contain spaces must be wrapped in double quotes (e.g. `key="hello world"`).
Single quotes are **not** supported. Embedded newlines inside a quoted value are
not supported. The `version` value contains no spaces and does not require
quoting in normal use.

| Arg | Required | Valid values |
|-----|----------|--------------|
| `version` | yes | Semver-style string matching `^[0-9]+\.[0-9]+\.[0-9]+$` (digits only, e.g. `1.2.3`) |

Unknown keys (any key other than `version`) are an error — stop immediately and
name the unrecognised key.

If `version` is missing or does not match `^[0-9]+\.[0-9]+\.[0-9]+$`, stop
immediately and print a clear error:
```
Error: version is required and must match X.Y.Z (digits only, e.g. 0.1.0).
```

**Prerelease and build-metadata suffixes** (e.g. `0.1.0-rc1`, `0.1.0-alpha.2`,
`0.1.0+build.5`, `0.1.0.dev1`) do **not** match this regex and are intentionally
rejected. If you need one, edit the docs and `CHANGELOG.md` by hand.

Do NOT proceed to any further step.

## Step 2 — Verify clean state

Run all four checks. If any fails, stop immediately and print the described
message.

**Check A — current branch is `develop`:**
```bash
git rev-parse --abbrev-ref HEAD
```
Expected output: `develop`. If not, stop:
```
Error: must be on the 'develop' branch to prep a release. Current branch is '<current>'.
Switch with: git switch develop
```

**Check B — working tree is clean:**
```bash
git status --porcelain
```
Expected output: empty (no lines). If not, stop:
```
Error: working tree is not clean. Commit or stash the following changes before prepping a release:
<output of git status --porcelain>
```

**Check C — `develop` is up to date with `origin/develop`:**

First fetch:
```bash
git fetch origin develop
```
Then compare:
```bash
git rev-list --count HEAD..origin/develop
```
Expected output: `0`. If not, stop:
```
Error: local 'develop' is behind 'origin/develop' by N commit(s). Pull first:
  git pull --ff-only origin develop
```

**Check D — release branch does not already exist:**

The prep must run before the cut, so the release branch must not exist yet.
```bash
git branch --list release/<version>
```
If the output is non-empty, stop:
```
Error: branch 'release/<version>' already exists locally. The release appears to be cut already — prep must run before the cut.
```
Also check the remote:
```bash
git ls-remote --heads origin release/<version>
```
If the output is non-empty, stop:
```
Error: branch 'release/<version>' already exists on origin. The release appears to be cut already — prep must run before the cut.
```

## Step 3 — Fail-fast guard: CHANGELOG [Unreleased] must not be empty

Read `CHANGELOG.md` in the repo root. Locate the `## [Unreleased]` section (the
block between the `## [Unreleased]` heading and the next `## [` version heading).

The section contains the subsection headers `### Added`, `### Changed`,
`### Fixed` (and possibly `### Removed` / `### Security`). It is **empty** if
there is no non-blank, non-subsection-header content line under any subsection —
i.e. every subsection has only its header and blank lines below it.

If the `[Unreleased]` block is empty, STOP with this exact error:
```
Error: CHANGELOG.md [Unreleased] block is empty — no entries under any subsection.
There is nothing to release. Add CHANGELOG entries on develop first, then re-run /release-prep.
```

Do not make any commit or branch in this case.

## Step 4 — Determine the previous version

The previous version is the version currently in `pyproject.toml` — the version
that is about to be replaced by the cut. Read it now:
```bash
grep -E '^version = "' pyproject.toml | head -1
```
Parse the value out of `version = "<prev>"` and store it as `PREV_VERSION`
(e.g. `0.7.2`). This is used to build the CHANGELOG compare links in Step 5.

If no `version = "..."` line is found under `[project]`, stop and print a clear
error naming what was found.

## Step 5 — Promote the CHANGELOG

Create the feature branch first so all edits land on it:
```bash
git switch -c feature/release-prep-<version>
```
On failure (branch already exists, unexpected error), stop and print the raw
git error.

Capture the system date at run time in `YYYY-MM-DD` form:
```bash
date +%F
```
Store it as `RELEASE_DATE`.

Now edit `CHANGELOG.md` with the Edit tool. Make the following four changes.
The CHANGELOG uses an **em-dash** (`—`, U+2014) between version and date, e.g.
`## [0.7.2] — 2026-05-28`; match that exactly.

**5.1 — Promote the heading and insert a fresh empty [Unreleased].**

The current top of the changelog body looks like:
```
## [Unreleased]

### Added

### Changed

### Fixed

## [<PREV_VERSION>] — <prev date>
```
Replace the `## [Unreleased]` heading region with a fresh empty `[Unreleased]`
block followed by the promoted heading, so it becomes:
```
## [Unreleased]

### Added

### Changed

### Fixed

## [<version>] — <RELEASE_DATE>

### Added

### Changed

### Fixed
```
Concretely: rename the existing `## [Unreleased]` to
`## [<version>] — <RELEASE_DATE>` (keeping all its existing entries), then insert
a new `## [Unreleased]` block with empty `### Added` / `### Changed` / `### Fixed`
subsections above it. Do NOT discard or reword any of the existing `[Unreleased]`
entries — they move verbatim into the `[<version>]` section.

Use the Edit tool anchored on the exact text from the file (`## [Unreleased]`
through the blank line before the previous `## [` heading). If your single
anchor cannot be made unique, do it in two Edit calls: one to rename the heading,
one to insert the fresh `[Unreleased]` block above it.

**5.2 — Retarget the [Unreleased] compare link.**

At the bottom-of-file link list, the existing line reads:
```
[Unreleased]: https://github.com/DocGerd/hangarfit/compare/v<PREV_VERSION>...HEAD
```
Edit it to:
```
[Unreleased]: https://github.com/DocGerd/hangarfit/compare/v<version>...HEAD
```

**5.3 — Append the new version compare link.**

Immediately under the `[Unreleased]:` link line, insert a new line:
```
[<version>]: https://github.com/DocGerd/hangarfit/compare/v<PREV_VERSION>...v<version>
```
The result is the `[Unreleased]:` link followed by the new `[<version>]:` link,
followed by the previous version's link, preserving descending order.

**5.4 — Verify the edits.**

Re-read the relevant regions of `CHANGELOG.md` and confirm:
- A `## [<version>] — <RELEASE_DATE>` heading now exists, carrying the entries
  that were under `[Unreleased]`.
- A fresh empty `## [Unreleased]` block sits above it.
- The bottom link list contains both
  `[Unreleased]: …/compare/v<version>...HEAD` and
  `[<version>]: …/compare/v<PREV_VERSION>...v<version>`.

If any of these is not true, stop and print what you found — do not commit.

## Step 6 — Doc-freshness audit prompt

Print this checklist **verbatim** and wait for the user to reply:
```
Doc-freshness audit
===================
These docs are NOT auto-edited. Please check each one against the
current shipped state on develop, then edit any stale items on this
prep branch BEFORE the PR opens.

[ ] README.md       — Status section, Scope (any "still out of scope"
                      claims that have shipped), Usage examples
[ ] SECURITY.md     — supported-versions table, scope/threat-surface
[ ] CLAUDE.md       — Status line, Quick Reference
[ ] docs/architecture/ — any "Phase N (in progress)" markers

Type DONE when you've checked + edited as needed, or ABORT to bail.
```

Wait for user input. If the user edits doc files during this step, those edits
stay on the feature branch and will be included in the Step 7 commit.

- On exactly `DONE`: proceed to Step 7. Re-run `git status --porcelain` so any
  user doc edits are picked up by the commit.
- On exactly `ABORT` (or any response other than `DONE`): bail cleanly. Discard
  the CHANGELOG edits and return to `develop` so no commit is made:
  ```bash
  git checkout -- CHANGELOG.md
  git switch develop
  git branch -D feature/release-prep-<version>
  ```
  Then print:
  ```
  Aborted. No commit was made; the prep branch was removed.
  ```
  Stop here.

## Step 7 — Commit

Stage the CHANGELOG and any doc files the user edited:
```bash
git add -A
```
Commit with a single conventional-commit message:
```bash
git commit -m "$(cat <<'EOF'
docs(release): prep v<version> — promote CHANGELOG, refresh docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```
Substitute the real `<version>` into the message before running. Capture and
print the commit SHA (`git rev-parse --short HEAD`).

If `git commit` fails (e.g. a pre-commit hook), stop, print the raw error, and
inform the user the prep branch and edits exist locally but are uncommitted.

## Step 8 — Determine milestone number

Look up the milestone so it can be set on the PR:
```bash
gh api repos/DocGerd/hangarfit/milestones?state=all
```
Parse the JSON for a milestone whose `title` contains `v<version>` (e.g.
`v0.7.3`). If found, store the numeric `number` as `MILESTONE_NUMBER`. If none
matches, set `MILESTONE_NUMBER` to `<unresolved>` and note in the PR body that
the milestone must be set by hand — do not abort.

## Step 9 — Push and open the PR into develop

Push the branch:
```bash
git push -u origin feature/release-prep-<version>
```
On failure, stop and print the raw git error plus the retry command:
```
The commit was created locally. To retry the push manually:
  git push -u origin feature/release-prep-<version>
```

Open the PR — base is **`develop`** (never `main`, never a release branch):
```bash
gh pr create \
  --base develop \
  --head feature/release-prep-<version> \
  --title "docs(release): prep v<version>" \
  --body "$(cat <<'PREOF'
## Release prep for v<version>

Promotes the CHANGELOG `[Unreleased]` block to `## [<version>] — <RELEASE_DATE>`
and refreshes any stale docs ahead of the release cut, so the doc work gets a
focused review surface separate from the release-state PR.

After this PR merges into `develop`, run `/release-cut version=<version>` to
create the actual release branch and PRs.
PREOF
)" \
  --assignee DocGerd \
  --label documentation \
  --milestone <MILESTONE_NUMBER>
```
If `MILESTONE_NUMBER` is `<unresolved>`, omit the `--milestone` flag and note in
the PR body that the milestone must be set by hand.

Capture and print the URL returned by `gh pr create`.

**Preferred pattern**: set `--assignee`, `--label`, `--milestone` at creation
time. If metadata needs to change afterward, use the GitHub Issues API endpoint
(PRs share the issue number):
```bash
gh api -X PATCH repos/DocGerd/hangarfit/issues/<PR_NUMBER> \
  -F milestone=<MILESTONE_NUMBER> \
  -f 'assignees[]=DocGerd'
```
Do NOT use `gh pr edit --milestone` / `gh pr edit --assignee` — those flags are
broken in this repo.

## Step 10 — Print summary and the cut reminder

Print:
```
Release prep complete.

Prep PR (→ develop): <prep-pr-url>
Commit: <short-sha>
CHANGELOG: [Unreleased] promoted to [<version>] — <RELEASE_DATE>

Next steps (in order):
1. Get the prep PR reviewed and merge it into 'develop' first.
2. Then run: /release-cut version=<version>

/release-cut will refuse to run until this prep PR is merged — it checks that
CHANGELOG.md has a [<version>] section and compare link before cutting.
```

## Failure modes

Every abort condition in one place. In all cases, stop immediately and print the
described message. The success summary (Step 10) only runs after every step above
has succeeded.

1. **`version` argument missing**: print `Error: version is required and must match X.Y.Z (digits only, e.g. 0.1.0).`
2. **`version` does not match `^[0-9]+\.[0-9]+\.[0-9]+$`**: print the same error as above, quoting the invalid value received.
3. **Unknown argument key**: print a clear error naming the unrecognised key.
4. **Not on `develop`**: print `Error: must be on the 'develop' branch to prep a release. Current branch is '<current>'.`
5. **Dirty working tree**: print `Error: working tree is not clean.` followed by `git status --porcelain` output.
6. **Behind `origin/develop`**: print `Error: local 'develop' is behind 'origin/develop' by N commit(s).`
7. **Release branch already exists (local or remote)**: print the matching "release appears to be cut already" error from Check D.
8. **`[Unreleased]` block empty**: print the exact Step 3 error and make no branch or commit.
9. **`pyproject.toml` version line not found**: print a clear error naming what was found; make no branch.
10. **`git switch -c` fails**: print the raw git error.
11. **CHANGELOG edit verification fails (Step 5.4)**: print what was found; do not commit.
12. **User does not confirm with `DONE` (Step 6)**: discard edits, remove the prep branch, print `Aborted. No commit was made; the prep branch was removed.`
13. **`git commit` fails**: print the raw git error; the branch and edits exist locally but uncommitted.
14. **Milestone not found**: set `MILESTONE_NUMBER` to `<unresolved>`, omit `--milestone`, note in the PR body; do not abort.
15. **`git push` fails**: print the raw git error plus the retry command.
16. **`gh pr create` fails**: print the raw error; the branch has been pushed but no PR was opened.
