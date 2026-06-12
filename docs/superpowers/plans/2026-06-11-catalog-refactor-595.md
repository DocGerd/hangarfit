# Per-Object Catalog + `type:` Discriminator (#595) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the monolithic inline-aircraft fleet YAML into a per-object **catalog** (each object its own file, carrying a `type:` discriminator) referenced **by path** from thin fleet manifests, with one central real catalog and per-fleet operational-flag overrides.

**Architecture:** Expand-migrate-contract. (1) Make `load_fleet` accept *both* legacy inline aircraft *and* catalog references (byte-identical, green). (2) Migrate the data, test helpers, and fuzz to references (green). (3) Drop inline support (green). (4) Collapse the two transient catalogs into one central **real** catalog and re-author the geometric fixtures that the real numbers move (closes #594). (5) Docs. Determinism (ADR-0003) is preserved by manifest **list-order** insertion; the byte-identity canary is the oracle through step 3 and re-baselines in step 4.

**Tech Stack:** Python 3.12, PyYAML, pytest, Hypothesis (fuzz). Loader in `src/hangarfit/loader.py`. No new deps.

**Spec:** `docs/superpowers/specs/2026-06-11-catalog-refactor-595-design.md`.

---

## File Structure

**Created:**
- `data/catalog/<id>.yaml` × 10 — the central catalog (real static defs; `fuji`/`cessna_150` stay synthetic).
- `data/catalog/README.md` — the parts-model conventions header (moved out of `data/fleet.yaml`).
- `examples/herrenteich/catalog/<id>.yaml` × 8 — **transient** (created in Commit 2, deleted in Commit 5).
- `tests/fixtures/catalog/taper_glider.yaml` — test-only catalog entry.
- `tests/test_loader_catalog.py` — the dispatch-seam test suite.

**Modified:**
- `src/hangarfit/loader.py` — `load_fleet` (`:143-172`) + new `_build_catalog_object`, `_parse_manifest_entry`, `_OBJECT_BUILDERS`, `_ALLOWED_MANIFEST_OVERRIDE_KEYS`.
- `data/fleet.yaml`, `examples/herrenteich/fleet.yaml`, `tests/fixtures/fleet_taper.yaml` — become thin manifests.
- `tests/test_loader.py` — helper rework (`_fleet_yaml`→`_write_fleet`) + ~4 pinned error tests.
- `tests/fuzz/strategies.py` — `_well_formed_fleet_doc`, `fleet_documents`, `_write_fleet_yaml`.
- ~15–20 fixture YAMLs under `tests/fixtures/` (Commit 5, discovery-driven).
- Docs: `CLAUDE.md`, `docs/architecture/05-building-block-view.md`, `CHANGELOG.md`, two superpowers plan snippets.

---

## COMMIT 1 — Expand: `load_fleet` accepts inline AND references (byte-identical)

### Task 1: Catalog dispatch + manifest-entry parsing + flag override

**Files:**
- Modify: `src/hangarfit/loader.py` (imports near `:34-59`; new helpers + `load_fleet` rewrite at `:143-172`)
- Test: `tests/test_loader_catalog.py` (create)

- [ ] **Step 1: Write the failing tests** — create `tests/test_loader_catalog.py`:

```python
"""Catalog dispatch + manifest-reference loader behaviour (#595)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from hangarfit.loader import LoaderError, load_fleet

REPO_ROOT = Path(__file__).resolve().parent.parent


def _aircraft_doc(aid: str = "p1", **overrides: Any) -> dict[str, Any]:
    """A minimal valid aircraft catalog dict (no `type` → defaults to aircraft)."""
    doc: dict[str, Any] = {
        "id": aid,
        "name": f"Plane {aid}",
        "wing_position": "high",
        "gear": "nosewheel",
        "movement_mode": "always_cart",
        "measured": False,
        "parts": [
            {"kind": "fuselage", "length_m": 6.0, "width_m": 1.2,
             "z_bottom_m": 0.0, "z_top_m": 2.0},
        ],
    }
    doc.update(overrides)
    return doc


def _write(path: Path, obj: Any) -> Path:
    path.write_text(yaml.safe_dump(obj, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _catalog(tmp_path: Path, aid: str, **overrides: Any) -> str:
    """Write a catalog file; return its manifest-relative ref ('catalog/<aid>.yaml')."""
    cat = tmp_path / "catalog"
    cat.mkdir(exist_ok=True)
    _write(cat / f"{aid}.yaml", {"type": "aircraft", **_aircraft_doc(aid, **overrides)})
    return f"catalog/{aid}.yaml"


def test_string_ref_builds_aircraft(tmp_path: Path) -> None:
    ref = _catalog(tmp_path, "p1")
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": [ref]})
    fleet = load_fleet(manifest)
    assert set(fleet) == {"p1"}
    assert fleet["p1"].movement_mode == "always_cart"


def test_type_omitted_defaults_to_aircraft(tmp_path: Path) -> None:
    cat = tmp_path / "catalog"
    cat.mkdir()
    _write(cat / "p1.yaml", _aircraft_doc("p1"))  # NO `type:` key
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/p1.yaml"]})
    assert set(load_fleet(manifest)) == {"p1"}


def test_unknown_type_is_stage_a_error(tmp_path: Path) -> None:
    cat = tmp_path / "catalog"
    cat.mkdir()
    _write(cat / "trailer.yaml", {"type": "ground_object", "id": "t1"})
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/trailer.yaml"]})
    with pytest.raises(LoaderError, match=r"not yet supported.*Stage A"):
        load_fleet(manifest)


def test_type_key_does_not_trip_aircraft_allowlist(tmp_path: Path) -> None:
    # `type: aircraft` must be stripped before _build_aircraft sees the dict.
    ref = _catalog(tmp_path, "p1")  # writes type: aircraft
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": [ref]})
    load_fleet(manifest)  # must NOT raise "unknown aircraft key(s) ['type']"


def test_flag_override_applies(tmp_path: Path) -> None:
    _catalog(tmp_path, "p1")  # catalog default movement_mode = always_cart
    manifest = _write(
        tmp_path / "fleet.yaml",
        {"aircraft": [{"ref": "catalog/p1.yaml", "movement_mode": "cart_eligible"}]},
    )
    fleet = load_fleet(manifest)
    assert fleet["p1"].movement_mode == "cart_eligible"


def test_geometry_override_rejected(tmp_path: Path) -> None:
    _catalog(tmp_path, "p1")
    manifest = _write(
        tmp_path / "fleet.yaml",
        {"aircraft": [{"ref": "catalog/p1.yaml", "parts": []}]},
    )
    with pytest.raises(LoaderError, match="not allowed"):
        load_fleet(manifest)


def test_missing_ref_file_errors(tmp_path: Path) -> None:
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/nope.yaml"]})
    with pytest.raises(LoaderError, match="does not exist"):
        load_fleet(manifest)


def test_duplicate_id_across_refs(tmp_path: Path) -> None:
    cat = tmp_path / "catalog"
    cat.mkdir()
    _write(cat / "a.yaml", {"type": "aircraft", **_aircraft_doc("dup")})
    _write(cat / "b.yaml", {"type": "aircraft", **_aircraft_doc("dup")})
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": ["catalog/a.yaml", "catalog/b.yaml"]})
    with pytest.raises(LoaderError, match="duplicate aircraft id 'dup'"):
        load_fleet(manifest)


def test_manifest_order_preserved(tmp_path: Path) -> None:
    refs = [_catalog(tmp_path, aid) for aid in ("c", "a", "b")]
    manifest = _write(tmp_path / "fleet.yaml", {"aircraft": refs})
    assert list(load_fleet(manifest)) == ["c", "a", "b"]


def test_data_fleet_loads_after_migration() -> None:
    # The shipped manifest still resolves to the same 9 ids (guards the migration).
    fleet = load_fleet(REPO_ROOT / "data" / "fleet.yaml")
    assert "scheibe_falke" in fleet and "fk9_mkii" in fleet
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_loader_catalog.py -x -q`
Expected: FAIL — `load_fleet` does not yet handle string refs / `ref` mappings / `type` stripping (e.g. "unknown aircraft key(s) ['type']" or building a string as an aircraft).

- [ ] **Step 3: Implement the loader changes**

Add near the other allowlists (after `_reject_unknown_top_level_keys`, before `load_fleet`):

```python
# A fleet manifest references per-object CATALOG files (#595). Each catalog file
# carries a `type:` discriminator (default 'aircraft') routing to a per-type
# builder. Only 'aircraft' is registered here; non-aircraft objects (fuel
# trailer, glider trailer, rescue vehicle) arrive in Stage A (#600).
_DEFAULT_OBJECT_TYPE = "aircraft"

# Operational flags a fleet manifest entry may override on a catalog object.
# Geometry is STATIC — never override-able (edit the catalog file). Keep tight.
_ALLOWED_MANIFEST_OVERRIDE_KEYS = frozenset({"movement_mode", "tow_pivotable"})


def _build_catalog_object(raw: Any, *, source: Path) -> Aircraft:
    """Dispatch a catalog object on its `type:` discriminator to the per-type
    builder. `type:` is stripped before the builder runs, so the aircraft
    allowlist (:data:`_ALLOWED_AIRCRAFT_KEYS`, which has no 'type' member) is
    unchanged. An unregistered type is reserved for Stage A (#600)."""
    if not isinstance(raw, dict):
        raise LoaderError(
            f"{source}: catalog object must be a mapping, got {type(raw).__name__}"
        )
    obj_type = raw.get("type", _DEFAULT_OBJECT_TYPE)
    if obj_type != _DEFAULT_OBJECT_TYPE:
        raise LoaderError(
            f"{source}: object type {obj_type!r} not yet supported (non-aircraft "
            f"objects arrive in Stage A, #600); known types: ['aircraft']"
        )
    entry = {k: v for k, v in raw.items() if k != "type"}
    return _build_aircraft(entry)


def _parse_manifest_entry(entry: Any, *, index: int, path: Path) -> tuple[str, dict[str, Any]]:
    """Normalise a fleet-manifest entry to (ref_path, overrides).

    - ``"catalog/x.yaml"``            -> (path, {})            bare reference
    - ``{ref: p, movement_mode: …}``  -> (p, {allowed flags})  reference + overrides
    - ``{id|name|parts|…}`` (no ref)  -> rejected: the dropped inline-aircraft form
    """
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, dict):
        if "ref" not in entry:
            raise LoaderError(
                f"{path}: aircraft[{index}] is an inline aircraft mapping, which is no "
                f"longer supported (#595). Move the aircraft to a catalog file "
                f"(e.g. data/catalog/<id>.yaml with `type: aircraft`) and reference it: "
                f"`- catalog/<id>.yaml` (or `- {{ref: catalog/<id>.yaml, movement_mode: …}}` "
                f"to override a per-fleet flag)."
            )
        ref = entry["ref"]
        if not isinstance(ref, str):
            raise LoaderError(
                f"{path}: aircraft[{index}].ref must be a path string, got {type(ref).__name__}"
            )
        overrides = {k: v for k, v in entry.items() if k != "ref"}
        unknown = set(overrides) - _ALLOWED_MANIFEST_OVERRIDE_KEYS
        if unknown:
            raise LoaderError(
                f"{path}: aircraft[{index}] override key(s) {sorted(unknown)} not allowed; "
                f"only per-fleet operational flags may be overridden "
                f"({sorted(_ALLOWED_MANIFEST_OVERRIDE_KEYS)}) — geometry is static, edit "
                f"the catalog file instead"
            )
        return ref, overrides
    raise LoaderError(
        f"{path}: aircraft[{index}] must be a catalog reference (a path string or a "
        f"{{ref: path, …}} mapping), got {type(entry).__name__}"
    )
```

Replace the body of `load_fleet` (`:150-172`) with the **expand** version (keeps legacy inline support):

```python
    path = Path(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict) or "aircraft" not in raw:
        raise LoaderError(f"{path}: fleet manifest must contain an 'aircraft' list")
    aircraft_list = raw["aircraft"]
    if not isinstance(aircraft_list, list):
        raise LoaderError(f"{path}: 'aircraft' must be a list")

    manifest_dir = path.parent
    fleet: dict[str, Aircraft] = {}
    for i, entry in enumerate(aircraft_list):
        # NEW catalog-reference form: string, or a mapping carrying `ref`.
        if isinstance(entry, str) or (isinstance(entry, dict) and "ref" in entry):
            ref, overrides = _parse_manifest_entry(entry, index=i, path=path)
            catalog_path = (manifest_dir / ref).resolve()
            if not catalog_path.is_file():
                raise LoaderError(
                    f"{path}: aircraft[{i}] references catalog file {ref!r} which does "
                    f"not exist (resolved to {catalog_path})"
                )
            obj_raw = _read_yaml(catalog_path)
            if isinstance(obj_raw, dict) and overrides:
                obj_raw = {**obj_raw, **overrides}
            try:
                aircraft = _build_catalog_object(obj_raw, source=catalog_path)
            except (ValueError, KeyError, TypeError, LoaderError) as e:
                raise LoaderError(f"{path}: aircraft[{i}] ({ref}): {e}") from e
        # LEGACY inline aircraft (removed in Commit 4 — the contract step).
        elif isinstance(entry, dict):
            ident = entry.get("id", f"#{i}")
            try:
                aircraft = _build_aircraft(entry)
            except (ValueError, KeyError, TypeError, LoaderError) as e:
                raise LoaderError(f"{path}: aircraft {ident!r}: {e}") from e
        else:
            raise LoaderError(
                f"{path}: aircraft[{i}] must be a catalog reference or aircraft mapping, "
                f"got {type(entry).__name__}"
            )
        if aircraft.id in fleet:
            raise LoaderError(f"{path}: duplicate aircraft id {aircraft.id!r}")
        fleet[aircraft.id] = aircraft
    return fleet
```

Also update the `load_fleet` docstring (`:144-149`) to describe the manifest model. Add `from collections.abc import Callable`? — not needed (`_OBJECT_BUILDERS` registry is implicit in `_build_catalog_object`; keep it inline for now, no registry dict required for one type).

> **NOTE on the "missing 'aircraft'" message:** it changes from `"top-level mapping must contain 'aircraft' list"` to `"fleet manifest must contain an 'aircraft' list"`. The pinned test `test_missing_top_level_aircraft` matches `"must contain 'aircraft'"` — update it to `"must contain an 'aircraft'"` in Task 4. The not-a-list message stays substring-compatible (`"'aircraft' must be a list"`).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_loader_catalog.py -q`
Expected: `test_data_fleet_loads_after_migration` may still FAIL (data not migrated yet) — that's expected until Commit 2; all OTHER tests PASS. Confirm only that one fails for the data reason.

Run: `pytest tests/test_loader.py -q` → Expected: PASS (legacy inline still supported).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_catalog.py
git commit -m "feat(595): load_fleet accepts catalog references alongside inline (expand)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## COMMIT 2 — Migrate data to catalog references (two transient catalogs, byte-identical)

### Task 2: Split the three fleet files into catalog files + manifests

**Files:** Create `data/catalog/*.yaml`, `examples/herrenteich/catalog/*.yaml`, `tests/fixtures/catalog/taper_glider.yaml`, `data/catalog/README.md`; rewrite the 3 fleet files.

- [ ] **Step 1: Create `data/catalog/` (9 files, synthetic numbers VERBATIM).**
For each aircraft block in `data/fleet.yaml` (`scheibe_falke` `:54`, `aviat_husky` `:97`, `fuji` `:148`, `wild_thing` `:193`, `zlin_savage` `:243`, `cessna_140` `:293`, `cessna_150` `:344`, `ctsl` `:394`, `fk9_mkii` `:440`): write `data/catalog/<id>.yaml` containing `type: aircraft` then the block's fields **unchanged** (strip the leading `- ` list indentation; keep all per-field comments verbatim). Example head of `data/catalog/scheibe_falke.yaml`:
```yaml
# Catalog entry. See data/catalog/README.md for the parts-model conventions.
type: aircraft
id: scheibe_falke
name: "Scheibe SF-25E Falke"
wing_position: high
gear: monowheel
movement_mode: always_cart
turn_radius_m: null
measured: false
notes: "Cantilever wing; outriggers folded into wing footprint at the tips"
parts:
  - kind: fuselage
    length_m: 7.6
    # …rest verbatim from data/fleet.yaml:62-92…
```

- [ ] **Step 2: Move the conventions header** from `data/fleet.yaml:1-49` into a new `data/catalog/README.md` (verbatim, as prose). `data/fleet.yaml` keeps only a 3-line pointer header.

- [ ] **Step 3: Rewrite `data/fleet.yaml` as a manifest** (order matches the original list):
```yaml
# Synthetic demo fleet — a thin MANIFEST referencing data/catalog/ entries by
# path. Object definitions + parts-model conventions live in data/catalog/.
aircraft:
  - catalog/scheibe_falke.yaml
  - catalog/aviat_husky.yaml
  - catalog/fuji.yaml
  - catalog/wild_thing.yaml
  - catalog/zlin_savage.yaml
  - catalog/cessna_140.yaml
  - catalog/cessna_150.yaml
  - catalog/ctsl.yaml
  - catalog/fk9_mkii.yaml
```

- [ ] **Step 4: Create `examples/herrenteich/catalog/` (8 files, real numbers VERBATIM)** from `examples/herrenteich/fleet.yaml` blocks (`scheibe_falke` `:88`, `stemme_s10` `:144`, `aviat_husky` `:191`, `wild_thing` `:241`, `zlin_savage` `:292`, `cessna_140` `:342`, `ctsl` `:393`, `fk9_mkii` `:438`), same `type: aircraft` + verbatim-fields recipe (keep all sourcing comments). Move the herrenteich header (`:1-77`) into `examples/herrenteich/catalog/README.md` or keep it atop the manifest.

- [ ] **Step 5: Rewrite `examples/herrenteich/fleet.yaml` as a manifest** (preserve original order):
```yaml
aircraft:
  - catalog/scheibe_falke.yaml
  - catalog/stemme_s10.yaml
  - catalog/aviat_husky.yaml
  - catalog/wild_thing.yaml
  - catalog/zlin_savage.yaml
  - catalog/cessna_140.yaml
  - catalog/ctsl.yaml
  - catalog/fk9_mkii.yaml
```

- [ ] **Step 6: Create `tests/fixtures/catalog/taper_glider.yaml`** from `tests/fixtures/fleet_taper.yaml:5-45` (`type: aircraft` + verbatim). Rewrite `tests/fixtures/fleet_taper.yaml`:
```yaml
# TEST-ONLY fleet for the polygon-parts determinism canary (#548).
aircraft:
  - catalog/taper_glider.yaml
```

- [ ] **Step 7: Run the FULL suite — byte-identical gate.**

Run: `pytest -q` then `pytest -q tests/test_solver_determinism*.py tests/ -k canary -q` (whatever the canary test is named; e.g. `solve_canary_six_planes_tight`).
Expected: PASS, unchanged. The migration changed *how* data loads, not *what* loads, so every collision fixture + the determinism canary must stay green. **If any fixture flips here, it is a loader/migration bug — stop and fix before committing** (not a data change yet).

- [ ] **Step 8: Commit**

```bash
git add data/ examples/herrenteich/ tests/fixtures/fleet_taper.yaml tests/fixtures/catalog/
git commit -m "refactor(595): migrate fleet files to catalog references (byte-identical)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## COMMIT 3 — Migrate test helpers + fuzz to references

### Task 3: Rework `tests/test_loader.py` inline-fleet helpers

**Files:** Modify `tests/test_loader.py` (helpers near `:34-65`).

- [ ] **Step 1:** Inspect `_fleet_yaml(*entries)` and `_aircraft_entry(aid)` (around `:40-65`). `_aircraft_entry(aid)` returns a minimal valid aircraft **dict** — keep it. Replace `_fleet_yaml` (which wrapped entries as inline YAML) with a writer that emits catalog files + a manifest and returns the manifest path:

```python
def _write_fleet(tmp_path: Path, *aircraft: dict[str, Any]) -> Path:
    """Write each aircraft dict to tmp_path/catalog/<id>_<i>.yaml (with
    `type: aircraft`) + a manifest tmp_path/fleet.yaml referencing them.
    Filenames are index-disambiguated so duplicate ids can be exercised."""
    cat = tmp_path / "catalog"
    cat.mkdir(exist_ok=True)
    refs: list[str] = []
    for i, ac in enumerate(aircraft):
        fname = f"{ac.get('id', f'obj{i}')}_{i}.yaml"
        (cat / fname).write_text(
            yaml.safe_dump({"type": "aircraft", **ac}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        refs.append(f"catalog/{fname}")
    p = tmp_path / "fleet.yaml"
    p.write_text(yaml.safe_dump({"aircraft": refs}, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p
```

- [ ] **Step 2:** Migrate every test that built an inline fleet via `_write(tmp_path/"f.yaml", _fleet_yaml(...))` to `path = _write_fleet(tmp_path, _aircraft_entry(...), ...)`. (These tests just need *a* fleet; behaviour is unchanged because the catalog path builds the same Aircraft.) Search: `_fleet_yaml(` and `_aircraft_entry(` in `tests/test_loader.py`.

- [ ] **Step 3:** Run `pytest tests/test_loader.py -q` → Expected: PASS (the message-pinned error tests are fixed in Commit 4; everything else passes here).

- [ ] **Step 4:** Commit:
```bash
git add tests/test_loader.py
git commit -m "test(595): test_loader fleet helpers emit catalog references

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 4: Rework fuzz fleet-document strategies

**Files:** Modify `tests/fuzz/strategies.py` (`_well_formed_fleet_doc` `:195`, `fleet_documents` `:322`, `_write_fleet_yaml` `:442`), and the fleet-doc run helper.

- [ ] **Step 1:** The fuzz harness writes a fleet doc to one tmp file and calls `load_fleet`. Under the catalog model, a fleet file is a manifest of paths — so the harness must write catalog files too. Add a helper to `tests/fuzz/strategies.py`:

```python
def write_fleet_doc(doc: Any, tmpdir: Path) -> Path:
    """Materialise a fuzzed fleet doc as a catalog + manifest under tmpdir.

    A well-formed doc {"aircraft": [<aircraft dicts>]} is written as one
    catalog file per aircraft + a manifest of refs. A malformed doc (not a
    dict, or 'aircraft' not a list) is written verbatim as the manifest so the
    loader's top-level guards are still fuzzed."""
    manifest = tmpdir / "fleet.yaml"
    if isinstance(doc, dict) and isinstance(doc.get("aircraft"), list):
        cat = tmpdir / "catalog"
        cat.mkdir(exist_ok=True)
        refs: list[Any] = []
        for i, ac in enumerate(doc["aircraft"]):
            if isinstance(ac, dict):
                fname = f"obj_{i}.yaml"
                (cat / fname).write_text(
                    yaml.safe_dump({"type": "aircraft", **ac}, allow_unicode=True),
                    encoding="utf-8",
                )
                refs.append(f"catalog/{fname}")
            else:
                refs.append(ac)  # non-dict entry — fuzzes the ref-shape guard
        manifest.write_text(yaml.safe_dump({"aircraft": refs}, allow_unicode=True), encoding="utf-8")
    else:
        manifest.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return manifest
```

- [ ] **Step 2:** Update `run_fleet(doc)` (`:508`) to use `write_fleet_doc` into a tmp dir (instead of `_write_yaml_tmp(doc)` + `load_fleet`):

```python
def run_fleet(doc: Any) -> None:
    with tempfile.TemporaryDirectory() as d:
        try:
            loader.load_fleet(write_fleet_doc(doc, Path(d)))
        except LoaderError:
            pass
```
(Add `import tempfile` if absent.) `_well_formed_fleet_doc` and `fleet_documents` keep emitting `{"aircraft": [<dicts>]}` — `write_fleet_doc` now translates that to the catalog shape, so the **11 fleet fuzz tests still assert "never crashes"** against the real loader path. Update `_write_fleet_yaml` (`:442`, used by the layout/scenario ref-resolving helpers) to call `write_fleet_doc` with its 2-plane doc.

- [ ] **Step 3:** Run `pytest -m "" tests/fuzz/test_loader_fuzz.py -q` (fuzz tests; `-m ""` includes slow). Expected: PASS (never crashes; LoaderError swallowed).

- [ ] **Step 4:** Commit:
```bash
git add tests/fuzz/strategies.py
git commit -m "test(595): fuzz fleet docs emit catalog + manifest

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## COMMIT 4 — Contract: drop inline-aircraft support

### Task 5: Remove the legacy inline branch + fix pinned error tests

**Files:** Modify `src/hangarfit/loader.py` (`load_fleet` loop), `tests/test_loader.py` (pinned tests).

- [ ] **Step 1:** In `load_fleet`, delete the `elif isinstance(entry, dict):` legacy branch (Task 1, Step 3). Now a dict entry **without `ref`** falls into `_parse_manifest_entry`, which raises the inline-rejection error. The loop becomes: parse entry (string or `{ref}`) → resolve → build; any other shape → `_parse_manifest_entry` error. Simplify to always call `_parse_manifest_entry`:

```python
    for i, entry in enumerate(aircraft_list):
        ref, overrides = _parse_manifest_entry(entry, index=i, path=path)
        catalog_path = (manifest_dir / ref).resolve()
        if not catalog_path.is_file():
            raise LoaderError(
                f"{path}: aircraft[{i}] references catalog file {ref!r} which does "
                f"not exist (resolved to {catalog_path})"
            )
        obj_raw = _read_yaml(catalog_path)
        if isinstance(obj_raw, dict) and overrides:
            obj_raw = {**obj_raw, **overrides}
        try:
            aircraft = _build_catalog_object(obj_raw, source=catalog_path)
        except (ValueError, KeyError, TypeError, LoaderError) as e:
            raise LoaderError(f"{path}: aircraft[{i}] ({ref}): {e}") from e
        if aircraft.id in fleet:
            raise LoaderError(f"{path}: duplicate aircraft id {aircraft.id!r}")
        fleet[aircraft.id] = aircraft
    return fleet
```

- [ ] **Step 2:** Add a contract test to `tests/test_loader_catalog.py`:
```python
def test_inline_aircraft_mapping_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "fleet.yaml"
    manifest.write_text(
        yaml.safe_dump({"aircraft": [_aircraft_doc("p1")]}, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(LoaderError, match="no longer supported"):
        load_fleet(manifest)
```

- [ ] **Step 3:** Fix the pinned error tests in `tests/test_loader.py`:
  - `test_missing_top_level_aircraft` (`:304`): change `match="must contain 'aircraft'"` → `match="must contain an 'aircraft'"`.
  - `test_aircraft_not_a_list` (`:309`): unchanged (`"'aircraft' must be a list"` still a substring).
  - `test_duplicate_aircraft_id` (`:314`): already uses the reworked `_write_fleet` (Task 3); duplicate detection still fires → `match="duplicate aircraft id 'foo'"` unchanged.
  - `test_aircraft_entry_not_a_mapping` (`:379`): **repurpose** — a bare string is now a *valid ref*. Rename to `test_aircraft_entry_wrong_type` and assert a non-string/non-mapping entry is rejected:
    ```python
    def test_aircraft_entry_wrong_type(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "f.yaml", "aircraft:\n  - 123\n")
        with pytest.raises(LoaderError, match="must be a catalog reference"):
            load_fleet(path)
    ```

- [ ] **Step 4:** Run `pytest tests/test_loader.py tests/test_loader_catalog.py -q` → Expected: PASS. Then full suite `pytest -q` → Expected: PASS (still byte-identical — no data changed yet).

- [ ] **Step 5:** Commit:
```bash
git add src/hangarfit/loader.py tests/test_loader.py tests/test_loader_catalog.py
git commit -m "refactor(595)!: drop inline aircraft support; fleets are manifests only (contract)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## COMMIT 5 — Collapse to one central real catalog + re-author fixtures (closes #594)

### Task 6: Make `data/catalog/` the single real catalog

**Files:** Overwrite 7 `data/catalog/*.yaml`; add `data/catalog/stemme_s10.yaml`; delete `examples/herrenteich/catalog/`; re-point `examples/herrenteich/fleet.yaml`.

- [ ] **Step 1:** For each of the 7 shared aircraft (`scheibe_falke`, `aviat_husky`, `wild_thing`, `zlin_savage`, `cessna_140`, `ctsl`, `fk9_mkii`), **overwrite** `data/catalog/<id>.yaml` with the **herrenteich (real)** content (the `examples/herrenteich/catalog/<id>.yaml` file created in Commit 2), keeping `type: aircraft`. `fuji` and `cessna_150` are untouched (synthetic, no real source).
- [ ] **Step 2:** `git mv examples/herrenteich/catalog/stemme_s10.yaml data/catalog/stemme_s10.yaml`.
- [ ] **Step 3:** Re-point `examples/herrenteich/fleet.yaml` at the central catalog:
```yaml
aircraft:
  - ../../data/catalog/scheibe_falke.yaml
  - ../../data/catalog/stemme_s10.yaml
  - ../../data/catalog/aviat_husky.yaml
  - ../../data/catalog/wild_thing.yaml
  - ../../data/catalog/zlin_savage.yaml
  - ../../data/catalog/cessna_140.yaml
  - ../../data/catalog/ctsl.yaml
  - ../../data/catalog/fk9_mkii.yaml
```
- [ ] **Step 4:** `git rm -r examples/herrenteich/catalog/` (now empty / fully moved). `data/fleet.yaml` is **unchanged** (its `catalog/<id>.yaml` paths now resolve to real-numbered files automatically).

### Task 7: Re-author the fixtures the real numbers move (discovery-driven)

- [ ] **Step 1:** Run the full suite: `pytest -q`. Catalog the RED tests. Expect the tight geometric fixtures (`tests/fixtures/invalid_wing_over_cockpit.yaml`, `valid_left_side_nesting.yaml`, `valid_right_side_nesting.yaml`, `valid_wing_over_tail.yaml`, `invalid_bay_intrusion_wingtip.yaml`, `valid_two_separated.yaml`, the bay fixtures, …) and the determinism canary (`solve_canary_six_planes_tight`).
- [ ] **Step 2: For EACH red fixture, in isolation:**
  1. Read the fixture's comment/intent (what geometric relationship / verdict it asserts).
  2. Decide: did real numbers *break the intent* (e.g. a wingtip no longer overhangs the cockpit) or *just shift the result*? Re-tune the `placements` poses (x/y/heading) — **not** the catalog — until the fixture again exhibits the exact relationship its name/comment claims, and re-assert the original verdict (valid/invalid). Do NOT weaken an assertion to pass; preserve the invariant.
  3. If a fixture packs more planes than the (now-larger) real aircraft fit in the placeholder `data/hangar.yaml`, either adjust poses or switch it to `tests/fixtures/test_hangar_large.yaml` (the existing large test hangar), matching what comparable fixtures already do.
- [ ] **Step 3:** Re-baseline the determinism canary: the byte-identity golden changes because the geometry changed. Follow `docs/dev/test-flakes-and-ci-gotchas.md` ("re-baseline on a deliberate determinism re-base — pin max_restarts + budget both sides"). Regenerate the golden, confirm a fresh double-run is byte-identical, commit the new baseline.
- [ ] **Step 4:** Update `tests/test_loader_catalog.py::test_data_fleet_loads_after_migration` (now fully valid) and any test asserting exact synthetic dims (search `tests/` for the old synthetic values that moved, e.g. via the failures).
- [ ] **Step 5:** Full suite green: `pytest -q` AND `pytest -m "" -q` (include slow + fuzz). Run the determinism double-solve canary twice; confirm identical.
- [ ] **Step 6:** Commit:
```bash
git add data/ examples/herrenteich/ tests/
git commit -m "refactor(595): single real catalog; re-author fixtures for real dims (closes #594)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## COMMIT 6 — Docs, CHANGELOG, fold design artifacts

### Task 8: Documentation

- [ ] **Step 1:** `CLAUDE.md` — reframe the "`data/` is synthetic" notes (Open Questions + the Quick-Reference fleet pointer at `:26`): `data/` is now "the shipped real-spec aircraft **catalog** (`data/catalog/`) + the demo hangar + a thin demo fleet **manifest**"; `fuji`/`cessna_150` remain synthetic placeholders; note herrenteich now **references** the central catalog (trades self-containment for zero duplication, #595).
- [ ] **Step 2:** `docs/architecture/05-building-block-view.md` (loader description, ~`:119-130`): describe catalog dispatch (`type:` discriminator → per-type builder), manifest-by-path-reference, and per-fleet flag overrides.
- [ ] **Step 3:** `examples/herrenteich/README.md` — note the fleet manifest references the shared `data/catalog/`.
- [ ] **Step 4:** Update inline-fleet snippets in `docs/superpowers/plans/2026-05-28-wheels-canonical.md` and `2026-06-10-polygon-parts-pr1-collision.md` to the catalog/manifest shape (or annotate them as pre-#595 historical).
- [ ] **Step 5:** `CHANGELOG.md` `[Unreleased]`:
```markdown
### Changed
- Fleet data is now a per-object **catalog** (`data/catalog/`, one file per
  aircraft with a `type:` discriminator) referenced by path from thin fleet
  manifests; inline aircraft definitions are no longer supported. Manifest
  entries may override per-fleet operational flags (`movement_mode`,
  `tow_pivotable`). The synthetic/real Scheibe divergence (#594) is resolved by
  a single central real catalog. (#595)
```
- [ ] **Step 6:** `git add docs/superpowers/research/ docs/superpowers/specs/2026-06-11-learned-backend-and-ground-objects-design.md` (fold in the epic design artifacts, per the handoff). **Do NOT** `git add CLAUDE.md` blindly — stage only the #595 reframe hunk (`git add -p`), since `CLAUDE.md` also carries pre-existing unrelated edits that are not part of this PR.
- [ ] **Step 7:** Commit:
```bash
git add CHANGELOG.md docs/
git commit -m "docs(595): catalog model docs + CHANGELOG; fold learned-backend design artifacts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## FINAL VERIFICATION (before PR)

- [ ] `ruff check src/ tests/` → clean
- [ ] `ruff format --check src/ tests/` → clean
- [ ] `mypy src/hangarfit/` → clean
- [ ] `pytest -q` → all pass
- [ ] `pytest -m "" -q` → all pass (slow + fuzz incl.)
- [ ] Determinism: run the canary double-solve twice → byte-identical (manual `determinism-guard` equivalent).
- [ ] Smoke: `hangarfit check examples/layouts/example.yaml --render /tmp/out.png` (exit 0); `hangarfit solve tests/fixtures/scenario_minimal.yaml` (exit 0); `hangarfit check examples/herrenteich/layout.yaml` (exit 0 — references the central catalog).
- [ ] Draft PR: `gh pr create --draft --base develop --title "..." --body "Closes #595, Closes #594 ..."`; then the review arc (code-reviewer + silent-failure-hunter for loader; comment-analyzer for the doc changes), per CLAUDE.md.

---

## Self-review notes
- **Spec coverage:** D1 path-refs (Task 1 `_parse_manifest_entry`), D2 drop-inline (Commit 4), D3 central catalog (Commit 5), D4 catalog/flag split (Task 1 overrides; Commit 2 catalog), D5 real numbers + re-author (Commit 5). `type:` seam + Stage-A error (Task 1). Determinism list-order (Task 1 + Commit 2 gate). Fuzz/test/doc surfaces all have tasks.
- **Determinism:** byte-identity is asserted at Commit 2/4 (no data change) and deliberately re-based at Commit 5; manifest list-order → deterministic dict insertion.
- **Risk C1 (silent meaning change):** mitigated by staging — fixtures only move in Commit 5, where each red is re-verified against its documented invariant (not weakened to pass).
