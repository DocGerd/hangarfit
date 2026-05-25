# §2 Architecture Constraints

These are the non-negotiables. Every later choice in this document set
respects them; if a future decision needs to break one, that is an ADR in
its own right.

## Technical constraints

| Constraint | Rationale |
|------------|-----------|
| **Python 3.12 or newer.** | CI runs the test suite against a single Python 3.12 job (see `.github/workflows/ci.yml`); 3.12 is the durable lower bound, anchored to the Ubuntu 24.04 LTS interpreter since CPython itself names no LTS ([ADR-0009](../adr/0009-single-supported-python-version.md)). Older interpreters are not tested and not supported. |
| **Single-binary CLI** — one entry point (`hangarfit`), one process per invocation. | The tool is invoked interactively; there is no daemon, no service, and no IPC. Keeps the install/run loop trivial. |
| **No network calls at runtime.** | The tool runs in a hangar office; assume no reliable internet. Any input is local YAML; any output is local files. |
| **No persistent state between invocations.** | The scenario YAML carries everything the tool needs. Two consecutive runs with the same input must produce the same output without any side channel. Enables determinism (quality goal #2) and matches the "tool, not service" stance. |
| **All third-party dependencies are pure-Python or wheel-installable.** | Contributors install with `pip install -e ".[dev]"` and expect it to just work on Linux, macOS, and WSL. Anything requiring a system toolchain (compiler, native library beyond what wheels supply) raises the contribution bar. |

## Process constraints

| Constraint | Rationale |
|------------|-----------|
| **Plain-Markdown documentation only.** No MkDocs, Sphinx, or other site generator. | Picking a doc site is a real decision with its own tradeoffs; until that decision is made (in its own ADR), GitHub's built-in Markdown rendering is enough. Mermaid diagrams render natively in GitHub Markdown. |
| **Strict GitFlow.** `main` and `develop` are protected; all work lands via PR from `feature/<slug>` branches. | The maintainer is the sole reviewer and merger. The PR review skill is the dress rehearsal that catches the obvious issues before the human looks. |
| **Issue-driven.** Every change has a GitHub issue; PR bodies link to the issue with `Closes #N`. | Issues are the unit of work-tracking; milestones are the unit of release scope. PR-without-issue would bypass that scope-control. |
| **All consequential decisions tracked in ADRs.** | This is what [`docs/adr/`](../adr/) is for. The architecture docs (this directory) describe *what* the system is; the ADRs explain *why* it was built that way and what alternatives were rejected. |

## Constraints that are *not* in this list

- **No backwards-compatibility constraint** — the project is
  pre-release. The schemas (`hangarfit.check/v1`, `hangarfit.solve/v1`)
  have a version field already, so future breaking changes are
  expressible, but no external consumer has been promised stability yet.
- **No performance budget** — the solver runs against a nine-aircraft
  fleet on a single hangar. Wall-clock budgets are set per-invocation
  (`--budget`, default 30 s), not as a project-wide SLO. If a real
  performance constraint emerges from operational use, it gets an ADR.
- **No accessibility / i18n constraint** — the tool is a CLI used by one
  person at a time, in one club, in one language. If that changes, the
  constraint changes.
