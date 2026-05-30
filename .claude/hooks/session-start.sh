#!/usr/bin/env bash
# SessionStart hook — provision Python 3.12 for Claude Code web/remote sessions.
#
# Why this exists (#354): the project pins `requires-python = ">=3.12"`
# (ADR-0009), but the Claude Code on-the-web container's default
# `python`/`python3` is 3.11. A naive `pip install -e ".[dev]"` therefore
# fails the version gate, and the team-shared PostToolUse (ruff+pytest) and
# Stop (mypy) hooks — which call those tools by bare name — can't run.
#
# This hook builds (once) a 3.12 venv at `.venv312/`, installs the dev
# extras into it, and — crucially — prepends the venv's `bin/` to `PATH`
# via `$CLAUDE_ENV_FILE`. A hook runs in a subshell, so `source activate`
# would not survive to later tool calls; `$CLAUDE_ENV_FILE` is sourced by
# Claude Code into every subsequent command, so `python`, `pytest`, `ruff`,
# and `mypy` all then resolve to the 3.12 venv for the rest of the session.
#
# It is idempotent (safe to re-run), non-interactive, and a no-op outside the
# remote environment or when `HANGARFIT_SKIP_SESSIONSTART_HOOK` is set.
set -euo pipefail

# stdin carries the SessionStart JSON payload; we don't need it. Drain it so a
# closed pipe never wedges the hook.
cat >/dev/null 2>&1 || true

# Per-contributor opt-out, matching the HANGARFIT_SKIP_* pattern of the other
# hooks (see .claude/README.md).
if [ -n "${HANGARFIT_SKIP_SESSIONSTART_HOOK:-}" ]; then
  exit 0
fi

# Only needed in the web/remote container. Local 3.12 developers manage their
# own environment; running here would risk clobbering it.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

PY312="$(command -v python3.12 || true)"
if [ -z "$PY312" ]; then
  echo "[hangarfit session-start] python3.12 not found on PATH; cannot provision the" \
    "3.12 venv this project requires (ADR-0009). Tests/linters may not run." >&2
  exit 0
fi

VENV=".venv312"
if [ ! -x "$VENV/bin/python" ]; then
  echo "[hangarfit session-start] creating $VENV with $PY312"
  "$PY312" -m venv "$VENV"
fi

# Editable install of the project + dev extras. Cheap to re-run when already
# satisfied, and the container caches the result after the hook completes.
# Non-fatal: a transient failure should not wedge session start — the error is
# surfaced and the session continues.
"$VENV/bin/python" -m pip install --quiet --upgrade pip || true
if ! "$VENV/bin/python" -m pip install --quiet -e ".[dev]"; then
  echo "[hangarfit session-start] 'pip install -e .[dev]' failed; the 3.12 env may be" \
    "incomplete (check network / logs above)." >&2
  exit 0
fi

# Persist the interpreter choice for the whole session (see header note).
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$PWD/$VENV\""
    echo "export PATH=\"$PWD/$VENV/bin:\$PATH\""
  } >>"$CLAUDE_ENV_FILE"
fi

echo "[hangarfit session-start] Python 3.12 venv ready at $VENV ($("$VENV/bin/python" -V))"
exit 0
