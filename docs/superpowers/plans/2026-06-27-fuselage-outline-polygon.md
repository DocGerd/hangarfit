# Fuselage outline polygon (#550) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `kind: fuselage` part carry a tapered `vertices:` outline that the loader Shapely-clips into area-conserving `fuselage_front`/`fuselage_aft` sub-polygons at the wing trailing edge (capability-only; real fleet byte-identical).

**Architecture:** All work is in `src/hangarfit/loader.py` plus tests + docs. The collision predicate, the det(−1) transform (`aircraft_parts_world`), and the scene/v2 viewer seam are already polygon-generic (spike #541), so they are untouched. `_split_fuselage` gains a polygon branch; a new `vertices:` YAML key sets `Part.local_vertices` directly.

**Tech Stack:** Python 3.12, Shapely (core dep), pytest, ruff, mypy. Design spec: `docs/superpowers/specs/2026-06-27-fuselage-outline-polygon-design.md`.

## Global Constraints

- **Byte-identical real fleet.** The scalar (`local_vertices is None`) `_split_fuselage` path must stay literally unchanged; no catalog/example aircraft gains `vertices:`. A guard test enforces this.
- **Determinism (ADR-0003).** The clip is interpolation-only (no trig); all constructed sub-Parts pass through `Part.__post_init__` canonicalization, so same YAML → byte-identical sub-polygons.
- **Placeholder-rename gotcha.** A `kind: fuselage` YAML entry is built by `_build_part` under the renamed kind `"fuselage_aft"` (loader.py:1322); `"fuselage"` is **not** a valid `PartKind`. So the `vertices:` kind-scope gate must allow the fuselage family `{"fuselage_front", "fuselage_aft"}`, never literally `"fuselage"`.
- **`vertices:` rules.** Mutually exclusive with `planform:`; allowed only on the fuselage family; value is a list of `[x, y]` pairs in the part's **own centred frame** (each within `±length_m/2 × ±width_m/2`).
- **Polygon fuselage must be axis-aligned** (`angle_deg == 0`, tolerance `1e-9`).
- **No silent fallbacks.** An outline that cannot cleanly split is a `LoaderError`, never a silent revert to a box.
- Lint/type clean: `ruff check src/ tests/`, `ruff format --check`, `mypy src/hangarfit/` all pass. Delivered via a GitFlow PR (`feature/550-fuselage-outline-polygon` → `develop`, body `Closes #550`) with a `CHANGELOG.md [Unreleased]` entry.

Reference helpers already in the test suite: `tests/test_collisions.py` uses `check(_load("fixture"))`, `result.valid`, `result.conflicts`, and a local `_conflict_kinds(result)` helper. `models.Part` is a frozen dataclass; `Vertex = tuple[float, float]`; degeneracy floor `_RING_MIN_ABS_SIGNED_AREA = 1e-9`.

---

### Task 1: `vertices:` authoring key in the loader

**Files:**
- Modify: `src/hangarfit/loader.py` (`_ALLOWED_PART_KEYS` ~line 1494; `_build_part` ~line 1558)
- Test: `tests/test_loader_fuselage_outline.py` (create)

**Interfaces:**
- Consumes: `models.Part(local_vertices=...)`, `LoaderError`, `_build_part(data, index)`.
- Produces: `_build_part` accepts a `"vertices"` key → a `Part` with `local_vertices` set; raises `LoaderError` on `vertices`+`planform` together or `vertices` on a non-fuselage-family kind.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_loader_fuselage_outline.py
import pytest
from hangarfit.loader import _build_part, LoaderError


def _fuselage_outline_dict(**over):
    # A simple symmetric tapered outline in the part's own centred frame,
    # within +/- length/2 (=2.0) x +/- width/2 (=0.5). Pointed nose at +x.
    d = {
        "kind": "fuselage_aft",  # the loader's placeholder rename for `kind: fuselage`
        "length_m": 4.0,
        "width_m": 1.0,
        "z_bottom_m": 0.3,
        "z_top_m": 1.4,
        "vertices": [[2.0, 0.0], [0.5, 0.5], [-2.0, 0.5], [-2.0, -0.5], [0.5, -0.5]],
    }
    d.update(over)
    return d


def test_vertices_key_sets_local_vertices():
    part = _build_part(_fuselage_outline_dict(), 0)
    assert part.local_vertices is not None
    assert len(part.local_vertices) == 5


def test_vertices_and_planform_mutually_exclusive():
    d = _fuselage_outline_dict(kind="wing", planform={"root_chord_m": 1.0, "tip_chord_m": 0.5})
    with pytest.raises(LoaderError, match="mutually exclusive|both"):
        _build_part(d, 0)


def test_vertices_rejected_on_non_fuselage_kind():
    d = _fuselage_outline_dict(kind="wing")
    with pytest.raises(LoaderError, match="vertices"):
        _build_part(d, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_loader_fuselage_outline.py -v`
Expected: FAIL — `_build_part` rejects the unknown key `vertices` (`has unknown key(s) ['vertices']`).

- [ ] **Step 3: Implement the `vertices:` key**

In `src/hangarfit/loader.py`, add `"vertices"` to `_ALLOWED_PART_KEYS`:

```python
_ALLOWED_PART_KEYS = frozenset(
    {
        "kind",
        "length_m",
        "width_m",
        "offset_x_m",
        "offset_y_m",
        "angle_deg",
        "z_bottom_m",
        "z_top_m",
        "planform",
        "vertices",
    }
)
```

Add a module constant near the other part constants:

```python
# vertices: is valid only on the fuselage family. `kind: fuselage` is built under
# the placeholder kind "fuselage_aft" (see _build_aircraft), so "fuselage" itself
# is never a Part kind here.
_VERTICES_ALLOWED_KINDS = frozenset({"fuselage_front", "fuselage_aft"})
```

In `_build_part`, after the existing `planform`-is-wing-only check (line ~1571-1575) and before computing `width_m`/`length_m`, add:

```python
    if "vertices" in data and "planform" in data:
        raise LoaderError(
            f"parts[{index}]: 'vertices:' and 'planform:' are mutually exclusive "
            f"(both author a polygon footprint)"
        )
    if "vertices" in data and data["kind"] not in _VERTICES_ALLOWED_KINDS:
        raise LoaderError(
            f"parts[{index}]: 'vertices:' is only valid on a fuselage part "
            f"(authored as kind 'fuselage'), got kind {data['kind']!r}"
        )
```

Then, where `local_vertices` is computed (line ~1578-1580), add the `vertices` branch:

```python
    local_vertices = None
    if "planform" in data:
        local_vertices = _build_planform(data["planform"], width_m, length_m, index)
    elif "vertices" in data:
        local_vertices = _build_vertices(data["vertices"], index)
```

Add the `_build_vertices` helper near `_build_planform`:

```python
def _build_vertices(data: Any, index: int) -> tuple[tuple[float, float], ...]:
    """Parse a raw `vertices:` ring (part-own centred frame) into Part vertices.

    Each entry is an ``[x, y]`` pair. Part.__post_init__ canonicalizes the ring
    (ADR-0003) and enforces the bbox subset; this helper only does shape/type
    validation so a malformed entry is a clear LoaderError, not a deep TypeError.
    """
    if not isinstance(data, list):
        raise LoaderError(f"parts[{index}].vertices must be a list of [x, y] pairs")
    ring: list[tuple[float, float]] = []
    for j, pair in enumerate(data):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise LoaderError(
                f"parts[{index}].vertices[{j}] must be an [x, y] pair, got {pair!r}"
            )
        x = _to_float(pair[0], f"parts[{index}].vertices[{j}][0]")
        y = _to_float(pair[1], f"parts[{index}].vertices[{j}][1]")
        ring.append((x, y))
    return tuple(ring)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_loader_fuselage_outline.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_fuselage_outline.py
git commit -m "feat(550): accept a raw vertices: outline on fuselage parts"
```

---

### Task 2: `_split_fuselage` polygon clip (the core algorithm)

**Files:**
- Modify: `src/hangarfit/loader.py` (`_split_fuselage` ~line 1721; add `_clip_fuselage_outline` + `_subpart_from_clip`; add a Shapely import)
- Test: `tests/test_loader_fuselage_outline.py` (extend)

**Interfaces:**
- Consumes: `_wing_trailing_edge_x(wing)`, `models.Part`, `_RING_MIN_ABS_SIGNED_AREA` (import from `hangarfit.models`), Shapely `Polygon`/`box`.
- Produces: `_split_fuselage(fuselage, wing)` returns `[fuselage_front, fuselage_aft]` polygon Parts when `fuselage.local_vertices is not None`; scalar path unchanged. Raises `LoaderError` on `angle_deg != 0`, break outside span, or a non-single-Polygon clip.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_loader_fuselage_outline.py
import math
from hangarfit.loader import _split_fuselage
from hangarfit.models import Part
from shapely.geometry import Polygon


def _outline_fuselage(angle_deg=0.0):
    # Tapered tube: pointed nose at +x=2, full-width cabin to tail at x=-2.
    return Part(
        kind="fuselage_aft",
        length_m=4.0,
        width_m=1.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=angle_deg,
        z_bottom_m=0.3,
        z_top_m=1.4,
        local_vertices=((2.0, 0.0), (0.5, 0.5), (-2.0, 0.5), (-2.0, -0.5), (0.5, -0.5)),
    )


def _wing_at(te_x):
    # wing trailing edge = offset_x - length/2; pick offset/length to land TE at te_x
    return Part(kind="wing", length_m=1.0, width_m=8.0, offset_x_m=te_x + 0.5,
               offset_y_m=0.0, angle_deg=0.0, z_bottom_m=1.4, z_top_m=1.7)


def _ring_world(part):
    # part-own centred ring shifted to plane-local (angle 0): (offset+vx, offset+vy)
    return Polygon([(part.offset_x_m + x, part.offset_y_m + y) for x, y in part.local_vertices])


def test_clip_produces_front_and_aft_polygons():
    fus = _outline_fuselage()
    parts = _split_fuselage(fus, _wing_at(0.0))  # break at plane-local x=0
    kinds = {p.kind for p in parts}
    assert kinds == {"fuselage_front", "fuselage_aft"}
    for p in parts:
        assert p.local_vertices is not None


def test_clip_is_area_conserving_and_abutting():
    fus = _outline_fuselage()
    front, aft = sorted(_split_fuselage(fus, _wing_at(0.0)),
                        key=lambda p: p.offset_x_m, reverse=True)
    orig = _ring_world(fus).area
    assert math.isclose(_ring_world(front).area + _ring_world(aft).area, orig, rel_tol=1e-9)
    # front is the nose side (greater plane-local x), aft the tail side
    assert front.offset_x_m > aft.offset_x_m


def test_clip_rejects_break_outside_span():
    with pytest.raises(LoaderError, match="strictly inside|span"):
        _split_fuselage(_outline_fuselage(), _wing_at(5.0))  # TE beyond the nose


def test_clip_rejects_rotated_polygon_fuselage():
    with pytest.raises(LoaderError, match="axis-aligned|angle_deg"):
        _split_fuselage(_outline_fuselage(angle_deg=10.0), _wing_at(0.0))


def test_clip_rejects_non_x_monotone_outline():
    # A concave "C" opening toward +x: a vertical cut at x=0 leaves two
    # disconnected nose-side arms -> MultiPolygon -> rejected (the "front is
    # genuinely the cockpit" guard). Simple, non-self-intersecting, fits +/-2 x +/-0.5.
    cshape = Part(
        kind="fuselage_aft", length_m=4.0, width_m=1.0, offset_x_m=0.0, offset_y_m=0.0,
        angle_deg=0.0, z_bottom_m=0.3, z_top_m=1.4,
        local_vertices=((2.0, 0.5), (-2.0, 0.5), (-2.0, -0.5), (2.0, -0.5),
                        (2.0, -0.3), (-1.5, -0.3), (-1.5, 0.3), (2.0, 0.3)),
    )
    with pytest.raises(LoaderError, match="single|x-monotone|piece"):
        _split_fuselage(cshape, _wing_at(0.0))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_loader_fuselage_outline.py -k clip -v`
Expected: FAIL — `_split_fuselage` currently raises `LoaderError("a fuselage part may not carry a polygon footprint")`.

- [ ] **Step 3: Implement the clip**

At the top of `src/hangarfit/loader.py` (with the other imports), add:

```python
from shapely.geometry import Polygon, box

from hangarfit.models import _RING_MIN_ABS_SIGNED_AREA
```

(If `models` is already imported, add `_RING_MIN_ABS_SIGNED_AREA` to that import instead.)

Add a tolerance constant near the other loader constants:

```python
_FUSELAGE_OUTLINE_ANGLE_TOL_DEG = 1e-9
```

In `_split_fuselage`, replace the polygon **rejection** (lines ~1749-1753) with a dispatch to the clip, keeping the scalar path below unchanged:

```python
    x_break = _wing_trailing_edge_x(wing)
    if fuselage.local_vertices is not None:
        return _clip_fuselage_outline(fuselage, x_break)
    # --- scalar box-interval path below is UNCHANGED (byte-identical) ---
    c = fuselage.offset_x_m
    half_len = fuselage.length_m / 2.0
    ...
```

Add the two helpers after `_split_fuselage`:

```python
def _clip_fuselage_outline(fuselage: Part, x_break: float) -> list[Part]:
    """Clip a polygon fuselage outline into front/aft sub-polygons at x_break.

    ``x_break`` is the wing trailing edge in plane-local coords (ADR-0012). The
    outline lives in the part's own centred frame; for an axis-aligned fuselage
    that frame is plane-local shifted by offset_x_m, so the part-own clip station
    is ``xs = x_break - offset_x_m``. The half-plane intersection is
    interpolation-only (deterministic) and area-conserving; each side is
    re-canonicalized by Part.__post_init__.
    """
    if abs(fuselage.angle_deg) > _FUSELAGE_OUTLINE_ANGLE_TOL_DEG:
        raise LoaderError(
            f"a polygon fuselage (vertices:) must be axis-aligned (angle_deg = 0); "
            f"got angle_deg={fuselage.angle_deg:g}"
        )
    c = fuselage.offset_x_m
    xs = x_break - c
    outline = Polygon(fuselage.local_vertices)
    minx, miny, maxx, maxy = outline.bounds
    if not (minx < xs < maxx):
        raise LoaderError(
            f"kind 'fuselage': derived front/aft section break x={x_break:g} "
            f"(wing trailing edge) must lie strictly inside the fuselage outline "
            f"x-span; the break must be strictly inside the span. Check the wing "
            f"offset_x_m / length_m or the fuselage outline."
        )
    pad = (maxx - minx) + (maxy - miny) + 1.0  # any margin past the bbox
    front_geom = outline.intersection(box(xs, miny - pad, maxx + pad, maxy + pad))
    aft_geom = outline.intersection(box(minx - pad, miny - pad, xs, maxy + pad))
    return [
        _subpart_from_clip(fuselage, "fuselage_front", front_geom, "front"),
        _subpart_from_clip(fuselage, "fuselage_aft", aft_geom, "aft"),
    ]


def _subpart_from_clip(fuselage: Part, kind: str, geom: Any, side_label: str) -> Part:
    """Build one fuselage_front/aft Part from a clipped sub-polygon.

    Enforces "exactly one non-degenerate Polygon" — the formal guarantee that the
    front piece is genuinely the cockpit. The sub-polygon comes back in the source
    part's own centred frame; re-express it about its own sub-bbox centre.
    """
    if geom.is_empty or geom.geom_type != "Polygon" or geom.area < _RING_MIN_ABS_SIGNED_AREA:
        raise LoaderError(
            f"kind 'fuselage': the outline does not clip into a single non-degenerate "
            f"{side_label} piece at the wing trailing edge; the outline must be a simple, "
            f"x-monotone polygon spanning the break (got {geom.geom_type}, area={geom.area:g})."
        )
    coords = list(geom.exterior.coords)[:-1]  # drop Shapely's closing duplicate
    xs_ = [x for x, _ in coords]
    ys_ = [y for _, y in coords]
    sub_cx = (min(xs_) + max(xs_)) / 2.0
    sub_cy = (min(ys_) + max(ys_)) / 2.0
    return Part(
        kind=kind,  # type: ignore[arg-type]  # kind is a valid PartKind literal
        length_m=max(xs_) - min(xs_),
        width_m=max(ys_) - min(ys_),
        offset_x_m=fuselage.offset_x_m + sub_cx,
        offset_y_m=fuselage.offset_y_m + sub_cy,
        angle_deg=fuselage.angle_deg,
        z_bottom_m=fuselage.z_bottom_m,
        z_top_m=fuselage.z_top_m,
        local_vertices=tuple((x - sub_cx, y - sub_cy) for x, y in coords),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_loader_fuselage_outline.py -v`
Expected: PASS (all). If `mypy` flags the `kind` literal, keep the `# type: ignore[arg-type]`; verify with `mypy src/hangarfit/loader.py`.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_fuselage_outline.py
git commit -m "feat(550): clip a fuselage outline into front/aft sub-polygons"
```

---

### Task 3: integration build, determinism, byte-identical-fleet guard

**Files:**
- Test: `tests/test_loader_fuselage_outline.py` (extend)

**Interfaces:**
- Consumes: `_build_aircraft(entry: dict) -> Aircraft` (internal; the function that runs the fuselage split — importable from `hangarfit.loader`, already referenced by `tests/test_loader_catalog.py` + the fuzz suite). `load_fleet(path) -> dict[str, Aircraft]` for the fleet guard. **There is no `load_aircraft`** — a single aircraft is built from an entry dict via `_build_aircraft` (the catalog `type:` discriminator is stripped before it). Produces: nothing new; these are built on Tasks 1-2, so they pass on first run (no red phase).

- [ ] **Step 1: Write the tests**

```python
# append to tests/test_loader_fuselage_outline.py
from pathlib import Path
from hangarfit.loader import _build_aircraft, load_fleet


def _outline_entry():
    # Minimal aircraft entry with a tapered fuselage outline + a wing whose
    # trailing edge (offset_x - length/2 = 0.6 - 0.7 = -0.1) lands inside the
    # fuselage x-span [-3, 3]. Required entry keys: id, name, wing_position,
    # gear, movement_mode, parts (add more only if _build_aircraft demands them).
    return {
        "id": "outline_test",
        "name": "Outline Test Plane",
        "wing_position": "high",
        "gear": "nosewheel",
        "movement_mode": "tow_pivotable",
        "parts": [
            {"kind": "wing", "length_m": 1.4, "width_m": 9.0, "offset_x_m": 0.6,
             "z_bottom_m": 1.7, "z_top_m": 2.0},
            {"kind": "fuselage", "length_m": 6.0, "width_m": 1.2, "offset_x_m": 0.0,
             "z_bottom_m": 0.3, "z_top_m": 1.6,
             "vertices": [[3.0, 0.0], [1.0, 0.6], [-3.0, 0.6], [-3.0, -0.6], [1.0, -0.6]]},
        ],
    }


def test_outline_aircraft_builds_polygon_front_aft():
    ac = _build_aircraft(_outline_entry())
    by_kind = {p.kind: p for p in ac.parts}
    assert by_kind["fuselage_front"].local_vertices is not None
    assert by_kind["fuselage_aft"].local_vertices is not None


def test_outline_build_is_deterministic():
    a = _build_aircraft(_outline_entry())
    b = _build_aircraft(_outline_entry())
    fa = next(p for p in a.parts if p.kind == "fuselage_front")
    fb = next(p for p in b.parts if p.kind == "fuselage_front")
    assert fa.local_vertices == fb.local_vertices


def test_no_shipped_fuselage_part_is_a_polygon():
    # byte-identical-fleet guard: every real fleet fuselage stays a scalar box
    # (the scalar split produces fuselage_front/aft with local_vertices is None).
    fleet = load_fleet(Path("data/fleet.yaml"))
    for ac in fleet.values():
        for p in ac.parts:
            if p.kind in ("fuselage_front", "fuselage_aft"):
                assert p.local_vertices is None, f"{ac.id} {p.kind} became a polygon"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_loader_fuselage_outline.py -k "outline_aircraft or deterministic or shipped" -v`
Expected: PASS. If `_build_aircraft` rejects the entry for a missing key (e.g. wheels), mirror the minimal non-geometry fields from `data/catalog/cessna_140.yaml` until it builds. The fleet guard passes immediately (no catalog fuselage uses `vertices:`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_loader_fuselage_outline.py
git commit -m "test(550): integration build + determinism + byte-identical fleet guard"
```

---

### Task 4: downstream behavior (det(−1) transform + collision semantics)

**Files:**
- Create: `tests/fixtures/outline_wing_over_cockpit.yaml` (a layout: `hangar:` ref + placements, **no** `fleet:` — the fleet is supplied as an override)
- Test: `tests/test_loader_fuselage_outline.py` (extend) — proves the already-generic transform + collision pipeline behaves on the clip output. No src change.

**Interfaces:**
- Consumes: `hangarfit.geometry.aircraft_parts_world(aircraft, x_m, y_m, heading_deg)` (cross-check the exact signature in `tests/test_geometry.py`; returns world parts each with `.kind` and `.polygon`). `hangarfit.loader.load_layout(path, *, fleet=<dict>)` (override path — fixture omits `fleet:`). `hangarfit.collisions.check(layout)` → result with `.valid` / `.conflicts` (each conflict has `.kind`). Produces: nothing.

Note: aft-tail nesting and scene `vertices` emission are already covered generically for polygon parts by spike #541's suite (`tests/test_scene.py`, `tests/test_collisions.py`) and behave identically here (same kind-keyed predicate), so they are not re-tested.

- [ ] **Step 1: transform test (no fixture needed)**

```python
# append to tests/test_loader_fuselage_outline.py
from hangarfit.geometry import aircraft_parts_world


def test_clipped_front_flows_through_det_minus_one_transform():
    ac = _build_aircraft(_outline_entry())
    world = aircraft_parts_world(ac, 10.0, 5.0, 37.0)  # non-axis heading
    fronts = [w for w in world if w.kind == "fuselage_front"]
    assert len(fronts) == 1
    assert fronts[0].polygon.is_valid and not fronts[0].polygon.is_empty
```

Run: `pytest tests/test_loader_fuselage_outline.py -k det_minus_one -v` → PASS. (If `aircraft_parts_world` takes keyword args, match `tests/test_geometry.py`.)

- [ ] **Step 2: collision value test — wing over the polygon cockpit**

Create the layout fixture (hangar ref, no `fleet:`):

```yaml
# tests/fixtures/outline_wing_over_cockpit.yaml
hangar: ../../data/hangar.yaml
placements:
  - plane: outline_test
    x_m: 11.0
    y_m: 7.0
    heading_deg: 0.0
    on_carts: false
  - plane: overflyer
    x_m: 11.0
    y_m: 7.0
    heading_deg: 0.0
    on_carts: false
```

Add the test (builds the fleet override via `_build_aircraft` → no catalog/manifest plumbing):

```python
from hangarfit.loader import load_layout
from hangarfit.collisions import check

FIXTURES = Path(__file__).parent / "fixtures"


def _overflyer_entry():
    # A high-winger whose broad wing (z [1.7,2.0], above the fuselage z [0.3,1.6])
    # sits over the outline plane's cockpit (fuselage_front, plane-local x ~[-0.1, 3]).
    return {
        "id": "overflyer", "name": "Overflyer", "wing_position": "high",
        "gear": "nosewheel", "movement_mode": "tow_pivotable",
        "parts": [
            {"kind": "wing", "length_m": 6.0, "width_m": 9.0, "offset_x_m": 1.5,
             "z_bottom_m": 1.7, "z_top_m": 2.0},
            {"kind": "fuselage", "length_m": 6.0, "width_m": 1.2, "offset_x_m": 0.0,
             "z_bottom_m": 0.3, "z_top_m": 1.6},
        ],
    }


def _conflict_kinds(result):
    return {c.kind for c in result.conflicts}


def test_wing_over_polygon_cockpit_conflicts():
    fleet = {"outline_test": _build_aircraft(_outline_entry()),
             "overflyer": _build_aircraft(_overflyer_entry())}
    layout = load_layout(FIXTURES / "outline_wing_over_cockpit.yaml", fleet=fleet)
    result = check(layout)
    assert "fuselage_front_wing_overlap" in _conflict_kinds(result), (
        f"expected a cockpit conflict, got {result.conflicts!r}"
    )
```

Run: `pytest tests/test_loader_fuselage_outline.py -k cockpit -v`
Expected: PASS — the overflyer's wing covers the outline plane's polygon cockpit in plan view at a separated height, which D1 makes a hard `fuselage_front_wing_overlap` regardless of z. The assertion checks *membership* (extra conflicts from the co-located fuselages are fine). If it does not fire, nudge `overflyer.offset_x_m`/the placement so the wing footprint clearly overlaps the front sub-polygon (use the Step-1 transform output to read the front span); keep the wing z above the fuselage z so it is a genuine overhang.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/outline_wing_over_cockpit.yaml tests/test_loader_fuselage_outline.py
git commit -m "test(550): det(-1) transform + cockpit-conflict semantics on clipped outline"
```

---

### Task 5: ADR-0012 amendment, CHANGELOG, docstrings

**Files:**
- Modify: `docs/adr/0012-fuselage-front-aft-split.md` (add a dated Amendment)
- Modify: `CHANGELOG.md` (`[Unreleased] / ### Added`)
- Modify: `src/hangarfit/loader.py` `_split_fuselage` docstring (note the dual path)
- Check: `docs/architecture/08-crosscutting-concepts.md` "the parts model" — add a one-line pointer only if it materially helps; otherwise leave (capability is opt-in, fleet unchanged).

**Interfaces:** docs only.

- [ ] **Step 1: Amend ADR-0012**

Append to `docs/adr/0012-fuselage-front-aft-split.md`:

```markdown
## Amendment (#550, 2026-06-27): polygon fuselage outline → Shapely clip

D2's auto-split now has a second path. When the source `kind: fuselage` part
carries an outline polygon (the raw `vertices:` YAML key, part-own centred
frame), the front/aft split is a **Shapely half-plane clip** at the same
wing-trailing-edge `x_break`, producing two area-conserving **sub-polygons**
(`fuselage_front`/`fuselage_aft`). The scalar box-interval split is unchanged and
remains the path for every current aircraft (byte-identical). D1 (the
`wing × fuselage_front` hard-conflict rule), the conflict-kind taxonomy, and the
"break derived from the wing, not a YAML station" decision are all unchanged. The
clip enforces "exactly one non-degenerate Polygon per side", which is the formal
guarantee that the front sub-outline is genuinely the cockpit. See
`docs/superpowers/specs/2026-06-27-fuselage-outline-polygon-design.md`.
```

- [ ] **Step 2: CHANGELOG entry**

Under `## [Unreleased]` → `### Added` in `CHANGELOG.md`:

```markdown
- A `kind: fuselage` part may now carry a `vertices:` outline polygon, which the
  loader clips into area-conserving `fuselage_front`/`fuselage_aft` sub-polygons
  at the wing trailing edge (#550). Capability-only — no fleet behaviour change.
```

- [ ] **Step 3: Update the `_split_fuselage` docstring**

Note the dual path in the `_split_fuselage` docstring (scalar box-interval vs polygon clip) so the next reader sees both branches.

- [ ] **Step 4: Verify docs + full suite**

Run: `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/ && pytest tests/test_loader_fuselage_outline.py tests/test_collisions.py tests/test_loader.py -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docs/ CHANGELOG.md src/hangarfit/loader.py
git commit -m "docs(550): ADR-0012 amendment + CHANGELOG for fuselage outline clip"
```

---

## Final integration

- [ ] Run the safe full local suite: `make test` (two-pass split; never bare `pytest -n auto`).
- [ ] `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/hangarfit/` — all clean.
- [ ] Push `feature/550-fuselage-outline-polygon`; open a **draft** PR, base `develop`, body `Closes #550`.
- [ ] Review arc: `pr-review-toolkit:code-reviewer` (main) + `geometry-invariant-guard` (collision/transform interaction) + `silent-failure-hunter` (loader) + `comment-analyzer` (ADR/docstrings). Thread-per-finding, fix + resolve.
- [ ] Flip out of draft when the review arc is clean; the user merges.
