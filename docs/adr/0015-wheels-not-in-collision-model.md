# ADR-0015: Wheels do not participate in the static collision model (gear stays render/motion data)

- **Status:** Proposed
- **Date:** 2026-05-31
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

Wheel positions became canonical per-aircraft data in [ADR-0013](0013-wheels-canonical-data.md):
`data/fleet.yaml` now carries, per aircraft, a `wheels:` block — main gear at
plane-local `(main_offset_x_m, ±track_m/2)` and an optional third (nose/tail)
wheel at `(third_wheel_offset_x_m, 0)`. The visualizer draws gear glyphs from
these, and `turn_radius_m` is cross-checked against the implied wheelbase at
load.

But the collision checker has **never** known about wheels. The parts model
([ADR-0001](0001-aircraft-parts-model.md), refined by
[ADR-0012](0012-fuselage-front-aft-split.md)) represents an aircraft as a list
of `Part` rectangles — `fuselage_front`, `fuselage_aft`, `wing`, `strut`,
`tail` — each with a plan-view footprint and a z-range; `collisions.check`
iterates only over those. `src/hangarfit/geometry.py` and
`src/hangarfit/collisions.py` contain **zero** references to wheels.

The question was raised in issue
[#322](https://github.com/DocGerd/hangarfit/issues/322)'s "Knock-on effects"
note:

> The parts model (ADR-0001, ADR-0012) doesn't currently include wheels.
> Whether wheels participate in collision (they probably should: a wheel pad is
> part of the plane's footprint) is a separate decision worth recording in an
> ADR.

[ADR-0013](0013-wheels-canonical-data.md) flagged the same open question — its
D1.3 option, left as "a future decision; if revisited, this ADR is the starting
point". This ADR settles that question so the omission is an intentional, citable
decision rather than an apparent gap. **The question:** should a wheel /
wheel-pad contribute to an aircraft's footprint for static collision purposes?

## Decision Drivers

- **Fidelity vs. over-constraint.** The checker exists to reject *physically
  invalid* parking. It should catch real conflicts without rejecting layouts a
  human would happily park (real aircraft sit with wheels close together).
- **The hangar's actual geometry.** It is a deep, **stack-style** hangar with a
  single front door: planes are parked nose-in, towed one at a time into a
  fore/aft stack — not parked side-by-side, fuselage-to-fuselage.
- **The data we actually have.** ADR-0013 models wheels as **points**, not
  areas. There is no wheel-pad width/radius datum anywhere in `fleet.yaml`, and
  every existing dimension is already an unmeasured placeholder
  (`measured: false`).
- **Where wheel geometry actually bites.** The load-bearing consumer of wheel
  positions is the **tow-path planner** (turn radius, the swept motion fan) —
  a *dynamic* clearance concern — not the *static* parking-validity check.
- **The determinism / canary cost.** Any new collision-bearing part changes
  what the checker reports, which forces regeneration of the solver determinism
  canaries and re-validation of every routability fixture (ADR-0003 contract).

## Considered Options

1. **Keep wheels out of the static collision model** (status quo, now
   documented). Wheels remain render + motion-model data; static validity stays
   "wing / fuselage / strut footprints, with z-nesting."
2. **Add wheel pads as collision-bearing parts.** Derive a pad rectangle (or
   disc) per wheel from the `wheels:` coordinates plus a **new** per-aircraft
   pad-size datum, give it a ground-anchored z-range (`0 .. axle height`), and
   feed it through `aircraft_parts_world` / `check` like any other `Part`.
3. **Wheels participate only in a future tow-clearance check, never in static
   collision.** Static parking validity stays as Option 1; wheel footprint
   enters the model later, scoped to the planner's swept-path clearance.

## Decision Outcome

Chosen option: **Option 1 — keep wheels out of the static collision model**,
recorded deliberately, with **Option 3 named as the likely future home** for
wheel geometry if and when it is needed. Reasoning:

- **The modelled footprint already subsumes the wheels in this hangar's
  geometry.** In a nose-in fore/aft stack, the parts that come close between
  two aircraft are wings (laterally wide, often z-nested) and fuselages
  (caught by the cockpit/tail rules). The main-gear *track* can be wider than
  the fuselage, so the one case wheels could add is two fuselages parked
  **side-by-side, laterally close, and longitudinally aligned** — but that
  arrangement also aligns their wide wings (caught by the wing rules) and is
  not how this stack-style hangar is loaded. The marginal conflict wheels would
  catch is near-empty in practice.
- **We would have to invent data to do it.** Option 2 needs a pad-size field
  that does not exist; ADR-0013 deliberately kept wheels as points. Adding
  collision pads on top of placeholder point data would manufacture
  high-confidence-looking constraints from numbers nobody has measured —
  exactly the "two surface representations disagree" trap ADR-0013 closed,
  re-opened in a new place.
- **The natural consumer is the planner, not the checker.** Wheel geometry
  earns its keep in *swept-path* clearance (does the gear clear a neighbour
  while being towed past?), which is dynamic. Folding it into static validity
  conflates "can it be parked here" with "can it be moved to here" — a
  distinction the project already draws (exit-3 tow-routability is separate
  from exit-1 static invalidity).
- **It is the reversible choice.** Option 1 keeps the door open for Option 3
  without paying the canary-regeneration / fixture-re-validation cost now, for a
  conflict class the current hangar geometry rarely produces.

This is a genuine modelling decision, not a doc edit: **merging this ADR
ratifies "wheels are not a collision part."** If you would rather adopt Option
2 (wheels collide now), say so and this ADR is reworked to record that instead
— the Option-2 mechanism is sketched above and in *Negative Consequences*.

### Positive Consequences

- The wheels-in-collision question is **settled and citable**; the absence of
  wheels from `collisions.py` is intentional, not an oversight.
- No new unmeasured data, no change to `check` output, no canary regeneration,
  no fixture re-validation — zero risk to the ADR-0003 determinism contract.
- Keeps the static checker's footprint = "structure the aircraft *is*" (wing,
  fuselage, strut), cleanly separated from "how it *moves*" (gear/turn radius).

### Negative Consequences

- **A real (if narrow) conflict class is unmodelled:** two aircraft whose gear
  tracks overlap while their wings/fuselages clear (laterally-close,
  longitudinally-aligned side-by-side parking). Mitigation: this is not the
  stack-style hangar's normal arrangement; revisit via Option 3 if real
  measurements or a non-stack layout make it bite.
- If Option 3 is later adopted, wheel footprint must be derived **consistently**
  with the ADR-0013 point coordinates (a pad expanded around each canonical
  point), and a pad-size datum added per aircraft — tracked as future work, not
  done here.

## Compliance

- `grep -r wheel src/hangarfit/collisions.py src/hangarfit/geometry.py` returns
  nothing — the checker and the transform remain wheel-free by design. This ADR
  is the rationale a reader finds when they notice that and ask "why?".
- No test or behavioural change accompanies this ADR; it documents the existing
  boundary. A future Option-3 PR would add the tests.

## More Information

- [ADR-0001](0001-aircraft-parts-model.md) — the parts model (collision unit).
- [ADR-0012](0012-fuselage-front-aft-split.md) — fuselage front/aft split.
- [ADR-0013](0013-wheels-canonical-data.md) — wheels as canonical point data
  (which deferred this question).
- [ADR-0003](0003-rr-mc-solver-algorithm.md) — the determinism contract a
  collision-rule change would disturb.
- Issue [#353](https://github.com/DocGerd/hangarfit/issues/353) — this decision.
- Issue [#322](https://github.com/DocGerd/hangarfit/issues/322) — where the
  question was raised.
