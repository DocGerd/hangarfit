# Wheels-canonical-data Implementation Plan (#322)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make wheel positions first-class per-aircraft data in `fleet.yaml` with a compact β-schema, a loose loader cross-check against `turn_radius_m`, and a single accessor that visualize.py consumes — closing the docs-vs-data inconsistency that motivates #322.

**Architecture:** New `Wheels` dataclass in `models.py`; `Aircraft.wheels` becomes a required field. Loader parses a required `wheels:` block per aircraft and runs a 0.5×–5× wheelbase plausibility check against `turn_radius_m` (skipped for `always_cart` and `monowheel`). visualize.py's `_NOSE_GEAR_FRAC` / `_MAIN_GEAR_*_FRAC` heuristic constants get deleted; wheel-glyph drawing reduces to a loop over `aircraft.wheels.positions`. A new ADR-0013 records the decision.

**Tech Stack:** Python 3.12, `@dataclass(frozen=True, slots=True)` pattern (matches existing models.py), PyYAML loader, matplotlib (Agg backend, headless), pytest. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-28-wheels-canonical-design.md`](../specs/2026-05-28-wheels-canonical-design.md) — every decision and tradeoff in §5 of the spec is locked in; do not re-litigate.

**Ordering rationale:** Tasks 1–4 are additive (no breaking changes — `wheels` stays optional). Task 5 is the atomic flip-to-required moment where fleet.yaml, the loader, and the test surface must all be coherent simultaneously. Tasks 6–10 are post-flip cleanups.

---

## Task 0: Rename the branch and confirm baseline

**Files:** none (git only)

**Context:** The spec was committed on `feature/wheels-canonical-design-322`. Implementation continues on the same line of history but the branch name should reflect that it now covers more than just the design.

- [ ] **Step 1: Rename the branch**

```bash
git switch feature/wheels-canonical-design-322
git branch -m feature/wheels-canonical-322
```

- [ ] **Step 2: Confirm the spec commit is present**

```bash
git log --oneline -1
```

Expected: `260da09 docs(spec): design for #322 — wheels as canonical per-aircraft data` (sha may differ if rebased).

- [ ] **Step 3: Confirm baseline tests are green before any code change**

```bash
pytest -q
ruff check src/ tests/
mypy src/hangarfit/
```

Expected: all green. If anything fails on a clean develop, stop and surface the failure — it is not from this plan.

---

## Task 1: Add the `Wheels` dataclass to models.py

**Files:**
- Modify: `src/hangarfit/models.py` (add a new dataclass near the other geometry types)
- Test: `tests/test_models.py` (extend)

**Context:** `Wheels` is a small frozen dataclass holding the β-schema fields (`main_offset_x_m`, optional `track_m`, optional `third_wheel_offset_x_m`). Two derived properties: `positions` (the (x, y) list every consumer reads) and `wheelbase_m` (the absolute longitudinal distance between mains and third wheel, or `None` for monowheel). Validation is structural only at this stage — the relationship to `gear` is enforced in the loader (Task 3), not here, because `Wheels` does not know which `Gear` it belongs to.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
import pytest

from hangarfit.models import Wheels


class TestWheels:
    def test_monowheel_positions_single(self) -> None:
        w = Wheels(main_offset_x_m=0.0, track_m=None, third_wheel_offset_x_m=None)
        assert w.positions == [(0.0, 0.0)]
        assert w.wheelbase_m is None

    def test_monowheel_offset_non_zero_main(self) -> None:
        w = Wheels(main_offset_x_m=-0.5, track_m=None, third_wheel_offset_x_m=None)
        assert w.positions == [(-0.5, 0.0)]

    def test_nosewheel_three_positions_in_order(self) -> None:
        w = Wheels(main_offset_x_m=-0.10, track_m=1.80, third_wheel_offset_x_m=2.50)
        assert w.positions == [(-0.10, 0.90), (-0.10, -0.90), (2.50, 0.0)]
        assert w.wheelbase_m == pytest.approx(2.60)

    def test_tailwheel_three_positions_in_order(self) -> None:
        w = Wheels(main_offset_x_m=0.20, track_m=1.80, third_wheel_offset_x_m=-3.40)
        assert w.positions == [(0.20, 0.90), (0.20, -0.90), (-3.40, 0.0)]
        assert w.wheelbase_m == pytest.approx(3.60)

    def test_rejects_track_without_third_wheel(self) -> None:
        with pytest.raises(ValueError, match="track_m requires third_wheel_offset_x_m"):
            Wheels(main_offset_x_m=0.0, track_m=1.5, third_wheel_offset_x_m=None)

    def test_rejects_third_wheel_without_track(self) -> None:
        with pytest.raises(ValueError, match="third_wheel_offset_x_m requires track_m"):
            Wheels(main_offset_x_m=0.0, track_m=None, third_wheel_offset_x_m=2.0)

    def test_rejects_non_positive_track(self) -> None:
        with pytest.raises(ValueError, match="track_m must be positive"):
            Wheels(main_offset_x_m=0.0, track_m=0.0, third_wheel_offset_x_m=2.0)
        with pytest.raises(ValueError, match="track_m must be positive"):
            Wheels(main_offset_x_m=0.0, track_m=-1.0, third_wheel_offset_x_m=2.0)

    def test_rejects_non_finite_values(self) -> None:
        import math

        with pytest.raises(ValueError, match="must be finite"):
            Wheels(main_offset_x_m=math.inf, track_m=None, third_wheel_offset_x_m=None)
        with pytest.raises(ValueError, match="must be finite"):
            Wheels(main_offset_x_m=0.0, track_m=math.nan, third_wheel_offset_x_m=2.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_models.py::TestWheels -v
```

Expected: `ImportError` or `AttributeError: module 'hangarfit.models' has no attribute 'Wheels'`.

- [ ] **Step 3: Implement `Wheels`**

In `src/hangarfit/models.py`, add the dataclass alongside the other geometry types. Suggested placement: right above `Aircraft` (so the reader sees `Wheels` before the field that uses it). Match the existing `@dataclass(frozen=True, slots=True)` style.

```python
@dataclass(frozen=True, slots=True)
class Wheels:
    """Plane-local wheel positions for one aircraft.

    Origin is the per-aircraft anchor that ``Placement.x_m / y_m`` refers to —
    the same origin every other Part offset is measured from. Each main wheel
    sits at ``(main_offset_x_m, ±track_m/2)``; the third (nose or tail) wheel,
    if present, sits at ``(third_wheel_offset_x_m, 0)``.

    ``track_m`` and ``third_wheel_offset_x_m`` are both ``None`` for monowheel
    aircraft (only the central main wheel is modelled; outriggers stay
    render-only via the wing footprint). For tricycle and tailwheel aircraft,
    both fields are required — the loader enforces this against ``gear``.
    """

    main_offset_x_m: float
    track_m: float | None
    third_wheel_offset_x_m: float | None

    def __post_init__(self) -> None:
        import math

        if not math.isfinite(self.main_offset_x_m):
            raise ValueError(
                f"Wheels.main_offset_x_m must be finite, got {self.main_offset_x_m!r}"
            )
        if (self.track_m is None) != (self.third_wheel_offset_x_m is None):
            # XOR: both present (tricycle/tailwheel) or both absent (monowheel).
            if self.track_m is None:
                raise ValueError(
                    "Wheels.third_wheel_offset_x_m requires track_m to also be set "
                    "(both present for tricycle/tailwheel, both None for monowheel)"
                )
            raise ValueError(
                "Wheels.track_m requires third_wheel_offset_x_m to also be set "
                "(both present for tricycle/tailwheel, both None for monowheel)"
            )
        if self.track_m is not None:
            if not math.isfinite(self.track_m):
                raise ValueError(f"Wheels.track_m must be finite, got {self.track_m!r}")
            if self.track_m <= 0.0:
                raise ValueError(f"Wheels.track_m must be positive, got {self.track_m!r}")
        if self.third_wheel_offset_x_m is not None and not math.isfinite(
            self.third_wheel_offset_x_m
        ):
            raise ValueError(
                f"Wheels.third_wheel_offset_x_m must be finite, "
                f"got {self.third_wheel_offset_x_m!r}"
            )

    @property
    def positions(self) -> list[tuple[float, float]]:
        """Plane-local ``(x, y)`` of every wheel.

        Returns 1 entry for monowheel (``(main_offset_x_m, 0)``) or 3 entries
        for tricycle/tailwheel (two mains at ``(main_offset_x_m, ±track_m/2)``
        then the third wheel at ``(third_wheel_offset_x_m, 0)``). The order is
        stable: mains first (``+y`` then ``-y``), then the third wheel.
        """
        if self.track_m is None:
            return [(self.main_offset_x_m, 0.0)]
        assert self.third_wheel_offset_x_m is not None  # narrowed by __post_init__
        half_track = self.track_m / 2.0
        return [
            (self.main_offset_x_m, half_track),
            (self.main_offset_x_m, -half_track),
            (self.third_wheel_offset_x_m, 0.0),
        ]

    @property
    def wheelbase_m(self) -> float | None:
        """``abs(third_wheel_offset_x_m - main_offset_x_m)``, or ``None`` for monowheel."""
        if self.third_wheel_offset_x_m is None:
            return None
        return abs(self.third_wheel_offset_x_m - self.main_offset_x_m)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_models.py::TestWheels -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(models): add Wheels dataclass for canonical per-aircraft wheel positions (#322)"
```

---

## Task 2: Add `Aircraft.wheels` as optional (transitional)

**Files:**
- Modify: `src/hangarfit/models.py` (Aircraft dataclass)
- Test: `tests/test_models.py` (extend)

**Context:** Add the new field as `Wheels | None = None` so existing call sites compile and existing tests stay green. The flip to required happens in Task 5, after fleet.yaml is backfilled and the loader is wired up.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_aircraft_wheels_defaults_to_none() -> None:
    """Transitional state: existing Aircraft constructions still work."""
    from hangarfit.models import Aircraft, Part

    parts = (
        Part(kind="fuselage_front", length_m=1.0, width_m=0.5,
             offset_x_m=0.5, offset_y_m=0.0, z_bottom_m=0.0, z_top_m=1.0),
        Part(kind="fuselage_aft", length_m=1.0, width_m=0.5,
             offset_x_m=-0.5, offset_y_m=0.0, z_bottom_m=0.0, z_top_m=1.0),
        Part(kind="wing", length_m=0.5, width_m=4.0,
             offset_x_m=0.0, offset_y_m=0.0, z_bottom_m=2.0, z_top_m=2.2),
    )
    a = Aircraft(
        id="test_plane",
        name="Test",
        wing_position="high",
        gear="nosewheel",
        movement_mode="always_own_gear",
        turn_radius_m=5.0,
        measured=False,
        notes=None,
        parts=parts,
    )
    assert a.wheels is None


def test_aircraft_accepts_wheels() -> None:
    from hangarfit.models import Aircraft, Part, Wheels

    parts = (
        Part(kind="fuselage_front", length_m=1.0, width_m=0.5,
             offset_x_m=0.5, offset_y_m=0.0, z_bottom_m=0.0, z_top_m=1.0),
        Part(kind="fuselage_aft", length_m=1.0, width_m=0.5,
             offset_x_m=-0.5, offset_y_m=0.0, z_bottom_m=0.0, z_top_m=1.0),
        Part(kind="wing", length_m=0.5, width_m=4.0,
             offset_x_m=0.0, offset_y_m=0.0, z_bottom_m=2.0, z_top_m=2.2),
    )
    wheels = Wheels(main_offset_x_m=-0.10, track_m=1.80, third_wheel_offset_x_m=2.50)
    a = Aircraft(
        id="test_plane",
        name="Test",
        wing_position="high",
        gear="nosewheel",
        movement_mode="always_own_gear",
        turn_radius_m=5.0,
        measured=False,
        notes=None,
        parts=parts,
        wheels=wheels,
    )
    assert a.wheels is wheels
```

> **Note:** Adjust the `Aircraft(...)` kwargs to match the actual field set in `src/hangarfit/models.py` (read the existing dataclass before pasting; field set may have evolved). The two tests above prove the field exists and accepts both `None` and a `Wheels` value.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_models.py::test_aircraft_wheels_defaults_to_none tests/test_models.py::test_aircraft_accepts_wheels -v
```

Expected: `TypeError: Aircraft.__init__() got an unexpected keyword argument 'wheels'` and similar.

- [ ] **Step 3: Add the field**

In `src/hangarfit/models.py`, locate the `Aircraft` dataclass and add the field. Put it after `parts` (last existing field) and before any `__post_init__`. Use `Wheels | None = None`.

```python
@dataclass(frozen=True, slots=True)
class Aircraft:
    # ... existing fields ...
    parts: tuple[Part, ...]
    wheels: Wheels | None = None  # transitional — Task 5 flips to required
```

If `Aircraft` has a `__post_init__` that runs whole-aircraft validation, do not touch it yet — wheel/gear coherence is enforced in the loader (Task 3), not here.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_models.py -v
```

Expected: all tests pass, including the two new ones. The existing `Aircraft` construction tests must still pass — `wheels` defaults to `None`.

- [ ] **Step 5: Type-check**

```bash
mypy src/hangarfit/
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py
git commit -m "feat(models): add Aircraft.wheels field as transitional optional (#322)"
```

---

## Task 3: Add `_parse_wheels` to the loader (transitional — block optional)

**Files:**
- Modify: `src/hangarfit/loader.py` (add helper + call it from per-aircraft parse path)
- Test: `tests/test_loader_wheels.py` (create)

**Context:** Parser is structural: it validates that the key set matches `gear`, enforces sign rules (nose forward of mains, tail aft of mains), and produces a `Wheels` instance. **At this stage the block is still optional** — if a fleet entry has no `wheels:` key, `_parse_wheels` returns `None` and `Aircraft.wheels` stays `None`. This keeps every existing test green while we wire the new code. Task 5 flips the absence path to raise.

- [ ] **Step 1: Create the failing test file**

Create `tests/test_loader_wheels.py`:

```python
"""Loader tests for the wheels: block (#322).

These tests exercise the wheels parsing path in isolation by loading
single-aircraft fleet YAMLs from in-memory strings.
"""

from __future__ import annotations

import textwrap

import pytest

from hangarfit.loader import InvalidFleetError, load_fleet_from_string


# Minimal valid aircraft body — the wheels: block is appended per test.
_NOSEWHEEL_BODY = textwrap.dedent(
    """\
    aircraft:
      - id: testplane
        name: "Test Plane"
        wing_position: high
        gear: nosewheel
        movement_mode: always_own_gear
        turn_radius_m: 4.0
        measured: false
        parts:
          - kind: fuselage
            length_m: 6.0
            width_m: 0.8
            offset_x_m: 0.0
            offset_y_m: 0.0
            z_bottom_m: 0.0
            z_top_m: 1.4
          - kind: wing
            length_m: 1.2
            width_m: 9.0
            offset_x_m: 0.5
            offset_y_m: 0.0
            z_bottom_m: 2.0
            z_top_m: 2.2
    """
)


def _with_wheels(yaml_block: str) -> str:
    """Append a wheels: block (or any sub-yaml) to the test aircraft body."""
    indented = textwrap.indent(yaml_block, "    ")
    return _NOSEWHEEL_BODY + indented


class TestWheelsLoadingHappyPath:
    def test_nosewheel_loads(self) -> None:
        yaml = _with_wheels(
            "wheels:\n"
            "  main_offset_x_m: -0.10\n"
            "  track_m: 1.80\n"
            "  third_wheel_offset_x_m: 2.50\n"
        )
        fleet = load_fleet_from_string(yaml)
        a = fleet.aircraft["testplane"]
        assert a.wheels is not None
        assert a.wheels.main_offset_x_m == -0.10
        assert a.wheels.track_m == 1.80
        assert a.wheels.third_wheel_offset_x_m == 2.50

    def test_no_wheels_block_yields_none_for_now(self) -> None:
        """Transitional: missing wheels: block leaves Aircraft.wheels == None.

        Task 5 flips this to raise InvalidFleetError.
        """
        fleet = load_fleet_from_string(_NOSEWHEEL_BODY)
        assert fleet.aircraft["testplane"].wheels is None


class TestWheelsLoadingErrorPaths:
    def test_nosewheel_missing_track(self) -> None:
        yaml = _with_wheels(
            "wheels:\n"
            "  main_offset_x_m: -0.10\n"
            "  third_wheel_offset_x_m: 2.50\n"
        )
        with pytest.raises(InvalidFleetError, match="wheels.*track_m"):
            load_fleet_from_string(yaml)

    def test_nosewheel_missing_third_wheel(self) -> None:
        yaml = _with_wheels(
            "wheels:\n"
            "  main_offset_x_m: -0.10\n"
            "  track_m: 1.80\n"
        )
        with pytest.raises(InvalidFleetError, match="wheels.*third_wheel_offset_x_m"):
            load_fleet_from_string(yaml)

    def test_monowheel_with_track_rejected(self) -> None:
        yaml = (
            _NOSEWHEEL_BODY.replace("gear: nosewheel", "gear: monowheel")
            + "    wheels:\n"
            "      main_offset_x_m: 0.0\n"
            "      track_m: 1.80\n"
            "      third_wheel_offset_x_m: 2.50\n"
        )
        with pytest.raises(InvalidFleetError, match="monowheel.*track_m"):
            load_fleet_from_string(yaml)

    def test_nosewheel_third_wheel_behind_mains_rejected(self) -> None:
        """A nosewheel plane must have third_wheel_offset_x_m > main_offset_x_m."""
        yaml = _with_wheels(
            "wheels:\n"
            "  main_offset_x_m: 0.0\n"
            "  track_m: 1.80\n"
            "  third_wheel_offset_x_m: -2.50\n"  # WRONG: nose should be forward
        )
        with pytest.raises(InvalidFleetError, match="nosewheel.*forward"):
            load_fleet_from_string(yaml)

    def test_tailwheel_third_wheel_forward_of_mains_rejected(self) -> None:
        yaml = (
            _NOSEWHEEL_BODY.replace("gear: nosewheel", "gear: tailwheel")
            .replace("turn_radius_m: 4.0", "turn_radius_m: 5.0")
            + "    wheels:\n"
            "      main_offset_x_m: 0.20\n"
            "      track_m: 1.80\n"
            "      third_wheel_offset_x_m: 3.00\n"  # WRONG: tail should be aft
        )
        with pytest.raises(InvalidFleetError, match="tailwheel.*aft"):
            load_fleet_from_string(yaml)

    def test_unknown_keys_rejected(self) -> None:
        yaml = _with_wheels(
            "wheels:\n"
            "  main_offset_x_m: -0.10\n"
            "  track_m: 1.80\n"
            "  third_wheel_offset_x_m: 2.50\n"
            "  bogus_field: 1.0\n"
        )
        with pytest.raises(InvalidFleetError, match="unknown.*bogus_field"):
            load_fleet_from_string(yaml)
```

> **Note:** `load_fleet_from_string` may not exist as a public helper. If only `load_fleet` (file-based) exists, either (a) add a tiny `load_fleet_from_string` helper to `loader.py` that wraps `yaml.safe_load` + the existing dict-based parse path, or (b) write a `_tmp_yaml(tmp_path, body)` pytest fixture that writes and loads from disk. Pick whichever matches existing test idioms in `tests/test_loader.py` — do NOT introduce a new file-IO style if a string-based path already exists.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_loader_wheels.py -v
```

Expected: all tests fail with errors like `InvalidFleetError` not raised or `aircraft.wheels is None` (because `_parse_wheels` doesn't exist yet).

- [ ] **Step 3: Implement `_parse_wheels` in loader.py**

In `src/hangarfit/loader.py`:

```python
from hangarfit.models import Wheels  # add to imports if not already present

_WHEELS_KEYS_BY_GEAR: dict[str, frozenset[str]] = {
    "monowheel": frozenset({"main_offset_x_m"}),
    "nosewheel": frozenset({"main_offset_x_m", "track_m", "third_wheel_offset_x_m"}),
    "tailwheel": frozenset({"main_offset_x_m", "track_m", "third_wheel_offset_x_m"}),
}


def _parse_wheels(
    entry: Mapping[str, Any] | None,
    gear: str,
    aircraft_id: str,
) -> Wheels | None:
    """Parse a ``wheels:`` block into a :class:`Wheels`.

    Returns ``None`` if ``entry`` is ``None`` (transitional — Task 5 flips
    this to raise ``InvalidFleetError``).

    Validates that the key set exactly matches ``_WHEELS_KEYS_BY_GEAR[gear]``
    and that nose-vs-tail sign rules hold for tricycle/tailwheel gear.
    """
    if entry is None:
        return None  # TODO(#322 Task 5): raise InvalidFleetError

    if gear not in _WHEELS_KEYS_BY_GEAR:
        raise InvalidFleetError(
            f"Aircraft {aircraft_id!r}: unsupported gear {gear!r} for wheels parsing"
        )

    expected = _WHEELS_KEYS_BY_GEAR[gear]
    seen = frozenset(entry.keys())
    missing = expected - seen
    unknown = seen - expected
    if missing:
        raise InvalidFleetError(
            f"Aircraft {aircraft_id!r}: wheels: block missing required key(s) for "
            f"gear={gear!r}: {sorted(missing)}"
        )
    if unknown:
        # For monowheel, the most common mistake is adding track_m / third_wheel_offset_x_m;
        # surface those specifically.
        if gear == "monowheel" and unknown & {"track_m", "third_wheel_offset_x_m"}:
            raise InvalidFleetError(
                f"Aircraft {aircraft_id!r}: monowheel wheels: block must not set "
                f"track_m or third_wheel_offset_x_m (got {sorted(unknown)})"
            )
        raise InvalidFleetError(
            f"Aircraft {aircraft_id!r}: wheels: block has unknown key(s): "
            f"{sorted(unknown)}"
        )

    main_offset_x_m = _to_float(entry["main_offset_x_m"], "wheels.main_offset_x_m")
    if gear == "monowheel":
        wheels = Wheels(
            main_offset_x_m=main_offset_x_m,
            track_m=None,
            third_wheel_offset_x_m=None,
        )
    else:
        track_m = _to_float(entry["track_m"], "wheels.track_m")
        third = _to_float(entry["third_wheel_offset_x_m"], "wheels.third_wheel_offset_x_m")
        # Sign rule per gear:
        #   nosewheel → third (nose) must be forward of mains (greater x)
        #   tailwheel → third (tail) must be aft of mains    (lesser x)
        if gear == "nosewheel" and not third > main_offset_x_m:
            raise InvalidFleetError(
                f"Aircraft {aircraft_id!r}: nosewheel third_wheel_offset_x_m must be "
                f"forward of mains (greater than main_offset_x_m={main_offset_x_m}); "
                f"got {third}"
            )
        if gear == "tailwheel" and not third < main_offset_x_m:
            raise InvalidFleetError(
                f"Aircraft {aircraft_id!r}: tailwheel third_wheel_offset_x_m must be "
                f"aft of mains (less than main_offset_x_m={main_offset_x_m}); got {third}"
            )
        try:
            wheels = Wheels(
                main_offset_x_m=main_offset_x_m,
                track_m=track_m,
                third_wheel_offset_x_m=third,
            )
        except ValueError as exc:
            raise InvalidFleetError(
                f"Aircraft {aircraft_id!r}: invalid wheels block: {exc}"
            ) from exc

    return wheels
```

Wire it into the per-aircraft parse path. Locate the function that builds an `Aircraft` from a dict (likely `_parse_aircraft` or similar — read `loader.py` first). Pass `entry.get("wheels")` to `_parse_wheels` and assign the result to the `wheels=` kwarg when constructing `Aircraft`.

> **Note on `Mapping[str, Any]` import:** `Mapping` from `collections.abc` should already be imported in loader.py; if not, add it.

> **Note on `_to_float`:** This helper already exists at `loader.py:~660`; reuse it.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_loader_wheels.py -v
pytest tests/test_loader.py -v
```

Expected: all `test_loader_wheels.py` tests pass; existing `test_loader.py` tests still pass (because missing `wheels:` returns `None`, no break).

- [ ] **Step 5: Type-check**

```bash
mypy src/hangarfit/
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_wheels.py
git commit -m "feat(loader): parse optional wheels: block with structural validation (#322)"
```

---

## Task 4: Backfill `data/fleet.yaml` wheels blocks (all 9 aircraft)

**Files:**
- Modify: `data/fleet.yaml` (add `wheels:` block to every entry)

**Context:** Every aircraft gets a `wheels:` block using published-spec wheelbases and tracks where available, conservative estimates otherwise. The block sits between `notes:`/`parts:` — whichever pattern reads cleanest given the existing layout. **Critical guard:** the wheelbase implied by your numbers MUST satisfy `0.5 × wheelbase ≤ turn_radius_m ≤ 5 × wheelbase` for every own-gear aircraft, otherwise Task 6's cross-check will fail the load. Verify each entry by hand before the commit.

The 9 aircraft and reference numbers (published specs or fleet-typical estimates — use these as a starting point, refine to taste if you find better sources):

| id | gear | turn_radius_m | wheelbase target | track target | notes |
|---|---|---|---|---|---|
| scheibe_falke | monowheel | null | n/a | n/a | main wheel at origin |
| aviat_husky | tailwheel | 5.0 | ~5.0 m | ~2.0 m | main at ~+0.2, tail at ~−4.8 (Husky has long tail moment) |
| fuji | nosewheel | 7.0 | ~2.0 m | ~2.5 m | low-wing, retractable not modelled; mains at ~−0.4, nose at ~+1.6 |
| wild_thing | nosewheel | null | ~1.5 m | ~1.5 m | always_cart; cross-check skipped, but data still required |
| zlin_savage | tailwheel | null | ~4.5 m | ~1.8 m | always_cart; cross-check skipped |
| cessna_140 | tailwheel | 5.5 | ~4.3 m | ~2.1 m | mains at ~+0.5, tail at ~−3.8 |
| cessna_150 | nosewheel | 4.5 | ~1.5 m | ~2.3 m | mains at ~−0.2, nose at ~+1.3 |
| ctsl | nosewheel | 4.0 | ~1.5 m | ~1.7 m | LSA; mains at ~−0.1, nose at ~+1.4 |
| fk9_mkii | nosewheel | 4.0 | ~1.5 m | ~1.4 m | LSA; mains at ~−0.1, nose at ~+1.4 |

> The "target" columns are **starting estimates**, not gospel. Look up published specs (Pilot's Operating Handbook, type certificate data sheets, manufacturer marketing pages) for each aircraft and adjust. The cross-check band (0.5×–5×) is loose enough to absorb refinement.

- [ ] **Step 1: Add wheels blocks to all 9 aircraft**

For each aircraft entry in `data/fleet.yaml`, append a `wheels:` block. Example for `aviat_husky`:

```yaml
  - id: aviat_husky
    name: "Aviat Husky A-1"
    # ... existing fields unchanged ...
    parts:
      # ... unchanged ...
    struts:
      # ... unchanged ...
    wheels:
      main_offset_x_m: 0.20
      track_m: 2.0
      third_wheel_offset_x_m: -4.80
```

For `scheibe_falke` (monowheel):

```yaml
    wheels:
      main_offset_x_m: 0.0
```

Do all 9 entries. Place `wheels:` after `parts:` (and after `struts:` where present) so it reads as "geometry + struts + wheels".

- [ ] **Step 2: Verify each wheelbase passes the 0.5×–5× band by hand**

For each own-gear aircraft (Husky, Fuji, Cessna 140, Cessna 150, CTSL, FK9 — six total), compute `wheelbase = abs(third_wheel_offset_x_m - main_offset_x_m)` and confirm `0.5 × wheelbase ≤ turn_radius_m ≤ 5 × wheelbase`. Skip the always_cart and monowheel entries.

Example: Husky main=+0.20, third=−4.80 → wheelbase=5.0; turn_radius_m=5.0; band [2.5, 25.0]; 5.0 ∈ band ✓.

- [ ] **Step 3: Confirm fleet loads cleanly with the additions**

```bash
python -c "from hangarfit.loader import load_fleet; f = load_fleet('data/fleet.yaml'); [print(k, v.wheels) for k, v in f.aircraft.items()]"
```

Expected: every aircraft's `Wheels(...)` printed, no exception.

- [ ] **Step 4: Run the full test suite**

```bash
pytest -q
```

Expected: all green. The existing fleet-loading tests now produce Aircraft with `wheels` populated; nothing else changes.

- [ ] **Step 5: Commit**

```bash
git add data/fleet.yaml
git commit -m "data(fleet): backfill wheels: block on all 9 aircraft (#322)"
```

---

## Task 5: Flip `Aircraft.wheels` to required + raise on missing block

**Files:**
- Modify: `src/hangarfit/models.py` (drop the `= None` default)
- Modify: `src/hangarfit/loader.py` (`_parse_wheels` raises on `entry is None`)
- Modify: `tests/test_loader_wheels.py` (replace the "yields None for now" test)
- Search: any inline `Aircraft(...)` constructions in tests/ — add `wheels=` kwarg

**Context:** This is the atomic flip. Until now, `wheels` has been optional; from this commit onward, every fleet entry and every test-constructed Aircraft must carry one. Any inline `Aircraft(...)` in tests that doesn't pass `wheels=` will break. Tasks 1–4 prepared the ground so this commit is a clean change of contract.

- [ ] **Step 1: Find every inline Aircraft construction**

```bash
grep -rn "Aircraft(" tests/ --include='*.py'
grep -rn "from hangarfit.models import" tests/ --include='*.py' | grep Aircraft
```

Expected output: a list of test files. The ones found in the survey (Task 0) were:
`tests/test_visualize.py, tests/test_towplanner_motion.py, tests/test_geometry.py, tests/test_towplanner_fill.py, tests/test_towplanner_dubins.py, tests/test_towplanner_search.py, tests/test_models.py, tests/test_solver_search.py, tests/test_towplanner_entry_cone.py, tests/test_collisions.py, tests/fuzz/strategies.py`.

For each match: either (a) it's a test that loads from `data/fleet.yaml` (no change needed — fleet already has wheels), or (b) it constructs `Aircraft(...)` inline with literal field values (needs `wheels=Wheels(...)` added).

- [ ] **Step 2: Add a test helper to absorb the boilerplate**

Create or extend `tests/conftest.py`:

```python
from hangarfit.models import Aircraft, Part, Wheels


_DEFAULT_PARTS: tuple[Part, ...] = (
    Part(kind="fuselage_front", length_m=1.0, width_m=0.5,
         offset_x_m=0.5, offset_y_m=0.0, z_bottom_m=0.0, z_top_m=1.0),
    Part(kind="fuselage_aft", length_m=1.0, width_m=0.5,
         offset_x_m=-0.5, offset_y_m=0.0, z_bottom_m=0.0, z_top_m=1.0),
    Part(kind="wing", length_m=0.5, width_m=4.0,
         offset_x_m=0.0, offset_y_m=0.0, z_bottom_m=2.0, z_top_m=2.2),
)


def make_test_aircraft(
    *,
    id: str = "test_plane",
    gear: str = "nosewheel",
    movement_mode: str = "always_own_gear",
    turn_radius_m: float | None = 4.0,
    wheels: Wheels | None = None,
    parts: tuple[Part, ...] = _DEFAULT_PARTS,
    **overrides: object,
) -> Aircraft:
    """Build a minimal valid Aircraft for tests with sensible wheel defaults.

    For tricycle/tailwheel gear, `wheels` defaults to a plausible β-schema
    block whose wheelbase satisfies the 0.5×–5× cross-check against
    `turn_radius_m=4.0`. Override `wheels=` to test specific positions.
    """
    if wheels is None:
        if gear == "monowheel":
            wheels = Wheels(main_offset_x_m=0.0, track_m=None, third_wheel_offset_x_m=None)
        elif gear == "tailwheel":
            wheels = Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-2.0)
        else:  # nosewheel
            wheels = Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=2.0)
    kwargs: dict[str, object] = dict(
        id=id,
        name="Test",
        wing_position="high",
        gear=gear,
        movement_mode=movement_mode,
        turn_radius_m=turn_radius_m,
        measured=False,
        notes=None,
        parts=parts,
        wheels=wheels,
    )
    kwargs.update(overrides)
    return Aircraft(**kwargs)  # type: ignore[arg-type]
```

> Check `conftest.py` first — if it already exports a helper of this shape (`make_aircraft`, `_aircraft`, etc.), extend the existing one rather than introducing a new name.

- [ ] **Step 3: Migrate inline Aircraft constructions to the helper**

For each test file in the survey, replace literal `Aircraft(...)` calls with `make_test_aircraft(...)` calls. The helper's `**overrides` lets each test still set whatever fields it cares about. Test bodies that are testing the dataclass constructor itself (e.g. `tests/test_models.py::test_aircraft_*` cases) stay literal — those are testing the contract directly.

```bash
pytest -q  # before flipping to required, the suite must still be green
```

Expected: green. If any test fails here, it's because the migration missed a call site or the helper's defaults conflict with that test's expectations — fix before continuing.

- [ ] **Step 4: Flip `Aircraft.wheels` to required**

In `src/hangarfit/models.py`:

```python
@dataclass(frozen=True, slots=True)
class Aircraft:
    # ... existing fields, no defaults ...
    parts: tuple[Part, ...]
    wheels: Wheels   # required — no default
```

In `src/hangarfit/loader.py`, in `_parse_wheels`, replace the `if entry is None: return None` early-return with a raise:

```python
def _parse_wheels(
    entry: Mapping[str, Any] | None,
    gear: str,
    aircraft_id: str,
) -> Wheels:
    if entry is None:
        raise InvalidFleetError(
            f"Aircraft {aircraft_id!r}: wheels: block is required "
            f"(see fleet.yaml header for the schema)"
        )
    # ... rest unchanged ...
```

Update the function return type annotation from `Wheels | None` to `Wheels`. Update the call site so it no longer needs to handle `None`.

- [ ] **Step 5: Update the transitional test to assert the new behaviour**

In `tests/test_loader_wheels.py`, replace `test_no_wheels_block_yields_none_for_now`:

```python
def test_no_wheels_block_now_raises(self) -> None:
    """After Task 5 flip: a missing wheels: block is a load error."""
    with pytest.raises(InvalidFleetError, match="wheels: block is required"):
        load_fleet_from_string(_NOSEWHEEL_BODY)
```

Remove the `test_aircraft_wheels_defaults_to_none` test in `tests/test_models.py` (or update it to assert `wheels` is now positional/required).

- [ ] **Step 6: Run tests**

```bash
pytest -q
mypy src/hangarfit/
```

Expected: green and clean. mypy in particular should complain if any caller forgot to pass `wheels=`.

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/models.py src/hangarfit/loader.py tests/
git commit -m "feat(models)!: make Aircraft.wheels required; loader raises on missing block (#322)

BREAKING: every Aircraft construction must now pass wheels=Wheels(...).
Fleet YAML files must include a wheels: block per aircraft."
```

> The `!` and `BREAKING:` are conventional-commits format for a schema-breaking change. This is the only commit in the plan that warrants the marker.

---

## Task 6: Add the loader cross-check (0.5×–5× wheelbase band)

**Files:**
- Modify: `src/hangarfit/loader.py` (add `_validate_wheels_vs_turn_radius` + call site)
- Modify: `tests/test_loader_wheels.py` (extend with cross-check tests)

**Context:** Cross-check runs after the full Aircraft is constructed — needs both `aircraft.wheels.wheelbase_m` and `aircraft.turn_radius_m`. Skips `always_cart` (no own-gear radius) and monowheel (no wheelbase). Hard error on band violation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loader_wheels.py`:

```python
class TestCrossCheck:
    def test_own_gear_within_band_passes(self) -> None:
        # wheelbase = abs(2.5 - (-0.1)) = 2.6; turn_radius_m=4.0 ∈ [1.3, 13.0]
        yaml = _with_wheels(
            "wheels:\n"
            "  main_offset_x_m: -0.10\n"
            "  track_m: 1.80\n"
            "  third_wheel_offset_x_m: 2.50\n"
        )
        load_fleet_from_string(yaml)  # no raise

    def test_turn_radius_below_band_rejected(self) -> None:
        # wheelbase = 10.0; band [5.0, 50.0]; turn_radius_m=4.0 too small
        yaml = _with_wheels(
            "wheels:\n"
            "  main_offset_x_m: -5.0\n"
            "  track_m: 1.80\n"
            "  third_wheel_offset_x_m: 5.0\n"
        )
        with pytest.raises(InvalidFleetError, match="implausible.*wheelbase"):
            load_fleet_from_string(yaml)

    def test_turn_radius_above_band_rejected(self) -> None:
        # wheelbase = 0.4; band [0.2, 2.0]; turn_radius_m=4.0 too big
        yaml = _with_wheels(
            "wheels:\n"
            "  main_offset_x_m: -0.10\n"
            "  track_m: 1.80\n"
            "  third_wheel_offset_x_m: 0.30\n"
        )
        with pytest.raises(InvalidFleetError, match="implausible.*wheelbase"):
            load_fleet_from_string(yaml)

    def test_always_cart_skips_cross_check(self) -> None:
        """always_cart aircraft (turn_radius_m: null) skip the band check."""
        yaml = textwrap.dedent(
            """\
            aircraft:
              - id: cart_plane
                name: "Cart Test"
                wing_position: high
                gear: nosewheel
                movement_mode: always_cart
                turn_radius_m: null
                measured: false
                parts:
                  - kind: fuselage
                    length_m: 6.0
                    width_m: 0.8
                    offset_x_m: 0.0
                    offset_y_m: 0.0
                    z_bottom_m: 0.0
                    z_top_m: 1.4
                  - kind: wing
                    length_m: 1.2
                    width_m: 9.0
                    offset_x_m: 0.5
                    offset_y_m: 0.0
                    z_bottom_m: 2.0
                    z_top_m: 2.2
                wheels:
                  main_offset_x_m: -5.0
                  track_m: 1.8
                  third_wheel_offset_x_m: 5.0
            """
        )
        load_fleet_from_string(yaml)  # no raise even though wheelbase=10 vs radius=null

    def test_monowheel_skips_cross_check(self) -> None:
        """Monowheel aircraft have no wheelbase concept; check is skipped."""
        yaml = textwrap.dedent(
            """\
            aircraft:
              - id: mono_plane
                name: "Mono Test"
                wing_position: high
                gear: monowheel
                movement_mode: always_own_gear
                turn_radius_m: 100.0
                measured: false
                parts:
                  - kind: fuselage
                    length_m: 6.0
                    width_m: 0.7
                    offset_x_m: 0.0
                    offset_y_m: 0.0
                    z_bottom_m: 0.0
                    z_top_m: 1.4
                  - kind: wing
                    length_m: 1.2
                    width_m: 18.0
                    offset_x_m: 1.5
                    offset_y_m: 0.0
                    z_bottom_m: 2.0
                    z_top_m: 2.2
                wheels:
                  main_offset_x_m: 0.0
            """
        )
        load_fleet_from_string(yaml)  # no raise — no wheelbase to check against
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_loader_wheels.py::TestCrossCheck -v
```

Expected: `test_turn_radius_below_band_rejected` and `test_turn_radius_above_band_rejected` fail (cross-check not implemented yet, no raise).

- [ ] **Step 3: Implement the cross-check**

In `src/hangarfit/loader.py`:

```python
_WHEELBASE_BAND_LOW = 0.5
_WHEELBASE_BAND_HIGH = 5.0


def _validate_wheels_vs_turn_radius(aircraft: Aircraft) -> None:
    """Raise if turn_radius_m is implausible given the wheel-derived wheelbase.

    Skipped for always_cart (turn_radius_m is None) and for monowheel
    (no wheelbase concept). See ADR-0013 for the rationale on the loose band.
    """
    if aircraft.turn_radius_m is None:
        return
    wheelbase = aircraft.wheels.wheelbase_m
    if wheelbase is None:
        return
    low = _WHEELBASE_BAND_LOW * wheelbase
    high = _WHEELBASE_BAND_HIGH * wheelbase
    r = aircraft.turn_radius_m
    if not (low <= r <= high):
        raise InvalidFleetError(
            f"Aircraft {aircraft.id!r}: turn_radius_m={r} is implausible given "
            f"wheelbase={wheelbase:.2f}m (expected {low:.2f}..{high:.2f}). "
            f"Either fix the wheel positions or fix turn_radius_m."
        )
```

Call it from the per-aircraft parse path right after the `Aircraft(...)` is constructed. Pseudocode:

```python
aircraft = Aircraft(..., wheels=wheels, ...)
_validate_wheels_vs_turn_radius(aircraft)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_loader_wheels.py -v
pytest -q
```

Expected: all green, including the new cross-check tests and the full suite.

- [ ] **Step 5: Sanity-check the real fleet still loads**

```bash
python -c "from hangarfit.loader import load_fleet; load_fleet('data/fleet.yaml'); print('OK')"
```

Expected: `OK`. If this raises, the Task 4 backfill numbers don't satisfy the band — fix the backfill (preferred) before continuing.

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/loader.py tests/test_loader_wheels.py
git commit -m "feat(loader): cross-check turn_radius_m against wheelbase (0.5×–5× band) (#322)"
```

---

## Task 7: Simplify `visualize.py` to consume `aircraft.wheels.positions`

**Files:**
- Modify: `src/hangarfit/visualize.py` (replace `_draw_gear_glyph` body; delete heuristic constants)
- Test: `tests/test_visualize_wheels.py` (create)

**Context:** With wheel positions now first-class, the `_draw_gear_glyph` `if/elif aircraft.gear` ladder collapses into a single loop. The heuristic constants (`_NOSE_GEAR_FRAC`, `_MAIN_GEAR_FWD_FRAC`, `_MAIN_GEAR_TAILDRAGGER_FWD_FRAC`, `_MAIN_GEAR_LATERAL_FRAC`) get deleted along with the fuselage-segment reconstruction inside the function. `_draw_cart_glyph` and the cart-deck constants stay untouched — that surface belongs to #321.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_visualize_wheels.py`:

```python
"""Visualize tests for wheel glyph placement (#322)."""

from __future__ import annotations

import pytest

# Import everything via the tests/conftest helper.
from tests.conftest import make_test_aircraft

from hangarfit.models import Placement, Wheels
from hangarfit.visualize import _draw_gear_glyph


@pytest.fixture
def fake_ax(monkeypatch):
    """Capture every (wx, wy) passed to _add_wheel."""
    from hangarfit import visualize

    captured: list[tuple[float, float]] = []

    def fake_add(ax, wx, wy):
        captured.append((wx, wy))
        return None

    monkeypatch.setattr(visualize, "_add_wheel", fake_add)
    return captured


class TestOwnGearWheelPositions:
    def test_nosewheel_three_wheels_at_world_coords(self, fake_ax) -> None:
        a = make_test_aircraft(
            gear="nosewheel",
            wheels=Wheels(main_offset_x_m=-0.10, track_m=1.80, third_wheel_offset_x_m=2.50),
        )
        # Heading 0 (north / +y): plane-local +x maps to world +y, plane +y to world -x
        # (or whatever the ADR-0002 convention is; the assertion is on count + reflection).
        placement = Placement(plane_id=a.id, x_m=10.0, y_m=5.0, heading_deg=0.0, on_carts=False)
        _draw_gear_glyph(None, placement, a)
        assert len(fake_ax) == 3

    def test_monowheel_single_wheel(self, fake_ax) -> None:
        a = make_test_aircraft(
            gear="monowheel",
            turn_radius_m=None,
            movement_mode="always_cart",
            wheels=Wheels(main_offset_x_m=0.0, track_m=None, third_wheel_offset_x_m=None),
        )
        # always_cart means on_carts=True draws cart glyph; this test forces on_carts=False
        # to exercise the own-gear path even though it's physically unusual.
        placement = Placement(plane_id=a.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
        _draw_gear_glyph(None, placement, a)
        assert len(fake_ax) == 1

    def test_tailwheel_three_wheels(self, fake_ax) -> None:
        a = make_test_aircraft(
            gear="tailwheel",
            wheels=Wheels(main_offset_x_m=0.20, track_m=1.80, third_wheel_offset_x_m=-3.40),
        )
        placement = Placement(plane_id=a.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
        _draw_gear_glyph(None, placement, a)
        assert len(fake_ax) == 3


class TestCartGlyphUnchanged:
    def test_on_carts_falls_through_to_cart_glyph(self, monkeypatch) -> None:
        """Regression guard for #321: on_carts=True must NOT draw own-gear wheels."""
        from hangarfit import visualize

        called: list[str] = []
        monkeypatch.setattr(visualize, "_add_wheel", lambda *a, **k: called.append("wheel"))
        monkeypatch.setattr(visualize, "_draw_cart_glyph", lambda *a, **k: called.append("cart"))

        a = make_test_aircraft(
            gear="nosewheel",
            movement_mode="cart_eligible",
            wheels=Wheels(main_offset_x_m=-0.10, track_m=1.80, third_wheel_offset_x_m=2.50),
        )
        placement = Placement(plane_id=a.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=True)
        _draw_gear_glyph(None, placement, a)

        # Cart was drawn; own-gear wheels were NOT.
        assert "cart" in called
        # _draw_cart_glyph internally calls _add_wheel four times — those are the cart corner
        # wheels, not the plane's gear. We don't assert "wheel" not in called because
        # the cart path still drops 4 wheels; what matters is the cart path was taken.
```

> **Note on `Placement` import path:** `Placement` may live in `hangarfit.models` or in a different module. Read the file before importing — match what visualize.py imports today.

> **Note on the cart-path assertion:** The original `_draw_cart_glyph` calls `_add_wheel` four times for corner wheels (see `visualize.py:539-542`). The fake-monkeypatch above replaces `_draw_cart_glyph` itself, so those corner-wheel calls don't fire — the cart path is exercised via the captured `"cart"` marker.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_visualize_wheels.py -v
```

Expected: failures vary — the existing heuristic code path may still emit 3 wheel calls for nosewheel/tailwheel by coincidence, but the monowheel single-wheel and cart-glyph assertions should distinguish behaviour clearly.

- [ ] **Step 3: Rewrite `_draw_gear_glyph`**

In `src/hangarfit/visualize.py`:

Delete the constants block (currently `visualize.py:141-152`):

```python
# DELETE these four constants:
_NOSE_GEAR_FRAC = 0.85
_MAIN_GEAR_FWD_FRAC = 0.30
_MAIN_GEAR_TAILDRAGGER_FWD_FRAC = 0.45
_MAIN_GEAR_LATERAL_FRAC = 1.6
```

(Keep the comment block above the constants that explains the wheel/cart colour scheme — that's still relevant.)

Replace the body of `_draw_gear_glyph` (currently `visualize.py:432-506`) with:

```python
def _draw_gear_glyph(ax: Any, placement: Placement, aircraft: Aircraft) -> None:
    """Draw landing-gear wheels or a cart glyph depending on ``placement.on_carts``.

    Wheel positions come from ``aircraft.wheels.positions`` (ADR-0013). When the
    plane rides on a cart (``placement.on_carts=True``), the cart-deck glyph is
    drawn instead of the plane's own gear (see ``_draw_cart_glyph``).
    """
    if placement.on_carts:
        _draw_cart_glyph(ax, placement)
        return
    for u, v in aircraft.wheels.positions:
        wx, wy = local_to_world(u, v, placement)
        _add_wheel(ax, wx, wy)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_visualize_wheels.py -v
pytest tests/test_visualize.py -v
pytest -q
```

Expected: all green.

- [ ] **Step 5: Type-check and lint**

```bash
mypy src/hangarfit/
ruff check src/ tests/
```

Expected: clean. `ruff` will likely flag the unused `_NOSE_GEAR_FRAC` etc. as already-removed; if the constants were exported in any `__all__`, sync that too.

- [ ] **Step 6: Render a smoke PNG to eyeball the result**

```bash
hangarfit check layouts/example.yaml --render /tmp/wheels-smoke.png
```

Expected: PNG renders without error. Open it (or move to a viewable location) and confirm wheels are at sensible positions relative to fuselages.

- [ ] **Step 7: Commit**

```bash
git add src/hangarfit/visualize.py tests/test_visualize_wheels.py
git commit -m "refactor(visualize): drop heuristic gear constants; read wheels from data (#322)"
```

---

## Task 8: Reconcile `fleet.yaml` header documentation

**Files:**
- Modify: `data/fleet.yaml` (header comment block)

**Context:** The header comment currently asserts *"Origin of plane-local frame = main-gear / cart centroid"*, which the data doesn't actually honor (visualize.py historically drew mains offset from origin, and the existing fleet entries follow that convention). Replace with an honest description: origin is a per-aircraft anchor; main-gear centroid is derived from `wheels.main_offset_x_m`.

- [ ] **Step 1: Edit the header**

In `data/fleet.yaml`, find the existing header block (lines ~1–34) and replace the paragraph about origin with:

```yaml
# - Plane-local coords: +x = forward (toward nose), +y = right (toward right wingtip)
# - Origin of plane-local frame = a per-aircraft anchor used as the placement
#   reference (where Placement.x_m/y_m positions the plane in world coords).
#   The main-gear centroid is a *derived* point, accessible as
#   (wheels.main_offset_x_m, 0) in plane-local coords. See ADR-0013.
# - Each Part is an oriented rectangle: length_m runs along plane +x,
#   width_m runs along plane +y
# - Heights z_bottom_m / z_top_m are above-ground in world coords
# - struts block (optional) is expanded by the loader into two mirrored
#   strut Parts; cantilever aircraft omit the block entirely
# - wheels block (required) declares the β-schema main_offset_x_m,
#   track_m, and third_wheel_offset_x_m fields per aircraft (see ADR-0013).
#   Monowheel aircraft set only main_offset_x_m. The loader cross-checks
#   wheel-derived wheelbase against turn_radius_m for own-gear aircraft.
```

(Keep the rest of the header — the `kind: fuselage` paragraph and the `turn_radius_m` paragraph — unchanged.)

- [ ] **Step 2: Verify the fleet still loads**

```bash
python -c "from hangarfit.loader import load_fleet; load_fleet('data/fleet.yaml'); print('OK')"
pytest -q
```

Expected: green.

- [ ] **Step 3: Commit**

```bash
git add data/fleet.yaml
git commit -m "docs(fleet): reconcile header origin convention with wheels: block (#322)"
```

---

## Task 9: Write ADR-0013 — Wheels as canonical data

**Files:**
- Create: `docs/adr/0013-wheels-canonical-data.md`
- Modify: `docs/adr/README.md` (if it lists ADRs by number)

**Context:** Peer to ADR-0001 (parts model), ADR-0012 (fuselage split). Records the decision so future contributors don't re-litigate the empirical-vs-derived `turn_radius_m` tradeoff.

- [ ] **Step 1: Read the existing ADR template**

```bash
cat docs/adr/template.md
cat docs/adr/0012-fuselage-front-aft-split.md
```

Use the template structure; match the tone of ADR-0012.

- [ ] **Step 2: Write the ADR**

Create `docs/adr/0013-wheels-canonical-data.md`:

```markdown
# ADR-0013: Wheels as canonical per-aircraft data

Date: 2026-05-28
Status: Accepted

## Context

Two surface representations of the same physical property — *where the wheels
of each aircraft are* — disagreed:

- `data/fleet.yaml` carried `turn_radius_m` per aircraft (load-bearing for the
  Reeds–Shepp tow-path planner since Phase 3a, ADR-0007/ADR-0010). No wheel
  positions.
- `src/hangarfit/visualize.py` invented wheel-glyph positions on the fly from
  heuristic fractions of fuselage half-length. The module itself flagged the
  fractions as "intentionally approximate".

There was no link between the two: a contributor could change `turn_radius_m`
without touching the rendered wheelbase, or vice versa, and nothing noticed.

A separate latent inconsistency: the `fleet.yaml` header asserted *"Origin =
main-gear / cart centroid"*, which the data did not honor — visualize.py drew
mains offset from origin, and existing fleet entries followed that convention.

## Decision

Make wheel positions **first-class per-aircraft data** in `fleet.yaml`. Render
from the data; cross-check `turn_radius_m` against the wheel-derived wheelbase
at load time; reconcile the origin documentation.

Schema (β — compact, symmetry-enforcing):

```yaml
wheels:
  main_offset_x_m: <float>
  track_m: <float>                 # tricycle/tailwheel only
  third_wheel_offset_x_m: <float>  # tricycle/tailwheel only; sign by gear
```

Cross-check: `0.5 × wheelbase ≤ turn_radius_m ≤ 5 × wheelbase` (own-gear,
non-monowheel only; hard error on violation).

`turn_radius_m` remains an **independent empirical value** — not derived from
wheel geometry. The cross-check is a sanity guard, not a derivation.

## Alternatives considered

- **Render-only (option A):** Wheels become render data; no cross-check. Cheap,
  unblocks #321 cleanly. Rejected because it doesn't resolve the "two
  representations disagree" framing — drift could still accumulate silently.
- **Collision participation (option C):** Wheels become Parts in the collision
  model; a wing can't overhang another plane's main gear. Rejected as
  out-of-scope for this PR; the parts-model extension and canary re-bake make
  it a separate ADR if/when wheel-pad collisions become relevant.
- **Derive `turn_radius_m` from wheelbase + steering:** Requires a per-aircraft
  `max_steer_angle_deg` field we don't have, and the real relationship is
  non-trivial (taildraggers pivot differently than tricycles). The empirical
  number we already carry is more honest than a model-derived one.
- **Explicit per-wheel `(x, y)` pairs (schema α):** Verbose; loader must
  enforce left/right symmetry by convention rather than structure.
  Rejected — the β schema documents intent (this is a wheelbase, this is a
  track) and makes left/right mismatch structurally impossible.
- **Re-anchor every aircraft to `origin = main-gear centroid` (schema γ):**
  Would honor the original header docs, but the data churn touches every
  fixture file with a hard-coded placement coordinate, for no functional gain.
  Rejected; reconciled the docs instead.

## Consequences

- visualize.py drops `_NOSE_GEAR_FRAC` / `_MAIN_GEAR_*_FRAC` / `_MAIN_GEAR_LATERAL_FRAC`
  and reduces `_draw_gear_glyph` to a loop over `aircraft.wheels.positions`.
- #321 (cart glyph at wheel positions) consumes `wheels.positions` directly;
  no new heuristic surface to maintain.
- Determinism contract unchanged — `turn_radius_m` values are not modified by
  this change, so Reeds–Shepp solutions and canary baselines are stable.
- The main-gear centroid, a useful reference point for future motion-model
  work (e.g. #263 nose-out parking), is available as
  `(aircraft.wheels.main_offset_x_m, 0)` in plane-local coords.
- Wheel-collision participation remains a future decision (see "Alternatives
  considered, option C"). If revisited, this ADR is the starting point.

## References

- #322 (this issue)
- #321 (cart glyph at wheel positions — consumes wheels)
- ADR-0001 (Aircraft parts model)
- ADR-0007 (Tow-path planner v1 scope — introduced `turn_radius_m` as load-bearing)
- ADR-0010 (Reeds–Shepp motion model)
- ADR-0012 (Fuselage front/aft split — same architectural shape: explicit data over heuristic)
- Spec: `docs/superpowers/specs/2026-05-28-wheels-canonical-design.md`
```

- [ ] **Step 3: Update the ADR index if one exists**

```bash
cat docs/adr/README.md
```

If the README lists ADRs by number, add the new entry: `- ADR-0013 — Wheels as canonical per-aircraft data`. Match the existing format.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0013-wheels-canonical-data.md docs/adr/README.md
git commit -m "docs(adr): ADR-0013 wheels as canonical per-aircraft data (#322)"
```

---

## Task 10: Final guardrail run + push + open PR

**Files:** none (CI / git only)

- [ ] **Step 1: Full guardrail run**

```bash
pytest -q
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/hangarfit/
```

Expected: all green/clean. If `ruff format --check` reports unformatted files, run `ruff format src/ tests/` and commit the format-only change as `style(format): apply ruff format (#322)`.

- [ ] **Step 2: Confirm canary fixtures untouched**

```bash
git diff develop...HEAD -- tests/test_solver_canaries.py tests/test_towplanner_*.py | wc -l
```

Expected: `0`. If non-zero, something in the implementation perturbed determinism — investigate before pushing (the design explicitly forbids canary churn).

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feature/wheels-canonical-322
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create \
  --base develop \
  --title "feat: wheel positions as canonical per-aircraft data (#322)" \
  --body "$(cat <<'EOF'
## Summary

- Adds a required `wheels:` block to every aircraft in `data/fleet.yaml` using the β-schema (`main_offset_x_m`, `track_m`, `third_wheel_offset_x_m`).
- New `Wheels` dataclass + `Aircraft.wheels` required field.
- Loader cross-checks `turn_radius_m` against wheel-derived wheelbase (0.5×–5× band; hard error; skipped for `always_cart` and monowheel).
- `visualize.py` reads wheel positions from data — `_NOSE_GEAR_FRAC` and friends are deleted.
- `fleet.yaml` header origin paragraph reconciled with the actual data convention.
- ADR-0013 records the decision.

## Determinism impact

None. `turn_radius_m` values are unchanged, so Reeds–Shepp solutions and canary baselines are stable. The `determinism-guard` subagent should confirm canaries are byte-identical.

## Spec

[`docs/superpowers/specs/2026-05-28-wheels-canonical-design.md`](docs/superpowers/specs/2026-05-28-wheels-canonical-design.md)

## Test plan

- [ ] Full `pytest -q` green on develop merge
- [ ] `ruff check` clean
- [ ] `mypy src/hangarfit/` clean
- [ ] `hangarfit check layouts/example.yaml --render /tmp/smoke.png` produces a PNG with wheels at sensible positions
- [ ] Manual eyeball of the smoke PNG vs v0.7.2's smoke (regression check on cart-glyph placement, which is untouched)

Closes #322.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Set assignee, label, milestone**

(Per the [[feedback-pr-metadata]] memory: `gh pr edit` is broken in this repo; use `gh api -X PATCH`.)

```bash
PR=$(gh pr view --json number -q .number)
gh api -X PATCH "repos/DocGerd/hangarfit/issues/$PR" \
  -f assignees='["DocGerd"]' \
  -f labels='["enhancement"]'
```

Milestone assignment: if a milestone is open that covers wheel-realism / Phase 3 follow-ups, set it. Otherwise leave unset and ask the user in the PR comment.

- [ ] **Step 6: Run `/pr-review` and surface findings on the PR**

After the PR is open, invoke `/pr-review` (or the `pr-review-toolkit:review-pr` skill) with the reviewer set named in the spec §6:

- `geometry-invariant-guard` (defense in depth — geometry coords flow through `local_to_world`)
- `determinism-guard` (sanity check — towplanner.py is untouched, canaries should be byte-identical)
- `pr-review-toolkit:code-reviewer`
- `pr-review-toolkit:type-design-analyzer` (new `Wheels` dataclass)
- `pr-review-toolkit:silent-failure-hunter` (loader gained error paths)
- `pr-review-toolkit:comment-analyzer` (fleet.yaml header + ADR)

Convert every finding into a review thread on the diff. Resolve every thread (fix the code, or reply with rationale + mark resolved). Tell the user the PR is **clean and ready for final review** when threads are closed. **Never `gh pr merge` from this session.**

---

## Notes on what is deliberately NOT in this plan

- **Canary regeneration.** The design predicts no canary churn. If Task 10 Step 2 ever fails, stop and surface — that's a design-violation signal, not a routine task.
- **`gh pr merge`.** The user is the only merger.
- **Cart glyph relocation.** That's #321; do not touch `_draw_cart_glyph` or `_CART_DECK_*` constants in this PR.
- **Real fleet measurements.** Backfill uses estimates from public specs; the `measured: false` flag stays. Real measurements are #79.
