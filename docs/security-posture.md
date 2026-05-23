# Security posture — Scorecard structural zeros, explained

[OpenSSF Scorecard](https://securityscorecards.dev/) gives `hangarfit` an
aggregate of around **6.5 / 10** at the time of writing. Four of the
checks contributing to that number score **0** (or **-1**), and they
score that way because of structural properties of the project that we
do not intend to change. This document explains, per check, why the
zero is *structural* rather than a defect — so an outside reviewer
arriving from a Scorecard report can see the rationale in-tree rather
than guessing.

Three of the four also cap the realistic ceiling of our aggregate score
at roughly **8.0–8.5**, even after the rest of the supply-chain
hardening in milestone
[v0.7.0](https://github.com/DocGerd/hangarfit/milestone/13) lands.

If you want to verify any of this against the live data, see
[Where the score lives](#where-the-score-lives) at the bottom.

---

## Code-Review (score 0)

**What the check measures.** Scorecard inspects the most recent (up to
30) commits on the default branch and counts how many landed via a
pull request with at least one `APPROVED` review. A score of 0 means
**none** of them did.

**Why we score 0.** `hangarfit` has a single maintainer. GitHub does
not permit a maintainer to formally `APPROVE` their own pull request
(the "Approve" radio is disabled when reviewing your own PR), so every
PR merges with **zero** formal approvals on file — regardless of how
much review actually happened.

What did happen on every one of those PRs is a substantive review pass
performed by Claude Code via the in-repo
[`/pr-review` toolkit](../.claude/README.md). That toolkit dispatches
specialised review subagents (the `pr-review-toolkit` family plus
repo-specific ones like `geometry-invariant-guard`), and every finding
is converted into a review thread **on the diff**, then either fixed
in code or replied to with rationale before the thread is resolved.
The PR-review process is documented in
[CLAUDE.md › Per-PR process](../CLAUDE.md#per-pr-process).

So the Scorecard 0 is accurate as a count of `APPROVED` reviews; it is
**not** accurate as a count of *reviews that happened*. We have not
found a way to register the second signal with Scorecard without
inventing a second-account-approves-its-own-bot fiction (see
[Alternatives considered](#alternatives-considered)).

**Will it move?** Not without an external co-maintainer joining the
project. That would be welcome but is not on the roadmap.

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
specific deployment. The check is, in effect, asking the wrong
question of this codebase.

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
