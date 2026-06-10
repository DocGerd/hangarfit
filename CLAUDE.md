# Project context — hangarfit

This file is the durable **operational** context for the project: how we work, where the live config lives, and what is still uncertain. Architectural knowledge — the domain model, the coordinate convention, the module map, the decisions that shaped the substrate — lives in [`docs/architecture/`](docs/architecture/) and [`docs/adr/`](docs/adr/). Read this file first in any new session; follow the Quick Reference below to drill in.

---

## What this project is

`hangarfit` is an **on-demand exception tool** for a flying club: when the standard hangar parking layout breaks (delayed return, surprise maintenance, etc.), it helps find *a* valid alternative arrangement. The tool checks whether a hand-authored candidate layout is physically valid and renders a top-down PNG so a human can eyeball it; the solver searches for one when no candidate is in hand.

**Status:** Phase 1 (substrate), Phase 2a (static layout solver, `hangarfit solve`), Phase 2b–2c (solver realism + spread/diversity polish), Phase 3a (tow-path planning, `hangarfit solve --render-paths`), Phase 3b (Reeds–Shepp reverse-capable tow motion), and Phase 4 (interactive 3D viewer, `hangarfit view`) have all shipped. Live milestone status lives in auto-memory and GitHub milestones, not here.

---

## Quick Reference — where architectural content actually lives

| Looking for | See |
|---|---|
| What `hangarfit` is and the quality goals it optimizes for | [§1 Introduction & Goals](docs/architecture/01-introduction-and-goals.md) |
| What is in / out of scope, the external actors, exit-code semantics pointer | [§3 Context & Scope](docs/architecture/03-context-and-scope.md) |
| Module map (`cli`, `loader`, `models`, `geometry`, `collisions`, `solver`, `towplanner`, `visualize`, `scene`, `viewer`, `metrics`, `brand`) and per-module responsibilities | [§5 Building Block View](docs/architecture/05-building-block-view.md) |
| Runtime flow of `check` and `solve` invocations | [§6 Runtime View](docs/architecture/06-runtime-view.md) |
| **The parts model** (collision rule, why parts not bbox, `struts:` block, the fuselage front/aft split, the **empennage** `tail`+`vertical_stabilizer` surfaces — a wingtip may overhang a low-winger's *low tailplane* but not its *cockpit*, and not its *fin* which rises into the wing layer) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-parts-model) + [ADR-0001](docs/adr/0001-aircraft-parts-model.md) + [ADR-0012](docs/adr/0012-fuselage-front-aft-split.md) + [ADR-0023](docs/adr/0023-empennage-tail-surfaces.md) |
| **The coordinate convention + the determinant-−1 transform trap** | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-coordinate-convention) + [ADR-0002](docs/adr/0002-determinant-minus-one-transform.md) |
| **The maintenance bay rule** (current `bay_intrusion` semantics) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-maintenance-bay-rule) + [ADR-0006](docs/adr/0006-bay-intrusion-maintenance-rule.md). The Phase 1 predecessor is preserved as [ADR-0005](docs/adr/0005-maintenance-bay-rule.md) (Superseded by ADR-0006). |
| Fleet composition (per-plane wing type, gear, movement mode, struts, canonical wheel positions) | [`data/fleet.yaml`](data/fleet.yaml) — the source of truth; §8 calls out the strut-braced subset and the only low-wing. Wheel positions are canonical per-aircraft data ([ADR-0013](docs/adr/0013-wheels-canonical-data.md)), not renderer heuristics |
| Hangar dimensions, door, maintenance bay rectangle | [`data/hangar.yaml`](data/hangar.yaml) — all values currently placeholders pending real measurement |
| **The real Airfield Herrenteich dataset** (DWG-measured hangar + published-spec, second-source-verified fleet incl. a folded Stemme S10 + a valid all-8 `layout.yaml`), kept **separate** from the synthetic `data/` placeholders | [`examples/herrenteich/`](examples/herrenteich/README.md) — real data; `data/` stays the synthetic demo/test fixtures |
| Default clearances (`clearance_m`, `wing_layer_clearance_m`) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#default-clearances) |
| RR-MC solver algorithm and the determinism contract | [ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md) |
| Diversity metric (edit-count, thresholds) | [ADR-0004](docs/adr/0004-diversity-metric.md) |
| **The spread post-pass** (maximize inter-plane gap once valid) | [ADR-0008](docs/adr/0008-inter-plane-spread-soft-preference.md) |
| **The tow-path planner** (empty-hangar fill, Reeds–Shepp arcs, `solve --render-paths`, exit-3 tow-routability) | [§5 Building Block View](docs/architecture/05-building-block-view.md) (`towplanner`) + [ADR-0007](docs/adr/0007-tow-path-planner-v1-scope.md) (v1 scope) + [ADR-0010](docs/adr/0010-reeds-shepp-motion-model.md) (v2 Reeds–Shepp motion) |
| **The staging apron** (`hangar.apron_depth_m` / `--apron-depth N\|auto`, slide-in from outside the door, reverse nose-out seeds, depth-0 byte-identical) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-door-is-a-visual-marker-only) + [ADR-0021](docs/adr/0021-tow-planner-staging-apron.md). `collisions.check` is apron-inert (forbids `y<0`); the apron is a planner-level motion concept |
| **The 3D viewer** (`hangarfit view`, interactive offline HTML, whole-fill tow timeline, the `scene/v1` JSON seam, Python-owned transform) | [§5 Building Block View](docs/architecture/05-building-block-view.md) (`scene`, `viewer`) + [ADR-0017](docs/adr/0017-3d-viewer-architecture.md) + the schema reference [docs/architecture/scene-v1-schema.md](docs/architecture/scene-v1-schema.md) |
| Why the project targets a single Python (3.12), not a range | [ADR-0009](docs/adr/0009-single-supported-python-version.md) |
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

`required_linear_history` must **never** be enabled — it blocks GitFlow's release flow, which merges each `release/*` into both `main` and `develop`. Feature PRs land as merge commits too (squash/rebase merging is disabled repo-wide as a release-safety guardrail). The strategy is recorded in [ADR-0014](docs/adr/0014-merge-commit-only-history-strategy.md), superseding the never-adopted [ADR-0011](docs/adr/0011-linear-history-strategy-under-gitflow.md).

**Releasing** (two skills, in order). `/release-prep version=X.Y.Z` promotes the CHANGELOG `[Unreleased]` block + runs a doc audit, landing via a PR that **must merge first**; then `/release-cut version=X.Y.Z` bumps `pyproject.toml` and opens the release→`main` + back-merge→`develop` PRs (its Check E refuses unless develop already carries the `[X.Y.Z]` heading). After the release PR merges, tag the **main merge commit** with an **annotated** `git tag -a vX.Y.Z` — **not** `git tag -s`: releases are signed by `release.yml`'s **Sigstore keyless cosign on the artifacts** (CI OIDC, no stored key), not a GPG-signed tag. Then `gh release edit vX.Y.Z --notes-file` with that version's CHANGELOG section (auto-publish otherwise uses the bare tag message). Tagging + `gh release edit` aren't merges, so Claude may run them on the user's go-ahead — `gh pr merge` stays the user's alone.

### Per-PR process

1. Branch `feature/<slug>` off `develop`. Work, commit.
2. Open the PR **as a draft** (`gh pr create --draft`) — base `develop`, body includes `Closes #N`. A PR stays in draft until its review arc is done; draft signals "not yet for the human's attention."
3. Invoke `/pr-review` (or the `pr-review-toolkit:review-pr` skill).
4. Convert each finding into a **review thread on the diff** (via `gh pr review` line comments / `gh api .../pulls/<n>/comments`). Findings never live only in chat.
5. Resolve every thread: fix the code (preferred) or reply with rationale, then mark resolved.
6. If the changes were non-trivial, re-run the review.
7. When the review arc is clean, flip the PR out of draft (`gh pr ready <n>`) and tell the user it is **clean and ready for final review**. You may mark it ready **even before CI finishes** — readiness tracks the review arc, not the CI run (the user still cannot merge until the required checks pass, so an early ready flip never risks a premature merge). The user approves and merges. **Never `gh pr merge` from Claude — this includes `--auto`/enabling auto-merge, which counts as merging. Never arm it without the user's explicit go-ahead on that specific PR, every time (`gh pr merge <n> --disable-auto` undoes a stray arm).**

**Stacking PRs (shared-file features).** When a feature splits into PRs that touch
the same files (parallel branches would conflict), build a linear stack but **base
every PR on `develop`, never on the parent feature branch**: CI (`on:
pull_request: branches:[develop,main]`) and GitHub `Closes #N` linkage only fire
for develop/main-base PRs, so a feature-branch-based PR silently gets **no CI run
and no issue link**. Accept the cumulative diff until parents merge, and document
the merge order. (Mis-based already? `gh api -X PATCH repos/DocGerd/hangarfit/pulls/<n> -f base=develop`,
then close+reopen the PR to trigger CI.) Wire the stack's order as native issue
deps: `gh api -X POST repos/DocGerd/hangarfit/issues/<n>/dependencies/blocked_by -F issue_id=<numeric id>`.

### Issues

- Every change is tracked by a GitHub issue. No code without an issue.
- Issues are organized into milestones (one milestone = one releasable cut).
- PR bodies link to issues with `Closes #N` / `Fixes #N` (the body, not the title — only body syntax auto-closes).
- Each user-facing change carries its own `CHANGELOG.md [Unreleased]` entry; `/release-prep` only *promotes* that block (never authors it), so any missing entries must be backfilled at cut time.

---

## Subagents

Use the best-fitted model for the task. The model class to pick is "as much reasoning as the work needs" — heavy for novel design and deep review, lighter for mechanical work.

- **`pr-review-toolkit:code-reviewer`** — main PR review pass on every PR.
- **`pr-review-toolkit:comment-analyzer`** — for PRs that meaningfully change docs (README, CLAUDE.md, docstrings).
- **`pr-review-toolkit:silent-failure-hunter`** — for PRs touching loader or collision code.
- **`pr-review-toolkit:type-design-analyzer`** — when `models.py` changes.
- **`geometry-invariant-guard`** — for any PR touching `src/hangarfit/geometry.py` or `src/hangarfit/collisions.py`; guards the coordinate-transform sign-flip trap (see [ADR-0002](docs/adr/0002-determinant-minus-one-transform.md)).
- **`determinism-guard`** — for any PR touching `src/hangarfit/solver.py` or `src/hangarfit/towplanner.py` (including the #544 `--workers` parallel-restart fan-out and `tests/test_solver_parallel.py`); guards the byte-identical-plan determinism contract (same scenario + seed → bit-identical output, `max_restarts`-scoped per the #267 amendment; and parallel-restart ≡ serial in the `_parallel_eligible` regime per the #544 amendment), runs the solver twice on a fixed seed and diffs (see [ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md)).
- **`feature-dev:code-architect`** — only for genuinely novel design decisions, not routine implementation.

Most coding goes direct in-session. Subagent dispatch is for review work and isolated heavy lifts.

**Review subagents must stay read-only in the shared checkout.** A review agent that runs `git switch` / `checkout` / `stash` in the shared working tree silently reverts it under any sibling agent (and under you). Point review agents at `origin/<branch>` refs instead — `gh pr diff N`, `git diff origin/develop...origin/feature/X`, and `git show origin/develop:<path>` for the pre-change state — and **never** switch branches in place. Isolate any subagent that *writes* in its own worktree.

---

## Project-local Claude Code config

The `.claude/` directory holds team-shared Claude Code settings (currently: a PreToolUse guard that blocks hand-edits to the hash-pinned `requirements-*.txt` lockfiles, a PostToolUse hook that runs ruff + pytest after edits under `src/hangarfit/` or `tests/`, a second PostToolUse hook that reminds you to rebuild `viewer.js` after `viewer/src/*.ts` edits (#568), plus a Stop-event hook that runs mypy once when a turn finishes; and the `pyright-lsp` + `typescript-lsp` editor plugins under `enabledPlugins`). See [.claude/README.md](.claude/README.md) for what's there and how to disable per-contributor via a gitignored `.claude/settings.local.json`.

---

## MCP servers

`.mcp.json` at the repo root declares the project-scoped GitHub MCP server so every contributor gets it automatically on a fresh clone — no per-user setup step. See also [.claude/README.md](.claude/README.md) for the broader Claude Code config ecosystem in this repo.

| Server | Transport | Purpose |
|---|---|---|
| `github` | HTTP (`https://api.githubcopilot.com/mcp/`) | Issue / PR / release inspection from Claude; complements the existing `gh` CLI. |
| `context7` | HTTP (`https://mcp.context7.com/mcp`) | Live, version-correct library docs (shapely, matplotlib & other deps) pulled into context on demand, so doc lookups reflect the installed version rather than stale training data. |

**Canonical upstream references (verify before editing `.mcp.json`):**
- GitHub MCP: https://github.com/github/github-mcp-server
- Context7 MCP: https://github.com/upstash/context7

If a URL or env-var name in `.mcp.json` ever stops working, check these first.

### Auth requirements

- **GitHub MCP** — Requires `GITHUB_PERSONAL_ACCESS_TOKEN` in your shell environment. Minimum permissions depend on which PAT type you create:
  - **Classic PAT:** `repo` + `read:org` scopes are sufficient for read operations; add `write:discussion` if you want Claude to create issues or PRs via the MCP server rather than `gh`.
  - **Fine-grained PAT:** Repository permissions `Contents: Read`, `Issues: Read`, `Pull requests: Read`; plus Organization permissions `Members: Read` for org-level lookups. Add the corresponding `Write` levels for create operations. Fine-grained PATs use different UI checkboxes from classic — the scope names above are classic-only.
- **Context7 MCP** — **Works keyless out of the box; no env var required.** The checked-in `.mcp.json` entry carries no auth header on purpose, so a fresh clone connects under Context7's anonymous rate limits with zero setup. A `${CONTEXT7_API_KEY}` header is deliberately *not* committed: Claude Code does not expand `${VAR}` in HTTP `headers` for an unset variable, so an unresolved placeholder would be sent literally and break the keyless default. To raise rate limits, get a free key at context7.com/dashboard and add it locally (not committed) via your own client config or a gitignored override — Context7 reads it from the `CONTEXT7_API_KEY` request header.

### Verifying the servers loaded

After cloning and running `claude`, use the `/mcp` command. The `github` and `context7` servers should appear with status **connected**. If `github` shows **failed**, check that `GITHUB_PERSONAL_ACCESS_TOKEN` is set in your shell environment; `context7` needs no env var and should connect keyless.

---

## Worktrees

Allowed but not the default — use only for genuinely parallel feature work; plain branch checkout is simpler for sequential issues. Two gotchas if you do: **(1)** the editable install's `.pth` points at the **main** checkout, so a bare `pytest`/`python`/`hangarfit` *inside* a worktree imports the wrong `src` (the PostToolUse hook's pytest included) — use `PYTHONPATH=$PWD/src python -m …` (there is no `__main__.py`; the entry point is `hangarfit.cli:main`). **(2)** `EnterWorktree` branches off the wrong base — this clone's local `origin/HEAD` is unset, so it falls back to `origin/main` not `develop`; fix once with `git remote set-head origin -a`, then per-worktree verify `git merge-base --is-ancestor origin/develop HEAD` (else `git fetch origin develop && git rebase origin/develop` before pushing). The native auto-cleanup won't delete a `git branch -m`-renamed branch, so `git branch -d` it after the worktree is removed.

---

## Security policy & Scorecard rationale

Vulnerability reporting lives in [SECURITY.md](SECURITY.md). The rationale for the structural-zero OpenSSF Scorecard checks (Code-Review, Maintained, Contributors, Packaging) — why they score 0 by design and what we do instead — is documented in [docs/security-posture.md](docs/security-posture.md). If you're asked about the Scorecard number, point at that doc rather than the raw aggregate.

---

## Open questions / TBD before trusting output

- **`data/` is synthetic.** Every aircraft (`measured: false` in `fleet.yaml`) and the hangar in `data/hangar.yaml` are eyeballed placeholders — kept deliberately as the stable demo/test fixtures.
- **Real data lives in [`examples/herrenteich/`](examples/herrenteich/README.md)** (since #426): the DWG-measured L-shaped hangar (15.08 × 31.76 m, 13.46 m door) and the eight published-spec occupants (still `measured: false`). The two gaps it surfaced are **both fixed** — the back-right office **notch** is modelled as a `structural_notches` keep-out ([ADR-0018](docs/adr/0018-non-rectangular-hangar-footprint.md), epic #527), and `solve`'s trivial-infeasibility glider area-gate now sums part footprints, not bounding boxes (#425), so glider fleets reach the search.
- **Placeholder hangar can't fit the full fleet.** The placeholder hangar in [`data/hangar.yaml`](data/hangar.yaml) — widened 18 → 22 m for the #519/#520 empennage tail surfaces, which consume lateral room — is still too tight to fit every aircraft at once under the placeholder clearance budget. The default [`examples/layouts/example.yaml`](examples/layouts/example.yaml) is a deliberate subset (5 parked + the Scheibe in the maintenance bay); test fixtures that need the full fleet use [`tests/fixtures/test_hangar_large.yaml`](tests/fixtures/test_hangar_large.yaml). The `max_restarts` exhausted-budget determinism canary keeps its own dedicated tight 18 m fixture (`solve_canary_six_planes_tight.yaml`) so demo-hangar tweaks can't re-break it.

The collision checker will run on the `data/` placeholders, but until those are real, output on them is illustrative only (the `examples/herrenteich/` hangar is real; its fleet is published-spec).

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

# GOTCHA (test flakes + CI quirks) → docs/dev/test-flakes-and-ci-gotchas.md.
# Read it before treating a determinism/coverage CI failure as a regression: the
# `serial` wall-clock double-solve canaries (run OUTSIDE `-n auto`, #492); the same
# fragility in non-serial smokes + the wall-clock bench `--gate` speed ceilings
# (re-baseline on a deliberate determinism re-base, don't chase a phantom); two-pass
# coverage (@slow drops from the combined run — keep >=1 non-slow test per new
# path); and the ProcessPool/spawn worker coverage blind spot (#561).

# Lint + format check (CI also runs these)
ruff check src/ tests/
ruff format --check src/ tests/

# Auto-fix lint findings and format
ruff check --fix src/ tests/
ruff format src/ tests/

# Type check
mypy src/hangarfit/

# Regenerate the four hash-pinned lockfiles (dev / build / fuzz / pip-tools).
# Full recipes + rationale: docs/dev/lockfiles.md. You rarely run these by hand —
# the dev/build/fuzz drift jobs PRINT the exact command on drift (pip-tools has no
# drift job; its rationale lives in requirements-pip-tools.in). Same toolchain for
# all four: pip-tools 7.5.3 on Python 3.12. Most common (dev deps; no `.in`,
# regenerated from pyproject.toml after editing [project] deps or the dev extra):
pip-compile --generate-hashes --no-strip-extras --extra dev -o requirements-dev.txt pyproject.toml

# CI: GitHub Actions runs `pytest` on Python 3.12 for PRs into
# develop/main (see .github/workflows/ci.yml). CI installs dev deps
# from the hash-pinned `requirements-dev.txt` with `--require-hashes`,
# the build toolchain from `requirements-build.txt` likewise, then
# installs the project itself in editable mode with `--no-deps
# --no-build-isolation` (reusing the hash-verified host setuptools/wheel
# instead of an unpinned isolated build env). No pytest coverage threshold
# (no --cov-fail-under); Codecov posts a `codecov/patch` status flagging patch
# coverage on each PR, but it is NOT a required check on `develop` (required =
# test 3.12 + the three lockfile-drift jobs + Analyze + `bench correctness`, added
# by #564), so a red patch status
# reports but does not by itself block merge (see the @slow gotcha above).

# Phase 1 acceptance smoke test
hangarfit check examples/layouts/example.yaml --render out.png

# Phase 3a/3b: solve + tow-path overlay. Best-effort: a layout the planner
# can't fully route renders without paths (blocking plane named on stderr);
# exit 3 only if NO candidate layout is tow-routable.
hangarfit solve tests/fixtures/scenario_minimal.yaml --render out.png --render-paths

# #544/#560 parallel restarts. --max-restarts N bounds the search by a FIXED
# restart count (cross-machine reproducible, NOT wall-clock); --workers N fans
# those restarts across worker processes. The speedup is BYTE-IDENTICAL to serial
# ONLY in the --max-restarts + spread-on regime (`_parallel_eligible`): for any
# other config (no --max-restarts, or --no-spread) --workers is silently
# downgraded to serial and prints a `note: --workers N ignored (runs serial)` on
# stderr. Speedup is sub-linear and placement-only — most useful on roomy spread-on
# fills with many restarts. Determinism stays max_restarts-scoped (ADR-0003 #544
# amendment); see tests/test_solver_parallel.py for the byte-identity contract.
hangarfit solve tests/fixtures/scenario_minimal.yaml --max-restarts 64 --workers 8

# Phase 4: 3D viewer (self-contained offline HTML). examples/layouts/example.yaml is NOT
# tow-routable → it falls back to a static 3D render. Since #398 (F1), layout-mode
# `view` passes a small deterministic GLOBAL tow-expansion cap
# (_VIEW_TOW_MAX_TOTAL_EXPANSIONS=300) to the planner, so an un-routable layout
# degrades to static in ~5 s instead of grinding ~2 min — a deterministic
# expansion COUNT, not a wall-clock deadline (ADR-0003). Override with
# --tow-max-expansions N; pass --no-animate to skip tow planning entirely, or use
# the wing-over-nesting fixture for a fast animated demo. Verify headlessly via
# swiftshader WebGL (dbus/UPower + WebGL "ReadPixels stall" lines are noise; a
# transform mismatch shows an on-page banner).
hangarfit view tests/fixtures/valid_left_side_nesting.yaml -o out.html
google-chrome --headless=new --use-gl=angle --use-angle=swiftshader \
  --enable-unsafe-swiftshader --virtual-time-budget=8000 \
  --screenshot=out.png "file://$PWD/out.html"

# #412 staging apron (ADR-0021): with apron_depth_m > 0 each tow path starts
# OUTSIDE the door (y<0) and slides in. Set it on the hangar.yaml or override
# per run with --apron-depth N|auto (auto = ~max plane length + max turn radius)
# on BOTH solve and view (not check — collisions.check is apron-inert). Default
# 0/absent reproduces the no-apron plan byte-for-byte (gated behind depth>0).
hangarfit solve tests/fixtures/scenario_minimal.yaml --render out.png --render-paths --apron-depth auto
hangarfit view tests/fixtures/valid_left_side_nesting.yaml -o out.html --apron-depth 6

# Solve→tow profiling harness (DEV/CI-ONLY, #381 — top-level `bench/`, never in the
# wheel). Binds on `max_restarts` (reproducible). The no-`--gate` correctness run is
# a REQUIRED `bench correctness` check; the `--gate` SPEED job is non-required (#564).
# Rationale + ranked speedup levers + the heavy-regime caveat (heavy regimes use a
# tighter tow cap, so their routing time is a lower bound): bench/README.md and
# docs/spikes/solve-tow-profiling.md.
python -m bench.profile_pipeline                    # fast regimes: timing + correctness verdicts
python -m bench.profile_pipeline --heavy --profile  # + heavy regimes + cProfile stage breakdown

# Viewer TypeScript toolchain (DEV/CI-ONLY — ADR-0020, top-level `viewer/`). Node is
# needed ONLY to change the viewer; pip/build/pytest never invoke npm. The wheel ships
# the COMMITTED bundle src/hangarfit/_viewer_assets/viewer.js (built from viewer/src/*.ts
# by esbuild). After editing any viewer/src/*.ts, REBUILD + commit viewer.js in the same
# change or the `viewer-build-drift` CI guard (#438) fails. Commands: viewer/README.md;
# rationale: ADR-0020.
npm --prefix viewer/ ci          # install from the committed lockfile (CI uses this)
npm --prefix viewer/ run build   # rebuild ../src/hangarfit/_viewer_assets/viewer.js
npm --prefix viewer/ run typecheck   # tsc --noEmit (strict)
npm --prefix viewer/ run lint    # eslint (flat config, ESLint 10)
npm --prefix viewer/ run test    # node --test — pure units in viewer/test/
# Verify a build WITHOUT clobbering the committed bundle: redirect the output.
VIEWER_OUTFILE=/tmp/viewer-scratch.js npm --prefix viewer/ run build
# three stays vendored & external (r160; @types/three + a TEST-ONLY `three` devDep track
# it — the CI skew-guard ties all three, bump in lockstep). viewer/src uses explicit `.ts`
# imports (tsconfig allowImportingTsExtensions) so `node --test` resolves them under Node
# 24 type-stripping; esbuild inlines internal modules, so .ts stays bundle-neutral.

# GitFlow loops
git switch develop && git pull
git switch -c feature/<slug>
# ... work ...
git push -u origin feature/<slug>
gh pr create --draft --base develop --title "..." --body "Closes #N ..."
# ... review arc; then flip out of draft when clean ...
gh pr ready <n>
```
