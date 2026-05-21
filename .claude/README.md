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

Path matching is glob-based (`*src/hangarfit/*` / `*tests/*`), so both relative and absolute `file_path` payloads are handled.

## Opting out (per contributor)

If you'd rather not have pytest run on every edit (e.g., you're working on a machine where the suite is slow, or you want to disable the hook while debugging something else), drop a `.claude/settings.local.json` next to `settings.json` with an empty hooks override:

```json
{
  "hooks": {
    "PostToolUse": []
  }
}
```

`settings.local.json` overrides `settings.json` (Claude Code merges/overrides local-over-team), and it is listed in the repo's `.gitignore` so your personal override is never committed.

To **re-enable** the hook later, just delete `.claude/settings.local.json`.

## Adding new automations

Future entries in this milestone — subagents, skills, additional hooks — should also live under `.claude/` so they ship to every contributor on clone. Keep the team defaults conservative (non-blocking, opt-out-able) so a freshly-cloned checkout never surprises a contributor with mandatory behavior.
