# OpenSSF Best Practices Badge — Passing-tier answer script

This is the working script for the project's
[OpenSSF Best Practices Badge](https://www.bestpractices.dev/) entry
(formerly CII Best Practices). It maps each **Passing**-tier criterion to a
status (**Met** / **N/A** / **Unmet**), a justification, and an evidence URL,
so the live questionnaire at <https://www.bestpractices.dev/> can be filled in
quickly and re-certified later without re-deriving the rationale.

It also satisfies the Scorecard **CII-Best-Practices** check (code-scanning
alert #4) once the entry reaches Passing — see
[`security-posture.md`](security-posture.md) for the broader Scorecard picture.

- **Badge:** **Passing** (100%) — project [12987](https://www.bestpractices.dev/projects/12987), awarded 2026-05-26.
- **Repository:** <https://github.com/DocGerd/hangarfit>
- **Default branch:** `develop`
- **License:** Apache-2.0
- **Language:** Python (≥ 3.12)
- **Crypto:** none — the tool does no cryptography, so every `crypto_*` criterion is **N/A**.

> **MUST / SHOULD / SUGGESTED:** only unmet **MUST** criteria (or unmet
> **SHOULD**s without a justified exception) block the badge. The two items
> below that aren't cleanly Met (`dynamic_analysis`, `crypto_random`) are
> SUGGESTED/N/A and do **not** block Passing.

---

## 0. Items that need *your* decision in the live form

Everything else is mechanical. These three need a human:

1. **`know_secure_design` / `know_common_errors`** (Security) — self-attestation
   that the maintainer understands secure-design principles and common
   implementation errors. Mark **Met**; the evidence is the documented
   threat-surface reasoning in [`SECURITY.md`](../SECURITY.md) and the
   determinant-trap / input-validation handling in the ADRs. (Only you can
   truthfully assert this — it's true.)
2. **`dynamic_analysis`** (Analysis, *SUGGESTED*) — currently **Unmet**; fuzzing
   the YAML loader is tracked in **#143** (parked). SUGGESTED does not block
   Passing — mark Unmet with the #143 reference, or N/A.
3. **Contact email** — the form asks for a project contact. Use whatever address
   you want public; SECURITY.md routes vulnerabilities through GitHub private
   advisories rather than email.

---

## 1. Basics

| Criterion | Status | Justification + evidence |
|---|---|---|
| `description_good` | **Met** | README opens with a one-line + paragraph description of what the tool is and does. <https://github.com/DocGerd/hangarfit#readme> |
| `interact` | **Met** | CONTRIBUTING.md documents how to contribute (issues-first, GitFlow, PR process). <https://github.com/DocGerd/hangarfit/blob/develop/CONTRIBUTING.md> |
| `contribution` | **Met** | CONTRIBUTING.md. URL as above. |
| `contribution_requirements` | **Met** | CONTRIBUTING.md states PR requirements (green CI on 3.12, tests for behaviour changes, conventional commits, review-thread resolution). |
| `floss_license` | **Met** | Apache-2.0, an OSI-approved FLOSS license. <https://github.com/DocGerd/hangarfit/blob/develop/LICENSE> |
| `floss_license_osi` | **Met** | Apache-2.0 is OSI-approved. |
| `license_location` | **Met** | `LICENSE` at the repo root (standard location); also declared `license = "Apache-2.0"` in `pyproject.toml`. |
| `documentation_basics` | **Met** | README (usage, exit codes, examples) + `docs/architecture/` (arc42) + `docs/adr/`. <https://github.com/DocGerd/hangarfit/tree/develop/docs> |
| `documentation_interface` | **Met** | README documents the CLI surface (`check` / `solve`, flags, exit codes, JSON schemas). |
| `sites_https` | **Met** | Project home (GitHub) and all referenced sites are HTTPS. |
| `discussion` | **Met** | GitHub Issues. <https://github.com/DocGerd/hangarfit/issues> |
| `english` | **Met** | All documentation and code comments are in English. |
| `maintained` | **Met** | Actively maintained by a single maintainer; recent commits/releases and prompt issue handling. (Distinct from Scorecard's 90-day-age "Maintained" check — see security-posture.md.) |

## 2. Change Control

| Criterion | Status | Justification + evidence |
|---|---|---|
| `repo_public` | **Met** | Public Git repo on GitHub. <https://github.com/DocGerd/hangarfit> |
| `repo_track` | **Met** | All changes tracked in Git. |
| `repo_interim` | **Met** | Interim work is visible on `feature/*` branches and in PRs before release. |
| `repo_distributed` | **Met** | Git (a distributed VCS). |
| `version_unique` | **Met** | Unique version per release. |
| `version_semver` | **Met** | Semantic Versioning; declared in CHANGELOG. <https://github.com/DocGerd/hangarfit/blob/develop/CHANGELOG.md> |
| `version_tags` | **Met** | Git tags `v0.1.0`, `v0.6.0`, `v0.6.1`. <https://github.com/DocGerd/hangarfit/tags> |
| `release_notes` | **Met** | CHANGELOG.md (Keep a Changelog format) + GitHub Releases. <https://github.com/DocGerd/hangarfit/releases> |
| `release_notes_vulns` | **Met** | No vulnerabilities to date; release notes would call out any security fix per the CHANGELOG's "Fixed" section convention. |

## 3. Reporting

| Criterion | Status | Justification + evidence |
|---|---|---|
| `report_process` | **Met** | CONTRIBUTING.md + issue templates describe how to report bugs / request features. <https://github.com/DocGerd/hangarfit/tree/develop/.github/ISSUE_TEMPLATE> |
| `report_tracker` | **Met** | GitHub Issues. |
| `report_responses` | **Met** | Maintainer responds to issues on a best-effort basis. |
| `enhancement_responses` | **Met** | Enhancement requests are triaged into milestones; responded to. |
| `report_archive` | **Met** | GitHub Issues provides a public, searchable archive. |
| `vulnerability_report_process` | **Met** | SECURITY.md documents the disclosure path. <https://github.com/DocGerd/hangarfit/blob/develop/SECURITY.md> |
| `vulnerability_report_private` | **Met** | Private reporting via GitHub Security Advisories (not public issues). <https://github.com/DocGerd/hangarfit/security/advisories/new> |
| `vulnerability_report_response` | **Met** (SHOULD) | SECURITY.md states a best-effort response commitment (hobby project, no SLA). No reports to date. |

## 4. Quality

| Criterion | Status | Justification + evidence |
|---|---|---|
| `working_build` | **Met** | `pip install -e .` (PEP 517, setuptools backend). <https://github.com/DocGerd/hangarfit/blob/develop/pyproject.toml> |
| `build_common_tools` | **Met** | Standard Python build toolchain (pip / setuptools / build). |
| `build_floss_tools` | **Met** | All build tools are FLOSS. |
| `test` | **Met** | pytest suite, incl. a strut-aware collision golden set. <https://github.com/DocGerd/hangarfit/tree/develop/tests> |
| `test_invocation` | **Met** | `pytest` (documented in README + CONTRIBUTING). |
| `test_most` | **Met** | Golden collision suite + CLI/exit-code/JSON/render coverage; coverage measured via pytest-cov + Codecov. |
| `test_continuous_integration` | **Met** | GitHub Actions runs the suite on every PR. <https://github.com/DocGerd/hangarfit/blob/develop/.github/workflows/ci.yml> |
| `test_policy` | **Met** | CONTRIBUTING.md requires tests for any behaviour change. |
| `tests_are_added` | **Met** | New collision scenarios are added as YAML fixtures per CONTRIBUTING.md. |
| `tests_documented_added` | **Met** | The add-a-fixture policy is documented in CONTRIBUTING.md. |
| `warnings` | **Met** | ruff lint (`E,F,I,B,UP,SIM`) + mypy strict; enforced via pre-commit and CI. <https://github.com/DocGerd/hangarfit/blob/develop/.pre-commit-config.yaml> |
| `warnings_fixed` | **Met** | CI fails on lint/format/type findings, so warnings are fixed before merge. |
| `warnings_strict` | **Met** | mypy `disallow_untyped_defs` on `hangarfit.*`; ruff format `--check` in CI. |

## 5. Security

| Criterion | Status | Justification + evidence |
|---|---|---|
| `know_secure_design` | **Met** (self-attest) | Threat surface enumerated in SECURITY.md; input-validation + invariant handling documented in ADRs. *(Maintainer attestation — see §0.)* |
| `know_common_errors` | **Met** (self-attest) | *(Maintainer attestation — see §0.)* |
| `crypto_published` | **N/A** | No cryptography in the project. |
| `crypto_call` | **N/A** | No cryptography. |
| `crypto_floss` | **N/A** | No cryptography. |
| `crypto_keylength` | **N/A** | No cryptography. |
| `crypto_working` | **N/A** | No cryptography. |
| `crypto_weaknesses` | **N/A** | No cryptography. |
| `crypto_pfs` | **N/A** | No network/TLS termination in the tool. |
| `crypto_password_storage` | **N/A** | No passwords/credentials handled. |
| `crypto_random` | **N/A** | RNG is used only for the solver's random-restart search (non-security); no security-sensitive randomness. |
| `delivery_mitm` | **Met** | Delivered via `git clone` / `pip install` from GitHub over HTTPS/TLS. |
| `delivery_unsigned` | **Met** | Same HTTPS delivery; release tags are promoted to signed GitHub Releases (#138). <https://github.com/DocGerd/hangarfit/releases> |
| `vulnerabilities_fixed_60_days` | **Met** | No known unpatched vulnerabilities. |
| `vulnerabilities_critical_fixed` | **Met** | None to date. |
| `no_leaked_credentials` | **Met** | No secrets committed; GitHub secret scanning enabled on the repo. |

## 6. Analysis

| Criterion | Status | Justification + evidence |
|---|---|---|
| `static_analysis` | **Met** | CodeQL on every PR + ruff + mypy strict. <https://github.com/DocGerd/hangarfit/blob/develop/.github/workflows/codeql.yml> |
| `static_analysis_common_vulnerabilities` | **Met** | CodeQL's default Python query suite covers common vulnerability classes. |
| `static_analysis_fixed` | **Met** | CodeQL/ruff/mypy findings are fixed before merge (CI-gated). |
| `static_analysis_often` | **Met** | Runs on every PR into `develop`/`main`. |
| `dynamic_analysis` | **Unmet** (SUGGESTED) | No fuzzing yet; tracked in **#143** (fuzz the YAML loader). Does not block Passing. <https://github.com/DocGerd/hangarfit/issues/143> |
| `dynamic_analysis_unsafe` | **N/A** | No unsafe-language memory bugs (pure Python). |
| `dynamic_analysis_enable_assertions` | **N/A** | Follows from `dynamic_analysis` being deferred. |

---

## After the badge is awarded

1. Copy the badge markdown from the bestpractices.dev project page.
2. Add it to the README badge row (next to CI / Codecov / Scorecard) — that's
   the code change that closes **#139**, done as a one-line PR.
3. On the next weekly Scorecard run, **CII-Best-Practices** moves 0 → ≥ 5 and
   code-scanning alert #4 clears.
