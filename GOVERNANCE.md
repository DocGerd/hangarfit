# Governance

This document describes how decisions are made in `hangarfit` and who is
responsible for what. It is deliberately honest about the project's size:
`hangarfit` is a small, single-maintainer hobby tool for a flying club, not a
multi-stakeholder foundation project. The model below reflects that reality
rather than borrowing ceremony the project does not need.

## Model

`hangarfit` follows a **single-maintainer (BDFL-style)** model. The maintainer
is the final decision-maker on scope, design, and what ships. This is not a
committee or a voting body; it is one person maintaining a focused tool, with a
transparent, written process so that decisions and their rationale are visible
to anyone reading the repository.

The maintainer commits to keeping that process transparent: every change is
tracked by a public issue, lands through a reviewed pull request, and — for
anything that shapes the system — is recorded in an
[Architecture Decision Record](docs/adr/).

## How decisions are made

- **Proposing a change.** Anyone may open an issue (bug report, feature
  request, or design question) using the templates in
  [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/). Discussion happens on
  the issue and in [GitHub Discussions](https://github.com/DocGerd/hangarfit/discussions).
- **Accepting a change.** Work is done on a `feature/<slug>` branch off
  `develop`, opened as a pull request that links its issue with `Closes #N`.
  Every PR is reviewed before it merges (see
  [Code review in `CONTRIBUTING.md`](CONTRIBUTING.md#code-review)); the
  maintainer is the only approver and merger. The full branching model is
  GitFlow, documented in [`CONTRIBUTING.md`](CONTRIBUTING.md#workflow-gitflow).
- **Architectural decisions.** Choices that shape the substrate are written up
  as ADRs in [`docs/adr/`](docs/adr/) and the arc42 docs in
  [`docs/architecture/`](docs/architecture/), so the *why* behind a decision
  outlives the conversation that produced it.
- **Disagreements.** Raise them on the relevant issue or PR thread. The
  maintainer makes the final call and records the reasoning in the thread or an
  ADR.

## Key roles and current holders

For a single-maintainer project these roles are all currently held by the same
person; they are listed separately so the *responsibilities* are explicit and
so the list stays meaningful if the project ever grows.

| Role | Responsibility | Current holder |
|---|---|---|
| **Maintainer / project lead** | Final say on scope, design, and releases; reviews and merges all PRs. | [@DocGerd](https://github.com/DocGerd) |
| **Security contact** | Receives and triages vulnerability reports; see [`SECURITY.md`](SECURITY.md). Reports go through GitHub [private security advisories](https://github.com/DocGerd/hangarfit/security/advisories/new). | [@DocGerd](https://github.com/DocGerd) |
| **Release manager** | Cuts releases from `develop` via the GitFlow `release/*` process, tags them, and publishes the GitHub release. | [@DocGerd](https://github.com/DocGerd) |
| **Code-of-Conduct enforcement** | Handles conduct reports per [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). | [@DocGerd](https://github.com/DocGerd) |

## Becoming a maintainer

There is no formal maintainer-promotion process today because the project does
not yet need one. If `hangarfit` attracts sustained contribution, the
maintainer will revisit this document to add additional maintainers and a
shared decision process at that point. Until then, the fastest way to influence
direction is a well-argued issue or a clean pull request.

## Related documents

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute, the GitFlow
  workflow, PR requirements, and the code-review process.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — expected behaviour and how to
  report conduct issues.
- [`SECURITY.md`](SECURITY.md) — how to report a vulnerability.
- [`CLAUDE.md`](CLAUDE.md) — the operational guide (workflow, tooling,
  project-local configuration).
