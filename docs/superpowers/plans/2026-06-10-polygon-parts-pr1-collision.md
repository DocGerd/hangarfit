# Polygon Parts — PR1 (model & collision) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional polygon footprint to `Part` (load-time-canonicalized for determinism) plus the collision build-path and a parametrized `planform:` loader schema — keeping every shipped fleet byte-identical, with the viewer untouched.

**Architecture:** A new trailing `Part.local_vertices` field (part-own frame, `+x`=length axis, `+y`=width axis) is canonicalized in `__post_init__` (force-CCW, lex-min start, drop closing dup, reject degenerate/non-finite/self-intersecting) and constrained to its `length_m × width_m` bbox. `geometry.aircraft_parts_world` grows a branch that routes those vertices through the same det(−1) `local_to_world` as the scalar `oriented_rect` path. `loader._build_part` grows a `planform: {root_chord_m, tip_chord_m}` block (mirroring `struts:`) that expands into the canonical hexagon. No real fleet data is authored as a polygon in this PR — the determinism contract is exercised by a new test-only taper scenario.

**Tech Stack:** Python 3.12, `dataclasses` (frozen+slots), `shapely` (`LinearRing`/`Polygon`), `pytest`. Lint/format `ruff`, types `mypy`.

**Scope note:** This is PR1 of a 3-PR stack (design: `docs/superpowers/specs/2026-06-10-realistic-polygon-plane-geometry-design.md`). PR2 (#549 viewer scene/v2) and PR3 (glider taper data + flip regression) get their own plans. Closes the re-scoped #548.

**Conventions for every task:**
- Run tests with bare `pytest` (the editable install resolves `src/`). The `.claude` PostToolUse hook auto-runs `ruff` + `pytest` after edits under `src/hangarfit/` or `tests/`.
- Commit messages use Conventional Commits and end with the trailer
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Work on the existing branch `feature/polygon-parts-realistic-geometry`.

---

### Task 1: `_canonicalize_ring` — the determinism crux (lands first)

**Files:**
- Modify: `src/hangarfit/models.py` (imports near line 13–23; add helper + constants after the `_VALID_*` block ~line 39)
- Test: `tests/test_part_polygon.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_part_polygon.py`:

```python
"""Polygon-part canonicalization + Part.local_vertices tests (issue #548).

The canonicalization invariant is the determinism crux (ADR-0003): two
orderings of the same ring MUST produce a byte-identical canonical tuple.
"""

from __future__ import annotations

import math

import pytest

from hangarfit.models import Part, _canonicalize_ring


# A simple CCW unit square, vertices listed from a non-lex-min start.
_SQUARE_CCW = [(1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
# Same square wound CW.
_SQUARE_CW = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)]


def test_canonicalize_forces_ccw_and_lexmin_start() -> None:
    canon = _canonicalize_ring(_SQUARE_CCW)
    # Lex-min vertex (0,0) is first.
    assert canon[0] == (0.0, 0.0)
    # Wound CCW: signed area positive.
    n = len(canon)
    area2 = sum(
        canon[i][0] * canon[(i + 1) % n][1] - canon[(i + 1) % n][0] * canon[i][1]
        for i in range(n)
    )
    assert area2 > 0


def test_canonicalize_equivalent_orderings_are_identical() -> None:
    """CCW, CW, and rotated inputs of the same shape canonicalize identically."""
    rotated = _SQUARE_CCW[2:] + _SQUARE_CCW[:2]
    assert _canonicalize_ring(_SQUARE_CCW) == _canonicalize_ring(_SQUARE_CW)
    assert _canonicalize_ring(_SQUARE_CCW) == _canonicalize_ring(rotated)


def test_canonicalize_drops_closing_duplicate() -> None:
    closed = [*_SQUARE_CCW, _SQUARE_CCW[0]]
    assert _canonicalize_ring(closed) == _canonicalize_ring(_SQUARE_CCW)


def test_canonicalize_rejects_too_few_vertices() -> None:
    with pytest.raises(ValueError, match="3"):
        _canonicalize_ring([(0.0, 0.0), (1.0, 1.0)])


def test_canonicalize_rejects_non_finite() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        _canonicalize_ring([(0.0, 0.0), (1.0, 0.0), (math.inf, 1.0)])


def test_canonicalize_rejects_degenerate_collinear() -> None:
    with pytest.raises(ValueError, match="degenerate"):
        _canonicalize_ring([(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)])


def test_canonicalize_rejects_self_intersecting_bowtie() -> None:
    with pytest.raises(ValueError, match="self-intersect"):
        _canonicalize_ring([(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_part_polygon.py -q`
Expected: FAIL — `ImportError: cannot import name '_canonicalize_ring'`.

- [ ] **Step 3: Implement the helper**

In `src/hangarfit/models.py`, extend the shapely import (line 22) and add `Sequence`:

```python
from collections.abc import Mapping, Sequence
```
```python
from shapely import box, union_all
from shapely.geometry import LinearRing
from shapely.geometry.base import BaseGeometry
```

After the `_VALID_MOVEMENT_MODES = ...` line (~39) add:

```python
# Below this absolute |2·signed-area| a ring is treated as degenerate
# (zero-area / collinear). 1e-9 m² is far tighter than any real part.
_RING_MIN_ABS_SIGNED_AREA = 1e-9
# Float slack for the local_vertices-within-bbox containment check.
_PART_BBOX_TOL_M = 1e-9


def _canonicalize_ring(
    vertices: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    """Canonicalize an author-supplied polygon ring to a deterministic form.

    Returns the ring OPEN (no closing duplicate), wound counter-clockwise,
    rotated so the lexicographically-smallest vertex is first. Two orderings
    of the SAME shape therefore produce a byte-identical tuple — the
    determinism contract (ADR-0003) for polygon parts, since the geometry
    layer never re-orients at solve time (Shapely preserves vertex order
    verbatim). Rejects non-finite, fewer-than-3-vertex, degenerate
    (zero-area / collinear), and self-intersecting rings.
    """
    pts = [(float(x), float(y)) for x, y in vertices]
    # Drop an explicit closing duplicate if the author supplied one.
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError(f"polygon ring needs >= 3 distinct vertices, got {len(pts)}")
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in pts):
        raise ValueError(f"polygon ring has a non-finite vertex: {pts}")
    # Signed area (shoelace) gives BOTH the winding sign and a degeneracy check.
    n = len(pts)
    signed_area2 = sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)
    )
    if abs(signed_area2) < _RING_MIN_ABS_SIGNED_AREA:
        raise ValueError(f"polygon ring is degenerate (near-zero area): {pts}")
    # Reject self-intersection (bow-ties) — shapely is the well-tested oracle.
    if not LinearRing([*pts, pts[0]]).is_simple:
        raise ValueError(f"polygon ring self-intersects: {pts}")
    # Force counter-clockwise (positive signed area).
    if signed_area2 < 0:
        pts = list(reversed(pts))
    # Rotate to the lexicographically-minimum start vertex.
    start = min(range(len(pts)), key=lambda i: pts[i])
    pts = pts[start:] + pts[:start]
    return tuple(pts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_part_polygon.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/models.py tests/test_part_polygon.py
git commit -m "feat(models): add _canonicalize_ring polygon canonicalization helper (#548)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `Part.local_vertices` field + canonicalization + bbox-subset invariant

**Files:**
- Modify: `src/hangarfit/models.py` (`Part` dataclass ~line 42–95)
- Test: `tests/test_part_polygon.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_part_polygon.py`:

```python
def _wing(**kw):
    base = dict(
        kind="wing",
        length_m=2.0,
        width_m=10.0,
        offset_x_m=0.0,
        offset_y_m=0.0,
        angle_deg=0.0,
        z_bottom_m=1.9,
        z_top_m=2.1,
    )
    base.update(kw)
    return Part(**base)


def test_part_defaults_local_vertices_none() -> None:
    assert _wing().local_vertices is None


def test_part_canonicalizes_local_vertices_on_construction() -> None:
    # A hexagon taper, listed CW from a non-lex-min start; must come back canonical.
    verts = [(1.0, 0.0), (0.4, -5.0), (-0.4, -5.0), (-1.0, 0.0), (-0.4, 5.0), (0.4, 5.0)]
    p = _wing(local_vertices=verts)
    assert p.local_vertices == _canonicalize_ring(verts)
    # Canonical: lex-min start, CCW.
    assert p.local_vertices[0] == min(p.local_vertices)


def test_part_rejects_local_vertices_outside_bbox() -> None:
    # Vertex x=1.5 exceeds half-length (length_m/2 = 1.0).
    verts = [(1.5, 0.0), (0.4, 5.0), (-0.4, 5.0), (-1.0, 0.0), (-0.4, -5.0), (0.4, -5.0)]
    with pytest.raises(ValueError, match="bbox"):
        _wing(local_vertices=verts)


def test_part_local_vertices_within_bbox_ok() -> None:
    # Tip/root within the 2.0 x 10.0 box; touching the boundary is allowed.
    verts = [(1.0, 0.0), (0.4, 5.0), (-0.4, 5.0), (-1.0, 0.0), (-0.4, -5.0), (0.4, -5.0)]
    p = _wing(local_vertices=verts)
    assert len(p.local_vertices) == 6
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_part_polygon.py -q`
Expected: FAIL — `TypeError: Part.__init__() got an unexpected keyword argument 'local_vertices'`.

- [ ] **Step 3: Implement the field + validation**

In `src/hangarfit/models.py`, add the field to `Part` after `z_top_m` (line 76):

```python
    z_top_m: float
    local_vertices: tuple[tuple[float, float], ...] | None = None
```

At the END of `Part.__post_init__` (after the existing `z_top_m` check ~line 95) add:

```python
        if self.local_vertices is not None:
            canonical = _canonicalize_ring(self.local_vertices)
            half_l = self.length_m / 2.0
            half_w = self.width_m / 2.0
            for x, y in canonical:
                if abs(x) > half_l + _PART_BBOX_TOL_M or abs(y) > half_w + _PART_BBOX_TOL_M:
                    raise ValueError(
                        f"Part {self.kind!r}: local_vertices vertex ({x}, {y}) lies outside "
                        f"the length_m x width_m bbox (+/-{half_l} x +/-{half_w}); the polygon "
                        f"footprint must be a subset of the bounding box"
                    )
            object.__setattr__(self, "local_vertices", canonical)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_part_polygon.py -q`
Expected: PASS (11 passed).

- [ ] **Step 5: Run the full model + loader suites to confirm no back-compat break**

Run: `pytest tests/test_models.py tests/test_loader.py -q`
Expected: PASS (the trailing defaulted field leaves all keyword construction valid).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/models.py tests/test_part_polygon.py
git commit -m "feat(models): optional Part.local_vertices with canonicalization + bbox invariant (#548)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `aircraft_parts_world` polygon build-path

**Files:**
- Modify: `src/hangarfit/geometry.py` (`aircraft_parts_world` loop ~line 198–216)
- Test: `tests/test_geometry.py` (append a class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_geometry.py` (the helpers `_aircraft_with_one_part`, `Placement`, `aircraft_parts_world` are already imported/defined in this file):

```python
class TestAircraftPartsWorldPolygon:
    def test_rectangle_vertices_match_scalar_path_at_45deg(self) -> None:
        """A part whose local_vertices ARE its rectangle corners must transform
        to the SAME world polygon as the scalar oriented_rect path — proving the
        polygon branch routes every vertex through the det(-1) transform."""
        hl, hw = 1.0, 5.0  # length_m=2, width_m=10
        rect_corners = [(hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)]
        scalar = _aircraft_with_one_part(
            Part(kind="wing", length_m=2.0, width_m=10.0, offset_x_m=0.3,
                 offset_y_m=-0.2, angle_deg=0.0, z_bottom_m=1.9, z_top_m=2.1)
        )
        poly = _aircraft_with_one_part(
            Part(kind="wing", length_m=2.0, width_m=10.0, offset_x_m=0.3,
                 offset_y_m=-0.2, angle_deg=0.0, z_bottom_m=1.9, z_top_m=2.1,
                 local_vertices=rect_corners)
        )
        pl = Placement(plane_id="probe", x_m=4.0, y_m=7.0, heading_deg=45.0, on_carts=False)
        [ws] = aircraft_parts_world(scalar, pl)
        [wp] = aircraft_parts_world(poly, pl)
        assert wp.polygon.equals(ws.polygon)

    def test_taper_polygon_is_strict_subset_area_of_bbox(self) -> None:
        """A tapered hexagon transforms to a world polygon with LESS area than
        the bounding rectangle (the conservative footprint direction)."""
        taper = [(1.0, 0.0), (0.4, 5.0), (-0.4, 5.0), (-1.0, 0.0), (-0.4, -5.0), (0.4, -5.0)]
        scalar = _aircraft_with_one_part(
            Part(kind="wing", length_m=2.0, width_m=10.0, offset_x_m=0.0,
                 offset_y_m=0.0, angle_deg=0.0, z_bottom_m=1.9, z_top_m=2.1)
        )
        poly = _aircraft_with_one_part(
            Part(kind="wing", length_m=2.0, width_m=10.0, offset_x_m=0.0,
                 offset_y_m=0.0, angle_deg=0.0, z_bottom_m=1.9, z_top_m=2.1,
                 local_vertices=taper)
        )
        pl = Placement(plane_id="probe", x_m=0.0, y_m=0.0, heading_deg=45.0, on_carts=False)
        [ws] = aircraft_parts_world(scalar, pl)
        [wp] = aircraft_parts_world(poly, pl)
        assert wp.polygon.area < ws.polygon.area
        assert wp.polygon.within(ws.polygon.buffer(1e-9))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_geometry.py::TestAircraftPartsWorldPolygon -q`
Expected: FAIL — the polygon and scalar polygons differ (the branch is not yet implemented, so `local_vertices` is ignored and both build the rectangle... the first test PASSES trivially but the second FAILS because `wp.area == ws.area`). Confirm at least `test_taper_polygon_is_strict_subset_area_of_bbox` fails with `assert <equal areas>`.

- [ ] **Step 3: Implement the branch**

In `src/hangarfit/geometry.py`, replace the body of the `for part in aircraft.parts:` loop (the `oriented_rect` + `world_coords` construction, lines ~200–216) with:

```python
    for part in aircraft.parts:
        if part.local_vertices is not None:
            # Polygon footprint: rotate each author vertex from the part's own
            # frame into plane-local by the part's angle+offset (mirroring
            # oriented_rect's per-corner affine), then route EVERY vertex through
            # the det(-1) local_to_world transform — no centroid shortcut (ADR-0002).
            h = math.radians(part.angle_deg)
            cos_h = math.cos(h)
            sin_h = math.sin(h)
            cx = part.offset_x_m
            cy = part.offset_y_m
            local_coords = [
                (cx + x * cos_h - y * sin_h, cy + x * sin_h + y * cos_h)
                for x, y in part.local_vertices
            ]
        else:
            # Scalar oriented rectangle (back-compat path). ``angle_deg`` rotates
            # the part within plane-local (standard CCW).
            local_poly = oriented_rect(
                cx=part.offset_x_m,
                cy=part.offset_y_m,
                length=part.length_m,
                width=part.width_m,
                angle_deg=part.angle_deg,
            )
            # exterior.coords includes the closing-point duplicate; slice it off.
            local_coords = list(local_poly.exterior.coords)[:-1]
        # Apply the plane-local-to-world transform to each vertex.
        world_coords = [local_to_world(u, v, placement) for u, v in local_coords]
        world_poly = Polygon(world_coords)
        world_parts.append(
            WorldPart(
                polygon=world_poly,
                z_bottom_m=part.z_bottom_m,
                z_top_m=part.z_top_m,
                plane_id=placement.plane_id,
                kind=part.kind,
            )
        )
    return world_parts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_geometry.py -q`
Expected: PASS (existing geometry tests + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/geometry.py tests/test_geometry.py
git commit -m "feat(geometry): polygon build-path in aircraft_parts_world (#548)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: loader `planform:` schema + part-key allowlist

**Files:**
- Modify: `src/hangarfit/loader.py` (`_build_part` ~line 1041; add `_ALLOWED_PART_KEYS` + `_build_planform`)
- Test: `tests/test_loader_planform.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_loader_planform.py`:

```python
"""Loader tests for the parametrized `planform:` wing block (#548)."""

from __future__ import annotations

import pytest

from hangarfit.loader import LoaderError, _build_part


def _wing_data(**planform):
    return {
        "kind": "wing",
        "length_m": 2.0,
        "width_m": 10.0,
        "offset_x_m": 1.5,
        "z_bottom_m": 1.9,
        "z_top_m": 2.1,
        "planform": {"root_chord_m": 2.0, "tip_chord_m": 0.9, **planform},
    }


def test_planform_expands_to_canonical_hexagon() -> None:
    part = _build_part(_wing_data(), 0)
    assert part.local_vertices is not None
    assert len(part.local_vertices) == 6
    # Within the 2.0 x 10.0 bbox.
    for x, y in part.local_vertices:
        assert abs(x) <= 1.0 + 1e-9
        assert abs(y) <= 5.0 + 1e-9


def test_planform_absent_leaves_local_vertices_none() -> None:
    data = {"kind": "wing", "length_m": 2.0, "width_m": 10.0,
            "z_bottom_m": 1.9, "z_top_m": 2.1}
    assert _build_part(data, 0).local_vertices is None


def test_planform_rejects_tip_exceeding_root() -> None:
    with pytest.raises(LoaderError, match="taper outward"):
        _build_part(_wing_data(root_chord_m=1.0, tip_chord_m=1.5), 0)


def test_planform_rejects_root_exceeding_length_bbox() -> None:
    # root_chord 3.0 > length_m 2.0 -> a vertex pokes outside the bbox.
    with pytest.raises((LoaderError, ValueError), match="bbox"):
        _build_part(_wing_data(root_chord_m=3.0, tip_chord_m=0.9), 0)


def test_planform_rejects_unknown_nested_key() -> None:
    with pytest.raises(LoaderError, match="unknown key"):
        _build_part(_wing_data(sweep_deg=3.0), 0)


def test_build_part_rejects_unknown_part_key() -> None:
    data = {"kind": "wing", "length_m": 2.0, "width_m": 10.0,
            "z_bottom_m": 1.9, "z_top_m": 2.1, "planfrm": {}}
    with pytest.raises(LoaderError, match="unknown key"):
        _build_part(data, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_loader_planform.py -q`
Expected: FAIL — `_build_part` ignores `planform` (so `local_vertices is None`), and there is no part-key allowlist yet.

- [ ] **Step 3: Implement the schema**

In `src/hangarfit/loader.py`, immediately before `def _build_part` (~line 1041) add:

```python
# Strict unknown-key allowlist for a `parts[i]` entry, mirroring
# _ALLOWED_AIRCRAFT_KEYS. `planform` is a YAML-only convenience block expanded
# into Part.local_vertices by _build_planform (NOT a Part field). Without this,
# a typo like `planfrm:` would be silently dropped and the wing would stay a
# rectangle with no error.
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
    }
)


def _build_planform(data: Any, span_m: float, index: int) -> tuple[tuple[float, float], ...]:
    """Expand a parametrized symmetric double-taper wing into part-own vertices.

    Convention (ADR-0024): no sweep, root kink at y=0. In the part's own frame
    ``+x`` is the chord (forward = leading edge), and ``width_m`` is the span
    running along ``+-y``. Produces a 6-vertex hexagon; the chord at the root
    (y=0) is ``root_chord_m`` and at each tip (y=+-span/2) is ``tip_chord_m``.
    Part.__post_init__ canonicalizes the ring and enforces the bbox subset.
    """
    if not isinstance(data, dict):
        raise LoaderError(f"parts[{index}].planform must be a mapping")
    required = ("root_chord_m", "tip_chord_m")
    for key in required:
        if key not in data:
            raise LoaderError(f"parts[{index}].planform missing required field {key!r}")
    unknown = set(data) - set(required)
    if unknown:
        raise LoaderError(f"parts[{index}].planform has unknown key(s) {sorted(unknown)}")
    root = _to_float(data["root_chord_m"], f"parts[{index}].planform.root_chord_m")
    tip = _to_float(data["tip_chord_m"], f"parts[{index}].planform.tip_chord_m")
    if root <= 0 or tip <= 0:
        raise LoaderError(
            f"parts[{index}].planform chords must be positive, got root={root}, tip={tip}"
        )
    if tip > root:
        raise LoaderError(
            f"parts[{index}].planform tip_chord_m ({tip}) must not exceed root_chord_m "
            f"({root}) — a wing does not taper outward"
        )
    half_span = span_m / 2.0
    hr = root / 2.0
    ht = tip / 2.0
    return (
        (hr, 0.0),
        (ht, half_span),
        (-ht, half_span),
        (-hr, 0.0),
        (-ht, -half_span),
        (ht, -half_span),
    )
```

Replace the body of `_build_part` (lines ~1041–1057) with:

```python
def _build_part(data: Any, index: int) -> Part:
    if not isinstance(data, dict):
        raise LoaderError(f"parts[{index}] must be a mapping")
    unknown = set(data) - _ALLOWED_PART_KEYS
    if unknown:
        raise LoaderError(
            f"parts[{index}] has unknown key(s) {sorted(unknown)}; "
            f"allowed: {sorted(_ALLOWED_PART_KEYS)}"
        )
    required = ("kind", "length_m", "width_m", "z_bottom_m", "z_top_m")
    for key in required:
        if key not in data:
            raise LoaderError(f"parts[{index}] missing required field {key!r}")
    width_m = _to_float(data["width_m"], f"parts[{index}].width_m")
    local_vertices = None
    if "planform" in data:
        local_vertices = _build_planform(data["planform"], width_m, index)
    return Part(
        kind=data["kind"],
        length_m=_to_float(data["length_m"], f"parts[{index}].length_m"),
        width_m=width_m,
        offset_x_m=_to_float(data.get("offset_x_m", 0.0), f"parts[{index}].offset_x_m"),
        offset_y_m=_to_float(data.get("offset_y_m", 0.0), f"parts[{index}].offset_y_m"),
        angle_deg=_to_float(data.get("angle_deg", 0.0), f"parts[{index}].angle_deg"),
        z_bottom_m=_to_float(data["z_bottom_m"], f"parts[{index}].z_bottom_m"),
        z_top_m=_to_float(data["z_top_m"], f"parts[{index}].z_top_m"),
        local_vertices=local_vertices,
    )
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/test_loader_planform.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the FULL loader + fleet suites (the part-key allowlist is a behavior change)**

Run: `pytest tests/test_loader.py tests/test_scenario.py tests/test_herrenteich_dataset.py -q`
Expected: PASS. If any shipped fixture/data carries a part key outside `_ALLOWED_PART_KEYS`, this surfaces it — add the missing key to the allowlist (do NOT silently widen past the real schema).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_planform.py
git commit -m "feat(loader): parametrized planform: wing block + part-key allowlist (#548)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: determinism scenario fixtures + canary

**Files:**
- Create: `tests/fixtures/hangar_taper.yaml`, `tests/fixtures/fleet_taper.yaml`, `tests/fixtures/solve_taper_determinism.yaml`
- Test: `tests/test_solver_canaries.py` (append one function)

- [ ] **Step 1: Create the hangar fixture**

`tests/fixtures/hangar_taper.yaml`:

```yaml
# Roomy test hangar for the polygon-taper determinism canary (#548). Large
# enough for one 18 m-span tapered glider. Synthetic placeholder dimensions.
length_m: 25.0
width_m: 22.0
door:
  center_x_m: 11.0
  width_m: 12.0
clearance_m: 0.3
wing_layer_clearance_m: 0.2
```

- [ ] **Step 2: Create the test-only fleet fixture (with a polygon wing)**

`tests/fixtures/fleet_taper.yaml`:

```yaml
# TEST-ONLY fleet for the polygon-parts determinism canary (#548). One tapered
# glider authored via a `planform:` block so the polygon build-path + load-time
# canonicalization are exercised through solve(). NOT shipped data.
aircraft:
  - id: taper_glider
    name: "Taper Glider (test)"
    wing_position: high
    gear: monowheel
    movement_mode: always_cart
    turn_radius_m: null
    measured: false
    parts:
      - kind: fuselage
        length_m: 7.6
        width_m: 0.7
        offset_x_m: 0.0
        offset_y_m: 0.0
        z_bottom_m: 0.0
        z_top_m: 1.5
      - kind: wing
        length_m: 1.2
        width_m: 18.0
        offset_x_m: 1.5
        offset_y_m: 0.0
        z_bottom_m: 1.9
        z_top_m: 2.1
        planform:
          root_chord_m: 1.2
          tip_chord_m: 0.54
      - kind: tail
        length_m: 0.9
        width_m: 2.6
        offset_x_m: -3.35
        offset_y_m: 0.0
        z_bottom_m: 1.2
        z_top_m: 1.5
      - kind: vertical_stabilizer
        length_m: 1.1
        width_m: 0.15
        offset_x_m: -3.25
        offset_y_m: 0.0
        z_bottom_m: 1.5
        z_top_m: 1.68
    wheels:
      main_offset_x_m: 0.0
```

- [ ] **Step 3: Create the scenario fixture**

`tests/fixtures/solve_taper_determinism.yaml`:

```yaml
# Determinism canary scenario (#548): place the tapered glider so the polygon
# build-path runs inside solve()'s seeded restart loop.
fleet: fleet_taper.yaml
hangar: hangar_taper.yaml
fleet_in: [taper_glider]
```

- [ ] **Step 4: Append the canary test**

Append to `tests/test_solver_canaries.py`:

```python
def test_solve_deterministic_polygon_taper_fleet() -> None:
    """A fleet whose wing is a polygon (tapered hexagon) solves bit-identically
    across two runs under a fixed max_restarts cap — proving the polygon
    build-path + load-time vertex canonicalization are determinism-safe
    (ADR-0003). max_restarts (not wall-clock) makes this load-independent, so
    it stays in the parallel pool (no `serial` mark)."""
    fixture = "tests/fixtures/solve_taper_determinism.yaml"
    cfg = SearchConfig(max_restarts=8, spread=False, nose_out=False)

    s1 = load_scenario(fixture)
    r1 = solve(s1, budget_s=30.0, alternatives=1, seed=42, search=cfg)
    s2 = load_scenario(fixture)
    r2 = solve(s2, budget_s=30.0, alternatives=1, seed=42, search=cfg)

    assert r1.status == r2.status
    assert len(r1.layouts) == len(r2.layouts)
    for la, lb in zip(r1.layouts, r2.layouts, strict=True):
        assert la.placements == lb.placements
        assert la.maintenance_plane == lb.maintenance_plane

    bp1 = r1.diagnostics.best_partial_layout
    bp2 = r2.diagnostics.best_partial_layout
    if bp1 is not None:
        assert bp2 is not None
        assert bp1.placements == bp2.placements
```

- [ ] **Step 5: Run the canary**

Run: `pytest tests/test_solver_canaries.py::test_solve_deterministic_polygon_taper_fleet -q`
Expected: PASS. (If `status` is unexpectedly `exhausted_budget` for a single roomy-hangar plane, the byte-identity assertions still hold — the test is direction-agnostic. If the scenario fails to load, re-check the relative `fleet:`/`hangar:` paths resolve from `tests/fixtures/`.)

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/hangar_taper.yaml tests/fixtures/fleet_taper.yaml \
        tests/fixtures/solve_taper_determinism.yaml tests/test_solver_canaries.py
git commit -m "test(solver): polygon-taper determinism canary fixture + double-solve (#548)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: opt-in byte-identity guard (shipped fleets stay rectangles)

**Files:**
- Test: `tests/test_fleet_polygon_optin.py` (create)

- [ ] **Step 1: Write the test**

Create `tests/test_fleet_polygon_optin.py`:

```python
"""PR1 guarantee: the polygon-parts feature ships PLUMBING only. No shipped
fleet aircraft is authored as a polygon yet, so every shipped Part keeps
local_vertices=None and the real fleets stay byte-identical. The Scheibe taper
lands in a later PR once the viewer renders N-gon (#548 stack)."""

from __future__ import annotations

import pytest

from hangarfit.loader import load_fleet

_SHIPPED_FLEETS = ["data/fleet.yaml", "examples/herrenteich/fleet.yaml"]


@pytest.mark.parametrize("path", _SHIPPED_FLEETS)
def test_shipped_fleet_has_no_polygon_parts(path: str) -> None:
    fleet = load_fleet(path)
    for ac in fleet.values():
        for part in ac.parts:
            assert part.local_vertices is None, (
                f"{path}:{ac.id} part {part.kind!r} unexpectedly carries a polygon "
                f"footprint; PR1 must keep shipped fleets byte-identical"
            )
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_fleet_polygon_optin.py -q`
Expected: PASS (2 passed).

- [ ] **Step 3: Commit**

```bash
git add tests/test_fleet_polygon_optin.py
git commit -m "test(fleet): guard that shipped fleets stay rectangles in PR1 (#548)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: docs — ADR-0024 + arc42 note + CHANGELOG

**Files:**
- Create: `docs/adr/0024-optional-polygon-parts.md`
- Modify: `docs/adr/README.md` (ADR index), `docs/architecture/08-crosscutting-concepts.md` ("The parts model"), `CHANGELOG.md`

- [ ] **Step 1: Write ADR-0024**

Create `docs/adr/0024-optional-polygon-parts.md` (follow `docs/adr/template.md`'s structure). Required content:
- **Status:** Accepted. **Refines:** ADR-0001 (mesh deferral).
- **Context:** every `Part` is an oriented rectangle (ADR-0001); ADR-0012/0023 added realism only by adding rectangles. The spike (#541) measured a 0.10–0.30 m verdict-flip window where a tapered glider wingtip nests but its bounding rectangle falsely conflicts.
- **Decision:** add an optional `Part.local_vertices` (part-own frame), load-time-canonicalized (force-CCW, lex-min start, drop closing dup, reject degenerate/non-finite/self-intersecting — the ADR-0003 determinism crux), constrained to the `length_m × width_m` bbox. `aircraft_parts_world` routes every vertex through the det(−1) `local_to_world` (ADR-0002). Author via a parametrized `planform: {root_chord_m, tip_chord_m}` block (symmetric double-taper, no sweep, root kink at y=0 → a hexagon). No raw `vertices:` primitive (no honest data source; spike Q2).
- **The bbox / area trade:** `length_m × width_m` stays the bounding box for all scalar consumers; the polygon is a strict subset, so wing **area is intentionally under-conserved** (the conservative footprint direction — the box over-claims). The area-gate reads scalars, so it stays a sound lower bound.
- **Consequences:** scalar fleets byte-identical; the viewer continues to render boxes until #549 (scene/v2); the folded Stemme wing stays a rectangle (folding ≠ taper).
- **Forward-compat:** the vertex-routed transform + explicit per-part height band leave room for a future wing-tilt DoF and true-3D meshes (both deferred).

- [ ] **Step 2: Add the ADR to the index**

In `docs/adr/README.md`, add a row/line for ADR-0024 in the same format as the surrounding entries (link + one-line summary, marked as refining ADR-0001).

- [ ] **Step 3: Note polygon parts in arc42 §8**

In `docs/architecture/08-crosscutting-concepts.md`, in "The parts model" section, add a short paragraph: parts are oriented rectangles by default, but a `Part` may carry an optional canonicalized polygon footprint (`local_vertices`, authored via `planform:`) that the collision build-path uses while `length_m`/`width_m` remain the bounding box; link to ADR-0024.

- [ ] **Step 4: Add the CHANGELOG entry**

In `CHANGELOG.md`, under `## [Unreleased]` → `### Added`, add:

```markdown
- Optional polygon part footprints: a `Part` may carry a load-time-canonicalized
  `local_vertices` polygon (authored via a parametrized `planform: {root_chord_m,
  tip_chord_m}` wing block), used by the collision build-path while `length_m`/
  `width_m` stay the bounding box. Scalar fleets are byte-identical; the 3D viewer
  still renders boxes until the scene/v2 work. (#548, ADR-0024)
```

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0024-optional-polygon-parts.md docs/adr/README.md \
        docs/architecture/08-crosscutting-concepts.md CHANGELOG.md
git commit -m "docs(adr): ADR-0024 optional polygon parts + arc42/CHANGELOG (#548)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Full verification + guard arc + draft PR

**Files:** none (process)

- [ ] **Step 1: Full local verification**

Run, expecting all green:
```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/hangarfit/
pytest -q
```
If `ruff format --check` fails, run `ruff format src/ tests/` and re-commit.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin feature/polygon-parts-realistic-geometry
```

- [ ] **Step 3: Open the draft PR (re-scoped #548)**

```bash
gh pr create --draft --base develop \
  --title "feat: optional polygon part footprints + collision build-path (#548)" \
  --body "$(cat <<'BODY'
Closes #548 (re-scoped: glider taper data carved into a follow-up issue, see the stack design).

PR1 of the polygon-parts stack (design: docs/superpowers/specs/2026-06-10-realistic-polygon-plane-geometry-design.md).
Adds optional `Part.local_vertices` (load-time-canonicalized, bbox-subset), the
`aircraft_parts_world` polygon build-path, and a parametrized `planform:` loader
schema. Shipped fleets stay byte-identical (guarded); the viewer is untouched
(renders boxes until #549). Determinism is exercised by a new placed-taper canary.
BODY
)"
```
Then set assignee/labels/milestone via `gh api -X PATCH` (the repo convention — `gh pr edit` is broken here).

- [ ] **Step 4: Run the guard arc** (per CLAUDE.md — convert every finding into a review thread on the diff):
  - `geometry-invariant-guard` (geometry.py touched — confirms every vertex routes through `local_to_world`, no centroid shortcut, non-axis-aligned heading covered)
  - `type-design-analyzer` (models.py `Part` changed)
  - `pr-review-toolkit:silent-failure-hunter` (loader changed)
  - `determinism-guard` (runs the solver twice on the taper scenario, diffs byte-for-byte)
  - `pr-review-toolkit:code-reviewer` (mandated main pass)
  - `pr-review-toolkit:comment-analyzer` (ADR + docstrings)

- [ ] **Step 5:** Resolve every thread (fix-in-code preferred). Re-run review if changes were non-trivial. When the arc is clean, `gh pr ready <n>` and tell the user it's clean and ready for final review. **Do not merge — the user is the sole merger.**

---

## Self-Review (completed)

**Spec coverage:** §3.1 `local_vertices`→T2; §3.2 canonicalization→T1; §3.3 bbox invariant→T2/T4; §3.4 build-path→T3; §3.5 `planform:`/no-`vertices:`→T4; §3.6 determinism scenario→T5; byte-identity→T6; ADR/CHANGELOG (§8)→T7; guard arc (§8)→T8. PR2 (§4) and PR3 (§5) are intentionally out of this plan (separate stack PRs).

**Type consistency:** `local_vertices: tuple[tuple[float, float], ...] | None` is used identically in models.py (field), the `_build_planform` return, and the geometry branch. `_canonicalize_ring` returns the same type. `load_fleet(path) -> dict[str, Aircraft]` (iterated via `.values()`). `SearchConfig(max_restarts=, spread=, nose_out=)`, `solve(scenario, budget_s=, alternatives=, seed=, search=)`, and `Placement(plane_id=, x_m=, y_m=, heading_deg=, on_carts=)` all match the existing call sites verified in `test_solver_canaries.py` / `test_geometry.py`.

**Placeholder scan:** no TBD/TODO; every code step shows complete code. ADR-0024 (T7) is described by required-content bullets rather than full prose — acceptable for a doc artifact that follows the in-repo `template.md`.
