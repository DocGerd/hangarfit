# Per-release security assessment — mandatory gate

Every release of `hangarfit` — whether cut by the `/release-cut` skill or manually
via the steps described in [CLAUDE.md](../CLAUDE.md#branching) — requires a
security assessment to be completed **before the release tag is pushed**. This
document is the checklist the releaser runs through. It is not aspirational; it
is a gate. A release that skips this checklist is not a valid release.

The assessment exists as a mandatory gate rather than a best-effort reminder
because Best Practices Gold criterion `security_review` requires that a
documented security review be performed and its results recorded for each release.

---

## When to run this checklist

Run it after:

1. The `release/<version>` branch has been created off `develop` and all
   planned commits are in.
2. CI is green on the `release/*` branch (test suite, lockfile-drift guards,
   CodeQL on the open PR).
3. **Before** pushing the `v*` tag that triggers
   [`.github/workflows/release.yml`](../.github/workflows/release.yml) (which
   builds, signs, and publishes the GitHub Release).

If the checklist reveals a finding, cut a fix commit on the `release/*` branch,
re-run CI, and run the checklist again from the top before pushing the tag.

---

## Gate 1 — CodeQL: no open, un-triaged alerts

**Workflow file:** [`.github/workflows/codeql.yml`](../.github/workflows/codeql.yml)

CodeQL runs automatically on every push to `develop` and `main`, on every PR
targeting `develop`, and on a weekly schedule (Mondays at 06:00 UTC). Results
are uploaded to the GitHub Security tab as SARIF.

**Checklist:**

- [ ] Open the repository's **Security → Code scanning** tab.
- [ ] Confirm there are **no open alerts** with state `open`.
- [ ] For any alert with state `dismissed`: verify the dismissal rationale is
  still valid (the affected code has not changed in a way that makes the
  original rationale stale).
- [ ] If a new alert is open: triage it before cutting the tag.
  - If it is a genuine finding: fix it, commit, re-run CI, restart checklist.
  - If it is a false positive: dismiss it via the Security tab with a written
    rationale, then continue.

**Pass condition:** zero open alerts OR every open alert is dismissed with a
rationale that still applies.

---

## Gate 2 — Fuzzing: nightly run still clean

**Workflow file:** [`.github/workflows/fuzz.yml`](../.github/workflows/fuzz.yml)

The nightly fuzzing workflow (`fuzz.yml`) targets the YAML loader
(`src/hangarfit/loader.py`) using a polyglot Hypothesis + Atheris strategy.
It was introduced in issue [#143](https://github.com/DocGerd/hangarfit/issues/143)
and is the project's continuous coverage-guided fuzzing gate. **Do not duplicate
or re-describe the fuzzing implementation here** — this item exists only to
confirm the nightly is healthy at the time of release.

**Checklist:**

- [ ] Open **Actions → Nightly fuzz** in the GitHub UI (or run
  `gh run list --workflow=fuzz.yml --limit=5`).
- [ ] Confirm the most recent completed run has status `success`.
- [ ] If the most recent run has status `failure`: investigate the failure
  before cutting the tag (a real fuzzer-found defect must be fixed; a transient
  infrastructure failure must be confirmed as infrastructure, not a finding,
  with the evidence noted).

**Pass condition:** the most recent completed `fuzz.yml` run is `success`, OR a
`failure` has been confirmed as an infrastructure flake with written evidence
(e.g. link to the GitHub Actions run log with the infrastructure error).

---

## Gate 3 — Threat-surface review: SECURITY.md still accurate

**File:** [`SECURITY.md`](../SECURITY.md)

`SECURITY.md` documents the project's attack surface — currently the YAML loader
and the visualizer. Over time, new code may introduce new surface (additional
file-parsing modules, network calls, etc.) or retire old surface. Before each
release the releaser should re-read `SECURITY.md` and confirm it still matches
reality.

**Checklist:**

- [ ] Re-read the **Scope** section of `SECURITY.md`.
- [ ] Compare it against the modules that changed since the last release
  (use `git log <last-tag>..HEAD -- src/hangarfit/` as a starting point).
- [ ] If a new file-reading, network, or subprocess call was added: assess
  whether it introduces new attack surface, and update `SECURITY.md` if so.
- [ ] If a module in the scope section was removed or its attack surface was
  significantly reduced: update `SECURITY.md` accordingly.

**Pass condition:** the Scope section accurately describes the current attack
surface. If an update was needed, it has been committed to the `release/*`
branch.

---

## Recording the assessment

After all three gates pass, record the fact in the release commit or the GitHub
Release description. A single sentence is sufficient:

> Security assessment completed: CodeQL clean, fuzz nightly green, SECURITY.md
> reviewed and current.

Paste this sentence into the GitHub Release body (the `--notes-from-tag`
mechanism used by `release.yml` pulls the tag annotation, so you may also put it
there).

This recorded statement is the artifact that satisfies the Best Practices Gold
`security_review` criterion.

---

## Quick reference — relevant files and links

| Item | Location |
|---|---|
| CodeQL workflow | [`.github/workflows/codeql.yml`](../.github/workflows/codeql.yml) |
| Nightly fuzz workflow | [`.github/workflows/fuzz.yml`](../.github/workflows/fuzz.yml) |
| Release workflow | [`.github/workflows/release.yml`](../.github/workflows/release.yml) |
| Threat surface | [`SECURITY.md`](../SECURITY.md) — Scope section |
| Scorecard + structural-zero rationale | [`docs/security-posture.md`](security-posture.md) |
| Best Practices badge tracking | [`docs/openssf-best-practices-badge.md`](openssf-best-practices-badge.md) |
| Best Practices Silver tracking | [`docs/openssf-best-practices-badge-silver.md`](openssf-best-practices-badge-silver.md) |
