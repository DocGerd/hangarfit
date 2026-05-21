# Contributing to hangarfit

Welcome, and thanks for looking at the code. `hangarfit` is a small, focused
tool — a collision checker for a flying club's hangar parking arrangements —
maintained by [DocGerd](https://github.com/DocGerd). The full design context,
coordinate conventions, and project philosophy live in [`CLAUDE.md`](CLAUDE.md).
Read that before touching anything geometric; it will save you a round-trip on
review.

---

## Issues first

Every change — bug fix, feature, docs update — starts with a GitHub issue. No
code without an issue, so there's a clear record of what was intended and why.

If you're reporting a bug, use the **Bug report** template. For a feature or
design question, use the **Feature request** template. Both live in
[`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/).

---

## Workflow (GitFlow)

`develop` is the integration branch; `main` is release-tagged and production.
**Never push directly to either.** All work lands via pull request.

The standard loop for a new feature or fix:

```bash
git switch develop && git pull
git switch -c feature/<slug>

# ... write code, add tests, commit ...

git push -u origin feature/<slug>
gh pr create --base develop --title "type(scope): short summary" --body "Closes #N ..."
```

Replace `<slug>` with something short and descriptive (e.g. `contributing-md`,
`fix-husky-strut-offset`). One branch per issue.

### Branch naming

| Prefix | Use for |
|---|---|
| `feature/<slug>` | New features and bug fixes (off `develop`) |
| `release/<version>` | Release cuts (off `develop`, PR'd into `main` + `develop`) |
| `hotfix/<slug>` | Urgent fixes (off `main`, back-merged into `develop`) |

---

## Commit style

Follow the conventional-commit pattern: `<type>(<scope>): <summary>`.

```
fix(fleet): correct fuselage offsets and verified-dimension placeholders
chore(release): bump version to 0.1.0
feat(claude-config): add /release-cut skill for GitFlow releases
```

Common types: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`. Keep the
summary under ~72 characters and written in the imperative mood.

---

## Pull request requirements

Use the PR template (`.github/pull_request_template.md`) when you open a PR.
It has a short checklist — fill it in honestly.

Before the PR can merge:

- All CI status checks must be green on Python 3.11 **and** 3.12
  (see `.github/workflows/ci.yml`).
- Tests added or updated for any behaviour change. New collision scenarios
  belong in `tests/fixtures/` as a YAML file, not as geometry literals in
  Python.

---

## Code review

The project uses the `pr-review-toolkit` — invoke it with `/pr-review` in
the Claude Code CLI. Reviewers file findings as **review threads on the diff**,
not as chat comments, so every issue is tied to the relevant line and has a
clear resolution state. Work through every open thread before asking for final
approval: either fix the code (preferred) or reply with a clear rationale and
mark the thread resolved.

If you're not using the Claude Code CLI, the same principle applies: all review
feedback should be in GitHub PR review comments, not in the general PR
conversation.

---

## Approval and merge

Merging is maintainer-only. Once your PR is green and all review threads are
resolved, post a comment saying it's ready for final review. Wait for DocGerd
to approve and merge. Don't run `gh pr merge` from your fork — it won't work
and it bypasses the maintainer's final check.

---

## Where the design lives

- [`CLAUDE.md`](CLAUDE.md) — the full spec: fleet details, the parts-based
  collision rule, the coordinate convention (including the non-obvious
  heading transform), and the Phase 1 deliverables list.
- [`README.md`](README.md) — quick-start, usage examples, and a pointer back
  to `CLAUDE.md` for depth.

If something in the codebase seems strange — especially around geometry — check
`CLAUDE.md` first. The coordinate transform in particular has a non-obvious
determinant-−1 property that is documented there and tested in the golden suite.
