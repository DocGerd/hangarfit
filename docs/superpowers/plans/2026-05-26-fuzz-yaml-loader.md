# Polyglot Fuzzing for the YAML Loader — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Hypothesis property tests for the YAML loader (real crash-finding value, every PR) plus a thin Atheris bridge harness (`import atheris`) that flips OpenSSF Scorecard's Fuzzing check 0 → 10, with a nightly deep-fuzz workflow.

**Architecture:** One shared strategies module (`tests/fuzz/strategies.py`) generates near-valid YAML documents and encodes the loader contract ("returns a model XOR raises `LoaderError`"). The pytest property suite (`test_loader_fuzz.py`) consumes it via `@given` on every PR (`ci` profile, ~50 examples). The Atheris harness (`atheris_loader_harness.py`) consumes the *same* strategies via `fuzz_one_input` for a nightly coverage-guided run; its `import atheris` line is what Scorecard greps for.

**Tech Stack:** Python 3.12, [Hypothesis](https://hypothesis.readthedocs.io/) (dev dep, pure-Python), [Atheris](https://github.com/google/atheris) 3.0.0 (nightly-only, hash-pinned `requirements-fuzz.txt`), pip-tools 7.5.3 lockfiles, GitHub Actions.

**Design spec:** `docs/superpowers/specs/2026-05-26-fuzz-yaml-loader-design.md`

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/hangarfit/loader.py` | Modify (`_read_yaml`) | Guard `UnicodeDecodeError` → `LoaderError` (found by raw-bytes fuzzing) |
| `tests/test_loader.py` | Modify | Unit test for the new UTF-8 guard |
| `pyproject.toml` | Modify | Add `hypothesis` to `[project.optional-dependencies] dev` |
| `requirements-dev.txt` | Regenerate | Hash-pinned `hypothesis` |
| `.github/workflows/ci.yml` | Modify | Add `hypothesis` to the `lockfile-drift` declared-dep allow-list; add `fuzz-lockfile-drift` job |
| `tests/fuzz/__init__.py` | Create | Package marker (lets `python -m tests.fuzz.…` work) |
| `tests/fuzz/conftest.py` | Create | Hypothesis settings profiles (`ci`/`nightly`/`dev`) |
| `tests/fuzz/strategies.py` | Create | Shared strategies + run-helpers + valid fixtures (the single source of input logic) |
| `tests/fuzz/test_loader_fuzz.py` | Create | `@given` property tests (pytest-collected, runs on PRs) |
| `tests/fuzz/atheris_loader_harness.py` | Create | `import atheris` bridge harness (NOT pytest-collected) |
| `requirements-fuzz.in` | Create | Atheris source pin (constrained by dev lockfile) |
| `requirements-fuzz.txt` | Generate | Hash-pinned Atheris toolchain (nightly-only) |
| `.github/workflows/fuzz.yml` | Create | Nightly deep Hypothesis + time-boxed Atheris |
| `CLAUDE.md` | Modify | Document the `requirements-fuzz.txt` regen command |

**Pinned action SHAs (reuse from existing workflows, do not bump):**
- `actions/checkout` → `de0fac2e4500dabe0009e67214ff5f5447ce83dd` (v6.0.2)
- `actions/setup-python` → `a309ff8b426b58ec0e2a45f0f869d46889d02405` (v6.2.0)

---

## Task 0: Governance — milestone + rewrite #143

**Files:** none (GitHub state only). Branch `feature/143-fuzz-loader` already exists off `develop`.

- [ ] **Step 1: Create the follow-up milestone**

```bash
gh api repos/:owner/:repo/milestones -f title="v0.7.1 — Security follow-ups" \
  -f state=open \
  -f description="Post-v0.7.0 security/supply-chain follow-ups (fuzzing, signed commits)." \
  --jq '.number'
```

Record the printed milestone number as `$MS` for Step 2.

- [ ] **Step 2: Rewrite #143 in place** (corrected premise, drop `later` label, set milestone)

`gh issue edit` is unreliable in this repo — use the REST API. Write the new body to a temp file first:

```bash
cat > /tmp/issue143.md <<'EOF'
## Goal

Move OpenSSF **Scorecard "Fuzzing"** 0 → 10 **and** add real defensive value for
the YAML loader (the in-scope attack surface named in `SECURITY.md`).

## Corrected premise (was wrong in the original issue)

Scorecard's Python fuzzing probe (`checks/raw/fuzzing.go`) greps `*.py` for the
literal regex `import atheris` and **nothing else** — it has no Hypothesis/
property-based probe for Python. A pure-Hypothesis harness leaves Fuzzing at 0.

## Plan (polyglot Hypothesis + Atheris)

- Hypothesis property tests for `load_fleet`/`load_hangar`/`load_layout`/
  `load_scenario` enforcing the strict invariant: **return a model XOR raise
  `LoaderError`** (a bare `KeyError`/`TypeError`/`ValueError`/… escaping = a
  missing-wrap bug to fix). Runs on every PR (`ci` profile).
- A thin Atheris bridge harness reusing the same `@given` strategies via
  `fuzz_one_input`; the `import atheris` line flips the Scorecard check.
- A nightly workflow: deep Hypothesis run (2000 examples) + time-boxed Atheris.
- `atheris` hash-pinned in a standalone `requirements-fuzz.txt` (nightly-only,
  never in `pyproject.toml`); `hypothesis` added to the dev extra.

Full design: `docs/superpowers/specs/2026-05-26-fuzz-yaml-loader-design.md`.

## Acceptance

- [ ] `tests/fuzz/` Hypothesis suite (4 entry points + raw parse-layer guard), green on PRs.
- [ ] `tests/fuzz/atheris_loader_harness.py` contains `import atheris`.
- [ ] `hypothesis` in `requirements-dev.txt`; `atheris` in `requirements-fuzz.txt` (+ drift guard).
- [ ] Nightly `fuzz.yml` passes.
- [ ] After the next weekly Scorecard run, **Fuzzing moves 0 → 10**.
EOF

gh api -X PATCH repos/:owner/:repo/issues/143 \
  -f title="Polyglot fuzzing for the YAML loader (Scorecard: Fuzzing 0 → 10)" \
  -F "body=@/tmp/issue143.md" \
  -F milestone=$MS \
  -f 'labels[]=security' -f 'labels[]=scorecard'
```

(The `labels[]` rewrite drops the `later` label by omission.)

- [ ] **Step 3: Verify**

```bash
gh issue view 143 --json title,milestone,labels --jq '{title,ms:.milestone.title,labels:[.labels[].name]}'
```
Expected: corrected title, milestone `v0.7.1 — Security follow-ups`, labels `["security","scorecard"]` (no `later`).

---

## Task 1: Harden `_read_yaml` against non-UTF-8 input

The raw-bytes fuzz strategy (Task 6) feeds arbitrary bytes; `_read_yaml` opens
with `encoding="utf-8"` but only catches `FileNotFoundError`/`yaml.YAMLError`,
so a non-UTF-8 file currently escapes as a bare `UnicodeDecodeError`. Fix it
TDD-first so the raw-guard property can pass.

**Files:**
- Modify: `src/hangarfit/loader.py` (`_read_yaml`, ~line 678-686)
- Test: `tests/test_loader.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_loader.py`:

```python
def test_load_fleet_rejects_non_utf8_file(tmp_path):
    """A file with invalid UTF-8 bytes must surface as LoaderError, not a
    bare UnicodeDecodeError leaking out of _read_yaml."""
    bad = tmp_path / "fleet.yaml"
    bad.write_bytes(b"\xff\xfe\x00bad bytes not utf-8")
    with pytest.raises(LoaderError, match="UTF-8"):
        load_fleet(bad)
```

(Confirm `LoaderError` and `load_fleet` are already imported at the top of the
test file; both are used elsewhere there.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_loader.py::test_load_fleet_rejects_non_utf8_file -v`
Expected: FAIL — raises `UnicodeDecodeError`, not `LoaderError`.

- [ ] **Step 3: Add the guard**

In `src/hangarfit/loader.py`, change `_read_yaml`:

```python
def _read_yaml(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError as e:
        raise LoaderError(f"file not found: {path}") from e
    except UnicodeDecodeError as e:
        raise LoaderError(f"{path}: file is not valid UTF-8: {e}") from e
    except yaml.YAMLError as e:
        raise LoaderError(f"{path}: YAML parse error: {e}") from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_loader.py::test_load_fleet_rejects_non_utf8_file -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader.py
git commit -m "fix(loader): guard non-UTF-8 files as LoaderError (#143)"
```

---

## Task 2: Add `hypothesis` dev dependency + regenerate lockfile + drift allow-list

**Files:**
- Modify: `pyproject.toml:21`
- Regenerate: `requirements-dev.txt`
- Modify: `.github/workflows/ci.yml` (drift allow-list loop)

- [ ] **Step 1: Add hypothesis to the dev extra**

In `pyproject.toml`, change line 21:

```toml
dev = ["pytest>=7.4", "pytest-cov>=4.0", "ruff>=0.7.0", "mypy>=1.0", "types-PyYAML", "types-shapely", "hypothesis>=6.100"]
```

- [ ] **Step 2: Regenerate the hash-pinned dev lockfile**

Run (Python 3.12, pip-tools 7.5.3 — install via `pip install --require-hashes -r requirements-pip-tools.txt` if not present):

```bash
pip-compile --generate-hashes --no-strip-extras --extra dev -o requirements-dev.txt pyproject.toml
```

Expected: `requirements-dev.txt` now contains `hypothesis==<ver>` plus its
transitives (`attrs`, `sortedcontainers`), all with `--hash=` lines.

- [ ] **Step 3: Add hypothesis to the lockfile-drift declared-dep allow-list**

In `.github/workflows/ci.yml`, find the loop in the `lockfile-drift` job:

```bash
          for pkg in pyyaml shapely matplotlib pytest pytest-cov ruff mypy types-pyyaml types-shapely; do
```

Add `hypothesis`:

```bash
          for pkg in pyyaml shapely matplotlib pytest pytest-cov ruff mypy types-pyyaml types-shapely hypothesis; do
```

- [ ] **Step 4: Install locally and verify import**

Run:
```bash
pip install --require-hashes -r requirements-dev.txt && python -c "import hypothesis; print(hypothesis.__version__)"
```
Expected: prints a version ≥ 6.100.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements-dev.txt .github/workflows/ci.yml
git commit -m "build: add hypothesis dev dep + lockfile + drift guard (#143)"
```

---

## Task 3: Hypothesis profiles + fuzz package marker

**Files:**
- Create: `tests/fuzz/__init__.py`
- Create: `tests/fuzz/conftest.py`

- [ ] **Step 1: Create the package marker**

`tests/fuzz/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Create the profiles conftest**

`tests/fuzz/conftest.py`:

```python
"""Hypothesis settings profiles for the loader fuzz suite.

Selected via the HYPOTHESIS_PROFILE env var (default "ci"):
  ci      — fast, runs on every PR (default `pytest`)
  nightly — deep run for the nightly fuzz workflow
  dev     — local opt-in middle ground

deadline=None: the loader does small-file I/O per example; a per-example
deadline flakes on CI cold starts for no signal.
"""

import os

from hypothesis import HealthCheck, settings

settings.register_profile("dev", max_examples=100, deadline=None)
settings.register_profile("ci", max_examples=50, deadline=None)
settings.register_profile(
    "nightly",
    max_examples=2000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))
```

- [ ] **Step 3: Verify the conftest imports cleanly**

Run: `python -c "import os; os.chdir('tests/fuzz'); import conftest"` — or simply
`pytest tests/fuzz/ -q` (collects nothing yet, exits 0 / "no tests ran").
Expected: no import error.

- [ ] **Step 4: Commit**

```bash
git add tests/fuzz/__init__.py tests/fuzz/conftest.py
git commit -m "test(fuzz): add Hypothesis settings profiles + package (#143)"
```

---

## Task 4: Shared strategies module + valid fixtures + run-helpers

**Files:**
- Create: `tests/fuzz/strategies.py`

This is the single source of input-construction logic. Verified later by the
property tests in Task 5.

- [ ] **Step 1: Write the strategies module**

`tests/fuzz/strategies.py`:

```python
"""Hypothesis strategies + run-helpers for fuzzing the hangarfit YAML loader.

Shared by the pytest property suite (``test_loader_fuzz.py``) and the Atheris
bridge harness (``atheris_loader_harness.py``) so input construction lives in
exactly one place. Each ``run_*`` helper encodes the loader contract: the
loader must either return a model or raise ``LoaderError``; any other
exception propagates and is reported as a fuzz finding.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from hypothesis import strategies as st

from hangarfit import loader
from hangarfit.loader import LoaderError
from hangarfit.models import Aircraft, Door, Hangar, MaintenanceBay, Part

# --- valid in-memory fixtures used as fleet/hangar overrides for layout/scenario.
# Built directly with model constructors so they pass __post_init__; this lets
# the fuzzer concentrate on placement/constraint logic instead of re-fuzzing
# fleet/hangar path resolution.
VALID_HANGAR = Hangar(
    length_m=40.0,
    width_m=20.0,
    door=Door(center_x_m=10.0, width_m=8.0),
    maintenance_bay=MaintenanceBay(center_x_m=10.0, width_m=6.0, depth_m=5.0),
    clearance_m=0.3,
    wing_layer_clearance_m=0.2,
)
_FUSELAGE = Part(
    kind="fuselage",
    length_m=6.0,
    width_m=1.2,
    offset_x_m=0.0,
    offset_y_m=0.0,
    angle_deg=0.0,
    z_bottom_m=0.0,
    z_top_m=2.0,
)
VALID_FLEET: dict[str, Aircraft] = {
    "p1": Aircraft(
        id="p1",
        name="Plane One",
        wing_position="high",
        gear="nosewheel",
        movement_mode="always_cart",
        turn_radius_m=None,
        measured=False,
        parts=(_FUSELAGE,),
    ),
    "p2": Aircraft(
        id="p2",
        name="Plane Two",
        wing_position="low",
        gear="tailwheel",
        movement_mode="always_cart",
        turn_radius_m=None,
        measured=False,
        parts=(_FUSELAGE,),
    ),
}

# --- primitive adversarial strategies ---
# Only UTF-8-encodable characters: generated text is dumped to a YAML file and
# read back with encoding="utf-8"; lone surrogates would crash the writer, not
# the loader, producing false findings.
_safe_text = st.text(st.characters(codec="utf-8"), max_size=20)
_numbers = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(min_value=-1000, max_value=1000),
)
# A scalar that might land where a number / bool / string is expected.
_scalars = st.one_of(st.none(), st.booleans(), _numbers, _safe_text)


def _enum_or_garbage(valid: list[str]) -> st.SearchStrategy[Any]:
    return st.one_of(st.sampled_from(valid), _safe_text, st.none(), st.integers())


# Plane ids: mostly real ids + near-misses so resolution / difflib paths run.
_plane_ids = st.one_of(
    st.sampled_from(["p1", "p2"]),
    st.sampled_from(["P1", "p3", "plane_one", ""]),
    _safe_text,
)


@st.composite
def _maybe_drop_keys(draw: Any, doc_strategy: st.SearchStrategy[Any]) -> Any:
    """Randomly omit a subset of a dict's keys to exercise missing-key guards,
    while usually leaving documents deep enough to reach inner loader logic."""
    doc = draw(doc_strategy)
    if isinstance(doc, dict) and doc and draw(st.booleans()):
        drop = draw(st.sets(st.sampled_from(sorted(doc)), max_size=len(doc)))
        return {k: v for k, v in doc.items() if k not in drop}
    return doc


# --- per-entry-point document strategies ---
def _part_docs() -> st.SearchStrategy[Any]:
    full = st.fixed_dictionaries(
        {
            "kind": _enum_or_garbage(["fuselage", "wing", "strut", "tail"]),
            "length_m": _scalars,
            "width_m": _scalars,
            "z_bottom_m": _scalars,
            "z_top_m": _scalars,
        },
        optional={"offset_x_m": _scalars, "offset_y_m": _scalars, "angle_deg": _scalars},
    )
    return st.one_of(_maybe_drop_keys(full), st.none(), _safe_text, st.integers())


def _struts_docs() -> st.SearchStrategy[Any]:
    full = st.fixed_dictionaries(
        {
            "fuselage_attach_x_m": _scalars,
            "fuselage_attach_y_m": _scalars,
            "fuselage_attach_z_m": _scalars,
            "wing_attach_y_m": _scalars,
            "width_m": _scalars,
        }
    )
    return st.one_of(_maybe_drop_keys(full), st.none(), _safe_text)


def fleet_documents() -> st.SearchStrategy[Any]:
    aircraft = st.fixed_dictionaries(
        {
            "id": st.one_of(_safe_text, st.sampled_from(["p1", "p2"]), st.integers()),
            "name": _safe_text,
            "wing_position": _enum_or_garbage(["high", "mid", "low"]),
            "gear": _enum_or_garbage(["tailwheel", "nosewheel", "monowheel"]),
            "movement_mode": _enum_or_garbage(
                ["always_cart", "always_own_gear", "cart_eligible"]
            ),
            "parts": st.lists(_part_docs(), max_size=4),
        },
        optional={
            "turn_radius_m": _scalars,
            "measured": _scalars,
            "struts": _struts_docs(),
            "notes": _safe_text,
        },
    )
    top = st.fixed_dictionaries({"aircraft": st.lists(_maybe_drop_keys(aircraft), max_size=4)})
    return st.one_of(_maybe_drop_keys(top), st.none(), st.lists(st.integers()), _safe_text)


def hangar_documents() -> st.SearchStrategy[Any]:
    door = st.fixed_dictionaries({"center_x_m": _scalars, "width_m": _scalars})
    bay = st.fixed_dictionaries(
        {"center_x_m": _scalars, "width_m": _scalars, "depth_m": _scalars}
    )
    top = st.fixed_dictionaries(
        {
            "length_m": _scalars,
            "width_m": _scalars,
            "door": st.one_of(_maybe_drop_keys(door), st.none(), _safe_text),
            "maintenance_bay": st.one_of(_maybe_drop_keys(bay), st.none(), _safe_text),
        },
        optional={"clearance_m": _scalars, "wing_layer_clearance_m": _scalars},
    )
    return st.one_of(_maybe_drop_keys(top), st.none(), _safe_text)


def layout_documents() -> st.SearchStrategy[Any]:
    placement = st.fixed_dictionaries(
        {"plane": _plane_ids, "x_m": _scalars, "y_m": _scalars, "heading_deg": _scalars},
        optional={"on_carts": _scalars},
    )
    maintenance = st.one_of(
        st.none(),
        st.builds(lambda p: {"plane": p}, _plane_ids),
        _safe_text,
    )
    top = st.fixed_dictionaries(
        {"placements": st.lists(_maybe_drop_keys(placement), max_size=4)},
        optional={"maintenance": maintenance},
    )
    return st.one_of(_maybe_drop_keys(top), st.none(), _safe_text)


def scenario_documents() -> st.SearchStrategy[Any]:
    pin = st.fixed_dictionaries(
        {"x_m": _scalars, "y_m": _scalars, "heading_deg": _scalars, "on_carts": _scalars}
    )
    constraint = st.fixed_dictionaries(
        {},
        optional={"pin": st.one_of(_maybe_drop_keys(pin), st.none()), "force_on_carts": _scalars},
    )
    top = st.fixed_dictionaries(
        {"fleet_in": st.lists(_plane_ids, max_size=4)},
        optional={
            "maintenance": st.one_of(st.none(), st.builds(lambda p: {"plane": p}, _plane_ids)),
            "constraints": st.dictionaries(_plane_ids, constraint, max_size=3),
        },
    )
    return st.one_of(_maybe_drop_keys(top), st.none(), _safe_text)


def raw_documents() -> st.SearchStrategy[Any]:
    """Raw parse-layer inputs: arbitrary text/bytes fed straight to a loader,
    exercising _read_yaml and the top-level-shape guards. st.binary() covers
    invalid-UTF-8 (guarded in Task 1)."""
    return st.one_of(st.text(max_size=200), st.binary(max_size=200))


# --- run helpers: each encodes the loader contract (LoaderError is acceptable;
# anything else propagates as a finding) ---
def _write_yaml_tmp(doc: Any) -> Path:
    fd, name = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    p = Path(name)
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return p


def _write_raw_tmp(doc: Any) -> Path:
    fd, name = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    p = Path(name)
    if isinstance(doc, bytes):
        p.write_bytes(doc)
    else:
        p.write_text(str(doc), encoding="utf-8")
    return p


def run_fleet(doc: Any) -> None:
    p = _write_yaml_tmp(doc)
    try:
        loader.load_fleet(p)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


def run_hangar(doc: Any) -> None:
    p = _write_yaml_tmp(doc)
    try:
        loader.load_hangar(p)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


def run_layout(doc: Any) -> None:
    p = _write_yaml_tmp(doc)
    try:
        loader.load_layout(p, fleet=dict(VALID_FLEET), hangar=VALID_HANGAR)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


def run_scenario(doc: Any) -> None:
    p = _write_yaml_tmp(doc)
    try:
        loader.load_scenario(p, fleet=dict(VALID_FLEET), hangar=VALID_HANGAR)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


def run_raw(doc: Any) -> None:
    p = _write_raw_tmp(doc)
    try:
        loader.load_fleet(p)
    except LoaderError:
        pass
    finally:
        p.unlink(missing_ok=True)


# --- tagged union for the Atheris single-target harness ---
_RUNNERS = {
    "fleet": run_fleet,
    "hangar": run_hangar,
    "layout": run_layout,
    "scenario": run_scenario,
    "raw": run_raw,
}


def tagged_documents() -> st.SearchStrategy[tuple[str, Any]]:
    return st.one_of(
        st.tuples(st.just("fleet"), fleet_documents()),
        st.tuples(st.just("hangar"), hangar_documents()),
        st.tuples(st.just("layout"), layout_documents()),
        st.tuples(st.just("scenario"), scenario_documents()),
        st.tuples(st.just("raw"), raw_documents()),
    )


def run_tagged(tagged: tuple[str, Any]) -> None:
    tag, doc = tagged
    _RUNNERS[tag](doc)
```

- [ ] **Step 2: Verify it imports and the fixtures are valid**

Run:
```bash
python -c "from tests.fuzz import strategies as s; print(sorted(s.VALID_FLEET), s.VALID_HANGAR.length_m)"
```
Expected: `['p1', 'p2'] 40.0` (no `ValueError` from model `__post_init__`).

- [ ] **Step 3: Lint/format the new file**

Run: `ruff check tests/fuzz/strategies.py && ruff format --check tests/fuzz/strategies.py`
Expected: passes. If format fails, run `ruff format tests/fuzz/strategies.py`.

- [ ] **Step 4: Commit**

```bash
git add tests/fuzz/strategies.py
git commit -m "test(fuzz): shared loader fuzz strategies + run-helpers (#143)"
```

---

## Task 5: Property tests for all four entry points + raw guard

**Files:**
- Create: `tests/fuzz/test_loader_fuzz.py`

- [ ] **Step 1: Write the property tests**

`tests/fuzz/test_loader_fuzz.py`:

```python
"""Property tests for the YAML loader.

Invariant under test for every entry point: given any near-valid (or raw)
document, the loader must either return a model or raise ``LoaderError`` — never
a bare KeyError/AttributeError/IndexError/TypeError/ValueError/RecursionError.
The run-helpers in ``strategies`` swallow only ``LoaderError``; any other
exception propagates here and Hypothesis shrinks it to a minimal repro.

Runs under the ``ci`` profile by default (fast, every PR); the nightly workflow
sets HYPOTHESIS_PROFILE=nightly for a deep run.
"""

from __future__ import annotations

from hypothesis import given

from tests.fuzz import strategies as s


@given(s.fleet_documents())
def test_load_fleet_never_crashes(doc):
    s.run_fleet(doc)


@given(s.hangar_documents())
def test_load_hangar_never_crashes(doc):
    s.run_hangar(doc)


@given(s.layout_documents())
def test_load_layout_never_crashes(doc):
    s.run_layout(doc)


@given(s.scenario_documents())
def test_load_scenario_never_crashes(doc):
    s.run_scenario(doc)


@given(s.raw_documents())
def test_load_raw_input_never_crashes(doc):
    s.run_raw(doc)
```

- [ ] **Step 2: Run the suite under the ci profile**

Run: `pytest tests/fuzz/test_loader_fuzz.py -v`
Expected: 5 tests PASS. If any FAILS, Hypothesis prints a minimal failing
example — that is a genuine loader finding: add the missing `LoaderError` wrap
in `src/hangarfit/loader.py` (do **not** loosen the test), then re-run.

- [ ] **Step 3: Run a deeper pass locally to shake out rare paths**

Run: `HYPOTHESIS_PROFILE=nightly pytest tests/fuzz/test_loader_fuzz.py -q`
Expected: PASS (slower). Fix any finding as in Step 2.

- [ ] **Step 4: Confirm the full default suite still passes (fuzz runs by default)**

Run: `pytest -q`
Expected: all tests pass; the fuzz tests run under the `ci` profile as part of
the normal suite (they are not `slow`-marked).

- [ ] **Step 5: Lint/format**

Run: `ruff check tests/fuzz/ && ruff format --check tests/fuzz/`
Expected: passes (run `ruff format tests/fuzz/` if needed).

- [ ] **Step 6: Commit**

```bash
git add tests/fuzz/test_loader_fuzz.py
git commit -m "test(fuzz): Hypothesis property suite for loader entry points (#143)"
```

---

## Task 6: Atheris bridge harness

**Files:**
- Create: `tests/fuzz/atheris_loader_harness.py`

This file is named `atheris_*` (not `test_*`) so pytest never collects it and
PR CI never needs atheris installed. It is the only file containing
`import atheris`.

- [ ] **Step 1: Write the harness**

`tests/fuzz/atheris_loader_harness.py`:

```python
"""Atheris bridge harness for the YAML loader.

The ``import atheris`` below is exactly what OpenSSF Scorecard's Python fuzzing
probe greps for (scorecard checks/raw/fuzzing.go: filePatterns ``["*.py"]``,
funcPattern ``import atheris``) — its presence is what flips the Fuzzing check
0 -> 10. Running it (nightly) drives libFuzzer through the SAME Hypothesis
strategies the pytest property suite uses, via ``fuzz_one_input``.

Run (from repo root, with atheris installed):
    python -m tests.fuzz.atheris_loader_harness -max_total_time=300
"""

import sys

import atheris

with atheris.instrument_imports():
    from hypothesis import given

    from tests.fuzz import strategies


@given(strategies.tagged_documents())
def _fuzz_loader(tagged):
    strategies.run_tagged(tagged)


if __name__ == "__main__":
    atheris.Setup(sys.argv, _fuzz_loader.hypothesis.fuzz_one_input)
    atheris.Fuzz()
```

- [ ] **Step 2: Confirm pytest does NOT collect it**

Run: `pytest tests/fuzz/ --collect-only -q`
Expected: lists only the 5 `test_loader_fuzz.py` tests; `atheris_loader_harness`
does not appear (and no `ModuleNotFoundError: atheris` at collection).

- [ ] **Step 3: Confirm the Scorecard grep target is present**

Run: `grep -rn "import atheris" tests/fuzz/atheris_loader_harness.py`
Expected: one match on line 17.

- [ ] **Step 4: Smoke-test the harness end-to-end (optional locally, definitive in CI)**

Atheris ships a `cp312` Linux wheel, so this works in this WSL/Linux dev env:

```bash
pip install atheris
python -m tests.fuzz.atheris_loader_harness -atheris_runs=2000 -max_total_time=60
```
Expected: libFuzzer banner, runs to the run/time cap, exits 0 with no uncaught
exception ("Done N runs"). A crash means a real loader finding — fix the wrap
in `loader.py`. (Skip locally if atheris install is undesirable; the nightly
workflow in Task 9 is the authoritative run.)

- [ ] **Step 5: Lint/format**

Run: `ruff check tests/fuzz/atheris_loader_harness.py && ruff format --check tests/fuzz/atheris_loader_harness.py`
Expected: passes. (`import atheris` does not error — ruff does not resolve
imports, and atheris is used by `Setup`/`Fuzz`.)

- [ ] **Step 6: Commit**

```bash
git add tests/fuzz/atheris_loader_harness.py
git commit -m "test(fuzz): Atheris bridge harness (Scorecard Fuzzing detection) (#143)"
```

---

## Task 7: `requirements-fuzz` lockfile + CLAUDE.md regen doc

**Files:**
- Create: `requirements-fuzz.in`
- Generate: `requirements-fuzz.txt`
- Modify: `CLAUDE.md` (Useful commands)

- [ ] **Step 1: Write the source `.in`**

`requirements-fuzz.in`:

```
# Fuzzing-only toolchain (Atheris). Installed SOLELY by the nightly fuzz
# workflow (.github/workflows/fuzz.yml) — deliberately NOT in pyproject.toml so
# routine `pip install -e .[dev]` never pulls a native libFuzzer wheel.
# Hypothesis itself lives in the dev extra (it runs on every PR); only Atheris
# is here. Shared transitives are constrained to the dev lockfile via `-c` so
# the nightly job can install requirements-dev.txt and requirements-fuzz.txt
# together without version skew (mirrors requirements-build.in).
-c requirements-dev.txt
atheris>=3.0.0
```

- [ ] **Step 2: Generate the hash-pinned lockfile**

Run (Python 3.12, pip-tools 7.5.3):

```bash
pip-compile --generate-hashes --no-strip-extras -o requirements-fuzz.txt requirements-fuzz.in
```

Expected: `requirements-fuzz.txt` contains `atheris==3.0.0` with `--hash=` lines.

- [ ] **Step 3: Verify a clean hash-pinned install works**

Run (ideally in a throwaway venv):

```bash
pip install --require-hashes -r requirements-fuzz.txt && python -c "import atheris; print('atheris ok')"
```
Expected: `atheris ok` (the `cp312` wheel installs without clang).

- [ ] **Step 4: Document the regen command in CLAUDE.md**

In `CLAUDE.md`, after the build-toolchain lockfile regen block in "Useful
commands", add:

````markdown
# Regenerate the hash-pinned FUZZING-toolchain lockfile. Source is
# `requirements-fuzz.in` (Atheris only — Hypothesis lives in the dev extra).
# Atheris is installed solely by the nightly fuzz workflow, never by
# `pip install -e .[dev]`, so it is kept out of pyproject.toml. The `.in`
# constrains shared transitives via `-c requirements-dev.txt` so the nightly
# job can install the dev and fuzz lockfiles together without skew. The
# `fuzz-lockfile-drift` CI job enforces this on every PR. Same toolchain as
# the other lockfiles: pip-tools 7.5.3 on Python 3.12.
pip-compile --generate-hashes --no-strip-extras -o requirements-fuzz.txt requirements-fuzz.in
````

- [ ] **Step 5: Commit**

```bash
git add requirements-fuzz.in requirements-fuzz.txt CLAUDE.md
git commit -m "build: hash-pinned requirements-fuzz.txt (Atheris) + regen doc (#143)"
```

---

## Task 8: `fuzz-lockfile-drift` CI job

**Files:**
- Modify: `.github/workflows/ci.yml` (append a third drift job)

- [ ] **Step 1: Append the drift-guard job**

In `.github/workflows/ci.yml`, after the `build-lockfile-drift` job, add (match
existing indentation — top-level job under `jobs:`):

```yaml
  fuzz-lockfile-drift:
    # Sibling of build-lockfile-drift, for requirements-fuzz.txt (the
    # hash-pinned Atheris toolchain consumed only by the nightly fuzz
    # workflow). Source is requirements-fuzz.in, which constrains shared
    # transitives to requirements-dev.txt via `-c`. Guards two silent-drift
    # paths: (1) requirements-fuzz.in edited but the lockfile not regenerated;
    # (2) a constrained transitive bumped in requirements-dev.txt without
    # regenerating here.
    name: fuzz lockfile drift check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2

      - name: Set up Python 3.12
        uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405  # v6.2.0
        with:
          python-version: "3.12"

      - name: Install pip-tools (hash-pinned)
        run: pip install --require-hashes -r requirements-pip-tools.txt

      - name: Regenerate fuzz lockfile against existing pins
        run: |
          cp requirements-fuzz.txt /tmp/expected-fuzz.txt
          pip-compile --quiet --generate-hashes --no-strip-extras -o /tmp/expected-fuzz.txt requirements-fuzz.in

      - name: Compare package==version sets
        run: |
          extract() {
            { grep -E '^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?==' "$1" || true; } \
              | sed 's/ *\\$//' | sort
          }
          extract requirements-fuzz.txt > /tmp/committed-fuzz.set
          extract /tmp/expected-fuzz.txt > /tmp/regenerated-fuzz.set
          if [ ! -s /tmp/committed-fuzz.set ] || [ ! -s /tmp/regenerated-fuzz.set ]; then
            echo "::error::extract() produced an empty package set — the regex in .github/workflows/ci.yml no longer matches the lockfile format. Update the guard."
            exit 1
          fi
          for pkg in atheris; do
            grep -qE "^${pkg}==" /tmp/regenerated-fuzz.set || {
              echo "::error::fuzz lockfile regeneration dropped a declared dependency: ${pkg} — pip-compile may have produced partial output."
              exit 1
            }
          done
          if ! diff -u /tmp/committed-fuzz.set /tmp/regenerated-fuzz.set; then
            echo "::error::requirements-fuzz.txt is out of sync. Regenerate on Python 3.12 with pip-tools==7.5.3: pip-compile --generate-hashes --no-strip-extras -o requirements-fuzz.txt requirements-fuzz.in"
            exit 1
          fi
```

- [ ] **Step 2: Validate workflow YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK`
Expected: `OK`.

- [ ] **Step 3: Dry-run the drift logic locally (should be a no-op)**

Run:
```bash
cp requirements-fuzz.txt /tmp/expected-fuzz.txt
pip-compile --quiet --generate-hashes --no-strip-extras -o /tmp/expected-fuzz.txt requirements-fuzz.in
diff <(grep -E '^atheris==' requirements-fuzz.txt) <(grep -E '^atheris==' /tmp/expected-fuzz.txt) && echo "no drift"
```
Expected: `no drift`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add fuzz-lockfile-drift guard for requirements-fuzz.txt (#143)"
```

---

## Task 9: Nightly fuzz workflow

**Files:**
- Create: `.github/workflows/fuzz.yml`

- [ ] **Step 1: Write the workflow**

`.github/workflows/fuzz.yml`:

```yaml
name: Nightly fuzz

on:
  schedule:
    - cron: '0 4 * * *'   # 04:00 UTC daily
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}
  cancel-in-progress: false

jobs:
  fuzz:
    name: Fuzz YAML loader
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd  # v6.0.2

      - name: Set up Python 3.12
        uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405  # v6.2.0
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: |
            pyproject.toml
            requirements-dev.txt
            requirements-build.txt
            requirements-fuzz.txt

      - name: Install dev deps (hash-pinned)
        run: pip install --require-hashes -r requirements-dev.txt

      - name: Install build toolchain (hash-pinned)
        run: pip install --require-hashes -r requirements-build.txt

      - name: Install fuzz toolchain (hash-pinned)
        run: pip install --require-hashes -r requirements-fuzz.txt

      - name: Install project (editable, no deps)
        run: pip install -e . --no-deps --no-build-isolation

      - name: Hypothesis deep run
        run: HYPOTHESIS_PROFILE=nightly pytest tests/fuzz/ -q

      - name: Atheris coverage-guided run (time-boxed)
        run: python -m tests.fuzz.atheris_loader_harness -max_total_time=300
```

- [ ] **Step 2: Validate workflow YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/fuzz.yml'))" && echo OK`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/fuzz.yml
git commit -m "ci: nightly fuzz workflow (deep Hypothesis + time-boxed Atheris) (#143)"
```

---

## Task 10: Full verification + push + PR + review

**Files:** none (verification + GitHub).

- [ ] **Step 1: Full local gate (mirror CI)**

Run:
```bash
ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit && pytest -q
```
Expected: all green. (`mypy` only checks `src/hangarfit`; the only src change is
the `_read_yaml` UTF-8 guard.)

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feature/143-fuzz-loader
```

- [ ] **Step 3: Open the PR** (base `develop`, `Closes #143`)

```bash
gh pr create --base develop \
  --title "Polyglot fuzzing for the YAML loader (Scorecard Fuzzing 0→10) (#143)" \
  --body "$(cat <<'EOF'
Closes #143.

Adds Hypothesis property tests for the loader (value, every PR) + a thin Atheris
bridge harness (`import atheris`) that flips OpenSSF Scorecard's Fuzzing check
0 → 10, plus a nightly deep-fuzz workflow.

- Strict invariant: every loader entry point returns a model XOR raises
  `LoaderError`; the suite already flushed out one unguarded path
  (`UnicodeDecodeError` on non-UTF-8 files), now fixed in `_read_yaml`.
- `hypothesis` added to the dev extra (PR CI); `atheris` hash-pinned in a
  standalone `requirements-fuzz.txt` (nightly-only) + new `fuzz-lockfile-drift`
  guard.

Design: docs/superpowers/specs/2026-05-26-fuzz-yaml-loader-design.md
Plan:   docs/superpowers/plans/2026-05-26-fuzz-yaml-loader.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" --milestone "v0.7.1 — Security follow-ups"
```

- [ ] **Step 4: Set PR metadata** (assignee + labels via REST API — `gh pr edit` is broken in this repo)

```bash
PR=$(gh pr view --json number --jq .number)
gh api -X PATCH repos/:owner/:repo/issues/$PR \
  -f 'assignees[]=DocGerd' -f 'labels[]=security' -f 'labels[]=scorecard'
```

- [ ] **Step 5: Run the PR review**

Invoke `/pr-review` (or the `pr-review-toolkit:review-pr` skill). Given the
files touched, expect these specialist agents to be relevant:
- `pr-review-toolkit:silent-failure-hunter` (loader change + the `except
  LoaderError: pass` pattern in run-helpers — confirm it is not masking real
  findings).
- `pr-review-toolkit:code-reviewer` (main pass).
- `geometry-invariant-guard` is **not** needed (no geometry/collision change).

Convert each finding into a review thread on the diff, fix or reply with
rationale, then resolve every thread (per CLAUDE.md workflow).

- [ ] **Step 6: Confirm CI is green on the PR**

```bash
gh pr checks --watch
```
Expected: `test (Python 3.12)`, `lockfile drift check`, `build lockfile drift
check`, and the new `fuzz lockfile drift check` all pass.

- [ ] **Step 7: Hand back to the user**

Tell the user the PR is clean and ready for final review/merge. **Do not merge.**
Note that the Fuzzing score flips only after the next weekly Scorecard cron
(Mon 06:00 UTC) following merge to `develop`.

---

## Self-Review (completed during planning)

- **Spec coverage:** every spec section maps to a task — invariant (§4 → T5),
  strategies structured+raw (§5 → T4/T5), profiles (§6 → T3), file layout (§7 →
  T3/T4/T5/T6), deps incl. drift guard (§8 → T2/T7/T8), nightly workflow (§9 →
  T9), governance/issue/milestone (§10 → T0), acceptance (§11 → T5/T6/T7/T9/T10).
- **Bonus task vs spec:** Task 1 (UTF-8 guard) is not in the spec — it is a
  loader finding the spec's strict invariant predicts, so it is implemented as a
  prerequisite of the raw-guard property. Worth calling out at PR time.
- **Type/name consistency:** `VALID_FLEET`/`VALID_HANGAR`, `run_fleet`/
  `run_hangar`/`run_layout`/`run_scenario`/`run_raw`/`run_tagged`,
  `tagged_documents`, and the `_RUNNERS` keys (`fleet|hangar|layout|scenario|
  raw`) are consistent across strategies.py, test_loader_fuzz.py, and the
  harness.
- **Placeholder scan:** no TBD/TODO; every code step is complete.
```
