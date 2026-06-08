# §8 Crosscutting Concepts

The rules in this section are not the property of any single module —
they shape multiple modules at once. A new contributor who reads only
one module will get the syntax right but the semantics wrong. This is
the section that fixes that.

Each concept below has either a corresponding ADR (the *why*) or a
canonical implementation file (the *where*). The text here states the
rule and points at the right place to read further.

## Domain conventions

### The parts model

Every aircraft is represented as a tuple of `Part`s — each an oriented
rectangle in plan view plus a height range `[z_bottom_m, z_top_m]`.
Two parts from different aircraft conflict iff (1) their plan-view
polygons are closer than `hangar.clearance_m` AND (2) the gap between
their `[z_bottom_m, z_top_m]` ranges is less than
`hangar.wing_layer_clearance_m` (overlap counted as zero gap). Parts
of the *same* aircraft are never checked against each other — a
Husky's wing and its own strut share a plan-view column by design.

**The fuselage front/aft exception.** The fuselage is split into two
kinds, `fuselage_front` (cockpit / nose) and `fuselage_aft` (the
cabin-aft tube), so the rule can tell a wing-over-cockpit from a
wing-over-tail. (The tail *surfaces* are now explicit `tail` +
`vertical_stabilizer` parts, no longer folded into `fuselage_aft` — see
"The empennage" below and [ADR-0023](../adr/0023-empennage-tail-surfaces.md).)
`wing × fuselage_aft` keeps the two-clause rule above (a wing may
overhang another plane's aft fuselage when the heights are disjoint). But
`wing × fuselage_front` is a **hard conflict on plan-view overlap
alone — the height clause (2) is dropped**: a wing over a cockpit blocks
the canopy / prop arc / pilot ingress at *any* nesting height. This is
the one pair that ignores `z`; every other pair (including
`fuselage_* × fuselage_*`, which share a z-band by construction) uses
the uniform two-clause predicate. See
[ADR-0012](../adr/0012-fuselage-front-aft-split.md) for the rationale
and rejected alternatives.

The wing-over-tail-but-not-cockpit rule in plan view, with the height
band that makes the aft case a pass-through:

```text
PLAN VIEW (top-down).  +x along the door; +y runs deeper into the hangar,
drawn DOWNWARD (door at top) to match the §8 convention box and the renderer.
Plane B parked nose-in (heading_deg = 0 -> nose points DOWN, deeper in).  Plane-local +x = nose.
Split station x_break = wing.offset_x_m - wing.length_m/2  (the wing TRAILING edge):
  fuselage_front = nose side [x_break .. nose]   |   fuselage_aft = tail side [tail .. x_break]

              |   tail (-y, near door)                      |   tail (-y, near door)
        +-----------+                                 +-----------+
        |           |                                 |:::::::::::|
        |           |  ... fuselage_aft (TAIL)        |::::: A :::|
   - - -|:::::::::::|- - - x_break (wing TE) - - - - -|           |- - - x_break - - -
        |::::: A :::|                                 |           |
        +-----------+  ... fuselage_front (COCKPIT)   +-----------+
              |   Plane B fuselage                          |   Plane B fuselage
              v   nose (+y, deeper in)                      v   nose (+y, deeper in)
   [::: A :::] = Plane A's WING footprint overhanging Plane B in plan view

      CASE B:  wing over fuselage_front          CASE A:  wing over fuselage_aft
      => HARD CONFLICT, z-gap IGNORED            => LEGAL iff heights disjoint
         (fuselage_front_wing_overlap)              (height-disjoint pass-through)

HEIGHT BAND (side view, both cases): A's wing sits ABOVE B's fuselage with a
z-gap >= wing_layer_clearance_m.   Case A: the gap makes it pass.  Case B: the
gap is irrelevant -- the cockpit rule drops clause (2) entirely (ADR-0012, D1).

         A wing  ----========----    (z_bottom_m .. z_top_m, the upper layer)
                        | z-gap >= wing_layer_clearance_m
         B fuselage  [############]   (lower layer)
```
*A wingtip may overhang a parked plane's aft fuselage / low tailplane (Case A: legal when heights are disjoint, the two-clause `wing × fuselage_aft` rule) but never its cockpit / front fuselage (Case B: a hard `fuselage_front_wing_overlap` conflict at any nesting height). The loader auto-splits a `fuselage` part at the wing trailing edge `x_break`; front is the nose side, aft the tail side. Since [ADR-0023](../adr/0023-empennage-tail-surfaces.md) Case A also requires the wing to clear the plane's centreline `vertical_stabilizer` (fin), which rises into the wing layer — see "The empennage" below.*

The closed set of `PartKind` values is `{"fuselage_front",
"fuselage_aft", "wing", "strut", "tail", "vertical_stabilizer"}`. The
legacy `"fuselage"` is **not** a constructed kind — it survives only as a
transient YAML keyword the loader auto-splits at the wing trailing-edge
station (`wing.offset_x_m − wing.length_m/2`, the #282 wing-spar
precedent), emitting an area-conserving `fuselage_front` + `fuselage_aft`
pair whose union is the original box. An aircraft with a `fuselage` part
but no `wing` part is a load error (nothing to derive the break from);
explicit `fuselage_front`/`fuselage_aft` parts in YAML are a valid
override the loader does not split. Adding a new structural element (e.g.
an engine nacelle) is a code change in `src/hangarfit/models.py`, not just
a YAML edit — see [ADR-0001](../adr/0001-aircraft-parts-model.md) for the
parts-not-bbox rationale and [ADR-0012](../adr/0012-fuselage-front-aft-split.md)
for the front/aft refinement.

**The empennage** ([ADR-0023](../adr/0023-empennage-tail-surfaces.md)).
Each aircraft carries two explicit tail surfaces: `tail` (the horizontal
stabilizer — wide, ~2.5–3.5 m span, at a per-aircraft height) and
`vertical_stabilizer` (the fin + rudder — thin, on the centreline, rising
from the fuselage top to the published overall height, *into* the wing
layer). No collision-rule change is needed: the same two-clause predicate
makes a wing nested over a neighbour's tail conflict with that plane's fin
**only when the wing also overlaps the thin centreline fin in plan view** —
i.e. wing-over-tail nesting stays legal exactly when the wing clears the
fin laterally. Per-part z expresses every tail configuration with no
per-type code: conventional / cruciform tails sit the horizontal
stabilizer *below* the wing layer (z-disjoint from an overhanging high
wing, so it stays overhangable); a T-tail (the Stemme S10) sits it at the
fin top *inside* the wing layer (so an overhanging wing conflicts).
The fin is never overhangable; `metrics._OVERHANGABLE` keeps only `tail`
and `fuselage_aft`.

**Fleet composition relevant to the parts model.** Of the nine
aircraft in `data/fleet.yaml`, six are **strut-braced** (the Aviat
Husky, Wild Thing, Zlin Savage, Cessna 140, Cessna 150, and FK9 Mk II)
and three are **cantilever** (Scheibe SF-25E Falke, Fuji FA-200, and
Flight Design CTSL). The **Fuji is the only low-wing**; every other
aircraft is high-wing. These two facts — which planes have struts and
which plane is low-wing — drive the operationally interesting cases
of the collision rule: strut-braced planes block another plane's
wing from nesting through their wing volume, and the only low-wing
allows a high-wing's wingtip to legally project over its **aft
fuselage / low tailplane** in plan view (the height-disjoint pass-through
case) — but *not* over its **cockpit / front fuselage**, which is a hard
conflict regardless of height (the front/aft split, ADR-0012), and *not*
over its **fin** (`vertical_stabilizer`), which rises into the wing layer
(ADR-0023). Per-plane dimensions, gear types, and movement modes live in
`data/fleet.yaml` as the source of truth.

### The maintenance bay rule

A scenario designates one aircraft as the maintenance occupant; that
plane is absent from `layout.placements` and present in `layout.fleet`
(enforced by `Layout.__post_init__`). When the occupant is set, the
**bay rectangle becomes a hard keep-out for every other plane's parts**.
The bay is the axis-aligned rectangle anchored to the back wall:
`x ∈ (center_x_m − width_m/2, center_x_m + width_m/2)`,
`y ∈ (length_m − depth_m, length_m]`. The half-open notation reflects
that the back-`y` edge is *inherited* from the hangar boundary
(inclusive, enforced upstream by `_hangar_bounds_conflicts`) and not
re-tested here. Any vertex of a non-occupant part that lies strictly
inside that rectangle fires a `bay_intrusion` conflict on the owning
plane — one conflict per offending part.

Top-down, with the placeholder `data/hangar.yaml` values made concrete
(the bay is the back-right 9 m × 9 m corner):

```text
 TOP-DOWN.  +x along the door; +y runs deeper into the hangar, drawn DOWNWARD
 (door at top) to match the §8 convention box and the renderer.  Placeholder
 data/hangar.yaml: length_m=25, width_m=18; bay center_x_m=13.5, width_m=9,
 depth_m=9  ->  x in (9.0, 18.0),  y in (16.0, 25.0].
 x=0          door center_x=9.0, width=12.0  (x in [3, 15])               x=18
  +=========================[  door  ]=======================================+   y = 0.0   front wall
  |        +x runs right along the door --->        +y runs into hangar      |
  |                                                                          |
  |                                                                          |
  +------------------------------------------+#############################+   y = 16.0  bay FRONT edge
  |                                          |#############################|   (= 25 - depth; STRICT:
  |                                          |#  MAINTENANCE BAY (closed)  #|   a vertex ON x=9 or
  |          normal hangar floor             |#  back-anchored, partial-w  #|   y=16 is OUTSIDE the bay)
  |                                          |#  x in (9.0, 18.0)          #|
  |                                          |#  y in (16.0, 25.0]         #|   right edge x=18
  |                                          |#  any non-occupant vertex   #|   == hangar wall
  |                                          |#  STRICTLY inside  =>  one   #|
  |                                          |#  bay_intrusion per part    #|
  +==========================================+#############################+   y = 25.0  [INSIDE]
 x=0                                          x=9            x=13.5          x=18
 back wall  y = length_m = 25.0   (bay back edge: INHERITED, INCLUSIVE -> y = 25 is INSIDE)

 In-bay predicate:  (9.0 < x < 18.0)  AND  (16.0 < y <= 25.0)
   strict < on the left / right / front edges; inclusive <= on the back edge
   (inherited from _hangar_bounds_conflicts, so y = 25 is not re-tested here)
```
*Maintenance-bay geometry on the placeholder hangar (`length_m`=25, `width_m`=18; bay `center_x_m`=13.5, `width_m`=9, `depth_m`=9): the back-right 9×9 m corner, `x ∈ (9.0, 18.0)`, `y ∈ (16.0, 25.0]`. The left/right/front edges are strict `<` (a vertex on the edge is in the aisle, not the bay); the back-`y` edge is inclusive because it coincides with the hangar back wall and is inherited from `_hangar_bounds_conflicts`. When a maintenance occupant is set, any non-occupant part vertex strictly inside fires one `bay_intrusion` conflict per offending part.*

This rule replaced the earlier "fuselage centroid in the back strip"
rule during the bay-walling work that completed in
[#103](https://github.com/DocGerd/hangarfit/issues/103) and follow-up
PRs. The current rule's decision is recorded in
[ADR-0006](../adr/0006-bay-intrusion-maintenance-rule.md) (Status:
**Accepted**); the Phase 1 predecessor is preserved in
[ADR-0005](../adr/0005-maintenance-bay-rule.md) (Status: **Superseded
by ADR-0006**). The implementation lives in
`src/hangarfit/collisions.py::_bay_intrusion_conflicts`.

### Movement modes

Each aircraft has a `movement_mode` in `{"always_cart",
"always_own_gear", "cart_eligible"}`. The cart rule — at most one
`cart_eligible` plane on carts in any layout — is enforced in
`Layout.__post_init__`, not in the collision checker. The parts model
and collision checker remain deliberately **motion-agnostic** — they
describe where a plane *is*, never how it got there.

Motion behaviour now lives in the `towplanner` module (Phase 3a), which
uses a **single closed-form motion model** for every plane —
**Reeds–Shepp** (Dubins forward arc-line-arc *plus reverse* arcs and
straights, [ADR-0010](../adr/0010-reeds-shepp-motion-model.md)): a plane
can back up to reorient instead of driving a full turning-circle loop,
and reverse legs cost 1.5× their length so forward motion is preferred. A
cart-borne plane is treated as own-gear with `turn_radius_m = 0` (a
pivot-in-place, and now a back-straight-out option too), supplied through
`Aircraft.effective_turn_radius_m()`. This **retires the earlier
"holonomic on carts; Dubins-path-style on own gear" two-mode framing** —
there is one motion model, not two (ADR-0007, forks 2–3; ADR-0010
supersedes fork 2's *Dubins-only* choice with Reeds–Shepp, still
closed-form and deterministic). A consequence: `turn_radius_m` is now
**load-bearing**. It was an unused placeholder through Phase 1/2a and is
consumed by the planner's Reeds–Shepp arithmetic and its bound-aware
Hybrid-A\* path search ([#222](https://github.com/DocGerd/hangarfit/issues/222),
[#261](https://github.com/DocGerd/hangarfit/issues/261)). Cart planes keep
`turn_radius_m: null` in `fleet.yaml`; the zero radius is supplied by the
accessor, not baked into the data (ADR-0007, fork 4).

### The door is a visual marker only

The hangar's `door` field positions the opening for the PNG renderer
to draw a gap in the front wall, but the collision checker does **not**
treat the door as a separate opening: every part of every placed plane
must fit fully inside the hangar rectangle for the layout to be
considered valid. There is no "door clearance" rule beyond the
hangar-bounds check itself (`_hangar_bounds_conflicts` in
`src/hangarfit/collisions.py`).

At the `collisions.check` level the door stays a **visual marker only** —
the static checker cares solely about the hangar-bounds rectangle. The
`towplanner` (Phase 3a) is the first consumer to treat the door as a
**motion gate**: a plane enters from a **searched door-cone** and is towed
to its slot along a Reeds–Shepp path, with the front gap exempted during
motion (a mover may straddle `y < 0` in front of the door mid-tow — and may
*back out* through it, the front-gap exemption being pose-only and
gear-agnostic). That door semantics lives entirely in the planner and changes
no `collisions.check` verdict (ADR-0007).

**The door-cone** (`entry_poses`, #262) is a deterministic 3 × 5 grid of
start poses: three x-samples within the door interval (door centre, clamped
target x, and their midpoint) combined with five forward-admissible headings
(straight-in ±30° in 15° steps: 330°, 345°, 0°, 15°, 30°). All surviving
candidates — those whose footprint at the front boundary does not clip the
side or back walls — are seeded into the Hybrid-A* frontier simultaneously at
`g = 0`; A* then returns the shortest path across the whole cone. The
`DubinsArc.start` of the returned arc is the winning cone pose. Rear-entry
headings (near 180°) are out of scope here; they belong to the Reeds–Shepp
motion issue (#261). This replaces the earlier v1 single-ray reduction (one
clamped target-x, heading 0°) described in ADR-0007 Q6.

**The staging apron** ([ADR-0021](../adr/0021-tow-planner-staging-apron.md),
#412) generalises this when the site has one (`Hangar.apron_depth_m > 0`). The
door-cone grid extends *south* into the apron rectangle (`y ∈ [−apron_depth_m,
0)`, full frontage in `x`), the `y = 0` door-line start is **excluded** (so every
plane originates outside and visibly slides in), and the rear-entry cone `{150°,
165°, 180°, 195°, 210°}` joins the grid as additional deterministic seeds (a plane
may back in tail-first — the rear-entry headings that are out of scope *without* an
apron become routable *with* one). The front-gap exemption widens accordingly:
during motion the whole apron rectangle is open ground, but the front wall at
`y = 0` stays solid except for the door gap — a footprint that *crosses* the wall
line beside the door (vertices on both sides of `y = 0` outside the door interval)
is still a conflict (the **#411 jamb rejection, retained verbatim**), while a
footprint wholly on the apron is free. `collisions.check` is still untouched (it
forbids `y < 0` entirely; the final parked slot is a fully in-bounds placement),
and `apron_depth_m = 0`/absent reproduces the pre-apron behaviour byte-for-byte.

The motion vocabulary and the door entry-cone the planner searches over:

```text
REEDS-SHEPP MOTION MODEL (towplanner._primitives, ADR-0010)
World frame: +x runs ALONG the door, +y runs INTO the hangar.
A pose heading_deg=0 points nose-first toward +y (straight in).

(1) REVERSE-CAPABLE REORIENT vs CART PIVOT-IN-PLACE
---------------------------------------------------------------------
 OWN GEAR (turn_radius_m = r > 0): 6 primitives, fixed order
   forward:  Lf   Sf   Rf      reverse:  Lr   Sr   Rr   (gear = -1)
   reverse legs cost _REVERSE_COST_FACTOR = 1.5 x  -> forward preferred

   A turned goal a forward-only Dubins car can't reach in-bounds:

      forward-only Dubins          Reeds-Shepp (fwd arc-line-arc + reverse)
      must drive a full            backs up to reorient, then pulls in
      turning-circle loop          (measured: an 18 m reverse beats a 32 m
        .--->--.                    loop; even at the 1.5x weight it wins)
       /        \                       Sf      Lr (reverse arc)
      |  ~32 m   |  goal             >------>  .<......
       \        /   X (turned)              \ '-.    : reverse retreats
        '--<---'                       Lf arc   '--> X   around the steer centre

 CART (turn_radius_m = 0): 4 primitives  ->  Lf  Sf  Rf  Sr
   pivot-in-place (r = 0, zero translation) + reverse-straight (Sr).
   Reverse PIVOTS (Lr/Rr) are omitted: a reverse pivot rotates heading the
   same way as the OPPOSITE forward pivot -> exact duplicate, always loses
   the best_g race. Only the reverse *straight* (Sr) is a genuinely new move.

        ^ pivot CCW (Lf)    pivot CW (Rf)        Sr: back straight
        |  .-.                .-.                 out of a slot
        | (   ) spin in place (   )            <===== [plane] ======

(2) DOOR ENTRY-CONE  (entry_poses, #262)  -- 3 x-samples x 5 headings
---------------------------------------------------------------------
 All surviving cone poses are seeded into the Hybrid-A* frontier at g=0
 simultaneously; A* then returns the shortest path across the whole cone.

  x-samples (within door interval): door-centre | clamped-target-x | midpoint
  headings (forward-admissible, straight-in +/-30 deg in 15 deg steps):
        330   345    0    15    30        (0 = straight into +y)

  front wall (y=0) ===door-gap=== along +x ============================
        x0          x1(target)         x2(mid)
      \ | /        \ | /              \ | /        each fan = 5 headings
       \|/          \|/                \|/         {330,345,0,15,30}
        *            *                  *          = up to 15 seed poses
                     |                              (duplicates removed;
                     v  +y into hangar               poses clipping the
                                                      side/back walls dropped)
```
*Reeds–Shepp gives every plane reverse legs (6 own-gear primitives `Lf Sf Rf Lr Sr Rr`, reverse weighted 1.5×; carts get 4: `Lf Sf Rf` + reverse-straight `Sr`), and the planner seeds the Hybrid-A\* search from a 3×5 door-cone fan instead of a single straight-in ray.*

## The coordinate convention

The single most contributor-confusing concept in the project. Read
[ADR-0002](../adr/0002-determinant-minus-one-transform.md) once,
in full, before touching `src/hangarfit/geometry.py` or any code that
consumes world-coordinate parts.

### Frames

**Hangar (world) coordinates** — origin at the front-left corner,
looking down on the layout:

```
       +x ->
  +---[door]-------+
  |                |
  | y (deeper)     |
  v                |
  +----------------+
```

- `+x` runs right along the door wall.
- `+y` runs deeper into the hangar.
- `heading_deg = 0` means the plane's nose points toward `+y`
  (deeper into hangar).

Throughout §8, the top-down diagrams draw `+y` **downward** (door at the
top), matching this box and the PNG renderer (`visualize.py` inverts the
y-axis so the door renders at the top). The one exception is the det = −1
sketch below, which uses standard math axes (`+y` up) because it is a
coordinate-algebra diagram, not a hangar floor plan.

**Plane-local coordinates** — origin at the plane reference point
(main-gear / cart centroid):

- Plane-local `+x` = forward (toward nose).
- Plane-local `+y` = right (toward right wingtip).

### The det = −1 trap

The linear part of the plane-local → world transform is
`[[sin h, cos h], [cos h, −sin h]]`. Its determinant is `−1` —
a rotation composed with a reflection, not a pure rotation. This is
**intentional** (two simultaneous sign-flips from compass-CW heading
convention and the plane-local-vs-world handedness mismatch).

A textbook CCW rotation matrix `[[cos α, −sin α], [sin α, cos α]]`
would silently break every layout, while still passing tests at the
symmetric headings 0°, 90°, 180°, 270°. The 45° canary test
(`test_heading_45_right_wingtip_in_plus_x_minus_y_quadrant` in
`tests/test_geometry.py`) and the `geometry-invariant-guard`
review-time subagent are the project's combined defense against a
well-meaning contributor "fixing" it.

If you're tempted to simplify the matrix, read ADR-0002 first.

The picture below makes the handedness flip concrete: at the 45° canary
heading the plane-local axes are overlaid on the world frame. The nose
*and* the right wingtip both land in `+x` (to the right), but the wingtip
lands *clockwise* of the nose, in `−y` (toward the door) — the opposite
of where a pure (det = +1) rotation would send it.

```text
heading_deg = 45   (compass heading, CW from world +y; plane at the origin)
plane-local axes:  +u = nose (forward),   +v = right wingtip (starboard)

det = -1 map (local -> world), ADR-0002 / geometry.py:
    world_x = x + u*sin(h) + v*cos(h)
    world_y = y + u*cos(h) - v*sin(h)        at h = 45deg, sin h = cos h = 0.71

                        +y  (deeper into hangar)
                        ^
                        |        N      nose:    (u=1, v=0) -> (+0.71, +0.71)
                        |       /              ...lands in the (+x, +y) quadrant
                        |      /
                        |     /         sweeping nose -> right wingtip is +90deg
                        |    /          IN THE PLANE, but in the WORLD the
   -x ------------------+--------------> +x   (right, along the door wall)
                        |    \          wingtip lands CLOCKWISE of the nose
                        |     \
                        |      \
                        |       \
                        |        R      wingtip: (u=0, v=1) -> (+0.71, -0.71)
                        v              ...lands in the (+x, -y) quadrant
                       -y  (front of door)         (right AND toward the door)

THE FLIP:  a right-handed (det = +1) CCW rotation would instead send the
           wingtip to (-0.71, +0.71) = the (-x, +y) quadrant -- mirrored,
           WRONG. The nose probe alone cannot catch this (sin45 = cos45, so
           the row swap is invisible at 45deg for u=1,v=0); only the wingtip
           sign-flip reveals it. That is what the 45deg canary test pins.
```
*Plane-local frame (+u = nose, +v = right wingtip) overlaid on the world frame at the 45° canary heading. Both probes land in `+x`; the nose at (+0.71, +0.71) and the right wingtip at (+0.71, −0.71). A pure rotation would mirror the wingtip to (−0.71, +0.71). That reflected handedness is the determinant −1 (ADR-0002).*

### Fuselage offset signs

Because the main gear sits *forward* of the geometric fuselage
centroid, every fuselage's `offset_x_m` in `data/fleet.yaml` is
**negative** (roughly `−0.25 × length` for tailwheels, `−0.05 ×
length` for nosewheels; `scheibe_falke`'s monowheel is at the
centroid, so its offset is 0). Wing and strut offsets shift in
tandem so each airplane's internal geometry stays self-consistent.
Resetting any fuselage offset to 0 silently breaks the
gear-at-origin contract — an earlier regression of exactly this
shape was caught and reversed during the Phase 1 audit.

## Default clearances

Both clearances are configurable in `data/hangar.yaml` and consumed
by `src/hangarfit/collisions.py`:

| Clearance | Default | Key in `hangar.yaml` | Used for |
|-----------|---------|----------------------|----------|
| Horizontal | 0.30 m | `clearance_m` | Plan-view distance threshold in the collision predicate |
| Vertical | 0.20 m | `wing_layer_clearance_m` | Height-range gap threshold in the collision predicate |

The defaults are placeholder values pending real measurement. The
collision checker reads them once per `check()` call from
`layout.hangar`; changing them at runtime means editing the YAML
file. Hard-coding them anywhere outside `data/hangar.yaml` is a bug.

`data/hangar.yaml` carries one more defaulted site scalar that is *not*
a clearance: `max_carts` (default `1`). It is the number of spare carts
available to the `cart_eligible` pool and is enforced by
`Layout.__post_init__` (not the collision checker) — at most `max_carts`
`cart_eligible` planes may sit on carts in one layout, while `always_cart`
planes get their own carts and never draw from this pool. It is
overridable per-invocation with the `--max-carts` CLI flag, which
replaces the value on the loaded `Hangar` before any layout is built. See
[ADR-0007](../adr/0007-tow-path-planner-v1-scope.md) (cart-inventory
amendment).

The two-clause predicate is symmetric in the two clearances: a
collision requires *both* the plan-view and the height-gap thresholds
to be violated simultaneously. This is what lets a high-wing's
wingtip legally project over a low-wing's **aft fuselage / low tailplane**
(close in plan view, far in height) — see ADR-0001. The **one
exception** is `wing × fuselage_front`: a wing over a cockpit is a hard
conflict on plan-view overlap alone, with the height clause dropped
(ADR-0012). `wing_layer_clearance_m` therefore governs every pair
*except* wing-over-cockpit — including `wing × vertical_stabilizer`, where
the fin's reach *into* the wing layer is exactly what makes the height
gap small enough to bite when a wing passes over the centreline fin
(ADR-0023).

## Data integrity: frozen dataclasses + `__post_init__` invariants

Every domain object is a `@dataclass(frozen=True)`. Construction is
the only writeable boundary; once an `Aircraft`, `Hangar`, `Layout`,
or any other model exists, no field can be reassigned.

Invariants that cannot be expressed in the type system are enforced
in `__post_init__`. The canonical examples live on `Layout`:

- The cart rule (at most one `cart_eligible` plane on carts).
- `movement_mode` ↔ `on_carts` consistency (an `always_cart` plane
  must have `on_carts=True`; an `always_own_gear` plane must have
  `on_carts=False`).
- If `maintenance_plane` is set, it must be a key in `fleet` and
  must NOT be a key in `placements` (the maintenance occupant is
  parked separately, not placed by the layout).

The contract is **a constructed instance is structurally valid**.
Downstream code (collision checker, solver, visualizer, CLI) never
re-validates the cart rule or the maintenance-plane membership; if a
`Layout` made it through `__post_init__`, those invariants hold.

This pattern is the project-wide answer to "where should
cross-reference invariants live?" — the data layer, at construction
time, not as scattered checks in each consumer.

## Explicit conflicts and explicit construction errors over silent passes

When the system encounters a violation — geometric or structural —
the answer is an **explicit signal** with a named taxonomy entry,
not a silent pass.

Two signal channels exist:

- **`Conflict.kind`** — emitted by the collision checker for
  geometric / placement violations of a structurally valid layout.
  Examples in the current taxonomy: `hangar_bounds` and
  `bay_intrusion` (both single-plane conflicts — `Conflict.planes`
  has one entry), and the pairwise `<kindA>_<kindB>_overlap` family
  (`fuselage_aft_wing_overlap`, `fuselage_front_wing_overlap`,
  `fuselage_aft_fuselage_aft_overlap`, `strut_wing_overlap`, etc., two-plane
  conflicts with the kind names always alphabetically sorted —
  `"fuselage_aft"` < `"fuselage_front"` < `"strut"` < `"wing"` — so the
  string is deterministic regardless of iteration order). The
  single-vs-pair arity matters downstream: the visualizer highlights
  one plane vs two; `total_penetration_m2` accounting only sums the
  pair-arity overlap area; the solver's scoring uses both.
- **Construction-time exceptions** — raised by
  `Layout.__post_init__` and the loader for structural problems
  (cart rule violated, maintenance plane absent from fleet,
  maintenance plane also in placements). A `Layout` either
  constructs successfully (and the structural invariants hold) or
  raises immediately; no caller has to re-check.

The discipline is: when in doubt, add a new `Conflict.kind` value
and emit it, or raise at construction with a precise message —
never let the silent path through. The pairwise overlap kinds'
alphabetical-sort rule is in service of the same posture: a
deterministic name lets fixtures and tests pin the exact failure
mode, which silent-fail behaviour could not.

## Determinism

Two distinct determinism contracts hold in the project, both
load-bearing:

1. **`check(layout)` is a pure function of its argument.** Same
   layout in, same `CheckResult` out, every time. No randomness,
   no environment dependence, no time-of-day variation. Tests rely
   on this when pinning specific conflict counts or
   `total_penetration_m2` values.
2. **`solve(scenario, seed=N)` is deterministic in scenario + seed.**
   Same scenario + same seed → bit-identical `SolveResult`. Achieved
   by single-threaded RNG threaded through every randomized step
   (initial placement, perturbation, restart-order choice). The
   diversity filter's accept/reject decisions are part of the same
   contract — same seed → same K layouts in the same order
   (see [ADR-0004](../adr/0004-diversity-metric.md) for the filter's
   metric). The determinism canaries in `tests/test_solver_canaries.py`
   are intentionally fragile: any unintended drift fails CI
   immediately and forces a conscious decision about whether the
   drift was wanted. See
   [ADR-0003](../adr/0003-rr-mc-solver-algorithm.md) for the search
   algorithm itself.

The "no parallelism in the solver" choice is the direct corollary —
parallelism would compromise determinism (different thread
schedules → different visit orders → different first-found layouts).
If a future performance need demands parallelism, it gets its own
ADR.

## Soft preferences

The hard score tuple `(conflict_count, total_penetration_m2)` measures only illegal overlap. The first **soft** preference — inter-plane spread (maximize separation once valid) — ships as an isolated post-pass (`solver._spread`), deliberately *outside* the hard tuple so the conflict-resolution determinism contract ([ADR-0003](../adr/0003-rr-mc-solver-algorithm.md)) is unaffected. See [ADR-0008](../adr/0008-inter-plane-spread-soft-preference.md) for the repulsion-energy metric and why it is a post-pass rather than a third score key.

The second soft preference — **nose-out parked heading** (park each plane pointing toward the door for an easy straight-out exit) — is a second isolated post-pass (`solver._nose_out`), run *after* `_spread` and independently of it. For each movable plane it applies the zero-displacement 180° antipodal flip `(h + 180) % 360` iff that is strictly more nose-out (closer to `heading 180`, the door, under the [ADR-0002](../adr/0002-determinant-minus-one-transform.md) convention) **and** the layout stays valid. It is **default ON** (`--no-nose-out`), with a per-plane tri-state `PlaneConstraint.nose_out` override for the legitimate nose-IN exemption. Crucially the pass is **RNG-free** — it draws no random numbers — so byte-identical determinism holds *even with the feature on* (strictly stronger than `_spread`, which guarantees byte-identity only when off). It is gap-neutral (position fixed), so it cannot fight spread. The companion **`tow_pivotable`** aircraft flag (a free-castering / nose-lift plane pivots in place when towed, `effective_turn_radius_m() → 0`, routed via the existing zero-radius cart-pivot fan) is a realism flag orthogonal to `movement_mode`. See [ADR-0022](../adr/0022-nose-out-parked-heading.md); the cheap *reachability* of a nose-out slot (backing in rather than looping) is the [ADR-0010](../adr/0010-reeds-shepp-motion-model.md) #480 amendment.

## Visualizer colour accessibility

The PNG renderer (`src/hangarfit/visualize.py`) must stay usable by
people with colour vision deficiency (CVD). Roughly 1 in 12 men have
red–green CVD; a purely red-vs-green signal is the single most common
accessibility failure in technical diagrams.

**Two invariants that must not regress:**

1. **The tow-path palette (`_TOW_PATH_COLORS`) is the Okabe–Ito 8-colour
   CVD-safe set** (source: https://jfly.uni-koeln.de/color/). This palette
   is distinguishable under deuteranopia, protanopia, and tritanopia, and
   degrades gracefully in greyscale. Any future palette extension or
   substitution must remain CVD-safe — verify with a simulation tool
   (e.g., Coblis) before committing.

2. **The conflict overdraw carries a non-colour redundancy channel.** The
   red edge (`_CONFLICT_COLOR = "#e74c3c"`) is retained as a fast signal
   for colour-normal viewers, but conflict patches also carry `hatch="xxx"`
   (dense cross pattern) and `linestyle="--"` (dashed stroke) so "this
   part is in conflict" reads on a B&W printout and for red-blind viewers.
   A future refactor that drops the hatch and dashes while keeping only the
   red edge is a regression, even if the layout looks correct at colour-normal
   rendering.

The wing-position triad (`_WING_COLORS`) uses `#d55e00` (Okabe–Ito
vermillion) for mid-wing rather than the original `#e67e22` (orange),
because orange and the low-wing yellow `#f4d03f` can merge under
protanopia. The blue (`#3498db`) and yellow are retained.

## Testing posture

### Fixture-driven over Python literals

New regression scenarios are added as YAML fixtures in
`tests/fixtures/`, not as Python-constructed `Layout`s with geometry
literals. The fixture naming convention is:

- `valid_*.yaml` — layouts that the checker should accept.
- `invalid_*.yaml` — layouts that the checker should reject.
- `solve_*.yaml` — scenarios for the solver's matrix tests.

The `.claude/skills/new-fixture/` skill scaffolds a fixture with the
right header (rationale, expected conflict kinds, related issue).
Adding a fixture is the right move when a new regression class
appears; the alternative (a Python test with hand-coded part offsets)
duplicates the YAML schema and ages worse.

### Golden tests

The strut-aware collision test suite in `tests/test_collisions.py`
is the canary for the parts model. It covers: same-height wing
overlap (must fail), high-over-low height-disjoint pass-through
(must pass), strut-blocks-nesting (must fail), inboard/outboard
strut-free nesting (must pass), the maintenance-bay rule, and the
all-nine-planes valid layout on the test-only larger hangar. If
these tests pass, the geometry is trustworthy on the current
placeholder measurements. If they fail, suspect the parts model
or the transform (`tests/test_geometry.py` will localize which).

### Determinism canaries

`tests/test_solver_canaries.py` is parametrized over three
representative scenarios. Each calls `solve(seed=42)` twice and
asserts the returned `SolveResult` is bit-identical. The canary is
**intentionally fragile** — a refactor that changes RNG threading
will surface here before it hides in a flaky test downstream.
Updating expected outputs requires a conscious "yes, the algorithm
changed" decision in the PR.

### Slow-test markers

Tests that take more than a few seconds carry `@pytest.mark.slow`.
The default `pytest` invocation excludes them via `pyproject.toml`
addopts; CI runs the slow set on a separate matrix entry. Add the
marker to any test whose wall-clock time exceeds the budget;
otherwise the default-fast invariant erodes.

### Test-only fixtures live alongside the production ones

Files like `tests/fixtures/test_hangar_large.yaml` (30 × 25 m) exist
because the placeholder production hangar (25 × 18 m in
`data/hangar.yaml`) cannot fit all nine aircraft simultaneously
under the placeholder clearance budget. The fixture header explains
the reason; the all-nine-planes test uses this larger hangar. When
real hangar measurements arrive, this fixture-vs-production
divergence may go away — until then, keep the rationale in the
fixture header.

## Documentation discipline

### Why versus what

This documentation set (Arc42) describes *what the system is*. The
ADRs (`docs/adr/`) describe *why each load-bearing decision was made
and what alternatives were rejected*. Adding a new architectural
decision means:

1. Write an ADR with ≥ 2 considered options and a concrete rejection
   reason for each rejected one. The "≥ 2" rule is the load-bearing
   discipline — see [ADR-0000](../adr/0000-record-architecture-decisions.md).
2. Reference the ADR from the relevant Arc42 section. The Arc42
   section states the choice; the ADR explains it.

### Single source of truth per fact

Each load-bearing *rationale* lives in exactly one ADR. The
*operational statement* of the same fact may appear in code (the
collision predicate), in this Arc42 set (the parts model summary in
§8, the coordinate convention summary in §8), and in `CLAUDE.md`'s
session-context surface — but each of those is a pointer to the
canonical ADR, not a parallel source. The Arc42 §3 → §8 → ADR chain
is the canonical descent from operational view to mechanical detail.
Cross-references link rather than duplicate so that updating a
decision means updating one ADR, not chasing every restatement.

### No backwards-compat artifacts in docs

The project is pre-release; comments like "// removed" or
"deprecated since 0.x" do not belong here. When something is removed,
the removal is final and the docs reflect the current state. The
ADR record is the historical artifact — superseded ADRs stay in the
directory with their status updated; nothing else needs to.
