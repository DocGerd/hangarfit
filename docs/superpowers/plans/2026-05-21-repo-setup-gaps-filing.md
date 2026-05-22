> **Archived 2026-05-22 — historical record.** This filing plan has been fully executed:
> **T1 = #56** (ruff), **T2 = #57** (mypy), **T3 = #55** (coverage in CI), **T4 = #53** (pin Actions SHAs),
> **T5 = #54** (OpenSSF Scorecard), **T6 = #52** (repo topics).
> All six issues filed; both `addBlockedBy` edges on #27 encoded; milestone description PATCH'd; underlying PRs all merged; milestone "Going public" (#5) is closed.
> Placeholders `$Tx` (bash variables) and `#Tx` (issue cross-refs) below are intentionally left intact — the plan documents the substitution itself, so erasing the placeholders would make the plan incoherent. Use the legend above to resolve them.
> Companion spec: `docs/superpowers/specs/2026-05-21-repo-setup-gaps-design.md`. Archive PR-tracked by #77.

# Repo-setup gaps (T1–T6) — filing plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** File six new "Going public" issues (T1 ruff, T2 mypy, T3 coverage, T4 pinned Actions SHAs, T5 OpenSSF Scorecard, T6 repo topics), encode the two `blocked-by` edges from #27 onto T1+T2, and rewrite the milestone description to slot the new items into the existing P1/P2/P3 buckets.

**Architecture:** GitHub-side work only — no code change in this repo. Five tasks, each a small batch of `gh` CLI invocations: (1) create the six issues with the paste-ready bodies from the spec; (2) substitute the `#T1..#T6` cross-reference placeholders in each body for the real numbers assigned by GitHub; (3) encode two `addBlockedBy` GraphQL mutations on #27; (4) PATCH the milestone description; (5) final sanity sweep.

**Tech Stack:** `gh` CLI (issue create, view, list), `gh api graphql` (node-ID lookups + `addBlockedBy` mutation), `gh api -X PATCH` for milestone and issue-body edits (per [[feedback_pr_metadata]], `gh edit` is broken in this repo).

**Working directory:** `/home/pkuhn/hangarfit` on `develop` (no worktree needed — nothing lands in the repo).

**Spec reference:** `docs/superpowers/specs/2026-05-21-repo-setup-gaps-design.md` is the source of truth. Disagreements between this plan and the spec should be resolved by updating the plan, not by ad-libbing.

**Number bookkeeping:** Each filing step captures the issue number assigned by GitHub. Record them in a scratch buffer for use in later tasks:

| Symbol | Issue | Assigned at Task 1, Step | Number |
|---|---|---|---|
| `$T6` | repo topics | 1 | _to fill_ |
| `$T4` | pinned Action SHAs | 2 | _to fill_ |
| `$T5` | OpenSSF Scorecard | 3 | _to fill_ |
| `$T3` | coverage in CI | 4 | _to fill_ |
| `$T1` | ruff | 5 | _to fill_ |
| `$T2` | mypy | 6 | _to fill_ |

When a later task references `$Tx`, substitute the captured number.

---

## Task 1: File the six issues with placeholder cross-references

**Files:** none (GitHub-side only).

Each step files one issue. Bodies are embedded verbatim in HEREDOCs, with `#T1..#T6` left as placeholders — those get substituted in Task 2. Filing order per spec §6: T6 → T4 → T5 → T3 → T1 → T2.

### Step 1: File T6 — repo topics

- [ ] **Step 1.1: Run `gh issue create` for T6**

```bash
gh issue create \
  --title "Set GitHub repository topics (and refresh description)" \
  --milestone "Going public" \
  --label "documentation" \
  --assignee "DocGerd" \
  --body "$(cat <<'EOF'
## Goal

Make the public repo discoverable from GitHub topic search by setting repository topics. Optionally refresh the description to drop the "Phase 1" qualifier now that v0.1.0 has shipped.

## Why

- `gh repo view DocGerd/hangarfit --json repositoryTopics` currently returns `repositoryTopics: null`. The repo is invisible to anyone browsing GitHub by topic (`https://github.com/topics/<topic>`).
- Topics also feed GitHub's recommendation surfaces and federated tag search.
- The current description mentions "(Phase 1)" — accurate at v0.1.0 ship but will look stale as Phase 2 lands. Cheap to fix at the same time.

## Scope

**Topics (the real change):** set to

`python, aviation, flying-club, matplotlib, shapely, geometry, cli, collision-detection`

(GitHub allows up to 20; this is 8 high-signal terms. All lowercase, hyphen-separated, ≤50 chars per the topic format rules.)

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
EOF
)"
```

Expected output: a single URL line like `https://github.com/DocGerd/hangarfit/issues/49`. Capture the trailing number — that's `$T6`.

- [ ] **Step 1.2: Verify T6 metadata**

```bash
gh issue view $T6 --json number,title,labels,milestone,assignees,state
```

Expected: `state=OPEN`, `labels=[{name:"documentation"}]`, `milestone.title="Going public"`, `assignees=[{login:"DocGerd"}]`, title matches.

### Step 2: File T4 — pinned Action SHAs

- [ ] **Step 2.1: Run `gh issue create` for T4**

```bash
gh issue create \
  --title "Pin GitHub Actions to commit SHAs" \
  --milestone "Going public" \
  --label "enhancement,good first issue" \
  --assignee "DocGerd" \
  --body "$(cat <<'EOF'
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
EOF
)"
```

Capture the trailing issue number as `$T4`.

- [ ] **Step 2.2: Verify T4 metadata**

```bash
gh issue view $T4 --json number,title,labels,milestone,assignees,state
```

Expected: `labels` contains both `enhancement` and `good first issue`, milestone + assignee as above.

### Step 3: File T5 — OpenSSF Scorecard

- [ ] **Step 3.1: Run `gh issue create` for T5**

```bash
gh issue create \
  --title "Add OpenSSF Scorecard workflow" \
  --milestone "Going public" \
  --label "enhancement" \
  --assignee "DocGerd" \
  --body "$(cat <<'EOF'
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
EOF
)"
```

Capture as `$T5`.

- [ ] **Step 3.2: Verify T5 metadata**

```bash
gh issue view $T5 --json number,title,labels,milestone,assignees,state
```

Expected as above with `labels=[{name:"enhancement"}]`.

### Step 4: File T3 — coverage in CI

- [ ] **Step 4.1: Run `gh issue create` for T3**

```bash
gh issue create \
  --title "Add coverage measurement to CI" \
  --milestone "Going public" \
  --label "enhancement" \
  --assignee "DocGerd" \
  --body "$(cat <<'EOF'
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
EOF
)"
```

Capture as `$T3`.

- [ ] **Step 4.2: Verify T3 metadata**

```bash
gh issue view $T3 --json number,title,labels,milestone,assignees,state
```

### Step 5: File T1 — ruff

- [ ] **Step 5.1: Run `gh issue create` for T1**

```bash
gh issue create \
  --title "Adopt ruff for lint and format" \
  --milestone "Going public" \
  --label "enhancement" \
  --assignee "DocGerd" \
  --body "$(cat <<'EOF'
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
EOF
)"
```

Capture as `$T1`.

- [ ] **Step 5.2: Verify T1 metadata**

```bash
gh issue view $T1 --json number,title,labels,milestone,assignees,state
```

### Step 6: File T2 — mypy

- [ ] **Step 6.1: Run `gh issue create` for T2**

```bash
gh issue create \
  --title "Adopt mypy for type checking" \
  --milestone "Going public" \
  --label "enhancement" \
  --assignee "DocGerd" \
  --body "$(cat <<'EOF'
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
- Reinforces the geometry-invariant-guard subagent's coverage of geometry.py/collisions.py.
EOF
)"
```

Capture as `$T2`.

- [ ] **Step 6.2: Verify T2 metadata**

```bash
gh issue view $T2 --json number,title,labels,milestone,assignees,state
```

- [ ] **Step 7: Sanity-check all six are in the milestone**

```bash
gh issue list --milestone "Going public" --state open --limit 25 --json number,title \
  | python3 -c "import json,sys; rows=json.load(sys.stdin); print('\n'.join(f\"#{r['number']:>3}  {r['title']}\" for r in sorted(rows, key=lambda r: r['number'])))"
```

Expected: the six new issues appear at the bottom, in order `$T6` < `$T4` < `$T5` < `$T3` < `$T1` < `$T2`.

---

## Task 2: Substitute `#T1..#T6` placeholders in issue bodies for real numbers

Each issue body filed in Task 1 contains one or more `#T1..#T6` placeholders that won't autolink on GitHub. This task edits each body in place via `gh api -X PATCH /repos/.../issues/<n>`, swapping placeholders for the real numbers captured in Task 1.

**Substitution map** (use the numbers captured in Task 1):

| Placeholder | Replace with |
|---|---|
| `#T1` | `#$T1` |
| `#T2` | `#$T2` |
| `#T3` | `#$T3` |
| `#T4` | `#$T4` |
| `#T5` | `#$T5` |
| `#T6` | `#$T6` |

Only four issues actually contain T-references: T4 (references T5), T5 (references T4), T3 (references T1, T2, T4), T1 (references T2). T6 and T2 have no T-references to substitute.

**Patching technique** (used in every step below): build the request body as JSON via `jq -n --arg body <text> '{body: $body}'` and pipe through `gh api ... --input -`. This avoids shell-quoting concerns with markdown bodies containing nested quotes, backticks, code fences, or `$`.

- [ ] **Step 1: Fetch the current body of T4 and substitute**

```bash
gh issue view $T4 --json body --jq .body \
  | sed "s/#T5/#$T5/g" \
  > /tmp/t4-body.md

jq -n --arg body "$(cat /tmp/t4-body.md)" '{body: $body}' \
  | gh api -X PATCH /repos/DocGerd/hangarfit/issues/$T4 --input -
```

Verify:

```bash
gh issue view $T4 --json body --jq .body | grep -E '#T[0-9]'
```

Expected output: empty (no remaining `#T<digit>` placeholders in T4).

- [ ] **Step 2: Fetch the current body of T5 and substitute**

```bash
gh issue view $T5 --json body --jq .body \
  | sed "s/#T4/#$T4/g" \
  > /tmp/t5-body.md

jq -n --arg body "$(cat /tmp/t5-body.md)" '{body: $body}' \
  | gh api -X PATCH /repos/DocGerd/hangarfit/issues/$T5 --input -
```

Verify:

```bash
gh issue view $T5 --json body --jq .body | grep -E '#T[0-9]'
```

Expected: empty.

- [ ] **Step 3: Fetch the current body of T3 and substitute (three placeholders)**

```bash
gh issue view $T3 --json body --jq .body \
  | sed -e "s/#T1/#$T1/g" -e "s/#T2/#$T2/g" -e "s/#T4/#$T4/g" \
  > /tmp/t3-body.md

jq -n --arg body "$(cat /tmp/t3-body.md)" '{body: $body}' \
  | gh api -X PATCH /repos/DocGerd/hangarfit/issues/$T3 --input -
```

Verify:

```bash
gh issue view $T3 --json body --jq .body | grep -E '#T[0-9]'
```

Expected: empty.

- [ ] **Step 4: Fetch the current body of T1 and substitute**

```bash
gh issue view $T1 --json body --jq .body \
  | sed "s/#T2/#$T2/g" \
  > /tmp/t1-body.md

jq -n --arg body "$(cat /tmp/t1-body.md)" '{body: $body}' \
  | gh api -X PATCH /repos/DocGerd/hangarfit/issues/$T1 --input -
```

Verify:

```bash
gh issue view $T1 --json body --jq .body | grep -E '#T[0-9]'
```

Expected: empty.

- [ ] **Step 5: Confirm no placeholders remain anywhere**

```bash
for n in $T1 $T2 $T3 $T4 $T5 $T6; do
  echo "=== #$n ==="
  gh issue view $n --json body --jq .body | grep -E '#T[0-9]' || echo "  (clean)"
done
```

Expected: every line should be `(clean)`.

---

## Task 3: Encode the two `blocked-by` edges on #27

Per spec §4 and [[feedback_encode_dependencies_in_tickets]] — the canonical `addBlockedBy` mutation in this repo.

- [ ] **Step 1: Fetch node IDs for #27, T1, T2**

```bash
ID_27=$(gh api graphql -f query='{ repository(owner:"DocGerd", name:"hangarfit") { issue(number: 27) { id } } }' --jq .data.repository.issue.id)
ID_T1=$(gh api graphql -f query="{ repository(owner:\"DocGerd\", name:\"hangarfit\") { issue(number: $T1) { id } } }" --jq .data.repository.issue.id)
ID_T2=$(gh api graphql -f query="{ repository(owner:\"DocGerd\", name:\"hangarfit\") { issue(number: $T2) { id } } }" --jq .data.repository.issue.id)

echo "ID_27=$ID_27"
echo "ID_T1=$ID_T1"
echo "ID_T2=$ID_T2"
```

Expected: three non-empty `I_kw...`-shaped node IDs printed. If any echo as empty, the issue number is wrong — stop and re-check `$T1` / `$T2`.

- [ ] **Step 2: Run the `addBlockedBy` mutation for T1 → #27**

```bash
gh api graphql -f query="mutation { addBlockedBy(input: {issueId: \"$ID_27\", blockingIssueId: \"$ID_T1\"}) { issue { number blockedBy(first: 5) { nodes { number } } } } }"
```

Expected: JSON like `{"data": {"addBlockedBy": {"issue": {"number": 27, "blockedBy": {"nodes": [{"number": <T1>}]}}}}}`.

- [ ] **Step 3: Run the `addBlockedBy` mutation for T2 → #27**

```bash
gh api graphql -f query="mutation { addBlockedBy(input: {issueId: \"$ID_27\", blockingIssueId: \"$ID_T2\"}) { issue { number blockedBy(first: 5) { nodes { number } } } } }"
```

Expected: JSON now showing both T1 and T2 in `blockedBy.nodes`.

- [ ] **Step 4: Independent verification via a fresh query**

```bash
gh api graphql -f query='{ repository(owner:"DocGerd", name:"hangarfit") { issue(number: 27) { number blockedBy(first: 5) { nodes { number title } } } } }'
```

Expected: a list containing exactly two entries — `$T1` (ruff) and `$T2` (mypy). If the list contains anything else, run `removeBlockedBy` on the unwanted edge.

---

## Task 4: PATCH the "Going public" milestone description

The current milestone description has the P1/P2/P3 buckets but doesn't mention #48 or the six new issues. Rewrite via `gh api -X PATCH`.

- [ ] **Step 1: Build the new description**

```bash
NEW_DESC="Repo hygiene for outside contributors. Continues Sprint A (#15 CI + #16 branch protection, both merged).

- **P1 — external surface:** #17 (CODEOWNERS), #18 (issue templates), #19 (PR template), #20 (CONTRIBUTING), #$T6 (repo topics + description).
- **P2 — security + CI hardening:** #21 (SECURITY), #22 (Dependabot), #23 (CodeQL), #48 (tag protection ruleset), #$T3 (coverage in CI), #$T4 (pinned Actions SHAs), #$T5 (OpenSSF Scorecard).
- **P3 — developer-experience polish:** #24 (CoC), #25 (CHANGELOG), #26 (.editorconfig), #$T1 (ruff), #$T2 (mypy), #27 (pre-commit; blocked-by #$T1 + #$T2)."

echo "$NEW_DESC"
```

Eyeball-check: each `$Tx` should have been substituted into a real issue number; no `$` should remain.

- [ ] **Step 2: PATCH the milestone**

```bash
gh api -X PATCH /repos/DocGerd/hangarfit/milestones/5 -f description="$NEW_DESC"
```

Expected: JSON response with the updated milestone object, `description` field matching `$NEW_DESC`.

- [ ] **Step 3: Verify**

```bash
gh api /repos/DocGerd/hangarfit/milestones/5 --jq .description
```

Expected: the new description prints verbatim.

---

## Task 5: Final sanity sweep

- [ ] **Step 1: Re-list the milestone to confirm the full picture**

```bash
gh issue list --milestone "Going public" --state open --limit 25 --json number,title,labels,assignees \
  | python3 -c "
import json, sys
issues = json.load(sys.stdin)
issues.sort(key=lambda i: i['number'])
for i in issues:
    labels = ','.join(l['name'] for l in i['labels']) or '-'
    assignees = ','.join(a['login'] for a in i['assignees']) or '-'
    print(f\"#{i['number']:>3}  [{labels:35}]  [{assignees}]  {i['title']}\")
"
```

Expected: 18 rows (12 originals + 6 new). Every row should show `DocGerd` as assignee and a non-empty label set. Eyeball the new rows for correct titles and labels.

- [ ] **Step 2: Spot-check one issue body end-to-end**

```bash
gh issue view $T3 --web
```

(Or non-interactive: `gh issue view $T3 --json title,body,labels,milestone,assignees`.)

Eyeball-check: cross-refs render as autolinked `#<number>` (not `#T<x>`), markdown formatting is intact, no shell-escape artefacts visible.

- [ ] **Step 3: Report completion to the user**

Summarize back to the user:
- Six new issues filed: `#$T6` (T6), `#$T4` (T4), `#$T5` (T5), `#$T3` (T3), `#$T1` (T1), `#$T2` (T2).
- Two `blocked-by` edges live on #27.
- Milestone description rewritten.

No code changed; no PR; nothing to merge.

---

## Self-review checklist (for the executing agent before reporting done)

- All 18 issues in milestone "Going public" show assignee `DocGerd` and a non-empty label.
- `gh issue view $Tx --json body --jq .body | grep -E '#T[0-9]'` returns empty for every `$Tx`.
- `gh api graphql -f query='{ repository(owner:"DocGerd", name:"hangarfit") { issue(number: 27) { blockedBy(first: 5) { nodes { number } } } } }'` returns exactly the T1 and T2 numbers.
- `gh api /repos/DocGerd/hangarfit/milestones/5 --jq .description` returns the new P1/P2/P3 text with real numbers (no `$Tx` or `#Tx` placeholders).
