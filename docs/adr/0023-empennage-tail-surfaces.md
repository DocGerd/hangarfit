# ADR-0023: Model the empennage as explicit tail surfaces so a vertical fin in the wing layer can block wing-over-tail nesting

- **Status:** Proposed

- **Date:** 2026-06-08
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

Every aircraft in [`data/fleet.yaml`](../../data/fleet.yaml) is modelled with two
part kinds: `wing` and `fuselage` (the loader auto-splits the latter into
`fuselage_front` + `fuselage_aft`, [ADR-0012](0012-fuselage-front-aft-split.md)).
The empennage — the tail surfaces — is not represented at all; it is implicitly
just the aft end of the fuselage rectangle: as wide as the fuselage tube and as
tall as the fuselage's z-range. ADR-0012 recorded this deliberately ("the
empennage folds into `fuselage_aft` … a separate tail segment would add a kind
with no distinct rule", its Neutral consequence).

That assumption is wrong in two independent, collision-relevant ways:

- **Lateral span.** The real horizontal stabilizer / elevator spans ~2.5–3.5 m
  tip-to-tip, against `aviat_husky`'s ~0.85 m fuselage tube. In plan view the
  model sees free space on either side of the tail where a ~3 m tailplane is.
- **Vertical extent (safety-critical).** The real vertical stabilizer (fin) +
  rudder rises to roughly the aircraft's published overall height (~1.7–2.3 m
  for this fleet) — *into* the wing-nesting layer. The modelled aft-fuselage box
  tops out far lower (`aviat_husky`: 1.5 m). ADR-0012's wing-over-tail nesting —
  the core space-saving trick — lets a high wing overhang another plane's
  `fuselage_aft` because the two are z-disjoint (aft-fuselage top ≪ overhanging
  wing bottom). A real fin sits *in the very z-band the nested wing occupies*, so
  a layout the checker reports **valid** can **physically collide**, fin against
  wing.

This ADR records the model for the tail surfaces and, crucially, **how the
vertical fin interacts with wing-over-tail nesting** — the part of ADR-0012's
Neutral consequence it amends. It is a refinement *within* the parts model;
[ADR-0001](0001-aircraft-parts-model.md) and ADR-0012's front/aft split and
cockpit rule stay **Accepted**.

## Decision Drivers

- **The physical model must be sound.** A `valid` verdict must not hide a
  fin-against-wing collision. This is the safety driver and it is
  non-negotiable.
- **Express the lateral-clearance nuance.** A wing whose tip passes *outboard*
  of a narrow centreline fin genuinely clears it; a wing passing *over* it does
  not. Keep the former legal (it is real, hard-won packing density) and reject
  only the latter.
- **One model, every tail config.** The fleet has conventional-low tails, one
  cruciform (`ctsl`), and one T-tail (`stemme_s10`). The representation must
  cover all three with no per-type special-casing.
- **Robustness to placeholder measurements.** Every fleet dimension is a guess
  (`measured: false`); the rule must not hinge on precise unmeasured numbers.
- **Minimal blast radius on the guarded checker.** The collision predicate and
  the det(−1) transform are the project's most safety-sensitive code. Prefer a
  change that adds *data and parts*, not predicate branches.
- **Consistency with existing idioms.** Closed `PartKind` literal, the
  alphabetical conflict-kind taxonomy, the parts tuple as single source of
  truth.

## Considered Options

### D1 — how to decompose the empennage

1. **Two parts: a wide horizontal-stabilizer rectangle + a thin, tall
   vertical-fin rectangle**, each with its own `[z_bottom_m, z_top_m]`
   *(chosen)*.
2. **One enlarged empennage bounding box** — a single part as wide as the
   stabilizer *and* as tall as the fin.
3. **Status quo** — keep the empennage folded into `fuselage_aft`, no separate
   part. (The defect under repair; listed for completeness.)

### D2 — the fin's `PartKind`

1. **Add a `vertical_stabilizer` kind for the fin; reuse the existing `tail`
   kind for the horizontal stabilizer** *(chosen)*.
2. **Two `tail` parts** — model both surfaces as `kind: tail`, no new kind.
3. **Two new kinds** (`horizontal_stabilizer` + `vertical_stabilizer`), retiring
   bare `tail`.

### D3 — how the fin interacts with wing-over-tail nesting (the ADR-0012 amendment)

1. **Apply the existing two-clause predicate, unchanged, to
   `wing × vertical_stabilizer`** — nesting stays legal iff the wing clears the
   fin *laterally* (no plan-view overlap with the centreline fin); it conflicts
   when the wing passes over the fin and their z-bands are within
   `wing_layer_clearance_m` *(chosen)*.
2. **Hard conflict, `z` ignored** — any wing overhanging a plane that has a fin
   conflicts, mirroring the `wing × fuselage_front` cockpit rule.

### D4 — how the tail surfaces are authored

1. **Explicit `parts:` entries** per aircraft (`kind: tail`,
   `kind: vertical_stabilizer`) *(chosen)*.
2. **A new `empennage:` YAML block** the loader expands into the two parts, like
   `struts:`.

## Decision Outcome

**Chosen: D1 = option 1 (two parts); D2 = option 1 (`tail` + new
`vertical_stabilizer`); D3 = option 1 (unchanged predicate; legal iff laterally
clear); D4 = option 1 (explicit parts).**

The pivot is **D3**, and it is almost free. The collision predicate's z-clause is
already `gap = max(z_bottom) − min(z_top); conflict iff gap <
wing_layer_clearance_m` (`collisions._parts_conflict`). The reason it fails to
catch a fin today is *not* a flaw in that math — it is that no part's z-range
reaches the fin's true height. The moment a `vertical_stabilizer` part exists
whose `z_top_m` enters the wing band, `wing × vertical_stabilizer` yields
`gap ≤ 0` and the **unchanged** predicate returns a conflict — but only after its
mandatory first gate, plan-view overlap, is met. A wingtip passing outboard of
the thin centreline fin never overlaps it in plan view, so that nest stays
legal. The predicate therefore expresses "legal iff the wing clears the fin
laterally" with **zero new branches** — exactly the nuance D3 needs, and the
most robust outcome to placeholder heights (the verdict flips only when the
geometry genuinely intersects).

**D1 two parts**, because the tail over-extends in two *independent* dimensions
at two *different* heights: the stabilizer is wide and (usually) low; the fin is
narrow and tall. A single box (D1.2) cannot hold both truths — it would be both
stabilizer-wide and fin-tall, a solid no aircraft has, and would forbid every
wing-over-tail nest even where the wing passes outboard of the centreline fin.
Two parts each carry honest geometry.

**The two-part model handles all three tail configurations through data alone**,
with no per-type code, because the horizontal-stabilizer `tail` part carries its
own z-band:

- **Conventional-low** (`aviat_husky`, the Cessnas, `zlin_savage`, `fk9_mkii`,
  `scheibe_falke`, `fuji`, `wild_thing`): the `tail` plane sits below the wing
  layer → z-disjoint from an overhanging high wing → wing-over-tail nesting
  stays legal over the *stabilizer*; the stabilizer newly conflicts only with a
  neighbour's *low* parts (fuselage, strut, low wing) it now overlaps in plan
  view.
- **Cruciform** (`ctsl`): the stabilizer sits low-to-mid on the fin, still below
  the wing layer — modelled like conventional-low.
- **T-tail** (`stemme_s10`, the fleet's only one): the stabilizer sits at the
  fin top, *inside* the wing layer → a neighbour's overhanging wing z-overlaps
  it → conflict. Physically correct: you cannot tuck a wing over a T-tail.

In every case it is the same uniform predicate reading the part's z-range; the
configuration lives entirely in the data.

**D2 reuse `tail` + add `vertical_stabilizer`**, because `tail` already means,
throughout this codebase, "the overhangable low aft surface" — it is the kind in
`metrics._OVERHANGABLE`, the kind `visualize.py` renders like the aft fuselage,
and the subject of the "wing-over-tail clearance" readout. The horizontal
stabilizer *is* that surface, so reuse keeps those consumers correct with no
churn. The fin is the opposite — a hard obstacle that is *never* overhangable —
so it earns a distinct kind, which makes "fin keeps the height clause; it is not
a cockpit and not overhangable" structural rather than a comment.

**D4 explicit parts**, because the loader already constructs any kind in
`_VALID_PART_KINDS` via `_build_part`; authoring the two surfaces as plain
`parts:` entries keeps this breaking-change PR free of new loader logic,
allowlist surface, and expansion tests, and the per-part `z_bottom_m` /
`z_top_m` already expresses every tail configuration directly.

### Why not D1 option 2 (single empennage box)?

It collapses the two independent over-extents into one solid that is
simultaneously stabilizer-wide and fin-tall — a shape no aircraft has. Every
wing-over-tail nest would then conflict, including the legitimate ones where the
wingtip passes metres outboard of a ~0.15 m centreline fin. That throws away
real packing density (the whole point of wing-over-tail nesting) to model a
collision that is not there, and it cannot answer the epic's central question —
"does the wing clear the fin laterally?" — because it has erased the lateral
structure.

### Why not D1 option 3 (status quo)?

It is the defect. The model reports `valid` for a wing nested over a neighbour's
tail when a real fin rises into that wing's layer, and it sees free space beside
a ~3 m tailplane. ADR-0012's "the empennage folds into `fuselage_aft`, no
distinct rule" was a reasonable Phase-1 simplification; this ADR is the additive
upgrade ADR-0001 anticipated ("a later ADR can supersede … the upgrade path is
additive").

### Why not D2 option 2 (two `tail` parts)?

`tail` is wired into `metrics._OVERHANGABLE` and rendered by `visualize.py` as a
low aft surface. A fin tagged `tail` would be wrongly counted overhangable and
mis-rendered, forcing a z-aware special-case in exactly the consumers a single
kind was meant to keep simple — defeating the simplicity that motivated the
option. A distinct kind makes the fin's "never overhangable, always keeps the
height clause" nature structural.

### Why not D2 option 3 (retire `tail` for two new kinds)?

It renames a kind that is already correct. `tail` already denotes the
overhangable low aft surface across `metrics`, `visualize`, the scene/viewer
readout, and the "wing-over-tail clearance" terminology; retiring it churns all
of those — and the user-facing readout name — for no behavioural gain over
reusing it for the horizontal stabilizer.

### Why not D3 option 2 (hard conflict, `z` ignored)?

It is less faithful *and* more code. It would forbid a wing from nesting over the
tail of any finned aircraft even when the wingtip passes outboard of a narrow
centreline fin — a real, safe, dense arrangement — costing packing density for
no safety gain. And unlike the chosen option it requires a *new* special-case
branch in the predicate (a second `_is_wing_over_*` classifier), enlarging the
guarded checker. The cockpit rule (ADR-0012 D1) earned its z-drop because
wing-over-cockpit is *categorically* unacceptable at any height; wing-over-fin
is not categorical — it depends on lateral clearance, which the unchanged
predicate already measures.

### Why not D4 option 2 (`empennage:` loader block)?

It adds loader surface — an allowlist entry, an `_expand_empennage` expander, its
validation, and its tests — to a PR that is already a breaking change across
data, model, render, and tests. The per-part z fully expresses every
configuration without it, so the block buys only reduced hand-authoring
boilerplate (two parts per plane). If that authoring proves painful as the fleet
grows, a later ADR can add the block additively, exactly as `struts:` was. YAGNI
for now.

## Consequences

### Positive

- The safety defect is fixed *structurally*: a fin reaching into the wing layer
  now blocks the nest it would physically foul, via the existing predicate.
- All three tail configurations (conventional, cruciform, T-tail) are handled by
  data, with no per-type code branch.
- The lateral-clearance nuance is preserved: a wing that genuinely passes
  outboard of a centreline fin still nests.
- Blast radius is small and avoids the most safety-sensitive code: **no change**
  to the collision predicate's logic, the det(−1) transform, the solver, or the
  tow-planner. The change is `+1 PartKind`, fleet data, rendering, and tests.

### Negative

- **Breaking change to the validity contract.** Currently-"valid" layouts where
  a wing nests over a fin (or over a T-tail's high stabilizer), or where a
  neighbour's low part overlaps a now-realistic ~3 m tailplane, flip to
  **invalid**. This is intended — it is the defect surfacing — and the flipped
  fixtures are enumerated in the PR.
- Packing density drops wherever a fin or wide tailplane now blocks an
  arrangement the old model allowed.
- Each aircraft gains two placeholder tail parts to author and maintain
  (`measured: false`), and the viewer bundle must be rebuilt (`viewer.js`) to
  render them.

### Neutral

- **Amends ADR-0012's Neutral consequence.** ADR-0012 stated the empennage
  "folds into `fuselage_aft` … a separate tail segment would add a kind with no
  distinct rule." That is now false: the tail surfaces are separate parts, and
  the fin *does* exercise a distinct outcome — though via the *uniform*
  predicate, not a new rule. ADR-0012's front/aft split and the
  `wing × fuselage_front` cockpit rule are unchanged and stay Accepted.
- `fuselage_aft` still exists and still carries the aft fuselage tube; the legacy
  `kind: fuselage` auto-split (ADR-0012 D2) is untouched. The tail surfaces are
  *additional* parts alongside it.
- `PartKind` stays a closed `Literal`, now six kinds; the alphabetical
  conflict-kind taxonomy auto-derives `tail_wing_overlap`,
  `vertical_stabilizer_wing_overlap`, `fuselage_aft_tail_overlap`, etc. with no
  taxonomy code change.

## Compliance

- **`tests/test_collisions.py`** — three golden cases pin the model:
  1. a wing nested over a neighbour's aft fuselage that passes *over* that
     plane's fin → exactly one `vertical_stabilizer_wing_overlap` conflict (the
     case silently accepted today);
  2. the same nest with the wingtip *outboard* of the fin → still valid
     (lateral-clearance pass-through);
  3. a neighbour's low part (fuselage / strut / low wing) overlapping a
     realistic-width horizontal stabilizer in plan view at a shared height →
     conflict.
- **The closed `PartKind` set** is asserted in the model tests; the new
  `vertical_stabilizer` member is added there.
- **`_is_wing_over_cockpit` stays pinned to `{wing, fuselage_front}`** — verified
  by the existing cockpit tests continuing to pass and by the new fin retaining
  the height clause; the predicate gains no branch.
- **Fixture-flip audit.** The PR documents every fixture whose validity changes,
  including whether the real [`examples/herrenteich/layout.yaml`](../../examples/herrenteich/layout.yaml)
  all-eight arrangement flips.
- No automated determinism / geometry guard is *triggered* (no `solver.py` /
  `towplanner.py` / transform change), but the parts model is the subject, so the
  relevant review subagents run at PR review.

## More Information

- Related ADRs: [ADR-0001](0001-aircraft-parts-model.md) (the parts model this
  refines), [ADR-0012](0012-fuselage-front-aft-split.md) (front/aft split +
  wing-over-tail nesting; its tail-fold-in Neutral consequence is amended here),
  [ADR-0002](0002-determinant-minus-one-transform.md) (the transform left
  untouched).
- Related spec:
  [`docs/superpowers/specs/2026-06-08-empennage-model-design.md`](../superpowers/specs/2026-06-08-empennage-model-design.md).
- Related issues: epic [#518](https://github.com/DocGerd/hangarfit/issues/518),
  [#519](https://github.com/DocGerd/hangarfit/issues/519) (horizontal
  stabilizer), [#520](https://github.com/DocGerd/hangarfit/issues/520) (vertical
  fin, safety-critical).
- [§8 Crosscutting Concepts — "The parts model"](../architecture/08-crosscutting-concepts.md#the-parts-model)
  — the operational statement, updated alongside this ADR.
