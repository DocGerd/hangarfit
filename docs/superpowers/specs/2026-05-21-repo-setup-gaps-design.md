> **Archived 2026-05-22 — historical record.** The six issues this spec plans have all shipped:
> **T1 = #56** (ruff), **T2 = #57** (mypy), **T3 = #55** (coverage in CI), **T4 = #53** (pin Actions SHAs),
> **T5 = #54** (OpenSSF Scorecard), **T6 = #52** (repo topics).
> All six CLOSED; underlying PRs all merged. Milestone "Going public" (#5) is closed.
> Placeholders `#T1..#T6` below are intentionally left intact — use the legend above to resolve them.
> Archive PR-tracked by #77.

# Repo-setup gaps — filing plan (T1–T6)

**Status:** draft 2026-05-21
**Tracks:** new issues to file into milestone [Going public (#5)](https://github.com/DocGerd/hangarfit/milestone/5)
**Author:** Claude (Opus 4.7), reviewed by @DocGerd
**Brainstorm constraint:** "cheap, high-value gaps only" — speculative items deferred.

This spec defines the six GitHub issues to file as a follow-on to the existing "Going public" backlog (#17–#27, #48). It is **a filing plan**, not an implementation plan for the underlying work. Each issue body in section 3 is paste-ready; doing the actual work (configuring ruff, wiring mypy, etc.) is downstream — those tasks become individual feature branches once the issues exist.

---

## 1. Goals & non-goals

**Goals**

- File six new issues (T1–T6) in milestone "Going public," each with the metadata pattern used by #17–#27/#48 (assignee `DocGerd`, appropriate label, paste-ready body).
- Encode the two real dependency edges structurally so they're queryable: `#27 ← T1` and `#27 ← T2` via GraphQL `addBlockedBy`.
- Update the milestone description to slot the seven new items (T1–T6 plus #48) into the existing P1/P2/P3 buckets.
- Leave the milestone in a state where each new issue is self-contained: an outside contributor or a future Claude session can pick any one up without needing this spec for context.

**Non-goals**

- **Not** implementing T1–T6. No `pyproject.toml` edits, no new workflow files, no codebase formatting passes — those each get their own feature branch driven by the filed issue.
- **Not** re-prioritising the existing #17–#27/#48 items. The P1/P2/P3 buckets stand; the new items slot into the existing structure.
- **Not** filing Tier-2 items (T7–T10) or Tier-3 fence items (F1–F3) from the brainstorm. Explicitly deferred per user decision.
- **Not** creating a `security` or `tooling` label. User declined label-as-code; we reuse the existing `enhancement` / `documentation` / `good first issue` set.

---

## 2. The six gaps (one-line summaries)

| ID | Title to file | Label(s) | Bucket |
|---|---|---|---|
| T1 | Adopt `ruff` for lint and format | `enhancement` | P3 |
| T2 | Adopt `mypy` for type checking | `enhancement` | P3 |
| T3 | Add coverage measurement to CI | `enhancement` | P2 |
| T4 | Pin GitHub Actions to commit SHAs | `enhancement`, `good first issue` | P2 |
| T5 | Add OpenSSF Scorecard workflow | `enhancement` | P2 |
| T6 | Set GitHub repository topics (and refresh description) | `documentation` | P1 |

All six: milestone = `Going public`, assignee = `DocGerd`.

---

## 3. Paste-ready issue bodies

Each subsection is the verbatim body to pass to `gh issue create --body`. They are written stand-alone — none of them reference this spec.

**Before filing, substitute the `#T1`–`#T6` placeholder references for the real issue numbers.** Per §6, T6 is filed first; subsequent bodies that cross-reference it can use the number assigned at filing time. The bodies are designed so the only edit needed is a `sed`-style swap.

### 3.1 — T1: Adopt `ruff` for lint and format

```markdown
## Goal

Adopt [ruff](https://docs.astral.sh/ruff/) as the single tool for Python linting and formatting. Configure it in `pyproject.toml`, run it once across the codebase, and wire it into CI as a check.

## Why

- `pyproject.toml` currently configures no linter or formatter. Style drift is a question of when, not if.
- `ruff` is the de facto Python standard in 2025-2026; it consolidates `flake8`, `isort`, `pyupgrade`, and `black` into one fast Rust binary.
- **Precondition for #27 (pre-commit hooks).** The pre-commit story has nothing to run without a linter/formatter configured first.

## Scope

- Add `[tool.ruff]` section to `pyproject.toml`. Suggested starting config: line length 100, target `py311`, rule set `["E", "F", "I", "B", "UP", "SIM"]`. The `B` (bugbear) and `UP` (pyupgrade) rules are the highest-value catches; `I` replaces `isort`. Keep it conservative initially — easier to opt in to more rules than to backtrack.
- Add `ruff` to `[project.optional-dependencies].dev`.
- Run `ruff format` once across `src/` and `tests/`; commit the formatting pass as the first commit on the PR so subsequent commits are reviewable.
- Add two CI steps to the existing `test` job (or a new `lint` job — either works; single job is simpler):
  - `ruff check .`
  - `ruff format --check .`
- Update `CLAUDE.md` "Useful commands" section to mention `ruff check` / `ruff format`.

## Out of scope

- Pre-commit hook integration (that's #27; this issue is the precondition).
- Aggressive rule sets (`ANN`, `D`, `PL`, etc.) — start conservative; expand later.

## Verification

- `ruff check .` returns clean.
- CI passes on the PR.
- `git diff` of the formatting commit is reviewable (no semantic changes mixed in).

## Related

- Blocks #27 (pre-commit hooks).
- Sibling of #T2 (mypy) — same "code quality tooling" tranche.
```

---

### 3.2 — T2: Adopt `mypy` for type checking

```markdown
## Goal

Adopt [mypy](https://mypy.readthedocs.io/) as the type checker for `src/hangarfit/`. Configure in `pyproject.toml`, fix existing type errors, and add to CI.

## Why

- `models.py` leans heavily on frozen dataclasses with hand-enforced invariants (cart rule, `movement_mode` ↔ `on_carts`, maintenance plane membership). Type safety is load-bearing in this codebase, not cosmetic.
- The geometry transform in `geometry.py` is precisely the kind of code where a type error catches the determinant-−1 sign-flip trap that CLAUDE.md and the `geometry-invariant-guard` subagent already exist to guard against. mypy is another layer of defense.
- **Precondition for #27 (pre-commit hooks).** The pre-commit type-check hook needs mypy configured first.

## Scope

- Add `[tool.mypy]` section to `pyproject.toml`. Suggested starting config:
  - `python_version = "3.11"`
  - `strict_optional = true`
  - `disallow_untyped_defs = true` for `src/hangarfit/`
  - `disallow_untyped_defs = false` for `tests/` (test code stays low-ceremony)
  - `warn_unused_ignores = true`
  - `warn_redundant_casts = true`
- Add `mypy` to `[project.optional-dependencies].dev`.
- Annotate any currently-unannotated public functions in `src/hangarfit/`.
- Add CI step: `mypy src/`.
- Update `CLAUDE.md` "Useful commands" to mention `mypy src/`.

## Out of scope

- Aggressive flags: `strict = true`, `disallow_any_explicit`, etc. — start conservative.
- Type-stubbing third-party libraries that lack stubs (`shapely`, `matplotlib`): use `# type: ignore[import-not-found]` at the import site or `ignore_missing_imports` per-module; do not write stubs ourselves.
- Pre-commit integration (#27).

## Verification

- `mypy src/` returns clean.
- CI passes on the PR.

## Related

- Blocks #27 (pre-commit hooks).
- Sibling of #T1 (ruff) — same "code quality tooling" tranche.
- Reinforces the `geometry-invariant-guard` subagent's coverage of `geometry.py`/`collisions.py`.
```

---

### 3.3 — T3: Add coverage measurement to CI

```markdown
## Goal

Measure test coverage in CI and publish the result so coverage regressions are visible on every PR.

## Why

- The CI workflow currently has a comment that explicitly says *"No coverage gate yet"* (`.github/workflows/ci.yml`).
- The test suite is substantial (242 tests at v0.1.0). Without coverage tracking, new geometry or collision code can land without tests and nobody notices until a fixture breaks months later.
- This issue adds **measurement and reporting only**, not a hard gate. The gate (a `fail_under=X` threshold) can be a follow-up once we know the natural floor on this codebase.

## Scope

- Add `pytest-cov` to `[project.optional-dependencies].dev`.
- Update CI test step to `pytest --cov=hangarfit --cov-report=xml --cov-report=term-missing`.
- Upload XML to Codecov via `codecov/codecov-action` (free for public repos; no token needed). Pin to a commit SHA (#T4 will set the project-wide pattern; if T4 lands first, follow that pattern; if not, pin defensively from day one — Codecov action tokens have been compromised before).
- Add Codecov badge to `README.md` (under the existing CI badge).

## Out of scope

- Coverage gate (`fail_under=X`). File as a follow-up once we have a baseline.
- Branch coverage (line coverage is the typical default; branch can be a follow-up).
- Per-PR coverage delta comments. Codecov does this automatically once linked; no extra config needed.

## Verification

- CI logs show coverage summary table.
- Codecov dashboard shows the repo and a coverage percentage.
- README badge renders.

## Related

- Sibling of #T1, #T2 — "code quality tooling" tranche.
- Sibling of #22 (Dependabot) — if T4 (pinned Action SHAs) is in place by the time this lands, the Codecov action gets pinned too.
```

---

### 3.4 — T4: Pin GitHub Actions to commit SHAs

```markdown
## Goal

Replace version-tag references (`@v4`, `@v5`) for third-party GitHub Actions with full commit SHAs, with the version tag preserved as a trailing comment so Dependabot can identify and bump them.

## Why

- The CI workflow currently uses `actions/checkout@v4` and `actions/setup-python@v5`. Version tags are mutable — a compromised maintainer or a typosquat can swap what `v4` resolves to without any commit on our side.
- GitHub's own [hardening guide for third-party actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions#using-third-party-actions) and the [OpenSSF Scorecard `Pinned-Dependencies` check](https://github.com/ossf/scorecard/blob/main/docs/checks.md#pinned-dependencies) both call this out as the supply-chain best practice for public repos.
- Once #22 (Dependabot) is configured with `package-ecosystem: github-actions`, Dependabot auto-bumps pinned SHAs (uses the `# v4.x.y` trailing comment as the version hint). Maintenance cost after this change: zero.

## Scope

- For every `uses:` line in every workflow under `.github/workflows/` referencing a non-GitHub-org action, replace `@<tag>` with `@<sha>  # <tag>`. The SHA is the full 40-character hash of the tagged commit (look it up via `gh api /repos/<owner>/<repo>/git/refs/tags/<tag>` or the action's release page).
- Actions in scope right now: `actions/checkout@v4`, `actions/setup-python@v5` (in `.github/workflows/ci.yml`).
- If #T3, #T5, #23, etc. have landed before this and added more `uses:` lines, pin those too in the same PR.

## Out of scope

- First-party `actions/*` Actions are still third-party from a supply-chain perspective — also pin them. Do not exempt the `actions/` org.
- Pinning the runner image (`ubuntu-latest` → `ubuntu-22.04`). That's a separate trade-off (stability vs. always-current) and not what this issue is about.

## Verification

- `grep -rE '@v[0-9]' .github/workflows/` returns nothing (no version-tag-only refs left).
- CI runs green after the swap.
- Once #22 ships, the first Dependabot PR should target an Actions SHA bump — that's the live confirmation the trailing-comment convention works.

## Related

- Sibling of #22 (Dependabot — closes the maintenance loop).
- Sibling of #T5 (OpenSSF Scorecard — Scorecard's `Pinned-Dependencies` check goes from FAIL to PASS once this lands).
```

---

### 3.5 — T5: Add OpenSSF Scorecard workflow

```markdown
## Goal

Add the [OpenSSF Scorecard](https://github.com/ossf/scorecard) workflow to publish a weekly security-posture score for the repo. Surface the result in the GitHub Security tab and (optionally) as a README badge.

## Why

- Scorecard is the standard "we take supply-chain security seriously" signal for public Python repos alongside CodeQL (#23) and Dependabot (#22).
- It evaluates 19 checks (branch protection, pinned deps, signed releases, dangerous workflows, etc.) and produces a score. Most of those checks are things we've already done (branch protection #16, tag protection #48) or are doing (#22, #23, #T4) — Scorecard makes the work visible.
- Free, no third-party signup; uploads SARIF to the GitHub Security tab.

## Scope

- Add `.github/workflows/scorecard.yml` based on the official [Scorecard starter workflow](https://github.com/actions/starter-workflows/blob/main/code-scanning/scorecards.yml).
- Schedule: weekly cron + on push to `main`. Do **not** run on pull_request — Scorecard needs `id-token: write` and `security-events: write` permissions that are inappropriate for forked-PR contexts.
- Pin every Action in the new workflow to a commit SHA (follow the #T4 convention; if T4 hasn't landed yet, do it here anyway — Scorecard would flag itself otherwise).
- Optional: add the Scorecard badge to `README.md`.

## Out of scope

- Setting a score threshold / failing the build on score regressions. Reporting only in v1.
- Enabling every Scorecard check; defaults are fine.
- Acting on every finding immediately — read the first run and triage in a follow-up issue if interesting items surface.

## Verification

- Workflow appears in Actions tab; first run completes (manual trigger or wait for cron).
- Score visible in GitHub Security tab → Code scanning alerts.
- (Optional) README badge renders with a score.

## Related

- Sibling of #22 (Dependabot), #23 (CodeQL), #16 (branch protection), #48 (tag protection), #T4 (pinned Action SHAs) — Scorecard scores the cumulative effect of this whole tranche.
```

---

### 3.6 — T6: Set GitHub repository topics (and refresh description)

```markdown
## Goal

Make the public repo discoverable from GitHub topic search by setting repository topics. Optionally refresh the description to drop the "Phase 1" qualifier now that v0.1.0 has shipped.

## Why

- `gh repo view DocGerd/hangarfit --json repositoryTopics` currently returns `repositoryTopics: null`. The repo is invisible to anyone browsing GitHub by topic (`https://github.com/topics/<topic>`).
- Topics also feed GitHub's recommendation surfaces and federated tag search (e.g., `lib.rs` for Rust has an equivalent in `pypi.org/search` indirectly via topic crawls).
- The current description mentions "(Phase 1)" — accurate at v0.1.0 ship but will look stale as Phase 2 lands. Cheap to fix at the same time.

## Scope

**Topics (the real change):** set to

```
python, aviation, flying-club, matplotlib, shapely, geometry, cli, collision-detection
```

(GitHub allows up to 20; this is 8 high-signal terms. All are lowercase, hyphen-separated, ≤50 chars per the topic format rules.)

**Description (optional polish):** consider rewriting from

> Helper tool for arranging the flying club fleet in a stack-style hangar — collision checker + visualizer (Phase 1)

to

> On-demand exception tool for arranging a flying club's fleet in a stack-style hangar — collision checker, visualizer, and CLI.

(Drops the phase qualifier; adds "CLI" which now exists; preserves the "on-demand exception" framing from the README's opening line.)

## How

Manual via GitHub UI (Settings → General → Topics + Description), **or** via `gh repo edit DocGerd/hangarfit --add-topic python --add-topic aviation ...` and `gh repo edit --description "..."`. Either is fine; no code change.

## Out of scope

- A custom social-preview image (`/settings#social-preview`). Separate concern, separate issue if wanted.
- Setting a homepage URL. README + GitHub repo page are the canonical entry points right now.

## Verification

- `gh repo view DocGerd/hangarfit --json repositoryTopics,description` returns the new values.
- Repo appears at `https://github.com/topics/aviation` (and the other topic pages).

## Related

- Stands alone; not blocking anything else in the milestone.
```

---

## 4. Dependency edges

Two structural `blocked-by` edges to encode via the GitHub GraphQL `addBlockedBy` mutation (per [[feedback_encode_dependencies_in_tickets]] — use the mutation, not prose in the body):

| Blocked issue | Blocked by |
|---|---|
| #27 (pre-commit hooks) | T1 (ruff) |
| #27 (pre-commit hooks) | T2 (mypy) |

Confirmed-working call shape (from the memory record):

```bash
# 1. Fetch node IDs for #27 and the new T1/T2 issues:
gh api graphql -f query='{ repository(owner:"DocGerd", name:"hangarfit") { issue(number: 27) { id } } }'
gh api graphql -f query='{ repository(owner:"DocGerd", name:"hangarfit") { issue(number: <T1-number>) { id } } }'
gh api graphql -f query='{ repository(owner:"DocGerd", name:"hangarfit") { issue(number: <T2-number>) { id } } }'

# 2. Run the mutation once per edge:
gh api graphql -f query='mutation { addBlockedBy(input: {issueId: "<#27-node-id>", blockingIssueId: "<T1-node-id>"}) { issue { number blockedBy(first: 5) { nodes { number } } } } }'
gh api graphql -f query='mutation { addBlockedBy(input: {issueId: "<#27-node-id>", blockingIssueId: "<T2-node-id>"}) { issue { number blockedBy(first: 5) { nodes { number } } } } }'
```

Inverse is `removeBlockedBy(input: {issueId, blockingIssueId})` if a fix-up is needed.

No other `blocked-by` edges. T4 ↔ #22 and T5 ↔ #22/#23 are **additive siblings**, not blockers — encoded as "Related" mentions in the bodies above, not as structural edges.

---

## 5. Milestone description update

Replace the current "Going public" milestone description with the version below (substituting real issue numbers in for T1–T6 once filed):

> Repo hygiene for outside contributors. Continues Sprint A (#15 CI + #16 branch protection, both merged).
>
> - **P1 — external surface:** #17 (CODEOWNERS), #18 (issue templates), #19 (PR template), #20 (CONTRIBUTING), #T6 (repo topics + description).
> - **P2 — security + CI hardening:** #21 (SECURITY), #22 (Dependabot), #23 (CodeQL), #48 (tag protection ruleset), #T3 (coverage in CI), #T4 (pinned Actions SHAs), #T5 (OpenSSF Scorecard).
> - **P3 — developer-experience polish:** #24 (CoC), #25 (CHANGELOG), #26 (.editorconfig), #T1 (ruff), #T2 (mypy), #27 (pre-commit; blocked-by #T1 + #T2).

Update via:

```bash
gh api -X PATCH /repos/DocGerd/hangarfit/milestones/5 -f description='<new description>'
```

(`gh edit` is broken in this repo per [[feedback_pr_metadata]]; the `gh api -X PATCH` form is the working substitute.)

---

## 6. Filing order

T6 → T4 → T5 → T3 → T1 → T2.

Reasoning:

1. **T6** — cheapest, no code change, instant external visibility win. Useful to file first so it doesn't slip.
2. **T4 → T5** — the two security-tranche items, filed adjacent so they live next to each other in the issue list and pair naturally with the existing #22/#23/#48.
3. **T3** — the CI-hardening item, sandwiched between security and the precondition pair so it doesn't get buried.
4. **T1 → T2** — the two #27-precondition items, filed last so they end up adjacent to #27 in numbering. Encode the two `blocked-by` edges on #27 right after T2 is filed.

After all six are filed:

1. Run two `addBlockedBy` (or equivalent — see §4) GraphQL mutations on #27.
2. `gh api -X PATCH /repos/DocGerd/hangarfit/milestones/5 -f description='...'` to install the new milestone description with real numbers.
3. `gh issue list --milestone "Going public"` as the final sanity print.

---

## 7. Out of scope (for this filing pass, by explicit user decision)

Tier-2 brainstorm items deferred:

- T7 visual regression test for the visualizer (golden PNG)
- T8 Hypothesis property-based tests for the geometry transform
- T9 devcontainer for Codespaces
- T10 build sdist + wheel on tag and attach to GitHub Releases

Tier-3 "fence" items deferred:

- F1 cross-OS CI matrix
- F2 aviation glossary
- F3 CLI snapshot tests

Items the user filtered out at the scoping question (NOT recommended even on a full sweep):

API docs site (Sphinx/mkdocs/pdoc), All-Contributors, GitHub Discussions / Wiki, Makefile / justfile, stale-issue bot, performance benchmarks, release-please automation, dev-dep lockfile, `harden-runner` egress monitoring, label-as-code (`.github/labels.yml`), funding.yml.

Any of these can be revisited in a future brainstorm if circumstances change.
