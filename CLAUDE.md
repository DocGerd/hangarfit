# Project context — hangarfit

This file is the durable **operational** context for the project: how we work, where the live config lives, and what is still uncertain. Architectural knowledge — the domain model, the coordinate convention, the module map, the decisions that shaped the substrate — lives in [`docs/architecture/`](docs/architecture/) and [`docs/adr/`](docs/adr/). Read this file first in any new session; follow the Quick Reference below to drill in.

---

## What this project is

`hangarfit` is an **on-demand exception tool** for a flying club: when the standard hangar parking layout breaks (delayed return, surprise maintenance, etc.), it helps find *a* valid alternative arrangement. The tool checks whether a hand-authored candidate layout is physically valid and renders a top-down PNG so a human can eyeball it; the solver searches for one when no candidate is in hand.

**Status:** Phase 1 (substrate) and Phase 2a (static layout solver, `hangarfit solve`) have both shipped. Live milestone status lives in auto-memory and GitHub milestones, not here.

---

## Quick Reference — where architectural content actually lives

| Looking for | See |
|---|---|
| What `hangarfit` is and the quality goals it optimizes for | [§1 Introduction & Goals](docs/architecture/01-introduction-and-goals.md) |
| What is in / out of scope, the external actors, exit-code semantics pointer | [§3 Context & Scope](docs/architecture/03-context-and-scope.md) |
| Module map (`cli`, `loader`, `models`, `geometry`, `collisions`, `solver`, `visualize`) and per-module responsibilities | [§5 Building Block View](docs/architecture/05-building-block-view.md) |
| Runtime flow of `check` and `solve` invocations | [§6 Runtime View](docs/architecture/06-runtime-view.md) |
| **The parts model** (collision rule, why parts not bbox, `struts:` block) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-parts-model) + [ADR-0001](docs/adr/0001-aircraft-parts-model.md) |
| **The coordinate convention + the determinant-−1 transform trap** | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-coordinate-convention) + [ADR-0002](docs/adr/0002-determinant-minus-one-transform.md) |
| **The maintenance bay rule** (current `bay_intrusion` semantics) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-maintenance-bay-rule) + [ADR-0006](docs/adr/0006-bay-intrusion-maintenance-rule.md). The Phase 1 predecessor is preserved as [ADR-0005](docs/adr/0005-maintenance-bay-rule.md) (Superseded by ADR-0006). |
| Fleet composition (per-plane wing type, gear, movement mode, struts) | [`data/fleet.yaml`](data/fleet.yaml) — the source of truth; §8 calls out the strut-braced subset and the only low-wing |
| Hangar dimensions, door, maintenance bay rectangle | [`data/hangar.yaml`](data/hangar.yaml) — all values currently placeholders pending real measurement |
| Default clearances (`clearance_m`, `wing_layer_clearance_m`) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#default-clearances) |
| RR-MC solver algorithm and the determinism contract | [ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md) |
| Diversity metric (edit-count, thresholds) | [ADR-0004](docs/adr/0004-diversity-metric.md) |
| **The spread post-pass** (maximize inter-plane gap once valid) | [ADR-0008](docs/adr/0008-inter-plane-spread-soft-preference.md) |
| All architecture decisions, including superseded ones | [`docs/adr/`](docs/adr/) |

If you find yourself about to write a domain assertion in this file, **don't** — extend the relevant arc42 section or ADR instead. CLAUDE.md is for *how we work together*; arc42/ADR is for *what the system is and why*.

---

## Development workflow

**Strict GitFlow + issue-driven + PR-review on every change. The user is the only approver and merger.**

### Branching

| Branch | Purpose | Direct push allowed? |
|---|---|---|
| `main` | Production / release-tagged. | **No** |
| `develop` | Integration; default branch on GitHub. | No, only via PR from `feature/*` |
| `feature/<slug>` | One per issue; off `develop`. | Yes (Claude works here) |
| `release/<version>` | Cut from `develop`, PR'd into both `main` and `develop`. Use `/release-cut version=X.Y.Z` to automate this. | No, only via PR |
| `hotfix/<slug>` | Only if needed; off `main`. | No, only via PR |

### Per-PR process

1. Branch `feature/<slug>` off `develop`. Work, commit.
2. Open PR with `gh pr create` — base `develop`, body includes `Closes #N`.
3. Invoke `/pr-review` (or the `pr-review-toolkit:review-pr` skill).
4. Convert each finding into a **review thread on the diff** (via `gh pr review` line comments / `gh api .../pulls/<n>/comments`). Findings never live only in chat.
5. Resolve every thread: fix the code (preferred) or reply with rationale, then mark resolved.
6. If the changes were non-trivial, re-run the review.
7. Tell the user the PR is **clean and ready for final review**. The user approves and merges. **Never `gh pr merge` from Claude.**

### Issues

- Every change is tracked by a GitHub issue. No code without an issue.
- Issues are organized into milestones (one milestone = one releasable cut).
- PR bodies link to issues with `Closes #N` / `Fixes #N` (the body, not the title — only body syntax auto-closes).

---

## Subagents

Use the best-fitted model for the task. The model class to pick is "as much reasoning as the work needs" — heavy for novel design and deep review, lighter for mechanical work.

- **`pr-review-toolkit:code-reviewer`** — main PR review pass on every PR.
- **`pr-review-toolkit:comment-analyzer`** — for PRs that meaningfully change docs (README, CLAUDE.md, docstrings).
- **`pr-review-toolkit:silent-failure-hunter`** — for PRs touching loader or collision code.
- **`pr-review-toolkit:type-design-analyzer`** — when `models.py` changes.
- **`geometry-invariant-guard`** — for any PR touching `src/hangarfit/geometry.py` or `src/hangarfit/collisions.py`; guards the coordinate-transform sign-flip trap (see [ADR-0002](docs/adr/0002-determinant-minus-one-transform.md)).
- **`feature-dev:code-architect`** — only for genuinely novel design decisions, not routine implementation.

Most coding goes direct in-session. Subagent dispatch is for review work and isolated heavy lifts.

---

## Project-local Claude Code config

The `.claude/` directory holds team-shared Claude Code settings (currently: a PostToolUse pytest hook that auto-runs tests after edits under `src/hangarfit/` or `tests/`). See [.claude/README.md](.claude/README.md) for what's there and how to disable per-contributor via a gitignored `.claude/settings.local.json`.

---

## MCP servers

`.mcp.json` at the repo root declares the project-scoped GitHub MCP server so every contributor gets it automatically on a fresh clone — no per-user setup step. See also [.claude/README.md](.claude/README.md) for the broader Claude Code config ecosystem in this repo.

| Server | Transport | Purpose |
|---|---|---|
| `github` | HTTP (`https://api.githubcopilot.com/mcp/`) | Issue / PR / release inspection from Claude; complements the existing `gh` CLI. |

**Canonical upstream references (verify before editing `.mcp.json`):**
- GitHub MCP: https://github.com/github/github-mcp-server

If a URL or env-var name in `.mcp.json` ever stops working, check these first.

### Auth requirements

- **GitHub MCP** — Requires `GITHUB_PERSONAL_ACCESS_TOKEN` in your shell environment. Minimum permissions depend on which PAT type you create:
  - **Classic PAT:** `repo` + `read:org` scopes are sufficient for read operations; add `write:discussion` if you want Claude to create issues or PRs via the MCP server rather than `gh`.
  - **Fine-grained PAT:** Repository permissions `Contents: Read`, `Issues: Read`, `Pull requests: Read`; plus Organization permissions `Members: Read` for org-level lookups. Add the corresponding `Write` levels for create operations. Fine-grained PATs use different UI checkboxes from classic — the scope names above are classic-only.

### Verifying the servers loaded

After cloning and running `claude`, use the `/mcp` command. The `github` server should appear with status **connected**. If it shows **failed**, check that `GITHUB_PERSONAL_ACCESS_TOKEN` is set in your shell environment.

---

## Worktrees

Allowed but not the default. Use only when two feature branches need parallel work (e.g., long-running test suite while writing the visualizer). For sequential issue flow, plain branch checkout is simpler.

---

## Security policy & Scorecard rationale

Vulnerability reporting lives in [SECURITY.md](SECURITY.md). The rationale for the structural-zero OpenSSF Scorecard checks (Code-Review, Maintained, Contributors, Packaging) — why they score 0 by design and what we do instead — is documented in [docs/security-posture.md](docs/security-posture.md). If you're asked about the Scorecard number, point at that doc rather than the raw aggregate.

---

## Open questions / TBD before trusting output

- **Real measurements** for every aircraft (`measured: false` in `fleet.yaml`). All current dimensions are eyeballed placeholders.
- **Real hangar measurements** (`data/hangar.yaml`) — length, width, door position and width, maintenance bay rectangle (`center_x_m`, `width_m`, `depth_m` — back-anchored, partial-width).
- **Placeholder hangar can't fit the full fleet.** The placeholder hangar in [`data/hangar.yaml`](data/hangar.yaml) is too tight to fit every aircraft at once under the placeholder clearance budget. The default [`layouts/example.yaml`](layouts/example.yaml) is a deliberate 6-plane subset; test fixtures that need the full fleet use [`tests/fixtures/test_hangar_large.yaml`](tests/fixtures/test_hangar_large.yaml). Real hangar measurements will reset this.

The collision checker will run on placeholder data, but until the measurements are real, the output is illustrative only.

---

## Useful commands

```bash
# Install
pip install -e ".[dev]"

# Run tests
pytest

# Run only the slow set (excluded by default; see pyproject.toml addopts)
pytest -m slow
# Or run everything regardless of marker
pytest -m ""

# Lint + format check (CI also runs these)
ruff check src/ tests/
ruff format --check src/ tests/

# Auto-fix lint findings and format
ruff check --fix src/ tests/
ruff format src/ tests/

# Type check
mypy src/hangarfit/

# Regenerate the CI hash-pinned dev-deps lockfile. Required after
# editing EITHER `[project] dependencies` OR
# `[project.optional-dependencies] dev` in pyproject.toml — the lockfile
# is generated with `--extra dev`, which covers BOTH groups, so both
# need to be in sync. CI's `pip install -e . --no-deps` will silently
# skip a runtime dep that's in pyproject.toml but missing from the
# lockfile (ImportError surfaces only at test-collection time). The
# `lockfile-drift` CI job (see .github/workflows/ci.yml) enforces this
# invariant on every PR by regenerating the lockfile against the
# committed pyproject.toml and comparing the resolved
# `package==version` set. The job pins `pip-tools==7.5.3` on Python
# 3.12; use the same pip-tools version locally so the lockfile header
# and the regenerated content stay consistent with what the guard
# expects. `--no-strip-extras` is explicit so a future pip-tools 8.0
# default flip cannot silently prune transitive extras. Run on the
# single supported Python (3.12) — the interpreter the lockfile is
# resolved against and the only version CI tests.
pip-compile --generate-hashes --no-strip-extras --extra dev -o requirements-dev.txt pyproject.toml

# Regenerate the hash-pinned BUILD-toolchain lockfile. Source is
# `requirements-build.in` (build + setuptools + wheel). Required after
# bumping any of those or after `packaging` moves in requirements-dev.txt
# (the `.in` constrains shared transitive deps via `-c requirements-dev.txt`
# so the two lockfiles can be installed together in CI without skew).
# `--allow-unsafe` is REQUIRED — pip-tools classifies setuptools/wheel as
# "unsafe to pin" and comments them out by default, which would defeat the
# `--no-build-isolation` install in ci.yml. `--no-strip-extras` mirrors the
# dev lockfile (8.0 default-flip defense). The `build-lockfile-drift` CI
# job enforces this on every PR. Same toolchain as the dev lockfile:
# pip-tools 7.5.3 on Python 3.12.
pip-compile --generate-hashes --no-strip-extras --allow-unsafe -o requirements-build.txt requirements-build.in

# Regenerate the hash-pinned PIP-TOOLS bootstrap lockfile. Source is
# `requirements-pip-tools.in` (a single `pip-tools==7.5.3` pin). This is
# the toolchain the two lockfile-drift guard jobs install to regenerate
# the dev + build lockfiles above — hash-pinning it closes the residual
# `pipCommand not pinned by hash` Scorecard finding on the bare
# `pip install pip-tools==7.5.3` the guards used to run (#224). Required
# after bumping the pip-tools pin (do that here AND in the `.in`, in
# lockstep with the version named in the two regeneration commands
# above). `--allow-unsafe` is REQUIRED — pip-tools depends on
# setuptools/wheel, which pip-tools comments out by default; `--require-
# hashes` is all-or-nothing, so an un-pinned transitive dep would make
# the guard-job install fail. Same toolchain: pip-tools 7.5.3 / Python 3.12.
pip-compile --generate-hashes --no-strip-extras --allow-unsafe -o requirements-pip-tools.txt requirements-pip-tools.in

# CI: GitHub Actions runs `pytest` on Python 3.12 for PRs into
# develop/main (see .github/workflows/ci.yml). CI installs dev deps
# from the hash-pinned `requirements-dev.txt` with `--require-hashes`,
# the build toolchain from `requirements-build.txt` likewise, then
# installs the project itself in editable mode with `--no-deps
# --no-build-isolation` (reusing the hash-verified host setuptools/wheel
# instead of an unpinned isolated build env). No coverage gate yet.

# Phase 1 acceptance smoke test
hangarfit check layouts/example.yaml --render out.png

# GitFlow loops
git switch develop && git pull
git switch -c feature/<slug>
# ... work ...
git push -u origin feature/<slug>
gh pr create --base develop --title "..." --body "Closes #N ..."
```
