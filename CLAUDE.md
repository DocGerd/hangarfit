# Project context — hangarfit

This file is the durable **operational** context for the project: how we work, where the live config lives, and what is still uncertain. Architectural knowledge — the domain model, the coordinate convention, the module map, the decisions that shaped the substrate — lives in [`docs/architecture/`](docs/architecture/) and [`docs/adr/`](docs/adr/). Read this file first in any new session; follow the Quick Reference below to drill in.

---

## What this project is

`hangarfit` is an **on-demand exception tool** for a flying club: when the standard hangar parking layout breaks (delayed return, surprise maintenance, etc.), it helps find *a* valid alternative arrangement. The tool checks whether a hand-authored candidate layout is physically valid and renders a top-down PNG so a human can eyeball it; the solver searches for one when no candidate is in hand.

**Status:** Phase 1 (substrate), Phase 2a (static layout solver, `hangarfit solve`), Phase 2b–2c (solver realism + spread/diversity polish), Phase 3a (tow-path planning, `hangarfit solve --render-paths`), Phase 3b (Reeds–Shepp reverse-capable tow motion), and Phase 4 (interactive 3D viewer, `hangarfit view`) have all shipped. An opt-in **learned backend** (epic #607) has its inference **seam** shipped — the `--backend learned` / `--weights` CLI flags route through the wheel-shipped `hangarfit.learned` module (verifier-gated, same `SolveResult` shape, #706). The ONNX inference *implementation* (`ml.infer`) and the RL *training* both live in the dev/CI-only `ml/` package (a top-level package outside `src/`, so `packages.find` never ships it in the wheel), so a bare install reports the backend unavailable until `ml/` + the `[learned-infer]` extra + trained weights are present. **Train-to-mastery on the dense `trio-notch` rung is resolved-negative (the lever program is stopped); the backend is scoped to the shipped seam** — see [ADR-0028](docs/adr/0028-learned-backend-train-to-mastery-resolved-negative.md) for the measured root cause (cold-start drive-and-pack wall) and the falsifiable re-open gate. Live milestone status lives in auto-memory and GitHub milestones, not here.

---

## Quick Reference — where architectural content actually lives

| Looking for | See |
|---|---|
| What `hangarfit` is and the quality goals it optimizes for | [§1 Introduction & Goals](docs/architecture/01-introduction-and-goals.md) |
| What is in / out of scope, the external actors, exit-code semantics pointer | [§3 Context & Scope](docs/architecture/03-context-and-scope.md) |
| Module map (`cli`, `loader`, `models`, `geometry`, `collisions`, `_sat`, `solver`, `learned`, `towplanner`, `visualize`, `scene`, `viewer`, `metrics`, `brand`) and per-module responsibilities | [§5 Building Block View](docs/architecture/05-building-block-view.md) |
| Runtime flow of `check` and `solve` invocations | [§6 Runtime View](docs/architecture/06-runtime-view.md) |
| **The parts model** (collision rule, why parts not bbox, `struts:` block, the fuselage front/aft split, the **empennage** `tail`+`vertical_stabilizer` surfaces — a wingtip may overhang a low-winger's *low tailplane* but not its *cockpit*, and not its *fin* which rises into the wing layer) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-parts-model) + [ADR-0001](docs/adr/0001-aircraft-parts-model.md) + [ADR-0012](docs/adr/0012-fuselage-front-aft-split.md) + [ADR-0023](docs/adr/0023-empennage-tail-surfaces.md) |
| **The coordinate convention + the determinant-−1 transform trap** | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-coordinate-convention) + [ADR-0002](docs/adr/0002-determinant-minus-one-transform.md) |
| **The maintenance bay rule** (current `bay_intrusion` semantics) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-maintenance-bay-rule) + [ADR-0006](docs/adr/0006-bay-intrusion-maintenance-rule.md). The Phase 1 predecessor is preserved as [ADR-0005](docs/adr/0005-maintenance-bay-rule.md) (Superseded by ADR-0006). |
| Fleet composition (per-plane wing type, gear, movement mode, struts, canonical wheel positions) | [`data/catalog/`](data/catalog/) — per-object catalog files (one per aircraft, with a `type:` discriminator), referenced by path from the thin [`data/fleet.yaml`](data/fleet.yaml) manifest (#595); §8 calls out the strut-braced subset and the only low-wing. Wheel positions are canonical per-aircraft data ([ADR-0013](docs/adr/0013-wheels-canonical-data.md)), not renderer heuristics |
| Hangar dimensions, door, maintenance bay rectangle | [`data/hangar.yaml`](data/hangar.yaml) — all values currently placeholders pending real measurement |
| **The real Airfield Herrenteich dataset** (DWG-measured hangar + published-spec, second-source-verified fleet incl. a folded Stemme S10 + a valid all-8 `layout.yaml`) | [`examples/herrenteich/`](examples/herrenteich/README.md) — real hangar + layout/scenario; its aircraft are the shared central `data/catalog/` entries since #595 (no per-world duplication; `fuji`/`cessna_150` remain the only synthetic placeholders) |
| Default clearances (`clearance_m`, `wing_layer_clearance_m`) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#default-clearances) |
| RR-MC solver algorithm and the determinism contract | [ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md) |
| Diversity metric (edit-count, thresholds) | [ADR-0004](docs/adr/0004-diversity-metric.md) |
| **The spread post-pass** (maximize inter-plane gap once valid) | [ADR-0008](docs/adr/0008-inter-plane-spread-soft-preference.md) |
| **The tow-path planner** (empty-hangar fill, Reeds–Shepp arcs, `solve --render-paths`, exit-3 tow-routability) | [§5 Building Block View](docs/architecture/05-building-block-view.md) (`towplanner`) + [ADR-0007](docs/adr/0007-tow-path-planner-v1-scope.md) (v1 scope) + [ADR-0010](docs/adr/0010-reeds-shepp-motion-model.md) (v2 Reeds–Shepp motion) |
| **The staging apron** (`hangar.apron_depth_m` / `--apron-depth N\|auto`, slide-in from outside the door, reverse nose-out seeds, depth-0 byte-identical) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md#the-door-is-a-visual-marker-only) + [ADR-0021](docs/adr/0021-tow-planner-staging-apron.md). `collisions.check` is apron-inert (forbids `y<0`); the apron is a planner-level motion concept |
| **The 3D viewer** (`hangarfit view`, interactive offline HTML, whole-fill tow timeline, the `scene/v2` JSON seam, Python-owned transform) | [§5 Building Block View](docs/architecture/05-building-block-view.md) (`scene`, `viewer`) + [ADR-0017](docs/adr/0017-3d-viewer-architecture.md) + the schema reference [docs/architecture/scene-v2-schema.md](docs/architecture/scene-v2-schema.md) |
| **Ground objects** (fixed obstacles + placed/routed movers — cars & trailers; the Caddy hard-door egress gate; the soft right/left-region preference; movers are solver-placed since #604) | [§8 Crosscutting Concepts](docs/architecture/08-crosscutting-concepts.md) + [ADR-0025](docs/adr/0025-ground-object-taxonomy.md) (taxonomy) + [ADR-0026](docs/adr/0026-caddy-hard-door-egress.md) (Caddy egress) + [ADR-0008](docs/adr/0008-inter-plane-spread-soft-preference.md) (region soft-term amendment) + [ADR-0010](docs/adr/0010-reeds-shepp-motion-model.md) (mover motion) |
| **The learned-backend RL workspace** (`ml/`, a top-level package *outside* `src/hangarfit/`; #607 — cold-joint env/reward, observation tensorizer, policy net, PPO, curriculum, eval/benchmark) | [`ml/README.md`](ml/README.md) + [ADR-0027](docs/adr/0027-learned-backend-determinism-scope.md) (Proposed) + the design spec `docs/superpowers/specs/2026-06-12-learned-backend-cold-joint-rl-env-design.md` |
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

**Releasing** (two skills, in order; **both are user-invoked only** — `disable-model-invocation`, so Claude executes the steps but the user must type `/release-prep` / `/release-cut`; Claude cannot trigger them via the Skill tool). `/release-prep version=X.Y.Z` promotes the CHANGELOG `[Unreleased]` block + runs a doc audit, landing via a PR that **must merge first**; then `/release-cut version=X.Y.Z` bumps `pyproject.toml` and opens the release→`main` + back-merge→`develop` PRs (its Check E refuses unless develop already carries the `[X.Y.Z]` heading). **The release→`main` PR opens `BEHIND`** (main's `required_status_checks.strict=true` + the prior release **merge commits** live only on `main`, never back-merged to `develop`), so an armed auto-merge can't self-heal it — run `gh api -X PUT repos/DocGerd/hangarfit/pulls/<n>/update-branch` once (merges `main` in, normally conflict-free) to make it mergeable. `/release-cut`'s `gh pr create --milestone` also needs the milestone **title**, not the number (number → `not found`); looking that title up via `gh api milestones` needs `?state=all&per_page=100` (it paginates at 30, so a freshly-created milestone hides past page 1). After the release PR merges, tag the **main merge commit** with an **annotated** `git tag -a vX.Y.Z` — **not** `git tag -s`: releases are signed by `release.yml`'s **Sigstore keyless cosign on the artifacts** (CI OIDC, no stored key), not a GPG-signed tag. `release.yml` then **auto-populates the Release notes from the tagged commit's `## [X.Y.Z]` CHANGELOG block** (#486) — only run `gh release edit vX.Y.Z --notes-file` if the workflow logged the CHANGELOG-mismatch fallback warning. Pushing the tag isn't a merge, so Claude may do it on the user's go-ahead — `gh pr merge` stays the user's alone.

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
the merge order. **Cascading CHANGELOG conflicts:** stacked PRs each insert a
`[Unreleased]` entry at the top of the same `### Added` block, so **every sibling
re-conflicts on `CHANGELOG.md` each time one merges** — re-sync the rest (`git
merge origin/develop`, NOT rebase — no force-push; keep ALL entries) after each
lands. **Prevention (preferred for ≥2 PRs delivered in parallel):** keep the
`[Unreleased]` entry OUT of each feature PR and collect them all in ONE separate
CHANGELOG-only PR — no sibling then conflicts (user preference, 2026-06-23). The
default "entry per PR" rule still holds for one-at-a-time delivery. GitHub mergeability **lags a push**: a transient `CONFLICTING`/`DIRTY`
banner right after a merge/push clears on recompute — `mergeable=MERGEABLE` (and
`git merge-base --is-ancestor origin/develop <branch>`) is authoritative;
`mergeStateStatus=BLOCKED` just means required CI is still pending, not a conflict.
(Mis-based already? `gh api -X PATCH repos/DocGerd/hangarfit/pulls/<n> -f base=develop`,
then close+reopen the PR to trigger CI.) Wire the stack's order as native issue
deps: `gh api -X POST repos/DocGerd/hangarfit/issues/<n>/dependencies/blocked_by -F issue_id=<numeric id>`.

**Scratch-gitignore is per-branch.** A `.gitignore` rule added on one branch does NOT
protect a sibling cut from `develop` before it merged — a `git add -A` there sweeps the
still-untracked scratch (`train-*.log` / `ck-*.pt` / `metrics-*.jsonl`) into the commit and
pollutes `develop` (a tracked file is never re-ignored; the fix is a `git rm --cached` PR,
e.g. #786). Prefer **explicit `git add <paths>`** when reproducible gate scratch sits in the
tree, or merge `develop` in / land the ignore first. Spot a leak with `git ls-files | grep -E
'train-|ck-|metrics-'` — `git check-ignore` returns 0 for already-tracked files, so it won't.

### Issues

- Every change is tracked by a GitHub issue. No code without an issue.
- Issues are organized into milestones (one milestone = one releasable cut).
- PR bodies link to issues with `Closes #N` / `Fixes #N` (the body, not the title — only body syntax auto-closes).
- Each user-facing change carries its own `CHANGELOG.md [Unreleased]` entry; `/release-prep` only *promotes* that block (never authors it), so any missing entries must be backfilled at cut time. Write a **milestone** number bare (`milestone 34`), **not** `#34`, in CHANGELOG/PR prose — `release.yml` renders the CHANGELOG block verbatim into the GitHub Release notes (#486), where a bare `#N` auto-links to **PR/issue N**, not the milestone (issue refs like `#614` are correct and intended).

---

## Subagents

Use the best-fitted model for the task. The model class to pick is "as much reasoning as the work needs" — heavy for novel design and deep review, lighter for mechanical work.

- **`pr-review-toolkit:code-reviewer`** — main PR review pass on every PR.
- **`pr-review-toolkit:comment-analyzer`** — for PRs that meaningfully change docs (README, CLAUDE.md, docstrings).
- **`pr-review-toolkit:silent-failure-hunter`** — for PRs touching loader or collision code.
- **`pr-review-toolkit:type-design-analyzer`** — when `models.py` changes.
- **`geometry-invariant-guard`** — for any PR touching `src/hangarfit/geometry.py` or `src/hangarfit/collisions.py`; guards the coordinate-transform sign-flip trap (see [ADR-0002](docs/adr/0002-determinant-minus-one-transform.md)).
- **`determinism-guard`** — for any PR touching `src/hangarfit/solver.py` or `src/hangarfit/towplanner.py` (including the #544 `--workers` parallel-restart fan-out and `tests/test_solver_parallel.py`); guards the byte-identical-plan determinism contract (same scenario + seed → bit-identical output, `max_restarts`-scoped per the #267 amendment; and parallel-restart ≡ serial in the `_parallel_eligible` regime per the #544 amendment), runs the solver twice on a fixed seed and diffs (see [ADR-0003](docs/adr/0003-rr-mc-solver-algorithm.md)).
- **`ml-rl-guard`** — for any PR touching the `ml/` RL workspace (`ml/*.py`) or `tests/ml/`; guards the four RL invariants the solver-scoped `determinism-guard` does not cover — training reproducibility/seeding, the 4c-ii **knob default-neutrality** contract, validity = the product checker (`collisions.check` + Caddy egress) not the env oracle (#694), and the numeric silent-failure + intrinsic-horizon-GAE guards. Runs `ruff`/`mypy ml/` + the targeted `tests/ml/` regression tests (see [`ml/README.md`](ml/README.md) + [ADR-0027](docs/adr/0027-learned-backend-determinism-scope.md)).
- **`scene-schema-guard`** — for any PR touching `src/hangarfit/scene.py`, `src/hangarfit/viewer.py`, the `viewer/src/*.ts` contract mirrors, the committed `viewer.js`, or `src/hangarfit/brand.py`; guards the `scene/v2` JSON-seam contract — additive-only `SCHEMA` bumps, byte-identical `build_scene`, the Python-owned determinant-−1 transform the viewer must *consume* (not recompute), `scene.py`↔`scene-contract.ts` key-set parity (#440), and the #666 viewer-compare container layering over untouched scene/v2 docs. Runs the `tests/test_scene.py` / `tests/test_viewer.py` byte-identity + key-parity regression net (see [ADR-0017](docs/adr/0017-3d-viewer-architecture.md) + the schema reference [docs/architecture/scene-v2-schema.md](docs/architecture/scene-v2-schema.md)).
- **`feature-dev:code-architect`** — only for genuinely novel design decisions, not routine implementation.

Most coding goes direct in-session. Subagent dispatch is for review work and isolated heavy lifts.

`ml/` is reviewable source, not scratch — run the formal `/pr-review` arc on `ml/` PRs like any `src/` change. Note CI's `mypy` only covers `src/hangarfit/`, so a Pyright complaint under `tests/ml/` is usually stale-LSP noise — `mypy`/CI is the source of truth. Run `mypy ml/` over the **whole package**, not a single file — under `ml.*`'s `follow_imports = "skip"` a subset run resolves cross-module imports as `Any`, manufacturing a false `unused type: ignore`.

**Review subagents must stay read-only in the shared checkout.** A review agent that runs `git switch` / `checkout` / `stash` in the shared working tree silently reverts it under any sibling agent (and under you). Point review agents at `origin/<branch>` refs instead — `gh pr diff N`, `git diff origin/develop...origin/feature/X`, and `git show origin/develop:<path>` for the pre-change state — and **never** switch branches in place. Isolate any subagent that *writes* in its own worktree.

---

## Project-local Claude Code config

The `.claude/` directory holds team-shared Claude Code settings (currently: a SessionStart hook that provisions a one-time Python 3.12 venv for the remote/web container so the bare-name tool hooks — ruff/pytest/mypy — resolve there (#354), a PreToolUse guard that blocks hand-edits to the hash-pinned `requirements-*.txt` lockfiles, a PostToolUse hook that runs ruff + pytest after edits under `src/hangarfit/` or `tests/` (and ruff + the scoped `pytest tests/ml/` after `ml/*.py` edits), a second PostToolUse hook that reminds you to rebuild `viewer.js` after `viewer/src/*.ts` edits (#568), a third PostToolUse hook that reminds you to regenerate the lockfiles after a `pyproject.toml` dependency change (#801), a PreToolUse Bash hook that warns on `gh pr create` when the branch carries no `CHANGELOG.md` entry (advisory, non-blocking; #802), plus a Stop-event hook that runs mypy — over `src/hangarfit/`, and `ml/` too when torch is importable — once when a turn finishes; and the `pyright-lsp` + `typescript-lsp` editor plugins under `enabledPlugins`). See [.claude/README.md](.claude/README.md) for what's there and how to disable per-contributor via a gitignored `.claude/settings.local.json`.

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

Allowed but not the default — use only for genuinely parallel feature work; plain branch checkout is simpler for sequential issues. Two gotchas if you do: **(1)** the editable install's `.pth` points at the **main** checkout, so a bare `pytest`/`python`/`hangarfit` *inside* a worktree imports the wrong `src` (the PostToolUse hook's pytest included) — use `PYTHONPATH=$PWD/src python -m …` (there is no `__main__.py`; the entry point is `hangarfit.cli:main`). **(2)** `EnterWorktree` branches off the wrong base — this clone's local `origin/HEAD` is unset, so it falls back to `origin/main` not `develop`; fix once with `git remote set-head origin -a`, then per-worktree verify `git merge-base --is-ancestor origin/develop HEAD` (else `git fetch origin develop && git rebase origin/develop` before pushing). The native auto-cleanup won't delete a `git branch -m`-renamed branch, so `git branch -d` it after the worktree is removed. **(3)** A long `ml.train` run reads fixture FILES (the witness layout, the rung's hangar/fleet YAML) **lazily** from the working tree when the curriculum reaches that rung — *not* at startup — so switching the shared checkout's branch mid-run breaks it (a `LoaderError` deep in the loop, even though its Python modules are already imported). Run a long training job from a **worktree pinned to its branch** (cwd inside the worktree so `ml` resolves there and `_ROOT` points at it), then your main checkout is free to switch branches. Cost a 37-min crash (#736 run, 2026-06-23). **(4)** Running/grading such a gate: `ml.train` writes its `--metrics-out` JSONL **only at run-end** (after the whole curriculum returns), so a mid-run crash leaves no metrics — reconstruct the curve from stdout (`[rung] iter N … valid_placed= fraction_placed=`, the exact fields `analyze.py`/`ml.gate` key on). `pgrep -f ml.train` **self-matches your own Bash wrapper** — list real procs with `ps -eo comm,args | awk '$1 ~ /^python/ && /-m ml\.train/'`. To confirm a lever flag actually **engaged** (not just that it was passed), load the per-rung atomic `ck-*.pt` with the pinned worktree's torch and check `policy_kwargs` + the `token_proj` input dim (e.g. `--relative-encoder` ⇒ 28 vs 24). (#829)

---

## Security policy & Scorecard rationale

Vulnerability reporting lives in [SECURITY.md](SECURITY.md). The rationale for the structural-zero OpenSSF Scorecard checks (Code-Review, Maintained, Contributors, Packaging) — why they score 0 by design and what we do instead — is documented in [docs/security-posture.md](docs/security-posture.md). If you're asked about the Scorecard number, point at that doc rather than the raw aggregate.

---

## Open questions / TBD before trusting output

- **`data/` is now a per-object catalog of best-available specs (#595).** Aircraft are defined in [`data/catalog/`](data/catalog/) (one file per aircraft, with a `type:` discriminator) and referenced by path from the thin `data/fleet.yaml` manifest; a manifest entry may override per-fleet operational flags (`movement_mode`/`tow_pivotable`) on the shared static definition. The eight Airfield Herrenteich occupants carry real published-spec / TCDS-sourced numbers — a **single central catalog shared with `examples/herrenteich/`** (#594, no per-world duplication); `fuji`/`cessna_150` (not based there) remain eyeballed placeholders. Every entry keeps `measured: false` (none are on-site tape/laser measurements). `data/hangar.yaml` is still a placeholder.
- **Real data lives in [`examples/herrenteich/`](examples/herrenteich/README.md)** (since #426): the DWG-measured L-shaped hangar (15.08 × 31.76 m, 13.46 m door) and the eight published-spec occupants — now defined once in the shared `data/catalog/` and pulled in by herrenteich's `fleet.yaml` manifest (#595; still `measured: false`). The two gaps it surfaced are **both fixed** — the back-right office **notch** is modelled as a `structural_notches` keep-out ([ADR-0018](docs/adr/0018-non-rectangular-hangar-footprint.md), epic #527), and `solve`'s trivial-infeasibility glider area-gate now sums part footprints, not bounding boxes (#425), so glider fleets reach the search.
- **⚠ The real Herrenteich all-8 is statically valid but does NOT fully auto-tow-route yet (root-caused 2026-06-26; see [`docs/spikes/herrenteich-all8-tow-routing-rootcause.md`](docs/spikes/herrenteich-all8-tow-routing-rootcause.md)).** The club parks all 8 daily by monotone fill (never moving a parked plane), so a valid order provably exists — but `solve --render-paths` / `view` can't route the whole fill. **Factor 1 — FIXED (#842; the dominant cause, ~95% confidence):** `scheibe_falke` is a real LOW-wing glider whose 18 m wing was modelled in the HIGH-wing z-layer (`z[1.9,2.1]`), manufacturing a phantom wing-vs-wing collision when a high-winger towed past the parked Scheibe; the fix is a thin keep-out band `z[1.72,1.78]` (float-safe under both the herrenteich 0.15 m and the synthetic 0.20 m clearance, since the catalog is **shared**). **Factor 2 — OPEN (#844), now GROUNDED (2026-06-26; see [`docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md`](docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md)):** the residual blocker is the `fk9_mkii`↔`cessna_140` front-door corridor — two *genuine* high-wingers that mutually space-exhaust at the deployed 0.5 m / 15° tow grid (no monotone order places both). A witness-first probe on the real oracle shows a **feasible own-gear path provably exists**: own-gear A* at **0.25 m/10° FINDS a no-carts tow path** (96 949 exp, 39 min, exact-oracle-validated) while the deployed 0.5 m/15° grid finds none. So #844 is a **search-*efficiency*** problem — the deep own-gear pivot+forward "parallel-park" shuffle exists but the coarse grid can't represent it and finer grid finds it too slowly to ship. The **resolution hypothesis is VINDICATED** (not refuted). Carts route the pair cheaply at the coarse grid (196/1613 exp) — the **diagnostic** that isolates *lateral displacement* as the crux, not the fix (the club **hand-shuffles on own gear**, user-confirmed; `on_carts: true` would be unfaithful). Also ruled out: pivot-point fidelity (mains ≈ reference origin). The all-8 has a **second** blocker — husky ordering (`always_own_gear`, an order-search efficiency issue). Fix = a `towplanner` search-efficiency improvement (adaptive grid near tight corridors / analytic parallel-park maneuver injection / #840 learned guidance), determinism-guard-bound; a raised budget alone is NOT enough (97 k exp/39 min). Discarded hypotheses tabled in both spike docs — don't re-investigate.
- **Placeholder hangar can't fit the full fleet.** The placeholder hangar in [`data/hangar.yaml`](data/hangar.yaml) — widened 18 → 22 m for the #519/#520 empennage tail surfaces, which consume lateral room — is still too tight to fit every aircraft at once under the placeholder clearance budget. The default [`examples/layouts/example.yaml`](examples/layouts/example.yaml) is a deliberate subset (5 parked + the Scheibe in the maintenance bay); test fixtures that need the full fleet use [`tests/fixtures/test_hangar_large.yaml`](tests/fixtures/test_hangar_large.yaml). The `max_restarts` exhausted-budget determinism canary keeps its own dedicated tight 18 m fixture (`solve_canary_six_planes_tight.yaml`) so demo-hangar tweaks can't re-break it.
- **⚠ Feasibility-first for any ML eval (the #832 lesson, 2026-06-25).** *"RR-MC reaches 0" ≠ "hard"* — at over-capacity (and the tight/placeholder hangars **can't fit the full fleet**, see above) it means the layout is **infeasible**, so a *"policy/method reaches 0"* result there is **vacuous** (you tested the impossible — a flag/lever can never fix an infeasible setup). Any *"reach what RR-MC misses"* / witness-absent eval MUST carry a **feasibility witness** — a valid layout *proven to exist*: hand-authored like [`examples/herrenteich/layout.yaml`](examples/herrenteich/layout.yaml) (a valid all-8 the solver can't find), or one a big-budget `solve` returns. Verify with `hangarfit check <layout> --render out.png` (renders even INVALID layouts; reports validity). Selecting eval scenarios by *"the baseline failed"* structurally **selects for impossibility**. ADR-0028's cold-start probe is the trustworthy evidence (feasibility-grounded by construction); **#832's merged reach-rate result is NOT — it tested an over-capacity infeasible population and is retracted in #835** (which also audited every *training* rung as feasible, so the lever KILLs stand).

The collision checker runs on the `data/catalog/` aircraft (now mostly real published-spec), but `data/hangar.yaml` stays a placeholder, so layouts on the synthetic hangar remain illustrative (the `examples/herrenteich/` hangar is real; its fleet is the shared published-spec catalog).

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

# Mirror CI's #492 two-pass split locally (~3.5× vs a plain serial `pytest`; #624).
# A bare `pytest -n auto` is UNSAFE — it re-flakes the @serial wall-clock
# canaries (rationale: docs/dev/test-flakes-and-ci-gotchas.md §1):
make test        # two-pass split: parallel bulk + serial canaries (the safe mirror)
make test-fast   # parallel bulk only (skips the serial canaries — faster iteration)
# `make help` lists every target (test-slow / test-all / lint / typecheck / format / check).

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

# #666 multi-solution compare: `view --solve --alternatives N` carries up to N diverse
# solutions in ONE HTML with a switcher (dropdown / ←→ keys, shared camera) + per-
# solution metrics. Requires --solve (a hand-authored layout is a single arrangement;
# --alternatives without --solve exits 2). The compare container is a viewer-HTML-level
# `<script id="solutions">` blob (schema `hangarfit.viewer-compare/v1`) layered OVER N
# independent scene/v2 docs — NOT a scene/v2 schema change, so `build_scene` and its
# key-parity guard are untouched and each carried scene's bytes are byte-identical to a
# standalone render (ADR-0003). With <2 diverse layouts found it falls through to the
# single render. The switch path (`mount`→`buildWorld`→`checkAnchors`) re-runs the
# transform self-check per solution; headlessly drive it by dispatching a `change` on
# `#compare` and reading `#banner`.hidden (the viewer exposes state via the DOM).
hangarfit view tests/fixtures/scenario_minimal.yaml --solve --alternatives 3 -o compare.html

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

# Learned-backend RL workspace (ml/, #607 — DEV/CI-ONLY, never shipped in the wheel).
# ml/ is a TOP-LEVEL package, so the editable install (packages.find where=["src"])
# does NOT put it on sys.path — run from the repo root (cwd=root or PYTHONPATH=$PWD).
# torch is the OPTIONAL `[train]` extra; torch-free modules (benchmark) vs torch-needing
# (train/eval/policy/ppo, gated by importorskip in tests). Entry points + the 4c-ii
# training-knob table + A/B command live in ml/README.md.
# CI installs only the [dev] extra (no torch), so its ml/ coverage is the torch-free
# subset — the torch modules importorskip-skip there; full torch-CI is a future #607 rung.
pip install -e ".[train]"      # adds torch for training/eval (CPU is fine)
pytest tests/ml/               # the ml/ test tree (collected by default; testpaths=["tests"])
python -m ml.train --save P    # train + export state_dict (needs [train])

# #706 learned-backend inference (epic #607 sub-project #5). Export a trained policy to
# ONNX, then run it torch-free behind the verifier. Export needs [train] (torch + onnx);
# inference needs the [learned-infer] extra (onnxruntime). With trivial-schedule (under-
# trained) weights the verifier rejects the proposal → a no-layout result, NOT an error.
# (Reaching valid *dense* layouts was the train-to-mastery goal — now resolved-negative; the
# seam still ships and works on what it reaches. See ADR-0028.)
python -m ml.train --schedule trivial --save /tmp/p.pt --save-onnx /tmp/p.onnx   # [train]
hangarfit solve tests/fixtures/scenario_minimal.yaml --backend learned --weights /tmp/p.onnx

# GitFlow loops
git switch develop && git pull
git switch -c feature/<slug>
# ... work ...
git push -u origin feature/<slug>
gh pr create --draft --base develop --title "..." --body "Closes #N ..."
# ... review arc; then flip out of draft when clean ...
gh pr ready <n>
```

## graphify (optional)

graphify is an **optional, per-developer** tool — it is *not* required to work on this project, and `graphify-out/` is gitignored, so nothing in the build / test / CI depends on it. **If** a contributor has graphify installed and `graphify-out/graph.json` exists, it provides a knowledge graph (god nodes, community structure, cross-file relationships) that can scope codebase questions faster than raw grep:

- For "what/where/how-does-X-relate/impact" questions, prefer `graphify query "<question>"` (also `graphify path "<A>" "<B>"` for relationships, `graphify explain "<concept>"` for a focused concept) — these return a scoped subgraph, usually much smaller than `GRAPH_REPORT.md` or raw grep output. Use raw grep/read for line-exact code you're about to modify.
- If `graphify-out/wiki/index.md` exists, use it for broad navigation instead of raw source browsing.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review, or when query/path/explain don't surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

If graphify is not installed, ignore this section.
