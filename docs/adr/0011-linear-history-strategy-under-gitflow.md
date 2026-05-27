# ADR-0011: Linear history strategy under GitFlow — squash feature merges, accept release merge commits, target a clean first-parent mainline

- **Status:** Proposed
- **Date:** 2026-05-27
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

While applying branch protection (#141), `required_linear_history` was enabled
on `develop` and `main` then immediately reverted. The reason: this repo uses
GitFlow, whose release flow depends on merge commits — `release/*` branches are
PR'd into **both** `main` and `develop`, producing two-parent commits at each
landing. GitHub's classic `required_linear_history` forbids *any* merge commit
on the protected branch, which would block every release.

Spike #202 asked whether we can enforce something useful about history shape
without blocking the release flow, and specifically whether GitHub rulesets (the
newer protection system) provide an escape hatch that classic branch protection
lacks.

The four questions this ADR answers:

1. Does classic `required_linear_history` have any per-ref or bypass escape hatch?
2. Do GitHub rulesets allow requiring linear history with bypass actors, or allow
   enforcing it only on certain refs or merge types?
3. What is the concrete cost of squash/rebase back-merges (SHA divergence between
   `main` and `develop`) for this repo's actual release cadence?
4. What is the *actual* goal — fully-linear history, or a clean first-parent
   mainline (`git log --first-parent`)?

---

## Research findings (evidence base for the four questions)

### Q1 — Classic `required_linear_history`: no bypass, no escape hatch

Classic branch protection's `required_linear_history` is an all-or-nothing flag.
GitHub's documentation states it "prevents collaborators from pushing merge
commits to the branch" with no documented exception mechanism. The admin-bypass
available for *other* classic rules (e.g. required status checks) does not apply
to `required_linear_history` — force-pushing a merge commit is still refused even
for admins when the rule is active.
Source: [About protected branches — GitHub Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

### Q2 — GitHub rulesets: bypass actors exist, but *not* for the linear-history rule

GitHub rulesets (GA 2023, continuously updated) improve on classic protection in
several ways that matter here:

- **Multiple rulesets can target different branch patterns simultaneously** using
  fnmatch syntax (`feature/*`, `release/*`, `main`, `develop`). Each ruleset has
  its own rule set and its own bypass list.
- **Bypass actors are configured per ruleset**, not per individual rule. Eligible
  actors: repository admins, organization owners, teams, GitHub Apps.
- **The "Require linear history" rule within rulesets does not support bypass
  actors for that specific rule.** Based on the available-rules documentation, only
  the three "Restrict" rules (creations, updates, deletions) explicitly expose
  per-rule bypass. The linear-history rule is an all-or-nothing check within the
  ruleset — meaning you can bypass the *entire ruleset*, but you cannot keep the
  squash requirement while letting release merges through.
- **An additional complication found in community reports (#80952, open since
  December 2023, unresolved as of October 2025):** The rulesets implementation of
  "Require linear history" checks *all* commits in the repository's history, not
  only new commits. This means enabling it on a repo that already has merge commits
  (as this repo does — 107 two-parent merge commits excluding the 12 release/back-merge
  commits, 119 total; reproducible with `git rev-list --merges --min-parents=2 --count HEAD`)
  would immediately block all pushes until the entire history is rewritten.

**The critical new capability (GA March 2025): the "Pull request merge method"
ruleset rule.** Announced in public preview December 2024 and GA on 2025-03-24,
this rule lets you restrict which merge methods (merge commit, squash, rebase) are
allowed when PRs land on a targeted branch. Crucially:

> **Note:** The GitHub-behavior claims in this section (bypass-actor scope, the
> historical-commits behavior of "Require linear history", and the PR-merge-method
> rule GA date) were verified against GitHub documentation and community reports as
> of 2026-05-27 (research date for this ADR). Re-verify against current GitHub docs
> before moving this ADR from Proposed to Accepted.

- It is a *positive allowlist*, not a linear-history prohibition — it says which
  methods are permitted, not that merge commits are forbidden.
- You can create **two separate rulesets with different branch targets and different
  method restrictions**, e.g.:
  - Ruleset A targets `feature/*` → allow squash only
  - Ruleset B targets `release/*` → allow merge commits (or all methods)
  - Ruleset C targets `develop`, `main` → allow squash + merge commits (to accept
    both feature squashes and release back-merges)
- This ruleset rule does not check historical commits; it only gates the merge UI
  going forward.

Source: [PR merge method rule — GA announcement, 2025-03-24](https://github.blog/changelog/2025-03-24-enterprise-custom-properties-enterprise-rulesets-and-pull-request-merge-method-rule-are-all-now-generally-available/)
Source: [Available rules for rulesets — GitHub Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
Source: [About rulesets — GitHub Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)

**Summary for Q2:** Classic `required_linear_history` is incompatible with GitFlow;
the rulesets "Require linear history" rule has the same fundamental incompatibility
(plus the historical-commits bug). However, the new **merge-method ruleset rule**
can enforce squash-only on feature→develop without touching the release→main /
release→develop merge path.

### Q3 — Concrete cost of squash/rebase back-merges for this repo's cadence

If the release back-merge to `develop` were performed as a *squash* (or rebase)
instead of a merge commit, `main` and `develop` would diverge in SHA space even
though they carry identical file content. Every subsequent `git log --graph`,
`git merge-base`, and `git branch --merged` query would treat the two as diverged;
the release version bump commits would appear to need cherry-picking rather than
already being present on `develop`.

This repo's actual release history (inspected with `git log --merges --all`):

| Release | PR → main | PR → develop (back-merge) |
|---------|-----------|--------------------------|
| v0.1.0  | `20cf741` (Merge PR #46) | `7b7ca73` (Merge PR #47) |
| v0.6.0  | `9117a72` (Merge PR #123) | `685886d` (Merge PR #124) |
| v0.6.1  | `3813b85` (Merge PR #146) | `1d18b36` (Merge PR #147) |
| v0.7.0  | `b5179f5` (Merge PR #283)¹ | `a0c0934` (Merge PR #284) |
| v0.7.1  | `0d5de57` (Merge PR #288) | (HEAD of develop) |

¹ The annotated `v0.7.0` git tag exists and points to `b5179f5`. The GitHub
Release page for v0.7.0 was permanently tombstoned (immutable-release collision
during the release cascade), so v0.7.1 became the publicly visible release; the
git tag itself is intact. This is why both v0.7.0 and v0.7.1 show the same
2026-05-27 date.

Five releases in the repo's ~6-day history (2026-05-21 to 2026-05-27). At that
cadence, back-merges are not rare events — they happen at every release cut,
multiple times per week during active milestones. The merge commit at the back-merge
is the only mechanism that preserves `git merge-base develop main` pointing at a
shared ancestor, making `git log main..develop` report exactly the features not yet
released (zero divergence).

If these were squash commits, every `git log main..develop` would report the entire
back-merged content as "ahead of main" even though it is identical, and tools like
`git log --graph` would show a confusing phantom divergence. The `/release-cut`
skill and future tooling relying on branch relationships would need additional
SHA-to-content reconciliation logic.

**Cost verdict:** The cost of squash/rebase back-merges for this repo's GitFlow
cadence is *high*. The merge commits at release→main and release→develop are load-
bearing — they preserve the DAG topology that makes branch-relationship queries
semantically meaningful.

### Q4 — The actual goal: a clean first-parent mainline, not fully-linear history

The original motivation for `required_linear_history` was to make `git log
develop` readable — one issue = one commit, no per-feature-branch noise from
intermediate commits. That goal is already substantially achieved:

Since approximately PR #168 (v0.7.x era), all feature PRs to `develop` land as
**squash merges** — a single commit with the PR number in the message. Inspecting
the recent `develop` log confirms this: commits like
`feat(towplanner): Reeds–Shepp motion model … (#261) (#269)` are single-parent
with the PR's squashed diff. The older history (PRs #8–#166) contains two-parent
feature-merge commits, but these are in the past and do not affect day-to-day
readability.

Running `git log --first-parent develop` produces a clean chain of 130 entries
(vs 375 total) that reads as a high-level changelog: each entry is either a
release merge-commit (`Merge pull request #NNN from DocGerd/release/X.Y.Z`) or a
single squashed feature commit. This is the most useful reading of the history
for bisect, blame, and changelog generation — and it already works today.

**Goal reframing:** The actual goal is a *clean first-parent mainline*, not
fully-linear (all-single-parent) history. These are different:

- **Fully-linear:** every commit in `git log develop` is single-parent. Achieved
  only if release back-merges are also squashed — which destroys meaningful branch
  topology (Q3).
- **Clean first-parent mainline:** `git log --first-parent develop` tells the
  feature/release story one entry at a time, with no intermediate commits from
  feature branches. Already the case for all commits since PR #168, and preserved
  by the GitFlow release-merge-commit convention.

The release merge commits — despite being two-parent — are *desirable* entries in
the first-parent log: they mark each release landing with the PR number and the
release branch name, which is exactly what a changelog or `git describe` wants.

---

## Decision Drivers

- **GitFlow release flow must remain viable.** `release/*` → `main` and
  `release/*` → `develop` both produce merge commits; destroying that topology
  breaks branch-relationship semantics.
- **Feature merges to `develop` should be squash-only** to keep `--first-parent`
  readable and to avoid noise from intermediate commits on long-running branches.
- **Squash+rebase-disabled repo setting was already tried and reverted** during the
  v0.7.0 release cascade — it broke the release flow because GitHub's "Squash and
  merge" disabled flag was interpreted too broadly.
- **No changes to existing branch protection.** The current branch protection on
  `develop` and `main` (status checks, admin enforcement) is already set; changes
  to method enforcement must be additive, not destructive.
- **GitHub's linear-history tooling has an unresolved historical-commits bug**
  (community #80952) that makes it unsafe to enable `required_linear_history`
  rulesets on any repo with pre-existing merge commits.

---

## Considered Options

1. **Accept the status quo (squash for features, merge commits for releases/back-merges) and document it explicitly** — this is the chosen option.
2. **Enable classic `required_linear_history`** — reverted on the spot in #141;
   blocks every release.
3. **Enable rulesets "Require linear history"** — same fundamental incompatibility
   as classic (Q2), plus the unresolved historical-commits bug makes it
   immediately destructive.
4. **Squash all merges including releases** — destroys `git merge-base` semantics
   between `main` and `develop`; makes `git log main..develop` report phantom
   divergence; high cost for no readability gain (Q3).
5. **Use the GA merge-method ruleset to enforce squash-only on `feature/*`→`develop`** — an implementation follow-up. Codifies the current practice (which is already squash-only in the v0.7.x era) in enforced policy. Does not affect the release path. This option is compatible with Option 1 — it is an automation of the existing convention, not a change to it.

---

## Decision Outcome

**Chosen option: Option 1 (accept + document), with Option 5 proposed as a
follow-up implementation issue.**

The status quo is already correct: feature PRs land as squash merges (single
parent), and release/back-merge PRs land as merge commits (two parents). This
gives a clean first-parent mainline on both `develop` and `main` today.

The missing piece is enforced policy rather than convention — a repo-level ruleset
that gates `feature/*`→`develop` PRs to squash-only would prevent accidental
three-commit merge commits from slipping through. That follow-up does not change
any behavior for the release path.

### Why not Option 2 (classic `required_linear_history`)?

It forbids *any* merge commit on the branch. The GitFlow release flow requires
merge commits at back-merge PRs. Already tried and reverted in #141.

### Why not Option 3 (rulesets "Require linear history")?

Same semantic incompatibility as Option 2. Additionally, community issue #80952
(open since December 2023, unresolved as of October 2025) reports that the
rulesets implementation checks *historical* commits — enabling it on this repo
would immediately block all pushes until the entire git history is rewritten. This
is not a viable path.

### Why not Option 4 (squash all merges including releases)?

See Q3. Squashing the back-merge from `release/*` to `develop` produces SHA
divergence between `main` and `develop` even when the file trees are identical.
`git merge-base`, `git log main..develop`, `git branch --merged` all become
misleading. The `/release-cut` skill and any future tooling that reasons about
branch relationships would break or require special-casing. The cost is high and
the readability benefit is zero — release merge commits are *desirable* landmarks
in the first-parent log.

---

## Consequences

### Positive

- Release flow continues to work exactly as today; no branch-protection changes
  required.
- `git log --first-parent develop` is already a clean, readable changelog and will
  remain so: release merge-commits appear as explicit landmarks; feature squash-
  commits are one-per-PR.
- The rationale for `required_linear_history` being off is now recorded and
  auditable — future maintainers won't re-enable it chasing a Scorecard point
  (see `docs/security-posture.md`).
- The GA merge-method ruleset rule (March 2025) provides a low-cost path to
  enforce squash-only on feature branches without affecting the release path,
  described as a concrete follow-up.

### Negative

- `git log develop` without `--first-parent` is noisier than a fully-linear
  history, showing the pre-v0.7.0 two-parent feature merges in the older history.
  This is unavoidable without history rewriting and is accepted.
- Fully-linear history (a meaningful Scorecard "Branch-Protection" signal) remains
  out of reach by design. `docs/security-posture.md` already documents this.

### Neutral

- The merge-method ruleset configuration (Option 5 follow-up) would need to
  explicitly allow *both* squash and merge-commit methods on `develop` and `main`
  (so releases can back-merge), while allowing only squash on `feature/*`. This is
  a two-ruleset configuration, not one global setting.

---

## Compliance

No automated check enforces the squash requirement on feature branches today; it
is a social convention. The proposed follow-up (a "PR merge method" ruleset
targeting `feature/*` with squash-only) would make it machine-enforced. Until
that is in place, compliance is verified by PR review: any two-parent commit on
`develop` that is not a release back-merge is a protocol violation.

`docs/security-posture.md` already cross-references this ADR as the justification
for `required_linear_history` being intentionally off — that cross-reference is
the audit trail.

---

## More Information

- Related issues: [#141](https://github.com/DocGerd/hangarfit/issues/141) (branch
  protection applied), [#202](https://github.com/DocGerd/hangarfit/issues/202)
  (this spike)
- Related docs: [`docs/security-posture.md`](../security-posture.md) — the
  "Branch-Protection" and "A note on linear history" sections
- GitHub docs:
  - [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
  - [About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
  - [Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
  - [PR merge method rule — GA announcement, 2025-03-24](https://github.blog/changelog/2025-03-24-enterprise-custom-properties-enterprise-rulesets-and-pull-request-merge-method-rule-are-all-now-generally-available/)
- GitHub community discussion: [#80952 — Require Linear History Only for New Commits in Rulesets](https://github.com/orgs/community/discussions/80952) (unresolved as of 2025-10; status verified 2026-05-27)
- Repo history commands used in this research:
  ```
  git log --first-parent --oneline develop
  git log --merges --oneline --all | grep release
  git cat-file -p <sha>   # to verify parent count
  git rev-list --merges --min-parents=2 --count HEAD  # total two-parent merges (119)
  ```

---

## Proposed implementation follow-up

The following is a concrete implementation issue for the maintainer to file
against a future release milestone. **This ADR does not implement it.**

**"Enforce squash-only for feature→develop via GitHub merge-method ruleset"**

Create a repository ruleset (Settings → Rules → Rulesets → New branch ruleset)
with:
- Target branches: `feature/**` (fnmatch pattern)
- Rule: "Require a pull request before merging" → allowed merge type: **squash
  only**
- No bypass actors needed (releases don't touch `feature/*`)

Separately, verify the `develop` and `main` ruleset (or classic branch protection)
allows both squash **and** merge-commit methods so back-merges from `release/*`
continue to work.

Cost: ~15 minutes of UI configuration; no code changes. The GA merge-method rule
(March 2025) makes this possible without touching `required_linear_history`.
