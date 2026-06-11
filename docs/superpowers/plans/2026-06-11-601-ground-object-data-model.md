# Ground-Object Data Model + Loader (#601) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give non-aircraft ground objects (fixed obstacles + placed/routed movers) a first-class home built on the #595 catalog, wired into the existing collision checker and tow planner as a purely *additive*, byte-identical-when-empty change.

**Architecture:** A new frozen-slots `GroundObject` model (concrete catalog `type:` values `fixed_obstacle`/`car`/`trailer` → per-type builders; `object_class` derived from type). Ground objects reuse the existing `Part` footprint (via a new `"ground"` `PartKind`), the existing `Placement` pose, and the det-−1 world transform. They live in two new parallel `Layout` fields (`ground_objects` map + `ground_object_placements` tuple). Fixed obstacles become keep-outs (pairwise-set seam → `ground_obstacle` conflict); movers join pairwise collision + the tow-planner routable enumeration (route search deferred to #602).

**Tech Stack:** Python 3.12, frozen `@dataclass(slots=True)`, Shapely (polygons), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-11-601-ground-object-data-model-design.md`

---

## Conventions for every task

- **Branch:** `feature/601-ground-object-data-model` (already created off `develop`).
- **Run a single test:** `pytest tests/<file>::<test> -v`
- **Run a module's tests:** `pytest tests/test_models.py -v`
- **Full fast suite (pre-commit safety):** `pytest`
- **Lint/format/type after each task:** `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`
- **Do NOT** `git add -A` — the working tree carries the user's local `CLAUDE.md` + `.claude/settings.json` edits that must not be committed. Always `git add` the **explicit paths** listed in each Commit step.
- The repo's pre-commit hook runs ruff; a PostToolUse hook runs ruff+pytest after `src/`/`tests/` edits; a Stop hook runs mypy. Let them run.

---

## Test fixtures (READ FIRST — the snippets below use shorthand names)

The test snippets in this plan use shorthand fixture names (`minimal_hangar`, `tiny_aircraft`, `low_wing_aircraft`, `existing_valid_layout`). **These are NOT pytest fixtures — they do not exist.** `tests/conftest.py` provides only plain factory *functions*, constructed on demand (the suite deliberately avoids module-level model construction — see the conftest docstring). Resolve every shorthand as follows:

```python
# Aircraft factory (a function, NOT a fixture). Import exactly like sibling tests:
from tests.conftest import make_test_aircraft
# tiny_aircraft / low_wing_aircraft → make_test_aircraft(id="p1", wing_position="high")
#   (override wing_position="low", gear=..., movement_mode=... as a case needs)

# There is NO hangar fixture — build one inline (copied from tests/test_collisions.py:_hangar):
from hangarfit.models import Door, Hangar, MaintenanceBay

def _hangar(clearance: float = 0.3, wlc: float = 0.2) -> Hangar:
    return Hangar(
        length_m=40.0,
        width_m=40.0,
        door=Door(center_x_m=20.0, width_m=12.0),
        maintenance_bay=MaintenanceBay(center_x_m=20.0, width_m=8.0, depth_m=6.0),
        clearance_m=clearance,
        wing_layer_clearance_m=wlc,
    )
# minimal_hangar → _hangar()
```

For each test snippet: drop the fixture-style function parameters (`def test_x(minimal_hangar, tiny_aircraft)`) and instead build locals at the top of the test body — `hangar = _hangar()`, `ac = make_test_aircraft(id="p1")`. Place planes/objects at world coords well inside `0..40 × 0..40`. For `existing_valid_layout` (the byte-identity test in Task 9), load a checked-in valid layout (e.g. `load_layout(Path("examples/layouts/example.yaml"))`) or build one inline with `_hangar()` + one `make_test_aircraft()` placed clear — assert `check(it).valid` and `conflicts == ()`.

> The factory's aircraft has wing z 2.0–2.2 and fuselage z 0–1.0 (see `_default_parts`). To force an aircraft-vs-ground-object overlap, give the ground footprint `z_top_m ≥ 2.2` (so it meets the wing layer) and place it under the wing; to force a *clear* case, keep the ground object's `(x,y)` far from the plane.

---

## File structure (what each touched file owns)

- `src/hangarfit/models.py` — adds `GroundObject`, the `"ground"` `PartKind`, the `GroundObjectClass`/`MoverMotionMode` literals, and the `Layout`/`Scenario` ground-object fields + invariants.
- `src/hangarfit/loader.py` — adds the catalog builder registry, the three `_build_*` ground-object builders + allowlists, `load_ground_objects`, and scenario/layout `ground_objects:` parsing.
- `src/hangarfit/geometry.py` — widens `aircraft_parts_world` to accept a `GroundObject`.
- `src/hangarfit/collisions.py` — builds ground-object world parts, adds `_ground_obstacle_conflicts`, feeds movers into pairwise.
- `src/hangarfit/towplanner.py` — adds fixed-obstacle/mover static obstacles to `_build_obstacles`; registers movers in `plan_fill`'s routable enumeration (deferred search).
- `src/hangarfit/metrics.py` / `visualize.py` / `scene.py` — audited for `"ground"` `PartKind` fallout (Task 11).
- `tests/fixtures/catalog/*.yaml` — fixture ground objects.
- `docs/adr/0025-ground-object-taxonomy.md`, `docs/architecture/05-*.md`, `08-*.md`, `data/catalog/README.md`, `CHANGELOG.md` — docs.

---

## Task 1: `GroundObject` model + `"ground"` PartKind

**Files:**
- Modify: `src/hangarfit/models.py` (literals near line 29-40; new class after `Aircraft`, ~line 417)
- Test: `tests/test_models_ground_object.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_models_ground_object.py`:

```python
"""GroundObject model + validation (#601)."""

import pytest

from hangarfit.models import GroundObject, Part


def _rect_part(*, kind: str = "ground", length_m: float = 4.0, width_m: float = 2.0) -> Part:
    return Part(
        kind=kind,  # type: ignore[arg-type]
        length_m=length_m,
        width_m=width_m,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=0.0,
        z_top_m=1.5,
    )


def test_fixed_obstacle_constructs() -> None:
    obj = GroundObject(
        id="fuel_trailer",
        name="Fuel trailer",
        parts=(_rect_part(),),
        object_class="fixed_obstacle",
    )
    assert obj.object_class == "fixed_obstacle"
    assert obj.motion_mode is None
    assert obj.turn_radius_m is None


def test_mover_constructs_with_motion() -> None:
    obj = GroundObject(
        id="vw_caddy",
        name="VW Caddy",
        parts=(_rect_part(),),
        object_class="placed_routed_mover",
        motion_mode="steerable",
        turn_radius_m=4.5,
    )
    assert obj.motion_mode == "steerable"
    assert obj.turn_radius_m == 4.5


def test_ground_partkind_is_valid() -> None:
    # A "ground" footprint Part must construct without error.
    assert _rect_part(kind="ground").kind == "ground"


@pytest.mark.parametrize(
    "kwargs, msg",
    [
        (dict(id="", name="x", parts=(_rect_part(),), object_class="fixed_obstacle"), "id"),
        (dict(id="x", name="", parts=(_rect_part(),), object_class="fixed_obstacle"), "name"),
        (dict(id="x", name="x", parts=(), object_class="fixed_obstacle"), "parts"),
        (
            dict(id="x", name="x", parts=(_rect_part(),), object_class="bogus"),
            "object_class",
        ),
        (
            dict(
                id="x", name="x", parts=(_rect_part(),),
                object_class="fixed_obstacle", motion_mode="towed",
            ),
            "fixed_obstacle",  # a fixed obstacle must not carry motion
        ),
        (
            dict(
                id="x", name="x", parts=(_rect_part(),),
                object_class="placed_routed_mover",
            ),
            "motion_mode",  # a mover must carry motion
        ),
        (
            dict(
                id="x", name="x", parts=(_rect_part(),),
                object_class="placed_routed_mover", motion_mode="steerable",
                turn_radius_m=-1.0,
            ),
            "turn_radius_m",
        ),
    ],
)
def test_invalid_ground_object_rejected(kwargs: dict, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        GroundObject(**kwargs)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models_ground_object.py -v`
Expected: FAIL — `ImportError: cannot import name 'GroundObject'` (and `Part(kind="ground")` would raise once import works).

- [ ] **Step 3: Add the `"ground"` PartKind**

In `src/hangarfit/models.py`, change the `PartKind` literal (currently line 35) to add `"ground"`:

```python
PartKind = Literal[
    "fuselage_front", "fuselage_aft", "wing", "strut", "tail", "vertical_stabilizer", "ground"
]
```

(The `_VALID_PART_KINDS = frozenset(typing.get_args(PartKind))` line below it picks `"ground"` up automatically.) Update the `PartKind` comment above it to note: `"ground"` denotes a non-aircraft ground-object footprint (a solid keep-out/body, never overhangable — #601.

- [ ] **Step 4: Add the literals + `GroundObject` class**

In `src/hangarfit/models.py`, after the `MovementMode` literal (line 31) add:

```python
GroundObjectClass = Literal["fixed_obstacle", "placed_routed_mover"]
MoverMotionMode = Literal["steerable", "towed"]
```

And after the `_VALID_MOVEMENT_MODES` line (line 40) add:

```python
_VALID_GROUND_OBJECT_CLASSES = frozenset(typing.get_args(GroundObjectClass))
_VALID_MOVER_MOTION_MODES = frozenset(typing.get_args(MoverMotionMode))
```

Then add the class immediately after the `Aircraft` class (after line 417, before `class Door`):

```python
@dataclass(frozen=True, slots=True)
class GroundObject:
    """A non-aircraft object on the hangar floor (#601 / ADR-0025).

    Two classes, set by ``object_class`` (derived by the loader from the
    catalog ``type:`` — ``fixed_obstacle`` → ``"fixed_obstacle"``;
    ``car``/``trailer`` → ``"placed_routed_mover"``):

    * **fixed_obstacle** — a placed-but-immovable keep-out (e.g. a fuel
      trailer at the door). Carries no motion. Its world footprint is a
      keep-out for aircraft/mover parts; the tow planner routes around it.
    * **placed_routed_mover** — a placed body that is itself routed (a
      self-driving ``steerable`` car, a ``towed`` trailer). Participates in
      pairwise collision like an aircraft. ``motion_mode`` is carried here;
      the actual route search lands in #602 (this type only carries the field).

    Geometry reuses the parts model: ``parts`` is a tuple of ``Part`` with
    ``kind="ground"`` (a solid footprint, never overhangable), transformed to
    world coords by the *same* :func:`hangarfit.geometry.aircraft_parts_world`
    path aircraft use. ``turn_radius_m`` is static catalog data carried for
    #602's routing; it is unused in #601.
    """

    id: str
    name: str
    parts: tuple[Part, ...]
    object_class: GroundObjectClass
    motion_mode: MoverMotionMode | None = None
    turn_radius_m: float | None = None
    measured: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("GroundObject.id must be non-empty")
        if not self.name:
            raise ValueError(f"GroundObject {self.id!r}: name must be non-empty")
        if not self.parts:
            raise ValueError(f"GroundObject {self.id!r}: parts must be non-empty")
        if self.object_class not in _VALID_GROUND_OBJECT_CLASSES:
            raise ValueError(
                f"GroundObject {self.id!r}: object_class must be one of "
                f"{sorted(_VALID_GROUND_OBJECT_CLASSES)}, got {self.object_class!r}"
            )
        if self.object_class == "fixed_obstacle":
            if self.motion_mode is not None:
                raise ValueError(
                    f"GroundObject {self.id!r}: a fixed_obstacle must not carry a "
                    f"motion_mode (got {self.motion_mode!r}) — it never moves"
                )
            if self.turn_radius_m is not None:
                raise ValueError(
                    f"GroundObject {self.id!r}: a fixed_obstacle must not carry a "
                    f"turn_radius_m — it never moves"
                )
        else:  # placed_routed_mover
            if self.motion_mode not in _VALID_MOVER_MOTION_MODES:
                raise ValueError(
                    f"GroundObject {self.id!r}: a placed_routed_mover requires a "
                    f"motion_mode in {sorted(_VALID_MOVER_MOTION_MODES)}, "
                    f"got {self.motion_mode!r}"
                )
        if self.turn_radius_m is not None and self.turn_radius_m <= 0:
            raise ValueError(
                f"GroundObject {self.id!r}: turn_radius_m must be positive, "
                f"got {self.turn_radius_m}"
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_models_ground_object.py -v`
Expected: PASS (all cases).

- [ ] **Step 6: Confirm the new PartKind broke nothing**

Run: `pytest && mypy src/hangarfit/`
Expected: PASS. If mypy flags a non-exhaustive `match`/dict on `PartKind` in `metrics.py`/`visualize.py`/`scene.py`, note the file — it is handled in **Task 11**. If the *test suite* fails (not just mypy), fix the immediate fallout now (most likely none: `_OVERHANGABLE` is a frozenset that simply excludes `"ground"`, the correct default).

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/models.py tests/test_models_ground_object.py
git commit -m "feat(601): GroundObject model + 'ground' PartKind

Refs #601"
```

---

## Task 2: `Layout` ground-object fields + invariants

**Files:**
- Modify: `src/hangarfit/models.py` (`Layout`, lines 706-806)
- Test: `tests/test_models_ground_object.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models_ground_object.py`. Add imports at top: `from hangarfit.models import Aircraft, Hangar, Layout, Placement, Wheels` plus whatever the existing test fixtures use. Use the existing test helpers if a `conftest.py` builds a minimal `Hangar`/`Aircraft`; otherwise build them inline. Reuse the repo's existing minimal-fixture helpers — grep `tests/conftest.py` for a `make_hangar`/`tiny_aircraft` factory first.

```python
def _mover(obj_id: str = "caddy") -> GroundObject:
    return GroundObject(
        id=obj_id, name="Caddy", parts=(_rect_part(),),
        object_class="placed_routed_mover", motion_mode="steerable",
    )


def test_layout_accepts_ground_objects(minimal_hangar, tiny_aircraft) -> None:
    # minimal_hangar / tiny_aircraft: reuse existing conftest fixtures (adjust
    # names to the repo's actual fixtures).
    obj = _mover()
    layout = Layout(
        fleet={tiny_aircraft.id: tiny_aircraft},
        hangar=minimal_hangar,
        placements=(Placement(plane_id=tiny_aircraft.id, x_m=5.0, y_m=5.0, heading_deg=0.0, on_carts=False),),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=2.0, y_m=2.0, heading_deg=0.0, on_carts=False),
        ),
    )
    assert layout.ground_objects[obj.id] is obj
    assert len(layout.ground_object_placements) == 1


def test_layout_rejects_ground_key_id_mismatch(minimal_hangar, tiny_aircraft) -> None:
    obj = _mover("caddy")
    with pytest.raises(ValueError, match="ground_object"):
        Layout(
            fleet={tiny_aircraft.id: tiny_aircraft}, hangar=minimal_hangar, placements=(),
            ground_objects={"WRONG": obj}, ground_object_placements=(),
        )


def test_layout_rejects_ground_placement_unknown_id(minimal_hangar, tiny_aircraft) -> None:
    obj = _mover("caddy")
    with pytest.raises(ValueError, match="unknown"):
        Layout(
            fleet={tiny_aircraft.id: tiny_aircraft}, hangar=minimal_hangar, placements=(),
            ground_objects={obj.id: obj},
            ground_object_placements=(
                Placement(plane_id="ghost", x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False),
            ),
        )


def test_layout_rejects_ground_id_colliding_with_fleet(minimal_hangar, tiny_aircraft) -> None:
    # Ground-object id must be disjoint from fleet ids.
    obj = GroundObject(
        id=tiny_aircraft.id, name="clash", parts=(_rect_part(),), object_class="fixed_obstacle",
    )
    with pytest.raises(ValueError, match="disjoint|collide|both"):
        Layout(
            fleet={tiny_aircraft.id: tiny_aircraft}, hangar=minimal_hangar, placements=(),
            ground_objects={obj.id: obj}, ground_object_placements=(),
        )


def test_layout_empty_ground_objects_default(minimal_hangar, tiny_aircraft) -> None:
    layout = Layout(fleet={tiny_aircraft.id: tiny_aircraft}, hangar=minimal_hangar, placements=())
    assert dict(layout.ground_objects) == {}
    assert layout.ground_object_placements == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models_ground_object.py -k ground_object -v`
Expected: FAIL — `TypeError: Layout.__init__() got an unexpected keyword argument 'ground_objects'`.

- [ ] **Step 3: Add the fields + invariants**

In `src/hangarfit/models.py` `Layout` (line 706), add two fields after `maintenance_plane` (line 737):

```python
    ground_objects: Mapping[str, GroundObject] = field(default_factory=dict)
    ground_object_placements: tuple[Placement, ...] = ()
```

Add `"ground_objects"` to `_PROXY_FIELDS` (line 743):

```python
    _PROXY_FIELDS: typing.ClassVar[tuple[str, ...]] = ("fleet", "ground_objects")
```

In `Layout.__post_init__`, add a validation block **before** the final `_PROXY_FIELDS` wrap loop (before line 797). The wrap loop already handles `ground_objects` because it iterates `_PROXY_FIELDS`:

```python
        # Ground objects (#601): keys equal their id; placements resolve;
        # ids disjoint from the fleet so a placement id resolves unambiguously.
        for k, obj in self.ground_objects.items():
            if obj.id != k:
                raise ValueError(
                    f"ground_objects key {k!r} does not match its GroundObject.id "
                    f"({obj.id!r}); keys must equal their ground-object id"
                )
        fleet_ids = set(self.fleet)
        ground_ids = set(self.ground_objects)
        clash = fleet_ids & ground_ids
        if clash:
            raise ValueError(
                f"ground-object id(s) {sorted(clash)} collide with fleet aircraft ids; "
                f"ids must be disjoint so a placement resolves to exactly one object"
            )
        seen_ground: set[str] = set()
        for gp in self.ground_object_placements:
            if gp.plane_id not in self.ground_objects:
                raise ValueError(
                    f"ground_object_placement references unknown id {gp.plane_id!r} "
                    f"(ground_objects has: {sorted(self.ground_objects)})"
                )
            if gp.plane_id in seen_ground:
                raise ValueError(
                    f"Duplicate ground_object_placement for id {gp.plane_id!r}"
                )
            seen_ground.add(gp.plane_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models_ground_object.py -k ground_object -v`
Expected: PASS.

- [ ] **Step 5: Confirm pickle round-trip + no regressions**

Ground objects ride the `_PROXY_FIELDS` pickle path (`__getstate__`/`__setstate__`). Add a quick pickle test:

```python
def test_layout_with_ground_objects_pickles(minimal_hangar, tiny_aircraft) -> None:
    import pickle
    obj = _mover()
    layout = Layout(
        fleet={tiny_aircraft.id: tiny_aircraft}, hangar=minimal_hangar, placements=(),
        ground_objects={obj.id: obj},
        ground_object_placements=(
            Placement(plane_id=obj.id, x_m=1.0, y_m=1.0, heading_deg=0.0, on_carts=False),
        ),
    )
    back = pickle.loads(pickle.dumps(layout))
    assert back.ground_objects[obj.id].id == obj.id
    assert back.ground_object_placements[0].plane_id == obj.id
```

Run: `pytest tests/test_models_ground_object.py -v && pytest tests/test_models.py -v`
Expected: PASS (existing Layout tests unaffected — new fields default empty).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/models.py tests/test_models_ground_object.py
git commit -m "feat(601): Layout ground-object fields + invariants (proxy/pickle)

Refs #601"
```

---

## Task 3: `Scenario` ground-object id-list

**Files:**
- Modify: `src/hangarfit/models.py` (`Scenario`, lines 920-1088)
- Test: `tests/test_models_ground_object.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from hangarfit.models import Scenario


def test_scenario_ground_objects_idlist(minimal_hangar, tiny_aircraft) -> None:
    obj = _mover()
    scn = Scenario(
        fleet={tiny_aircraft.id: tiny_aircraft},
        hangar=minimal_hangar,
        fleet_in=(tiny_aircraft.id,),
        ground_objects=(obj.id,),
        ground_object_defs={obj.id: obj},
    )
    assert scn.ground_objects == (obj.id,)


def test_scenario_rejects_unknown_ground_object_ref(minimal_hangar, tiny_aircraft) -> None:
    with pytest.raises(ValueError, match="ground_object"):
        Scenario(
            fleet={tiny_aircraft.id: tiny_aircraft}, hangar=minimal_hangar,
            fleet_in=(tiny_aircraft.id,),
            ground_objects=("ghost",), ground_object_defs={},
        )
```

> Design note: the `Scenario` needs both the *id-list* (`ground_objects`) and the *defs* (`ground_object_defs`, the `GroundObject` map) to validate the references and to forward them to a built `Layout` later. `ground_object_defs` mirrors `fleet`; `ground_objects` mirrors `fleet_in`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models_ground_object.py -k scenario -v`
Expected: FAIL — unexpected keyword argument `ground_objects`.

- [ ] **Step 3: Add the fields + invariant**

In `Scenario` (line 945), after `constraints` (line 949) add:

```python
    ground_objects: tuple[str, ...] = ()
    ground_object_defs: Mapping[str, GroundObject] = field(default_factory=lambda: MappingProxyType({}))
```

Add `ground_object_defs` to `_PROXY_FIELDS` (line 956):

```python
    _PROXY_FIELDS: typing.ClassVar[tuple[str, ...]] = ("fleet", "constraints", "ground_object_defs")
```

In `Scenario.__post_init__`, add validation before the final wrap loop (before line 1079):

```python
        # Ground objects (#601): every referenced id has a def; keys match ids.
        for k, obj in self.ground_object_defs.items():
            if obj.id != k:
                raise ValueError(
                    f"Scenario.ground_object_defs key {k!r} != GroundObject.id ({obj.id!r})"
                )
        for gid in self.ground_objects:
            if gid not in self.ground_object_defs:
                raise ValueError(
                    f"Scenario.ground_objects references unknown ground_object {gid!r}; "
                    f"defs have: {sorted(self.ground_object_defs)}"
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models_ground_object.py -k scenario -v`
Expected: PASS.

- [ ] **Step 5: Regression check**

Run: `pytest tests/test_models.py tests/test_solver.py -v && mypy src/hangarfit/`
Expected: PASS (existing Scenario callers unaffected — new fields default empty).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/models.py tests/test_models_ground_object.py
git commit -m "feat(601): Scenario ground-object id-list + defs

Refs #601"
```

---

## Task 4: Catalog builder registry + 3 ground-object builders

**Files:**
- Modify: `src/hangarfit/loader.py` (`_build_catalog_object` 157-172; new builders + allowlists near `_build_aircraft` 1044)
- Test: `tests/test_loader_catalog.py` (extend) + `tests/fixtures/catalog/` (new fixtures)

- [ ] **Step 1: Create fixture catalog entries**

Create `tests/fixtures/catalog/fixture_fuel_trailer.yaml`:

```yaml
type: fixed_obstacle
id: fixture_fuel_trailer
name: "Fixture fuel trailer"
measured: false
parts:
  - kind: ground
    length_m: 5.0
    width_m: 2.0
    offset_x_m: 0.0
    offset_y_m: 0.0
    z_bottom_m: 0.0
    z_top_m: 2.0
```

Create `tests/fixtures/catalog/fixture_caddy.yaml`:

```yaml
type: car
id: fixture_caddy
name: "Fixture rescue car"
turn_radius_m: 5.0
measured: false
parts:
  - kind: ground
    length_m: 4.5
    width_m: 1.8
    offset_x_m: 0.0
    offset_y_m: 0.0
    z_bottom_m: 0.0
    z_top_m: 1.8
```

Create `tests/fixtures/catalog/fixture_glider_trailer.yaml`:

```yaml
type: trailer
id: fixture_glider_trailer
name: "Fixture glider trailer"
measured: false
parts:
  - kind: ground
    length_m: 8.0
    width_m: 2.2
    offset_x_m: 0.0
    offset_y_m: 0.0
    z_bottom_m: 0.0
    z_top_m: 2.5
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_loader_catalog.py` (it already has `test_unknown_type_is_stage_a_error`-style tests; reuse its `_FIXTURE_DIR`/read helpers — grep the file for how it loads a single catalog file, e.g. a `_build_catalog_object(_read_yaml(path), source=path)` helper or `load_fleet` on a tiny manifest):

```python
from pathlib import Path

from hangarfit.loader import _build_catalog_object, _read_yaml
from hangarfit.models import GroundObject

_CAT = Path(__file__).parent / "fixtures" / "catalog"


def _build(name: str) -> object:
    p = _CAT / name
    return _build_catalog_object(_read_yaml(p), source=p)


def test_fixed_obstacle_loads() -> None:
    obj = _build("fixture_fuel_trailer.yaml")
    assert isinstance(obj, GroundObject)
    assert obj.object_class == "fixed_obstacle"
    assert obj.motion_mode is None
    assert obj.parts[0].kind == "ground"


def test_car_loads_with_steerable_default() -> None:
    obj = _build("fixture_caddy.yaml")
    assert isinstance(obj, GroundObject)
    assert obj.object_class == "placed_routed_mover"
    assert obj.motion_mode == "steerable"   # car default
    assert obj.turn_radius_m == 5.0


def test_trailer_loads_with_towed_default() -> None:
    obj = _build("fixture_glider_trailer.yaml")
    assert isinstance(obj, GroundObject)
    assert obj.object_class == "placed_routed_mover"
    assert obj.motion_mode == "towed"   # trailer default
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_loader_catalog.py -k "fixed_obstacle or car_loads or trailer_loads" -v`
Expected: FAIL — `_build_catalog_object` raises `LoaderError("object type 'fixed_obstacle' not yet supported …")`.

- [ ] **Step 4: Add the allowlists + builders + registry**

In `src/hangarfit/loader.py`, near `_build_aircraft` (line 1044) add the new allowlists and builders. Reuse the existing `_build_part` helper (used by `_build_aircraft`) and the `_to_float`/`_to_bool` coercers:

```python
_ALLOWED_FIXED_OBSTACLE_KEYS = frozenset({"id", "name", "parts", "measured"})
_ALLOWED_MOVER_KEYS = frozenset({"id", "name", "parts", "measured", "motion_mode", "turn_radius_m"})


def _build_ground_parts(entry: dict[str, Any]) -> tuple[Part, ...]:
    """Parse a ground object's ``parts`` list. Every part must be kind 'ground'
    (a solid footprint) — no fuselage split, no struts, no wings."""
    parts_data = entry.get("parts")
    if not isinstance(parts_data, list) or not parts_data:
        raise LoaderError("'parts' must be a non-empty list")
    parts = tuple(_build_part(p, i) for i, p in enumerate(parts_data))
    for i, part in enumerate(parts):
        if part.kind != "ground":
            raise LoaderError(
                f"parts[{i}] kind {part.kind!r} not allowed on a ground object; "
                f"use kind: ground (a solid footprint)"
            )
    return parts


def _check_ground_keys(entry: Any, allowed: frozenset[str], *, what: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise LoaderError(f"{what} entry must be a mapping, got {type(entry).__name__}")
    unknown = set(entry) - allowed
    if unknown:
        raise LoaderError(
            f"unknown {what} key(s) {sorted(unknown)}; allowed: {sorted(allowed)}"
        )
    for key in ("id", "name"):
        if key not in entry:
            raise LoaderError(f"missing required field {key!r}")
    return entry


def _build_fixed_obstacle(entry: Any) -> GroundObject:
    entry = _check_ground_keys(entry, _ALLOWED_FIXED_OBSTACLE_KEYS, what="fixed_obstacle")
    return GroundObject(
        id=entry["id"],
        name=entry["name"],
        parts=_build_ground_parts(entry),
        object_class="fixed_obstacle",
        measured=_to_bool(entry.get("measured", False), "measured"),
    )


def _build_mover(entry: Any, *, default_motion: MoverMotionMode) -> GroundObject:
    entry = _check_ground_keys(entry, _ALLOWED_MOVER_KEYS, what="mover")
    motion_raw = entry.get("motion_mode", default_motion)
    tr_raw = entry.get("turn_radius_m")
    return GroundObject(
        id=entry["id"],
        name=entry["name"],
        parts=_build_ground_parts(entry),
        object_class="placed_routed_mover",
        motion_mode=motion_raw,
        turn_radius_m=None if tr_raw is None else _to_float(tr_raw, "turn_radius_m"),
        measured=_to_bool(entry.get("measured", False), "measured"),
    )


def _build_car(entry: Any) -> GroundObject:
    return _build_mover(entry, default_motion="steerable")


def _build_trailer(entry: Any) -> GroundObject:
    return _build_mover(entry, default_motion="towed")
```

Add the imports at the top of `loader.py`: `GroundObject` and `MoverMotionMode` to the existing `from .models import (...)` block, and `Part` if not already imported there.

Now replace the Stage-A guard in `_build_catalog_object` (lines 157-172). Define a registry just above it and dispatch:

```python
_CATALOG_BUILDERS: dict[str, Callable[[dict[str, Any]], Aircraft | GroundObject]] = {
    "aircraft": _build_aircraft,
    "fixed_obstacle": _build_fixed_obstacle,
    "car": _build_car,
    "trailer": _build_trailer,
}


def _build_catalog_object(raw: Any, *, source: Path) -> Aircraft | GroundObject:
    """Dispatch a catalog object on its ``type:`` discriminator to the per-type
    builder (#595 seam; ground-object types added in #601). ``type:`` is stripped
    before the builder runs, so each per-type allowlist needs no ``type`` member."""
    if not isinstance(raw, dict):
        raise LoaderError(f"{source}: catalog object must be a mapping, got {type(raw).__name__}")
    obj_type = raw.get("type", _DEFAULT_OBJECT_TYPE)
    builder = _CATALOG_BUILDERS.get(obj_type)
    if builder is None:
        raise LoaderError(
            f"{source}: unknown catalog type {obj_type!r}; "
            f"known types: {sorted(_CATALOG_BUILDERS)}"
        )
    entry = {k: v for k, v in raw.items() if k != "type"}
    return builder(entry)
```

> Note: `_CATALOG_BUILDERS` must be defined **after** the four builder functions (Python resolves the names at dict-construction time). Put the registry + the rewritten `_build_catalog_object` at the bottom, after `_build_aircraft`/`_build_*` are all defined, and **delete** the old `_build_catalog_object` at line 157. Add `from collections.abc import Callable` (or `from typing import Callable`) to the imports if absent.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_loader_catalog.py -k "fixed_obstacle or car_loads or trailer_loads" -v`
Expected: PASS.

- [ ] **Step 6: Update the unknown-type test + add allowlist tests**

The existing `test_unknown_type_is_stage_a_error` (or similarly named) asserts the old "Stage A #600" message. Update it to assert the new behaviour:

```python
def test_unknown_catalog_type_lists_known_types() -> None:
    p = _CAT / "fixture_bogus_type.yaml"  # create: type: spaceship + minimal parts
    with pytest.raises(LoaderError, match="unknown catalog type 'spaceship'.*known types"):
        _build_catalog_object(_read_yaml(p), source=p)


def test_mover_motion_mode_override() -> None:
    # a trailer authored with motion_mode: steerable keeps the override
    ...  # build from an inline dict: {"type":"trailer", ..., "motion_mode":"steerable"}


def test_fixed_obstacle_rejects_motion_key() -> None:
    bad = {"type": "fixed_obstacle", "id": "x", "name": "x",
           "parts": [{"kind": "ground", "length_m": 1, "width_m": 1,
                      "offset_x_m": 0, "offset_y_m": 0, "z_bottom_m": 0, "z_top_m": 1}],
           "motion_mode": "towed"}
    with pytest.raises(LoaderError, match="unknown fixed_obstacle key"):
        _build_catalog_object(bad, source=Path("inline"))


def test_ground_object_rejects_aircraft_part_kind() -> None:
    bad = {"type": "car", "id": "x", "name": "x",
           "parts": [{"kind": "wing", "length_m": 1, "width_m": 1,
                      "offset_x_m": 0, "offset_y_m": 0, "z_bottom_m": 0, "z_top_m": 1}]}
    with pytest.raises(LoaderError, match="not allowed on a ground object"):
        _build_catalog_object(bad, source=Path("inline"))
```

Create `tests/fixtures/catalog/fixture_bogus_type.yaml` (`type: spaceship`, valid `id`/`name`/one `ground` part).

Run: `pytest tests/test_loader_catalog.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_catalog.py tests/fixtures/catalog/
git commit -m "feat(601): catalog builder registry + fixed_obstacle/car/trailer builders

Refs #601"
```

---

## Task 5: Manifest `ground_objects:` + `load_ground_objects`

**Files:**
- Modify: `src/hangarfit/loader.py` (`load_fleet` 214-271; new `load_ground_objects`)
- Test: `tests/test_loader_catalog.py` (extend) + `tests/fixtures/` manifest

- [ ] **Step 1: Create a fixture manifest**

Create `tests/fixtures/catalog/fixture_ground_manifest.yaml`:

```yaml
aircraft: []
ground_objects:
  - fixture_fuel_trailer.yaml
  - fixture_caddy.yaml
  - fixture_glider_trailer.yaml
```

(Refs are relative to the manifest's directory — same as `aircraft:` refs.)

- [ ] **Step 2: Write the failing test**

```python
from hangarfit.loader import load_ground_objects


def test_load_ground_objects_resolves_manifest() -> None:
    gobjs = load_ground_objects(_CAT / "fixture_ground_manifest.yaml")
    assert set(gobjs) == {"fixture_fuel_trailer", "fixture_caddy", "fixture_glider_trailer"}
    assert gobjs["fixture_caddy"].object_class == "placed_routed_mover"


def test_load_ground_objects_absent_key_is_empty(tmp_path) -> None:
    m = tmp_path / "m.yaml"
    m.write_text("aircraft: []\n")
    assert load_ground_objects(m) == {}


def test_load_fleet_rejects_ground_object_under_aircraft(tmp_path) -> None:
    # A ground-object ref listed under aircraft: must fail loudly.
    import shutil
    shutil.copy(_CAT / "fixture_fuel_trailer.yaml", tmp_path / "ft.yaml")
    m = tmp_path / "m.yaml"
    m.write_text("aircraft:\n  - ft.yaml\n")
    with pytest.raises(LoaderError, match="aircraft|not an aircraft|ground"):
        load_fleet(m)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_loader_catalog.py -k "load_ground_objects or ground_object_under_aircraft" -v`
Expected: FAIL — `ImportError: cannot import name 'load_ground_objects'`.

- [ ] **Step 4: Implement `load_ground_objects` + the defensive guard in `load_fleet`**

In `load_fleet` (after building each object, ~line 265-270), add a type guard so an accidental ground-object ref under `aircraft:` fails:

```python
        try:
            obj = _build_catalog_object(obj_raw, source=catalog_path)
        except (ValueError, KeyError, TypeError, LoaderError) as e:
            raise LoaderError(f"{path}: aircraft[{i}] ({ref}): {e}") from e
        if not isinstance(obj, Aircraft):
            raise LoaderError(
                f"{path}: aircraft[{i}] ({ref}) is a {type(obj).__name__}, not an Aircraft; "
                f"list non-aircraft objects under 'ground_objects:' instead"
            )
        if obj.id in fleet:
            raise LoaderError(f"{path}: duplicate aircraft id {obj.id!r}")
        fleet[obj.id] = obj
```

(Rename the local `aircraft` variable to `obj` in that block.)

Add `load_ground_objects` next to `load_fleet`. It mirrors `load_fleet`'s ref-resolution loop but reads the `ground_objects:` list and asserts each is a `GroundObject`:

```python
def load_ground_objects(path: Path | str) -> dict[str, GroundObject]:
    """Load the ``ground_objects:`` list of a fleet manifest into a dict keyed
    by :attr:`GroundObject.id` (#601). Returns ``{}`` when the key is absent, so
    existing manifests (aircraft-only) are byte-identical. Refs resolve relative
    to the manifest dir, exactly like ``aircraft:`` refs."""
    path = Path(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise LoaderError(f"{path}: top-level mapping required")
    obj_list = raw.get("ground_objects")
    if obj_list is None:
        return {}
    if not isinstance(obj_list, list):
        raise LoaderError(f"{path}: 'ground_objects' must be a list")
    manifest_dir = path.parent
    out: dict[str, GroundObject] = {}
    for i, entry in enumerate(obj_list):
        ref, overrides = _parse_manifest_entry(entry, index=i, path=path)
        if overrides:
            raise LoaderError(
                f"{path}: ground_objects[{i}] overrides {sorted(overrides)} not supported"
            )
        try:
            catalog_path = (manifest_dir / ref).resolve()
            ref_exists = catalog_path.is_file()
        except (ValueError, OSError) as e:
            raise LoaderError(
                f"{path}: ground_objects[{i}] has an invalid catalog reference {ref!r}: {e}"
            ) from e
        if not ref_exists:
            raise LoaderError(
                f"{path}: ground_objects[{i}] references {ref!r} which does not exist "
                f"(resolved to {catalog_path})"
            )
        try:
            obj = _build_catalog_object(_read_yaml(catalog_path), source=catalog_path)
        except (ValueError, KeyError, TypeError, LoaderError) as e:
            raise LoaderError(f"{path}: ground_objects[{i}] ({ref}): {e}") from e
        if not isinstance(obj, GroundObject):
            raise LoaderError(
                f"{path}: ground_objects[{i}] ({ref}) is a {type(obj).__name__}, not a "
                f"GroundObject; list aircraft under 'aircraft:' instead"
            )
        if obj.id in out:
            raise LoaderError(f"{path}: duplicate ground_object id {obj.id!r}")
        out[obj.id] = obj
    return out
```

> Note: `_parse_manifest_entry`'s error messages say "aircraft[index]". That is acceptable for #601 (movers don't use overrides). If a reviewer objects, generalise `_parse_manifest_entry` to take a `block` label — but YAGNI for now; ground objects pass bare path strings.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_loader_catalog.py -k "load_ground_objects or ground_object_under_aircraft" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_catalog.py tests/fixtures/catalog/fixture_ground_manifest.yaml
git commit -m "feat(601): manifest ground_objects: + load_ground_objects + aircraft/ground guard

Refs #601"
```

---

## Task 6: Layout YAML `ground_objects:` parsing

**Files:**
- Modify: `src/hangarfit/loader.py` (`_ALLOWED_LAYOUT_KEYS` 420; `load_layout` 423-543)
- Test: `tests/test_loader.py` (extend) + `tests/fixtures/` layout

- [ ] **Step 1: Write the failing test**

Build a self-contained layout fixture in `tmp_path` (so the test owns its fleet/hangar refs), or add a checked-in fixture. The layout's `fleet:` points at the fixture manifest from Task 5 (which has both `aircraft:` and `ground_objects:`), but that manifest has `aircraft: []`. For a layout test we need ≥1 aircraft too; build a tiny manifest inline. Append to `tests/test_loader.py`:

```python
def test_layout_loads_ground_objects(tmp_path) -> None:
    # minimal manifest with one aircraft + one fixed obstacle (copy fixtures in)
    cat = tmp_path / "catalog"
    cat.mkdir()
    # ... copy a known-good aircraft catalog file + fixture_fuel_trailer.yaml into cat ...
    (tmp_path / "fleet.yaml").write_text(
        "aircraft:\n  - catalog/<aircraft>.yaml\n"
        "ground_objects:\n  - catalog/fixture_fuel_trailer.yaml\n"
    )
    # ... write hangar.yaml (reuse an existing fixture hangar) ...
    (tmp_path / "layout.yaml").write_text(
        "fleet: fleet.yaml\n"
        "hangar: hangar.yaml\n"
        "placements:\n"
        "  - plane: <aircraft_id>\n    x_m: 5.0\n    y_m: 5.0\n    heading_deg: 0.0\n    on_carts: false\n"
        "ground_objects:\n"
        "  - object: fixture_fuel_trailer\n    x_m: 2.0\n    y_m: 2.0\n    heading_deg: 0.0\n"
    )
    layout = load_layout(tmp_path / "layout.yaml")
    assert "fixture_fuel_trailer" in layout.ground_objects
    assert layout.ground_object_placements[0].plane_id == "fixture_fuel_trailer"


def test_layout_unknown_ground_object_id(tmp_path) -> None:
    # ... same setup, but the layout references object: ghost ...
    with pytest.raises(LoaderError, match="ghost"):
        load_layout(tmp_path / "layout.yaml")


def test_layout_ground_object_unknown_entry_key(tmp_path) -> None:
    # ... a ground_objects entry with a bogus key like 'rotation' ...
    with pytest.raises(LoaderError, match="unknown.*ground_object"):
        load_layout(tmp_path / "layout.yaml")
```

> Implementation tip for the test author: grep `tests/` for an existing helper that scaffolds a tmp fleet/hangar/layout (several loader tests do this) and reuse it; copy a real `data/catalog/<id>.yaml` aircraft file as the fixture aircraft.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_loader.py -k "layout_loads_ground or unknown_ground" -v`
Expected: FAIL — `load_layout` rejects the unknown top-level key `ground_objects` (`_reject_unknown_top_level_keys`).

- [ ] **Step 3: Extend the allowlist + parse the block**

In `loader.py`, extend `_ALLOWED_LAYOUT_KEYS` (line 420):

```python
_ALLOWED_LAYOUT_KEYS = frozenset({"fleet", "hangar", "placements", "maintenance", "ground_objects"})
```

Add a `ground_objects=` injection param to `load_layout`'s signature (after `apron_depth`):

```python
    ground_objects: dict[str, GroundObject] | None = None,
```

In `load_layout`, after the fleet is resolved (after line 462) resolve the ground-object defs from the *same* manifest, mirroring the fleet pattern:

```python
    if ground_objects is None:
        fleet_ref = raw.get("fleet")
        # When the layout supplies a fleet ref, the same manifest carries the
        # ground_objects list; resolve it. When fleet is injected (no ref),
        # ground objects default to empty unless injected.
        if fleet_ref is not None:
            ground_objects = load_ground_objects((path.parent / fleet_ref).resolve())
        else:
            ground_objects = {}
```

Then parse the layout's `ground_objects:` block (after the `placements` parse, ~line 508), building a `Placement` per entry:

```python
    go_data = raw.get("ground_objects", [])
    if not isinstance(go_data, list):
        raise LoaderError(f"{path}: 'ground_objects' must be a list")
    _allowed_go_entry = frozenset({"object", "x_m", "y_m", "heading_deg"})
    ground_object_placements_list: list[Placement] = []
    for i, e in enumerate(go_data):
        if not isinstance(e, dict):
            raise LoaderError(f"{path}: ground_objects[{i}] must be a mapping")
        unknown = set(e) - _allowed_go_entry
        if unknown:
            raise LoaderError(
                f"{path}: ground_objects[{i}] has unknown key(s) {sorted(unknown)}; "
                f"allowed: {sorted(_allowed_go_entry)}"
            )
        for key in ("object", "x_m", "y_m", "heading_deg"):
            if key not in e:
                raise LoaderError(f"{path}: missing required field 'ground_objects[{i}].{key}'")
        obj_id = e["object"]
        if obj_id not in ground_objects:
            raise LoaderError(
                f"{path}: ground_objects[{i}] references unknown object {obj_id!r}; "
                f"the manifest defines: {sorted(ground_objects)}"
            )
        ground_object_placements_list.append(
            Placement(
                plane_id=obj_id,
                x_m=_to_float(e["x_m"], f"ground_objects[{i}].x_m"),
                y_m=_to_float(e["y_m"], f"ground_objects[{i}].y_m"),
                heading_deg=_to_float(e["heading_deg"], f"ground_objects[{i}].heading_deg"),
                on_carts=False,
            )
        )
    ground_object_placements = tuple(ground_object_placements_list)
```

Finally, pass both to the `Layout(...)` constructor (line 536):

```python
        return Layout(
            fleet=fleet,
            hangar=hangar,
            placements=placements,
            maintenance_plane=maintenance_plane,
            ground_objects=ground_objects,
            ground_object_placements=ground_object_placements,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_loader.py -k "layout_loads_ground or unknown_ground" -v`
Expected: PASS.

- [ ] **Step 5: Add the allowlist round-trip test**

The repo has `test_all_allowed_layout_keys_load` (tests/test_loader.py) asserting `set(yaml_keys) == _ALLOWED_LAYOUT_KEYS`. Update its fixture to include a `ground_objects:` block so the assertion still equals the (now larger) allowlist. Run it:

Run: `pytest tests/test_loader.py -k all_allowed_layout_keys -v`
Expected: PASS.

- [ ] **Step 6: Byte-identity check (no ground objects)**

Run: `pytest tests/test_loader.py -v`
Expected: PASS — existing layout fixtures (no `ground_objects:`) load unchanged (`ground_objects` defaults to the manifest's empty set or `{}`; `ground_object_placements` is `()`).

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader.py tests/fixtures/
git commit -m "feat(601): layout YAML ground_objects: block parsing

Refs #601"
```

---

## Task 7: Scenario YAML `ground_objects:` id-list parsing

**Files:**
- Modify: `src/hangarfit/loader.py` (`_ALLOWED_SCENARIO_KEYS` 550; `load_scenario` 553-703)
- Test: `tests/test_loader.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
def test_scenario_loads_ground_objects(tmp_path) -> None:
    # manifest with one aircraft + one mover; scenario lists the mover id
    # ... scaffold fleet.yaml (aircraft + ground_objects), hangar.yaml ...
    (tmp_path / "scn.yaml").write_text(
        "fleet: fleet.yaml\nhangar: hangar.yaml\n"
        "fleet_in:\n  - <aircraft_id>\n"
        "ground_objects:\n  - fixture_caddy\n"
    )
    scn = load_scenario(tmp_path / "scn.yaml")
    assert scn.ground_objects == ("fixture_caddy",)
    assert "fixture_caddy" in scn.ground_object_defs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_loader.py -k scenario_loads_ground -v`
Expected: FAIL — unknown top-level key `ground_objects`.

- [ ] **Step 3: Extend allowlist + parse**

Extend `_ALLOWED_SCENARIO_KEYS` (line 550):

```python
_ALLOWED_SCENARIO_KEYS = frozenset(
    {"fleet_in", "fleet", "hangar", "maintenance", "constraints", "ground_objects"}
)
```

Add a `ground_objects=` injection param to `load_scenario` (after `apron_depth`):

```python
    ground_objects: dict[str, GroundObject] | None = None,
```

After the fleet is resolved (after line 608), resolve ground-object defs from the same manifest:

```python
    if ground_objects is None:
        fleet_ref = raw.get("fleet")
        ground_objects = (
            load_ground_objects((path.parent / fleet_ref).resolve())
            if fleet_ref is not None else {}
        )
```

Parse the id-list (after `fleet_in` is built, ~line 594, but resolve against defs after they exist — put it just before the `Scenario(...)` build):

```python
    go_ids_raw = raw.get("ground_objects", [])
    if not isinstance(go_ids_raw, list):
        raise LoaderError(f"{path}: 'ground_objects' must be a list of ids")
    scenario_ground_objects = tuple(str(x) for x in go_ids_raw)
    for gid in scenario_ground_objects:
        if gid not in ground_objects:
            raise LoaderError(
                f"{path}: ground_objects references unknown object {gid!r}; "
                f"the manifest defines: {sorted(ground_objects)}"
            )
```

Pass to `Scenario(...)` (line 695):

```python
        return Scenario(
            fleet=fleet,
            hangar=hangar,
            fleet_in=fleet_in,
            maintenance_plane=maintenance_plane,
            constraints=constraints,
            ground_objects=scenario_ground_objects,
            ground_object_defs=ground_objects,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_loader.py -k scenario_loads_ground -v`
Expected: PASS.

- [ ] **Step 5: Update the scenario allowlist round-trip test + byte-identity**

Update `test_all_allowed_scenario_keys_load` to include a `ground_objects:` key. Run:

Run: `pytest tests/test_loader.py -k "all_allowed_scenario or scenario" -v && pytest tests/test_solver.py -v`
Expected: PASS — solver scenarios (no ground objects) load unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader.py
git commit -m "feat(601): scenario YAML ground_objects: id-list parsing

Refs #601"
```

---

## Task 8: Geometry — widen `aircraft_parts_world` to ground objects

**Files:**
- Modify: `src/hangarfit/geometry.py` (`aircraft_parts_world` 196-259; import 32)
- Test: `tests/test_geometry.py` (extend)

- [ ] **Step 1: Write the failing test**

`aircraft_parts_world` is guarded by `geometry-invariant-guard`; the transform is unchanged, only the accepted type widens. The test asserts a ground object's footprint transforms identically to an equivalent aircraft part. Add to `tests/test_geometry.py`:

```python
from hangarfit.geometry import aircraft_parts_world
from hangarfit.models import GroundObject, Part, Placement


def test_ground_object_parts_world_uses_same_transform() -> None:
    part = Part(
        kind="ground", length_m=4.0, width_m=2.0, offset_x_m=0.0, offset_y_m=0.0,
        angle_deg=0.0, z_bottom_m=0.0, z_top_m=1.5,
    )
    obj = GroundObject(id="trolley", name="t", parts=(part,), object_class="fixed_obstacle")
    pl = Placement(plane_id="trolley", x_m=7.0, y_m=3.0, heading_deg=37.0, on_carts=False)
    wps = aircraft_parts_world(obj, pl)
    assert len(wps) == 1
    assert wps[0].plane_id == "trolley"
    assert wps[0].kind == "ground"
    # det-(-1) transform: a non-axis-aligned heading must produce a rotated box
    # (the geometry-invariant-guard requirement). Centroid maps via local_to_world.
    cx, cy = wps[0].polygon.centroid.coords[0]
    assert cx == pytest.approx(7.0)
    assert cy == pytest.approx(3.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_geometry.py -k ground_object_parts_world -v`
Expected: FAIL — `mypy`/runtime type mismatch is the *intent*, but at runtime duck-typing may already pass. If it PASSES at runtime, still proceed to Step 3 to fix the **type annotation** (mypy would otherwise reject ground-object callers in Tasks 9-10).

- [ ] **Step 3: Widen the signature**

In `geometry.py`, change the import (line 32) to add `GroundObject`:

```python
from .models import Aircraft, GroundObject, Part, PartKind, Placement
```

Change `aircraft_parts_world`'s signature (line 196-199) and the loop variable:

```python
def aircraft_parts_world(
    obj: Aircraft | GroundObject,
    placement: Placement,
) -> list[WorldPart]:
```

In the body, change `for part in aircraft.parts:` (line 225) to `for part in obj.parts:`. Update the docstring's first line to: "Transform every part of an aircraft **or ground object** from plane-local to world coords." The transform math is untouched (ADR-0002 invariant preserved).

> `cached_parts_world` (the hot per-solve memoized wrapper) is **not** changed — ground objects are few and not in the solver hot loop, so collisions/towplanner call the un-cached `aircraft_parts_world` for them directly (Tasks 9-10).

- [ ] **Step 4: Run test + the guard's own tests**

Run: `pytest tests/test_geometry.py -v && mypy src/hangarfit/geometry.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/geometry.py tests/test_geometry.py
git commit -m "feat(601): widen aircraft_parts_world to accept GroundObject

Refs #601"
```

---

## Task 9: Collisions — fixed-obstacle keep-out + movers in pairwise

**Files:**
- Modify: `src/hangarfit/collisions.py` (`check` 61-74; new `_ground_obstacle_conflicts` after 75; import 51-58)
- Test: `tests/test_collisions_ground_object.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Ground-object collision wiring (#601)."""

import pytest

from hangarfit.collisions import check
from hangarfit.models import GroundObject, Layout, Part, Placement
# reuse conftest fixtures: minimal_hangar, an aircraft factory


def _ground_part(length_m=4.0, width_m=2.0, z_top_m=1.5) -> Part:
    return Part(kind="ground", length_m=length_m, width_m=width_m, offset_x_m=0.0,
                offset_y_m=0.0, angle_deg=0.0, z_bottom_m=0.0, z_top_m=z_top_m)


def test_aircraft_over_fixed_obstacle_conflicts(minimal_hangar, low_wing_aircraft) -> None:
    # Place a fixed obstacle directly under an aircraft part → ground_obstacle conflict.
    obj = GroundObject(id="obstacle", name="o", parts=(_ground_part(z_top_m=3.0),),
                       object_class="fixed_obstacle")
    ac = low_wing_aircraft  # whatever the conftest provides; place it ON the obstacle
    layout = Layout(
        fleet={ac.id: ac}, hangar=minimal_hangar,
        placements=(Placement(plane_id=ac.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),),
        ground_objects={obj.id: obj},
        ground_object_placements=(Placement(plane_id=obj.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),),
    )
    result = check(layout)
    kinds = {c.kind for c in result.conflicts}
    assert "ground_obstacle" in kinds
    assert any("obstacle" in "".join(c.planes) or "obstacle" in c.detail for c in result.conflicts)


def test_mover_overlapping_aircraft_conflicts(minimal_hangar, low_wing_aircraft) -> None:
    mover = GroundObject(id="caddy", name="c", parts=(_ground_part(z_top_m=3.0),),
                         object_class="placed_routed_mover", motion_mode="steerable", turn_radius_m=4.0)
    ac = low_wing_aircraft
    layout = Layout(
        fleet={ac.id: ac}, hangar=minimal_hangar,
        placements=(Placement(plane_id=ac.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),),
        ground_objects={mover.id: mover},
        ground_object_placements=(Placement(plane_id=mover.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),),
    )
    result = check(layout)
    # mover participates in pairwise → a *_overlap conflict naming the ground part
    assert any(c.kind.endswith("_overlap") and "ground" in c.kind for c in result.conflicts)


def test_separated_ground_object_is_valid(minimal_hangar, low_wing_aircraft) -> None:
    obj = GroundObject(id="obstacle", name="o", parts=(_ground_part(),), object_class="fixed_obstacle")
    ac = low_wing_aircraft
    layout = Layout(
        fleet={ac.id: ac}, hangar=minimal_hangar,
        placements=(Placement(plane_id=ac.id, x_m=6.0, y_m=6.0, heading_deg=0.0, on_carts=False),),
        ground_objects={obj.id: obj},
        # far corner, no overlap
        ground_object_placements=(Placement(plane_id=obj.id, x_m=1.0, y_m=1.0, heading_deg=0.0, on_carts=False),),
    )
    assert check(layout).valid
```

Also add a **byte-identity** test: load an existing valid fixture layout (no ground objects) and assert its `CheckResult` is unchanged:

```python
def test_empty_ground_objects_byte_identical(existing_valid_layout) -> None:
    # existing_valid_layout: an already-checked fixture Layout with no ground objects.
    r = check(existing_valid_layout)
    assert r.valid  # and conflicts/total_penetration_m2 unchanged vs pre-#601
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_collisions_ground_object.py -v`
Expected: FAIL — `check` ignores ground objects (no `ground_obstacle` conflict).

- [ ] **Step 3: Implement the wiring in `check` + the new function**

In `collisions.py`, add `aircraft_parts_world` and `GroundObject`/`WorldPart` to imports:

```python
from .geometry import (
    WorldPart,
    aircraft_parts_world,
    axis_aligned_rect,
    cached_parts_world,
    polygon_overlap,
    polygon_overlap_area,
)
from .models import CheckResult, Conflict, GroundObject, Hangar, Layout
```

Rewrite `check` (lines 61-74):

```python
def check(layout: Layout) -> CheckResult:
    """Run all geometric checks and return a :class:`CheckResult`."""
    aircraft_parts: dict[str, list[WorldPart]] = {
        p.plane_id: cached_parts_world(layout.fleet[p.plane_id], p) for p in layout.placements
    }
    # Ground objects (#601): movers are placed bodies (pairwise); fixed obstacles
    # are keep-outs. Both reuse the aircraft world-transform (det-(-1), ADR-0002).
    mover_parts: dict[str, list[WorldPart]] = {}
    obstacle_parts: dict[str, list[WorldPart]] = {}
    for gp in layout.ground_object_placements:
        obj = layout.ground_objects[gp.plane_id]
        wparts = aircraft_parts_world(obj, gp)
        if obj.object_class == "fixed_obstacle":
            obstacle_parts[gp.plane_id] = wparts
        else:
            mover_parts[gp.plane_id] = wparts

    # Movers join the placed-body set for bounds-free pairwise collision; aircraft
    # come first so the no-ground-object case is byte-identical (same dict order).
    placed_bodies = {**aircraft_parts, **mover_parts}

    conflicts: list[Conflict] = []
    conflicts.extend(_hangar_bounds_conflicts(aircraft_parts, layout.hangar))
    conflicts.extend(_bay_intrusion_conflicts(aircraft_parts, layout))
    pairwise, total_penetration_m2 = _pairwise_conflicts(placed_bodies, layout.hangar)
    conflicts.extend(pairwise)
    conflicts.extend(_ground_obstacle_conflicts(placed_bodies, obstacle_parts, layout.hangar))
    return CheckResult(
        conflicts=tuple(conflicts),
        total_penetration_m2=total_penetration_m2,
    )
```

> Byte-identity rationale (state in the code comment): with no ground objects, `mover_parts`/`obstacle_parts` are empty, `placed_bodies == aircraft_parts`, `_ground_obstacle_conflicts` returns `[]`, and the conflict order/`total_penetration_m2` are identical to pre-#601. `_hangar_bounds_conflicts`/`_bay_intrusion_conflicts` keep their aircraft-only input (ground-object bounds checking is out of #601 scope — #604/#605).

Add `_ground_obstacle_conflicts` right after `check` (after line 74). It reuses `_parts_conflict` (the z-gap predicate) so its boundary semantics match the oracle exactly:

```python
def _ground_obstacle_conflicts(
    placed_bodies: dict[str, list[WorldPart]],
    obstacle_parts: dict[str, list[WorldPart]],
    hangar: Hangar,
) -> list[Conflict]:
    """A fixed obstacle's footprint is a keep-out: any aircraft/mover part that
    conflicts with it (plan-view overlap within clearance AND z-gap rule, via
    :func:`_parts_conflict`) is a single-object ``ground_obstacle`` conflict
    naming the offending body and the obstacle (#601 / ADR-0025).

    Deterministic iteration: obstacles in dict-insertion order (manifest order),
    bodies in placement order. Empty ``obstacle_parts`` → ``[]`` (byte-identity)."""
    out: list[Conflict] = []
    for obstacle_id, obs_wparts in obstacle_parts.items():
        for body_id, body_wparts in placed_bodies.items():
            for op in obs_wparts:
                for bp in body_wparts:
                    if _parts_conflict(op, bp, hangar):
                        out.append(
                            Conflict.single(
                                kind="ground_obstacle",
                                plane=body_id,
                                detail=(
                                    f"part {bp.kind!r} of {body_id!r} overlaps fixed "
                                    f"obstacle {obstacle_id!r} part {op.kind!r}"
                                ),
                            )
                        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_collisions_ground_object.py -v`
Expected: PASS.

- [ ] **Step 5: Full collisions regression (byte-identity)**

Run: `pytest tests/test_collisions.py tests/test_collisions_ground_object.py -v && mypy src/hangarfit/collisions.py`
Expected: PASS — every existing collision test unchanged (empty ground objects → byte-identical).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/collisions.py tests/test_collisions_ground_object.py
git commit -m "feat(601): fixed-obstacle keep-out + movers in pairwise collision

Refs #601"
```

---

## Task 10: Tow-planner — fixed obstacles as static; movers in routable enumeration

**Files:**
- Modify: `src/hangarfit/towplanner.py` (`_build_obstacles` 1720-1751; `plan_fill` 1279-1445)
- Test: `tests/test_towplanner_ground_object.py` (new)

> This file is guarded by `determinism-guard`. The empty-ground-object case MUST stay byte-identical. Read `_build_obstacles` and `plan_fill` in full before editing — the snippets below are anchored insertions, not whole-function rewrites.

- [ ] **Step 1: Write the failing test**

```python
"""Tow-planner ground-object wiring (#601)."""

import pytest

from hangarfit.towplanner import plan_fill, _build_obstacles
from hangarfit.models import GroundObject, Layout, Part, Placement


def _ground_part(length_m=3.0, width_m=13.0, z_top_m=3.0) -> Part:
    return Part(kind="ground", length_m=length_m, width_m=width_m, offset_x_m=0.0,
                offset_y_m=0.0, angle_deg=0.0, z_bottom_m=0.0, z_top_m=z_top_m)


def test_fixed_obstacle_in_static_obstacle_set(minimal_hangar, tiny_aircraft) -> None:
    obj = GroundObject(id="doorblock", name="d", parts=(_ground_part(),),
                       object_class="fixed_obstacle")
    layout = Layout(
        fleet={tiny_aircraft.id: tiny_aircraft}, hangar=minimal_hangar,
        placements=(Placement(plane_id=tiny_aircraft.id, x_m=6.0, y_m=8.0, heading_deg=0.0, on_carts=False),),
        ground_objects={obj.id: obj},
        # a wide obstacle just inside the door throat (small y)
        ground_object_placements=(Placement(plane_id=obj.id, x_m=7.0, y_m=1.0, heading_deg=0.0, on_carts=False),),
    )
    obstacles = _build_obstacles(layout, mover_id=tiny_aircraft.id)
    # the obstacle's footprint is present among the planner's static world parts
    assert any(wp.plane_id == "doorblock" for wp in obstacles.world_parts)


def test_mover_in_routable_enumeration(minimal_hangar, tiny_aircraft) -> None:
    mover = GroundObject(id="caddy", name="c", parts=(_ground_part(width_m=2.0),),
                         object_class="placed_routed_mover", motion_mode="steerable", turn_radius_m=4.0)
    layout = Layout(
        fleet={tiny_aircraft.id: tiny_aircraft}, hangar=minimal_hangar,
        placements=(Placement(plane_id=tiny_aircraft.id, x_m=6.0, y_m=8.0, heading_deg=0.0, on_carts=False),),
        ground_objects={mover.id: mover},
        ground_object_placements=(Placement(plane_id=mover.id, x_m=3.0, y_m=8.0, heading_deg=0.0, on_carts=False),),
    )
    plan = plan_fill(layout)
    # the mover id appears in the plan's routed enumeration (path deferred/None to #602)
    routed_ids = {m.plane_id for m in plan.moves}
    assert "caddy" in routed_ids
```

> Adjust `plan.moves`/`m.plane_id` to the real `MovesPlan` shape — grep `towplanner.py` for the `MovesPlan` dataclass and the existing best-effort `plans[i] = None` pattern before finalising the assertion. The intent: the mover is enumerated as something the planner must route, even though its path is `None` in #601.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_towplanner_ground_object.py -v`
Expected: FAIL — `_build_obstacles` ignores ground objects; `plan_fill` does not enumerate the mover.

- [ ] **Step 3: Add fixed obstacles + placed movers to `_build_obstacles`**

In `_build_obstacles` (line 1720-1751), after the placed-plane world-parts collection and the `notch_boxes` construction, append ground-object world parts (fixed obstacles always; movers except the one being routed). Read the function to find where `world_parts` / `world_part_aabbs` are assembled and mirror the exact AABB pairing (the `_Obstacles.__post_init__` enforces parallel-array length):

```python
    # Ground objects (#601): fixed obstacles are always static keep-outs; placed
    # movers (other than the one being routed) are static placed bodies. Sorted by
    # id so the world_parts tuple order is deterministic (determinism-guard).
    from .geometry import aircraft_parts_world  # local import if not already at top
    for gp in sorted(placed.ground_object_placements, key=lambda p: p.plane_id):
        if gp.plane_id == mover_id:
            continue
        obj = placed.ground_objects[gp.plane_id]
        for wp in aircraft_parts_world(obj, gp):
            extra_world_parts.append(wp)
            extra_world_part_aabbs.append(_aabb_of(wp))  # use the same AABB helper this fn uses
```

> Implementation detail: match the names the function actually uses for its world-part list and AABB list, and the AABB helper it calls (grep `_build_obstacles` for how it builds `world_part_aabbs`). Append to those before the `_Obstacles(...)` construction so the parallel-array invariant holds. If `aircraft_parts_world` is already imported at module top, drop the local import.

- [ ] **Step 4: Enumerate movers in `plan_fill` (deferred route)**

In `plan_fill` (line 1279-1445), after the aircraft routable order is built from `back_first_order(target.placements)`, append the movers as deferred routables. Read the loop that produces `moves`/`plans` and follow the existing best-effort `None`-path pattern: for each `gp` in `target.ground_object_placements` whose object is a `placed_routed_mover`, emit a `Move` with a `None`/deferred path (it is enumerated but not searched — #602 implements the search):

```python
    # Ground-object movers (#601): enumerate each as a body the planner must route,
    # but DEFER the actual path search to #602 — emit a deferred (None-path) move.
    # Fixed obstacles are never routed (they are keep-outs, handled in _build_obstacles).
    for gp in target.ground_object_placements:
        obj = target.ground_objects[gp.plane_id]
        if obj.object_class != "placed_routed_mover":
            continue
        moves.append(_deferred_mover_move(gp))  # build a Move with path=None, plane_id=gp.plane_id
```

> Define `_deferred_mover_move` (or inline) to match the `Move` dataclass fields — grep `towplanner.py` for `class Move` and the existing place a `None` path is constructed. The deferred move must carry `plane_id = gp.plane_id`. Keep it OUT of the aircraft routing loop so aircraft plans are byte-identical.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_towplanner_ground_object.py -v`
Expected: PASS.

- [ ] **Step 6: Determinism + byte-identity regression**

Run: `pytest tests/test_towplanner.py tests/test_solver_parallel.py -v`
Expected: PASS — no ground objects in those fixtures → byte-identical plans. Then run the determinism double-solve canary if present:

Run: `pytest -k determinism -v`
Expected: PASS (the guard's fixtures carry no ground objects, so the plan is bit-identical).

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/towplanner.py tests/test_towplanner_ground_object.py
git commit -m "feat(601): fixed obstacles as static keep-outs; movers in routable enumeration

Refs #601"
```

---

## Task 11: `"ground"` PartKind ripple audit

**Files:**
- Modify (if needed): `src/hangarfit/metrics.py`, `src/hangarfit/visualize.py`, `src/hangarfit/scene.py`
- Test: existing suites + `mypy`

- [ ] **Step 1: Find every PartKind-exhaustive consumer**

Run: `grep -rn "PartKind\|_OVERHANGABLE\|part.kind\|\.kind ==" src/hangarfit/metrics.py src/hangarfit/visualize.py src/hangarfit/scene.py`
Expected: a short list. Classify each: does it `match`/dict-lookup on kind without a default (would break on `"ground"`), or is it a membership test (safe — `"ground"` simply isn't a wing/tail/overhangable)?

- [ ] **Step 2: Run mypy + full suite to surface fallout**

Run: `mypy src/hangarfit/ && pytest`
Expected: PASS. The most likely findings and their correct handling:
- `metrics._OVERHANGABLE` (a frozenset): `"ground"` is correctly absent → a ground part is never overhangable. **No change.**
- `visualize.py` / `scene.py` color/material lookup keyed by kind: only reachable when a layout *contains* a ground part. No existing fixture does, and #601 ships no rendering (deferred to #606). If a `dict[...]` lookup would `KeyError` or a `match` is non-exhaustive for mypy, add a minimal default branch (e.g. a neutral grey for an unknown kind) — do **not** build the full ground-object rendering here (that is #606).

- [ ] **Step 3: If a default was needed, add a focused test**

If you added a default render branch, add one test that a ground `Part` renders without raising (a smoke test), in the relevant test module.

- [ ] **Step 4: Commit (only if changes were needed)**

```bash
git add src/hangarfit/metrics.py src/hangarfit/visualize.py src/hangarfit/scene.py tests/
git commit -m "fix(601): handle 'ground' PartKind in metrics/visualize/scene defaults

Refs #601"
```

If no changes were needed, record that in the task notes and skip the commit.

---

## Task 12: Docs — ADR-0025, arc42 §5/§8, catalog README, CHANGELOG

**Files:**
- Create: `docs/adr/0025-ground-object-taxonomy.md`
- Modify: `docs/architecture/05-building-block-view.md`, `docs/architecture/08-crosscutting-concepts.md`, `data/catalog/README.md`, `CHANGELOG.md`, `docs/adr/README.md` (the ADR index)

- [ ] **Step 1: Write ADR-0025**

Create `docs/adr/0025-ground-object-taxonomy.md` following the format of an existing ADR (e.g. `0023-empennage-tail-surfaces.md` — read it for the header/status/context/decision/consequences shape). Content: the concrete-type taxonomy (D1), Layout-uniform-via-Placement (D2), the pairwise-set keep-out seam (D3) + the `"ground"` PartKind, the #601 scope line, and a forward note that the ADR-0010 *motion* amendment lands in #602. Mark **Status: Accepted**.

- [ ] **Step 2: Update the ADR index**

Add the ADR-0025 line to `docs/adr/README.md` (match the existing table/list format).

- [ ] **Step 3: arc42 §5 — building-block view**

In `docs/architecture/05-building-block-view.md`, add a ground-object responsibility line to the `models`, `loader`, `collisions`, and `towplanner` entries (one clause each): `models` owns `GroundObject`; `loader` resolves catalog `fixed_obstacle`/`car`/`trailer` types + the `ground_objects:` blocks; `collisions` treats fixed obstacles as keep-outs and movers as pairwise bodies; `towplanner` routes around fixed obstacles and enumerates movers.

- [ ] **Step 4: arc42 §8 — crosscutting**

In `docs/architecture/08-crosscutting-concepts.md`, under "The parts model", add a short "Ground objects (#601)" subsection: the two object classes, that footprints reuse `Part` with `kind="ground"`, the keep-out-vs-pairwise distinction, and the concrete catalog `type:` vocabulary. Cross-link ADR-0025.

- [ ] **Step 5: Catalog README**

In `data/catalog/README.md`, add a "Ground objects (#601)" section documenting the `type: fixed_obstacle | car | trailer` values, the `kind: ground` footprint convention, `motion_mode` defaults (car→steerable, trailer→towed) and override, and that real Herrenteich entries land in #605.

- [ ] **Step 6: CHANGELOG**

In `CHANGELOG.md` under `[Unreleased]` → `Added`, add: "Ground-object data model (#601): catalog `fixed_obstacle`/`car`/`trailer` types and a layout `ground_objects:` block; fixed obstacles are keep-outs and movers join collision/tow enumeration. Empty-set output is byte-identical."

- [ ] **Step 7: Verify links + commit**

Run: `grep -rn "0025" docs/adr/README.md docs/architecture/` (confirm the cross-references resolve).

```bash
git add docs/adr/0025-ground-object-taxonomy.md docs/adr/README.md docs/architecture/05-building-block-view.md docs/architecture/08-crosscutting-concepts.md data/catalog/README.md CHANGELOG.md
git commit -m "docs(601): ADR-0025 + arc42 §5/§8 + catalog README + CHANGELOG

Refs #601"
```

---

## Task 13: Integration fixture + end-to-end check

**Files:**
- Create: `tests/fixtures/layouts/ground_objects_smoke.yaml` (+ supporting manifest/hangar if needed)
- Test: `tests/test_loader.py` or a new `tests/test_ground_objects_integration.py`

- [ ] **Step 1: Write the end-to-end test**

A single test that loads a real layout YAML with a fixed obstacle + a mover through `load_layout`, runs `check`, and asserts the verdict — proving the data flows model→loader→collisions end to end:

```python
def test_ground_objects_end_to_end(tmp_path) -> None:
    # Scaffold: catalog/ (one aircraft + fixture_fuel_trailer + fixture_caddy),
    # fleet.yaml (aircraft: [...] + ground_objects: [...]), hangar.yaml, layout.yaml
    # with the aircraft parked clear, the fuel trailer at the door, the caddy clear.
    layout = load_layout(tmp_path / "layout.yaml")
    assert "fixture_fuel_trailer" in layout.ground_objects
    assert "fixture_caddy" in layout.ground_objects
    result = check(layout)
    assert result.valid   # everything placed clear → valid

    # Now author an overlapping placement and assert the ground_obstacle conflict.
    # (rewrite layout.yaml with the aircraft on top of the fuel trailer)
    layout2 = load_layout(tmp_path / "layout_clash.yaml")
    assert not check(layout2).valid
    assert any(c.kind == "ground_obstacle" for c in check(layout2).conflicts)
```

- [ ] **Step 2: Run it (fails until fixtures exist)**

Run: `pytest tests/test_ground_objects_integration.py -v`
Expected: FAIL → then PASS once the fixtures are written. Keep this test **non-slow** (the two-pass coverage gotcha: ≥1 non-slow test per new path).

- [ ] **Step 3: Full suite + lint + type**

Run: `pytest && ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`
Expected: PASS across the board.

- [ ] **Step 4: Commit**

```bash
git add tests/test_ground_objects_integration.py tests/fixtures/
git commit -m "test(601): end-to-end ground-object load + check integration

Refs #601"
```

---

## Final verification (before opening the PR)

- [ ] `pytest` (full fast suite) green.
- [ ] `pytest -m slow` green (if any slow ground-object tests were added).
- [ ] `ruff check src/ tests/ && ruff format --check src/ tests/` clean.
- [ ] `mypy src/hangarfit/` clean.
- [ ] **Byte-identity spot check:** an existing valid layout + an existing scenario solve produce unchanged output (no ground objects ⇒ no behavioural change). The `determinism-guard` double-solve is bit-identical.
- [ ] CHANGELOG `[Unreleased]` carries the #601 entry.
- [ ] Open the PR **as a draft**, base `develop`, body `Closes #601`, assignee `DocGerd`, label `enhancement`+`area:backend`, milestone "Ground objects + Herrenteich calibration".
- [ ] Review arc (per CLAUDE.md): `code-reviewer` (main) + `geometry-invariant-guard` (geometry/collisions) + `silent-failure-hunter` (loader/collisions) + `type-design-analyzer` (models) + `determinism-guard` (towplanner). Convert findings to diff threads; resolve each; re-review if non-trivial; then `gh pr ready`.

---

## Spec-coverage self-check (run before handing off)

| Spec deliverable (#601 AC) | Task |
|---|---|
| `GroundObject` model + validation | 1 |
| `"ground"` footprint kind | 1 |
| Layout fields + invariants (id==key, disjoint, resolve) | 2 |
| Scenario id-list | 3 |
| Catalog builder registry + 3 types + allowlists | 4 |
| Manifest `ground_objects:` + `load_ground_objects` + aircraft/ground guards | 5 |
| Layout YAML `ground_objects:` parsing + round-trip test | 6 |
| Scenario YAML `ground_objects:` parsing + round-trip test | 7 |
| Ground objects reuse the det-(-1) transform | 8 |
| Fixed-obstacle keep-out (`ground_obstacle`) + movers in pairwise + byte-identity | 9 |
| Fixed obstacles static in tow planner; movers in routable enumeration (deferred) | 10 |
| Determinism preserved | 9, 10 |
| Docs (ADR-0025, arc42 §5/§8, README, CHANGELOG) | 12 |
| End-to-end coverage | 13 |
