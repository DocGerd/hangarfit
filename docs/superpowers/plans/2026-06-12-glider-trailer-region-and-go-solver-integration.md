# Glider-trailer region + ground-object solver integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deterministic solver place + route the glider trailers as full RR-MC citizens, and add a soft right/left-region preference scored as a layered `_spread` post-pass term (#604).

**Architecture:** Approach 1 — one unified *placeable-id* set (`fleet_in ++ sorted(mover_ids)`) with a `_body()` dispatch and a centralized `_build_layout()` that splits aircraft vs ground-object placements and injects fixed-obstacle keep-outs. The whole integration degenerates **byte-identically** to today when a scenario has no ground objects (zero new RNG draws, empty `Layout` args, region term not added). The region term is the #320 back-bias rotated 90°: `R = Σ wₒ·dₒ/W`, RNG-free, secondary to `min_pairwise_gap_m`.

**Tech Stack:** Python 3.12, frozen dataclasses (`models.py`), `random.Random` seeded RNG, shapely polygons, pytest. Determinism is contractual (ADR-0003); the `determinism-guard` subagent gates the solver diff.

**Determinism invariant (every solver task must preserve it):** with no ground objects in the scenario the change must be a no-op on the RNG stream and on `_score`. After every solver task run: `pytest tests/test_solver_canaries.py tests/test_solver_parallel.py -p no:randomly -q` must stay green **unmodified**.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/hangarfit/models.py` | `RegionPreference`, `Scenario.region_preferences` + `fixed_obstacle_placements` + mover-id helper, `SolverDiagnostics.region_alignment` | modify |
| `src/hangarfit/solver.py` | `_body`/`_body_parts_world`, `_build_layout`, unified placeable set across init/descent/energy/spread, `_region_energy`, `_SpreadCandidate.region_alignment`, diagnostics population | modify |
| `src/hangarfit/loader.py` | parse scenario `ground_objects:` into fixed-obstacle poses + mover entries + `region_preference`; allowlists | modify |
| `src/hangarfit/cli.py` | surface `region_alignment` (human + `--json`) | modify |
| `tests/fixtures/solve_region_demo.yaml` (+ `fleet_region_demo.yaml`) | tractable demo: 2-3 aircraft + 2 trailers in a roomy hangar | create |
| `examples/herrenteich/scenario.yaml` | opt Herrenteich in (2 trailers as movers + right pref + fuel-trailer keep-out) | modify |
| `tests/test_models_region.py` | `RegionPreference` + `Scenario` validation | create |
| `tests/test_loader_scenario_ground_objects.py` | scenario GO parse | create |
| `tests/test_solver_region.py` | `_region_energy` inert trio + effect + e2e + diagnostics | create |
| `tests/test_solver_ground_objects.py` | solver places/routes movers; egress-in-solve; movers-active determinism canary | create |
| `tests/test_cli.py` | `region_alignment` surfacing | modify |
| `docs/adr/0008-…md`, `docs/adr/0010-…md`, `docs/architecture/08-crosscutting-concepts.md`, `CHANGELOG.md` | amendments | modify |

**Conventions used below:** run tests with `pytest <path> -q` (the repo default excludes `@slow`; the PostToolUse hook also runs ruff + a pytest subset after `src/`/`tests/` edits). Commit messages use `type(604): …` and end with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer. **Never** `git add` `CLAUDE.md` or `.claude/settings.json` (user-local graphify edits).

---

## Task 1: `RegionPreference` model

**Files:**
- Modify: `src/hangarfit/models.py` (add near `PlaneConstraint`, ~line 1040)
- Test: `tests/test_models_region.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_region.py
import math
import pytest
from hangarfit.models import RegionPreference


def test_region_preference_valid_right():
    rp = RegionPreference(side="right", weight=1.0)
    assert rp.side == "right"
    assert rp.weight == 1.0


def test_region_preference_zero_weight_allowed():
    assert RegionPreference(side="left", weight=0.0).weight == 0.0


@pytest.mark.parametrize("bad", [-0.1, float("nan"), float("inf")])
def test_region_preference_rejects_bad_weight(bad):
    with pytest.raises(ValueError):
        RegionPreference(side="right", weight=bad)


def test_region_preference_rejects_bad_side():
    with pytest.raises(ValueError):
        RegionPreference(side="up", weight=1.0)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models_region.py -q`
Expected: FAIL — `ImportError: cannot import name 'RegionPreference'`.

- [ ] **Step 3: Implement `RegionPreference`**

Add to `src/hangarfit/models.py` immediately before `class PlaneConstraint` (the `Literal`/`math` imports already exist at top of file):

```python
RegionSide = Literal["left", "right"]
_VALID_REGION_SIDES: frozenset[str] = frozenset(("left", "right"))


@dataclass(frozen=True, slots=True)
class RegionPreference:
    """A soft per-object preference to align a placed body to one hangar wall (#604).

    ``side`` is the preferred wall in the x-axis (``"left"`` ≡ ``x → 0``,
    ``"right"`` ≡ ``x → hangar.width_m``); ``weight`` is a non-negative, finite
    soft importance (``0.0`` is permitted and inert). Realized as the RNG-free
    ``solver._region_energy`` term folded into the ``_spread`` hill-climb,
    secondary to ``min_pairwise_gap_m`` and never overriding the hard validity
    gate (ADR-0008 amended). Modeled on :attr:`PlaneConstraint.priority`'s
    soft-weight validation (#441).
    """

    side: RegionSide
    weight: float

    def __post_init__(self) -> None:
        if self.side not in _VALID_REGION_SIDES:
            raise ValueError(
                f"RegionPreference.side must be one of {sorted(_VALID_REGION_SIDES)}, "
                f"got {self.side!r}"
            )
        if not math.isfinite(self.weight):
            raise ValueError(f"RegionPreference.weight={self.weight!r} must be finite")
        if self.weight < 0.0:
            raise ValueError(
                f"RegionPreference.weight={self.weight!r} must be >= 0.0 (a soft weight)"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models_region.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/models.py tests/test_models_region.py
git commit -m "feat(604): RegionPreference soft per-object region-alignment type

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `Scenario.region_preferences` + `fixed_obstacle_placements` + mover-id helper

**Files:**
- Modify: `src/hangarfit/models.py` — `Scenario` fields (~1106-1114), `_PROXY_FIELDS` (~1121), `__post_init__` (validation ~1238-1250 region), add a `mover_ids` property
- Test: `tests/test_models_region.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_models_region.py
from types import MappingProxyType
from hangarfit.models import Scenario, GroundObject, Placement
# Reuse existing test builders for Aircraft/GroundObject/Hangar from the suite's
# conftest/fixtures; the snippet below assumes helpers `make_aircraft(id)`,
# `make_mover(id)` (object_class='placed_routed_mover'), `make_hangar()` exist or
# are constructed inline as in tests/test_models.py.

def _scn(**kw):
    fleet = {"husky": make_aircraft("husky")}
    base = dict(fleet=fleet, hangar=make_hangar(), fleet_in=("husky",))
    base.update(kw)
    return Scenario(**base)


def test_scenario_region_preferences_default_empty():
    s = _scn()
    assert dict(s.region_preferences) == {}


def test_scenario_region_preference_must_reference_placeable_id():
    with pytest.raises(ValueError):
        _scn(region_preferences={"ghost": RegionPreference("right", 1.0)})


def test_scenario_region_preference_on_aircraft_ok():
    s = _scn(region_preferences={"husky": RegionPreference("right", 1.0)})
    assert s.region_preferences["husky"].side == "right"


def test_scenario_mover_ids_from_ground_objects():
    mover = make_mover("glider_trailer_1")
    s = _scn(
        ground_objects=("glider_trailer_1",),
        ground_object_defs={"glider_trailer_1": mover},
    )
    assert s.mover_ids == ("glider_trailer_1",)


def test_scenario_region_preference_on_mover_ok():
    mover = make_mover("glider_trailer_1")
    s = _scn(
        ground_objects=("glider_trailer_1",),
        ground_object_defs={"glider_trailer_1": mover},
        region_preferences={"glider_trailer_1": RegionPreference("right", 1.0)},
    )
    assert s.region_preferences["glider_trailer_1"].weight == 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_models_region.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'region_preferences'`.

- [ ] **Step 3: Implement the `Scenario` additions**

In `src/hangarfit/models.py` `class Scenario`, after `ground_object_defs` (line ~1114) add:

```python
    fixed_obstacle_placements: tuple[Placement, ...] = ()
    region_preferences: Mapping[str, RegionPreference] = field(
        default_factory=lambda: MappingProxyType({})
    )
```

Extend `_PROXY_FIELDS` (line ~1121) to include the new mapping:

```python
    _PROXY_FIELDS: typing.ClassVar[tuple[str, ...]] = (
        "fleet",
        "constraints",
        "ground_object_defs",
        "region_preferences",
    )
```

Add a `mover_ids` cached property after `__post_init__` (the set of placed_routed_mover ids active in the scenario, in `ground_objects` order):

```python
    @property
    def mover_ids(self) -> tuple[str, ...]:
        """Placed-routed-mover ids active in this scenario, in ``ground_objects`` order.

        These are the ground objects the solver PLACES + routes (vs fixed
        obstacles, which are authored static keep-outs in
        :attr:`fixed_obstacle_placements`). Empty ⇒ the solver is aircraft-only
        and byte-identical to the pre-#604 behaviour (ADR-0003)."""
        return tuple(
            gid
            for gid in self.ground_objects
            if self.ground_object_defs[gid].object_class == "placed_routed_mover"
        )

    @property
    def placeable_ids(self) -> tuple[str, ...]:
        """Aircraft (``fleet_in``) then sorted mover ids — the unified search bodies.

        ``maintenance_plane`` is still excluded at sample time (it is treated as
        away). With no movers this is exactly ``fleet_in`` (ADR-0003)."""
        return self.fleet_in + tuple(sorted(self.mover_ids))
```

In `__post_init__`, after the ground-object validation block (after line ~1250, before the `_PROXY_FIELDS` wrap loop) add region-preference + fixed-obstacle validation:

```python
        # Region preferences (#604): every key must reference a placeable body —
        # an aircraft in fleet_in or a placed_routed_mover ground object. A pref
        # on a fixed obstacle is meaningless (it never moves) and is rejected.
        placeable = set(self.fleet_in) | {
            gid
            for gid in self.ground_objects
            if self.ground_object_defs[gid].object_class == "placed_routed_mover"
        }
        for rid in self.region_preferences:
            if rid not in placeable:
                raise ValueError(
                    f"Scenario.region_preferences references {rid!r} which is not a "
                    f"placeable body (aircraft or placed_routed_mover); "
                    f"placeable ids: {sorted(placeable)}"
                )
        # Fixed-obstacle placements must reference fixed_obstacle ground objects
        # present in ground_objects, with no duplicates.
        seen_fixed: set[str] = set()
        for p in self.fixed_obstacle_placements:
            if p.plane_id not in self.ground_object_defs:
                raise ValueError(
                    f"Scenario.fixed_obstacle_placements references unknown ground "
                    f"object {p.plane_id!r}"
                )
            if self.ground_object_defs[p.plane_id].object_class != "fixed_obstacle":
                raise ValueError(
                    f"Scenario.fixed_obstacle_placements[{p.plane_id!r}] is not a "
                    f"fixed_obstacle"
                )
            if p.plane_id in seen_fixed:
                raise ValueError(
                    f"Duplicate fixed_obstacle_placement for {p.plane_id!r}"
                )
            seen_fixed.add(p.plane_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_models_region.py -q`
Expected: PASS.

- [ ] **Step 5: Run the model + scenario regression set**

Run: `pytest tests/test_models.py tests/test_solver_canaries.py -q`
Expected: PASS (the new optional fields default empty ⇒ no behavior change).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/models.py tests/test_models_region.py
git commit -m "feat(604): Scenario.region_preferences + fixed_obstacle_placements + mover_ids

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `SolverDiagnostics.region_alignment`

**Files:**
- Modify: `src/hangarfit/models.py` — `SolverDiagnostics` field (~1420) + `__post_init__` validation (~1450)
- Test: `tests/test_models_region.py`

Representation: `tuple[tuple[tuple[str, float], ...], ...]` — outer index-aligned with `SolveResult.layouts`; inner = `(id, alignment)` pairs in sorted id order; `alignment ∈ [0, 1]` (1.0 = at the preferred wall). Hashable (frozen-dataclass-safe), JSON-friendly.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_models_region.py
from hangarfit.models import SolverDiagnostics

def _diag(**kw):
    base = dict(restarts_attempted=1, wall_time_s=0.0, best_partial=None,
                best_partial_layout=None, seed=0)
    base.update(kw)
    return SolverDiagnostics(**base)


def test_region_alignment_default_empty():
    assert _diag().region_alignment == ()


def test_region_alignment_valid():
    d = _diag(region_alignment=((("glider_trailer_1", 0.92),),))
    assert d.region_alignment[0][0] == ("glider_trailer_1", 0.92)


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan")])
def test_region_alignment_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        _diag(region_alignment(((("t", bad),),)) if False else None)  # placeholder
```

Replace the broken last test with:

```python
@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan")])
def test_region_alignment_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        _diag(region_alignment=((("t", bad),),))
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_models_region.py -q -k region_alignment`
Expected: FAIL — `TypeError: unexpected keyword argument 'region_alignment'`.

- [ ] **Step 3: Implement**

Add field to `SolverDiagnostics` after `nose_out_flips` (line ~1420):

```python
    region_alignment: tuple[tuple[tuple[str, float], ...], ...] = ()
```

Add validation in `SolverDiagnostics.__post_init__` after the `nose_out_flips` check (line ~1454):

```python
        for layout_alignments in self.region_alignment:
            for _id, a in layout_alignments:
                if math.isnan(a) or a < 0.0 or a > 1.0:
                    raise ValueError(
                        "SolverDiagnostics.region_alignment values must be in "
                        f"[0.0, 1.0], got {a!r}"
                    )
```

Document the field in the class docstring (after the `nose_out_flips` paragraph, ~line 1370):

```
``region_alignment`` is index-aligned with :attr:`SolveResult.layouts`: for each
returned layout, a tuple of ``(body_id, alignment)`` pairs (sorted by id) for the
bodies carrying a :class:`RegionPreference`, where ``alignment`` is 0–1 with 1.0
meaning the body sits exactly at its preferred wall (#604, ADR-0008 amended).
Empty when no scenario region preferences are set. Advisory / RNG-free.
```

Note: `SolveResult.__post_init__` validates index-alignment of `min_pairwise_gap_m`/`nose_out_flips` against `layouts` (models.py ~1504). Add `region_alignment` to that length check:

```python
        # in SolveResult.__post_init__, alongside the existing per-layout tuples:
        if self.diagnostics.region_alignment and len(self.diagnostics.region_alignment) != len(self.layouts):
            raise ValueError(
                "SolverDiagnostics.region_alignment length must match layouts when populated"
            )
```
(Read the existing check at `models.py:1504` first and mirror its exact style/guard.)

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_models_region.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/models.py tests/test_models_region.py
git commit -m "feat(604): SolverDiagnostics.region_alignment per-layout per-object metric

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Loader — parse scenario `ground_objects:` (fixed poses + mover entries + region_preference)

**Files:**
- Modify: `src/hangarfit/loader.py` — scenario GO parse (`load_scenario`, ~803-822) + a new allowlist
- Test: `tests/test_loader_scenario_ground_objects.py` (create) + fixtures under `tests/fixtures/`

Scenario YAML schema (new):
```yaml
ground_objects:
  - object: maul_fuel_trailer       # fixed_obstacle → REQUIRES pose, FORBIDS region_preference
    x_m: 1.2
    y_m: 2.0
    heading_deg: 90.0
  - object: glider_trailer_1        # placed_routed_mover → FORBIDS pose, allows region_preference
    region_preference: { side: right, weight: 1.0 }
  - glider_trailer_2                # bare string also accepted for a mover (no pref)
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_loader_scenario_ground_objects.py
from pathlib import Path
import pytest
from hangarfit.loader import load_scenario, LoaderError

FIX = Path(__file__).parent / "fixtures"


def test_scenario_loads_mover_with_region_preference():
    s = load_scenario(FIX / "scenario_region_demo.yaml")
    assert "glider_trailer_1" in s.mover_ids
    assert s.region_preferences["glider_trailer_1"].side == "right"


def test_scenario_loads_fixed_obstacle_pose():
    s = load_scenario(FIX / "scenario_region_demo.yaml")
    ids = {p.plane_id for p in s.fixed_obstacle_placements}
    assert "maul_fuel_trailer" in ids


def test_scenario_fixed_obstacle_requires_pose(tmp_path):
    # a fixed_obstacle entry WITHOUT x_m/y_m/heading_deg is rejected
    ...  # build a minimal scenario referencing maul_fuel_trailer with no pose
    with pytest.raises(LoaderError):
        load_scenario(tmp_path / "bad.yaml")


def test_scenario_mover_forbids_pose(tmp_path):
    # a placed_routed_mover entry WITH x_m is rejected (the solver places it)
    with pytest.raises(LoaderError):
        load_scenario(tmp_path / "bad2.yaml")
```

(Fill the two `tmp_path` builders by writing a tiny scenario YAML mirroring `scenario_region_demo.yaml` with the offending field; see Task 16 for the demo fixture content.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_loader_scenario_ground_objects.py -q`
Expected: FAIL (fixture missing / current loader ignores the new keys).

- [ ] **Step 3: Implement the loader parse**

Read `load_scenario`'s current `ground_objects` parse (`loader.py:803-822`). Replace the id-string-list parse with an entry parser that resolves each entry's `object` id against `ground_object_defs`, branches on `object_class`, and accumulates three outputs: `scenario_ground_objects` (all ids), `fixed_obstacle_placements`, `region_preferences`.

```python
_ALLOWED_SCENARIO_GO_KEYS = frozenset({"object", "x_m", "y_m", "heading_deg", "region_preference"})
_ALLOWED_REGION_PREF_KEYS = frozenset({"side", "weight"})

# inside load_scenario, replacing the id-list parse:
go_entries = raw.get("ground_objects", [])
if not isinstance(go_entries, list):
    raise LoaderError(f"{path}: 'ground_objects' must be a list")

scenario_ground_objects: list[str] = []
fixed_obstacle_placements: list[Placement] = []
region_preferences: dict[str, RegionPreference] = {}

for i, entry in enumerate(go_entries):
    if isinstance(entry, str):
        gid, fields = entry, {}
    elif isinstance(entry, dict):
        unknown = set(entry) - _ALLOWED_SCENARIO_GO_KEYS
        if unknown:
            raise LoaderError(f"{path}: ground_objects[{i}] has unknown key(s) {sorted(unknown)}")
        if "object" not in entry:
            raise LoaderError(f"{path}: ground_objects[{i}] missing required 'object'")
        gid, fields = entry["object"], entry
    else:
        raise LoaderError(f"{path}: ground_objects[{i}] must be a string or mapping")

    if gid not in ground_objects:  # ground_objects = resolved defs dict in this fn
        raise LoaderError(
            f"{path}: ground_objects[{i}] references unknown object {gid!r}; "
            f"defs have: {sorted(ground_objects)}"
        )
    if gid in scenario_ground_objects:
        raise LoaderError(f"{path}: duplicate scenario ground object {gid!r}")
    scenario_ground_objects.append(gid)
    obj_class = ground_objects[gid].object_class

    has_pose = any(k in fields for k in ("x_m", "y_m", "heading_deg"))
    if obj_class == "fixed_obstacle":
        if "region_preference" in fields:
            raise LoaderError(f"{path}: ground_objects[{i}] ({gid}): a fixed_obstacle cannot carry a region_preference")
        missing = [k for k in ("x_m", "y_m", "heading_deg") if k not in fields]
        if missing:
            raise LoaderError(f"{path}: ground_objects[{i}] ({gid}): fixed_obstacle requires {missing}")
        fixed_obstacle_placements.append(
            Placement(plane_id=gid, x_m=float(fields["x_m"]), y_m=float(fields["y_m"]),
                      heading_deg=float(fields["heading_deg"]), on_carts=False)
        )
    else:  # placed_routed_mover
        if has_pose:
            raise LoaderError(f"{path}: ground_objects[{i}] ({gid}): a placed_routed_mover must not carry a pose (the solver places it)")
        rp = fields.get("region_preference")
        if rp is not None:
            if not isinstance(rp, dict) or set(rp) - _ALLOWED_REGION_PREF_KEYS or "side" not in rp or "weight" not in rp:
                raise LoaderError(f"{path}: ground_objects[{i}] ({gid}): region_preference must be {{side, weight}}")
            try:
                region_preferences[gid] = RegionPreference(side=rp["side"], weight=float(rp["weight"]))
            except ValueError as e:
                raise LoaderError(f"{path}: ground_objects[{i}] ({gid}): {e}") from e

# pass the three through to Scenario(...)
```

Update the `Scenario(...)` construction (loader.py ~815-822) to pass `fixed_obstacle_placements=tuple(fixed_obstacle_placements)` and `region_preferences=region_preferences`. Add `RegionPreference` to the `models` import at the top of `loader.py`.

- [ ] **Step 4: Create the demo fixture** (shared with Task 16) — see Task 16 Step 1 for `tests/fixtures/scenario_region_demo.yaml` + `fleet_region_demo.yaml`. Create them now so the loader tests have data.

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_loader_scenario_ground_objects.py -q`
Expected: PASS.

- [ ] **Step 6: Run loader regression**

Run: `pytest tests/test_loader.py tests/test_loader_catalog.py -q`
Expected: PASS (existing scenarios have no `ground_objects:` block ⇒ untouched).

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_scenario_ground_objects.py tests/fixtures/scenario_region_demo.yaml tests/fixtures/fleet_region_demo.yaml
git commit -m "feat(604): scenario ground_objects parse — fixed poses + mover region_preference

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Solver `_body` / `_body_parts_world` dispatch helpers

**Files:**
- Modify: `src/hangarfit/solver.py` (add helpers near the top imports/utilities)
- Test: `tests/test_solver_ground_objects.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_solver_ground_objects.py
from hangarfit.solver import _body, _body_parts_world
from hangarfit.geometry import cached_parts_world  # current aircraft world-parts path
# build a Scenario `s` with one aircraft 'husky' and one mover 'glider_trailer_1'
# (reuse the demo fixture via load_scenario, or construct inline).


def test_body_returns_aircraft_for_fleet_id(region_scenario):
    s = region_scenario
    assert _body(s, "husky").id == "husky"


def test_body_returns_ground_object_for_mover_id(region_scenario):
    s = region_scenario
    assert _body(s, "glider_trailer_1").object_class == "placed_routed_mover"


def test_body_parts_world_aircraft_byte_identical(region_scenario):
    # aircraft path must be the SAME object/values as cached_parts_world (ADR-0003)
    s = region_scenario
    from hangarfit.models import Placement
    p = Placement("husky", 5.0, 8.0, 30.0, on_carts=False)
    assert _body_parts_world(s, "husky", p) == cached_parts_world(s.fleet["husky"], p)
```

(Provide a `region_scenario` pytest fixture in a local `conftest.py` or the test file that `load_scenario(FIX/"scenario_region_demo.yaml")`.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_ground_objects.py -q -k body`
Expected: FAIL — `ImportError: cannot import name '_body'`.

- [ ] **Step 3: Implement**

In `solver.py` (confirm the exact name of the current aircraft world-parts helper used by `_inter_plane_energy` — it is `cached_parts_world(scenario.fleet[pid], placement)`; reuse it for aircraft to preserve byte-identity):

```python
def _body(scenario: Scenario, body_id: str) -> Aircraft | GroundObject:
    """Resolve a placeable body id to its Aircraft or GroundObject definition (#604).

    Aircraft live in ``fleet``; placed_routed_mover ground objects in
    ``ground_object_defs``. Aircraft are checked first so the common (and
    pre-#604) path is unchanged."""
    plane = scenario.fleet.get(body_id)
    if plane is not None:
        return plane
    return scenario.ground_object_defs[body_id]


def _body_parts_world(scenario: Scenario, body_id: str, placement: Placement) -> list[WorldPart]:
    """World parts for any placeable body. Aircraft reuse the memoized
    ``cached_parts_world`` (BYTE-IDENTICAL to the pre-#604 path, ADR-0003); movers
    use the same world-parts transform the towplanner already applies to
    GroundObjects (#602)."""
    body = _body(scenario, body_id)
    return cached_parts_world(body, placement)
```

Note: confirm `cached_parts_world` accepts a `GroundObject` (the towplanner already calls the world-parts transform on GroundObjects per #602; if the memoized variant is aircraft-only, call the uncached `aircraft_parts_world`/`parts_world` for movers — the towplanner-mover map says movers use the *uncached* `aircraft_parts_world`. Match that exactly so geometry is identical to the planner's.) Add `GroundObject`, `WorldPart` to imports if absent.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_solver_ground_objects.py -q -k body`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_ground_objects.py
git commit -m "feat(604): _body / _body_parts_world dispatch over Aircraft|GroundObject

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Centralize `Layout` construction in `_build_layout` (byte-identical refactor)

**Files:**
- Modify: `src/hangarfit/solver.py` — add `_build_layout`; replace inline `Layout(...)` builds at ~255, ~287, ~320, ~1052, ~1381, ~1472, ~1808, ~1858, ~2011 (audit all)
- Test: `tests/test_solver_ground_objects.py`

`_build_layout` splits the unified placements dict into aircraft `placements=` and mover `ground_object_placements=`, appends `scenario.fixed_obstacle_placements`, and sets `ground_objects=` from the active defs. With an aircraft-only dict + no ground objects it produces a `Layout` **identical** to today's inline build.

- [ ] **Step 1: Write the failing test**

```python
# append tests/test_solver_ground_objects.py
from hangarfit.solver import _build_layout
from hangarfit.models import Placement


def test_build_layout_aircraft_only_matches_plain_layout(region_scenario_no_go):
    s = region_scenario_no_go  # a scenario with NO ground objects
    placements = {"husky": Placement("husky", 5.0, 8.0, 0.0, on_carts=False)}
    from hangarfit.models import Layout
    built = _build_layout(s, placements)
    plain = Layout(fleet=s.fleet, hangar=s.hangar,
                   placements=tuple(placements.values()),
                   maintenance_plane=s.maintenance_plane)
    assert built.placements == plain.placements
    assert built.ground_object_placements == ()


def test_build_layout_splits_movers_and_injects_fixed(region_scenario):
    s = region_scenario  # husky + glider_trailer_1 (mover) + maul_fuel_trailer (fixed pose)
    placements = {
        "husky": Placement("husky", 5.0, 8.0, 0.0, on_carts=False),
        "glider_trailer_1": Placement("glider_trailer_1", 12.0, 20.0, 90.0, on_carts=False),
    }
    built = _build_layout(s, placements)
    assert {p.plane_id for p in built.placements} == {"husky"}
    go_ids = {p.plane_id for p in built.ground_object_placements}
    assert "glider_trailer_1" in go_ids and "maul_fuel_trailer" in go_ids
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_ground_objects.py -q -k build_layout`
Expected: FAIL — `_build_layout` undefined.

- [ ] **Step 3: Implement `_build_layout`**

```python
def _build_layout(scenario: Scenario, placements: Mapping[str, Placement]) -> Layout:
    """Build a Layout from a unified placeable-body dict (#604).

    Splits ``placements`` into aircraft (ids in ``fleet``) and mover ground
    objects (ids in ``ground_object_defs``), injects the authored
    ``fixed_obstacle_placements``, and wires the active ``ground_objects`` defs.
    With an aircraft-only dict and a scenario with no ground objects this yields a
    Layout byte-identical to the pre-#604 inline construction (ADR-0003)."""
    aircraft: list[Placement] = []
    movers: list[Placement] = []
    for pid, p in placements.items():
        (aircraft if pid in scenario.fleet else movers).append(p)
    go_placements = tuple(movers) + scenario.fixed_obstacle_placements
    if not go_placements:
        # No ground objects: identical to today's call (empty GO fields default).
        return Layout(
            fleet=scenario.fleet,
            hangar=scenario.hangar,
            placements=tuple(aircraft),
            maintenance_plane=scenario.maintenance_plane,
        )
    return Layout(
        fleet=scenario.fleet,
        hangar=scenario.hangar,
        placements=tuple(aircraft),
        maintenance_plane=scenario.maintenance_plane,
        ground_objects={gid: scenario.ground_object_defs[gid] for gid in scenario.ground_objects},
        ground_object_placements=go_placements,
    )
```

Note: `Layout.placements` ordering matters for downstream determinism. Today the inline builds use `tuple(placements.values())` over the aircraft dict (insertion order = `fleet_in`). Preserve that: build `aircraft` by iterating `placements` in its existing insertion order (the dict is `fleet_in`-then-mover ordered after Task 8). Since aircraft are inserted first (Task 8 appends movers after), `aircraft` keeps `fleet_in` order. **Verify** with the canaries in Step 5.

- [ ] **Step 4: Replace inline builds**

Replace each `Layout(fleet=scenario.fleet, hangar=scenario.hangar, placements=tuple(<dict>.values()), maintenance_plane=scenario.maintenance_plane)` in `solver.py` with `_build_layout(scenario, <dict>)`. Audit every `Layout(` in solver.py (grep) — the restart body (255/287/320), `_nose_out` (1381), `_spread` trial (1472), `_descent_step` build (1808/1858), the pin-only build (1052), and the final return (2011). **Do not** change `_build_exhausted_result`'s re-check or any `check_layout` call signature.

- [ ] **Step 5: Run the determinism canaries (CRITICAL)**

Run: `pytest tests/test_solver_canaries.py tests/test_solver_parallel.py tests/test_solver_spread.py -q`
Expected: PASS, unchanged. If any canary flips, the refactor changed placement ordering — fix `_build_layout` to match the pre-refactor tuple order before proceeding.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_ground_objects.py
git commit -m "refactor(604): centralize Layout construction in _build_layout (byte-identical)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Unified placeable set in `_initial_placements` (movers sampled; cart-excluded)

**Files:**
- Modify: `src/hangarfit/solver.py` — `_initial_placements` (~1524-1575), `_initial_placement_for_plane` (~1140-1180), `_enumerate_cart_buckets` (mover exclusion — already iterates fleet_in, so movers are naturally excluded; confirm)
- Test: `tests/test_solver_ground_objects.py`

- [ ] **Step 1: Write the failing tests**

```python
# append tests/test_solver_ground_objects.py
import random
from hangarfit.solver import _initial_placements


def test_initial_placements_samples_movers(region_scenario):
    s = region_scenario
    rng = random.Random(0)
    pl = _initial_placements(scenario=s, rng=rng, cart_bucket=frozenset())
    assert "glider_trailer_1" in pl and pl["glider_trailer_1"].on_carts is False


def test_initial_placements_no_go_unchanged(region_scenario_no_go):
    # With no movers, the sampled aircraft poses are byte-identical to a direct
    # pre-#604 sample (same seed, same draw order).
    s = region_scenario_no_go
    a = _initial_placements(scenario=s, rng=random.Random(7), cart_bucket=frozenset())
    b = _initial_placements(scenario=s, rng=random.Random(7), cart_bucket=frozenset())
    assert {k: (v.x_m, v.y_m, v.heading_deg) for k, v in a.items()} == \
           {k: (v.x_m, v.y_m, v.heading_deg) for k, v in b.items()}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_ground_objects.py -q -k initial_placements`
Expected: FAIL — movers not sampled (`KeyError`).

- [ ] **Step 3: Implement**

In `_initial_placements` (after the `fleet_in` loop, ~line 1575), append mover sampling in `sorted(mover_ids)` order. The per-body sampler must use the body's footprint margins, so generalize `_initial_placement_for_plane` to take a body (via `_body`) instead of `scenario.fleet[pid]`:

```python
    # Movers (#604): sampled AFTER all aircraft, in sorted-id order, so adding a
    # mover never reorders aircraft draws (ADR-0003: aircraft-only is unchanged).
    for gid in sorted(scenario.mover_ids):
        placements[gid] = _initial_placement_for_plane(
            plane_id=gid,
            scenario=scenario,
            rng=rng,
            on_carts=False,  # movers never ride carts
        )
```

In `_initial_placement_for_plane` (and the bbox-margin computation it calls), replace `scenario.fleet[plane_id]` with `_body(scenario, plane_id)` and derive the bbox/margins from that body's parts. Confirm `_enumerate_cart_buckets` iterates `scenario.fleet_in` (it does — movers are not in `fleet_in`, so they are already cart-excluded; no change needed).

- [ ] **Step 4: Run to verify pass + canaries**

Run: `pytest tests/test_solver_ground_objects.py -q -k initial_placements && pytest tests/test_solver_canaries.py tests/test_solver_parallel.py -q`
Expected: PASS, canaries unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_ground_objects.py
git commit -m "feat(604): sample mover initial poses in _initial_placements (cart-excluded)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Movers perturbable in the hard descent

**Files:**
- Modify: `src/hangarfit/solver.py` — `_perturb_plane` (~1657), `_generate_candidates` (~1711), `_descent_step` (the conflict-body selection + candidate build)
- Test: `tests/test_solver_ground_objects.py`

Goal: movers are valid perturbation targets in `_descent_step`; the unified placements dict carries them through descent. Geometry/bbox lookups dispatch via `_body`. Byte-identical when no movers (the descent target set == aircraft when no movers exist).

- [ ] **Step 1: Write the failing test**

```python
# append tests/test_solver_ground_objects.py
from hangarfit.solver import _perturb_plane
from hangarfit.models import Placement, SearchConfig
import random


def test_perturb_mover_stays_on_gear(region_scenario):
    s = region_scenario
    cur = Placement("glider_trailer_1", 12.0, 20.0, 90.0, on_carts=False)
    out = _perturb_plane(current=cur, scenario=s, rng=random.Random(0),
                         search=SearchConfig(), large_jump=False)
    assert out.on_carts is False
    assert out.plane_id == "glider_trailer_1"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_ground_objects.py -q -k perturb_mover`
Expected: FAIL — `_perturb_plane` looks up `scenario.fleet[...]` and `KeyError`s on the mover id.

- [ ] **Step 3: Implement**

In `_perturb_plane` and `_generate_candidates`, replace `scenario.fleet[current.plane_id]` / `scenario.fleet[target]` with `_body(scenario, …)` for bbox-margin and footprint computations. In `_descent_step`, the conflict-body selection iterates the placements dict — confirm it does not special-case `scenario.fleet` membership; if it does, broaden it to the unified placeable set (movers are perturbable targets). Pinned bodies (none for movers in this cut) stay excluded.

- [ ] **Step 4: Run to verify pass + canaries**

Run: `pytest tests/test_solver_ground_objects.py -q -k perturb && pytest tests/test_solver_canaries.py tests/test_solver_parallel.py -q`
Expected: PASS, canaries unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_ground_objects.py
git commit -m "feat(604): movers are perturbable bodies in the hard descent

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Energy functions iterate the unified placeable set

**Files:**
- Modify: `src/hangarfit/solver.py` — `_inter_plane_energy` (~1199), `_spread_quality` (~1283), `_back_bias_energy` (~1256)
- Test: `tests/test_solver_region.py` (create)

Replace `cached_parts_world(scenario.fleet[pid], …)` with `_body_parts_world(scenario, pid, …)` in `_inter_plane_energy` and `_spread_quality`. `_back_bias_energy` already sums over `sorted(placements)` and only reads `y_m`, so it works for movers unchanged. The pairwise repulsion now also repels movers from aircraft/each other (desirable).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_solver_region.py
import math
from hangarfit.solver import _inter_plane_energy, _resolve_spread_scale
from hangarfit.models import Placement, SearchConfig


def test_inter_plane_energy_includes_movers(region_scenario):
    s = region_scenario
    scale = _resolve_spread_scale(s, SearchConfig())
    placements = {
        "husky": Placement("husky", 5.0, 8.0, 0.0, on_carts=False),
        "glider_trailer_1": Placement("glider_trailer_1", 6.0, 8.0, 0.0, on_carts=False),
    }
    e = _inter_plane_energy(placements, s, scale)
    assert e > 0.0  # two near bodies repel; mover participates


def test_inter_plane_energy_no_go_byte_identical(region_scenario_no_go):
    s = region_scenario_no_go
    scale = _resolve_spread_scale(s, SearchConfig())
    placements = {"husky": Placement("husky", 5.0, 8.0, 0.0, on_carts=False),
                  "fuji": Placement("fuji", 9.0, 8.0, 0.0, on_carts=False)}
    e1 = _inter_plane_energy(placements, s, scale)
    e2 = _inter_plane_energy(placements, s, scale)
    assert e1 == e2  # exact
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_region.py -q -k inter_plane_energy`
Expected: FAIL — `scenario.fleet[...]` `KeyError` on the mover.

- [ ] **Step 3: Implement**

In `_inter_plane_energy` (line ~1236) and `_spread_quality` (line ~1304), change:
```python
world = {pid: cached_parts_world(scenario.fleet[pid], placements[pid]) for pid in ids}
```
to:
```python
world = {pid: _body_parts_world(scenario, pid, placements[pid]) for pid in ids}
```
(`ids = sorted(placements)` already includes movers once they are in the dict.) Leave `_priority_weight` as-is (movers have no `PlaneConstraint`, so `w == 1.0` for them — neutral, correct).

- [ ] **Step 4: Run to verify pass + canaries**

Run: `pytest tests/test_solver_region.py -q -k inter_plane && pytest tests/test_solver_canaries.py tests/test_solver_spread.py -q`
Expected: PASS, canaries unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_region.py
git commit -m "feat(604): energy functions score movers via _body_parts_world

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `_region_energy` + fold into `_spread._energy` (inert trio)

**Files:**
- Modify: `src/hangarfit/solver.py` — add `_region_energy`; `_spread` `_energy` local (~1425-1439) + the `back_fill`/`region_active` gating (~1418)
- Test: `tests/test_solver_region.py`

- [ ] **Step 1: Write the failing tests (the inert trio + formula)**

```python
# append tests/test_solver_region.py
from hangarfit.solver import _region_energy
from hangarfit.models import RegionPreference


def test_region_energy_empty_is_zero(region_scenario_no_go):
    s = region_scenario_no_go
    placements = {"husky": Placement("husky", 5.0, 8.0, 0.0, on_carts=False)}
    assert _region_energy(placements, s) == 0.0  # no preferences ⇒ inert


def test_region_energy_right_minimized_at_right_wall(region_scenario):
    # right pref: energy DECREASES as x → width_m
    s = region_scenario  # has region_preferences['glider_trailer_1'] = right,1.0
    W = s.hangar.width_m
    near_left = {"glider_trailer_1": Placement("glider_trailer_1", 1.0, 10.0, 0.0, on_carts=False)}
    near_right = {"glider_trailer_1": Placement("glider_trailer_1", W - 1.0, 10.0, 0.0, on_carts=False)}
    assert _region_energy(near_right, s) < _region_energy(near_left, s)


def test_region_energy_formula(region_scenario):
    s = region_scenario
    W = s.hangar.width_m
    x = 3.0
    pl = {"glider_trailer_1": Placement("glider_trailer_1", x, 10.0, 0.0, on_carts=False)}
    # weight 1.0, side right ⇒ (W - x)/W
    assert _region_energy(pl, s) == pytest.approx((W - x) / W)
```

(`import pytest` at top.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_region.py -q -k region_energy`
Expected: FAIL — `_region_energy` undefined.

- [ ] **Step 3: Implement `_region_energy`**

```python
def _region_energy(placements: Mapping[str, Placement], scenario: Scenario) -> float:
    """Soft right/left wall-alignment bias ``R = Σ wₒ·dₒ / W`` (#604).

    For each body ``o`` carrying a :class:`RegionPreference`, ``dₒ`` is the
    distance from ``x_o`` to the preferred wall — ``(W − x_o)`` for ``"right"``,
    ``x_o`` for ``"left"`` — normalized by ``W = hangar.width_m`` so one weight
    reads across hangar sizes (mirrors ``_back_bias_energy``'s ``length_m``
    normalization, #320). Minimized when the body sits at its preferred wall.
    Summed over preferring ids in ``sorted`` order (order-stable float sum).
    RNG-free. ``0.0`` when no preferences (inert ⇒ byte-identical, ADR-0003).
    """
    prefs = scenario.region_preferences
    if not prefs:
        return 0.0
    width = scenario.hangar.width_m
    total = 0.0
    for pid in sorted(prefs):
        if pid not in placements:
            continue  # e.g. maintenance plane (away) — not placed
        pref = prefs[pid]
        x = placements[pid].x_m
        d = (width - x) if pref.side == "right" else x
        total += pref.weight * (d / width)
    return total
```

Fold into `_spread`. At line ~1418 add the gate alongside `back_fill`:
```python
    region_active = bool(scenario.region_preferences)
```
In the `_energy` local (line ~1436-1439) add the term:
```python
        e = _inter_plane_energy(trial, scenario, scale, gap_cache=gap_cache, moved=moved)
        if back_fill:
            e += search.back_bias_weight * _back_bias_energy(trial, scenario)
        if region_active:
            e += _region_energy(trial, scenario)
        return e
```
(The per-object `weight` already scales the term — no extra global multiplier, matching the spec.) Also widen the early-bail guard at line ~1419 so a lone preferring body still runs the hill-climb: change `if not movable or (len(placements) < 2 and not back_fill):` to `... and not back_fill and not region_active):`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_solver_region.py -q -k region_energy`
Expected: PASS.

- [ ] **Step 5: Inert trio at solve level — write + run**

```python
# append tests/test_solver_region.py
from hangarfit.solver import solve
from hangarfit.models import SearchConfig


def _key(layouts):
    return [[(p.plane_id, p.x_m, p.y_m, p.heading_deg) for p in l.placements] for l in layouts]


def test_solve_no_region_pref_byte_identical(region_scenario_no_go):
    s = region_scenario_no_go
    cfg = SearchConfig(max_restarts=4, spread=True)
    a = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    b = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert _key(a.layouts) == _key(b.layouts)


def test_solve_region_pref_active_deterministic(region_scenario):
    s = region_scenario
    cfg = SearchConfig(max_restarts=4, spread=True)
    a = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    b = solve(s, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert _key(a.layouts) == _key(b.layouts)
```

Run: `pytest tests/test_solver_region.py -q && pytest tests/test_solver_canaries.py tests/test_solver_parallel.py tests/test_solver_spread.py -q`
Expected: PASS, canaries unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_region.py
git commit -m "feat(604): _region_energy soft term folded into _spread (default-inert)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: `_spread` movable includes movers + region effect

**Files:**
- Modify: `src/hangarfit/solver.py` — `_spread` `movable` set (~1417)
- Test: `tests/test_solver_region.py`

Today `movable = sorted(pid for pid in placements if pid not in pinned_planes)` — once movers are in `placements` (Task 7), they are already movable. This task adds the **effect** test that proves the region term shifts the trailer, and confirms `movable` includes movers.

- [ ] **Step 1: Write the failing/effect test**

```python
# append tests/test_solver_region.py
def test_region_pref_pulls_trailer_right(region_scenario, region_scenario_left):
    # Same scenario except region side; the right-pref trailer ends further right.
    cfg = SearchConfig(max_restarts=6, spread=True)
    r = solve(region_scenario, search=cfg, seed=1, budget_s=120.0, plan_paths=False)
    l = solve(region_scenario_left, search=cfg, seed=1, budget_s=120.0, plan_paths=False)
    x_right = [p for p in r.layouts[0].ground_object_placements if p.plane_id == "glider_trailer_1"][0].x_m
    x_left = [p for p in l.layouts[0].ground_object_placements if p.plane_id == "glider_trailer_1"][0].x_m
    assert x_right > x_left
```

(`region_scenario_left` fixture = the demo scenario with `side: left`.)

- [ ] **Step 2: Run to verify it currently fails or is flaky without the term, passes with it**

Run: `pytest tests/test_solver_region.py -q -k pulls_trailer_right`
Expected: PASS now that Task 10 added the term (if it does not separate, raise `max_restarts` or `weight` in the fixtures until the pull is robust — document the chosen value).

- [ ] **Step 3: Confirm `movable` includes movers** — no code change expected; add an assertion test:

```python
def test_spread_movable_includes_movers(region_scenario):
    # indirect: after solve, the trailer is not at its initial sample for all seeds
    # (it was moved by spread). Covered by pulls_trailer_right; keep as documentation.
    pass
```

- [ ] **Step 4: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_region.py
git commit -m "test(604): region term pulls the trailer toward its preferred wall

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Populate `region_alignment` diagnostics

**Files:**
- Modify: `src/hangarfit/solver.py` — `_SpreadCandidate` (~1907) add `region_alignment`; compute it where the candidate is built (~293-300); `_build_found_result` (~843) populate from selected candidates
- Test: `tests/test_solver_region.py`

- [ ] **Step 1: Write the failing test**

```python
# append tests/test_solver_region.py
def test_region_alignment_surfaced_in_diagnostics(region_scenario):
    cfg = SearchConfig(max_restarts=6, spread=True)
    r = solve(region_scenario, search=cfg, seed=1, budget_s=120.0, plan_paths=False)
    align = r.diagnostics.region_alignment
    assert len(align) == len(r.layouts)
    layout0 = dict(align[0])
    assert 0.0 <= layout0["glider_trailer_1"] <= 1.0


def test_region_alignment_empty_when_no_pref(region_scenario_no_go):
    cfg = SearchConfig(max_restarts=4, spread=True)
    r = solve(region_scenario_no_go, search=cfg, seed=0, budget_s=120.0, plan_paths=False)
    assert r.diagnostics.region_alignment == ()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_solver_region.py -q -k region_alignment`
Expected: FAIL — alignment always `()`.

- [ ] **Step 3: Implement**

Add a helper to compute per-layout alignment (1.0 at preferred wall):
```python
def _region_alignment(placements: Mapping[str, Placement], scenario: Scenario) -> tuple[tuple[str, float], ...]:
    """Per-preferring-object alignment in [0,1] (1.0 = at preferred wall), sorted by id."""
    prefs = scenario.region_preferences
    if not prefs:
        return ()
    width = scenario.hangar.width_m
    out: list[tuple[str, float]] = []
    for pid in sorted(prefs):
        if pid not in placements:
            continue
        x = placements[pid].x_m
        frac = (x / width) if prefs[pid].side == "right" else (1.0 - x / width)
        out.append((pid, max(0.0, min(1.0, frac))))
    return tuple(out)
```
Add `region_alignment` to `_SpreadCandidate` (NamedTuple, ~1907) and compute it where the candidate is built in the restart body (~line 293, alongside `min_gap, energy = _spread_quality(...)`):
```python
        cand_alignment = _region_alignment(placements, scenario)
        candidate = _SpreadCandidate(
            layout=candidate_layout,
            min_gap=min_gap,
            energy=energy,
            restart_index=restart_index,
            nose_out_flips=n_flips,
            region_alignment=cand_alignment,
        )
```
In `_build_found_result` (~843), add `region_alignment=tuple(c.region_alignment for c in selected)` to the `SolverDiagnostics(...)` kwargs.

- [ ] **Step 4: Run to verify pass + canaries**

Run: `pytest tests/test_solver_region.py -q && pytest tests/test_solver_canaries.py -q`
Expected: PASS, canaries unchanged (alignment is RNG-free; `()` when no prefs).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/solver.py tests/test_solver_region.py
git commit -m "feat(604): populate SolverDiagnostics.region_alignment per layout

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Egress gate fires in `solve` (verification)

**Files:**
- Test only: `tests/test_solver_ground_objects.py`

Once movers are in the solved layout (Tasks 7-12) and the scenario carries a `hard_door_mover` (the Caddy), the existing #603 gate at `solver.py:766` fires. This task is a verification test (no new production code unless the gate path needs a small fix).

- [ ] **Step 1: Write the test**

```python
# append tests/test_solver_ground_objects.py
def test_solve_with_caddy_routability_gate(region_scenario_with_caddy):
    # A scenario including the vw_caddy (hard_door_mover) routes-or-rejects via the
    # egress gate; assert solve completes and, if the caddy is trapped, the layout
    # is dropped (plans[i] is None) or status reflects no routable candidate.
    cfg = SearchConfig(max_restarts=6, spread=True)
    r = solve(region_scenario_with_caddy, search=cfg, seed=0, budget_s=120.0, plan_paths=True)
    # The gate must have been EXERCISED (caddy present in any returned layout's GO):
    if r.layouts:
        go_ids = {p.plane_id for p in r.layouts[0].ground_object_placements}
        assert "vw_caddy" in go_ids
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_solver_ground_objects.py -q -k caddy`
Expected: PASS (or a clear, asserted exit-3/None-plan outcome). If the gate path errors because solve never attached GO placements before #604, this confirms the fix; if it needs a tweak in `_tow_plan_layouts`, make the minimal change and note it.

- [ ] **Step 3: Commit**

```bash
git add tests/test_solver_ground_objects.py
git commit -m "test(604): #603 egress gate now fires in solve once movers are placed

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: CLI surfacing of `region_alignment`

**Files:**
- Modify: `src/hangarfit/cli.py` — human `solve` summary + `--json` payload (find the `min gap` / `min_pairwise_gap_m` emission sites)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# append tests/test_cli.py (mirror test_solve_human_output_shows_min_gap / _has_spread_diagnostics)
def test_solve_human_output_shows_region_alignment(run_cli, region_demo_scenario_path):
    out = run_cli(["solve", str(region_demo_scenario_path)]).out
    assert "region" in out.lower()  # a 'region alignment' line


def test_solve_json_has_region_alignment(run_cli, region_demo_scenario_path):
    import json
    payload = json.loads(run_cli(["solve", str(region_demo_scenario_path), "--json"]).out)
    diag = payload["diagnostics"]
    assert "region_alignment" in diag
    assert len(diag["region_alignment"]) == len(payload["layouts"])
```

(Use the existing CLI test harness/fixtures in `tests/test_cli.py`; `region_demo_scenario_path` points at the Task 16 demo.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cli.py -q -k region`
Expected: FAIL — no region output.

- [ ] **Step 3: Implement**

In `cli.py`, where `min_pairwise_gap_m` is rendered:
- Human: after the `min gap` line, when `diagnostics.region_alignment` is non-empty, emit per-layout `region alignment: <id>=<0.00-1.00>, …`.
- JSON: add `"region_alignment"` to the diagnostics dict, serializing each layout's `((id, a), …)` as a JSON object `{id: a}` (or list of `[id, a]` pairs — match the `min_pairwise_gap_m` null-safety; no `Infinity` tokens, values are finite floats in [0,1]).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_cli.py -q -k region`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/cli.py tests/test_cli.py
git commit -m "feat(604): surface region_alignment in solve human + --json output

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Tractable demo fixture + end-to-end test

**Files:**
- Create: `tests/fixtures/scenario_region_demo.yaml`, `tests/fixtures/fleet_region_demo.yaml` (created in Task 4; finalize here)
- Test: `tests/test_solver_region.py`

The demo: a roomy hangar (reuse `tests/fixtures/test_hangar_large.yaml`'s dimensions or the demo hangar) + 2-3 aircraft (small ones: `fuji`, `cessna_150`) + 2 glider trailers (movers) with a right preference. Tuned so the solver reliably finds a valid layout AND both trailers route AND the right pull is observable. The fleet manifest references the existing catalog (aircraft + `glider_trailer_1/2.yaml`).

- [ ] **Step 1: Author the fixtures**

`tests/fixtures/fleet_region_demo.yaml`:
```yaml
aircraft:
  - ../../data/catalog/fuji_fa200.yaml      # confirm exact catalog filenames
  - ../../data/catalog/cessna_150.yaml
ground_objects:
  - ../../data/catalog/glider_trailer_1.yaml
  - ../../data/catalog/glider_trailer_2.yaml
  - ../../data/catalog/maul_fuel_trailer.yaml
```
`tests/fixtures/scenario_region_demo.yaml`:
```yaml
fleet: fleet_region_demo.yaml
hangar: test_hangar_large.yaml      # roomy enough for 2 aircraft + 2 trailers
fleet_in: [fuji, cessna_150]
ground_objects:
  - object: maul_fuel_trailer
    x_m: 1.5
    y_m: 2.0
    heading_deg: 90.0
  - object: glider_trailer_1
    region_preference: { side: right, weight: 1.5 }
  - object: glider_trailer_2
    region_preference: { side: right, weight: 1.5 }
```
Also create `scenario_region_demo_left.yaml` (identical but `side: left`) for the effect test, and `scenario_region_demo_no_go.yaml` (no `ground_objects:` block) for the inert/byte-identity fixtures. Add the matching pytest fixtures (`region_scenario`, `region_scenario_left`, `region_scenario_no_go`, `region_scenario_with_caddy`) to a `conftest.py` next to the tests or inline.

- [ ] **Step 2: Write the end-to-end test**

```python
# append tests/test_solver_region.py
def test_demo_solver_places_and_routes_both_trailers(region_scenario):
    cfg = SearchConfig(max_restarts=8, spread=True)
    r = solve(region_scenario, search=cfg, seed=0, budget_s=120.0, plan_paths=True)
    assert r.status in ("found", "found_partial")
    go = {p.plane_id for p in r.layouts[0].ground_object_placements}
    assert {"glider_trailer_1", "glider_trailer_2"}.issubset(go)
    # both movers routed (plan present and their moves are non-None)
    plan = r.plans[0]
    assert plan is not None
    mover_moves = [m for m in plan.moves if m.plane_id in ("glider_trailer_1", "glider_trailer_2")]
    assert all(m.path is not None for m in mover_moves)
```

- [ ] **Step 3: Run to verify pass**

Run: `pytest tests/test_solver_region.py -q -k demo`
Expected: PASS. If routing is flaky, raise `--tow-max-expansions` via the search/plan call or loosen the hangar; document the tuned values in the fixture comment.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/scenario_region_demo*.yaml tests/fixtures/fleet_region_demo.yaml tests/test_solver_region.py tests/conftest.py
git commit -m "test(604): tractable demo fixture — solver places+routes+right-biases trailers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Herrenteich scenario opt-in

**Files:**
- Modify: `examples/herrenteich/scenario.yaml` — add the 2 glider trailers (movers, right pref) + fuel-trailer fixed keep-out
- Test: `tests/test_examples.py` (or wherever Herrenteich is smoke-tested)

- [ ] **Step 1: Add the ground_objects block to `examples/herrenteich/scenario.yaml`**

```yaml
ground_objects:
  - object: maul_fuel_trailer
    x_m: <real fixed location x>      # from examples/herrenteich/layout_full.yaml GO block
    y_m: <real fixed location y>
    heading_deg: <real heading>
  - object: glider_trailer_1
    region_preference: { side: right, weight: 1.5 }
  - object: glider_trailer_2
    region_preference: { side: right, weight: 1.5 }
```
(Confirm the fleet manifest `examples/herrenteich/fleet.yaml` already lists these GO catalog files — it does per the data map. Pull the fuel-trailer pose from `layout_full.yaml` lines 66-82.)

- [ ] **Step 2: Write the honest smoke test**

```python
# tests/test_examples.py (or new tests/test_herrenteich_region.py)
def test_herrenteich_scenario_loads_with_region_prefs():
    s = load_scenario("examples/herrenteich/scenario.yaml")
    assert s.region_preferences  # opted in
    assert {"glider_trailer_1", "glider_trailer_2"}.issubset(set(s.mover_ids))


@pytest.mark.slow
def test_herrenteich_solve_is_honest():
    # The real 8-aircraft set may be intractable (#599); assert solve TERMINATES
    # with a well-formed result (found/found_partial/exhausted_budget), never raises.
    s = load_scenario("examples/herrenteich/scenario.yaml")
    r = solve(s, search=SearchConfig(max_restarts=8, spread=True), seed=0, budget_s=30.0)
    assert r.status in ("found", "found_partial", "exhausted_budget")
```

- [ ] **Step 3: Run**

Run: `pytest tests/test_herrenteich_region.py -q` (and `-m slow` for the solve)
Expected: PASS (load test fast; solve test honest, no exception).

- [ ] **Step 4: Commit**

```bash
git add examples/herrenteich/scenario.yaml tests/test_herrenteich_region.py
git commit -m "feat(604): opt Herrenteich scenario into trailers + right region preference

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Movers-active determinism canary

**Files:**
- Test: `tests/test_solver_ground_objects.py` (or `tests/test_solver_canaries.py`)

A dedicated canary that pins same-seed byte-identity with movers + region active (the live-path analogue of the existing `spread=False` canaries). Keep it NON-`@slow` so it stays in the fast coverage set; bound by `max_restarts`, not wall-clock.

- [ ] **Step 1: Write the canary**

```python
# append tests/test_solver_ground_objects.py
def test_solve_with_movers_byte_identical_same_seed(region_scenario):
    cfg = SearchConfig(max_restarts=4, spread=True)
    a = solve(region_scenario, search=cfg, seed=42, budget_s=120.0, plan_paths=False)
    b = solve(region_scenario, search=cfg, seed=42, budget_s=120.0, plan_paths=False)
    def sig(r):
        return [
            [(p.plane_id, p.x_m, p.y_m, p.heading_deg) for p in l.placements]
            + [(p.plane_id, p.x_m, p.y_m, p.heading_deg) for p in l.ground_object_placements]
            for l in r.layouts
        ]
    assert sig(a) == sig(b)
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_solver_ground_objects.py -q -k byte_identical`
Expected: PASS.

- [ ] **Step 3: Run the FULL suite**

Run: `pytest -q` (default markers; then `pytest -m "" -q` for everything once before the PR)
Expected: PASS. A subset can miss `test_loader_catalog` — run the full set as the gate.

- [ ] **Step 4: Commit**

```bash
git add tests/test_solver_ground_objects.py
git commit -m "test(604): movers-active same-seed byte-identity canary (ADR-0003)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 18: Docs — ADR-0008 amendment, ADR-0010 note, crosscutting, CHANGELOG

**Files:**
- Modify: `docs/adr/0008-inter-plane-spread-soft-preference.md` (amendment), `docs/adr/0010-reeds-shepp-motion-model.md` (cross-ref), `docs/architecture/08-crosscutting-concepts.md`, `CHANGELOG.md`

- [ ] **Step 1: ADR-0008 amendment** — append a `### 2026-06-12 — #604 right/left-region soft term` level-3 heading mirroring the 2026-06-01 back-bias amendment structure (Background / Change with the aligned energy box / Default & toggle / Determinism / Known limitation):

```
**Change.** A per-object soft wall-alignment term folded into the same `_spread` energy:

    E_total = Σ_{i<j} w_i·w_j·exp(−gap_ij/scale)  +  back_bias_weight·Σ_p (L−y_p)/L  +  Σ_{o∈prefs} w_o·d_o/W
              └────────── spread ──────────┘        └──────── back bias (#320) ──────┘   └──── region (#604) ────┘

    where d_o = (W − x_o) for side="right", x_o for side="left", W = hangar.width_m.
```
Key sentences to include: per-object preference is **scenario data** (default-inert), the term is **secondary** — `min_pairwise_gap_m` stays the primary basin key (#267); it is **RNG-free re-ranking** (same-seed byte-identical, no determinism-guard amendment needed); and the **known limitation** that the pull is a preference, not a guarantee (a space-tight basin may keep a trailer off-side, validity wins). Note the ground-object integration: movers are now solver-placed bodies (full RR-MC citizens), inert/byte-identical when a scenario has no ground objects.

- [ ] **Step 2: ADR-0010 cross-reference** — append a short note that movers are now *solver-placed* (no new motion model; towed = cart per the #602 amendment).

- [ ] **Step 3: crosscutting `08-…md`** — extend the "Soft preferences" entry (add the region term) and the "Ground objects" entry (now solver-placed in `solve`, not only authored in layouts).

- [ ] **Step 4: CHANGELOG** — add an `[Unreleased]` entry: "Glider trailers are now placed + routed by the solver, with a soft right/left-region preference (#604)."

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0008-inter-plane-spread-soft-preference.md docs/adr/0010-reeds-shepp-motion-model.md docs/architecture/08-crosscutting-concepts.md CHANGELOG.md
git commit -m "docs(604): ADR-0008 region-term amendment + ADR-0010 note + crosscutting + CHANGELOG

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 19: Quality gates + PR

- [ ] **Step 1: Lint, format, type-check**

Run: `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/`
Expected: clean. Fix any findings.

- [ ] **Step 2: Full suite + graphify update**

Run: `pytest -m "" -q` then `graphify update .`
Expected: green.

- [ ] **Step 3: determinism-guard subagent** — dispatch the `determinism-guard` agent on the solver diff (mandatory for any `solver.py` change). It runs the solver twice on a fixed seed and diffs; expected byte-identical. (`geometry-invariant-guard` is NOT triggered — no `geometry.py`/`collisions.py` change.)

- [ ] **Step 4: Open the draft PR**

```bash
git push -u origin feature/604-glider-trailer-region
gh pr create --draft --base develop --title "feat(604): glider-trailer placement + soft right-region preference" \
  --body "Closes #604

Full RR-MC integration of ground objects as solver-placed bodies + the soft right/left-region energy term (the #320 back-bias rotated 90°). Default-inert ⇒ byte-identical when a scenario has no ground objects. See docs/superpowers/specs/2026-06-12-glider-trailer-region-and-go-solver-integration-design.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```
Set assignee/labels/milestone via `gh api -X PATCH` (the repo's `gh pr edit` is broken): assignee `DocGerd`, labels `enhancement` + `area:backend`, milestone #34.

- [ ] **Step 5: Review arc** — run `/pr-review` (code-reviewer mandated; + `determinism-guard`, `type-design-analyzer` since `models.py` changed, `silent-failure-hunter` for the loader change). Convert findings to review threads, resolve each, re-review if non-trivial, then `gh pr ready`. **Do not merge** — hand off to the user.

---

## Self-review (plan vs spec)

- **Spec coverage:** Part A (movers placed: Tasks 7-8; routed + egress: Tasks 13, reuses #602/#603; collisions: existing) ✓; Part B (RegionPreference: Task 1; scenario field: Tasks 2,4; `_region_energy` + fold: Task 10; secondary/inert: Tasks 10-11; determinism: Tasks 10,17) ✓; Part C (diagnostics: Tasks 3,12; CLI: Task 14; calibration via demo: Task 15 + Herrenteich opt-in: Task 16) ✓; ADRs: Task 18 ✓; fixed obstacles in scene: Tasks 2,4,6 ✓.
- **Determinism invariant:** every solver task (6-12, 17) re-runs the canaries; the inert trio (Task 10) + movers-active canary (Task 17) + determinism-guard (Task 19) cover both the absent⇒identical and present⇒deterministic halves.
- **Type consistency:** `RegionPreference{side, weight}`, `Scenario.region_preferences`/`fixed_obstacle_placements`/`mover_ids`/`placeable_ids`, `_body`/`_body_parts_world`/`_build_layout`/`_region_energy`/`_region_alignment`, `_SpreadCandidate.region_alignment`, `SolverDiagnostics.region_alignment` are used with the same signatures across tasks.
- **Open item to firm during execution:** confirm the exact catalog filenames for the demo fleet (Task 15) and the real fuel-trailer pose from `layout_full.yaml` (Task 16); confirm `cached_parts_world` accepts `GroundObject` vs using the uncached `aircraft_parts_world` for movers (Task 5) to match the planner's geometry exactly.
