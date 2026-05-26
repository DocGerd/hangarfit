# OpenSSF Baseline Level 1 — self-attestation checklist

This is the project's self-attestation for **[OpenSSF Security Baseline](https://baseline.openssf.org/)
Level 1**, version **2026-02-19**. It maps every Level-1 control to a status
(**Met** / **N/A**), a justification, and an evidence path or URL — the same
answer-script pattern as the
[Best Practices Badge sheet](openssf-best-practices-badge.md), so the attestation
can be re-verified later without re-deriving the rationale.

It complements [`security-posture.md`](security-posture.md) (which explains the
Scorecard structural zeros) and [`openssf-best-practices-badge.md`](openssf-best-practices-badge.md)
(the Best Practices Badge answer script). The MFA control below
(`OSPS-AC-01.01`) is shared with `security-posture.md`.

- **Baseline version:** `2026-02-19` (source tag [`v2026.02.19`](https://github.com/ossf/security-baseline/tree/v2026.02.19/baseline))
- **Level:** 1
- **Repository:** <https://github.com/DocGerd/hangarfit>
- **Default branch:** `develop`
- **License:** Apache-2.0
- **Language:** Python (≥ 3.12)
- **Maintainership:** single maintainer (@DocGerd)

> **Attestation.** As of 2026-05-26, this project complies with OSPS Baseline
> version 2026-02-19 level 1.

> **Scope of Level 1.** The Level-1 tier defines **24** assessment requirements
> across seven categories (Access Control, Build & Release, Documentation,
> Governance, Legal, Quality, Vulnerability Management). The **Security
> Assessment (SA)** category contributes *zero* Level-1 requirements — all its
> controls begin at Level 2 — so it does not appear below. All 24 Level-1
> controls are **Met**.

---

## 0. Items that need *human* judgment

Everything else is mechanical and independently checkable. Two controls rest on
a maintainer attestation or a conditional that the tooling scores as passed:

1. **`OSPS-AC-01.01` (MFA)** — *self-attestation, not third-party-verifiable.*
   2FA is enabled on the maintainer account, but a personal account's 2FA state
   is not exposed by any GitHub API to outside observers. Marked **Met**; the
   reasoning and verifiability limit are documented in
   [`security-posture.md` §Two-factor authentication](security-posture.md#two-factor-authentication-osps-ac-0101).
   (Only the maintainer can truthfully assert this — it is true.)
2. **`OSPS-QA-04.01` (multi-repository codebase list)** — *conditional that does
   not trigger.* The requirement applies to "projects with multiple
   repositories." `hangarfit` is a **single** repository, so the condition does
   not apply; the OSPS assessment scores an untriggered conditional as passed.
   Marked **Met** on that single-repo basis (equally defensible as N/A).

---

## Access Control (AC)

| Control | Status | Justification + evidence |
|---|---|---|
| `OSPS-AC-01.01` — Use MFA for sensitive actions | **Met** (self-attest) | 2FA enabled on @DocGerd; see [`security-posture.md` §Two-factor authentication](security-posture.md#two-factor-authentication-osps-ac-0101) and §0 above. |
| `OSPS-AC-02.01` — Restrict collaborator permissions | **Met** | Single-maintainer repo; GitHub grants new collaborators no access until explicitly assigned (least privilege by default). Sole owner named in [`.github/CODEOWNERS`](../.github/CODEOWNERS). |
| `OSPS-AC-03.01` — Protect primary branch (no direct commit) | **Met** | Branch protection on `develop` (default) and `main`: pull-request required, `enforce_admins=true`. Verified live via `gh api repos/DocGerd/hangarfit/branches/{develop,main}/protection`; rationale in [`security-posture.md` §Branch-Protection](security-posture.md#branch-protection-score-3). |
| `OSPS-AC-03.02` — Protect primary branch (no deletion) | **Met** | Same protection objects: `allow_deletions=false` and `allow_force_pushes=false` on both `develop` and `main` (verified live). |

## Build and Release (BR)

| Control | Status | Justification + evidence |
|---|---|---|
| `OSPS-BR-01.01` — Sanitize untrusted CI/CD metadata | **Met** | [`release.yml`](../.github/workflows/release.yml) reads any `workflow_dispatch` input through an `env:` indirection (injection-safe); workflows default to `permissions: contents: read`. |
| `OSPS-BR-01.03` — No privileged creds on untrusted snapshots | **Met** | [`ci.yml`](../.github/workflows/ci.yml) and [`codeql.yml`](../.github/workflows/codeql.yml) set top-level `permissions: contents: read` and trigger only on `develop`/`main`; `release.yml`'s elevated permissions are gated to `v*` tag pushes. |
| `OSPS-BR-03.01` — Encrypted project-channel URIs | **Met** | All official project URIs are HTTPS GitHub URLs (README badges, `SECURITY.md` advisory link, repository home). |
| `OSPS-BR-03.02` — Authenticated distribution channel | **Met** | Distribution is GitHub Releases over HTTPS; [`release.yml`](../.github/workflows/release.yml) additionally Sigstore-signs each artifact (keyless cosign → `.sigstore.json` bundle). The project deliberately does not publish to PyPI (see [`security-posture.md` §Packaging](security-posture.md#packaging-score--1)). |
| `OSPS-BR-07.01` — Keep secrets out of VCS | **Met** | [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) large-file/whitespace hooks + GitHub secret-scanning push protection (public repo); `git ls-files` tracks no key/credential files. |

## Documentation (DO)

| Control | Status | Justification + evidence |
|---|---|---|
| `OSPS-DO-01.01` — Publish user guides | **Met** | [`README.md`](../README.md) (install, `check`/`solve` usage, exit codes, examples) plus [`docs/architecture/`](architecture/) and [`docs/adr/`](adr/). |
| `OSPS-DO-02.01` — Document how to report defects | **Met** | [`.github/ISSUE_TEMPLATE/`](../.github/ISSUE_TEMPLATE) (bug/feature/question), [`CONTRIBUTING.md`](../CONTRIBUTING.md) issues-first policy, [`SECURITY.md`](../SECURITY.md) for vulnerabilities. |

## Governance (GV)

> Level-1 governance is satisfied by existing community docs. A dedicated
> `GOVERNANCE.md` (maintainer roles / decision-making) is **not** an L1
> requirement — those are higher-tier GV controls — and is deferred to the
> Best Practices Silver milestone (#237).

| Control | Status | Justification + evidence |
|---|---|---|
| `OSPS-GV-02.01` — Public discussion mechanism | **Met** | GitHub [Issues](https://github.com/DocGerd/hangarfit/issues) and [Discussions](https://github.com/DocGerd/hangarfit/discussions) both enabled (`has_issues`/`has_discussions` verified live), plus issue templates and the public PR-review process. |
| `OSPS-GV-03.01` — Publish a contribution guide | **Met** | [`CONTRIBUTING.md`](../CONTRIBUTING.md) documents the GitFlow contribution process, PR requirements, review, and merge policy. |

## Legal (LE)

| Control | Status | Justification + evidence |
|---|---|---|
| `OSPS-LE-02.01` — Source under an open-source license | **Met** | Apache-2.0 (OSI-approved); [`LICENSE`](../LICENSE) and `license = "Apache-2.0"` in [`pyproject.toml`](../pyproject.toml). |
| `OSPS-LE-02.02` — Released assets under an open-source license | **Met** | Same Apache-2.0; `license-files = ["LICENSE"]` ships the license inside the sdist and wheel (PR #252). |
| `OSPS-LE-03.01` — License in a well-known location (source) | **Met** | [`LICENSE`](../LICENSE) at the repository root. |
| `OSPS-LE-03.02` — License travels with released assets | **Met** | `license-files = ["LICENSE"]` → `LICENSE` present in the sdist (`hangarfit-*/LICENSE`) and wheel (`*.dist-info/licenses/LICENSE`), confirmed by `python -m build` (PR #252). |

## Quality (QA)

| Control | Status | Justification + evidence |
|---|---|---|
| `OSPS-QA-01.01` — Source code publicly readable at a static URL | **Met** | <https://github.com/DocGerd/hangarfit> — `private=false`, `visibility=public` (verified live). |
| `OSPS-QA-01.02` — Public change history | **Met** | Public Git history on GitHub (commit log with author and timestamp per change). |
| `OSPS-QA-02.01` — Publish the direct-dependency list | **Met** | `[project] dependencies` + `[project.optional-dependencies] dev` in [`pyproject.toml`](../pyproject.toml); hash-pinned [`requirements-dev.txt`](../requirements-dev.txt), [`requirements-build.txt`](../requirements-build.txt), [`requirements-fuzz.txt`](../requirements-fuzz.txt). |
| `OSPS-QA-04.01` — List of codebases (multi-repo) | **Met** | Single-repository project — the multi-repo conditional does not trigger; see §0. |
| `OSPS-QA-05.01` — No generated executable artifacts in VCS | **Met** | `git ls-files` tracks no executables; [`.gitignore`](../.gitignore) excludes `build/`, `dist/`, `*.egg-info/`. |
| `OSPS-QA-05.02` — No unreviewable binary artifacts in VCS | **Met** | No tracked binaries/images (`git ls-files` clean-tree audit, issue #231); [`.gitignore`](../.gitignore) root-anchored `/*.png` + `out*.png` guard render outputs (PR #251). |

## Vulnerability Management (VM)

| Control | Status | Justification + evidence |
|---|---|---|
| `OSPS-VM-02.01` — Publish vulnerability-reporting contacts | **Met** | [`SECURITY.md`](../SECURITY.md) "Reporting a Vulnerability" routes to a [private GitHub security advisory](https://github.com/DocGerd/hangarfit/security/advisories/new). |

---

## Source of truth

- Authoritative control text: the eight `OSPS-*.yaml` files at
  <https://github.com/ossf/security-baseline/tree/v2026.02.19/baseline> (tag
  `v2026.02.19`).
- Human-readable cross-reference: <https://baseline.openssf.org/versions/2026-02-19>.

When re-certifying, re-derive the Level-1 set by selecting requirements whose
`applicability` lists "Maturity Level 1" in those YAML files, and refresh the
attestation date above.
