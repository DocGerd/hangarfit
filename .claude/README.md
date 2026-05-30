# Project-local Claude Code config

This directory holds **team-shared** [Claude Code](https://docs.claude.com/en/docs/claude-code) settings that every contributor inherits automatically by checking out the repo. It is the foundation for the rest of the **Contributor automations** milestone (see [issue #35](https://github.com/DocGerd/hangarfit/issues/35)).

## What's here

| File | Status | Purpose |
|---|---|---|
| `settings.json` | committed | Team defaults — a `SessionStart` hook that provisions Python 3.12 in web/remote sessions, a `PreToolUse` guard that blocks hand-edits to the hash-pinned `requirements-*.txt` lockfiles, a `PostToolUse` hook that runs `ruff` + `pytest` after edits under `src/hangarfit/` or `tests/`, plus a `Stop` hook that runs `mypy` once when a turn finishes. |
| `hooks/session-start.sh` | committed | The `SessionStart` provisioner script (the one hook complex enough to warrant a file rather than an inline command). |
| `settings.local.json` | **gitignored** | Optional per-contributor override (see below). |
| `README.md` | committed | This file. |

## The hooks

`settings.json` registers four hooks, all shared with every contributor on clone: one fires when a session starts (`SessionStart`), two fire on the `Edit` and `Write` tools, and one fires when a turn finishes (`Stop`).

### SessionStart — Python 3.12 provisioner (non-blocking, web/remote only)

The project pins a single supported interpreter, **Python 3.12** ([ADR-0009](../docs/adr/0009-single-supported-python-version.md)). In the Claude Code **on-the-web / remote** container the default `python`/`python3` is 3.11, so a bare `pip install -e ".[dev]"` fails the `requires-python = ">=3.12"` gate — and the `PostToolUse` (ruff + pytest) and `Stop` (mypy) hooks below, which call those tools by bare name, can't run.

The `hooks/session-start.sh` script bridges that gap. On session start it:

1. **no-ops outside the remote env** (`$CLAUDE_CODE_REMOTE != "true"`) — local 3.12 developers manage their own environment and are never touched — and no-ops when `HANGARFIT_SKIP_SESSIONSTART_HOOK` is set;
2. creates (once) a 3.12 venv at `.venv312/` from `python3.12` (resolved on `PATH`) and installs the project + dev extras into it. The install is skipped on `resume`/`clear`/`compact` once the venv exists, so only a fresh `startup` (or a missing venv) pays for it;
3. prepends the venv's `bin/` to `PATH` (and sets `VIRTUAL_ENV`) via **`$CLAUDE_ENV_FILE`**, which Claude Code sources into every subsequent command. This is the key step: a hook runs in a subshell, so `source .venv312/bin/activate` would *not* survive to later tool calls — writing to `$CLAUDE_ENV_FILE` makes `python`, `pytest`, `ruff`, and `mypy` all resolve to the 3.12 venv for the rest of the session.

It is **non-blocking**: a missing `python3.12` or a failed `pip install` is reported to stderr and the session still starts (exit `0`). It runs **synchronously** — the session waits until provisioning finishes, trading a slower start for the guarantee that the toolchain is ready before Claude runs anything. (It can be switched to async mode if faster startup is preferred.) Unlike the other three hooks it lives in a **script file** rather than an inline command, because the provisioning logic is too long to read inline.

### PreToolUse — lockfile guard (blocking)

Before an edit lands, this hook inspects the target path. If its basename matches `requirements-*.txt` it **blocks the edit** (exit code `2`) and tells Claude to regenerate the lockfile with the matching `pip-compile` command in [`CLAUDE.md`](../CLAUDE.md) instead. Those files (`requirements-dev.txt`, `requirements-build.txt`, `requirements-fuzz.txt`, `requirements-pip-tools.txt`) are hash-pinned and machine-generated; hand-editing them passes locally but fails the `*-lockfile-drift` CI jobs confusingly. The guard keys on the `requirements-*.txt` glob, so the editable `requirements-*.in` sources are never blocked, and `pip-compile` (which runs via Bash, not Edit/Write) is unaffected. This is the one **blocking** hook — a safety rail, not advisory.

### PostToolUse — ruff + pytest (non-blocking)

After Claude Code edits a file, this hook inspects the edited path and:

- If the path matches `src/hangarfit/**` or `tests/**` → runs `ruff check` and `ruff format --check` on the edited file, then `pytest -q --no-header`, showing the tail of each in the transcript.
- Otherwise → no-op.

This mirrors three of the gates CI enforces (`ruff check`, `ruff format --check`, `pytest`) so problems surface on edit instead of in CI. `mypy` is deliberately omitted — too slow to run on every edit. The hook is **non-blocking**: it always exits `0`, even if a check fails. Failing output is shown to Claude as feedback, but the edit itself is never aborted. Treat it as a fast smoke signal, not a gate.

Path matching is glob-based (`*/src/hangarfit/*` / `*/tests/*`), anchored with a leading `/` so that sibling directories like `vendor-src/hangarfit/` or `contests/` do not accidentally trigger the hook.

### Stop — mypy (non-blocking)

When a turn finishes, this hook runs `mypy src/hangarfit` once and shows the tail of its output in the transcript. `mypy` is a hard CI gate (`.github/workflows/ci.yml`, "Type-check with mypy") and the *one* gate the on-edit `PostToolUse` hook does not mirror — running a full type-check on every single edit is too slow to be worth it. Amortizing it to once per turn surfaces type errors before a PR reaches CI without paying the cost on each keystroke. Like the `PostToolUse` hook it is **non-blocking**: it always exits `0` even when `mypy` reports errors, so a turn is never aborted — the output is feedback, not a gate.

## Opting out (per contributor)

Set the env var `HANGARFIT_SKIP_PYTEST_HOOK=1` in your shell init (`~/.bashrc`, `~/.zshrc`, etc.). The **PostToolUse** ruff + pytest hook detects this and exits immediately. The **Stop** mypy hook honours a separate `HANGARFIT_SKIP_MYPY_HOOK=1`, and the **SessionStart** provisioner honours `HANGARFIT_SKIP_SESSIONSTART_HOOK=1`, so all three can be disabled independently (the SessionStart hook also already no-ops entirely outside the web/remote env). Unset (or restart your shell) to re-enable. The PreToolUse lockfile guard is intentionally **not** opt-out-able — it is a cheap safety rail and the legitimate regeneration path (`pip-compile` via Bash) is never blocked anyway.

```bash
# ~/.bashrc or ~/.zshrc
export HANGARFIT_SKIP_SESSIONSTART_HOOK=1
export HANGARFIT_SKIP_PYTEST_HOOK=1
export HANGARFIT_SKIP_MYPY_HOOK=1
```

The old `.claude/settings.local.json` opt-out mentioned in earlier drafts of this README does **not** work — Claude Code merges hook arrays across scopes rather than overriding them, so an empty array in local doesn't subtract the project entry. The env-var pattern moves the opt-out into a layer that actually short-circuits.

## Adding new automations

Future entries in this milestone — subagents, skills, additional hooks — should also live under `.claude/` so they ship to every contributor on clone. Keep the team defaults conservative (non-blocking, opt-out-able) so a freshly-cloned checkout never surprises a contributor with mandatory behavior.
