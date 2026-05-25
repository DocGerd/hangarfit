# ADR-0009: Single supported Python — 3.12, anchored to the distro LTS

- **Status:** Proposed
  <!-- Flips to Accepted when the implementing PR (#213) merges. -->

- **Date:** 2026-05-25
- **Deciders:** Patrick Kuhn (DocGerd)

## Context & Problem Statement

Through Phase 2 the project advertised "Python 3.11 or newer" and tested a
two-version CI matrix (3.11 + 3.12). Carrying a version *range* multiplies the
support surface: a two-cell test matrix, lockfiles whose resolution is anchored
to the *lowest* supported interpreter, and "3.11 or newer" claims scattered
across README, CONTRIBUTING, CLAUDE.md, and the arc42 constraints. We want one
supported interpreter — but CPython has **no LTS**: under [PEP 602](https://peps.python.org/pep-0602/)
every minor release gets the same ~5-year lifecycle and the core project never
labels any version "LTS." So the question this ADR answers is: *which single
interpreter do we support, and how is the choice defensible absent a CPython
LTS designation?*

## Decision Drivers

- **Shrink the support surface to one interpreter.** One CI job, one lockfile
  resolution target, one version claim in the docs.
- **Anchor the choice to an external long-term-support lifecycle.** Since
  CPython names no LTS, the durable anchor is the distribution that provides
  long-term support for an interpreter.
- **Universal wheel availability.** The scientific dependencies (shapely,
  matplotlib, numpy) must ship binary wheels for the chosen version with no
  source-build fallback.
- **Don't chase newest-stable.** A brand-new minor carries higher tooling/wheel
  risk and has no LTS backing either.
- **Don't hard-pin a single version.** A CLI/library should let downstream
  users run a newer interpreter if they have one.

## Considered Options

1. **Python `>=3.12`, anchored to Ubuntu 24.04 LTS** *(chosen).* Ubuntu
   24.04 LTS ships Python 3.12 with a ~5-year support window.
2. **Keep the `3.11+` range and the two-version matrix** — the status quo.
3. **Target the newest stable (3.14)** — truest to "most recent."
4. **Hard-pin `==3.12.*`** — the most literal "one version only."

## Decision Outcome

**Chosen option: a `requires-python = ">=3.12"` floor, anchored to Ubuntu
24.04 LTS**, because the distro LTS supplies the ~5-year support horizon that
CPython itself refuses to name, Python 3.12 has universal binary wheels for
every dependency, and a `>=` floor (rather than a pin) keeps the door open for
contributors and users already on 3.13+.

Concretely: `pyproject.toml` sets `requires-python = ">=3.12"`, `ruff`
`target-version = "py312"`, and `mypy` `python_version = "3.12"`; both
hash-pinned lockfiles are resolved on a real 3.12 interpreter; the CI `test`
matrix collapses to a single `3.12` job and the two lockfile-drift guards
regenerate on 3.12.

### Why not keep the `3.11+` range?

That is exactly the cost we are removing. The range forces a two-cell matrix, a
lockfile anchored to the lowest interpreter (so the resolved wheels cover the
widest span), and "3.11 or newer" claims that must be kept in sync across four
docs. None of it buys anything once we commit to a single supported version.

### Why not target the newest stable (3.14)?

It is the truest reading of "most recent," but CPython has no LTS to back *any*
version, so newest-stable would give us the *least* settled tooling/wheel
ecosystem (scientific wheels routinely lag a new minor by months) with no
compensating support guarantee. The distro-LTS anchor is the more defensible
stability story than "whatever CPython released last."

### Why not hard-pin `==3.12.*`?

It is the most literal "one version only," but it blocks anyone already on
3.13+ and is unusual for a CLI/library — a `>=3.12` floor expresses "this
version and forward" idiomatically while still giving CI a single concrete
interpreter to test against.

## Consequences

### Positive

- One CI `test` job instead of two; the lockfiles are resolved on the exact
  interpreter CI runs, so their `with Python 3.12` header is self-documenting
  and the drift guards reproduce a byte-identical set.
- A single "Python 3.12 or newer" claim across all operational docs.
- A modern 3.12 baseline for `ruff`/`mypy` and `UP` (pyupgrade) lint rules.

### Negative

- Raising `requires-python` is a **breaking change** for any 3.11 user →
  warrants at least a **minor version bump** at the next release-cut (handled
  via `/release-cut`, not the implementing PR).
- `from __future__ import annotations` is **kept**, not removed. A 3.12 floor
  does *not* make it obsolete: [PEP 649/749](https://peps.python.org/pep-0649/)
  deferred annotation evaluation lands only in **3.14**, so on 3.12 removing
  the import makes annotations evaluate *eagerly* and would break the modules'
  forward references. Removal is premature until the floor reaches 3.14.

### Neutral

- Revisitable by design: when Python 3.14 ships (deferred annotations) and/or
  the next Ubuntu LTS bumps its bundled Python, this ADR is the place to
  reconsider the floor — both raising it and the `__future__`-import cleanup it
  would unlock.

## Compliance

- **`pyproject.toml`** — `requires-python = ">=3.12"`, `[tool.ruff]
  target-version = "py312"`, `[tool.mypy] python_version = "3.12"`.
- **`.github/workflows/ci.yml`** — the `test` matrix is a single `"3.12"`
  entry; `lockfile-drift` and `build-lockfile-drift` set up Python 3.12.
- **`docs/architecture/02-architecture-constraints.md`** — the "Python 3.12 or
  newer" constraint row links to this ADR. No automated check on the docs;
  breakage is caught at PR review.

## More Information

- Related issues / PRs: [#213](https://github.com/DocGerd/hangarfit/issues/213)
- External references: [PEP 602 — Annual release cycle for Python](https://peps.python.org/pep-0602/)
  (no LTS designation); [Ubuntu 24.04 LTS release notes](https://releases.ubuntu.com/24.04/)
  (ships Python 3.12); [PEP 649](https://peps.python.org/pep-0649/) /
  [PEP 749](https://peps.python.org/pep-0749/) — deferred annotation evaluation
  (Python 3.14), the reason `from __future__ import annotations` stays.
