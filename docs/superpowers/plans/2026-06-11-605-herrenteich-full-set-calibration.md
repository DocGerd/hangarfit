# Herrenteich Full-Set Static Calibration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the full real Herrenteich set (8 aircraft + VW Caddy + 2 glider trailers + 1 fixed fuel trailer) pass `hangarfit check` as a single checked-in reference layout, give the four ground objects a real `data/catalog/` home, calibrate the Herrenteich clearances to make it feasible, and extend `collisions.check` to bounds/notch-check ground objects.

**Architecture:** Pure data + one small collision-checker extension. Four new ground-object catalog files (reusing #601's `car`/`trailer`/`fixed_obstacle` builders), wired into `examples/herrenteich/fleet.yaml`. A new `layout_full.yaml` places all 11 bodies in a verified-valid arrangement. `examples/herrenteich/hangar.yaml` clearances drop (0.3→0.20 horizontal, 0.2→0.15 vertical) — the empirically-established feasibility frontier for the full set, local to this hangar and monotonic-safe. `collisions.check` gains ground objects in its bounds/notch pass (byte-identical when no ground objects).

**Tech Stack:** Python 3.12, dataclasses, Shapely, pytest, ruff, mypy. No new dependencies.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `data/catalog/vw_caddy.yaml` | Create | VW Caddy Maxi `car` catalog entry |
| `data/catalog/glider_trailer_1.yaml` | Create | Glider trailer `trailer` entry (instance 1) |
| `data/catalog/glider_trailer_2.yaml` | Create | Glider trailer `trailer` entry (instance 2) |
| `data/catalog/maul_fuel_trailer.yaml` | Create | Maul fuel trailer `fixed_obstacle` entry |
| `examples/herrenteich/fleet.yaml` | Modify | Add `ground_objects:` list referencing the four |
| `examples/herrenteich/hangar.yaml` | Modify | `clearance_m` 0.3→0.20, `wing_layer_clearance_m` 0.2→0.15 |
| `examples/herrenteich/layout_full.yaml` | Create | The full-set reference layout (all 11 bodies) |
| `src/hangarfit/collisions.py` | Modify (`check`, ~L86-89) | Bounds/notch-check ground objects too |
| `tests/test_collisions_ground_object.py` | Modify | GO out-of-bounds + in-notch conflict tests |
| `tests/test_herrenteich_dataset.py` | Modify | Loader + full-set valid/notch/Caddy regression |
| `examples/herrenteich/README.md` | Modify | Point at `layout_full.yaml` calibration reference |
| `data/catalog/README.md` | Modify | Document the four ground objects |
| `CHANGELOG.md` | Modify | `[Unreleased]` entry |

**Determinism / review note:** `collisions.py` is guarded — after Task 2, the review arc must include **geometry-invariant-guard** and **silent-failure-hunter** (per CLAUDE.md). The change is bounds-only (no geometry transform, no solver/towplanner), but the guards still apply to the file.

---

## Task 1: Ground-object catalog entries + fleet wiring

**Files:**
- Create: `data/catalog/vw_caddy.yaml`, `data/catalog/glider_trailer_1.yaml`, `data/catalog/glider_trailer_2.yaml`, `data/catalog/maul_fuel_trailer.yaml`
- Modify: `examples/herrenteich/fleet.yaml`
- Test: `tests/test_herrenteich_dataset.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_herrenteich_dataset.py`:

```python
def test_ground_objects_load_from_manifest() -> None:
    """The four real ground objects load from the herrenteich fleet manifest
    with the right object_class + motion (#605)."""
    from hangarfit.loader import load_ground_objects

    gos = load_ground_objects(HERRENTEICH / "fleet.yaml")
    assert set(gos) == {
        "maul_fuel_trailer",
        "vw_caddy",
        "glider_trailer_1",
        "glider_trailer_2",
    }
    assert gos["maul_fuel_trailer"].object_class == "fixed_obstacle"
    assert gos["vw_caddy"].object_class == "placed_routed_mover"
    assert gos["vw_caddy"].motion_mode == "steerable"
    assert gos["glider_trailer_1"].motion_mode == "towed"
    assert gos["glider_trailer_2"].motion_mode == "towed"
    # each is a single solid ground footprint
    for go in gos.values():
        assert len(go.parts) == 1 and go.parts[0].kind == "ground"
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `pytest tests/test_herrenteich_dataset.py::test_ground_objects_load_from_manifest -v`
Expected: FAIL — `fleet.yaml` has no `ground_objects:` key, so `load_ground_objects` returns `{}` and the set assertion fails.

- [ ] **Step 3: Create the four catalog files**

`data/catalog/vw_caddy.yaml`:
```yaml
# VW Caddy Maxi — the club's rescue/utility vehicle, parked in the hangar.
# Dimensions: VW Caddy Maxi (long-wheelbase) manufacturer technical data.
# measured: false — published spec, not an on-site survey.
type: car
id: vw_caddy
name: "VW Caddy Maxi (rescue vehicle)"
turn_radius_m: 5.5        # carried for #602 routing; unused by static check
measured: false
parts:
  - kind: ground
    length_m: 4.88        # VW Caddy Maxi overall length (manufacturer)
    width_m: 1.79         # body width excl. mirrors (manufacturer)
    offset_x_m: 0.0
    offset_y_m: 0.0
    z_bottom_m: 0.0
    z_top_m: 1.84         # roof height (manufacturer)
```

`data/catalog/glider_trailer_1.yaml`:
```yaml
# Closed single-glider road trailer (one of two at Herrenteich). Envelope is a
# TYPICAL closed trailer for a 15–18 m glider (Cobra/Spindelberger class);
# measured: false — not an on-site survey.
type: trailer
id: glider_trailer_1
name: "Glider trailer 1 (closed)"
measured: false
parts:
  - kind: ground
    length_m: 9.0         # typical closed glider-trailer length
    width_m: 2.1          # road-legal trailer width
    offset_x_m: 0.0
    offset_y_m: 0.0
    z_bottom_m: 0.0
    z_top_m: 2.3          # closed-trailer height
```

`data/catalog/glider_trailer_2.yaml` (same envelope, second instance):
```yaml
# Second closed glider trailer at Herrenteich. Same TYPICAL envelope as
# glider_trailer_1 (no per-trailer survey); a distinct catalog object because
# ground_object_placements forbids duplicate ids. measured: false.
type: trailer
id: glider_trailer_2
name: "Glider trailer 2 (closed)"
measured: false
parts:
  - kind: ground
    length_m: 9.0
    width_m: 2.1
    offset_x_m: 0.0
    offset_y_m: 0.0
    z_bottom_m: 0.0
    z_top_m: 2.3
```

`data/catalog/maul_fuel_trailer.yaml`:
```yaml
# "Maul Tankanhänger" — the fixed fuel trailer that lives by the hangar door.
# A fixed_obstacle (immovable keep-out). Envelope is an ESTIMATE of a Maul
# road fuel trailer (not surveyed). measured: false.
type: fixed_obstacle
id: maul_fuel_trailer
name: "Maul fuel trailer (Tankanhänger)"
measured: false
parts:
  - kind: ground
    length_m: 4.5         # estimated road-trailer length
    width_m: 2.0          # estimated width
    offset_x_m: 0.0
    offset_y_m: 0.0
    z_bottom_m: 0.0
    z_top_m: 1.9          # estimated height
```

- [ ] **Step 4: Wire the manifest** — append to `examples/herrenteich/fleet.yaml` (after the `aircraft:` list):

```yaml

# Non-aircraft ground objects usually on the floor with the fleet (#605/#601).
# Referenced from the central catalog, same as aircraft. The fuel trailer is a
# fixed keep-out; the Caddy + two glider trailers are placed movers (routing
# lands in #602).
ground_objects:
  - ../../data/catalog/maul_fuel_trailer.yaml
  - ../../data/catalog/vw_caddy.yaml
  - ../../data/catalog/glider_trailer_1.yaml
  - ../../data/catalog/glider_trailer_2.yaml
```

- [ ] **Step 5: Run the test, verify it passes**

Run: `pytest tests/test_herrenteich_dataset.py::test_ground_objects_load_from_manifest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add data/catalog/vw_caddy.yaml data/catalog/glider_trailer_1.yaml \
        data/catalog/glider_trailer_2.yaml data/catalog/maul_fuel_trailer.yaml \
        examples/herrenteich/fleet.yaml tests/test_herrenteich_dataset.py
git commit -m "feat(605): real ground-object catalog entries + herrenteich manifest wiring"
```

---

## Task 2: Extend `collisions.check` to bounds/notch-check ground objects

**Files:**
- Modify: `src/hangarfit/collisions.py` (`check`, ~L86-89)
- Test: `tests/test_collisions_ground_object.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_collisions_ground_object.py` (the existing `_hangar`/`_ground_part` helpers and imports are reused; add `StructuralNotch` to the model import):

```python
def test_ground_object_out_of_bounds_flagged() -> None:
    """A ground object straddling the hangar wall is a hangar_bounds conflict
    (#605 — #601 left ground objects un-bounds-checked)."""
    hangar = _hangar()  # 40x40, no notch
    obj = GroundObject(
        id="trailer",
        name="t",
        parts=(_ground_part(length_m=4.0, width_m=2.0),),
        object_class="placed_routed_mover",
        motion_mode="towed",
    )
    layout = Layout(
        fleet={},
        hangar=hangar,
        placements=(),
        ground_objects={obj.id: obj},
        # centred on x=0.5 with a 2 m width → half the footprint is at x<0.
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=0.5, y_m=20.0, heading_deg=0.0, on_carts=False),
        ),
    )
    result = check(layout)
    kinds = {c.kind for c in result.conflicts}
    assert "hangar_bounds" in kinds
    assert any("trailer" in "".join(c.planes) for c in result.conflicts)


def test_ground_object_in_notch_flagged() -> None:
    """A ground object inside a structural notch is a structural_notch conflict."""
    from hangarfit.models import StructuralNotch

    hangar = Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=0.3,
        wing_layer_clearance_m=0.2,
        structural_notches=(
            StructuralNotch(x_min_m=30.0, y_min_m=30.0, x_max_m=40.0, y_max_m=40.0),
        ),
    )
    obj = GroundObject(
        id="caddy",
        name="c",
        parts=(_ground_part(length_m=3.0, width_m=2.0),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
    )
    layout = Layout(
        fleet={},
        hangar=hangar,
        placements=(),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=35.0, y_m=35.0, heading_deg=0.0, on_carts=False),
        ),
    )
    kinds = {c.kind for c in check(layout).conflicts}
    assert "structural_notch" in kinds


def test_fixed_obstacle_out_of_bounds_flagged() -> None:
    """A fixed obstacle is bounds-checked too (not just movers)."""
    hangar = _hangar()
    obj = GroundObject(
        id="fuel",
        name="f",
        parts=(_ground_part(length_m=4.0, width_m=2.0),),
        object_class="fixed_obstacle",
    )
    layout = Layout(
        fleet={},
        hangar=hangar,
        placements=(),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=39.7, y_m=20.0, heading_deg=0.0, on_carts=False),
        ),
    )
    assert "hangar_bounds" in {c.kind for c in check(layout).conflicts}
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `pytest tests/test_collisions_ground_object.py -k "out_of_bounds or in_notch" -v`
Expected: FAIL — ground objects are not currently bounds/notch-checked, so no `hangar_bounds`/`structural_notch` conflict is emitted.

- [ ] **Step 3: Implement the bounds extension** — in `src/hangarfit/collisions.py`, inside `check`, replace the `placed_bodies = {**aircraft_parts, **mover_parts}` line and the `_hangar_bounds_conflicts(aircraft_parts, ...)` call:

```python
    placed_bodies = {**aircraft_parts, **mover_parts}
    # Ground objects (movers AND fixed obstacles) are bounds/notch-checked
    # alongside aircraft (#605): they too must fit the floor and clear the notch.
    # Aircraft come first so the no-ground-object case is byte-identical (empty
    # GO dicts ⇒ bounded_bodies == aircraft_parts, same dict order). Bay
    # intrusion stays aircraft-only (an aircraft-occupancy rule, not geometry).
    bounded_bodies = {**aircraft_parts, **mover_parts, **obstacle_parts}

    conflicts: list[Conflict] = []
    conflicts.extend(_hangar_bounds_conflicts(bounded_bodies, layout.hangar))
    conflicts.extend(_bay_intrusion_conflicts(aircraft_parts, layout))
```

(The `_pairwise_conflicts` and `_ground_obstacle_conflicts` lines below are unchanged.)

- [ ] **Step 4: Run the new tests + the byte-identity guard, verify they pass**

Run: `pytest tests/test_collisions_ground_object.py -v`
Expected: PASS — including the existing `test_empty_ground_objects_byte_identical` (confirms the no-GO path is unchanged) and `test_separated_ground_object_is_valid` (the in-bounds GO is still valid).

- [ ] **Step 5: Run the full collision + integration suites (no regression)**

Run: `pytest tests/test_collisions.py tests/test_collisions_ground_object.py tests/test_ground_objects_integration.py tests/test_towplanner_ground_object.py -q`
Expected: PASS — all existing ground objects in those tests are in-bounds, so the extension adds no conflicts.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/collisions.py tests/test_collisions_ground_object.py
git commit -m "feat(605): bounds/notch-check ground objects in collisions.check"
```

---

## Task 3: Calibrate the Herrenteich clearances

**Files:**
- Modify: `examples/herrenteich/hangar.yaml`
- Test: `tests/test_herrenteich_dataset.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_herrenteich_dataset.py`:

```python
def test_hangar_clearances_calibrated() -> None:
    """The Herrenteich clearances were calibrated to fit the full real set
    (#605): horizontal 0.20, vertical 0.15 (placeholders were 0.30/0.20)."""
    hangar = load_hangar(HERRENTEICH / "hangar.yaml")
    assert hangar.clearance_m == 0.20
    assert hangar.wing_layer_clearance_m == 0.15
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `pytest tests/test_herrenteich_dataset.py::test_hangar_clearances_calibrated -v`
Expected: FAIL — current values are 0.3 / 0.2.

- [ ] **Step 3: Edit `examples/herrenteich/hangar.yaml`** — replace the two clearance lines (currently `clearance_m: 0.3` / `wing_layer_clearance_m: 0.2`):

```yaml
# CALIBRATED for the full real set (#605). The placeholders were 0.3 / 0.2; the
# full set (8 aircraft + Caddy + 2 glider trailers + fixed fuel trailer) is
# INFEASIBLE at 0.3/0.2 and feasible at <=0.22/0.15 (checker-driven search).
# 0.20 m lateral matches real hand-pushed club packing density; 0.15 m vertical
# fixes a too-large wing-layer clearance that falsely rejected legal z-disjoint
# wing-over-tail nestings (the real club clears fins ~0.40 m by hand, well above
# 0.15). Lowering a clearance only RELAXES the constraint, so the existing
# layout.yaml / scenario_demo.yaml stay valid; the synthetic data/ hangar is a
# separate file, untouched.
clearance_m: 0.20            # minimum horizontal gap between any two parts
wing_layer_clearance_m: 0.15 # minimum vertical gap between two parts at the same XY
```

- [ ] **Step 4: Run the calibration test + confirm existing layouts still valid (monotonic safety)**

Run:
```bash
pytest tests/test_herrenteich_dataset.py -v
hangarfit check examples/herrenteich/layout.yaml          # still: valid, exit 0
hangarfit check examples/layouts/example.yaml             # synthetic data/ untouched: valid
```
Expected: all PASS / `valid` (tightening clearance cannot invalidate a previously-valid layout).

- [ ] **Step 5: Commit**

```bash
git add examples/herrenteich/hangar.yaml tests/test_herrenteich_dataset.py
git commit -m "feat(605): calibrate herrenteich clearances (0.3/0.2 -> 0.20/0.15)"
```

---

## Task 4: The full-set reference layout

**Files:**
- Create: `examples/herrenteich/layout_full.yaml`
- Test: `tests/test_herrenteich_dataset.py`

> Coordinates below come from a checker-driven search (the documented norm for
> `layout.yaml`); the search script is NOT committed. They are verified valid at
> the calibrated 0.20/0.15 by the regression test in this task, not by the search.

- [ ] **Step 1: Write the failing test** — append to `tests/test_herrenteich_dataset.py`:

```python
FULL_SET = USUAL_OCCUPANTS | {
    "vw_caddy",
    "glider_trailer_1",
    "glider_trailer_2",
    "maul_fuel_trailer",
}


def test_full_set_layout_is_valid() -> None:
    """The full real set (8 aircraft + 4 ground objects) passes the real
    checker at the calibrated clearances (#605 primary acceptance)."""
    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    present = {p.plane_id for p in layout.placements} | {
        gp.plane_id for gp in layout.ground_object_placements
    }
    assert present == FULL_SET
    result = collisions.check(layout)
    assert result.conflicts == (), [c.kind for c in result.conflicts]


def test_full_set_ground_objects_in_bounds_and_clear_notch() -> None:
    """Independent, model-free vertex scan: every ground object is inside the
    L-shaped floor and clear of the office notch (belt-and-suspenders over the
    Task-2 checker extension)."""
    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    floor = layout.hangar.floor_polygon
    assert floor is not None
    x0, y0, x1, y1 = NOTCH
    for gp in layout.ground_object_placements:
        obj = layout.ground_objects[gp.plane_id]
        for part in aircraft_parts_world(obj, gp):
            assert floor.covers(part.polygon), f"{gp.plane_id} {part.kind} outside floor"
            for x, y in part.polygon.exterior.coords:
                assert not (x0 <= x <= x1 and y0 <= y <= y1), (
                    f"{gp.plane_id} vertex ({x:.2f},{y:.2f}) in office notch"
                )


def test_full_set_caddy_near_door() -> None:
    """SOFT intent (pre-#603): the Caddy is parked near the door — in the front
    third of the hangar and within the door's x-span — the precursor to the #603
    hard nearest-door egress gate. (Exact 'nearest' is #603's job; the 9 m glider
    trailers run along the walls toward the door, so a strict min-y assertion
    would fight that geometry.)"""
    layout = load_layout(HERRENTEICH / "layout_full.yaml")
    caddy = next(gp for gp in layout.ground_object_placements if gp.plane_id == "vw_caddy")
    assert caddy.y_m < layout.hangar.length_m / 3
    door = layout.hangar.door
    assert door.center_x_m - door.width_m / 2 <= caddy.x_m <= door.center_x_m + door.width_m / 2
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `pytest tests/test_herrenteich_dataset.py -k full_set -v`
Expected: FAIL — `layout_full.yaml` does not exist (`LoaderError`/`FileNotFoundError`).

- [ ] **Step 3: Create `examples/herrenteich/layout_full.yaml`** with this exact content (verified valid at 0.20/0.15 this session — `check`: zero conflicts, all ground objects in-bounds + notch-clear):

```yaml
# Airfield Herrenteich — the FULL real set: all 8 usual aircraft PLUS the four
# non-aircraft floor occupants (VW Caddy, two glider trailers, fixed Maul fuel
# trailer). This is the #605 calibration reference — it passes `hangarfit check`
# (exit 0) at the calibrated clearances (hangar.yaml: clearance_m 0.20,
# wing_layer_clearance_m 0.15).
#
# HOW THIS WAS PRODUCED. Like layout.yaml, this is the TOOL's arrangement, not a
# record of where the club parks: an offline search driving the real part-based
# collision checker directly found a valid all-11 packing (the product solver does
# not generate it). The aircraft are RE-NESTED relative to layout.yaml to free
# wall corridors for the 9 m glider trailers — layout.yaml (aircraft-only) is
# unchanged. Replace with real parking positions when surveyed.
#
# DEFERRED: tow-routing of this set (#602; the 18 m Scheibe is not yet routable),
# the hard Caddy nearest-door egress gate (#603 — the Caddy IS placed near the
# door here, but egress is not enforced), and rendering the ground objects (#606).
#
# Coordinate convention (see hangar.yaml): world (0,0) front-left, +x right along
# the door wall, +y deeper in; heading 0 = nose deep (+y), 90 = nose toward +x.

fleet: fleet.yaml
hangar: hangar.yaml

placements:
  - plane: scheibe_falke
    x_m: 8.18
    y_m: 22.67
    heading_deg: 90.0
    on_carts: true          # always_cart
  - plane: stemme_s10
    x_m: 3.15
    y_m: 13.65
    heading_deg: 270.0
    on_carts: false         # always_own_gear
  - plane: aviat_husky
    x_m: 5.28
    y_m: 16.51
    heading_deg: 90.0
    on_carts: false         # always_own_gear
  - plane: cessna_140
    x_m: 13.36
    y_m: 5.09
    heading_deg: 90.0
    on_carts: false         # cart_eligible, on own gear
  - plane: fk9_mkii
    x_m: 7.09
    y_m: 9.10
    heading_deg: 270.0
    on_carts: false         # cart_eligible, on own gear
  - plane: zlin_savage
    x_m: 7.41
    y_m: 25.52
    heading_deg: 270.0
    on_carts: true          # always_cart
  - plane: wild_thing
    x_m: 11.32
    y_m: 17.32
    heading_deg: 90.0
    on_carts: true          # always_cart
  - plane: ctsl
    x_m: 5.32
    y_m: 3.00
    heading_deg: 180.0
    on_carts: false         # cart_eligible, on own gear

ground_objects:
  - object: vw_caddy            # rescue vehicle near the door (egress precursor, #603)
    x_m: 11.57
    y_m: 8.26
    heading_deg: 180.0
  - object: glider_trailer_1    # along the left wall, lengthwise
    x_m: 1.20
    y_m: 7.99
    heading_deg: 180.0
  - object: glider_trailer_2    # along the left wall, deep
    x_m: 2.02
    y_m: 27.16
    heading_deg: 0.0
  - object: maul_fuel_trailer   # fixed keep-out (clear of the back-right office notch)
    x_m: 14.05
    y_m: 13.32
    heading_deg: 0.0
```

- [ ] **Step 4: Run the full-set tests, verify they pass**

Run: `pytest tests/test_herrenteich_dataset.py -k full_set -v`
Expected: PASS.

- [ ] **Step 5: CLI smoke + render**

Run: `hangarfit check examples/herrenteich/layout_full.yaml --render /tmp/full.png`
Expected: `valid`, exit 0, PNG written (ground objects are not yet rendered — #606 — but the aircraft render and the check is the gate).

- [ ] **Step 6: Commit**

```bash
git add examples/herrenteich/layout_full.yaml tests/test_herrenteich_dataset.py
git commit -m "feat(605): full-set reference layout (8 aircraft + 4 ground objects)"
```

---

## Task 5: Docs + audit trail

**Files:**
- Modify: `examples/herrenteich/README.md`, `data/catalog/README.md`, `CHANGELOG.md`

- [ ] **Step 1: `examples/herrenteich/README.md`** — in the Files table add a `layout_full.yaml` row ("the full real set — 8 aircraft + Caddy + 2 glider trailers + fixed fuel trailer — the calibration reference; passes `check` at the calibrated 0.20/0.15 clearances"); add a short prose paragraph: the full set is feasible only at the calibrated clearances (0.3/0.2 → 0.20/0.15); tow-routing of the full set and the hard Caddy-egress rule are deferred (#602/#603); ground objects are not yet rendered (#606).

- [ ] **Step 2: `data/catalog/README.md`** — document the four new ground objects (id, type, envelope, that they are `measured: false` published/typical specs) under the existing catalog listing.

- [ ] **Step 3: `CHANGELOG.md`** — add under `## [Unreleased]` → `### Added`:

```markdown
- **Herrenteich full real set + ground-object catalog (#605).** The real
  hangar's four non-aircraft occupants — a VW Caddy, two glider trailers, and a
  fixed "Maul" fuel trailer — now have `data/catalog/` entries, and a new
  `examples/herrenteich/layout_full.yaml` parks the full real set (8 aircraft +
  those four) in one arrangement that passes `hangarfit check`. `collisions.check`
  now bounds/notch-checks ground objects (previously aircraft-only). The
  Herrenteich clearances were calibrated (`clearance_m` 0.3→0.20,
  `wing_layer_clearance_m` 0.2→0.15) so the full set is feasible — the placeholder
  values were too loose to model real club packing density. Tow-routing of the
  full set, the hard Caddy nearest-door egress rule, and rendering of ground
  objects are deferred (#602/#603/#606).
```

- [ ] **Step 4: Commit**

```bash
git add examples/herrenteich/README.md data/catalog/README.md CHANGELOG.md
git commit -m "docs(605): full-set reference, catalog entries, CHANGELOG, audit trail"
```

---

## Task 6: Final verification

- [ ] **Step 1: Full test suite**

Run: `pytest -q`
Expected: all PASS (no regressions from the clearance change or the bounds extension).

- [ ] **Step 2: Lint + format + types**

Run:
```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/hangarfit/
```
Expected: clean.

- [ ] **Step 3: Run the issue's verification recipe**

Run:
```bash
hangarfit check examples/herrenteich/layout_full.yaml   # valid, exit 0
hangarfit check examples/herrenteich/layout.yaml        # still valid (monotonic)
hangarfit check examples/layouts/example.yaml           # synthetic data/ untouched
pytest tests/test_herrenteich_dataset.py -v
```
Expected: all `valid` / PASS.

- [ ] **Step 4: Open the PR (draft)** — per CLAUDE.md GitFlow: `gh pr create --draft --base develop`, body `Refs #605` (NOT `Closes` — #605 stays open for the deferred routing/egress/render phases), assignee DocGerd, labels `enhancement` + `area:backend`, milestone "Ground objects + Herrenteich calibration". Then run the review arc (code-reviewer + geometry-invariant-guard + silent-failure-hunter + comment-analyzer for the doc changes) before flipping out of draft.

---

## Self-review notes

- **Spec coverage:** §3 calibration → Task 3; §4 catalog → Task 1; §5 wiring+layout → Tasks 1+4; §6 check extension → Task 2; §7 tests → Tasks 1,2,4; §8 docs/audit → Task 5; verification → Task 6. All covered.
- **#605 stays OPEN:** PR uses `Refs #605` (routing/egress/render deferred), not `Closes`.
- **Byte-identity:** guarded by the pre-existing `test_empty_ground_objects_byte_identical` (Task 2 Step 4).
