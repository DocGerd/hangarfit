# ADR-0031: Mover pin — a path-less keep-out for a hand-placed `placed_routed_mover`

- **Status:** Accepted

- **Date:** 2026-07-04
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

Since #604 a `placed_routed_mover` (a car or towed trailer, ADR-0025) is a
full RR-MC search citizen in `solve`: the solver samples its pose, perturbs
it in the min-conflicts descent, and routes it with `plan_fill`. There is
**no way to hand-place one at an exact spot** — a Scenario's `ground_objects:`
entry for a mover rejects an authored pose outright (a #604 loader rule, not
an ADR-0025 restriction). So a club cannot say "park the Caddy *here*" as
part of an exception layout, and the #911 drag-to-fix editor gizmo has
nothing to move for a mover (only aircraft carry a pinnable pose).

This ADR records the decision reached in #912 (PR A, the backend half): the
semantics of an optional **mover pin** — a hand-authored resting pose the
solver honors instead of searching — and how it reuses the existing
`hand_placed` path-less machinery (#667 Rung A) rather than introducing a
second keep-out mechanism.

## Decision Drivers

- **Matches how the club actually operates.** "Park the Caddy here" is an
  exact, chosen spot, not a soft steer — the existing `region_preferences`
  soft term (ADR-0008 #604 amendment) is the wrong shape for this ask.
- **Reuse over new machinery.** #667 Rung A already generalizes
  `hand_placed=True` as a path-less-keep-out marker for a dolly-borne
  aircraft; extending it to movers needs no new solver/planner concept.
- **Coarse-grid honesty.** The tow-planner's deployed 0.5 m / 15° Reeds–Shepp
  grid can produce false unroutable verdicts on a physically fine spot (the
  documented fk9↔cessna grid-lock class, #844). A pin should never trigger a
  spurious exit-3 from that coarse search.
- **Preserve mover identity.** A pinned mover must still render as a mover,
  join the pairwise collision loop, and — if flagged `hard_door_mover` — stay
  subject to the ADR-0026 Caddy egress gate. It is a *placed* mover, not a
  reclassified static obstacle.
- **Byte-identical when unpinned (ADR-0003).** An empty `mover_pins` map must
  leave sampling, the descent's movable set, and routing untouched.

## Considered Options

1. **Path-less keep-out via generalized `hand_placed`** — the pinned mover
   is seated at its pin, excluded from the search, and rendered/routed
   around as a static body; no tow route is computed for it *(chosen)*.
2. **Reuse `fixed_obstacle`** — build the pin as a `Scenario.fixed_obstacle_placements`
   entry instead of a `placed_routed_mover` placement.
3. **A tow-routed pin** — still compute and verify a tow path to the pin, so
   an unreachable pin is caught by the planner rather than silently accepted.

## Decision Outcome

**Chosen: Option 1 — path-less keep-out via generalized `hand_placed`.**

`Scenario` gains `mover_pins: Mapping[str, Placement] = {}`, keyed by mover
id. Each value is `Placement(plane_id=<mover_id>, x_m, y_m, heading_deg,
on_carts=False, hand_placed=True)` — the 3-field pose already used for a
`fixed_obstacle`'s scenario pose (no `on_carts`; movers never ride carts).
`Scenario.__post_init__` requires `mover_pins.keys() ⊆ mover_ids` and that
`mover_pins.keys()` is disjoint from `region_preferences.keys()` — a pin
fixes position; a region preference steers a *searched* position, so the two
are mutually exclusive per object.

`Layout.__post_init__`'s existing guard (originally: `hand_placed=True` on
*any* `ground_object_placement` is rejected as meaningless, since the tow
planner only special-cased aircraft placements) is **relaxed**: it now
allows `hand_placed=True` on a `placed_routed_mover` placement and still
rejects it on a `fixed_obstacle` (a fixed obstacle is positioned by its pose
alone — it has no motion concept, so `hand_placed` there would be silently
ignored).

Wiring, end to end:

- **Solver sampling / descent (`solver.py`).** `pinned_planes` (which already
  excludes a pinned aircraft from the min-conflicts movable set) is widened
  to also include `scenario.mover_pins.keys()`. The initial-placement
  short-circuit that returns a pinned aircraft's pose verbatim is extended to
  consult `mover_pins` for a mover id.
- **Path-less rendering + keep-out (`towplanner.py`).** The `plan_fill` mover
  loop, on a `hand_placed` ground-object placement, appends a `Move` with
  `path=None` instead of calling `plan_path` — `scene._timeline` already
  renders any `Move` with `path is None` path-less, so no scene/viewer change
  is needed. The same hand-placed mover is added to the static
  `fixed_obstacle_placements` obstacle tuple the **aircraft** router consults
  (aircraft are routed before the mover loop), so aircraft also route around
  a pinned car — faithful #667-Rung-A keep-out parity between aircraft and
  movers.
- **Collision + egress (unchanged).** The pinned mover is still a normal
  `placed_routed_mover` placement in `layout.ground_object_placements`, so it
  still joins the pairwise overlap loop like any mover, and — if
  `hard_door_mover` — the ADR-0026 egress gate still fires for it: that gate
  keys off the mover's **resting pose** (it computes a fresh door→pose search;
  it never consumes the fill route), so a pinned, path-less Caddy is
  egress-gated with **no change to the gate itself**.
- **Determinism (ADR-0003).** Every new code path is gated on `mover_pins`
  being non-empty; an empty map leaves `pinned_planes`, sampling, and routing
  byte-identical to before #912. The determinism-guard double-solve canary on
  an unpinned scenario is unaffected.

### Why not Option 2 (reuse `fixed_obstacle`)?

Building the pin as a `fixed_obstacle` would inject it via
`Scenario.fixed_obstacle_placements` and **bypass the search entirely while
dropping its mover identity**: it would not appear in the routing
enumeration, would not be subject to the `hard_door_mover` egress gate (that
gate only iterates mover-class placements), and would render with
`fixed_obstacle` semantics (e.g. `MOVER_3D` vs. static-obstacle rendering)
instead of as a mover. A pinned Caddy must still *be* a Caddy — the whole
point is that it still needs to prove it can drive out.

### Why not Option 3 (a tow-routed pin)?

A tow-routed pin is more rigorous in principle — verify the mover can
actually reach its pin — but risks the coarse 0.5 m / 15° Reeds–Shepp grid
declaring a physically fine spot unroutable (the same grid-geometry-lock
class documented for the fk9↔cessna corridor, #844). "I parked it here" is
an assertion about the resting pose, not a request to route *to* it; a false
exit-3 on a valid pin would make the feature actively worse than not having
it. The path-less keep-out also directly reuses the #667 Rung A machinery
with no new planner concept.

## Consequences

### Positive

- The club can hand-place a car/trailer at an exact pose in a Scenario
  (`ground_objects: [{object: <id>, x_m, y_m, heading_deg}]`), matching how
  the fleet is actually parked day to day.
- Zero new keep-out mechanism: the pin rides the existing `hand_placed`
  path-less-keep-out path (#667 Rung A), the existing pairwise collision
  loop, and the existing ADR-0026 egress gate unchanged.
- A pinned mover never produces a spurious tow-unroutable verdict from the
  coarse grid, because no route to it is ever attempted.
- This is the backend seam the #912 PR B drag-to-fix editor gizmo consumes
  verbatim (the same `POST /convert` pose round-trip #911 already built for
  aircraft).
- Byte-identical when no mover is pinned — no regression on any existing
  fixture or solver seed (ADR-0003).

### Negative

- A pinned mover's validity is only as good as the pin: an overlapping or
  egress-blocking pin is caught at verification time (invalid layout / exit
  3 from the egress gate), not prevented up front — the same "garbage in,
  rejected out" contract every hand-authored pose already has.
- `Layout.__post_init__`'s guard is now class-conditional
  (`placed_routed_mover` vs. `fixed_obstacle`) rather than a blanket reject,
  a small increase in invariant surface area.

### Neutral

- A mover pin and a `region_preference` are mutually exclusive per object
  (enforced by `Scenario.__post_init__`) — this is a modeling choice, not a
  limitation of either mechanism; a pin fixes position, a region preference
  steers a searched one.
- The mover keeps its full `placed_routed_mover` class membership (rendering,
  collision, egress) — pinning changes *how its pose is chosen*, not *what it
  is*.

## Compliance

- **`tests/test_models_ground_object.py`** / **`tests/test_models.py`** —
  `Layout.__post_init__` rejects `hand_placed=True` on a `fixed_obstacle` but
  accepts it on a `placed_routed_mover`; `Scenario.mover_pins` invariants
  (`mover_pins.keys() ⊆ mover_ids`, disjoint from `region_preferences.keys()`,
  each pin's `plane_id` matches its key).
- **`tests/test_loader*.py`** — a Scenario mover with a pose loads into
  `mover_pins[id]` with `hand_placed=True`; a pin on a non-mover id, or a pin
  together with a `region_preference` on the same object, raises
  `LoaderError`.
- **`tests/test_solver*.py`** — a pinned mover seats at its pin and is
  excluded from the descent's movable set; the rest of the fill routes
  around it as a path-less body (no `Move.path` emitted for it); a pinned
  `hard_door_mover` that blocks the door is still invalid via the egress
  gate. The determinism-guard double-solve canary on an **unpinned**
  scenario stays bit-identical.

## More Information

- Related ADRs: [ADR-0002](0002-determinant-minus-one-transform.md) (Python
  owns the transform both ways, including the #911 pose round-trip this
  feature's editor half reuses), [ADR-0003](0003-rr-mc-solver-algorithm.md)
  (determinism contract — empty-`mover_pins` byte-identity),
  [ADR-0025](0025-ground-object-taxonomy.md) (ground-object taxonomy — the
  `GroundObject`/`placed_routed_mover` model this ADR amends; see its
  "Amendments" note), [ADR-0026](0026-caddy-hard-door-egress.md) (the Caddy
  egress gate, unchanged — it keys off the resting pose, not the route).
- Related spec:
  [`docs/superpowers/specs/2026-07-04-912-mover-pin-design.md`](../superpowers/specs/2026-07-04-912-mover-pin-design.md).
- Related issues: #912 (this ADR; epic #436, milestone #39), #604 (movers as
  full RR-MC search citizens — the loader rule this relaxes), #667 (Rung A —
  the aircraft-only `hand_placed` path-less keep-out this generalizes), #911
  (drag-to-fix `POST /convert` pose round-trip, reused verbatim by the #912
  PR B editor half), #603/ADR-0026 (Caddy hard-door egress gate, unchanged).
- [§8 Crosscutting Concepts — "The parts model" / "Ground objects"](../architecture/08-crosscutting-concepts.md#the-parts-model)
  — amended alongside this ADR to note the optional pin.
