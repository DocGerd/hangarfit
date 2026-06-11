# Design Spec — Per-Object Catalog + `type:` Discriminator (#595, Stage 0)

**Status:** Design / planning (2026-06-11). Audience: maintainer + contributors.
**Issue:** #595 (Stage 0 of the learned-backend ladder).
**Companion:** the epic design `docs/superpowers/specs/2026-06-11-learned-backend-and-ground-objects-design.md` (this is its Stage 0, the substrate everything else builds on).

---

## Intent

Today a fleet is a monolithic YAML inlining every aircraft's full definition, and `load_fleet` hard-requires a top-level `aircraft:` list of mappings (`loader.py:152`). This blocks two things: (1) a clean home for the **non-aircraft physical objects** the ground-objects epic needs (a fuel trailer, glider trailers, a rescue vehicle — none of which are aircraft), and (2) reuse — the Scheibe SF-25E is defined twice today (`data/fleet.yaml` and `examples/herrenteich/fleet.yaml`) with *divergent* numbers (#594).

This refactor introduces a **per-object catalog of physical objects** — each object in its own file, carrying a `type:` discriminator (of which `aircraft` is one type) — referenced by **path** from thin fleet manifests. It is the substrate the ground-objects epic (#600) and the learned backend (#607) build on.

**Non-goal for #595:** building the ground-object *schema/behavior*. This refactor only (a) reserves the `type:` discriminator, (b) routes to a pluggable per-type builder, and (c) keeps each type's unknown-key allowlist strict and separate — exactly as the #595 sequencing comment specifies. `type: ground_object` resolves to a clear "not yet supported (Stage A, #600)" error.

---

## Decisions (settled with the maintainer, 2026-06-11)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Path references.** A manifest lists catalog file *paths*, resolved relative to the manifest's parent dir. | Identical idiom to how `fleet:`/`hangar:` already resolve (`loader.py:358/371/504/517`). Deterministic, no new resolution machinery, no id→path drift. |
| D2 | **Full migration, drop inline.** Fleet files become thin manifests; inline-aircraft support is removed. An inline mapping under `aircraft:` is a hard error with a migration hint. | One way to define an object, one place it lives. |
| D3 | **One central catalog of *real* static definitions.** Both the demo (`data/fleet.yaml`) and `examples/herrenteich/` reference the *same* catalog entries → **zero duplication** of static data. | The maintainer's intent: "herrenteich does not duplicate anything that should be static." A Scheibe SF-25E is defined once. |
| D4 | **Catalog = static physics; manifest = per-fleet operational flags.** A manifest entry may **override** a small allowlist of operational flags (`movement_mode`, `tow_pivotable`) on top of the shared static definition. | The maintainer's model: "same values except for flags (always-on-cart vs cart-eligible)." Geometry is static and never override-able. |
| D5 | **Real numbers win; synthetic placeholders retired; affected fixtures re-authored.** | The synthetic `data/` geometry was eyeballed and is admittedly wrong (`measured: false`); the real published-spec numbers are correct. This also genuinely closes #594. |

### Why this changed from an earlier "two catalogs" recommendation
An interim recommendation was *two catalogs* (synthetic + real, no churn) because the herrenteich-real numbers differ from the synthetic ones in **geometry** (e.g. Scheibe wing chord 1.2 → 1.01 m; `wild_thing` fuselage width 0.85 → 1.15 m), and ~45 fixtures + the determinism canary reference `data/fleet.yaml`, with ~15–20 hand-tuned geometric edge-case fixtures at risk of silent verdict flips. The maintainer chose the central-catalog path anyway: the duplication-free model is worth the bounded, *visible* (red-test-guided) fixture re-authoring. Crucially, today the shared aircraft already have **identical operational flags** — only the static geometry diverges — so the divergence is purely "placeholder vs real," exactly the thing a single source fixes.

---

## Architecture

### Catalog file — `data/catalog/<id>.yaml`
```yaml
type: aircraft            # discriminator; defaults to `aircraft` if omitted
id: scheibe_falke
name: "Scheibe SF-25E Super Falke"
wing_position: high
gear: monowheel
movement_mode: always_cart   # the static *default* (override-able per fleet)
measured: false
parts: [ ... ]               # exactly as today
wheels: { ... }
struts: { ... }              # if present
```
- The `type:` field is the heterogeneous seam ("a catalog of physical objects, of which aircraft is one type").
- All existing aircraft fields are unchanged. `type:` is **stripped by the dispatcher** before the aircraft builder runs, so `_ALLOWED_AIRCRAFT_KEYS` is untouched.

### Fleet file → thin manifest (`data/fleet.yaml`, `examples/herrenteich/fleet.yaml`)
```yaml
# Each entry is EITHER a bare path string ...
aircraft:
  - catalog/scheibe_falke.yaml          # demo: relative to data/
  - catalog/aviat_husky.yaml
# ... OR a mapping {ref: <path>, <flag overrides>} for per-fleet operational flags:
  - ref: catalog/cessna_140.yaml
    movement_mode: cart_eligible        # overrides the catalog default for THIS fleet
```
For herrenteich, refs point at the shared central catalog: `- ../../data/catalog/scheibe_falke.yaml`.

- Paths resolve relative to the manifest's parent dir.
- **List order is preserved** → deterministic (no globbing, no sorting; author order *is* the order).
- The three forms are unambiguous: **string** = plain ref; **mapping with `ref`** = ref + flag overrides; **mapping without `ref`** (i.e. an old inline `{id, name, parts, …}`) = the dropped inline form → hard error: *"inline aircraft no longer supported — move it to a catalog file and reference it by path."*

### Loader dispatch (`loader.py`)
- New registry: `_OBJECT_BUILDERS: dict[str, Callable] = {"aircraft": _build_aircraft}`.
- `load_fleet(manifest_path)`:
  1. Read manifest; require top-level `aircraft:` is a **list**.
  2. For each entry, in order: normalise to `(ref_path, overrides)`; resolve `ref_path` relative to the manifest dir; `_read_yaml` the catalog file; pop `type:` (default `aircraft`); look up the builder; merge `overrides` (allowlisted to `_ALLOWED_MANIFEST_OVERRIDE_KEYS = {"movement_mode", "tow_pivotable"}`) onto the object dict; build; key by `id`.
  3. Reject: duplicate `id` across refs; missing-file ref (loud `LoaderError` naming the manifest + the bad path); an `aircraft:`-list ref whose catalog file is `type:` other than `aircraft` (type mismatch); an unregistered `type:` → *"type 'X' not yet supported (arrives in Stage A, #600); known types: aircraft."*
- `_build_aircraft` and `_ALLOWED_AIRCRAFT_KEYS` are **unchanged**. Return type stays `dict[str, Aircraft]` → all callers (`cli.py` `--fleet`; `load_layout`/`load_scenario`) are unaffected.

### Central catalog
- **Location:** `data/catalog/` (aligns with the maintainer's own `data/catalog/` sketch in the #595 comment; avoids a new top-level dir). `data/` is **reframed** from "synthetic placeholders" to "shipped reusable data: the real-spec aircraft catalog + the demo hangar + the demo manifest." (Alternative considered: a neutral top-level `catalog/`. Flagged for the maintainer; `data/catalog/` chosen for lower structural churn.)
- **Contents (10 aircraft):** 8 published-spec (`scheibe_falke`, `aviat_husky`, `wild_thing`, `zlin_savage`, `cessna_140`, `ctsl`, `fk9_mkii`, `stemme_s10`) + 2 synthetic-only with no real source (`fuji`, `cessna_150`, staying `measured: false`). The 7 shared aircraft take the **real (herrenteich) numbers**.
- **Test-only:** `tests/fixtures/catalog/taper_glider.yaml` (the polygon-taper test shape; referenced by `tests/fixtures/fleet_taper.yaml`). Stays test-scoped, not in the shipped catalog.

---

## Migration plan — staged in two commits

**Commit 1 — loader mechanism, byte-identical (the oracle).**
Build the catalog/manifest/dispatch mechanism and migrate the data **preserving current numbers exactly**. To stay byte-identical while numbers still diverge, this commit temporarily keeps the two number-sets: `data/catalog/` from the *synthetic* `data/fleet.yaml`, `examples/herrenteich/catalog/` from the *real* herrenteich fleet. Rewrite the 3 fleet files as manifests; drop inline; rework fuzz/loader tests for the new *shape*. **The entire existing suite — every collision fixture + the determinism canary — must pass unchanged.** This proves the loader is correct independently of any data change.

**Commit 2 — collapse to the single real catalog (the data change).**
Make `data/catalog/` the central real catalog: **overwrite** the 7 shared synthetic entries with the real numbers, keep `fuji`/`cessna_150`, add `stemme_s10` (moved over from the deleted `examples/herrenteich/catalog/`). `data/fleet.yaml`'s manifest is **unchanged** — its paths (`catalog/<id>.yaml`) are stable; only the catalog file *contents* change, so the demo automatically picks up real numbers. Re-point herrenteich's manifest at `../../data/catalog/` and delete `examples/herrenteich/catalog/`. **Run the suite; each red fixture is re-verified/re-tuned against the real geometry.** Close #594.

> Splitting the risk sources this way is the core de-risking move: a fixture that flips in Commit 2 is *attributable* to the intended number change, never to a hidden loader bug.

### Fixture re-authoring scope (Commit 2)
- ~45 fixtures reference `data/fleet.yaml`; realistically the ~15–20 tight geometric edge-case ones flip (`invalid_wing_over_cockpit`, the `valid_*_nesting` pair, `invalid_bay_intrusion_wingtip`, `valid_wing_over_tail`, …) plus the determinism canary `solve_canary_six_planes_tight` (re-baselined per the documented re-base procedure).
- **Cascade risk:** real aircraft are often *larger* than the placeholders; fixtures packing many planes into the placeholder `data/hangar.yaml` (e.g. `valid_all_nine_planes`, `solve_all_nine_large_hangar`) may need pose/hangar adjustment. Surfaced as red tests; handled case-by-case with the rule each fixture documents as the invariant to preserve.

---

## Test & fuzz strategy

- **`tests/fuzz/strategies.py`** (highest-risk shape change): `_well_formed_fleet_doc()` and `fleet_documents()` currently emit inline `aircraft:` lists; rework them to write per-object catalog files to a tmp dir + a manifest listing their paths. `_well_formed_aircraft()` is reused unchanged as catalog-file content. The 11 fleet fuzz tests still assert "never crashes" against the new shape.
- **`test_loader.py`**: ~35 inline-fleet tests migrated to the catalog+manifest form; ~10 exact-error-text assertions updated to the new messages.
- **New `test_loader_catalog.py`** (the dispatch seam):
  - `type: aircraft` (and omitted `type`) builds an Aircraft; `type` is stripped (not an unknown-key error).
  - unregistered `type` → Stage-A `LoaderError`.
  - inline mapping under `aircraft:` (no `ref`) → drop-inline error.
  - bare-string ref builds; `{ref, movement_mode}` override applies; non-allowlisted override key rejected; geometry override key rejected.
  - missing-file ref, duplicate id across refs, `aircraft:`-list ref to a non-aircraft type → each a loud `LoaderError`.
  - byte-identity: a manifest+catalog reproduces the same `dict[str, Aircraft]` as the equivalent old inline fleet (Commit-1 guard).

---

## Docs to update
- `CLAUDE.md`: reframe the "`data/` is synthetic" notes (Open questions / Quick-Reference fleet pointer) to "real-spec catalog + demo manifest"; note herrenteich now references the central catalog (trades self-containment for zero duplication).
- arc42 `docs/architecture/05-building-block-view.md` (`loader` description: catalog dispatch + manifest-by-reference).
- `data/fleet.yaml` header comments (manifest-of-paths, not inline mappings).
- Two superpowers plan snippets that print an inline fleet (`2026-05-28-wheels-canonical.md`, `2026-06-10-polygon-parts-pr1-collision.md`) + the fuzz-loader spec snippet.
- New ADR? **No** — this is within the loader's existing remit; recorded here + in CHANGELOG. (An ADR is reserved for the ground-object *taxonomy* in Stage A.)
- `CHANGELOG.md [Unreleased]`: catalog refactor + #594 closure entry.
- Fold the epic design + research artifacts (already in `docs/superpowers/`) into this PR.

---

## Out of scope (deferred by design)
- Ground-object **schema/behavior** (Stage A #600) — only the `type:` seam + Stage-A error here.
- Hangar cataloging (hangars stay single-file refs).
- Catalog **search-dirs / id-based resolution** (path refs only).
- Any solver/towplanner change (`determinism-guard` not triggered; loader-only).

---

## Risk register
| # | Risk | Mitigation |
|---|---|---|
| C1 | **Fixture re-authoring changes a test's meaning silently.** | Staged commits: loader proven byte-identical (Commit 1) *before* any number changes (Commit 2); in Commit 2 every flip is a *red* test (visible), re-verified against the invariant each fixture documents. |
| C2 | **Real dims cascade** (bigger planes don't fit placeholder hangars). | Surfaced as red tests; adjust poses/hangar per fixture; the all-nine fixtures already use a larger test hangar. |
| C3 | **Determinism / ordering regression.** | Manifest **list order preserved** → deterministic `dict` insertion; no globbing. Commit-1 byte-identity guard + the existing canary cover it. |
| C4 | **Fuzz shape rework misses an inline path.** | New-shape fuzz strategies + a dedicated drop-inline rejection test; the inventory of all inline producers is recorded (loader, 3 YAML files, fuzz strategies, ~35 tests). |
| C5 | **Over-building the `type:` seam.** | Only `aircraft` is registered; `ground_object` is an explicit not-yet-supported error. The flag-override allowlist is 2 keys. No ground-object schema. |
| C6 | **herrenteich loses self-containment.** | Accepted trade for zero duplication (maintainer's explicit intent); documented in CLAUDE.md + the herrenteich README. |
