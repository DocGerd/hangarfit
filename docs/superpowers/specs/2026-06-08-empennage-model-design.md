# Empennage model — design spec (epic #518 / #519 + #520)

- **Date:** 2026-06-08
- **Status:** Proposed (pending review)
- **ADR:** [ADR-0023](../../adr/0023-empennage-tail-surfaces.md) records the
  decision + rejected alternatives. This spec is the implementation companion:
  the data plan, the file-by-file change set, the test matrix, and the
  breaking-change audit.

## Problem

The collision model has no representation of the tail surfaces. The empennage is
implicitly the aft end of the fuselage rectangle — fuselage-tube-wide and
fuselage-tall. Two independent consequences (see ADR-0023 for the full framing):

- **#519 (lateral):** the real horizontal stabilizer spans ~2.5–3.5 m vs a
  ~0.85 m fuselage tube; the checker sees free space beside a ~3 m tailplane.
- **#520 (vertical, safety-critical):** the real fin rises to the aircraft's
  overall height (~1.7–2.3 m) — into the wing-nesting layer — while the aft box
  tops out at ~1.5 m, so a wing nested "valid" over a neighbour's tail can
  physically foul that plane's fin.

## Goal

Model both tail surfaces as honest oriented-rectangle `Part`s with real
z-extents, so the **existing** two-clause collision predicate produces the
physically correct verdict — including the lateral-clearance nuance (a wing
passing *outboard* of a centreline fin still nests; a wing passing *over* it does
not) and all three tail configurations (conventional, cruciform, T-tail), with
no per-type code.

## The model (per ADR-0023)

Two new parts per aircraft, both oriented rectangles with their own
`[z_bottom_m, z_top_m]`:

| Surface | `PartKind` | plan-view footprint | z-band | overhangable? |
|---|---|---|---|---|
| Horizontal stabilizer / elevator | `tail` *(existing kind, reused)* | `length = h_stab_chord` (fore-aft) × `width = h_stab_span` (lateral) | per config (below) | yes for low/cruciform (z-disjoint from overhanging wings); structurally a `tail` so it stays in `metrics._OVERHANGABLE` |
| Vertical stabilizer (fin) + rudder | `vertical_stabilizer` *(new kind)* | `length = fin_chord` (fore-aft) × `width ≈ 0.15` (thin, centreline) | fuselage-top → overall height (into the wing layer) | **never** — distinct kind, not in `_OVERHANGABLE` |

**Why it just works (no predicate change):** `collisions._parts_conflict`
already gates on plan-view overlap **then** the z-gap
`max(z_bottom) − min(z_top) < wing_layer_clearance_m`. A fin whose `z_top_m`
enters the wing band makes `wing × vertical_stabilizer` conflict *only when the
wing also overlaps the thin centreline fin in plan view*. Pass outboard → no
plan-view overlap → still legal. That is the lateral-clearance rule, for free.

## Geometry derivation rules (per aircraft)

The exact numbers are finalized at implementation by reading each plane's
existing `fuselage` and `wing` parts; the placement is mechanical:

- **Station (both surfaces).** Centre near the aft fuselage end:
  `offset_x_m ≈ (fuselage.offset_x_m − fuselage.length_m/2) + chord/2`. (Sanity
  check against the tailwheel station where present — e.g. `aviat_husky`'s third
  wheel sits at `offset_x_m = −4.80`, beside its tail.) `offset_y_m = 0`,
  `angle_deg = 0`.
- **Horizontal stabilizer (`tail`) z-band:**
  - *conventional-low / cruciform:* a thin band at/just below the aft-fuselage
    top, kept clearly below `wing.z_bottom_m − wing_layer_clearance_m` so it
    stays z-disjoint from overhanging high wings (remains overhangable). E.g.
    `aviat_husky` (fuselage top 1.5, wing bottom 2.0): `z ≈ [1.2, 1.5]` — gap to
    a 2.0 m wing is 0.5 m ≥ 0.2.
  - *T-tail (`stemme_s10`):* a thin band at the **fin top** (`z_top ≈ overall
    height`), i.e. inside the wing layer → an overhanging neighbour wing
    z-overlaps it → conflict.
- **Vertical stabilizer (`vertical_stabilizer`) z-band:** `z_bottom_m ≈
  fuselage.z_top_m` (fin root atop the aft fuselage), `z_top_m = published
  overall height`. E.g. `aviat_husky`: `z ≈ [1.5, 2.01]` — `z_top` reaches into
  the 2.0–2.3 m wing layer, so a neighbour wing nested over the centreline
  conflicts (gap ≈ −0.01).

All values are placeholders (`measured: false`), consistent with the rest of
`data/`. The fin `z_top` honestly equals the published overall height — even
where that yields a thin (~cm) overlap with the wing layer; we do not pad it to
manufacture a larger margin.

## Per-aircraft empennage data (researched; spans/chords are estimates)

`tail_config` and `overall_height_m` are sourced; `h_stab_span_m`,
`h_stab_chord_m`, `fin_chord_m` are published-spec-absent estimates
(h-stab span ≈ 33–40 % of wingspan for powered types; gliders sized absolutely).

| id | tail_config | h_stab_span_m | h_stab_chord_m | overall_height_m (→ fin top) | fin_chord_m |
|---|---|---|---|---|---|
| `aviat_husky` | conventional_low | ~3.0 | ~1.0 | 2.01 | ~1.3 |
| `scheibe_falke` | conventional_low | ~2.6 | ~0.9 | 1.68 | ~1.1 |
| `stemme_s10` *(herrenteich)* | **t_tail** | ~2.8 | ~0.8 | 1.80 | ~1.0 |
| `fuji` | conventional_low | ~3.2 | ~1.1 | 2.02 | ~1.3 |
| `wild_thing` | conventional_low | ~2.9 | ~0.9 | ~1.9 | ~1.0 |
| `zlin_savage` | conventional_low | ~2.8 | ~0.9 | 2.03 | ~1.1 |
| `cessna_140` | conventional_low | ~3.2 | ~1.1 | 1.91 | ~1.2 |
| `cessna_150` *(data only)* | conventional_low | ~3.4 | ~1.1 | 2.11 | ~1.4 |
| `ctsl` | **cruciform** (low/mid stabilator; NOT a T-tail) | ~2.6 | ~0.9 | 2.34 | ~1.2 |
| `fk9_mkii` | conventional_low | ~3.2 | ~0.9 | 2.15 | ~1.1 |

> **Research correction:** the Flight Design CTSL is **cruciform**, not a T-tail
> (low/mid all-flying stabilator + rudder over a small ventral fin). The fleet's
> only true T-tail is the **Stemme S10**. The CTSL is modelled like
> conventional-low (stabilizer below the wing layer); the Stemme's stabilizer
> sits at the fin top.

`stemme_s10` lives only in `examples/herrenteich/fleet.yaml`; `cessna_150` and
`fuji` live only in `data/fleet.yaml`. Both files get the tail surfaces.

## Change set (file by file)

| File | Change | Notes |
|---|---|---|
| `src/hangarfit/models.py` | add `"vertical_stabilizer"` to the `PartKind` `Literal` | `_VALID_PART_KINDS` auto-derives; docstring note |
| `data/fleet.yaml` | add `tail` + `vertical_stabilizer` parts to each aircraft | per the derivation rules + table; header comment |
| `examples/herrenteich/fleet.yaml` | same, incl. the `stemme_s10` T-tail | published-spec |
| `src/hangarfit/collisions.py` | **logic unchanged**; add a clarifying comment that `vertical_stabilizer` keeps the height clause and is not in the cockpit exception | audit only |
| `src/hangarfit/metrics.py` | confirm `_OVERHANGABLE` stays `{tail, fuselage_aft}` (do **not** add the fin) | likely no change |
| `src/hangarfit/visualize.py` | render the `tail` (already aft-fuselage-like) + a height cue for `vertical_stabilizer` | 2D |
| `src/hangarfit/scene.py` | emit the new parts into `scene/v1` JSON | by `kind` |
| `viewer/src/*.ts` → rebuild `src/hangarfit/_viewer_assets/viewer.js` | render `vertical_stabilizer` (+ `tail` if not already) | **must** rebuild bundle or `viewer-build-drift` CI fails |
| `tests/test_collisions.py` | the 3 golden cases (below) | — |
| `tests/test_models.py` / wherever the closed `PartKind` set is asserted | add `vertical_stabilizer` | — |
| `docs/architecture/08-crosscutting-concepts.md` | update "The parts model" (6 kinds; tail surfaces) | operational statement |
| `docs/adr/0012-...md` | amend the tail-fold-in Neutral consequence to point at ADR-0023 | + index entry for 0023 |
| `CHANGELOG.md` | breaking-change entry | list flipped fixtures |

**Not touched:** the collision predicate logic, the det(−1) transform,
`geometry.py` (`aircraft_parts_world` passes parts through unchanged),
`solver.py`, `towplanner.py`, the loader's expansion logic.

## Test matrix (`tests/test_collisions.py`)

Synthetic fixtures with clear numbers (not the real fleet) so the assertions are
unambiguous:

1. **Fin blocks nesting (the #520 safety case).** Plane B's wing nested over
   plane A's aft fuselage, the wing's footprint passing *over* A's centreline fin
   (fin `z_top` in the wing band) → exactly one `vertical_stabilizer_wing_overlap`
   conflict. This layout is silently `valid` today.
2. **Lateral clearance still valid (the #520 nuance).** Same nest, but B's
   wingtip passes *outboard* of A's thin centreline fin → no plan-view overlap
   with the fin → **valid**.
3. **Wide tailplane conflict (the #519 case).** A neighbour's low part
   (fuselage / strut / low wing) overlapping A's realistic-width `tail` in plan
   view at a shared z-band → `..._tail_overlap` conflict (free space under the
   old narrow model).
4. *(regression)* existing cockpit tests (`_is_wing_over_cockpit`) and the
   wing-over-`fuselage_aft` positive control still pass — the predicate gained no
   branch.
5. *(model)* the closed `PartKind` set now includes `vertical_stabilizer`.

Each new path needs ≥1 **non-slow** test (the `codecov/patch` gotcha in
CLAUDE.md).

## Breaking-change / fixture-flip audit

A required deliverable. Procedure:

1. Enumerate every fixture and example layout under `tests/fixtures/`,
   `examples/`, and `data/` that is currently `valid` and involves a
   wing-over-tail nest or a near-tail side-by-side arrangement.
2. After adding the tail surfaces, re-run `hangarfit check` on each; record which
   flip `valid → invalid` and the conflict kind.
3. **Expected to flip:** the canonical `valid_wing_over_tail` /
   `valid_high_over_low_aft_z_disjoint` positive control (that *is* the safety
   fix). Re-pin or rename those fixtures to reflect the new reality, with a note.
4. **Investigate, don't paper over:** check whether the real
   `examples/herrenteich/layout.yaml` all-eight arrangement flips. If it does,
   report it as a genuine finding (real tight fin/wing clearance vs. too-aggressive
   placeholder fin height) — do **not** tune fin heights to keep it green.
5. List every flipped fixture in the PR body + CHANGELOG.

## Out of scope / non-goals

- No 3D mesh (ADR-0001 stands); parts remain oriented rectangles with z-bands.
- No `empennage:` loader block — explicit `parts:` entries (ADR-0023 D4). A block
  can be added additively later if hand-authoring proves painful.
- No new clearance knob; the fin uses the existing `wing_layer_clearance_m`.
- T-tail vs cruciform vs conventional is expressed purely by per-part z; the
  model does **not** gain a `tail_config` field.

## Open questions

- **Horizontal-stabilizer thickness/z-band fidelity.** A thin band at the
  aft-fuselage top is the placeholder; if a real low-set tailplane should sit a
  little higher/lower per type, that is a measurement refinement, not a model
  change.
- **Herrenteich `layout.yaml` outcome** — resolved during the audit (step 4);
  may surface a follow-up issue if the real layout flips for a real reason.
