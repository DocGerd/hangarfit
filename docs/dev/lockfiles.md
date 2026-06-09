# Hash-pinned lockfile regeneration

The repo carries four hash-pinned `requirements-*.txt` lockfiles, each enforced
by a `*-lockfile-drift` CI job on every PR. Extracted from `CLAUDE.md` (#567); the
commands and rationale are unchanged.

**You rarely need to run these by hand.** The `dev`/`build`/`fuzz` drift jobs
**print the exact `pip-compile` command** on drift, so copy it from the failing
job log. `pip-tools`' own rationale lives in its `.in` header. Note the **dev**
lockfile has **no `.in`** — it is generated directly from `pyproject.toml`.

All four use the **same toolchain**: `pip-tools 7.5.3` on **Python 3.12** (the
single supported interpreter, and the one the lockfiles are resolved against).
`--no-strip-extras` is explicit on each so a future pip-tools 8.0 default flip
cannot silently prune transitive extras.

---

## 1. Dev-deps lockfile (`requirements-dev.txt`)

```bash
pip-compile --generate-hashes --no-strip-extras --extra dev -o requirements-dev.txt pyproject.toml
```

Required after editing **either** `[project] dependencies` **or**
`[project.optional-dependencies] dev` in `pyproject.toml` — the lockfile is
generated with `--extra dev`, which covers **both** groups, so both must stay in
sync. CI's `pip install -e . --no-deps` will **silently skip** a runtime dep
that's in `pyproject.toml` but missing from the lockfile (the `ImportError`
surfaces only at test-collection time). The `lockfile-drift` CI job
(`.github/workflows/ci.yml`) enforces this on every PR by regenerating against the
committed `pyproject.toml` and comparing the resolved `package==version` set.

## 2. Build-toolchain lockfile (`requirements-build.txt`)

```bash
pip-compile --generate-hashes --no-strip-extras --allow-unsafe -o requirements-build.txt requirements-build.in
```

Source is `requirements-build.in` (build + setuptools + wheel). Required after
bumping any of those or after `packaging` moves in `requirements-dev.txt` (the
`.in` constrains shared transitive deps via `-c requirements-dev.txt` so the two
lockfiles can be installed together in CI without skew). `--allow-unsafe` is
**required** — pip-tools classifies setuptools/wheel as "unsafe to pin" and
comments them out by default, which would defeat the `--no-build-isolation`
install in `ci.yml`. The `build-lockfile-drift` CI job enforces this.

## 3. Fuzzing-toolchain lockfile (`requirements-fuzz.txt`)

```bash
pip-compile --generate-hashes --no-strip-extras -o requirements-fuzz.txt requirements-fuzz.in
```

Source is `requirements-fuzz.in` (Atheris only — Hypothesis lives in the dev
extra). Atheris is installed solely by the nightly fuzz workflow, never by
`pip install -e .[dev]`, so it is kept out of `pyproject.toml`. The `.in`
constrains shared transitives via `-c requirements-dev.txt` so the nightly job can
install the dev and fuzz lockfiles together without skew. The `fuzz-lockfile-drift`
CI job enforces this.

## 4. pip-tools bootstrap lockfile (`requirements-pip-tools.txt`)

```bash
pip-compile --generate-hashes --no-strip-extras --allow-unsafe -o requirements-pip-tools.txt requirements-pip-tools.in
```

Source is `requirements-pip-tools.in` (a single `pip-tools==7.5.3` pin). This is
the toolchain the two lockfile-drift guard jobs install to regenerate the dev +
build lockfiles above — hash-pinning it closes the residual `pipCommand not pinned
by hash` Scorecard finding on the bare `pip install pip-tools==7.5.3` the guards
used to run (#224). Required after bumping the pip-tools pin (do that **here and
in the `.in`**, in lockstep with the version named in the regeneration commands
above). `--allow-unsafe` is **required** — pip-tools depends on pip + setuptools,
which pip-tools comments out by default; `--require-hashes` is all-or-nothing, so
an un-pinned transitive dep would make the guard-job install fail.
