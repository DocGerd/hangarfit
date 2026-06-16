# Observation Tensorizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ml/encoding.py` — a pure, deterministic, numpy-only `encode(Observation, hangar, bodies, config) -> ObservationTensors` that turns the cold-joint RL env's semantic `Observation` into fixed-shape tensors a policy can consume.

**Architecture:** A 7-channel world-frame raster (static keep-outs + z-split dynamic occupancy, rasterized from the same `geometry.aircraft_parts_world()` polygons the verifier uses) + a `(max_objects, 24)` padded set-token table + a `(9,)` legal-action mask. Fixed metres/cell, zero-padded → scale-preserving. No `torch` (that arrives in sub-project #3); numpy + shapely only (both already deps).

**Tech Stack:** Python 3.12, numpy, shapely ≥2.0 (`shapely.contains_xy`, `shapely.union_all`, `shapely.box`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-16-learned-backend-observation-tensorizer-design.md`

---

## File Structure

- **Create** `ml/encoding.py` — the tensorizer: constants, `EncoderConfig`, `ObservationTensors`, private channel/token/mask helpers, public `encode()`. One cohesive module (mirrors `ml/env.py`, `ml/reward.py` single-purpose modules).
- **Create** `tests/ml/test_encoding.py` — TDD tests, reusing `tests/ml/conftest.py` helpers.
- **Create** `docs/architecture/ml-observation-schema.md` — versioned schema reference (mirrors `scene-v2-schema.md`).
- **Modify** `CHANGELOG.md` — one `[Unreleased]` entry.

**Conventions:** cwd is the repo root; the editable install puts `ml/` on the import path (existing `tests/ml/test_env.py` already does `from ml import ...`). Run tests with `pytest tests/ml/test_encoding.py`. The `.claude/` PostToolUse hook runs ruff + a pytest slice after edits under `src/`/`tests/`; `ml/` edits are not hooked, so run ruff/mypy manually (Task 9).

---

### Task 1: Module scaffold — constants, dataclasses, cell-centre grid

**Files:**
- Create: `ml/encoding.py`
- Test: `tests/ml/test_encoding.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ml/test_encoding.py
"""Tests for the observation tensorizer (ml/encoding.py, sub-project #2)."""

from __future__ import annotations

import numpy as np

from ml import encoding
from ml.encoding import EncoderConfig, _cell_centers


def test_schema_version_and_dims_constants():
    assert encoding.SCHEMA_VERSION == 1
    assert encoding.TOKEN_DIM == 24
    assert encoding.RASTER_CHANNELS == 7
    assert encoding.ACTION_DIM == 9
    assert encoding.PARK_INDEX == 8


def test_config_defaults():
    c = EncoderConfig()
    assert (c.cell_m, c.grid_w, c.grid_h, c.max_objects) == (0.25, 96, 192, 16)
    assert c.z_split_m == 1.6 and c.pos_ref_m == 20.0 and c.apron_band_m == 10.0


def test_cell_centers_shape_and_anchor():
    c = EncoderConfig()
    origin_x, origin_y, xs, ys = _cell_centers(c)
    assert origin_x == 0.0 and origin_y == -10.0
    assert xs.shape == (96,) and ys.shape == (192,)
    # first cell centre is half a cell in from the origin
    assert xs[0] == 0.125
    assert ys[0] == -10.0 + 0.125
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_encoding.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ml.encoding'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ml/encoding.py
"""Observation tensorizer (sub-project #2, epic #607). Pure, deterministic,
numpy-only: turn the cold-joint env's semantic Observation into fixed-shape
tensors a policy can consume. No torch — numpy + shapely only. The contract is
versioned by SCHEMA_VERSION; see docs/architecture/ml-observation-schema.md."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

SCHEMA_VERSION = 1

# Canonical discrete action order for the legal-action mask. Magnitude is a
# sub-project #3 concern; this covers only (kind, gear) legality + PARK.
_CANONICAL_ACTIONS: tuple[tuple[str, int], ...] = (
    ("L", 1), ("S", 1), ("R", 1), ("L", -1), ("S", -1), ("R", -1), ("T", 1), ("T", -1),
)
ACTION_DIM = len(_CANONICAL_ACTIONS) + 1  # + PARK
PARK_INDEX = ACTION_DIM - 1
TOKEN_DIM = 24
RASTER_CHANNELS = 7


@dataclass(frozen=True, slots=True)
class EncoderConfig:
    cell_m: float = 0.25       # metres per raster cell (scale-preserving)
    grid_w: int = 96           # 96 * 0.25 = 24 m window width
    grid_h: int = 192          # 192 * 0.25 = 48 m window height (apron + hangar + margin)
    apron_band_m: float = 10.0  # world-y offset of row 0 below the hangar front (y=0)
    max_objects: int = 16      # token padding cap (Herrenteich set is 12)
    z_split_m: float = 1.6     # low-band / wing-band boundary (ADR-0023)
    pos_ref_m: float = 20.0    # normalization reference for dims / radii


@dataclass(frozen=True, slots=True)
class ObservationTensors:
    raster: np.ndarray             # (RASTER_CHANNELS, grid_h, grid_w) float32
    tokens: np.ndarray             # (max_objects, TOKEN_DIM) float32
    token_mask: np.ndarray         # (max_objects,) bool
    active_index: int              # row of the active object, or -1 at a terminal state
    legal_action_mask: np.ndarray  # (ACTION_DIM,) bool
    meta: dict[str, float]
    schema_version: int = SCHEMA_VERSION


def _cell_centers(config: EncoderConfig) -> tuple[float, float, np.ndarray, np.ndarray]:
    """World (origin_x, origin_y, xs, ys) of cell centres. Hangar front-left = world
    origin; row 0 sits ``apron_band_m`` below the front so the apron (y<0) is visible."""
    origin_x = 0.0
    origin_y = -config.apron_band_m
    xs = origin_x + (np.arange(config.grid_w, dtype=np.float64) + 0.5) * config.cell_m
    ys = origin_y + (np.arange(config.grid_h, dtype=np.float64) + 0.5) * config.cell_m
    return origin_x, origin_y, xs, ys
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_encoding.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(607): tensorizer scaffold — config, dataclasses, cell grid"
```

---

### Task 2: Rasterize a polygon to a binary occupancy channel

**Files:**
- Modify: `ml/encoding.py`
- Test: `tests/ml/test_encoding.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_encoding.py
import shapely

from ml.encoding import _rasterize


def test_rasterize_box_cell_count_and_dtype():
    c = EncoderConfig()
    # a 2 m x 2 m box anchored inside the hangar (x in [10,12], y in [5,7])
    box = shapely.box(10.0, 5.0, 12.0, 7.0)
    grid = _rasterize(box, c)
    assert grid.shape == (192, 96)
    assert grid.dtype == np.float32
    # ~ (2/0.25)^2 = 64 cell centres inside; allow a 1-cell boundary slop per axis
    assert 49 <= int(grid.sum()) <= 81
    # values are binary
    assert set(np.unique(grid)).issubset({0.0, 1.0})


def test_rasterize_none_is_empty():
    c = EncoderConfig()
    grid = _rasterize(None, c)
    assert grid.shape == (192, 96) and grid.sum() == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_encoding.py -q -k rasterize`
Expected: FAIL — `ImportError: cannot import name '_rasterize'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/encoding.py imports
import shapely

# add to ml/encoding.py
def _rasterize(geom: shapely.Geometry | None, config: EncoderConfig) -> np.ndarray:
    """Binary occupancy (grid_h, grid_w) float32: 1.0 where a cell centre is inside
    ``geom``. Deterministic point-in-polygon via ``shapely.contains_xy`` (shapely
    >=2.0; NOT the deprecated ``shapely.vectorized.contains``)."""
    empty = np.zeros((config.grid_h, config.grid_w), dtype=np.float32)
    if geom is None or geom.is_empty:
        return empty
    _, _, xs, ys = _cell_centers(config)
    xx, yy = np.meshgrid(xs, ys)  # (grid_h, grid_w)
    inside = shapely.contains_xy(geom, xx, yy)
    return inside.astype(np.float32)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_encoding.py -q -k rasterize`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(607): tensorizer rasterize helper (shapely.contains_xy)"
```

---

### Task 3: Static keep-out channels (oob, bay, apron, door)

**Files:**
- Modify: `ml/encoding.py`
- Test: `tests/ml/test_encoding.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_encoding.py
from tests.ml.conftest import empty_hangar
from ml.encoding import _static_channels


def test_static_channels_shape_and_content():
    c = EncoderConfig()
    h = empty_hangar()  # synthetic 22 m hangar, apron_depth_m=8.0, has door + bay
    static = _static_channels(h, c)
    assert static.shape == (4, 192, 96) and static.dtype == np.float32
    oob, bay, apron, door = static
    # oob has both inside (0) and outside (1) cells
    assert 0.0 in np.unique(oob) and 1.0 in np.unique(oob)
    # apron occupies the y<0 band → some rows fully set
    assert apron.sum() > 0.0
    # bay and door markers are non-empty
    assert bay.sum() > 0.0
    assert door.sum() > 0.0


def test_static_channels_notch_marks_oob():
    from dataclasses import replace

    from hangarfit.models import StructuralNotch

    c = EncoderConfig()
    h = empty_hangar()
    # carve a notch in the back-right corner; its interior must read oob=1
    notch = StructuralNotch(
        x_min_m=h.width_m - 4.0, x_max_m=h.width_m,
        y_min_m=h.length_m - 4.0, y_max_m=h.length_m,
    )
    h2 = replace(h, structural_notches=(notch,))
    oob = _static_channels(h2, c)[0]
    oob_base = _static_channels(h, c)[0]
    # the notch adds out-of-floor area inside the outer rectangle
    assert oob.sum() > oob_base.sum()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_encoding.py -q -k static`
Expected: FAIL — `ImportError: cannot import name '_static_channels'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/encoding.py imports
from hangarfit.models import Hangar

# add to ml/encoding.py
def _floor_polygon(hangar: Hangar) -> shapely.Geometry:
    fp = hangar.floor_polygon
    if fp is None:
        return shapely.box(0.0, 0.0, hangar.width_m, hangar.length_m)
    return fp


def _static_channels(hangar: Hangar, config: EncoderConfig) -> np.ndarray:
    """(4, grid_h, grid_w): [oob, bay, apron, door]."""
    origin_x, origin_y, xs, ys = _cell_centers(config)
    window = shapely.box(
        origin_x, origin_y,
        origin_x + config.grid_w * config.cell_m,
        origin_y + config.grid_h * config.cell_m,
    )
    # oob: window minus the hangar floor (includes the L-notch and the apron band)
    oob = _rasterize(window.difference(_floor_polygon(hangar)), config)

    bay = hangar.maintenance_bay
    bay_poly = shapely.box(
        bay.center_x_m - bay.width_m / 2.0, hangar.length_m - bay.depth_m,
        bay.center_x_m + bay.width_m / 2.0, hangar.length_m,
    )
    bay_ch = _rasterize(bay_poly, config)

    # apron: full-width band y in [-apron_depth, 0)
    apron_depth = hangar.apron_depth_m or 0.0
    apron_ch = np.zeros((config.grid_h, config.grid_w), dtype=np.float32)
    apron_rows = (ys >= -apron_depth) & (ys < 0.0)
    apron_ch[apron_rows, :] = 1.0

    # door: a one-cell-thick band across the door opening on the front wall (y≈0)
    door = hangar.door
    door_poly = shapely.box(
        door.center_x_m - door.width_m / 2.0, -config.cell_m,
        door.center_x_m + door.width_m / 2.0, config.cell_m,
    )
    door_ch = _rasterize(door_poly, config)

    return np.stack([oob, bay_ch, apron_ch, door_ch]).astype(np.float32)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_encoding.py -q -k static`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(607): tensorizer static keep-out channels (oob/bay/apron/door)"
```

---

### Task 4: Dynamic occupancy channels (parked z-split + active)

**Files:**
- Modify: `ml/encoding.py`
- Test: `tests/ml/test_encoding.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_encoding.py
from hangarfit.models import Placement
from ml.types import ActiveObject, Observation, ParkedObject, Pose
from ml.encoding import _active_occupancy, _parked_occupancy
from tests.ml.conftest import _fuji


def _obs(*, parked=(), active=None, unplaced=()):
    return Observation(
        active=active, parked=tuple(parked), unplaced_ids=tuple(unplaced),
        steps_this_object=0, steps_total=0,
    )


def test_parked_occupancy_has_low_and_wing_bands():
    c = EncoderConfig()
    fleet = _fuji()
    pid = "aviat_husky"  # an aircraft with both low (gear/fuselage) and high (wing) parts
    pl = Placement(plane_id=pid, x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    obs = _obs(parked=(ParkedObject(object_id=pid, placement=pl),))
    low, wing = _parked_occupancy(obs, fleet, c)
    assert low.shape == (192, 96) and wing.shape == (192, 96)
    assert low.sum() > 0.0   # low parts (gear/fuselage) below z_split
    assert wing.sum() > 0.0  # wing/tail above z_split


def test_active_occupancy_painted_at_pose():
    c = EncoderConfig()
    fleet = _fuji()
    body = fleet["fuji"]
    active = ActiveObject(
        object_id="fuji", body=body,
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0), on_carts=False,
    )
    obs = _obs(active=active)
    occ = _active_occupancy(obs, c)
    assert occ.shape == (192, 96) and occ.sum() > 0.0


def test_active_occupancy_empty_when_terminal():
    c = EncoderConfig()
    occ = _active_occupancy(_obs(), c)
    assert occ.sum() == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_encoding.py -q -k occupancy`
Expected: FAIL — `ImportError: cannot import name '_parked_occupancy'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/encoding.py imports
from hangarfit.geometry import aircraft_parts_world
from hangarfit.models import Aircraft, GroundObject, Placement
from ml.types import Observation

# add to ml/encoding.py
def _parked_occupancy(
    obs: Observation, bodies: Mapping[str, Aircraft | GroundObject], config: EncoderConfig
) -> tuple[np.ndarray, np.ndarray]:
    """(low, wing): parked part polygons split by z-band at ``z_split_m``. A part
    paints LOW when it has mass below the split (z_bottom < z_split) and WING when it
    has mass above it (z_top > z_split); a part spanning the split paints both."""
    low_polys: list[shapely.Geometry] = []
    wing_polys: list[shapely.Geometry] = []
    for po in obs.parked:
        body = bodies[po.object_id]
        for wp in aircraft_parts_world(body, po.placement):
            if wp.z_bottom_m < config.z_split_m:
                low_polys.append(wp.polygon)
            if wp.z_top_m > config.z_split_m:
                wing_polys.append(wp.polygon)
    low = _rasterize(shapely.union_all(low_polys) if low_polys else None, config)
    wing = _rasterize(shapely.union_all(wing_polys) if wing_polys else None, config)
    return low, wing


def _active_occupancy(obs: Observation, config: EncoderConfig) -> np.ndarray:
    """Active object's footprint at its current pose (single band), or zeros if none."""
    if obs.active is None:
        return np.zeros((config.grid_h, config.grid_w), dtype=np.float32)
    a = obs.active
    placement = Placement(
        plane_id=a.object_id, x_m=a.pose.x_m, y_m=a.pose.y_m,
        heading_deg=a.pose.heading_deg, on_carts=a.on_carts,
    )
    polys = [wp.polygon for wp in aircraft_parts_world(a.body, placement)]
    return _rasterize(shapely.union_all(polys) if polys else None, config)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_encoding.py -q -k occupancy`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(607): tensorizer dynamic occupancy (parked z-split + active)"
```

---

### Task 5: Set-token table + mask + active index

**Files:**
- Modify: `ml/encoding.py`
- Test: `tests/ml/test_encoding.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_encoding.py
from ml.encoding import TOKEN_DIM, _tokens


def test_tokens_status_type_pose_and_padding():
    c = EncoderConfig()
    fleet = _fuji()
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky", body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=90.0), on_carts=False,
    )
    obs = _obs(
        parked=(ParkedObject(object_id="fuji", placement=pl),),
        active=active, unplaced=("cessna_150",),
    )
    tokens, mask, active_index = _tokens(obs, fleet, c)
    assert tokens.shape == (16, TOKEN_DIM) and tokens.dtype == np.float32
    assert mask.dtype == bool
    # order = [parked, active, unplaced], padded
    assert list(mask[:3]) == [True, True, True] and mask[3:].sum() == 0
    assert active_index == 1
    # status one-hots (cols 0..2): parked / active / unplaced
    assert list(tokens[0, 0:3]) == [1.0, 0.0, 0.0]
    assert list(tokens[1, 0:3]) == [0.0, 1.0, 0.0]
    assert list(tokens[2, 0:3]) == [0.0, 0.0, 1.0]
    # all three are aircraft (type col 3)
    assert tokens[0, 3] == 1.0 and tokens[1, 3] == 1.0 and tokens[2, 3] == 1.0
    # unplaced row has zero pose (cols 18..21)
    assert list(tokens[2, 18:22]) == [0.0, 0.0, 0.0, 0.0]
    # active pose is populated and heading 90deg -> sin≈1, cos≈0 (cols 20,21)
    assert abs(tokens[1, 20] - 1.0) < 1e-6 and abs(tokens[1, 21]) < 1e-6
    # reserved slots (22,23) are zero in v1
    assert list(tokens[1, 22:24]) == [0.0, 0.0]
    # padding rows are all zero
    assert tokens[5].sum() == 0.0


def test_tokens_wing_and_movement_one_hots():
    c = EncoderConfig()
    fleet = _fuji()
    pl = Placement(plane_id="aviat_husky", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    obs = _obs(parked=(ParkedObject(object_id="aviat_husky", placement=pl),))
    tokens, _, _ = _tokens(obs, fleet, c)
    # wing one-hot is cols 8..10; exactly one set for an aircraft
    assert tokens[0, 8:11].sum() == 1.0
    # movement one-hot is cols 11..13; exactly one set for an aircraft
    assert tokens[0, 11:14].sum() == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_encoding.py -q -k tokens`
Expected: FAIL — `ImportError: cannot import name '_tokens'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/encoding.py
def _body_dims(body: Aircraft | GroundObject, config: EncoderConfig) -> tuple[float, float]:
    """(length, width) of the body footprint in its own frame, normalized by pos_ref_m.
    At heading 0 the determinant-−1 transform maps fore-aft to world-y and lateral to
    world-x, so y-extent is length and x-extent is width."""
    parts = aircraft_parts_world(
        body, Placement(plane_id=body.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
    )
    geom = shapely.union_all([wp.polygon for wp in parts])
    minx, miny, maxx, maxy = geom.bounds
    return (maxy - miny) / config.pos_ref_m, (maxx - minx) / config.pos_ref_m


def _norm_pos(x_m: float, y_m: float, config: EncoderConfig) -> tuple[float, float]:
    """World (x, y) → [-1, 1] over the raster window."""
    nx = 2.0 * x_m / (config.grid_w * config.cell_m) - 1.0
    origin_y = -config.apron_band_m
    ny = 2.0 * (y_m - origin_y) / (config.grid_h * config.cell_m) - 1.0
    return nx, ny


_STATUS_COL = {"parked": 0, "active": 1, "unplaced": 2}
_WING_COL = {"high": 0, "mid": 1, "low": 2}
_MOVE_COL = {"always_cart": 0, "cart_eligible": 1, "always_own_gear": 2}


def _token_row(
    body: Aircraft | GroundObject, *, status: str, on_carts: bool,
    pose: tuple[float, float, float] | None, config: EncoderConfig,
) -> np.ndarray:
    row = np.zeros(TOKEN_DIM, dtype=np.float32)
    row[_STATUS_COL[status]] = 1.0  # status one-hot 0..2
    if isinstance(body, Aircraft):
        row[3] = 1.0  # type: aircraft
        row[8 + _WING_COL[body.wing_position]] = 1.0   # wing one-hot 8..10
        row[11 + _MOVE_COL[body.movement_mode]] = 1.0  # movement one-hot 11..13
        row[15] = 1.0 if body.tow_pivotable else 0.0
    else:
        row[4 if body.object_class == "fixed_obstacle" else 5] = 1.0  # type 4..5
        row[17] = 1.0 if body.hard_door_mover else 0.0  # door flag
    length, width = _body_dims(body, config)
    row[6], row[7] = length, width                       # dims 6..7 [length, width]
    row[14] = 1.0 if on_carts else 0.0                   # cart flag (from placement/active)
    row[16] = body.effective_turn_radius_m() / config.pos_ref_m  # turn radius
    if pose is not None:
        nx, ny = _norm_pos(pose[0], pose[1], config)
        th = np.radians(pose[2])
        row[18], row[19], row[20], row[21] = nx, ny, float(np.sin(th)), float(np.cos(th))
    # reserved 22..23 stay 0 (region_side, seq_order)
    return row


def _tokens(
    obs: Observation, bodies: Mapping[str, Aircraft | GroundObject], config: EncoderConfig
) -> tuple[np.ndarray, np.ndarray, int]:
    tokens = np.zeros((config.max_objects, TOKEN_DIM), dtype=np.float32)
    mask = np.zeros(config.max_objects, dtype=bool)
    entries: list[tuple[Aircraft | GroundObject, str, bool, tuple[float, float, float] | None]] = []
    for po in obs.parked:
        pl = po.placement
        entries.append((bodies[po.object_id], "parked", pl.on_carts, (pl.x_m, pl.y_m, pl.heading_deg)))
    active_index = -1
    if obs.active is not None:
        active_index = len(entries)
        a = obs.active
        entries.append((a.body, "active", a.on_carts, (a.pose.x_m, a.pose.y_m, a.pose.heading_deg)))
    for oid in obs.unplaced_ids:
        entries.append((bodies[oid], "unplaced", False, None))
    # Invariant: requested set <= max_objects (curriculum config). Defensive truncation
    # of the unplaced tail keeps the array fixed-shape; the active row is never dropped
    # because parked + active <= requested set <= max_objects.
    if active_index >= config.max_objects:
        active_index = -1
    for i, (body, status, on_carts, pose) in enumerate(entries[: config.max_objects]):
        tokens[i] = _token_row(body, status=status, on_carts=on_carts, pose=pose, config=config)
        mask[i] = True
    return tokens, mask, active_index
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_encoding.py -q -k tokens`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(607): tensorizer set-token table + mask + active index"
```

---

### Task 6: Legal-action mask

**Files:**
- Modify: `ml/encoding.py`
- Test: `tests/ml/test_encoding.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_encoding.py
from ml.encoding import ACTION_DIM, PARK_INDEX, _legal_action_mask


def _active_for(fleet, pid, *, on_carts):
    return ActiveObject(
        object_id=pid, body=fleet[pid],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0), on_carts=on_carts,
    )


def test_legal_mask_own_gear_no_strafe():
    c = EncoderConfig()
    fleet = _fuji()
    # fuji is always_own_gear (turn radius > 0): no strafe (idx6, idx7 False)
    mask = _legal_action_mask(_obs(active=_active_for(fleet, "fuji", on_carts=False)), c)
    assert mask.shape == (ACTION_DIM,) and mask.dtype == bool
    assert mask[6] == False and mask[7] == False
    assert mask[PARK_INDEX] == True


def test_legal_mask_cart_has_strafe_and_holes():
    c = EncoderConfig()
    fleet = _fuji()
    cart_ids = [i for i, b in fleet.items() if b.effective_turn_radius_m() == 0.0]
    assert cart_ids, "expected at least one cart/pivot body in the fleet"
    mask = _legal_action_mask(_obs(active=_active_for(fleet, cart_ids[0], on_carts=True)), c)
    # strafe legal when on carts
    assert mask[6] == True and mask[7] == True
    # cart reverse-arc holes idx3 (L,-1) and idx5 (R,-1) are False
    assert mask[3] == False and mask[5] == False
    assert mask[PARK_INDEX] == True


def test_legal_mask_terminal_all_false():
    c = EncoderConfig()
    mask = _legal_action_mask(_obs(), c)
    assert mask.sum() == 0  # entirely False, including PARK
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_encoding.py -q -k legal_mask`
Expected: FAIL — `ImportError: cannot import name '_legal_action_mask'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/encoding.py imports
from ml import geometry_oracle as go

# add to ml/encoding.py
_ACTION_INDEX = {kg: i for i, kg in enumerate(_CANONICAL_ACTIONS)}


def _legal_action_mask(obs: Observation, config: EncoderConfig) -> np.ndarray:
    """(ACTION_DIM,) bool over the canonical (kind, gear) order + PARK. Entirely False
    at a terminal state (no active object); PARK always legal otherwise."""
    mask = np.zeros(ACTION_DIM, dtype=bool)
    if obs.active is None:
        return mask
    for prim in go.legal_primitives(obs.active.body, on_carts=obs.active.on_carts):
        idx = _ACTION_INDEX.get((prim.kind, prim.gear))
        if idx is not None:
            mask[idx] = True
    mask[PARK_INDEX] = True
    return mask
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_encoding.py -q -k legal_mask`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(607): tensorizer legal-action mask"
```

---

### Task 7: Public `encode()` + determinism

**Files:**
- Modify: `ml/encoding.py`
- Test: `tests/ml/test_encoding.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/ml/test_encoding.py
from ml.encoding import RASTER_CHANNELS, encode


def _two_body_obs(fleet):
    pl = Placement(plane_id="fuji", x_m=11.0, y_m=12.0, heading_deg=0.0, on_carts=False)
    active = ActiveObject(
        object_id="aviat_husky", body=fleet["aviat_husky"],
        pose=Pose(x_m=11.0, y_m=-4.0, heading_deg=0.0), on_carts=False,
    )
    return _obs(parked=(ParkedObject(object_id="fuji", placement=pl),), active=active)


def test_encode_full_shapes_and_meta():
    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    out = encode(_two_body_obs(fleet), h, fleet, c)
    assert out.schema_version == 1
    assert out.raster.shape == (RASTER_CHANNELS, 192, 96) and out.raster.dtype == np.float32
    assert out.tokens.shape == (16, 24) and out.tokens.dtype == np.float32
    assert out.token_mask.shape == (16,) and out.token_mask.dtype == bool
    assert out.legal_action_mask.shape == (9,)
    assert out.active_index == 1
    assert set(out.meta) == {
        "cell_m", "origin_x_m", "origin_y_m", "grid_h", "grid_w",
        "steps_this_object", "steps_total",
    }
    assert all(isinstance(v, float) for v in out.meta.values())


def test_encode_is_deterministic():
    c = EncoderConfig()
    fleet = _fuji()
    h = empty_hangar()
    a = encode(_two_body_obs(fleet), h, fleet, c)
    b = encode(_two_body_obs(fleet), h, fleet, c)
    assert np.array_equal(a.raster, b.raster)
    assert np.array_equal(a.tokens, b.tokens)
    assert np.array_equal(a.token_mask, b.token_mask)
    assert np.array_equal(a.legal_action_mask, b.legal_action_mask)
    assert a.active_index == b.active_index and a.meta == b.meta
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/ml/test_encoding.py -q -k encode`
Expected: FAIL — `ImportError: cannot import name 'encode'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to ml/encoding.py
def encode(
    obs: Observation,
    hangar: Hangar,
    bodies: Mapping[str, Aircraft | GroundObject],
    config: EncoderConfig = EncoderConfig(),
) -> ObservationTensors:
    """Tensorize a semantic Observation. Pure + deterministic (no RNG). ``bodies`` is
    fleet ∪ ground_objects (Observation.parked carries only id+Placement, so the encoder
    looks each body up for its polygons and features)."""
    static = _static_channels(hangar, config)                 # (4, H, W)
    low, wing = _parked_occupancy(obs, bodies, config)
    active = _active_occupancy(obs, config)
    raster = np.concatenate([static, np.stack([low, wing, active])], axis=0).astype(np.float32)
    tokens, token_mask, active_index = _tokens(obs, bodies, config)
    legal = _legal_action_mask(obs, config)
    meta = {
        "cell_m": float(config.cell_m),
        "origin_x_m": 0.0,
        "origin_y_m": float(-config.apron_band_m),
        "grid_h": float(config.grid_h),
        "grid_w": float(config.grid_w),
        "steps_this_object": float(obs.steps_this_object),
        "steps_total": float(obs.steps_total),
    }
    return ObservationTensors(
        raster=raster, tokens=tokens, token_mask=token_mask,
        active_index=active_index, legal_action_mask=legal,
        meta=meta, schema_version=SCHEMA_VERSION,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/ml/test_encoding.py -q -k encode`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the whole encoding suite**

Run: `pytest tests/ml/test_encoding.py -q`
Expected: PASS (all ~17 tests).

- [ ] **Step 6: Commit**

```bash
git add ml/encoding.py tests/ml/test_encoding.py
git commit -m "feat(607): tensorizer encode() + determinism test"
```

---

### Task 8: Schema reference doc + CHANGELOG

**Files:**
- Create: `docs/architecture/ml-observation-schema.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write the schema reference doc**

Create `docs/architecture/ml-observation-schema.md` with the full contract (this is the source of truth the spec defers to):

```markdown
# ML observation tensor schema (`SCHEMA_VERSION = 1`)

The contract produced by `ml/encoding.py:encode()` (learned-backend sub-project #2,
epic #607). Mirrors `docs/architecture/scene-v2-schema.md`. Pure/deterministic; numpy
only. `ObservationTensors` fields:

## `raster` — `(7, grid_h, grid_w)` float32 (default 7×192×96)
World-frame, fixed `cell_m=0.25` m/cell, hangar front-left = world origin; row 0 sits
`apron_band_m=10` m below the front (apron `y<0` occupies the top rows).

| ch | name | meaning |
|---|---|---|
| 0 | `oob_mask` | cell centre outside `hangar.floor_polygon` (incl. L-notch + apron + beyond-length margin) |
| 1 | `bay_mask` | maintenance-bay rectangle |
| 2 | `apron_mask` | staging band `y ∈ [-apron_depth_m, 0)` |
| 3 | `door_gap` | one-cell band across the door opening on the front wall |
| 4 | `parked_occ_low` | parked part polygons with `z_bottom < z_split_m` |
| 5 | `parked_occ_wing` | parked part polygons with `z_top > z_split_m` |
| 6 | `active_occ` | active object footprint at its current pose |

## `tokens` — `(max_objects, 24)` float32 (default 16×24); `token_mask` — `(max_objects,)` bool
Row order: parked (placement order) → active → unplaced (queue order), padded. Column layout:

| cols | feature |
|---|---|
| 0–2 | status one-hot `[parked, active, unplaced]` |
| 3–5 | type one-hot `[aircraft, fixed_obstacle, placed_routed_mover]` |
| 6–7 | body-frame dims `[length, width]` ÷ `pos_ref_m` |
| 8–10 | wing one-hot `[high, mid, low]` (aircraft only; all-zero for ground objects) |
| 11–13 | movement one-hot `[always_cart, cart_eligible, always_own_gear]` (aircraft only) |
| 14–15 | `[on_carts, tow_pivotable]` (`on_carts` from `Placement`/`ActiveObject`) |
| 16 | `effective_turn_radius_m()` ÷ `pos_ref_m` |
| 17 | `hard_door_mover` (ground objects) |
| 18–21 | pose `[x, y, sinθ, cosθ]` (parked & active; zeros for unplaced) |
| 22–23 | reserved (zero in v1): `region_side`, `seq_order` |

`active_index`: row of the active object, or `-1` at a terminal state.

## `legal_action_mask` — `(9,)` bool
Canonical order `[(L,+), (S,+), (R,+), (L,−), (S,−), (R,−), (T,+), (T,−), PARK]`. Cart
reverse-arc slots idx3/idx5 are False (`_primitives` omits `Lr`/`Rr` at r=0). Entirely
False (incl. PARK) at a terminal state.

## `meta` — `dict[str, float]`
Keys: `cell_m`, `origin_x_m`, `origin_y_m`, `grid_h`, `grid_w`, `steps_this_object`,
`steps_total` (integers stored as floats). For debugging / un-normalization.

## Versioning
Adding/reordering channels or token columns, or wiring the reserved slots, bumps
`SCHEMA_VERSION`. Validity-only across machines; bit-identical within a build.
```

- [ ] **Step 2: Add the CHANGELOG entry**

Under the `## [Unreleased]` heading in `CHANGELOG.md`, add (in the `Added` subsection, creating it if absent):

```markdown
- Learned backend (#607, sub-project #2): a numpy-only observation tensorizer
  (`ml/encoding.py`) turning the cold-joint env's `Observation` into fixed-shape
  tensors — a 7-channel world-frame raster (static keep-outs + z-split occupancy),
  a `(16, 24)` set-token table, and a `(9,)` legal-action mask — with a versioned
  `SCHEMA_VERSION` contract (`docs/architecture/ml-observation-schema.md`). Dev-only
  (`ml/` is not in the wheel); no new runtime dependency.
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture/ml-observation-schema.md CHANGELOG.md
git commit -m "docs(607): observation tensor schema reference + CHANGELOG"
```

---

### Task 9: Lint, type-check, full suite, push, PR

**Files:** none (verification + workflow)

- [ ] **Step 1: Lint + format**

Run: `ruff check ml/encoding.py tests/ml/test_encoding.py && ruff format --check ml/encoding.py tests/ml/test_encoding.py`
Expected: no errors. (If format fails: `ruff format ml/encoding.py tests/ml/test_encoding.py` then re-commit.)

- [ ] **Step 2: Type-check**

Run: `mypy ml/encoding.py`
Expected: no errors. (`ml/` is not under `mypy src/hangarfit/`, so run it explicitly. Resolve any `Aircraft | GroundObject` narrowing the impl needs via `isinstance`.)

- [ ] **Step 3: Full ml suite (no regressions)**

Run: `pytest tests/ml/ -q`
Expected: PASS — the new ~17 encoding tests plus the existing 27 env/reward/oracle tests.

- [ ] **Step 4: Commit any lint/type fixups, then push the branch**

```bash
git add -A && git commit -m "chore(607): ruff/mypy cleanup for tensorizer" --allow-empty
git push -u origin feature/607-rung3-observation-tensorizer
```

- [ ] **Step 5: Open the draft PR (base develop)**

```bash
gh pr create --draft --base develop \
  --title "feat(607): observation tensorizer (sub-project #2 impl)" \
  --body "Closes #676. Implements the observation tensorizer per the design spec (PR #675). numpy-only; ml/ stays out of the wheel; SCHEMA_VERSION=1 contract. Review arc: code-reviewer + silent-failure-hunter (geometry/keep-out edges). geometry-invariant-guard NOT required (geometry.py/collisions.py untouched — consumed only)."
```

- [ ] **Step 6: Run the review arc, resolve threads, flip to ready**

Invoke `/pr-review` (code-reviewer main pass + silent-failure-hunter). Convert findings to review threads, fix or rebut each, then `gh pr ready <n>` and hand off to the user (who is the sole merger).

---

## Self-Review (completed during plan authoring)

- **Spec coverage:** §3 public surface → Task 1/7; §4 framing → Task 1 (`_cell_centers`) + Task 2; §5 raster channels → Task 3 (static) + Task 4 (dynamic, z-split); §6 tokens (F=24, reserved slots) → Task 5; §7 legal mask (9-slot, cart holes, terminal) → Task 6; §8 normalization + determinism → Task 5 (`_norm_pos`) + Task 7 (determinism test); §9 testing → Tasks 1–7; §11 schema doc → Task 8; §12 workflow → Task 9. No gaps.
- **Type consistency:** `EncoderConfig`/`ObservationTensors`/`encode` signatures identical across tasks; column indices in `_token_row` (0–2,3–5,6–7,8–10,11–13,14–15,16,17,18–21,22–23) sum to 24 = `TOKEN_DIM`; raster = 4 static + 3 dynamic = 7 = `RASTER_CHANNELS`; action order = 8 primitives + PARK = 9 = `ACTION_DIM`, `PARK_INDEX=8`. Consistent.
- **Placeholder scan:** every code step contains complete, runnable code; no TBD/TODO.
- **Known invariant (documented, not a gap):** `_tokens` assumes the requested set ≤ `max_objects`; the unplaced tail is truncated defensively and the active row is never dropped (parked+active ≤ requested ≤ max_objects).
