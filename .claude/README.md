# Project-local Claude Code config

This directory holds **team-shared** [Claude Code](https://docs.claude.com/en/docs/claude-code) settings that every contributor inherits automatically by checking out the repo. It is the foundation for the rest of the **Contributor automations** milestone (see [issue #35](https://github.com/DocGerd/hangarfit/issues/35)).

## What's here

| File | Status | Purpose |
|---|---|---|
| `settings.json` | committed | Team defaults — a `SessionStart` hook that provisions Python 3.12 in web/remote sessions, a `PreToolUse` guard that blocks hand-edits to the hash-pinned `requirements-*.txt` lockfiles, a `PostToolUse` hook that runs `ruff` + `pytest` after edits under `src/hangarfit/` or `tests/` (and `ruff` + the scoped `pytest tests/ml/` after edits to `ml/*.py`), a second `PostToolUse` hook that reminds you to rebuild `viewer.js` after editing `viewer/src/*.ts`, a third `PostToolUse` hook that reminds you to regenerate the lockfiles after a `pyproject.toml` dependency change, a `PreToolUse` `Bash` hook that warns on `gh pr create` when the branch carries no `CHANGELOG.md` entry (advisory, non-blocking), plus a `Stop` hook that runs `mypy` — over `src/hangarfit/`, and `ml/` too when `torch` is importable — once when a turn finishes. It also lists the team-shared **LSP plugins** under `enabledPlugins` ([see below](#lsp-plugins)). |
| `hooks/session-start.sh` | committed | The `SessionStart` provisioner script (the one hook complex enough to warrant a file rather than an inline command). |
| `agents/` | committed | Project-specific review subagents: `determinism-guard` (solver/towplanner byte-identity), `geometry-invariant-guard` (the coordinate sign-flip trap), and `ml-rl-guard` (the `ml/` RL invariants — seeding/reproducibility, the 4c-ii knob default-neutrality, product-checker validity per #694, the numeric silent-failure guards). |
| `skills/` | committed | Project skills invoked as `/<name>`: `new-fixture`, `new-adr`, `release-prep`, `release-cut`, `trim-memory`, and `ml-ab` (the #607 4c-ii control-vs-treatment basin-escape A/B). |
| `settings.local.json` | **gitignored** | Optional per-contributor override (see below). |
| `README.md` | committed | This file. |

## The hooks

`settings.json` registers seven hooks, all shared with every contributor on clone: one fires when a session starts (`SessionStart`); two fire on the `PreToolUse` event (the `Edit`/`Write` lockfile guard, and a `Bash` `gh pr create` CHANGELOG advisory); three fire on the `Edit`/`Write` `PostToolUse` event (ruff + pytest, a viewer-rebuild reminder, and a lockfile-regen reminder); and one fires when a turn finishes (`Stop`).

### SessionStart — Python 3.12 provisioner (non-blocking, web/remote only)

The project pins a single supported interpreter, **Python 3.12** ([ADR-0009](../docs/adr/0009-single-supported-python-version.md)). In the Claude Code **on-the-web / remote** container the default `python`/`python3` is 3.11, so a bare `pip install -e ".[dev]"` fails the `requires-python = ">=3.12"` gate — and the `PostToolUse` (ruff + pytest) and `Stop` (mypy) hooks below, which call those tools by bare name, can't run.

The `hooks/session-start.sh` script bridges that gap. On session start it:

1. **no-ops outside the remote env** (`$CLAUDE_CODE_REMOTE != "true"`) — local 3.12 developers manage their own environment and are never touched — and no-ops when `HANGARFIT_SKIP_SESSIONSTART_HOOK` is set;
2. creates (once) a 3.12 venv at `.venv312/` from `python3.12` (resolved on `PATH`) and installs the project + dev extras into it. The install is skipped on `resume`/`clear`/`compact` once the venv exists, so only a fresh `startup` (or a missing venv) pays for it;
3. prepends the venv's `bin/` to `PATH` (and sets `VIRTUAL_ENV`) via **`$CLAUDE_ENV_FILE`**, which Claude Code sources into every subsequent command. This is the key step: a hook runs in a subshell, so `source .venv312/bin/activate` would *not* survive to later tool calls — writing to `$CLAUDE_ENV_FILE` makes `python`, `pytest`, `ruff`, and `mypy` all resolve to the 3.12 venv for the rest of the session.

It is **non-blocking**: a missing `python3.12` or a failed `pip install` is reported to stderr and the session still starts (exit `0`). It runs **synchronously** — the session waits until provisioning finishes, trading a slower start for the guarantee that the toolchain is ready before Claude runs anything. (It can be switched to async mode if faster startup is preferred.) Unlike the other four hooks it lives in a **script file** rather than an inline command, because the provisioning logic is too long to read inline.

### PreToolUse — lockfile guard (blocking)

Before an edit lands, this hook inspects the target path. If its basename matches `requirements-*.txt` it **blocks the edit** (exit code `2`) and tells Claude to regenerate the lockfile with the matching `pip-compile` command in [`CLAUDE.md`](../CLAUDE.md) instead. Those files (`requirements-dev.txt`, `requirements-build.txt`, `requirements-fuzz.txt`, `requirements-pip-tools.txt`) are hash-pinned and machine-generated; hand-editing them passes locally but fails the `*-lockfile-drift` CI jobs confusingly. The guard keys on the `requirements-*.txt` glob, so the editable `requirements-*.in` sources are never blocked, and `pip-compile` (which runs via Bash, not Edit/Write) is unaffected. This is the one **blocking** hook — a safety rail, not advisory.

### PreToolUse — CHANGELOG reminder on `gh pr create` (advisory, non-blocking)

Before a `Bash` command runs, this hook inspects the command string. When it contains `gh pr create`, it checks the branch diff (`git diff origin/develop...HEAD --name-only`) for a `CHANGELOG.md` change and, if none is present, prints a one-line reminder to stderr. The reminder **names its own exceptions** so it never reads as a hard error: dev-tooling / `.claude` changes need no entry, and for ≥2 PRs delivered in parallel the entries are collected in one separate CHANGELOG-only PR (see [`CLAUDE.md`](../CLAUDE.md)) — so a missing entry can be intentional. Unlike the lockfile guard above (which `exit 2`-blocks), this is purely **advisory**: it always `exit 0`s and never aborts the `gh pr create`. It is the early local signal for the "each user-facing change carries its own `[Unreleased]` entry" discipline that otherwise surfaces only at review/release time (#802).

### PostToolUse — ruff + pytest (non-blocking)

After Claude Code edits a file, this hook inspects the edited path and:

- If the path matches `src/hangarfit/**` or `tests/**` → runs `ruff check` and `ruff format --check` on the edited file, then `pytest -q --no-header`, showing the tail of each in the transcript.
- If the path matches `ml/*.py` (the RL workspace source; `tests/ml/` already matches the rule above) → runs `ruff check` and `ruff format --check` on the edited file, then the **scoped** `pytest tests/ml/` — an `ml/` change can only break `ml/` tests, since `hangarfit` never imports `ml`. Unlike the `src/`/`tests/` rule this is **not** a CI mirror: CI's `ruff check src/ tests/ bench/` and `mypy src/hangarfit` exclude `ml/` (torch isn't installed in CI), so for the RL workspace this hook plus the `ml-rl-guard` subagent are the primary local signal.
- Otherwise → no-op.

This mirrors three of the gates CI enforces (`ruff check`, `ruff format --check`, `pytest`) so problems surface on edit instead of in CI. `mypy` is deliberately omitted — too slow to run on every edit. The hook is **non-blocking**: it always exits `0`, even if a check fails. Failing output is shown to Claude as feedback, but the edit itself is never aborted. Treat it as a fast smoke signal, not a gate.

Path matching is glob-based (`*/src/hangarfit/*` / `*/tests/*`), anchored with a leading `/` so that sibling directories like `vendor-src/hangarfit/` or `contests/` do not accidentally trigger the hook.

### PostToolUse — viewer-rebuild reminder (non-blocking)

After Claude Code edits a `viewer/src/*.ts` file, this second `PostToolUse` hook prints **one** line reminding you to rebuild the committed bundle (`npm --prefix viewer/ run build`) and commit `src/hangarfit/_viewer_assets/viewer.js` in the **same** change, citing the `viewer-build-drift` CI guard ([ADR-0020](../docs/adr/0020-viewer-typescript-architecture.md)). The shipped `viewer.js` *is* the esbuild output, so a `.ts` edit without a matching rebuild drifts the bundle and trips that guard — but the guard is path-gated **and** non-required ([`viewer.yml`](../.github/workflows/viewer.yml)), so the breakage surfaces late. The reminder is the early local signal (#568).

It deliberately does **not** run esbuild — that would re-impose the velocity-tax pattern that got the per-edit pytest hook disabled — and it makes no git-status check: at `PostToolUse` the bundle is *always* unrebuilt, so a path-match-only reminder is the honest signal. Like its sibling it is **non-blocking** (always exits `0`) and fires only on `.ts` edits, so non-viewer work is untouched.

### PostToolUse — lockfile-regen reminder (non-blocking)

After Claude Code edits `pyproject.toml`, this third `PostToolUse` hook prints **one** line reminding you that a change to `[project]` dependencies (or an optional extra) requires regenerating the four hash-pinned lockfiles (`requirements-dev.txt`, `requirements-build.txt`, `requirements-fuzz.txt`, `requirements-pip-tools.txt`) with the `pip-compile` recipes in [`CLAUDE.md`](../CLAUDE.md) / [`docs/dev/lockfiles.md`](../docs/dev/lockfiles.md). It is the dependency-surface complement to the `PreToolUse` lockfile **guard** above: the guard blocks hand-edits to the *generated* lockfiles, while this reminder catches the *upstream* `pyproject.toml` edit that makes them stale — before the `*-lockfile-drift` CI jobs surface it (#801). Like the other `PostToolUse` hooks it is **non-blocking** (always exits `0`), fires only on a `pyproject.toml` basename match, and honours its own `HANGARFIT_SKIP_LOCKFILE_HOOK` opt-out.

### Stop — mypy (non-blocking)

When a turn finishes, this hook runs `mypy src/hangarfit` once and shows the tail of its output in the transcript; when `torch` is importable it additionally runs `mypy ml/` (the RL workspace). The `ml/` pass is gated on `torch` because `ml/` imports it — so the check is a clean no-op in the torch-less CI/web env, matching the future torch-CI rung rather than failing there. `mypy` is a hard CI gate (`.github/workflows/ci.yml`, "Type-check with mypy") and the *one* gate the on-edit `PostToolUse` hook does not mirror — running a full type-check on every single edit is too slow to be worth it. Amortizing it to once per turn surfaces type errors before a PR reaches CI without paying the cost on each keystroke. Like the `PostToolUse` hooks it is **non-blocking**: it always exits `0` even when `mypy` reports errors, so a turn is never aborted — the output is feedback, not a gate.

## Opting out (per contributor)

Set the env var `HANGARFIT_SKIP_PYTEST_HOOK=1` in your shell init (`~/.bashrc`, `~/.zshrc`, etc.). The **PostToolUse** ruff + pytest hook detects this and exits immediately. The **PostToolUse** viewer-rebuild reminder honours its own `HANGARFIT_SKIP_VIEWER_HOOK=1`, the **PostToolUse** lockfile-regen reminder honours `HANGARFIT_SKIP_LOCKFILE_HOOK=1`, the **Stop** mypy hook honours a separate `HANGARFIT_SKIP_MYPY_HOOK=1`, and the **SessionStart** provisioner honours `HANGARFIT_SKIP_SESSIONSTART_HOOK=1`, so each can be disabled independently (the SessionStart hook also already no-ops entirely outside the web/remote env). Unset (or restart your shell) to re-enable. The two **PreToolUse** hooks are intentionally **not** opt-out-able: the lockfile guard is a cheap safety rail whose legitimate regeneration path (`pip-compile` via Bash) is never blocked anyway, and the `gh pr create` CHANGELOG advisory is a single non-blocking line that fires only on PR creation and already names its own exceptions.

```bash
# ~/.bashrc or ~/.zshrc
export HANGARFIT_SKIP_SESSIONSTART_HOOK=1
export HANGARFIT_SKIP_PYTEST_HOOK=1
export HANGARFIT_SKIP_VIEWER_HOOK=1
export HANGARFIT_SKIP_LOCKFILE_HOOK=1
export HANGARFIT_SKIP_MYPY_HOOK=1
```

The old `.claude/settings.local.json` opt-out mentioned in earlier drafts of this README does **not** work — Claude Code merges hook arrays across scopes rather than overriding them, so an empty array in local doesn't subtract the project entry. The env-var pattern moves the opt-out into a layer that actually short-circuits.

## LSP plugins

`settings.json` also enables two **language-server plugins** under `enabledPlugins`, so every contributor gets in-editor static analysis (diagnostics, hover, go-to-references) on a fresh clone — the editor-side analogue of the CI lint/type gates. Unlike the hooks above, plugins run only in the editor: they never touch CI, the build, the `scene/v1` contract, or determinism.

| Plugin | Covers | Config |
|---|---|---|
| `pyright-lsp@claude-plugins-official` | The repo's Python under the workspace root. | None — there is no `pyrightconfig.json` / `[tool.pyright]`, so Pyright analyzes the repo's Python with defaults. It complements the `Stop` **mypy** hook: `mypy` is the CI gate ([ci.yml](../.github/workflows/ci.yml)), Pyright is the live editor signal. |
| `typescript-lsp@claude-plugins-official` | The **TypeScript source** of the 3D viewer ([ADR-0017](../docs/adr/0017-3d-viewer-architecture.md)) under the top-level `viewer/src/*.ts` — the only first-party TS in the repo. (The shipped `src/hangarfit/_viewer_assets/viewer.js` is the **generated** esbuild bundle, not a source; see the note below.) | [`viewer/tsconfig.json`](../viewer/tsconfig.json) drives the analysis in **strict** mode — the same config CI's `viewer-toolchain` job runs as `tsc --noEmit`, so the editor signal and the CI gate are the *same* typecheck. The `three` import resolves to the pinned `three` devDep (typed by `@types/three` — both `viewer/` devDeps), so the LSP never analyzes the vendored runtime bundle from disk. |

**The `viewer/` TypeScript toolchain ([ADR-0020](../docs/adr/0020-viewer-typescript-architecture.md), issue #437).** The top-level `viewer/` directory holds a **dev/CI-only** TypeScript toolchain (esbuild + tsc + eslint) that builds the committed `src/hangarfit/_viewer_assets/viewer.js` from `viewer/src/*.ts`. Since the #439 port the shipped `viewer.js` **is** that esbuild output — a generated artifact, not a source. The `typescript-lsp` plugin + `viewer/tsconfig.json` give live editor diagnostics on the TS source, and that source is gated in CI by the `viewer-toolchain` job (`tsc --noEmit` + eslint), so the editor/CI relationship mirrors Pyright/mypy. Since #475 that job lives in its own **path-gated** workflow, [`.github/workflows/viewer.yml`](../.github/workflows/viewer.yml), so it runs only on viewer-affecting changes (`viewer/**`, `src/hangarfit/_viewer_assets/**`) rather than on every PR — it stays a non-required check, which is what makes the workflow-level `paths:` skip safe. The byte-drift gate on the *shipped* `viewer.js` — the closer analogue of mypy-as-final-gate — is the `viewer-build-drift` step of that same `viewer-toolchain` job (#438): it rebuilds the bundle and diffs it against the committed copy. The Node toolchain is **never** part of `pip install` / the wheel build / pytest, and esbuild is **not** run by either `PostToolUse` hook: after editing `viewer/src/*.ts`, rebuild and commit `viewer.js` yourself (`npm --prefix viewer/ run build`). The viewer-rebuild reminder hook (above) only *nudges* you to — it never builds.

> **Why no `checkJs` over the shipped bundle.** #423 added a repo-root `jsconfig.json` (`checkJs: true`) and an ambient-module shim `_lsp_shims.d.ts` so the LSP could analyze the *then-hand-written* `viewer.js`. The #439 port made `viewer.js` a generated bundle; esbuild **erases** the TS type assertions (`byId<HTMLInputElement>(…)` → `byId(…)`), so `checkJs` over the erased output reported `ts(2339)` DOM diagnostics ("`Property 'value' does not exist on type 'HTMLElement'`") that are *unfixable in place* — the source is already strict-typed and the bundle is regenerated and byte-guarded. The typed surface moved to `viewer/src/*.ts`, so both the `jsconfig.json` and `_lsp_shims.d.ts` were retired (issue #433; ADR-0020 records the subsumption). The editor now gets its diagnostics from the strict `viewer/tsconfig.json`, not from `checkJs` over the bundle.

### Disabling a plugin per contributor

The hooks opt out via env vars; the plugins are toggled through `enabledPlugins` instead. To disable one for yourself without touching the committed default, set it to `false` under `enabledPlugins` in your **gitignored** `.claude/settings.local.json`:

```json
{
  "enabledPlugins": {
    "typescript-lsp@claude-plugins-official": false
  }
}
```

`enabledPlugins` is a JSON object keyed by plugin id, so the most-local scope wins per key — a local `false` overrides the committed `true`. (This is why plugins can opt out via `settings.local.json` even though the hook *arrays* above cannot: object keys are overridden, whereas arrays are merged.)

## Adding new automations

Future entries in this milestone — subagents, skills, additional hooks — should also live under `.claude/` so they ship to every contributor on clone. Keep the team defaults conservative (non-blocking, opt-out-able) so a freshly-cloned checkout never surprises a contributor with mandatory behavior.
