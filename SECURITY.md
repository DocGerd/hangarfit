# Security Policy

## Supported Versions

`hangarfit` is a Phase 1 project. Only the `main` branch (current release) receives security updates. Older tagged versions do not receive backports.

| Version | Status |
|---------|--------|
| Current (`main`) | ✅ Supported |
| Older releases | ⚠️ No backports |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.** Instead, use GitHub's [private security advisory](https://github.com/DocGerd/hangarfit/security/advisories/new) feature to report the issue confidentially.

For non-sensitive questions or feature requests, regular issues are welcome.

## Response Expectation

This is a hobby project maintained on a best-effort basis. We appreciate vulnerability reports and will make a reasonable effort to investigate and respond, but we cannot promise SLA timelines or guaranteed patch releases.

## Scope

The following areas have non-zero attack surface and merit scrutiny:

- **YAML loader** (`src/hangarfit/loader.py`): parses user-supplied layout and fleet files
- **Visualizer** (`src/hangarfit/visualize.py`): renders user-supplied aircraft placements and conflict data

We welcome reports of resource exhaustion, parsing edge cases, or rendering defects that could affect the tool's reliability or the user's system.

## Project security posture

`hangarfit` runs an [OpenSSF Scorecard](https://securityscorecards.dev/) workflow on every push to `develop` (see [`.github/workflows/scorecard.yml`](.github/workflows/scorecard.yml)). Several of the checks score 0 for structural reasons specific to this project — single maintainer, single deployment site, deliberately unpublished — rather than because the underlying concern is unaddressed.

The rationale for each structural zero, plus what we *do* in lieu of the standard remediation (e.g. the in-repo `/pr-review` toolkit substituting for formal `APPROVED` reviews), is documented in [`docs/security-posture.md`](docs/security-posture.md). Read that before drawing conclusions from the aggregate Scorecard number.
