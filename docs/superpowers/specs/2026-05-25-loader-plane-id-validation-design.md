# Loader plane-id validation — design (issue #176)

**Status:** approved 2026-05-25
**Tracks:** [#176 Loader: case-sensitivity contract for plane ids](https://github.com/DocGerd/hangarfit/issues/176) — no milestone (loader UX hardening; follow-up from a `silent-failure-hunter` finding on PR #174)
**Author:** Claude (Opus 4.7), reviewed by @DocGerd

---

## 1. Goals & non-goals

**Goals**

- When a YAML author names a plane id that does not match the fleet exactly, fail at the **loader boundary** with an actionable message: the file path, the offending id, and — when there is a near match — a `did you mean 'X'?` suggestion.
- Catch the headline case (mis-capitalisation, e.g. `Foo` for `foo`) **and** general typos (`cesna_150` for `cessna_150`) through the same path.
- Preserve plane ids as **case-sensitive** (no normalisation, no semantic change). This is option **C** from the issue.
- Document the case-sensitivity contract (the issue's option **A**) — folded in for free.

**Non-goals**

- **No case-folding / normalisation** (issue option B — rejected, see §9). Two ids differing only by case remain distinct.
- No change to the `Layout` / `Scenario` model invariants — they stay as the programmatic backstop (§6).
- No new dependency: `difflib` is in the standard library.
- No fuzzy *resolution* (we never silently accept a near-miss — we only *suggest* and then reject).

---

## 2. The problem (current behaviour)

Plane ids are compared by case-sensitive string equality throughout the loader and the model invariants. A mis-cased or typo'd id is not caught where it is introduced:

- `_build_placement` (`loader.py:568`) builds `Placement(plane_id="Foo")` with **no fleet check**.
- The unknown id is only caught later, at `Layout.__post_init__` (`models.py:373`): `Placement references unknown plane_id 'Foo'`. The loader's `except ValueError → LoaderError` wrap adds the file path, but the message still (a) names only the typo, (b) offers no correction, and (c) reads as an internal invariant rather than a YAML-author error.

The scenario path has loader-level checks for `maintenance_plane ∉ fleet_in` (`loader.py:315`) and model backstops for `fleet_in ⊄ fleet` and `constraints.keys() ⊄ fleet_in`, but none of them offers a suggestion.

The id `'Foo'` genuinely *is* unknown — so the defect is **diagnostic timing and quality**, not a wrong result. That reframing is why option C (suggest + reject early) dominates option B (case-fold): C fixes the whole class of near-miss ids, surfaced early, while keeping ids exact.

---

## 3. Design

### 3.1 Two new private helpers in `loader.py`

```python
def _suggest_plane_id(candidate: str, valid_ids: Iterable[str]) -> str:
    """Return a '; did you mean X?' fragment for a near-miss id, or '' if none.

    Two passes, because difflib alone misses the headline case:
    SequenceMatcher is case-sensitive, so 'FOO' vs 'foo' scores 0.0 and
    would yield no suggestion at all.

    1. Case-insensitive exact match: if exactly one valid id equals the
       candidate under casefold(), suggest it with the case-sensitivity
       note. (If two valid ids share a casefold — only possible for a
       fleet that deliberately uses case-distinct ids — skip this pass as
       ambiguous and fall through to difflib.)
    2. difflib.get_close_matches(candidate, valid, n=1, cutoff=0.6) for
       genuine typos.
    """

def _resolve_known_plane_id(
    candidate: str,
    valid_ids: Collection[str],
    *,
    role: str,
    path: Path,
    fix_hint: str = "",
) -> None:
    """Raise LoaderError if candidate is not in valid_ids, otherwise return.

    Message: "{path}: {role} references unknown plane id {candidate!r}{tail}"
    where tail is, in priority order:
      - the _suggest_plane_id fragment when there is a near match, else
      - "; " + fix_hint when fix_hint is non-empty, else
      - "" (bare).
    """
```

A near-match suggestion always wins over `fix_hint`: if we can name the likely intended id, that is more useful than generic guidance.

### 3.2 Message shapes

| Situation | Message (after `{path}: `) |
|---|---|
| case mis-match (`Foo`, fleet has `foo`) | `placement references unknown plane id 'Foo'; did you mean 'foo'? (plane ids are case-sensitive)` |
| typo (`cesna_150`) | `placement references unknown plane id 'cesna_150'; did you mean 'cessna_150'?` |
| novel id, no near match (`zzz`) | `placement references unknown plane id 'zzz'` |
| scenario maintenance, novel id (`ghost`) | `maintenance.plane references unknown plane id 'ghost'; either add it to fleet_in ['aviat_husky', 'ctsl'] or fix the plane id` |

### 3.3 Call sites (the comprehensive scope, approved 2026-05-25)

| Path | id reference | `valid_ids` | `role` | `fix_hint` |
|---|---|---|---|---|
| `load_layout` | each `placements[].plane` | `fleet` | `"placement"` | — |
| `load_layout` | `maintenance.plane` | `fleet` | `"maintenance.plane"` | — |
| `load_scenario` | each `fleet_in[]` | `fleet` | `"fleet_in entry"` | — |
| `load_scenario` | `maintenance.plane` | `fleet_in` | `"maintenance.plane"` | `"either add it to fleet_in {sorted(fleet_in)} or fix the plane id"` |
| `load_scenario` | each `constraints` key | `fleet_in` | `"constraints key"` | `"either add it to fleet_in {sorted(fleet_in)} or fix the plane id"` |

**Asymmetry, by design:** references validated against the (large) fleet — placements and `fleet_in` entries — get a `did you mean` suggestion but no inline enumeration (listing all ~9 planes would be noise). References validated against the (small) `fleet_in` — scenario maintenance and constraint keys — additionally enumerate the valid set in `fix_hint`, preserving the helpful behaviour the existing `not_in_fleet_in` check already has.

### 3.4 Ordering of checks

- **`load_layout`:** validate each placement id, then the maintenance id, **before** the existing occupant-in-placements check (`loader.py:222`) and before `Layout(...)` construction. Root-cause (unknown id) surfaces before the more specific occupant rule.
- **`load_scenario`:** validate `fleet_in` entries (needs `fleet` loaded — placed after the fleet load at `loader.py:~308`); then replace the bare `maintenance_plane not in fleet_in` check (`:315`) with a `_resolve_known_plane_id` call carrying the `fix_hint`; then validate each constraint key inside the existing constraints loop (`:332`), before `_build_plane_constraint`.

---

## 4. Data flow

```
YAML → _read_yaml → build placements / fleet_in / constraints
     → _resolve_known_plane_id(id, valid_ids, role, path[, fix_hint])   ← NEW gate
         └─ on miss: _suggest_plane_id → LoaderError (path + id + hint)
     → Layout(...) / Scenario(...)   ← model invariants unchanged (backstop)
```

---

## 5. Backstops unchanged

`Layout.__post_init__` (`models.py:373`, `:405`) and `Scenario.__post_init__` (`models.py:503+`, the `fleet_in ⊆ fleet`, `maintenance ∈ fleet_in`, `constraints ⊆ fleet_in` checks) are **kept verbatim**. They are the only guard for callers that construct models directly (solver internals, tests, REPL) and never touch the loader. The new loader gate is an earlier, friendlier front door — not a replacement.

---

## 6. Tests

**New** (`tests/test_loader.py` for layout, `tests/test_loader_scenario.py` for scenario) — a parametrised class per entry point covering, for each of the five call sites:

- exact match → passes (no raise);
- case mis-match → `LoaderError` whose message contains `did you mean '<canonical>'` and `case-sensitive`;
- typo with a near match (e.g. `cesna_150`) → `LoaderError` suggesting the difflib match;
- novel id with no near match (e.g. `zzz`) → `LoaderError` that names the id and does **not** contain `did you mean` (no false suggestion).

**Backstop preservation** — direct `Layout` / `Scenario` construction with an unknown id still raises `ValueError` (these already exist: `test_models.py:587`, `test_scenario.py:82` — assert they remain green).

**Tests touched (message change):**

- `tests/test_loader.py::test_unknown_plane_reference_propagates` (`:749`) — currently `match="unknown plane_id 'ghost'"`; the loader gate now fires first with `unknown plane id 'ghost'` (space, no underscore). Update the regex.
- `tests/test_loader_scenario.py::test_load_scenario_rejects_maintenance_plane_not_in_fleet_in` (`:81`) — the message moves into the shared helper. Update its assertions to the new wording: still asserts the bad id `'ghost'`, the path, an `aviat_husky` mention (now inside the `fix_hint` enumeration), and the `add it to fleet_in` guidance — but the exact contiguous substring `either add it to fleet_in or fix the plane id` changes (the sorted list is inserted), so assert on the two halves separately.
- `tests/test_loader_scenario.py::test_load_scenario_rejects_null_maintenance_plane` (`:119`, added in #219) — its docstring notes the null guard "precedes the maintenance_plane not in fleet_in boundary check"; still true, reword lightly to point at the renamed check if needed. No assertion change.

`tests/test_loader_scenario.py:43` (`match="unknown plane"`) is robust — the substring survives the new wording.

---

## 7. Docs (folds in option A)

- **`src/hangarfit/loader.py` module docstring** — add a short paragraph: plane ids are case-sensitive; the loader does not normalise; near-miss ids are rejected with a suggestion.
- **arc42** — add a one-line note to the loader entry in [`docs/architecture/05-building-block-view.md`](../../architecture/05-building-block-view.md) and, if a natural home exists, the loader-conventions area of [`08-crosscutting-concepts.md`](../../architecture/08-crosscutting-concepts.md). Per the doc-layer-sweep lesson (PRs #178/#179), enumerate the layers explicitly during implementation and grep for any other "case-sensit" reference before declaring done.

---

## 8. Files touched

- `src/hangarfit/loader.py` — add `_suggest_plane_id` + `_resolve_known_plane_id`; wire the five call sites; replace the bare `:315` maintenance check; module-docstring note. (`import difflib`.)
- `tests/test_loader.py` — new layout cases; update `test_unknown_plane_reference_propagates`.
- `tests/test_loader_scenario.py` — new scenario cases; update `test_load_scenario_rejects_maintenance_plane_not_in_fleet_in`; light docstring reword on the null test.
- `docs/architecture/05-building-block-view.md` (+ possibly `08-crosscutting-concepts.md`) — case-sensitivity contract note.

No production-behaviour change beyond error messages and the *timing* of rejection (loader vs model). Valid layouts/scenarios are unaffected.

---

## 9. Alternatives considered (and why C)

- **A — document only.** Cheapest, zero risk, but leaves the misleading late error exactly as-is. Rejected as insufficient on its own; its documentation half is folded into C (§7).
- **B — case-fold on read.** Most forgiving, but high blast radius (fleet dict keys, every downstream compare in solver/visualize must normalise consistently), changes the id contract (silently merges case-distinct ids), and *still* needs typo diagnostics for non-case mistakes. Narrow benefit (fleet ids are already `lower_snake`) for broad cost. Rejected.
- **C — strict-validate + suggest (chosen).** Preserves case-sensitivity, fixes the whole near-miss class (case *and* typo) via one helper, surfaces early at the loader with the path and a suggestion, keeps the model invariants as backstop. ~30 LOC + tests.

## 10. Open questions resolved during design

- **difflib misses big case diffs** (`FOO`→`foo` scores 0.0 because `SequenceMatcher` is case-sensitive) → solved by the casefold-exact first pass in `_suggest_plane_id` (§3.1).
- **Don't lose the existing `add it to fleet_in` guidance** for scenario maintenance/constraints → carried in `fix_hint`, shown when there is no near match (§3.1, §3.3).
- **Scope** (layout-only per the issue's literal acceptance vs all five references) → comprehensive, approved 2026-05-25; the shared helper makes scenario coverage near-free and avoids a layout/scenario UX split.
