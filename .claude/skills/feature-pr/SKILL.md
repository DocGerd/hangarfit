---
name: feature-pr
description: This skill should be used when the user invokes "/feature-pr" to take committed feature work through hangarfit's per-PR GitFlow arc — branch off develop, open a DRAFT PR that closes its tracking issue, run the delegated review (pr-review-toolkit:review-pr), convert each finding into an inline thread, resolve every thread, and flip the PR to ready. Accepts issue=<N>, slug=<kebab-slug>, and optional title="...". Confirmation gate before the push/PR-create; NEVER merges (the user is the sole merger).
disable-model-invocation: true
argument-hint: issue=<N> slug=<kebab-slug> [title="<PR title>"]
---

Drive committed feature work through the hangarfit per-PR review arc (CLAUDE.md §Branching / §Per-PR process / §Issues). This skill manages the PR *lifecycle* — it does **not** write the feature; the implementation is already committed (or staged) on your branch before you invoke it. It **delegates the review** to the `pr-review-toolkit:review-pr` skill; it never reimplements a review checklist, and it **never merges**.

**Arguments from the invocation**: $ARGUMENTS

## Step 1 — Parse and validate arguments

Parse named arguments from `$ARGUMENTS` using shell-style (shlex-like) tokenization: a double-quoted value is a single token even with spaces. Unknown keys (anything other than `issue`, `slug`, `title`) are an error — stop and name the unrecognised key.

| Arg | Required | Valid values |
|-----|----------|--------------|
| `issue` | yes | a positive integer — the tracking issue number this PR closes |
| `slug` | yes | kebab-case identifier matching `^[a-z0-9][a-z0-9-]*$` (lowercase letters, digits, hyphens) |
| `title` | no | PR title string; if omitted, derive from the issue title (Step 2) |

If any argument is missing or invalid, stop immediately and print a clear error naming the failing argument and its expected format. Do NOT proceed.

## Step 2 — Verify the tracking issue, and confirm there is committed work

"Every change is tracked by a GitHub issue. No code without an issue."

1. Confirm the issue exists and read its title (for a default PR title and the branch name):
   ```bash
   gh api repos/DocGerd/hangarfit/issues/<issue> --jq '{number,title,state}'
   ```
   Use the REST API, not `gh issue view` — the latter can fail with a Projects-classic `repository.issue.projectCards` deprecation error. If the issue does not exist or is closed, stop and report it.
2. Confirm there is committed work to open a PR for:
   ```bash
   git fetch origin develop -q
   git rev-list --count origin/develop..HEAD    # must be >= 1
   ```
   If `0`, stop and print: `No commits ahead of origin/develop. Commit your feature work before running /feature-pr.`

## Step 3 — Ensure you are on `feature/<slug>` off `develop`

- If the current branch is `develop` (or any protected branch), create the feature branch: `git switch -c feature/<slug>` — but only if there are no uncommitted changes that belong elsewhere. NEVER work or push directly on `develop`/`main`.
- If already on a `feature/*` branch, verify it is based on `develop`:
  ```bash
  git merge-base --is-ancestor origin/develop HEAD || echo "BRANCH IS BEHIND develop"
  ```
  If behind, run `git merge origin/develop` (NEVER rebase — no force-push) before continuing.
- **Stacking note:** if this feature shares files with an unmerged sibling, base this branch on `develop` anyway (never on the parent feature branch) — a feature-branch-base PR gets no CI run and no `Closes #N` linkage. Accept the cumulative diff until parents merge.

## Step 4 — CHANGELOG entry (user-facing changes only)

Each **user-facing** change carries its own `CHANGELOG.md [Unreleased]` entry (`### Added` / `### Changed` / `### Fixed`). Before opening the PR, confirm one of:
- the diff has a `CHANGELOG.md` entry for this change; **or**
- the change is **dev-tooling / docs-only** (e.g. `.claude/`, CI, internal refactor) — no entry needed (the "no user-facing behaviour change → no CHANGELOG entry" policy); **or**
- this is one of **≥2 PRs delivered in parallel** — then keep the entry OUT of each feature PR and collect them all in ONE separate CHANGELOG-only PR (so siblings don't cascade-conflict on `CHANGELOG.md`).

In CHANGELOG / PR prose, write a milestone number **bare** (`milestone 34`), never `#34` (a bare `#N` auto-links to PR/issue N in the rendered release notes).

## Step 5 — Confirmation gate (before any push or PR creation)

Print the plan and wait for confirmation:

```
About to open a DRAFT pull request:

[ ] 1. Push feature/<slug> to origin
[ ] 2. gh pr create --draft --base develop  (body: "Closes #<issue>")
       title:     <title or derived>
       assignee:  DocGerd
       labels:    <labels>
       milestone: <none | resolved title>

Confirm? Type YES to proceed, anything else to abort.
```

Wait for user input. Proceed only on exactly `YES` (case-sensitive). Any other response is an abort: print `Aborted. No changes were made.` and stop. Print `[x] N. <description>` as each step below completes.

## Step 6 — Open the PR as a DRAFT

```bash
git push -u origin feature/<slug>
gh pr create --draft --base develop \
  --title "<title>" \
  --body "Closes #<issue>

<short description of the change and how it was verified>" \
  --assignee DocGerd \
  --label <label>
```

- `Closes #<issue>` / `Fixes #<issue>` goes in the **body**, not the title (only body syntax auto-closes).
- A PR stays a **draft** until its review arc is clean — draft signals "not yet for the human's attention."
- Set `--assignee`/`--label`/`--milestone` at create time (these flags work). For a **milestone**, `gh pr create --milestone` wants the **title string** — look it up with `gh api 'repos/DocGerd/hangarfit/milestones?state=all&per_page=100'` (paginates at 30; a fresh milestone hides past page 1). If milestone assignment is the user's call, omit it.
- Post-creation metadata fixes go via REST PATCH (`gh pr edit --milestone`/`--assignee` are broken here): `gh api -X PATCH repos/DocGerd/hangarfit/issues/<PR_NUMBER> -F milestone=<NUMBER> -f 'assignees[]=DocGerd'` (the REST API wants the milestone **number**, not the title).

## Step 7 — Run the review arc (DELEGATED)

Invoke the **`pr-review-toolkit:review-pr`** skill (the `/pr-review` command) on this PR. Do NOT inline a review checklist — delegate. That skill runs the mandated `pr-review-toolkit:code-reviewer` main pass plus the applicable specialists; ensure the topical ones fire for what this PR touches:

- `pr-review-toolkit:comment-analyzer` — PRs that meaningfully change docs (README, CLAUDE.md, docstrings).
- `pr-review-toolkit:silent-failure-hunter` — loader / collision code.
- `pr-review-toolkit:type-design-analyzer` — `models.py`.
- `geometry-invariant-guard` — `geometry.py` / `collisions.py`.
- `determinism-guard` — `solver.py` / `towplanner.py`.
- `ml-rl-guard` — `ml/*.py` / `tests/ml/`.
- `scene-schema-guard` — `scene.py` / `viewer.py` / `viewer/src/*.ts`.

Review subagents must stay **read-only** in the shared checkout — point them at `origin/<branch>` refs (`gh pr diff <n>`); never `git switch`/`stash` in place.

## Step 8 — One inline review thread per finding

Convert **each** finding into its own review thread anchored on the diff line — never a lumped summary, never chat-only. For N≥5 findings, POST one `gh api repos/DocGerd/hangarfit/pulls/<n>/reviews` with a `comments[]` array, anchored on the original commit so threads show "outdated" after a fix.

## Step 9 — Resolve every thread

For each thread: **fix the code** (preferred — commit + push) or reply with rationale, then explicitly mark it resolved via the GraphQL `resolveReviewThread` mutation (a reply alone does NOT resolve; read threads via the `pullRequest.reviewThreads` GraphQL path). Leave an auditable trace ON the PR — a submitted review summary naming the subagents run and the outcome, even for a clean pass.

## Step 10 — Re-review if non-trivial, then flip to ready and hand off

- If the fixes were non-trivial, re-run Step 7.
- When the review arc is clean, flip the PR out of draft: `gh pr ready <n>`. You may do this even before CI finishes — readiness tracks the review arc, not the CI run.
- Tell the user the PR is **clean and ready for final review**, with its number/URL.

**NEVER merge.** Do not run `gh pr merge` — this includes `--auto` / enabling auto-merge, which counts as merging. Never arm it without the user's explicit per-PR go-ahead (`gh pr merge <n> --disable-auto` undoes a stray arm). The user is the sole approver and merger.

## Constraints

- Never push to `develop` / `main`; only via PR from `feature/*`.
- Never use `--no-verify`, `--force`, `-f`, or `--force-with-lease` unless the user explicitly asks. If a hook fails, fix the root cause.
- Never `git switch` / `checkout` / `stash` while a review subagent is reading the shared checkout.
- Never `gh pr merge` (incl. `--auto`).
- Open the PR as a **draft**; only `gh pr ready` once the review arc is clean.
- Findings live as inline threads on the diff, never only in chat.
- Use `gh api -X PATCH .../issues/<n>` for PR metadata — `gh pr edit --milestone`/`--assignee` are broken here.

## Failure modes

Every abort condition in one place. In all cases, stop immediately and print the described message. The Step 10 handoff only runs after every step above has succeeded.

1. **Missing / invalid argument** (`issue`, `slug`) or **unknown key**: print a clear error naming the failing argument and the expected format.
2. **Issue not found or closed**: print the issue number and its state; do not branch or push.
3. **No commits ahead of `origin/develop`**: print `No commits ahead of origin/develop. Commit your feature work before running /feature-pr.`
4. **On `develop`/`main` with uncommitted unrelated changes**: stop and ask the user to stash/relocate them; never push to a protected branch.
5. **Confirmation not exactly `YES`**: print `Aborted. No changes were made.`
6. **`gh pr create` / `git push` fails**: print the error text verbatim; do NOT proceed to the review arc.
