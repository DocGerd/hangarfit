# ADR-0014: Merge-commit-only history under GitFlow — squash/rebase disabled as a release-safety guardrail

- **Status:** Proposed

- **Date:** 2026-05-29
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

[ADR-0011](0011-linear-history-strategy-under-gitflow.md) (Proposed, 2026-05-27)
chose **Option 1** — *feature PRs land as squash merges (single parent),
release/back-merge PRs land as merge commits* — and named a follow-up
(**Option 5**) to enforce squash-only on `feature/*`→`develop` via a GitHub
merge-method ruleset. Its whole "clean first-parent mainline" rationale rests on
*one squash commit per feature PR*.

After ADR-0011 was drafted, **squash and rebase merging were disabled repo-wide**
as a guardrail. The trigger was the v0.7.0 release cascade, where a squash-merge
on #283 contributed to the burned-tag mess (see the v0.7.0 / v0.7.1 release
history). The repo's current merge-button settings are:

```
allow_merge_commit:  true
allow_squash_merge:  false   ← squash DISABLED
allow_rebase_merge:  false   ← rebase DISABLED
```

So ADR-0011's central premise is now **false**: feature PRs land as **merge
commits**, not squash merges, and the Option-5 squash-only ruleset is impossible
to configure (squash is off). This ADR reconciles the documented strategy with
the in-effect reality (#344). It supersedes ADR-0011's *decision*; ADR-0011's
Q1–Q4 research — why `required_linear_history` is incompatible with GitFlow, the
rulesets community-#80952 historical-commits bug, and the merge-base cost of
squashed back-merges — **remains valid and is the analysis this ADR builds on**.

## Decision Drivers

- **Release-flow safety.** The squash-disable guardrail closed a concrete failure
  mode: a wrong merge method chosen during a release cut corrupting the release
  DAG (the #283 / v0.7.0 cascade). The guardrail must not be reopened casually.
- **One merge method = no foot-gun.** With only "Create a merge commit" enabled,
  no contributor or release operator can accidentally pick squash/rebase at a
  moment it would damage the release topology. The safe path is the *only* path.
- **Machine-enforcement over convention.** ADR-0011's squash-only goal was a
  social convention with no automated check (its own Compliance section says so).
  The merge-commit-only reality is enforced by the repo merge-button settings —
  strictly stronger than a convention.
- **`required_linear_history` is still incompatible with GitFlow.** Unchanged from
  ADR-0011 Q1–Q4: the release flow PRs each `release/*` into **both** `main` and
  `develop`, producing merge commits that `required_linear_history` forbids; the
  rulesets variant additionally trips community-#80952. It stays **off**.

## Considered Options

1. **Merge-commit-only (squash + rebase disabled) — the chosen option.** Every PR
   — feature and release alike — lands as a merge commit. The guardrail stays as
   the machine-enforced policy.
2. **Re-enable squash for feature merges** (revert to ADR-0011 Option 1 and pursue
   the Option-5 squash-only ruleset).
3. **Squash all merges, including releases** (ADR-0011 Option 4).
4. **Enable `required_linear_history`** (classic branch protection or the rulesets
   rule) (ADR-0011 Options 2/3).

## Decision Outcome

**Chosen option: Option 1 (merge-commit-only),** because the squash-disable
guardrail is a deliberate, machine-enforced safety measure born of a real
release-cascade incident, and the readability benefit of squashed feature commits
does not outweigh reopening that risk. `required_linear_history` stays off for the
reasons ADR-0011 established.

We accept that `git log --first-parent develop` now carries a two-parent merge
commit per feature PR rather than ADR-0011's hoped-for single squashed commit.
That is the readability cost of the guardrail, and it is small: each feature merge
commit still names its PR number and feature branch, so the first-parent log
remains a usable high-level changelog with release merges as landmarks.

### Why not Option 2 (re-enable squash for features)?

It reopens exactly the foot-gun the guardrail closed — a squash/rebase chosen at
the wrong moment during a release cut. ADR-0011's clean-single-parent-per-feature
log is a readability nicety, not a correctness requirement, and the pre-v0.7.0
two-parent feature merges already make the non-`--first-parent` log noisy
regardless. The cost (reintroduced release risk) exceeds the benefit (slightly
tidier first-parent log). If the release process is ever hardened so the
wrong-method foot-gun is closed another way, this is the option a future ADR would
revisit.

### Why not Option 3 (squash all merges) or Option 4 (`required_linear_history`)?

Both were already rejected by ADR-0011 and nothing has changed. Squashing the
`release/*`→`develop` back-merge destroys `git merge-base` semantics between `main`
and `develop` (ADR-0011 Q3). `required_linear_history` forbids the GitFlow release
double-merge entirely, and its rulesets form trips the unresolved historical-commits
bug community-#80952 on a repo that already has merge commits (ADR-0011 Q1/Q2).
This ADR carries those rejections forward unchanged.

## Consequences

### Positive

- Eliminates the wrong-merge-method-during-release foot-gun, **machine-enforced**
  by the repo merge-button settings (not a convention that can silently lapse).
- The GitFlow release flow and its back-merge topology are preserved exactly as
  ADR-0011 Q3 requires — `git merge-base`, `git log main..develop`, and
  `git branch --merged` stay semantically meaningful.
- The rationale for `required_linear_history` being off is preserved and still
  cross-referenced from `docs/security-posture.md`, so a future maintainer won't
  re-enable it chasing a Scorecard point.

### Negative

- `git log --first-parent develop` shows a two-parent merge commit per feature PR,
  noisier than ADR-0011's single squashed commit. The clean-single-parent-per-feature
  goal is given up.
- ADR-0011's Option-5 squash-only ruleset is moot/shelved while squash is disabled.

### Neutral

- If the release process is ever hardened enough to close the wrong-method foot-gun
  independently, re-enabling squash for `feature/*` (and revisiting ADR-0011's
  Option 5) could be reconsidered in a future ADR that would supersede this one.

## Compliance

Machine-enforced by the repo merge-button settings. Verify:

```
gh api repos/:owner/:repo --jq '{merge: .allow_merge_commit, squash: .allow_squash_merge, rebase: .allow_rebase_merge}'
# expected: {"merge": true, "squash": false, "rebase": false}
```

Any feature PR that lands as a squash or rebase commit means the guardrail was
changed — investigate before assuming it was intentional. The human-facing rule is
documented in [`CLAUDE.md`](../../CLAUDE.md#development-workflow) (Branching).

## More Information

- Supersedes [ADR-0011](0011-linear-history-strategy-under-gitflow.md). ADR-0011's
  Q1–Q4 research (the `required_linear_history` / rulesets / merge-base analysis)
  remains the authoritative technical background and is incorporated here by
  reference.
- Reconciliation issue: [#344](https://github.com/DocGerd/hangarfit/issues/344)
- Guardrail origin: the v0.7.0 release cascade (#283 squash-merge; #285 / #286
  immutable-release tombstone) that prompted disabling squash + rebase repo-wide.
- Related issues: [#141](https://github.com/DocGerd/hangarfit/issues/141) (branch
  protection applied), [#202](https://github.com/DocGerd/hangarfit/issues/202)
  (the spike that produced ADR-0011).
- Related docs: [`docs/security-posture.md`](../security-posture.md) — the
  "A note on linear history" section.
