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
kinds, `fuselage_front` (cockpit / nose) and `fuselage_aft` (cabin-aft
+ tail), so the rule can tell a wing-over-cockpit from a wing-over-tail.
`wing × fuselage_aft` keeps the two-clause rule above (a wing may
overhang another plane's tail when the heights are disjoint). But
`wing × fuselage_front` is a **hard conflict on plan-view overlap
alone — the height clause (2) is dropped**: a wing over a cockpit blocks
the canopy / prop arc / pilot ingress at *any* nesting height. This is
the one pair that ignores `z`; every other pair (including
`fuselage_* × fuselage_*`, which share a z-band by construction) uses
the uniform two-clause predicate. See
[ADR-0012](../adr/0012-fuselage-front-aft-split.md) for the rationale
and rejected alternatives.

The closed set of `PartKind` values is `{"fuselage_front",
"fuselage_aft", "wing", "strut", "tail"}`. The legacy `"fuselage"` is
**not** a constructed kind — it survives only as a transient YAML keyword
the loader auto-splits at the wing trailing-edge station
(`wing.offset_x_m − wing.length_m/2`, the #282 wing-spar precedent),
emitting an area-conserving `fuselage_front` + `fuselage_aft` pair whose
union is the original box. An aircraft with a `fuselage` part but no
`wing` part is a load error (nothing to derive the break from); explicit
`fuselage_front`/`fuselage_aft` parts in YAML are a valid override the
loader does not split. Adding a new structural element (engine nacelle,
ventral fin) is a code change in `src/hangarfit/models.py`, not just a
YAML edit — see [ADR-0001](../adr/0001-aircraft-parts-model.md) for the
parts-not-bbox rationale and [ADR-0012](../adr/0012-fuselage-front-aft-split.md)
for the front/aft refinement.

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
fuselage / tail** in plan view (the height-disjoint pass-through case)
— but *not* over its **cockpit / front fuselage**, which is a hard
conflict regardless of height (the front/aft split, ADR-0012).
Per-plane dimensions, gear types, and movement modes live in
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
wingtip legally project over a low-wing's **aft fuselage / tail**
(close in plan view, far in height) — see ADR-0001. The **one
exception** is `wing × fuselage_front`: a wing over a cockpit is a hard
conflict on plan-view overlap alone, with the height clause dropped
(ADR-0012). `wing_layer_clearance_m` therefore governs every pair
*except* wing-over-cockpit.

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
