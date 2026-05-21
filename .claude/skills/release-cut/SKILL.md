---
name: release-cut
description: This skill should be used when the user invokes "/release-cut" to walk through the GitFlow release process. Accepts version=X.Y.Z. Dry-run by default — shows the full step plan and waits for user confirmation before any destructive action.
disable-model-invocation: true
argument-hint: version=<X.Y.Z>
---

Walk through the GitFlow release process for the hangarfit project.

**Arguments from the invocation**: $ARGUMENTS

## Step 1 — Parse and validate arguments

Parse the following named argument from `$ARGUMENTS`:

| Arg | Required | Valid values |
|-----|----------|--------------|
| `version` | yes | Semver-style string matching `^[0-9]+\.[0-9]+\.[0-9]+$` (digits only, e.g. `1.2.3`) |

Unknown keys (any key other than `version`) are an error — stop immediately and name the unrecognised key.

If `version` is missing or does not match `^[0-9]+\.[0-9]+\.[0-9]+$`, stop immediately and print a clear error:
```
Error: version is required and must match X.Y.Z (digits only, e.g. 0.1.0).
```

Do NOT proceed to any further step.

## Step 2 — Verify clean state

Run all three checks. If any fails, stop immediately and print the described message.

**Check A — current branch is `develop`:**
```bash
git rev-parse --abbrev-ref HEAD
```
Expected output: `develop`. If not, stop:
```
Error: must be on the 'develop' branch to cut a release. Current branch is '<current>'.
Switch with: git switch develop
```

**Check B — working tree is clean:**
```bash
git status --porcelain
```
Expected output: empty (no lines). If not, stop:
```
Error: working tree is not clean. Commit or stash the following changes before cutting a release:
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
```bash
git branch --list release/<version>
```
If the output is non-empty, stop:
```
Error: branch 'release/<version>' already exists locally. Delete it first or choose a different version.
```
Also check the remote:
```bash
git ls-remote --heads origin release/<version>
```
If the output is non-empty, stop:
```
Error: branch 'release/<version>' already exists on origin. Push to an existing branch is not allowed here.
```

## Step 3 — Produce the dry-run plan and wait for confirmation

Print the following checklist verbatim (substituting `<version>` throughout):

```
Release plan for v<version>
===========================

The following steps will be executed in order. No changes have been made yet.

[ ] 1. Create branch: git switch -c release/<version>
[ ] 2. Bump version in pyproject.toml: "0.0.1" → "<version>"
[ ] 3. Commit: chore(release): bump version to <version>
[ ] 4. Push: git push -u origin release/<version>
[ ] 5. Open PR into main:
         gh pr create \
           --base main \
           --head release/<version> \
           --title "release: v<version>" \
           --body "..." \
           --assignee DocGerd \
           --label enhancement \
           --milestone <milestone_number>
[ ] 6. Open back-merge PR into develop:
         gh pr create \
           --base develop \
           --head release/<version> \
           --title "chore: back-merge v<version> into develop" \
           --body "..." \
           --assignee DocGerd \
           --label enhancement \
           --milestone <milestone_number>
[ ] 7. Print both PR URLs.
[ ] 8. Remind you to tag v<version> AFTER the main PR merges.

NOTE: 'main' is protected — the skill never pushes to main directly.
NOTE: No 'release' label exists in this repo. Using 'enhancement' for both PRs.
      TODO: create a dedicated 'release' label in a future PR.
```

Then stop and print:
```
Confirm? Type YES to execute all steps, or anything else to abort.
```

**Wait for user input before proceeding.** Do not execute any command from the plan until the user replies with exactly `YES` (case-sensitive). Any other response (including `yes`, `y`, `Y`, `ok`, `sure`) must be treated as an abort:
```
Aborted. No changes were made.
```

## Step 4 — Determine milestone number

Before executing the plan, look up the correct milestone number via the GitHub API so it can be passed to `gh pr create --milestone`:

```bash
gh api repos/DocGerd/hangarfit/milestones?state=all
```

Parse the JSON to find the milestone whose `title` contains `"v<version>"` or, if that does not match, the milestone most likely to be the release cut target (e.g. `v0.1.0` when cutting `0.1.0`). If no milestone title matches, use the milestone named `v<version>` literally. If still nothing, leave milestone unset and note it in the PR body.

Store the numeric `number` field as `MILESTONE_NUMBER`.

## Step 5 — Execute each step in order

Execute the steps one by one. After each step, print `[x] N. <description>` to mark it complete. If any step fails, stop immediately, print the error, and print the list of steps that were NOT yet completed so the user knows the partial state.

### Step 5.1 — Create release branch

```bash
git switch -c release/<version>
```

On failure (branch already exists, unexpected error), stop and print the raw error.

### Step 5.2 — Bump version in pyproject.toml

Read the file `/home/pkuhn/hangarfit/pyproject.toml` (or the `pyproject.toml` in the current working directory, i.e. the repo root). Find the line matching:
```
version = "<anything>"
```
under the `[project]` section. Replace the version value with `<version>`.

Use the Edit tool with the exact old string, e.g.:
- `old_string`: `version = "0.0.1"`
- `new_string`: `version = "<version>"`

After editing, verify the change is minimal: re-read `pyproject.toml` and confirm exactly one line changed and the new value is `version = "<version>"`. If the Edit tool does not find a unique match (file was already at the new version, or multiple version lines exist), stop and print a clear error describing what was found.

### Step 5.3 — Commit

Stage the change:
```bash
git add pyproject.toml
```

Commit with a conventional-commit message:
```bash
git commit -m "$(cat <<'EOF'
chore(release): bump version to <version>

Refs #38
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

Capture and print the commit SHA (first 8 characters from `git rev-parse --short HEAD`).

### Step 5.4 — Push

```bash
git push -u origin release/<version>
```

On failure (network error, permission denied, branch already on remote), stop and print the raw error plus:
```
The commit was created locally. To retry the push manually:
  git push -u origin release/<version>
```

### Step 5.5 — Open PR into main (release PR)

Construct the PR body:

```
## Release v<version>

This PR merges the `release/<version>` branch into `main`, tagging the
v<version> release of hangarfit (Phase 1).

Milestone: v<version>

Closes #<release-issue-number-if-known>
```

If no release tracking issue is known, omit the `Closes` line.

Create the PR:
```bash
gh pr create \
  --base main \
  --head release/<version> \
  --title "release: v<version>" \
  --body "$(cat <<'PREOF'
## Release v<version>

This PR merges the `release/<version>` branch into `main`, tagging the
v<version> release of hangarfit.

**Do not merge until all checks pass and the release is confirmed ready.**

After merging, tag the release:
  git tag v<version> <merge-commit-sha>
  git push origin v<version>
PREOF
)" \
  --assignee DocGerd \
  --label enhancement \
  --milestone <MILESTONE_NUMBER>
```

Capture and print the URL returned by `gh pr create`.

If `gh pr create` fails, stop and print the raw error. Do not proceed to Step 5.6 because the user needs to review the failure first.

**Preferred pattern**: use `--assignee`, `--label`, `--milestone` flags at creation time. These flags work correctly in this repo.

**Post-creation metadata update (fallback only)**: if metadata needs to change after PR creation, use the GitHub Issues API endpoint (PRs share the issue number on GitHub):
```bash
gh api -X PATCH repos/DocGerd/hangarfit/issues/<PR_NUMBER> \
  -f milestone=<MILESTONE_NUMBER> \
  -f assignees='["DocGerd"]'
```
Do NOT use `gh pr edit --milestone` or `gh pr edit --assignee` — those flags are broken in this repo.

### Step 5.6 — Open back-merge PR into develop

Create the back-merge PR:
```bash
gh pr create \
  --base develop \
  --head release/<version> \
  --title "chore: back-merge v<version> into develop" \
  --body "$(cat <<'PREOF'
## Back-merge v<version> into develop

Merges the `release/<version>` branch back into `develop` to keep the
branches in sync after the release.

This PR should be merged AFTER the main release PR is merged.
PREOF
)" \
  --assignee DocGerd \
  --label enhancement \
  --milestone <MILESTONE_NUMBER>
```

Capture and print the URL returned by `gh pr create`.

### Step 5.7 — Print summary

Print:
```
Release cut complete.

PRs created:
  Release PR (→ main):   <release-pr-url>
  Back-merge PR (→ develop): <backmerge-pr-url>

Commit: <short-sha>

Next steps (in order):
1. Wait for CI checks to pass on both PRs.
2. Have the release PR reviewed and merged by the assignee (DocGerd).
3. After the main PR merges, tag the release:
     git fetch origin main
     git tag v<version> <merge-commit-sha-on-main>
     git push origin v<version>
4. Then merge the back-merge PR into develop.

IMPORTANT: Do NOT tag an unmerged commit. The tag must point to the actual
merge commit on main, not the tip of the release branch (which may be
rewritten by a squash-merge).
```

## Failure modes

Every abort condition in one place. In all cases, stop immediately and print the described message. The success summary (Step 5.7) only runs after every step above has succeeded.

1. **`version` argument missing**: print `Error: version is required and must match X.Y.Z (digits only, e.g. 0.1.0).`
2. **`version` does not match `^[0-9]+\.[0-9]+\.[0-9]+$`**: print the same error as above, quoting the invalid value received.
3. **Unknown argument key**: print a clear error naming the unrecognised key.
4. **Not on `develop`**: print `Error: must be on the 'develop' branch to cut a release. Current branch is '<current>'.`
5. **Dirty working tree**: print `Error: working tree is not clean.` followed by `git status --porcelain` output.
6. **Behind `origin/develop`**: print `Error: local 'develop' is behind 'origin/develop' by N commit(s).`
7. **Release branch already exists locally**: print `Error: branch 'release/<version>' already exists locally.`
8. **Release branch already exists on remote**: print `Error: branch 'release/<version>' already exists on origin.`
9. **User does not confirm (any response other than `YES`)**: print `Aborted. No changes were made.`
10. **`git switch -c` fails**: print the raw git error.
11. **`pyproject.toml` version line not found or not unique**: print a clear error describing what was found and halt before any write.
12. **`pyproject.toml` already at target version**: print `Error: pyproject.toml already has version = "<version>". Nothing to bump.`
13. **`git commit` fails**: print the raw git error. The branch has been created and pyproject.toml edited — inform the user of the partial state.
14. **`git push` fails**: print the raw git error plus the retry command.
15. **Release PR (`gh pr create --base main`) fails**: print the raw error. The branch has been pushed — inform the user the branch is on remote but no PRs were opened.
16. **Back-merge PR (`gh pr create --base develop`) fails**: print the raw error. The release PR URL has been printed — inform the user so they can open the back-merge PR manually.
17. **`main` branch is protected**: this is expected behaviour. The skill never pushes to `main` directly. If a push to `main` is accidentally attempted and rejected, that is a skill-implementation error — do not retry.
