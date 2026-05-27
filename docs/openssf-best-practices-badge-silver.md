# OpenSSF Best Practices Badge — Silver-tier answer script

This is the working script for the **Silver** tier of the project's
[OpenSSF Best Practices Badge](https://www.bestpractices.dev/projects/12987)
(project 12987, already at **Passing**). It maps each **Silver** criterion to a
status (**Met** / **N/A** / **Unmet**), a justification, and an evidence URL, so
the live questionnaire can be filled in and re-certified later without
re-deriving the rationale. It mirrors the Passing-tier script in
[`openssf-best-practices-badge.md`](openssf-best-practices-badge.md); Silver
layers these criteria on top of the Passing set (which stays answered there).

- **Target tier:** Silver (builds on the awarded Passing tier).
- **Repository:** <https://github.com/DocGerd/hangarfit>
- **Default branch:** `develop`
- **License:** Apache-2.0
- **Language:** Python (≥ 3.12)
- **Crypto:** none — the tool does no cryptography, so every `crypto_*` criterion is **N/A**.
- **Statement coverage:** ~97% (full suite, measured via pytest-cov; authoritative figure on [Codecov](https://codecov.io/gh/DocGerd/hangarfit)) — clears Silver's ≥ 80%.

> **MUST / SHOULD / SUGGESTED:** only an unmet **MUST** (or an unmet **SHOULD**
> without a justified exception) blocks the badge. All Silver **MUST** criteria
> below are **Met** or legitimately **N/A**. The unmet items are all SHOULD /
> SUGGESTED (`dco`, `bus_factor`, `version_tags_signed`) and do **not** block.

> **Scope note — Silver vs Gold.** Verified against the authoritative
> [`criteria.yml`](https://raw.githubusercontent.com/coreinfrastructure/best-practices-badge/main/criteria/criteria.yml)
> (Silver = the `'1'` level block; 55 criteria). The **two-distinct-people**
> review requirement (`two_person_review`), `code_review_standards`, and
> `security_review` are all **Gold** (`'2'`) criteria, **not** Silver — so the
> single-maintainer cap that produces the Scorecard Code-Review zero (see
> [`security-posture.md`](security-posture.md)) does **not** block Silver. Those
> are tracked as the parked Gold issue #242.

---

## 0. Items that need *your* action

Everything else is mechanical. These need a human:

1. **Submit the answers.** Enter the statuses below in the Silver questionnaire
   at <https://www.bestpractices.dev/projects/12987>, then, once Silver is
   awarded, swap the README badge to the Silver variant. Criteria you mark
   **Met** that the form flags `met_url_required` need a public evidence URL —
   most map to an obvious repo file (e.g. `governance` → `GOVERNANCE.md`), but
   `assurance_case`'s evidence is the [Assurance case](#assurance-case) section
   of *this* document, so point it at this file's rendered GitHub URL
   (`…/docs/openssf-best-practices-badge-silver.md#assurance-case`).
2. **Self-attestations** — only the maintainer can truthfully assert these; they
   are all true: `implement_secure_design`, `documentation_current`,
   `access_continuity`, `regression_tests_added50`.
3. **Non-blocking SHOULD/SUGGESTED gaps** — decide whether to leave them or
   close them: `dco` (no sign-off gate), `bus_factor` (genuinely 1 — single
   maintainer), `version_tags_signed` (deliberately using keyless Sigstore
   artifact signing instead of signed git tags). None block Silver.

---

## Assurance case (`assurance_case`)

The Silver `assurance_case` MUST asks for a written argument, with evidence, for
why the project's security requirements are met. Here it is.

**What the software is.** `hangarfit` is an **offline command-line tool**. It
reads user-authored YAML (fleet, hangar, layout, scenario files), validates a
parking layout geometrically, and emits a result plus an optional PNG. It has
**no network I/O, no authentication, no persistent state, no privilege
escalation, and no cryptography**.

**Threat model.** The only untrusted input is the YAML the user points it at.
The realistic threats are therefore: (a) malformed or malicious YAML causing an
uncontrolled crash or wrong result; (b) resource exhaustion from pathological
input; (c) YAML deserialization abuse (arbitrary-object construction). There is
no remote attacker, no multi-tenant data, and no secret to exfiltrate.

**Trust boundary.** The single trust boundary is the **loader**
([`src/hangarfit/loader.py`](../src/hangarfit/loader.py)), which parses
untrusted files into validated, typed model objects. Everything downstream
(`geometry`, `collisions`, `solver`, `towplanner`, `visualize`) operates only
on already-validated `models.py` objects, never on raw input.

**Argument that secure design is applied.** YAML is parsed with
`yaml.safe_load` (no arbitrary Python object construction); the loader enforces
a schema and cross-reference invariants and validates plane IDs (#176) before
any object is built; the code uses no `eval`/`exec`, spawns no subprocess, and
opens no socket. The one non-obvious geometric invariant — the determinant-−1
coordinate transform — is documented in
[ADR-0002](adr/0002-determinant-minus-one-transform.md) and guarded by golden
tests and the `geometry-invariant-guard` review agent.

**Argument that common weaknesses are countered.** The untrusted-input path is
**fuzzed** nightly (Atheris + Hypothesis over the loader, `fuzz.yml` / #143 —
which already found and fixed a non-UTF-8 read gap); the whole codebase is
**statically analyzed** on every PR (CodeQL default Python suite + `ruff` +
`mypy --strict`); inputs are **validated at the boundary** as above. Because the
tool has no network, credential, or crypto surface, the large CWE classes that
dominate those domains are out of scope by construction.

**Conclusion.** The attack surface is local YAML parsing only; it is validated,
fuzzed, and statically analyzed, and the tool holds nothing an attacker would
gain by abusing it. The security requirements (don't crash on hostile input,
don't execute attacker-controlled code, produce correct results) are met. See
also [`SECURITY.md`](../SECURITY.md) and [`security-posture.md`](security-posture.md).

---

## 1. Prerequisites & project oversight

| Criterion | Status | Justification + evidence |
|---|---|---|
| `achieve_passing` (MUST) | **Met** | Passing badge awarded 2026-05-26 — project [12987](https://www.bestpractices.dev/projects/12987). |
| `contribution_requirements` (MUST) | **Met** | [`CONTRIBUTING.md`](../CONTRIBUTING.md) §Pull request requirements (green CI on 3.12, tests for behaviour changes, conventional commits, review-thread resolution). |
| `dco` (SHOULD) | **Unmet** (non-blocking) | No Developer Certificate of Origin sign-off gate; a single maintainer with an Apache-2.0 inbound=outbound model. SHOULD — left unmet by choice. |
| `governance` (MUST) | **Met** | [`GOVERNANCE.md`](../GOVERNANCE.md) — single-maintainer (BDFL) model and decision process. |
| `code_of_conduct` (MUST) | **Met** | [`CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md) (Contributor Covenant, real enforcement contact), linked from README and CONTRIBUTING. |
| `roles_responsibilities` (MUST) | **Met** | [`GOVERNANCE.md`](../GOVERNANCE.md) "Key roles and current holders" table. |
| `access_continuity` (MUST) | **Met** | Project is public and Apache-2.0 on GitHub — no private infrastructure to lose; anyone can fork and continue. Succession noted in [`GOVERNANCE.md`](../GOVERNANCE.md) "Becoming a maintainer". |
| `bus_factor` (SHOULD) | **Unmet** (non-blocking) | Bus factor is 1 (single maintainer). Stated honestly in [`GOVERNANCE.md`](../GOVERNANCE.md); SHOULD, does not block. |

## 2. Documentation

| Criterion | Status | Justification + evidence |
|---|---|---|
| `documentation_roadmap` (MUST) | **Met** | README "Scope" (Phase 1/2a shipped + explicit out-of-scope list) + public [milestones](https://github.com/DocGerd/hangarfit/milestones). |
| `documentation_architecture` (MUST) | **Met** | [`docs/architecture/`](architecture/) (arc42) + [`docs/adr/`](adr/). |
| `documentation_security` (MUST) | **Met** | [`SECURITY.md`](../SECURITY.md) + [`security-posture.md`](security-posture.md) + the assurance case above. |
| `documentation_quick_start` (MUST) | **Met** | README "Install" + "Usage" (`pip install -e .`; `hangarfit check …`). |
| `documentation_current` (MUST) | **Met** (self-attest) | Docs are in-repo and updated per PR; README/CHANGELOG current. |
| `documentation_achievements` (MUST) | **Met** | README badge row (CI / Codecov / Scorecard / Best Practices) + [`CHANGELOG.md`](../CHANGELOG.md). |

## 3. Accessibility & site security

| Criterion | Status | Justification + evidence |
|---|---|---|
| `accessibility_best_practices` (SHOULD) | **N/A** | Command-line tool, no GUI/web UI (README out-of-scope list). |
| `internationalization` (SHOULD) | **N/A** | Single-club English-only tool; no user-facing localizable UI. |
| `sites_password_security` (MUST) | **N/A** | The project operates no website that stores user passwords (GitHub-hosted only). |

## 4. Change control

| Criterion | Status | Justification + evidence |
|---|---|---|
| `maintenance_or_update` (MUST) | **Met** | Actively maintained: tagged releases (v0.1.0, v0.6.0, v0.6.1), recent commits, `[Unreleased]` section in [`CHANGELOG.md`](../CHANGELOG.md). |

## 5. Reporting

| Criterion | Status | Justification + evidence |
|---|---|---|
| `report_tracker` (MUST) | **Met** | GitHub Issues + [`.github/ISSUE_TEMPLATE/`](../.github/ISSUE_TEMPLATE) (bug/feature/question). |
| `vulnerability_report_credit` (MUST) | **N/A** | No vulnerabilities have been reported to date, so there are none to credit (`na_allowed`); [`SECURITY.md`](../SECURITY.md) welcomes reports and the project will credit reporters when any are resolved. |
| `vulnerability_response_process` (MUST) | **Met** | [`SECURITY.md`](../SECURITY.md) documents the private-advisory reporting path and a best-effort response expectation. |

## 6. Quality

| Criterion | Status | Justification + evidence |
|---|---|---|
| `coding_standards` (MUST) | **Met** | `ruff` lint (`E,F,I,B,UP,SIM`) + `mypy` strict configured in [`pyproject.toml`](../pyproject.toml); conventions in CLAUDE.md / CONTRIBUTING.md. |
| `coding_standards_enforced` (MUST) | **Met** | Enforced by [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) + CI ([`ci.yml`](../.github/workflows/ci.yml) runs ruff + ruff format --check + mypy, merge-gated). |
| `build_standard_variables` (MUST) | **N/A** | Compiler-flag criterion (CC/CFLAGS). Pure-Python PEP 517 build, no compilation. |
| `build_preserve_debug` (SHOULD) | **N/A** | Compiler debug-symbol criterion; no compiled artifacts. |
| `build_non_recursive` (MUST) | **N/A** | "Recursive make" criterion; no make-based build (`python -m build`). |
| `build_repeatable` (MUST) | **Met** | `python -m build` from a hash-pinned toolchain ([`requirements-build.txt`](../requirements-build.txt)); release built `--no-isolation` against pinned setuptools in [`release.yml`](../.github/workflows/release.yml). |
| `installation_common` (MUST) | **Met** | `pip install -e .` — standard Python install (README "Install"). |
| `installation_standard_variables` (MUST) | **N/A** | DESTDIR/prefix autotools criterion; pip manages installation location. |
| `installation_development_quick` (MUST) | **Met** | `pip install -e ".[dev]"` yields a working dev environment in one step (README + CONTRIBUTING First-time setup). |
| `external_dependencies` (MUST) | **Met** | Declared in [`pyproject.toml`](../pyproject.toml) `[project] dependencies` + `[dev]`; hash-pinned in `requirements-*.txt`. |
| `dependency_monitoring` (MUST) | **Met** | [`.github/dependabot.yml`](../.github/dependabot.yml) (weekly pip + github-actions); CodeQL + Scorecard also run. |
| `updateable_reused_components` (MUST) | **Met** | Dependencies are version-ranged in `pyproject.toml`, not vendored/forked; Dependabot keeps them updateable. |
| `interfaces_current` (SHOULD) | **Met** | Targets current Python (3.12); deps kept current via Dependabot. |
| `automated_integration_testing` (MUST) | **Met** | [`ci.yml`](../.github/workflows/ci.yml) runs the full `pytest` suite on every PR into develop/main. |
| `regression_tests_added50` (MUST) | **Met** (self-attest) | Per-PR test policy ([`CONTRIBUTING.md`](../CONTRIBUTING.md): tests for any behaviour change); bug fixes ship with regression fixtures in `tests/fixtures/`. |
| `test_statement_coverage80` (MUST) | **Met** | ~97% statement coverage (full suite; `ci.yml` uploads to [Codecov](https://codecov.io/gh/DocGerd/hangarfit)) — well above ≥ 80%. |
| `test_policy_mandated` (MUST) | **Met** | [`CONTRIBUTING.md`](../CONTRIBUTING.md) §Pull request requirements mandates tests for behaviour changes. |
| `tests_documented_added` (MUST) | **Met** | The add-a-fixture policy is documented in [`CONTRIBUTING.md`](../CONTRIBUTING.md). |
| `warnings_strict` (MUST) | **Met** | `mypy` strict (`disallow_untyped_defs` on `hangarfit.*`) + `ruff` lint and format `--check` enforced in CI (merge-blocking). |

## 7. Security

| Criterion | Status | Justification + evidence |
|---|---|---|
| `implement_secure_design` (MUST) | **Met** (self-attest) | See the assurance case above: boundary input-validation, `yaml.safe_load`, no eval/subprocess/network, invariant guards. |
| `crypto_weaknesses` (MUST) | **N/A** | No cryptography in the tool. |
| `crypto_algorithm_agility` (SHOULD) | **N/A** | No cryptography. |
| `crypto_credential_agility` (MUST) | **N/A** | No cryptography / no stored credentials. |
| `crypto_used_network` (SHOULD) | **N/A** | The tool performs no network communication. |
| `crypto_tls12` (SHOULD) | **N/A** | No TLS termination in the tool. |
| `crypto_certificate_verification` (MUST) | **N/A** | No cryptography / no certificate handling. |
| `crypto_verification_private` (MUST) | **N/A** | No cryptography. |
| `signed_releases` (MUST) | **Met** | [`release.yml`](../.github/workflows/release.yml) signs the sdist + wheel with **keyless Sigstore/cosign**; each release artifact ships a `.sigstore.json` bundle verifiable via the Fulcio cert chain + Rekor (the public trust root, not a long-lived key — verification command and identity pin documented in the workflow header). |
| `version_tags_signed` (SUGGESTED) | **Unmet** (non-blocking) | Git tags are not GPG/SSH-signed; the project deliberately signs the **release artifacts** with Sigstore instead (rationale in [`release.yml`](../.github/workflows/release.yml) header). SUGGESTED — does not block. |
| `input_validation` (MUST) | **Met** | The loader validates untrusted YAML (schema + cross-reference invariants + plane-id validation #176) and is fuzz-hardened (`tests/fuzz/`, #143). |
| `hardening` (SHOULD) | **Met** | Hash-pinned dependency/build/fuzz lockfiles (`--require-hashes`), SHA-pinned GitHub Actions, least-privilege workflow `permissions:`, branch protection with `enforce_admins`. |
| `assurance_case` (MUST) | **Met** | The **Assurance case** section above (threat model, trust boundary, secure-design argument, weakness-countering argument). |

## 8. Analysis

| Criterion | Status | Justification + evidence |
|---|---|---|
| `static_analysis_common_vulnerabilities` (MUST) | **Met** | CodeQL default Python query suite on every PR ([`codeql.yml`](../.github/workflows/codeql.yml)) + `ruff` + `mypy`. |
| `dynamic_analysis_unsafe` (MUST) | **N/A** | This criterion targets **memory-unsafe** languages (e.g. C/C++ + ASAN). `hangarfit` is pure (memory-safe) Python, so the conditional does not trigger. (The project nonetheless runs nightly Atheris/Hypothesis fuzzing — `fuzz.yml`, #143 — which exceeds what the criterion would ask.) |

---

## Verdict

All Silver **MUST** criteria are **Met** or legitimately **N/A**; the only unmet
criteria are the non-blocking `dco` / `bus_factor` (SHOULD) and
`version_tags_signed` (SUGGESTED). **Silver is achievable now** — it is *not*
gated by the single-maintainer two-person-review cap, which lives at Gold
(#242). Enter the statuses above at
<https://www.bestpractices.dev/projects/12987> and bump the README badge once
Silver is awarded.

## Source of truth

- Criteria: the `'1'` (Silver) block of
  [`criteria/criteria.yml`](https://raw.githubusercontent.com/coreinfrastructure/best-practices-badge/main/criteria/criteria.yml)
  in `coreinfrastructure/best-practices-badge` (55 Silver criteria; `'0'` =
  Passing, `'2'` = Gold).
- Rendered cross-reference: <https://www.bestpractices.dev/en/criteria/1>.
