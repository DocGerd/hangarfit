# ADR-0002: The plane-local-to-world transform has determinant −1 (intentional left-handed mapping)

- **Status:** Accepted
- **Date:** 2026-05-23
- **Deciders:** [@DocGerd](https://github.com/DocGerd)

## Context & Problem Statement

`hangarfit` represents every aircraft as a list of plane-local parts (see
[ADR-0001](0001-aircraft-parts-model.md)) and places each aircraft
in the hangar via a `Placement(x_m, y_m, heading_deg)`. The collision
checker, the solver, and the visualizer all consume world-coordinate
geometry, so a single transform must map every plane-local part to its
world position.

That transform is implemented in
[`src/hangarfit/geometry.py`](../../src/hangarfit/geometry.py)
(`aircraft_parts_world`) as:

```
world_x = px + u·sin(h) + v·cos(h)
world_y = py + u·cos(h) − v·sin(h)
```

where `(u, v)` is plane-local (forward, right), `(px, py, h)` is the
placement, and the linear part of the map is the 2×2 matrix
`[[sin h, cos h], [cos h, −sin h]]`. Its determinant is
`sin h·(−sin h) − cos h·cos h = −sin²h − cos²h = −1`.

A reader trained on standard 2D graphics will look at this matrix, notice
it is *not* the textbook CCW rotation `[[cos α, −sin α], [sin α, cos α]]`,
and reach for the rebase button. **They will be wrong.** The form is
deliberate, the determinant is supposed to be −1, and the implementation
encodes two simultaneous convention flips (compass-vs-math, world-frame
handedness). This ADR exists to stop the "correction" that turns a
working transform into a silently broken one, and to make the *why*
discoverable from the code rather than reachable only through CLAUDE.md
or a synchronous conversation with the maintainer.

## Decision Drivers

- **World coordinates must match how a human sees the hangar.** A person
  standing in front of the open door sees `+x` going right along the
  door and `+y` going deeper into the building. Layout YAMLs and PNG
  renders both have to read naturally in that frame.
- **Plane-local coordinates must match aviation convention.** A pilot
  thinks of "forward" (toward the nose) and "right" (toward the
  starboard wingtip). The YAML offsets in `data/fleet.yaml` are easier
  to author and review when the local axes obey that convention.
- **Compass headings (CW positive from north) are how the layout
  authors think about orientations**, not standard math angles
  (CCW positive from `+x`). Mixing the two conventions silently is
  the project's headline trap.
- **Once two convention choices are fixed, the matrix follows.** The
  linear map's handedness is determined, not chosen — given the
  desired axis conventions, the resulting transform must compose a
  rotation with a reflection. We cannot have all three of "natural
  world frame," "natural plane-local frame," and "pure rotation
  matrix" simultaneously.
- **Future contributors will, at some point, want to "fix" this.** The
  decision drivers above explain why a fix is the wrong move; the
  decision must be findable from the code, not just from the
  maintainer's head.

## Considered Options

1. **Keep the det = −1 transform; document loudly + enforce via a 45°
   canary test in `tests/test_geometry.py`.**
2. **Replace the matrix with a standard CCW rotation
   `[[cos h, −sin h], [sin h, cos h]]`** and accept whatever knock-on
   effects propagate through the rest of the code.
3. **Redefine world coordinates** so `+y` points *shallower* (toward
   the door) instead of deeper, making the matrix come out to a pure
   rotation.
4. **Redefine plane-local axes** so `+y` is "left" (port) rather than
   "right" (starboard), again producing a pure rotation.

## Decision Outcome

**Chosen option: keep the det = −1 transform**, because both axis
conventions are independently defensible (world coords match the human
view of the hangar; plane-local axes match aviation convention), and
the cost of a slightly surprising matrix is paid by the maintainer
*once* — at the documentation level — while the cost of breaking either
convention is paid by *every* future YAML author and PNG reader.

The transform's handedness is not a bug to be fixed but a derived
consequence of two upstream choices we want to keep. We make it safe
by documenting it three ways (CLAUDE.md, this ADR, the module
docstring) and by pinning it with a regression test that fails the
instant someone substitutes a pure rotation.

### Why the matrix has determinant −1: the two sign flips

The matrix comes out to `[[sin h, cos h], [cos h, −sin h]]` for two
independent reasons. **Both flips must happen** — either alone would
give a different (and also wrong) matrix. The two flips are:

**1. Compass headings rotate clockwise; standard math angles rotate
counter-clockwise.**

A textbook 2D rotation, rotating a vector CCW by angle α, is:

```
R_ccw(α) = [[cos α, −sin α],
            [sin α,  cos α]]
```

`heading_deg` is defined as "the compass-style angle of the nose,
measured from world `+y` (deeper into hangar), CW positive." Going from
CCW to CW negates the angle inside the matrix: substitute `α → −h`,
remember `cos(−h) = cos h` and `sin(−h) = −sin h`, and the matrix
becomes:

```
R_cw(h) = [[ cos h,  sin h],
           [−sin h,  cos h]]
```

That is the first sign flip. The matrix is still a pure rotation
(det = +1), just CW instead of CCW.

**2. Plane-local `(forward, right)` describes a left-handed mapping
into world `(right-along-door, deeper-into-hangar)`.**

Now the basis-axis correspondence. At heading 0°:

- Plane-local `+u` (forward, toward nose) must map to world `+y`
  (deeper into hangar).
- Plane-local `+v` (right, toward starboard wingtip) must map to world
  `+x` (right along the door).

So the column of the linear map applied to `(1, 0)` (the plane-local
forward unit vector) at `h = 0` must be `(0, 1)` in world coords, and
the column applied to `(0, 1)` (plane-local right unit vector) must be
`(1, 0)`. That immediately forces the matrix at `h = 0` to be
`[[0, 1], [1, 0]]` — a swap of axes, which is a reflection
(det = −1), not a rotation (det = +1).

Plugging into the general form: at `h = 0` the world coords reduce to

```
world_x = u·sin 0 + v·cos 0 = v
world_y = u·cos 0 − v·sin 0 = u
```

which is exactly the swap. The matrix is `[[sin h, cos h], [cos h,
−sin h]]`, and its determinant is `sin h·(−sin h) − cos h·cos h = −1`.

That is the second sign flip. The first flip alone (CW rotation, no
axis swap) would still be a pure rotation; the second flip alone (axis
swap, no CW conversion) would be a reflection at the wrong rotation
direction. Composing them gives the rotation-plus-reflection map we
actually want: a left-handed (det = −1) mapping that respects both the
compass convention and the natural axis assignment.

### Worked example at heading 45° (the canonical canary)

`sin 45° = cos 45° = √2/2 ≈ 0.7071`. Placement at origin
`(px, py) = (0, 0)`. Two probes:

**Probe A — nose-forward: plane-local `(u=1, v=0)`.**

```
world_x = 0 + 1·sin 45° + 0·cos 45° = +√2/2
world_y = 0 + 1·cos 45° − 0·sin 45° = +√2/2
```

The nose lands in the `(+x, +y)` quadrant — right and deeper into the
hangar. This matches intuition: at heading 45° (halfway between
"deeper" and "right"), the nose should point into that diagonal.

A textbook CCW rotation matrix would give *the same* answer for this
probe (because `sin 45° = cos 45°`, the row swap is invisible). So
**this probe alone does not detect a sign-flip regression**, even at
45°.

**Probe B — right wingtip: plane-local `(u=0, v=1)`.**

```
world_x = 0 + 0·sin 45° + 1·cos 45° = +√2/2
world_y = 0 + 0·cos 45° − 1·sin 45° = −√2/2
```

The right wingtip lands in the `(+x, −y)` quadrant — right and toward
the door. This also matches intuition: at heading 45° (nose toward the
diagonal away from the door), the right wingtip rotates around to
point toward the door.

A textbook CCW rotation would send `(0, 1)` to
`(−sin 45°, cos 45°) = (−√2/2, +√2/2)` — the `(−x, +y)` quadrant,
i.e. *left and deeper*. That is the wrong answer. **Probe B is
distinguishing** — the correct and the would-be-fixed transforms
disagree on it.

This is exactly why
`tests/test_geometry.py::TestAircraftPartsWorld::test_heading_45_right_wingtip_in_plus_x_minus_y_quadrant`
is the load-bearing test for this ADR: it pins probe B at heading 45°,
and any "fix" to a CCW matrix flips both signs in the expected output,
failing the assertion loudly.

### Why not Option 2 — replace with a standard CCW rotation matrix?

A pure rotation matrix `[[cos h, −sin h], [sin h, cos h]]` has
determinant `+1` and is *almost* indistinguishable from the correct
transform under casual inspection. The catch: it silently flips the
right-vs-left orientation of every aircraft against the world frame.
At heading 0° the wrong matrix would send plane-local `(0, 1)` (right
wingtip) to world `(−1, 0)` (left along the door) instead of world
`(+1, 0)` (right along the door) — mirroring every aircraft.

This bug is invisible at the symmetric headings (0°, 90°, 180°, 270°)
because `sin` or `cos` is 0 at each one, masking the swapped term.
Test cases at *only* axis-aligned headings cannot catch it. The
failure mode would surface as:

- Strut-braced planes whose struts are modeled on one side of the
  fuselage would collide differently than intended in some real
  layouts and not in others (depending on heading).
- PNG renders would show planes with their wing struts on the wrong
  side, but only at non-axis-aligned headings — and only if a human
  noticed.
- Solver outputs would still be "valid" by the (broken) checker, so
  the bug would not raise — it would just be wrong.

The cost of "rotation matrix looks normal" is paid by everyone
downstream, forever. Rejected.

### Why not Option 3 — redefine world coordinates so `+y` points "shallower"?

The world frame was chosen to match how a human standing in front of
the hangar perceives the layout: looking through the door, `+x` runs
right along the door wall, `+y` runs *away from the viewer* (deeper).
This is the "looking down at a map" frame familiar from architectural
drawings.

Flipping `+y` to point toward the door would invert every layout YAML
ever authored (`y_m: 5.0` would now mean "5 meters in front of the
door" instead of "5 meters into the hangar"), every PNG render
(planes near the door would appear at the bottom of the image
instead of the top, contrary to the convention), and every collision
fixture in `tests/fixtures/`. The matrix would simplify, but the
ecosystem of artifacts that depends on the current convention would
all have to change — for a cosmetic win on a single matrix that
already has a regression test pinning it. Rejected.

### Why not Option 4 — redefine plane-local axes so `+y` is "left" (port)?

Aviation convention is firm: starboard = right, port = left. The
plane-local `+y` axis is "right" specifically because that matches a
pilot's mental model and the conventional fuselage-station-line
diagrams used in the wider sport-aviation community. Inverting to
"left = `+y`" would make `data/fleet.yaml` harder to author from
manufacturer drawings (the part offsets would have inverted signs
from what the drawings show) and harder for future contributors with
aviation background to review.

The trade is: pay a one-time cost to document a surprising matrix, or
pay an ongoing cost to author every YAML against a flipped axis. The
ongoing cost is worse. Rejected.

## Consequences

### Positive

- **World coordinates match the human view of the hangar.** Layout
  YAMLs, PNG renders, and architectural intuition all align.
- **Plane-local coordinates match aviation convention.** Authoring
  fleet data from manufacturer drawings is straightforward.
- **The transform is mathematically valid.** A rotation composed with
  a reflection is a perfectly well-defined linear map; det = −1 is not
  a defect, just a property. The map is invertible (its inverse is
  itself, since reflections are involutions composed with rotations of
  appropriate sign).
- **The decision is now discoverable from `docs/adr/` and from the
  module docstring**, not only from CLAUDE.md or a maintainer
  conversation.

### Negative

- **Every contributor will, at some point, want to "fix" this.** The
  matrix does not look like the textbook form most graphics tutorials
  teach. We accept the recurring overhead of explaining why it stays.
- **One more concept to learn** before touching `geometry.py`. The
  module docstring and CLAUDE.md "Coordinate convention" section
  attempt to front-load that learning, and the
  `geometry-invariant-guard` subagent catches regressions at PR
  review, but the cost is real.

### Neutral

- **Tests at the symmetric headings (0°, 90°, 180°, 270°) cannot
  catch a regression** to a pure rotation — they are consistent with
  both the correct and the wrong matrix. Test coverage must include
  at least one non-axis-aligned heading with a *distinguishing*
  probe (one where the correct and CCW transforms produce different
  outputs). See
  [`.claude/agents/geometry-invariant-guard.md`](../../.claude/agents/geometry-invariant-guard.md)
  for the distinguishing-probe rule.
- The 45° heading is canonical for *probe B* (right-wingtip) because
  it is the simplest distinguishing case; 135° works for *probe A*
  (nose-forward) because at 135° `sin h ≠ cos h` and the row swap
  becomes visible on the nose vector alone. Both are pinned in the
  test suite.

## Compliance

- **The canary regression test:**
  `tests/test_geometry.py::TestAircraftPartsWorld::test_heading_45_right_wingtip_in_plus_x_minus_y_quadrant`.
  At heading 45°, plane-local `(u=0, v=1)` must land at world
  `(+√2/2, −√2/2)`. Any regression to a det = +1 matrix sends the probe
  to `(−√2/2, +√2/2)` and fails the assertion. The companion test
  `test_heading_135_nose_distinguishes_correct_from_ccw` uses the nose
  vector at 135° as a redundant guard.
- **The `geometry-invariant-guard` subagent**
  ([`.claude/agents/geometry-invariant-guard.md`](../../.claude/agents/geometry-invariant-guard.md))
  is invoked on every PR touching `src/hangarfit/geometry.py` or
  `src/hangarfit/collisions.py`. It re-derives the matrix form from
  the diff, checks the determinant, and verifies that any new
  geometry test exercises at least one non-axis-aligned heading with
  a distinguishing probe. This is a defense-in-depth layer beyond
  the pytest assertion.
- **The CLAUDE.md "Coordinate convention" section** spells out the
  convention in prose, so contributors encounter the explanation
  before they encounter the code. The
  `geometry.py` module docstring repeats the warning at the file
  level.
- **The PR review checklist** (per CLAUDE.md "Subagents" section)
  routes any change to `geometry.py` or `collisions.py` through
  `geometry-invariant-guard` automatically — the maintainer does not
  have to remember to invoke it.

Together: docstring at the code site, ADR for the rationale,
CLAUDE.md for the operational convention, pytest assertion for
automated regression, and a subagent for PR-time review. A
"correction" to a pure-rotation matrix would have to defeat all five
to land.

## More Information

- **Authoritative prose explanation:** `CLAUDE.md` §"Coordinate
  convention" — the source the rest of this ADR distills.
- **Implementation:** [`src/hangarfit/geometry.py`](../../src/hangarfit/geometry.py),
  specifically `aircraft_parts_world` (the world-coordinate list
  comprehension is the load-bearing line).
- **Canary tests:** [`tests/test_geometry.py`](../../tests/test_geometry.py)
  — `test_heading_45_right_wingtip_in_plus_x_minus_y_quadrant`
  (right-wingtip at 45°) and `test_heading_135_nose_distinguishes_correct_from_ccw`
  (nose at 135°).
- **Review-time guard:** [`.claude/agents/geometry-invariant-guard.md`](../../.claude/agents/geometry-invariant-guard.md).
- **Related ADRs:** [ADR-0001 — Parts-based collision model](0001-aircraft-parts-model.md)
  (defines what is being transformed); [ADR-0000 — Record
  architecture decisions](0000-record-architecture-decisions.md)
  (the meta-ADR that motivates this one's existence).
