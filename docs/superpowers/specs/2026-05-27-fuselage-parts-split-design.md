# Fuselage front/aft split — design (issue #50)

**Date:** 2026-05-27
**Status:** Draft — design for review. **No code yet.** The user reviews
the contract and the two open decisions below before any implementation
issue is cut.
**Tracks:** [#50](https://github.com/DocGerd/hangarfit/issues/50) in
milestone *v0.4.0 — Parts model refinement*
**Author:** Claude (Opus 4.7)
**Touches (proposed):** `models.py`, `loader.py`, `collisions.py`,
`visualize.py`, `data/fleet.yaml`, fixtures, `CLAUDE.md`, arc42 §8,
a new ADR (ADR-0012), and a note on ADR-0001.

---

## 1. Problem statement

The Phase-1 parts model ([ADR-0001](../adr/0001-aircraft-parts-model.md))
represents each aircraft's fuselage as a **single** oriented rectangle.
The pairwise overlap rule ([§8 "The parts model"](../architecture/08-crosscutting-concepts.md#the-parts-model))
treats *any* wing-over-fuselage overlap at a separated height
(`z`-gap ≥ `wing_layer_clearance_m`) as **valid**. This is the
height-disjoint pass-through case, exercised positively by
`tests/fixtures/valid_high_over_low_z_disjoint.yaml`,
`valid_left_side_nesting.yaml`, `valid_right_side_nesting.yaml`, and pinned
by `tests/test_collisions.py::test_case_3_high_wing_over_low_fuselage_z_disjoint_valid`.

That rule is *geometrically* correct (the structures genuinely do not
touch) but **operationally too coarse**. A wing hanging over the
**aft fuselage / tail** of a parked plane is fine — that region is empty,
walkable, no canopy, no prop disc. A wing hanging over the **cockpit /
front fuselage** is not: it blocks the canopy, sits in/near the prop arc
on a tractor, and obstructs pilot ingress/egress. The model cannot tell
these two apart today because the fuselage is one undifferentiated box.

**The rule the user actually wants:**

> A wing may overhang another plane's **aft fuselage / tail** (empty,
> walkable) — but **not** its **cockpit / front fuselage** (controls,
> prop arc on tractors, canopy, pilot ingress/egress).

## 2. Goals and non-goals

### Goals

1. Split the monolithic fuselage into two part kinds — `fuselage_front`
   (spinner → section break near the wing root) and `fuselage_aft`
   (section break → tail) — so the collision rule can distinguish
   wing-over-cockpit from wing-over-tail.
2. A precise pairwise overlap contract: `wing × fuselage_front` is a
   hard conflict (independent of, or far stricter in, `z`);
   `wing × fuselage_aft` keeps today's `z`-gap behavior.
3. A migration path for the existing nine `fleet.yaml` entries and any
   hand-authored layouts that does not silently change a layout's
   verdict by accident.
4. Keep the change *internally consistent* with existing idioms: closed
   `PartKind` literal validated in `__post_init__`, alphabetical-sort
   conflict-kind taxonomy, fixture-driven regression tests, one ADR per
   load-bearing decision.

### Non-goals

- **`strut × cockpit/tail` stays unchanged.** Struts already bridge
  fuselage and wing and the strut rule is conservative (any plan-view
  overlap inside the strut's thin `z`-band trips). Splitting struts is
  out of scope (§7).
- **No `tail` as a third fuselage segment.** The existing `tail`
  `PartKind` is unused by every fleet entry today (all tail mass is
  folded into the fuselage box). This design does **not** repurpose
  `tail`; see §6 Q3 for why and the alternative considered.
- **No new clearance knob in `hangar.yaml`** unless the user picks
  Decision-1 option (b) (§4.1).
- **No real measurements.** Everything stays `measured: false`; the
  `wing_root_x_m` / section-break values are placeholders like every
  other dimension (CLAUDE.md "Open questions").

---

## 3. The contract

### 3.1 Geometry of the two segments

Both segments remain plane-local oriented rectangles with a height range,
exactly like every other `Part` — nothing new in the `Part` shape. What
changes is that one fuselage box becomes two, split at a longitudinal
station near the wing root.

In plane-local coordinates `+x` is forward (toward the nose). Let the
monolithic fuselage have centre `offset_x_m = c`, `length_m = L`, so it
spans `x ∈ [c − L/2, c + L/2]` (nose tip at `c + L/2`, tail tip at
`c − L/2`). Define a **section break** at plane-local station
`x = x_break` with `c − L/2 < x_break < c + L/2`. Then:

| Segment | x-span | length_m | offset_x_m | width_m / z / angle |
|---|---|---|---|---|
| `fuselage_front` | `[x_break, c + L/2]` | `(c + L/2) − x_break` | midpoint of its span | inherited from the source fuselage |
| `fuselage_aft` | `[c − L/2, x_break]` | `x_break − (c − L/2)` | midpoint of its span | inherited from the source fuselage |

Both segments keep the source fuselage's `width_m`, `z_bottom_m`,
`z_top_m`, `angle_deg`, and `offset_y_m`. The split is purely
longitudinal — the two boxes abut at `x_break` and reconstitute the
original footprint exactly (no gap, no overlap, area-conserving). This
is the key invariant: **front ∪ aft = the old fuselage**, so any rule
that fired on the old fuselage still has the same plan-view geometry to
fire on; only the *kind* of the overlapping region changes.

Where is `x_break`? Near the wing **trailing** edge / wing-root aft
station — the cockpit and everything forward of the main spar is
`fuselage_front`; the cabin-aft tube and empennage are `fuselage_aft`.
The exact value is a per-aircraft placeholder (see §4 migration). A
sensible default in the absence of measurement: the wing's trailing-edge
station `wing.offset_x_m − wing.length_m/2`, i.e. the aft edge of the
wing chord. This is consistent with the existing
`loader._wing_spar_x` precedent (#282), which already anchors strut
geometry to the wing chord rather than to a hand-typed station.

### 3.2 The new pairwise overlap rule

The pairwise predicate gains two cases. Both reuse the existing plan-view
distance test (`polygon_overlap` with `hangar.clearance_m`); they differ
only in the **height clause**.

| Part pair | Conflict kind (alphabetical) | Height clause |
|---|---|---|
| `wing × fuselage_aft` | `fuselage_aft_wing_overlap` | **unchanged**: `z`-gap `< wing_layer_clearance_m` (or `< 0` when clearance is 0) |
| `wing × fuselage_front` | `fuselage_front_wing_overlap` | **see Decision-1 below** |

Everything else (`wing × wing`, `strut × wing`, `fuselage_* × fuselage_*`,
etc.) keeps today's uniform two-clause predicate. The conflict-kind
string is still the two kinds sorted alphabetically with the `_overlap`
suffix; `"fuselage_aft"` sorts before `"fuselage_front"` sorts before
`"wing"`, so the taxonomy stays deterministic with no special-casing.

**Note on `fuselage_front × fuselage_front` and the like.** Splitting
fuselage into two kinds also splits the *fuselage-vs-fuselage* taxonomy:
`invalid_fuselage_fuselage.yaml` (case 5) will now emit
`fuselage_aft_fuselage_aft_overlap` and/or
`fuselage_aft_fuselage_front_overlap` / `fuselage_front_fuselage_front_overlap`
instead of one `fuselage_fuselage_overlap`. Two fuselages overlapping is
a conflict regardless of which segments touch (they are at the same
`z`-band by construction), so the *verdict* is unchanged — but the test's
expected-kind set and count change. This is a real audit item (§5), not
a no-op.

---

## 4. Decision-1: how `wing × fuselage_front` treats height

This is the headline contract choice the user must ratify. Two options,
both implementable as a one-line change in `collisions._parts_conflict`.

### Option (a) — hard conflict, `z` ignored *(recommended)*

`wing × fuselage_front` conflicts whenever the polygons are within
`clearance_m` in plan view, **regardless of the height gap**. A high
wing 0.5 m above the cockpit roof still trips.

- **Pro — matches the operational intent exactly.** The objection to
  wing-over-cockpit is not "the structures touch" — it is canopy access,
  prop-arc proximity, pilot ingress. None of those care whether the wing
  is 0.3 m or 1.5 m above the cockpit; a wing over the cockpit is
  unacceptable at *any* nesting height.
- **Pro — smallest predicate.** Drop the height clause for this one
  pair: `if pa/pb is (wing, fuselage_front): return plan_view_overlap`.
  No new config, no new threshold to measure or tune, no interaction with
  `wing_layer_clearance_m`.
- **Pro — robust to placeholder height data.** Every fleet height is a
  guess (`measured: false`). A rule that depends on the precise `z`-gap
  over the cockpit would give different verdicts as those guesses are
  refined; a `z`-independent rule is stable across re-measurement.
- **Con — slightly less expressive.** It cannot model a hypothetical
  hangar with a genuinely tall plane whose wing clears a short plane's
  cockpit by, say, 3 m. In a tight club hangar with 6–9 m planes this
  case is operationally irrelevant (you would never *want* a wing over a
  cockpit even with clearance), but option (b) could express it.

### Option (b) — large vertical clearance (`cockpit_clearance_m`)

`wing × fuselage_front` conflicts when `z`-gap `< cockpit_clearance_m`,
a new, much larger threshold (e.g. 2.0 m) than `wing_layer_clearance_m`
(0.2 m), configurable in `hangar.yaml`.

- **Pro — one uniform predicate shape.** Every pair is still
  "plan-view-close AND height-gap < some-threshold"; only the threshold
  varies by kind. Conceptually tidy.
- **Pro — expresses the tall-plane case.** A wing clearing a cockpit by
  more than `cockpit_clearance_m` would pass.
- **Con — invents a number nobody can measure.** What *is* the right
  cockpit clearance? It is a proxy for "the wing is high enough that a
  pilot can still climb in and the prop disc is clear," which is not a
  single height — it is a 3-D access cone. Picking 2.0 m is as arbitrary
  as picking ∞, but ∞ (option a) is at least honestly arbitrary and has
  no tuning knob to get wrong.
- **Con — new config surface.** Adds `cockpit_clearance_m` to `Hangar`,
  the loader default, `data/hangar.yaml`, the §8 clearance table, and the
  loader/model tests — for a knob whose value is a guess that, in
  practice, every realistic setting drives toward "always conflict."
- **Con — placeholder-height fragility** (see option a's third pro,
  inverted).

### Recommendation

**Option (a) — hard conflict, `z` ignored.** The domain objection to
wing-over-cockpit is categorical, not geometric: there is no nesting
height at which a wing over a cockpit becomes acceptable in a club
hangar. Option (a) encodes that directly, adds zero config, and is the
most robust to the project's pervasive placeholder measurements. Option
(b)'s extra expressiveness models a case (`wing clears a short cockpit
by metres`) that the operator would reject anyway. If a future hangar
ever genuinely needs the tall-plane carve-out, a later ADR can add
`cockpit_clearance_m` additively without invalidating option-(a) code —
the upgrade path is open.

---

## 5. Decision-2: schema migration

How do the nine existing `fleet.yaml` entries — each with one
`kind: fuselage` part — become front/aft? Two options.

### Option (a) — auto-split a legacy `kind: fuselage` at a per-aircraft `wing_root_x_m`

Keep `kind: fuselage` accepted in YAML. Add an optional per-aircraft
`wing_root_x_m` (plane-local station of the section break). The loader,
on seeing a `fuselage` part, splits it into a `fuselage_front` +
`fuselage_aft` pair at that station (defaulting, when absent, to the wing
trailing-edge station per §3.1) before constructing the `Aircraft`. The
constructed `Aircraft.parts` never contains a `fuselage` kind — exactly
the pattern the `struts:` block already uses (one high-level YAML
construct expands into canonical low-level Parts; ADR-0001).

- **Pro — backward compatible.** Every existing `fleet.yaml` entry and
  every hand-authored fixture/layout that embeds `data/fleet.yaml` keeps
  loading. The diff to `fleet.yaml` is one optional line per aircraft
  (or zero, if we accept the wing-trailing-edge default).
- **Pro — human-scale YAML.** An operator types one fuselage box plus
  one break station, not two hand-mirrored boxes whose `offset_x_m` /
  `length_m` must be kept area-conserving by hand (an error-prone
  arithmetic the loader should own — same argument ADR-0001 made for
  `struts:`).
- **Pro — single source of truth stays in the parts tuple.** The
  `fuselage` kind is a *transient YAML-schema* convenience, like
  `StrutsSpec`; the canonical geometry is still the expanded `Part`
  tuple, so there is no risk of front/aft desyncing from a leftover
  `fuselage` box.
- **Con — two ways to express the same plane.** A future entry *could*
  write explicit `fuselage_front`/`fuselage_aft` parts and skip the
  auto-split, or write `fuselage` + `wing_root_x_m`. Two valid input
  shapes is mild cognitive overhead. (Mitigation: document the
  auto-split as the canonical authoring path; explicit two-part
  declaration is the escape hatch for asymmetric/odd fuselages.)
- **Con — loader carries split arithmetic.** More loader code and more
  loader tests (the §3.1 invariant — front ∪ aft reconstitutes the
  original — must be tested end-to-end, as the `struts:` expansion is).

### Option (b) — require explicit `fuselage_front` / `fuselage_aft` declarations

Remove `kind: fuselage` from the accepted set. Every aircraft must
declare two parts. The loader does no splitting.

- **Pro — no hidden arithmetic.** What you type is what you get; the
  parts tuple mirrors the YAML one-to-one. Simplest loader.
- **Pro — one input shape.** No "two ways to say the same thing."
- **Con — breaks every existing file at once.** All nine `fleet.yaml`
  entries and every embedded-fleet fixture must be hand-edited in the
  same PR, with hand-computed `offset_x_m` / `length_m` for each segment
  kept area-conserving. This is exactly the manual, mirror-the-numbers
  burden ADR-0001 rejected for struts.
- **Con — regresses the YAML ergonomics ADR-0001 established.** The
  project already decided (struts) that high-level YAML constructs
  expanding to canonical Parts is the right authoring model. Option (b)
  walks that back for fuselages specifically, for no stated benefit the
  auto-split lacks.
- **Con — error-prone audit.** Nine hand-splits, each a chance to fat-
  finger a station and silently shift a verdict. Option (a) computes the
  split deterministically from one number.

### Recommendation

**Option (a) — auto-split at `wing_root_x_m`.** It is the direct analogue
of the `struts:` block the project already trusts: a readable YAML
convenience that the loader expands into canonical Parts, with the parts
tuple remaining the single source of truth. It keeps every existing file
loading, makes the `fleet.yaml` diff a one-line-per-plane (or zero-line)
change, and owns the area-conserving arithmetic in one tested place
rather than nine hand-edits. The "two input shapes" con is real but mild
and documented away; option (b)'s "breaks everything at once" con is the
more expensive trade.

**Backward-compatibility summary (option a):** existing layouts/fixtures
that reference `data/fleet.yaml` keep their *verdict* unless a wing
actually overlapped the now-front segment of another plane's fuselage —
which is precisely the behavior change #50 wants. The §5 audit (below)
walks every fixture to confirm which verdicts move and that each move is
intended.

---

## 6. Module impact map

| Module / file | Change |
|---|---|
| `src/hangarfit/models.py` | `PartKind` literal becomes `Literal["fuselage_front", "fuselage_aft", "wing", "strut", "tail"]` — note **`fuselage` is removed** from the *constructed* kind set (it survives only as a transient YAML keyword the loader expands; see §5a). `_VALID_PART_KINDS` updates automatically via `get_args`. The `Part.__post_init__` kind guard now rejects a raw `fuselage` (caught earlier and friendlier by the loader). Docstring on `Part` updated to list the five kinds. **No new fields on `Part`** — both segments are ordinary Parts. |
| `src/hangarfit/loader.py` | (Option a) Accept `kind: fuselage` in `_build_part` as a transient marker, **or** add a pre-pass in `_build_aircraft` that expands a `fuselage` entry (+ optional `wing_root_x_m`) into `fuselage_front`/`fuselage_aft` Parts before the `Part` constructor sees the disallowed kind. Mirrors `_expand_struts`. New helper `_split_fuselage(fuselage_part, wing_root_x_m) -> list[Part]` enforcing the §3.1 invariant (break station strictly inside the span; segments area-conserving). The `struts:` expansion finds the `wing` part (`loader.py:564`) — unaffected, struts still foot to the wing. |
| `src/hangarfit/collisions.py` | `_parts_conflict` gains the front/aft branch (Decision-1). The module docstring's taxonomy example list adds `fuselage_aft_wing_overlap` / `fuselage_front_wing_overlap`. `_build_pairwise_conflict` is unchanged (it already sorts kinds alphabetically and builds the string mechanically). **This file is geometry-adjacent — the `geometry-invariant-guard` subagent must review the PR** (CLAUDE.md subagents table). |
| `src/hangarfit/geometry.py` | **No logic change.** `WorldPart.kind` is `PartKind`, so the new literals flow through `aircraft_parts_world` untouched. Listed only so the guard subagent confirms the transform is not touched. |
| `src/hangarfit/visualize.py` | `_draw_part` (`visualize.py:352`) currently styles `kind == "fuselage" or kind == "tail"`. Update to style `fuselage_front` / `fuselage_aft` (and keep `tail`). **Recommended:** render `fuselage_front` a *slightly different shade* (e.g. a darker/warmer tint of the wing-position colour) so the cockpit boundary is legible at a glance; `fuselage_aft` keeps the current near-opaque fuselage fill. The `else: raise ValueError` stays — it is the loud-fail guard for an unhandled kind. **Gotcha:** `_draw_gear_glyph` (`visualize.py:451`) finds the fuselage part by `kind == "fuselage"` to size the wheel glyph; after the split there is no `fuselage` part. Must change to find `fuselage_front`/`fuselage_aft` (e.g. reconstruct the full fuselage span from both, or anchor the glyph to `fuselage_aft` for the tail wheel and `fuselage_front` for the nose wheel). This is a real, easy-to-miss breakage — the glyph code silently `return`s if no fuselage is found, so it would *not* error, it would just stop drawing wheels. Flag for the reviewer. |
| `data/fleet.yaml` | (Option a) Optionally add `wing_root_x_m` to each of the nine entries; or rely on the wing-trailing-edge default and change nothing but the header comment. Header comment block (lines 8–23) gains a note on the fuselage split, mirroring the existing `struts` note. Stays `measured: false`. |
| `CLAUDE.md` | The "parts model" Quick-Reference row's headline example changes from "wingtip over a low-winger's *fuselage*" to "wingtip over a low-winger's *tail*". Pointer to the new ADR added. |
| `docs/architecture/08-crosscutting-concepts.md` | "The parts model" section: closed `PartKind` set updated to five kinds; the two-clause-predicate paragraph gains the front/aft exception; the "high-wing over low-wing fuselage" example reworded to "over the aft fuselage / tail." The "Explicit conflicts" taxonomy examples gain the two new kinds. |
| `docs/adr/0001-aircraft-parts-model.md` | Add a "More Information" / superseded-note pointer to ADR-0012. ADR-0001's *core* decision (parts not bbox) is **not** superseded — the fuselage split is a refinement *within* the parts model, so ADR-0001 stays **Accepted** with a forward-link, not a status change. |
| `docs/adr/0012-*.md` (**new**) | Record the front/aft split: ≥2 options for Decision-1 (hard vs large-clearance) and Decision-2 (auto-split vs explicit), each with a concrete rejection reason, per ADR-0000's "≥2 considered options" discipline. This spec is the source material. |

---

## 7. Out of scope

- **`strut × fuselage_front` / `strut × fuselage_aft`.** Struts keep
  today's uniform rule. A strut already bridges fuselage and wing; the
  strut keep-out is conservative and there is no operational rule like
  "a strut may pass over a tail but not a cockpit." No new strut/fuselage
  cases.
- **Repurposing the `tail` kind.** `tail` stays a separate, currently-
  unused kind (§6 Q3). Folding the empennage into `fuselage_aft` is
  deliberate — the operational rule is "wing over the aft *region*,"
  and the aft region includes the tail; a separate `tail` segment would
  add a kind with no distinct rule.
- **3-D / cockpit access cone.** Decision-1(a) collapses the cockpit
  rule to "no wing over the front, period." A true ingress/prop-arc
  volume model is a future ADR if ever needed.
- **Real measurements / `wing_root_x_m` per plane.** Placeholder, same
  as all other dimensions.

---

## 8. Test plan

### 8.1 Case 3 reframed

`test_case_3_high_wing_over_low_fuselage_z_disjoint_valid` currently
asserts a wingtip over Fuji's **fuselage** is valid (z-disjoint). After
the split, *where* over the fuselage matters:

- The fixture `valid_high_over_low_z_disjoint.yaml` places `ctsl`'s wing
  over `fuji` at world `x ∈ [4.5, 5.5], y ∈ [5.7, 9.0]`. Whether that
  region is now `fuselage_front` or `fuselage_aft` depends on Fuji's
  `wing_root_x_m`. **Action:** re-derive which segment the overlap lands
  on. If it lands on `fuselage_aft`, the case stays *valid* and is
  renamed to `valid_high_over_low_aft_z_disjoint` (the headline positive
  control for the new rule). If it lands on `fuselage_front`, the
  placement is nudged aft (increase the overlapping plane's `y`) so the
  positive control genuinely tests aft nesting — the case must remain a
  *valid* control, because that is the behavior the split preserves.

### 8.2 Two new fixtures

- **`invalid_wing_over_cockpit.yaml`** — a wing placed over another
  plane's `fuselage_front`, at a *z-disjoint* height (so that under the
  *old* rule it would have been valid). Expected: exactly one
  `fuselage_front_wing_overlap` conflict (Decision-1a: fires despite the
  z-gap). This is the canary that the new rule actually bites. Header
  documents the world-coordinate overlap zone and the z-gap, in the style
  of `valid_high_over_low_z_disjoint.yaml`.
- **`valid_wing_over_tail.yaml`** — the same wing placed over the other
  plane's `fuselage_aft` at the same z-disjoint height. Expected: valid,
  zero conflicts. Paired with the cockpit fixture, the two differ only in
  the longitudinal station of the overlap — the cleanest possible
  demonstration of the front/aft distinction (mirrors the
  case-7/case-8 left/right symmetry idiom).

### 8.3 Audit checklist — every existing fixture

Each fixture that embeds `data/fleet.yaml` (i.e. exercises a real plane's
fuselage) must be re-evaluated. The split changes a *verdict* only where a
wing overlaps the now-`fuselage_front` segment of another plane; it
changes the *conflict-kind set* wherever any fuselage participates in a
pairwise conflict.

| Fixture | Pre-split expectation | Post-split action |
|---|---|---|
| `valid_high_over_low_z_disjoint` | valid (z-disjoint wing/fuselage) | §8.1 — re-derive segment; keep valid (possibly renamed/nudged) |
| `valid_left_side_nesting` (case 8) | valid (under-wing nesting, no strut hit) | Confirm Fuji's wing clears Cessna's `fuselage_front`; the overlap is wing×wing, not wing×fuselage, so likely **unchanged** — verify. |
| `valid_right_side_nesting` (case 7) | valid | Same as case 8 — verify the only fuselage interaction (if any) is over the aft segment. |
| `invalid_fuselage_wing_overlap` (case 4) | `fuselage_wing_overlap` (Fuji wing × scheibe fuselage, z-overlap) | Kind changes to `fuselage_aft_wing_overlap` and/or `fuselage_front_wing_overlap` depending on which scheibe segment the wing hits. Still invalid; update expected-kind assertion. |
| `invalid_fuselage_fuselage` (case 5) | exactly one `fuselage_fuselage_overlap`, count 1 | Kind set + count change (§3.2). Two overlapping fuselages → up to several `fuselage_{aft,front}_fuselage_{aft,front}_overlap` conflicts. Re-pin the exact expected set and count. |
| `invalid_strut_blocks_nesting` | `strut_wing_overlap` | Strut rule unchanged; **verify** no incidental wing×`fuselage_front` overlap was introduced by the geometry. |
| `invalid_wing_wing_same_height` | `wing_wing_overlap` | No fuselage involved — **unchanged**; smoke-verify. |
| `invalid_hangar_bounds` | `hangar_bounds` | Single-plane bounds rule — **unchanged**. |
| `invalid_bay_intrusion_wingtip` | `bay_intrusion` | Single-plane bay rule — **unchanged**. |
| `valid_two_separated` (case 1) | valid | Well-separated; **unchanged** — smoke-verify. |
| `valid_all_nine_planes` / `solve_all_nine_large_hangar` (case 12) | valid (test-only large hangar) | **Highest-risk audit item.** Nine planes packed; some wing-over-fuselage overlaps may now land on a `fuselage_front` and flip the layout to invalid. Re-run the checker; if it flips, nudge placements until valid again (it must stay the valid acceptance control) and document the nudge in the fixture header. |
| `valid_bay_*`, `valid_part_vertex_on_bay_edge`, `valid_partial_width_bay_*`, `valid_wall_vertex`, `valid_gear_glyph_smoke` | valid (bay/bounds/glyph controls) | Re-run; most are single-plane or well-separated — **unchanged**; smoke-verify. `valid_gear_glyph_smoke` also exercises the §6 visualize glyph gotcha — keep it as the glyph regression. |
| `solve_*` scenarios | various solver statuses | The solver calls `check()` internally; any scenario whose feasibility depended on a wing-over-front-fuselage being *legal* may change status. Re-run the solver matrix; re-pin determinism canaries (`tests/test_solver_canaries.py`) only with a conscious "yes, the rule changed" note in the PR (per §8 "Determinism canaries"). |

New collision tests in `tests/test_collisions.py`: a `front`-vs-`aft`
pair of methods asserting the §8.2 fixtures, plus an assertion that the
alphabetical kind ordering produces `fuselage_aft_wing_overlap` /
`fuselage_front_wing_overlap` (not the reversed forms), mirroring case-4's
`fuselage_wing_overlap`-not-`wing_fuselage_overlap` check.

New loader test: the §3.1 area-conservation invariant — load an aircraft
with a `fuselage` + `wing_root_x_m`, assert the expanded parts are one
`fuselage_front` + one `fuselage_aft` whose spans abut at the break and
union to the original (the `struts:` expansion has the analogous
end-to-end test).

---

## 9. Open questions for the user

1. **Decision-1 (height treatment of `wing × fuselage_front`):** ratify
   **option (a) hard conflict** (recommended), or pick **option (b)**
   large `cockpit_clearance_m`? (§4)
2. **Decision-2 (migration):** ratify **option (a) auto-split at
   `wing_root_x_m`** (recommended), or **option (b)** explicit two-part
   declarations? (§5)
3. **`wing_root_x_m` default:** if Decision-2a, is the wing
   trailing-edge station (`wing.offset_x_m − wing.length_m/2`) an
   acceptable default when the field is omitted, or should the field be
   **required** so every plane states its break explicitly (no silent
   default)? (Trade: zero-diff `fleet.yaml` vs. explicitness.)
4. **`fuselage_front` shade:** any preference for how the cockpit segment
   is tinted in the PNG (darker tint of the wing-position colour vs. a
   distinct hatch vs. an outline)? (§6 visualize row)
5. **`tail` kind:** confirm it stays a separate unused kind (the
   empennage is folded into `fuselage_aft`), rather than being pressed
   into service as the aft-most third segment. (§7)
6. **Scope coupling with #1 (example revision):** #50's issue body notes
   landing this alongside the `layouts/example.yaml` revision makes the
   new example a natural acceptance smoke test. Should the example
   revision ride in the same release, or stay a separate issue?
