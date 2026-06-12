# hangarfit dev Makefile — a local mirror of the CI gates (.github/workflows/ci.yml).
#
# `make test` runs CI's two-pass test split (#492) LOCALLY: a parallel bulk pass
# plus a separate serial pass for the wall-clock determinism canaries. On a
# 32-core box this is ~169 s versus ~588 s for a plain serial `pytest`
# (3.5x; profiling spike #617, lever #624).
#
# Why two passes and not a bare `pytest -n auto`? The `serial`-marked canaries
# run solve() twice in-process under a wall-clock budget; under xdist CPU
# starvation the two solves can complete different restart counts and DIVERGE,
# re-exposing a determinism flake. Keeping them in a separate single-process pass
# is the whole point — do NOT collapse `test` into one `-n auto` run.
# Full rationale: docs/dev/test-flakes-and-ci-gotchas.md §1.
#
# These targets drop CI's `--cov` flags (coverage is a Codecov-upload concern,
# not a local gate) but otherwise mirror ci.yml — including linting `bench/`
# alongside `src/` and `tests/`. Run from the repo root (no `cd` needed).

.PHONY: help test test-fast test-slow test-all lint format typecheck check

help:  ## Show this help (default target)
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-11s\033[0m %s\n", $$1, $$2}'

test:  ## Two-pass split (parallel bulk + serial canaries) — the safe local CI mirror
	pytest -n auto -m "not slow and not serial"
	pytest -m "serial and not slow"

test-fast:  ## Parallel bulk only (5.2x; SKIPS the serial canaries — run `make test` before pushing)
	pytest -n auto -m "not slow and not serial"

test-slow:  ## The @slow set only (heavy solves; excluded from `make test` and from CI)
	pytest -m slow

test-all:  ## Everything regardless of marker, single-process (slowest but always safe)
	pytest -m ""

lint:  ## ruff check + format --check (mirrors ci.yml, incl. bench/)
	ruff check src/ tests/ bench/
	ruff format --check src/ tests/ bench/

format:  ## Auto-fix lint findings and format in place
	ruff check --fix src/ tests/ bench/
	ruff format src/ tests/ bench/

typecheck:  ## mypy (mirrors ci.yml)
	mypy src/hangarfit

check: lint typecheck test  ## Full local pre-push gate: lint + typecheck + two-pass tests
