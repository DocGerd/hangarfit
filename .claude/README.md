# Project-local Claude Code config

This directory holds **team-shared** [Claude Code](https://docs.claude.com/en/docs/claude-code) settings that every contributor inherits automatically by checking out the repo. It is the foundation for the rest of the **Contributor automations** milestone (see [issue #35](https://github.com/DocGerd/hangarfit/issues/35)).

## What's here

| File | Status | Purpose |
|---|---|---|
| `settings.json` | committed | Team defaults — auto-run `pytest` after edits under `src/hangarfit/` or `tests/`. |
| `settings.local.json` | **gitignored** | Optional per-contributor override (see below). |
| `README.md` | committed | This file. |

## The PostToolUse pytest hook

`settings.json` registers a `PostToolUse` hook on the `Edit` and `Write` tools. After Claude Code edits a file, the hook inspects the edited path and:

- If the path matches `src/hangarfit/**` or `tests/**` → runs `pytest -q --no-header 2>&1 | tail -30` and shows the last 30 lines in the transcript.
- Otherwise → no-op.

The hook is **non-blocking**: it always exits `0`, even if `pytest` fails. The failing output is shown to Claude as feedback, but the edit itself is never aborted. Treat it as a fast smoke signal, not a gate.

Path matching is glob-based (`*/src/hangarfit/*` / `*/tests/*`), anchored with a leading `/` so that sibling directories like `vendor-src/hangarfit/` or `contests/` do not accidentally trigger the hook.

## Opting out (per contributor)

Set the env var `HANGARFIT_SKIP_PYTEST_HOOK=1` in your shell init (`~/.bashrc`, `~/.zshrc`, etc.). The hook detects this and exits immediately without running pytest. Unset (or restart your shell) to re-enable.

```bash
# ~/.bashrc or ~/.zshrc
export HANGARFIT_SKIP_PYTEST_HOOK=1
```

The old `.claude/settings.local.json` opt-out mentioned in earlier drafts of this README does **not** work — Claude Code merges hook arrays across scopes rather than overriding them, so an empty array in local doesn't subtract the project entry. The env-var pattern moves the opt-out into a layer that actually short-circuits.

## Adding new automations

Future entries in this milestone — subagents, skills, additional hooks — should also live under `.claude/` so they ship to every contributor on clone. Keep the team defaults conservative (non-blocking, opt-out-able) so a freshly-cloned checkout never surprises a contributor with mandatory behavior.
