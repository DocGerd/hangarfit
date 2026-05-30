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
# SessionStart fires on startup AND resume/clear/compact. The work here is
# made idempotent across all of those: the venv is created once, the env-file
# export is written once (guarded against duplicate appends that would bloat
# PATH), and the expensive `pip install` is skipped on non-startup sources
# when the venv already exists.
#
# Non-interactive, and a no-op outside the remote environment or when
# `HANGARFIT_SKIP_SESSIONSTART_HOOK` is set.
set -euo pipefail

# stdin carries the SessionStart JSON payload. Capture it (rather than
# discarding) so we can read `.source` (startup|resume|clear|compact) below.
# `|| true` neutralizes a closed-pipe nonzero under `set -e`.
PAYLOAD="$(cat 2>/dev/null || true)"

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

# Guarded so a missing/unreadable project dir can't trip `set -e` — this hook
# advertises itself as strictly non-blocking (always exit 0).
cd "${CLAUDE_PROJECT_DIR:-.}" || {
  echo "[hangarfit session-start] cannot cd to project dir; skipping." >&2
  exit 0
}

# Resolve the interpreter via PATH (more robust than a hardcoded /usr/bin path:
# tolerates /usr/local, a pyenv shim, etc.).
PY312="$(command -v python3.12 || true)"
if [ -z "$PY312" ]; then
  echo "[hangarfit session-start] python3.12 not found on PATH; cannot provision the" \
    "3.12 venv this project requires (ADR-0009). Tests/linters may not run." >&2
  exit 0
fi

VENV=".venv312"
venv_existed=true
if [ ! -x "$VENV/bin/python" ]; then
  venv_existed=false
  echo "[hangarfit session-start] creating $VENV with $PY312"
  "$PY312" -m venv "$VENV"
fi

# Re-installing the dev extras on every resume/clear/compact adds latency for
# no benefit once the env is built. Run the install on a fresh `startup`, or
# whenever the venv had to be (re)created; otherwise skip it.
source_field="$(printf '%s' "$PAYLOAD" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("source",""))' 2>/dev/null || true)"
if [ "$source_field" = "startup" ] || [ "$venv_existed" = false ]; then
  # Editable install of the project + dev extras. Deliberately NOT
  # `--require-hashes` against requirements-dev.txt: this is a dev-convenience
  # provisioner whose only job is a working ruff/pytest/mypy toolchain, and an
  # unpinned resolve from pyproject.toml can't fail on lockfile skew. Do not
  # "fix" it to the hash-pinned CI path. Non-fatal: a transient failure should
  # not wedge session start — the error is surfaced and the session continues.
  "$VENV/bin/python" -m pip install --quiet --upgrade pip || true
  if ! "$VENV/bin/python" -m pip install --quiet -e ".[dev]"; then
    echo "[hangarfit session-start] 'pip install -e .[dev]' failed; the 3.12 env may be" \
      "incomplete (check network / logs above)." >&2
    exit 0
  fi
fi

# Persist the interpreter choice for the whole session (see header note).
# Append-once: SessionStart can fire repeatedly into the same env file, and an
# unguarded `>>` would stack duplicate `export PATH=...` lines and bloat PATH.
if [ -n "${CLAUDE_ENV_FILE:-}" ] && ! grep -qF "$PWD/$VENV/bin" "$CLAUDE_ENV_FILE" 2>/dev/null; then
  {
    echo "export VIRTUAL_ENV=\"$PWD/$VENV\""
    echo "export PATH=\"$PWD/$VENV/bin:\$PATH\""
  } >>"$CLAUDE_ENV_FILE"
fi

echo "[hangarfit session-start] Python 3.12 venv ready at $VENV ($("$VENV/bin/python" -V))"
exit 0
