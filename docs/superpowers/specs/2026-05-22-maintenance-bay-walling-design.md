# Maintenance Bay Walling — Design Spec

**Date:** 2026-05-22
**Status:** Approved (brainstorming complete; ready for issue decomposition and planning)
**Touches:** `models.py`, `collisions.py`, `loader.py`, `solver.py`, `visualize.py`, `data/hangar.yaml`, fixtures, `CLAUDE.md`

---

## 1. Problem statement

Today the maintenance bay is a **soft hint**: the back-most strip of the hangar (defined by `MaintenanceBay.depth_m` alone, implicitly full-width) just constrains where a designated maintenance plane's fuselage centroid must sit. The bay area is otherwise fully usable by other planes, the occupant still counts toward the cart cap, and the visualizer paints the strip in a pale guide color.

The real-world rule is different:

- **Bay open** (no aircraft in maintenance): the bay area is just normal hangar floor — anyone may park there.
- **Bay closed** (an aircraft is in maintenance): the bay perimeter becomes a hard wall. No other aircraft's parts may cross into it. The occupant is treated as **"away"** — physically absent from the parking problem.

Additionally, the current `MaintenanceBay` model implicitly assumes the bay is the full back strip. Real hangars (including the user's own) have a partial-width bay carved out of one corner. The model must express this.

## 2. Goals and non-goals

### Goals

1. Express partial-width, back-anchored bay geometry in `MaintenanceBay`.
2. Make the bay a *conditionally* walled region — closed iff `layout.maintenance_plane is not None`.
3. Treat the maintenance occupant as away: no placement, no geometry, no cart-cap contribution, no plane drawn in the PNG.
4. Replace the two existing maintenance-related conflict kinds with a single `bay_intrusion` kind that fires when any non-occupant part has a vertex inside the closed bay rectangle.
5. Keep the change minimal, internally consistent with existing idioms (per-vertex bounds, `Door`-style center+width parametrization), and YAGNI-respecting.

### Non-goals

- No clearance buffer around the bay perimeter (hard wall, zero tolerance, mirrors `hangar_bounds`).
- No detached or free-floating bays (always touches the back wall).
- No multiple bays.
- No "solver picks who goes to maintenance" — the operator nominates `maintenance_plane` up front.
- No back-compat shims for the retired conflict kinds — project is pre-1.0, no public consumers.

## 3. Decision log (brainstorming outcomes)

| Question | Decision | Why |
|---|---|---|
| How is the occupant represented? | Removed from `Layout.placements`; only `maintenance_plane: str` tracks them. | Cleanest "is away" semantics; the placement field stops carrying two meanings. |
| Bay-perimeter strictness? | Hard wall, zero tolerance; tangent vertex is OK (strict `>`). | Mirrors existing `_hangar_bounds_conflicts` convention; one less knob. |
| Closure trigger? | `maintenance_plane is not None` is the sole trigger. No separate `bay_closed` flag. | Smallest model change; matches user's framing ("aircraft in maintenance bay"). |
| Solver behavior? | Skip occupant entirely; solve N−1 around walled bay. | Skipping is the natural consequence of the "is away" semantics — the occupant has no placement to sample, no geometry to collide, no cart slot to consume. |
| Visualizer? | Walled rect + label `IN MAINTENANCE: <plane_id>`; no aircraft shape drawn. | Reads at a glance as "blocked"; still traceable to who's in there. |
| Implementation approach? | **A** — inverted-rectangle keep-out (dual of `_hangar_bounds_conflicts`). | Tiny diff; reuses an idiom we already trust; deterministic; no Shapely edge cases. |
| Bay horizontal parametrization? | `center_x_m` + `width_m`, anchored to back wall. | Matches `Door` model in same file; one consistent idiom for sub-rectangles of the hangar. |
| Retire old conflict kinds with a tombstone? | No — just delete them. | Pre-1.0; CLAUDE.md prohibits back-compat shims when we can just change the code. |

## 4. Architecture

### 4.1 Bay geometry

The closed bay is the axis-aligned rectangle:

```
x_min = bay.center_x_m − bay.width_m / 2
x_max = bay.center_x_m + bay.width_m / 2
y_min = hangar.length_m − bay.depth_m
y_max = hangar.length_m
```

A non-occupant part vertex `(vx, vy)` is **inside** the closed bay iff:

```
x_min < vx < x_max  AND  vy > y_min
```

Strict inequalities on the three "interior" edges (left, right, front of the bay): a vertex sitting on any of these counts as outside. The bay's back edge coincides with the hangar's back wall, which is why there is no separate `vy < y_max` test — a vertex with `vy = hangar.length_m` sits on the hangar's outer wall (still inside the hangar per `_hangar_bounds_conflicts`) and is correctly treated as inside the closed bay. The convention matches the existing hangar-bounds convention where a vertex at `x == 0` or `x == width_m` is inside the hangar.

### 4.2 Two-state semantics

| State | Condition | Behavior |
|---|---|---|
| **Open** | `layout.maintenance_plane is None` | Bay rect is decorative-only (visualizer doesn't shade it). Back strip is normal hangar floor; any plane may park there. No `bay_intrusion` check runs. |
| **Closed** | `layout.maintenance_plane is not None` | Bay rect is a keep-out. Occupant is absent from `placements`, has no geometry, contributes nothing to collision or cart-cap checks. The visualizer fills the bay rect with a "wall" treatment and labels the occupant. `bay_intrusion` rule checks all other parts' vertices. |

### 4.3 Invariant flip in `Layout.__post_init__`

Today (the `if self.maintenance_plane is not None` block at the end of `Layout.__post_init__`):

```python
if self.maintenance_plane is not None:
    if self.maintenance_plane not in self.fleet:
        raise ValueError(f"maintenance_plane {self.maintenance_plane!r} not in fleet")
    if self.maintenance_plane not in seen:
        raise ValueError(f"maintenance_plane {self.maintenance_plane!r} is not placed")
```

Becomes:

```python
if self.maintenance_plane is not None:
    if self.maintenance_plane not in self.fleet:
        raise ValueError(f"maintenance_plane {self.maintenance_plane!r} not in fleet")
    if self.maintenance_plane in seen:
        raise ValueError(
            f"maintenance_plane {self.maintenance_plane!r} must NOT be in "
            f"placements when in maintenance (occupant is treated as away)"
        )
```

The `∈ fleet` invariant survives unchanged — we need the Aircraft record to label the bay in the PNG.

### 4.4 Conflict kind taxonomy

**Retired:**
- `maintenance_position` — centroid-in-bay check; no longer meaningful (occupant has no geometry).
- `maintenance_no_fuselage` — was a defensive emission for the same check; gone with it.

**Added:**
- `bay_intrusion` — single-plane conflict. One per offending part (matching the per-part granularity of `hangar_bounds`). `detail` names the part kind, the violating vertex, and the bay rectangle:
  ```
  "part 'wing' vertex (12.450, 19.200) inside closed maintenance bay
   (x ∈ (10.0, 18.0), y ∈ (16.0, 25.0); occupant=aviat_husky)"
  ```

## 5. Module-level changes

### 5.1 `src/hangarfit/models.py`

- Expand `MaintenanceBay` to `(center_x_m, width_m, depth_m)`. All three positive; `width_m` must fit within hangar (`center_x_m ± width_m/2 ∈ [0, hangar.width_m]`), `depth_m < length_m`. New `width_m` validation lives in `Hangar.__post_init__` (it needs hangar width, same shape as the existing door check).
- Flip `Layout.__post_init__` invariant per §4.3. Update the `Layout` class docstring paragraph that describes the maintenance-plane position rule to describe the new "occupant is absent" semantics.

### 5.2 `src/hangarfit/collisions.py`

- Delete `_maintenance_conflicts` and remove it from `check()`.
- Add `_bay_intrusion_conflicts(world_parts, layout)`:
  - Early-return `[]` if `layout.maintenance_plane is None`.
  - Compute the bay rect once.
  - For every plane in `world_parts` (the occupant isn't in there — `check()` builds `world_parts` from `layout.placements`, which excludes the occupant by invariant), walk every part's vertices; emit one `bay_intrusion` conflict per part with any vertex strictly inside the bay rect.
  - Re-use the same first-violating-vertex pattern as `_first_out_of_bounds_vertex`.
- Update the module docstring's "four invariants" list: rename invariant #2 from "Maintenance bay" (position rule) to "Maintenance bay intrusion" (perimeter rule), and update the description.

### 5.3 `src/hangarfit/loader.py`

- Update `MaintenanceBay` YAML parsing to accept and require the new `center_x_m` / `width_m` fields.
- Reject any layout YAML where `maintenance_plane` is named *and* appears as a `placements` entry. Clear error message pointing to the new semantics. No auto-strip.

### 5.4 `src/hangarfit/solver.py`

- When `scenario.maintenance_plane` is set:
  - Drop it from the placeable set (the solver only iterates over the other N−1 fleet members).
  - Remove `bias_to_maintenance_bay` and the `bias` branch in the per-plane sampler — there's no longer a plane to bias.
  - Remove the `maintenance_pinned` / `maint_for_check` block at the end of `_check_trivially_infeasible`: the pin-only Layout no longer needs to carry the maintenance plane through, because the maintenance plane has no placement to pin.
- The bay rectangle automatically becomes an obstacle through the new `bay_intrusion` conflict in the checker — the solver's existing "minimize conflict count" loop discovers it without further changes.

### 5.5 `src/hangarfit/visualize.py`

- Bay rendering becomes conditional:
  - **Open** (`layout.maintenance_plane is None`): don't shade the bay at all (drop the existing `_BAY_COLOR` overlay). The bay is invisible — it's just normal floor.
  - **Closed**: fill the bay rect with a saturated wall color (darker red, optionally hatched), overlay a centered label `IN MAINTENANCE: <plane_id>` in a sans-serif weight that reads against the fill.
- The existing `_BAY_COLOR` constant (and its companion `_BAY_ALPHA`) becomes unused — delete both. The closed-state wall fill uses a new constant (suggested name `_BAY_WALL_COLOR`).
- Skip drawing the occupant aircraft entirely (it isn't in `placements` so the existing draw loop already skips it — no code change there, just docstring note).

### 5.6 `src/hangarfit/cli.py`

No flag changes. `check` and `solve` both pick up the new behavior via the model + collision changes.

### 5.7 `data/hangar.yaml`

Add `center_x_m` and `width_m` under `maintenance_bay`. Values remain placeholders until real measurements arrive; use a representative right-corner bay so the new geometry exercises the partial-width path:

```yaml
maintenance_bay:
  center_x_m: 13.5   # right side of the 18 m hangar (placeholder)
  width_m: 9.0       # right half (placeholder)
  depth_m: 9.0       # back nine meters (placeholder, unchanged)
```

## 6. Fixture migration

### Retire / rewrite

| Fixture | Action |
|---|---|
| `tests/fixtures/invalid_maintenance_position.yaml` | Delete; conflict kind no longer exists. Replace with `invalid_bay_intrusion_wingtip.yaml` — a wingtip vertex sits inside a closed partial-width bay. |
| `tests/fixtures/valid_maintenance_at_bay_boundary.yaml` | Delete. Replace with `valid_part_vertex_on_bay_edge.yaml` — a wingtip vertex sits exactly on `x = x_min` or `y = y_min` (strict inequality means OK). |
| `tests/fixtures/solve_maintenance_bay_required.yaml` | Migrate to new semantics: scenario still names `maintenance_plane`; the occupant is no longer in `placements`; the solver must fit the remaining N−1 around the walled bay. |
| `tests/fixtures/solve_infeasible_maint_pin_outside_bay.yaml` | Delete (pinning the maintenance plane has no meaning if it's not placed). Replace with `solve_infeasible_bay_closes_floor.yaml` — closing the bay leaves too little floor for the remaining planes; proves the search correctly reports infeasibility. |

### New goldens

1. `valid_bay_closed_no_intruder.yaml` — closed bay, all N−1 placed planes well clear of the bay rect. Happy path.
2. `valid_bay_open_planes_in_back_strip.yaml` — `maintenance_plane: null`, a plane parked inside the area that *would* be the bay. Asserts open-bay = normal floor.
3. `valid_partial_width_bay_plane_in_side_aisle.yaml` — closed bay, but a plane parked in the back strip *next to* the bay (in the unused part of the back strip). Asserts partial-width semantics.
4. `invalid_bay_intrusion_wingtip.yaml` — as above.
5. `valid_part_vertex_on_bay_edge.yaml` — as above.
6. `solve_infeasible_bay_closes_floor.yaml` — as above.

### Test code

- `tests/test_collisions.py` cases for `maintenance_position` and `maintenance_no_fuselage` deleted; replaced with `bay_intrusion` cases (intrusion through left edge, top edge, corner; tangent-edge passes).
- `tests/test_models.py` flipped-invariant cases: occupant-in-placements raises; occupant-not-in-placements passes; `maintenance_plane in fleet but not in placements` is the valid shape.
- `tests/test_loader.py` cases for the new `MaintenanceBay` YAML schema (missing fields, out-of-hangar bay).

## 7. Docs

- **`CLAUDE.md` "The hangar" section**, the prose paragraph beneath the heading: replace the current sentence with a description of the two-state model.
- **`CLAUDE.md` module map** rows for `models.py` (MaintenanceBay fields), `collisions.py` (new `bay_intrusion` rule, retired kinds), `visualize.py` (conditional bay rendering).
- **`CLAUDE.md` "Open questions / TBD"** updated: bay measurements are now three numbers, not one.

## 8. Issue decomposition

Issues to be filed under a new milestone **"Maintenance Bay Walling"** (or similar). Each child issue is a separate PR per the GitFlow workflow. Dependency edges encoded structurally via the GraphQL `addBlockedBy` mutation.

```
#A epic / tracking
   │
   ├── #B model: MaintenanceBay (center_x_m, width_m) + Layout invariant flip
   │     │
   │     ├── #C collision rule: add bay_intrusion, delete maintenance_position
   │     │      │
   │     │      ├── #D fixtures: retire 4, add 6 new goldens, rewrite test cases
   │     │      │
   │     │      └── #G solver: skip occupant, drop bay-bias + maint-pin machinery
   │     │
   │     ├── #E loader: accept new MaintenanceBay schema, reject occupant-in-placements
   │     │
   │     └── #F visualizer: conditional bay rendering, walled-rect + label
   │
   └── #H docs: CLAUDE.md rewrite + data/hangar.yaml update
         (blocked by all of #B..#G)
```

Issue summaries:

- **#A (epic)** — Maintenance Bay Walling. Two-state bay semantics + partial-width geometry. Tracks B–H.
- **#B (model)** — Expand `MaintenanceBay` with `center_x_m` and `width_m`; add `Hangar.__post_init__` validation; flip `Layout.__post_init__` so `maintenance_plane` must NOT appear in `placements`. Update model docstrings.
- **#C (collision)** — Add `_bay_intrusion_conflicts` (per-vertex inverted-rect, strict inequalities). Delete `_maintenance_conflicts` and the `maintenance_position` / `maintenance_no_fuselage` Conflict kinds. Update module docstring's invariant list.
- **#D (fixtures + tests)** — Delete 3 fixtures, migrate 1 in place (rewrite contents under the existing filename), add 6 new goldens (intrusion, edge-tangent, partial-width side-aisle, closed-bay happy path, open-bay back-strip-usable, solver infeasibility) — net: +3 fixtures. Rewrite the corresponding `test_collisions.py`, `test_models.py`, `test_loader.py` cases.
- **#E (loader)** — Parse the new `MaintenanceBay` YAML schema. Reject layouts where `maintenance_plane` is named *and* appears in `placements`, with a clear error pointing at the new semantics.
- **#F (visualizer)** — Conditional bay rendering: open = invisible, closed = walled rect + `IN MAINTENANCE: <id>` label. No aircraft shape for the occupant.
- **#G (solver)** — Drop the maintenance plane from the placeable set; remove `bias_to_maintenance_bay` and the `maint_for_check` / `maintenance_pinned` branches. Verify infeasibility reporting via the new fixture in #D.
- **#H (docs)** — Rewrite `CLAUDE.md` "The hangar" + module-map rows; update `data/hangar.yaml` with placeholder partial-width bay values; update Open Questions to call out the three bay measurements.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Fixture migration is large and easy to half-finish | Bundle all fixture work in #D; tag with `tests` label; reviewer must verify the migration plan's counts hold (3 deletions, 1 migrate-in-place, 6 new goldens — net: +3 fixtures). |
| Partial-width bay introduces a new fixture-geometry trap (placements that *just* fit when the bay is open but not when closed) | New `valid_partial_width_bay_plane_in_side_aisle.yaml` exercises the open part of the back strip explicitly; pairs with the infeasibility fixture in #D. |
| Visualizer regression — wall fill obscures the door or hangar outline | Visual review of PRs touching `visualize.py` is already standard practice; render the canonical example layout (both `example.yaml` and a new closed-bay variant) as part of #F. |
| Solver removes `bias_to_maintenance_bay` but a residual test asserts the bias still exists | #G must scan `tests/` for `bias_to_maintenance_bay` references and clean them in the same PR. |

## 10. Out of scope

- Real bay measurements (still placeholder; covered by the broader "open questions" entry in CLAUDE.md).
- Multiple simultaneous bays.
- Bay shapes other than axis-aligned rectangles.
- Operator UX for declaring a closure mid-day; this lives in scenario YAML only.
- Clearance buffer around the bay perimeter (not needed for the wall semantics; can be added later as a third number on `MaintenanceBay` if real-world experience shows ground crew needs the gap).
