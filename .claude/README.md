# Project-local Claude Code config

This directory holds **team-shared** [Claude Code](https://docs.claude.com/en/docs/claude-code) settings that every contributor inherits automatically by checking out the repo. It is the foundation for the rest of the **Contributor automations** milestone (see [issue #35](https://github.com/DocGerd/hangarfit/issues/35)).

## What's here

| File | Status | Purpose |
|---|---|---|
| `settings.json` | committed | Team defaults — a `PreToolUse` guard that blocks hand-edits to the hash-pinned `requirements-*.txt` lockfiles, plus a `PostToolUse` hook that runs `ruff` + `pytest` after edits under `src/hangarfit/` or `tests/`. |
| `settings.local.json` | **gitignored** | Optional per-contributor override (see below). |
| `README.md` | committed | This file. |

## The on-edit hooks

`settings.json` registers two hooks on the `Edit` and `Write` tools, both shared with every contributor on clone.

### PreToolUse — lockfile guard (blocking)

Before an edit lands, this hook inspects the target path. If its basename matches `requirements-*.txt` it **blocks the edit** (exit code `2`) and tells Claude to regenerate the lockfile with the matching `pip-compile` command in [`CLAUDE.md`](../CLAUDE.md) instead. Those files (`requirements-dev.txt`, `requirements-build.txt`, `requirements-fuzz.txt`, `requirements-pip-tools.txt`) are hash-pinned and machine-generated; hand-editing them passes locally but fails the `*-lockfile-drift` CI jobs confusingly. The guard keys on the `requirements-*.txt` glob, so the editable `requirements-*.in` sources are never blocked, and `pip-compile` (which runs via Bash, not Edit/Write) is unaffected. This is the one **blocking** hook — a safety rail, not advisory.

### PostToolUse — ruff + pytest (non-blocking)

After Claude Code edits a file, this hook inspects the edited path and:

- If the path matches `src/hangarfit/**` or `tests/**` → runs `ruff check` and `ruff format --check` on the edited file, then `pytest -q --no-header`, showing the tail of each in the transcript.
- Otherwise → no-op.

This mirrors three of the gates CI enforces (`ruff check`, `ruff format --check`, `pytest`) so problems surface on edit instead of in CI. `mypy` is deliberately omitted — too slow to run on every edit. The hook is **non-blocking**: it always exits `0`, even if a check fails. Failing output is shown to Claude as feedback, but the edit itself is never aborted. Treat it as a fast smoke signal, not a gate.

Path matching is glob-based (`*/src/hangarfit/*` / `*/tests/*`), anchored with a leading `/` so that sibling directories like `vendor-src/hangarfit/` or `contests/` do not accidentally trigger the hook.

## Opting out (per contributor)

Set the env var `HANGARFIT_SKIP_PYTEST_HOOK=1` in your shell init (`~/.bashrc`, `~/.zshrc`, etc.). The **PostToolUse** ruff + pytest hook detects this and exits immediately. Unset (or restart your shell) to re-enable. The PreToolUse lockfile guard is intentionally **not** opt-out-able — it is a cheap safety rail and the legitimate regeneration path (`pip-compile` via Bash) is never blocked anyway.

```bash
# ~/.bashrc or ~/.zshrc
export HANGARFIT_SKIP_PYTEST_HOOK=1
```

The old `.claude/settings.local.json` opt-out mentioned in earlier drafts of this README does **not** work — Claude Code merges hook arrays across scopes rather than overriding them, so an empty array in local doesn't subtract the project entry. The env-var pattern moves the opt-out into a layer that actually short-circuits.

## Adding new automations

Future entries in this milestone — subagents, skills, additional hooks — should also live under `.claude/` so they ship to every contributor on clone. Keep the team defaults conservative (non-blocking, opt-out-able) so a freshly-cloned checkout never surprises a contributor with mandatory behavior.
