# Security posture — Scorecard structural zeros, explained

[OpenSSF Scorecard](https://securityscorecards.dev/) gives `hangarfit` an
aggregate of around **6.5 / 10** at the time this document was written
(PR #166, 2026-05-23). Four of the checks contributing to that number
score **0** (or **-1**), and they score that way because of structural
properties of the project that we do not intend to change. This
document explains, per check, why the zero is *structural* rather than
a defect — so an outside reviewer arriving from a Scorecard report can
see the rationale in-tree rather than guessing.

One further check — **Branch-Protection** — scores a partial **3/10**
rather than 0; it is documented here too because its residual gap is the
same single-maintainer cap as Code-Review, not a missing protection.

Three of the four also cap the realistic ceiling of our aggregate score
at roughly **8.0–8.5** — the per-check breakdown that produces that
ceiling lives in the
[v0.7.0 milestone description](https://github.com/DocGerd/hangarfit/milestone/13).
Even after the rest of the supply-chain hardening in that milestone
lands, three structural zeros remain in the average.

If you want to verify any of this against the live data, see
[Where the score lives](#where-the-score-lives) at the bottom.

This document covers the **Scorecard** picture. The project's **OpenSSF
Baseline** Level-1 self-attestation — the full 24-control checklist — lives in
[`osps-baseline-L1.md`](osps-baseline-L1.md); the MFA control documented below
([Two-factor authentication](#two-factor-authentication-osps-ac-0101)) is the
one control shared between the two.

---

## Code-Review (score 0)

**What the check measures.** Scorecard inspects the most recent
changesets (up to 30) on the default branch and scores on the **share**
of those that show evidence of review — on GitHub, that means a PR with
at least one `APPROVED` review. A score of 0 means none of the
inspected changesets carry that evidence.

**Why we score 0.** `hangarfit` has a single maintainer.
[GitHub does not allow a PR's author to approve their own pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/about-pull-request-reviews):
submitting an `APPROVE` review event as the PR author is refused
(HTTP 422 from the REST API). Every PR therefore merges with **zero**
formal approvals on file, regardless of how much review actually
happened.

What did happen on every one of those PRs is a substantive review pass
performed by Claude Code via the
[`/pr-review` workflow documented in CLAUDE.md](../CLAUDE.md#per-pr-process).
That workflow dispatches a mix of subagents from the external
`pr-review-toolkit` plugin (`code-reviewer`, `comment-analyzer`,
`silent-failure-hunter`, …) and the repo-local
[`geometry-invariant-guard`](../.claude/agents/geometry-invariant-guard.md).
Every finding is converted into a review thread **on the diff**, then
either fixed in code or replied to with rationale before the thread is
resolved.

So the Scorecard 0 is accurate as a count of `APPROVED` reviews; it is
**not** accurate as a count of *reviews that happened*. We have not
found a way to register the second signal with Scorecard without
inventing a second-account-approves-its-own-bot fiction (see
[Alternatives considered](#alternatives-considered)).

**Will it move?** Not without an external co-maintainer joining the
project. That would be welcome but is not on the roadmap.

---

## Branch-Protection (score 3)

**What the check measures.** Scorecard inspects the protection configured
on the default branch and any release branches, awarding partial credit
per tier of protection (admin enforcement, required status checks,
required reviews, code-owner review, up-to-date-before-merge, …). A
maximal configuration scores 10.

**Why we score 3.** The two warnings Scorecard emits — *"branch does not
require approvers"* and *"codeowners review is not required"* — are the
[Code-Review](#code-review-score-0) cap wearing a different hat. Both ask
for a **second person** to approve before merge, and on a single-maintainer
repo there is no second person: GitHub refuses a PR author's own `APPROVE`
(HTTP 422), so requiring ≥ 1 approver would deadlock *every* merge rather
than improve security. Requiring code-owner review would deadlock it for
the same reason — even though the repo does ship a
[`CODEOWNERS`](../.github/CODEOWNERS) file naming the sole maintainer as
code-owner for the whole tree (GitHub suppresses the code-owner review
request on the author's own PR, which here is essentially every PR).

Everything that *can* be enforced without a second human is enforced on
both protected branches (`develop` and `main`), with one deliberate
workflow exception called out below (up-to-date-before-merge on `develop`):

- **Admin enforcement** — the rules apply to the maintainer too
  (`enforce_admins`), so protection cannot be silently bypassed.
- **Required status checks** — PRs must pass the configured CI gate (the
  test suite on every supported Python and all three lockfile-drift guards,
  plus CodeQL on `develop`) before merge. On `main` this is **strict** (the branch
  must also be up to date with the base before merge). On `develop` the
  up-to-date / `strict` requirement is **deliberately disabled**: with it on,
  merging any PR forced every *other* open PR to "Update branch" and re-run the
  full required-check set before it could merge — pure churn for a
  single-maintainer flow whose PRs are usually file-disjoint. The
  `push`-to-`develop` CI run re-validates the integrated result within seconds
  of each merge, so integration regressions still surface immediately; this
  trades a small slice of the Scorecard branch-protection tier (up-to-date is
  one of the credited sub-criteria) for merge throughput. The exact
  required-check set is otherwise configured per branch and reconciled at each
  release cut, so `develop` and `main` are not always byte-identical (e.g.
  CodeQL runs on `develop` PRs only).
- **Stale-review dismissal** — approvals are dismissed on new pushes (a
  no-op today given the reviewer count, but already correct for the day a
  co-maintainer joins).

So the 3 reflects a deliberately-applied, single-maintainer-maximal
configuration — not an unprotected branch. [#141] applied it; note that
issue aimed for "3 → 6+", but the realized ceiling is 3 precisely because
every remaining tier (required approvers, code-owner review) is
second-person-gated — the cap this section describes.

**A note on linear history.** `required_linear_history` is intentionally
**off**. Enabling it would forbid the merge commits the GitFlow release
flow depends on (`release/*` → `main`, and the back-merge to `develop`).
The trade-off was explored in spike [#202] and the decision is recorded in
[ADR-0014](adr/0014-merge-commit-only-history-strategy.md) — with the detailed
`required_linear_history` analysis preserved in the superseded
[ADR-0011](adr/0011-linear-history-strategy-under-gitflow.md); do not "fix"
this to chase a Scorecard point.

**Will it move?** Only with an external co-maintainer — exactly as for
[Code-Review](#code-review-score-0). The non-human protections are already
maxed.

[#141]: https://github.com/DocGerd/hangarfit/issues/141
[#202]: https://github.com/DocGerd/hangarfit/issues/202

---

## Maintained (score 0)

**What the check measures.** Scorecard considers a project "maintained"
if it has had at least one commit (or release) in the last 90 days
**and** it has existed for at least 90 days.

**Why we score 0.** The repository was created on **2026-05-21**. The
90-day age threshold is therefore crossed around **2026-08-19**. Until
then, the check returns 0 for everyone — there is no remediation
available to a project that is simply too new.

**Will it move?** Yes, automatically, around **2026-08-19**. No action
needed; the check will resample on its next scheduled run after that
date. If the score has not moved by early September 2026, it's worth
re-investigating, but no manual intervention is appropriate before
then.

---

## Contributors (score 0)

**What the check measures.** Scorecard counts the number of distinct
**companies or organisations** (inferred from contributors' GitHub
profile affiliation) whose members have committed to the project.
Scoring expects at least three; zero scores 0.

**Why we score 0.** `hangarfit` is an exception-handling CLI for a
**single flying club's** hangar — see
[§1 Introduction & Goals](architecture/01-introduction-and-goals.md)
and [§3 Context & Scope](architecture/03-context-and-scope.md) for the
scope statement. The expected contributor population is one
maintainer plus, perhaps, a small number of club members. There are no
upstream consumers and no realistic path to multi-organisation
commits, because the tool is not interesting to anyone outside this
specific deployment.

**Will it move?** Almost certainly not, and we are not trying to move
it. A 0 here means "this project does not look like a typical
multi-organisation open-source project"; the project genuinely is not
one.

---

## Packaging (score -1)

**What the check measures.** Scorecard looks for evidence that the
project publishes a versioned artifact to a package registry (PyPI,
npm, etc.) via a recognised workflow. A score of **-1** ("inconclusive")
means it cannot find one.

**Why we score -1.** We deliberately do not publish `hangarfit` to
PyPI. The tool is consumed via `pip install -e .` from a local clone,
exactly as documented in
[CLAUDE.md › Useful commands](../CLAUDE.md#useful-commands). Publishing
would imply a contract with downstream users that does not exist:
there are no downstream users, the dependency model is "clone and run
out of the checkout", and the per-deployment config (the placeholder
fleet and hangar YAML) is meaningful only to the one club that owns
the data.

**Will it move?** No, not unless we change our minds about publishing.
A -1 here is the correct answer for a deliberately unpublished tool.

---

## Two-factor authentication (OSPS-AC-01.01)

**What the control requires.** OSPS Baseline L1 control OSPS-AC-01.01 requires
that multi-factor authentication (MFA) is enabled for all accounts that can
perform sensitive repository actions — including pushing commits, merging pull
requests, and changing branch-protection settings.

**Status: enabled.** The sole maintainer account (@DocGerd) has two-factor
authentication enabled at the GitHub account level. Every sensitive repository
action — commit pushes, PR merges, branch-protection changes — is gated behind
this factor.

**Why this is a self-attestation.** A *personal* GitHub account's 2FA status is
not verifiable by an *outside* observer via the GitHub REST API. The public
`GET /users/{username}` endpoint never exposes a user's
`two_factor_authentication` status to third parties. The *authenticated*
`GET /user` endpoint can surface the field to the account holder, but whether
it returns a boolean or `null` depends on the token type and scope (a classic
PAT with `read:user` can return it; the OAuth token used by the `gh` CLI here
returns `null`) — and that is the holder inspecting their *own* account, not a
third party verifying it. No endpoint lets an external reviewer confirm a
personal account's 2FA state.

A GitHub *organisation* can enforce "require 2FA for all members" as a policy
and expose that enforcement status via `GET /orgs/{org}`; but `hangarfit` is
hosted under a personal account, not an organisation, so no equivalent
enforcement object exists.

This attestation therefore mirrors the posture already used for the structural
Scorecard zeros above: an honest written statement in-tree, with the
verifiability limit stated explicitly, rather than a claimed machine-checkable
signal that does not exist.

**Will it change?** Only if the repository moves under a GitHub organisation,
at which point organisation-level 2FA enforcement can be turned on and would
become externally verifiable.

---

## Alternatives considered

For completeness — these were considered for the Code-Review zero and
explicitly rejected:

**A. A `claude[bot]`-style second account that auto-`APPROVE`s a PR
once `/pr-review` reports no outstanding findings.** This would move
the Scorecard number but defeat the spirit of the check, which is
specifically about *human* peer review. Bot-as-second-reviewer is
security theatre; we prefer the 0 with a written rebuttal to a green
checkmark with a fictional reviewer.

**B. Solicit a real external co-maintainer.** This would be the only
honest way to move the Code-Review zero. It is welcome in principle
but cannot be engineered for a Scorecard pass — a co-maintainer joins
because they want to work on the tool, not so the score goes up.

---

## Where the score lives

- **Live Scorecard JSON for this repository:**
  <https://api.securityscorecards.dev/projects/github.com/DocGerd/hangarfit>
- **Workflow that publishes the score on every push to `develop` and
  weekly:** [`.github/workflows/scorecard.yml`](../.github/workflows/scorecard.yml)
- **Findings are also uploaded to the GitHub Security tab via SARIF.**

If the numbers in this document drift from the live JSON, the live
JSON is authoritative — open an issue and we'll refresh this page.
