# Empennage Tail-Surface Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (inline execution recommended — the fallout phase, Task 8, is observe-and-re-pin and needs live test output). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model each aircraft's empennage as two explicit oriented-rectangle `Part`s — a wide horizontal stabilizer (reusing the existing `tail` kind) and a thin, tall vertical fin (new `vertical_stabilizer` kind) — so the existing two-clause collision predicate catches a fin rising into the wing-nesting layer (#520) and a tailplane wider than the fuselage tube (#519), with no predicate change.

**Architecture:** Per ADR-0023. The collision predicate (`collisions._parts_conflict`) is unchanged; correctness falls out of the existing plan-view-overlap-then-z-gap rule once the parts carry honest z-extents. Per-part z expresses conventional / cruciform / T-tail configs. Blast radius: `+1 PartKind`, fleet data ×2, 2D render branch, tests, docs. **Not touched:** the predicate logic, the det(−1) transform, `geometry.py`, `solver.py`, `towplanner.py`, the loader, `scene.py`, and the viewer TypeScript (the new parts render in the 3D viewer for free via `scene.py`'s kind-pass-through + `boxMaterial`'s opaque-body fall-through — so **no `viewer.js` rebuild**).

**Tech Stack:** Python 3.12, pytest, ruff, mypy. Geometry via shapely (already wired). YAML fleet data.

**Spec:** [`docs/superpowers/specs/2026-06-08-empennage-model-design.md`](../specs/2026-06-08-empennage-model-design.md). **ADR:** [`docs/adr/0023-empennage-tail-surfaces.md`](../../adr/0023-empennage-tail-surfaces.md) (committed `02df1c1`).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/hangarfit/models.py` | `PartKind` closed set | add `"vertical_stabilizer"` to the `Literal` (line 28) |
| `src/hangarfit/visualize.py` | 2D part render switch | add a `vertical_stabilizer` branch to `_draw_part` (the `else` fails loud) |
| `src/hangarfit/collisions.py` | the predicate | **no logic**; one clarifying comment in `_is_wing_over_cockpit` |
| `src/hangarfit/metrics.py` | overhang metric | **no logic**; one comment why the fin is excluded from `_OVERHANGABLE` |
| `data/fleet.yaml` | synthetic fleet | add `tail` + `vertical_stabilizer` parts to all 9 aircraft |
| `examples/herrenteich/fleet.yaml` | real-spec fleet | add the two parts to all 8 (incl. the `stemme_s10` T-tail) |
| `tests/test_models.py` | closed-set assertion | add `"vertical_stabilizer"` to the parametrize list (line 96) |
| `tests/fuzz/geometry_strategies.py` | fuzz kind list | add `"vertical_stabilizer"` (line 54) |
| `tests/test_visualize.py` | render coverage | add a `vertical_stabilizer`-renders test |
| `tests/test_collisions.py` | golden tests | new `TestEmpennage` class (3 cases) |
| `tests/fixtures/*.yaml` | new + re-pinned fixtures | 3 new goldens; re-tune/re-pin flipped existing ones |
| `docs/architecture/08-crosscutting-concepts.md` | operational statement | 6th kind + tail-surface prose |
| `CHANGELOG.md` | breaking-change entry | list flipped fixtures |
| `examples/herrenteich/AUDIT-518.md` *(or PR body)* | fixture-flip audit record | which fixtures flipped & why |

---

## Task 1: Add the `vertical_stabilizer` PartKind

**Files:**
- Modify: `src/hangarfit/models.py:28`
- Modify: `tests/test_models.py:96`
- Modify: `tests/fuzz/geometry_strategies.py:54`

- [ ] **Step 1: Add the new kind to the closed-set test (failing first)**

In `tests/test_models.py`, change the parametrize list at line 96 from:
```python
    @pytest.mark.parametrize("kind", ["fuselage_front", "fuselage_aft", "wing", "strut", "tail"])
```
to:
```python
    @pytest.mark.parametrize(
        "kind", ["fuselage_front", "fuselage_aft", "wing", "strut", "tail", "vertical_stabilizer"]
    )
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/test_models.py -k test_all_valid_kinds_accepted -q`
Expected: FAIL on `kind="vertical_stabilizer"` — `ValueError: Part.kind must be one of [...]` (the kind isn't valid yet).

- [ ] **Step 3: Add the kind to the `PartKind` Literal**

In `src/hangarfit/models.py:28`, change:
```python
PartKind = Literal["fuselage_front", "fuselage_aft", "wing", "strut", "tail"]
```
to:
```python
PartKind = Literal["fuselage_front", "fuselage_aft", "wing", "strut", "tail", "vertical_stabilizer"]
```
(`_VALID_PART_KINDS` at line 30 auto-derives via `get_args`; no other models.py change.)

- [ ] **Step 4: Keep the fuzz strategy in sync**

In `tests/fuzz/geometry_strategies.py:54`, change:
```python
_PART_KINDS = ["fuselage_front", "fuselage_aft", "wing", "strut", "tail"]
```
to:
```python
_PART_KINDS = ["fuselage_front", "fuselage_aft", "wing", "strut", "tail", "vertical_stabilizer"]
```

- [ ] **Step 5: Run to confirm green**

Run: `pytest tests/test_models.py -q`
Expected: PASS (all kinds, incl. `vertical_stabilizer`, accepted; the invalid-kind test still rejects `"fuselage"`/bogus kinds).

- [ ] **Step 6: Commit**

```bash
git add src/hangarfit/models.py tests/test_models.py tests/fuzz/geometry_strategies.py
git commit -m "feat(models): #520 add vertical_stabilizer PartKind (fin)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Render `vertical_stabilizer` in the 2D visualizer

The `_draw_part` switch (`visualize.py:371-434`) raises `ValueError` on any unhandled kind (closed-type fail-loud). The fin needs a branch. Render it as an **opaque, ink-edged thin polygon at `zorder=3`** (above wings) — it is a solid obstacle that pokes up into/above the wing layer, so drawing it on top is the height cue.

**Files:**
- Modify: `src/hangarfit/visualize.py` (`_draw_part`, the `elif` chain)
- Modify: `tests/test_visualize.py` (mirror the existing `test_tail_kind_renders_without_exception`)

- [ ] **Step 1: Write the failing render test**

In `tests/test_visualize.py`, next to `TestDrawPartHandlesTailKind` (≈ line 458), add:
```python
    def test_vertical_stabilizer_kind_renders_without_exception(self) -> None:
        """A vertical_stabilizer (fin) part renders via its own branch, not
        the fail-loud else (#520)."""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from hangarfit.geometry import WorldPart
        from shapely.geometry import Polygon

        fig, ax = plt.subplots()
        fin = WorldPart(
            polygon=Polygon([(0, 0), (0.15, 0), (0.15, 1.2), (0, 1.2)]),
            z_bottom_m=1.5,
            z_top_m=2.4,
            plane_id="p",
            kind="vertical_stabilizer",
        )
        _draw_part(ax, fin, "#0079B5")  # must not raise
        plt.close(fig)
```
> Adjust the imports/`_draw_part` reference to match the file's existing test idiom (the tail test next to it shows the exact pattern — mirror it, including how `_draw_part` is imported and how `WorldPart` is built).

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/test_visualize.py -k vertical_stabilizer -q`
Expected: FAIL — `ValueError: _draw_part: unhandled part kind 'vertical_stabilizer'`.

- [ ] **Step 3: Add the render branch**

In `src/hangarfit/visualize.py` `_draw_part`, add an `elif` **before** the `else:` (after the `strut` branch):
```python
    elif part.kind == "vertical_stabilizer":
        # The fin: a thin centreline surface that rises into / above the wing
        # layer (ADR-0023). Drawn opaque and ink-edged on top (zorder above the
        # wing) so its height — invisible in a top-down view — reads as "this
        # pokes up through the wing band."
        patch = MplPolygon(
            coords,
            closed=True,
            facecolor=color,
            edgecolor=_INK_EDGE,
            alpha=_FUSELAGE_ALPHA,
            lw=0.5,
            zorder=4,
        )
```
Also update the `_draw_part` docstring's part-by-part list to mention the fin (one clause).

- [ ] **Step 4: Run to confirm green**

Run: `pytest tests/test_visualize.py -q`
Expected: PASS (incl. the existing `test_unknown_part_kind_raises`, which uses a *bogus* kind and still raises).

- [ ] **Step 5: Commit**

```bash
git add src/hangarfit/visualize.py tests/test_visualize.py
git commit -m "feat(visualize): #520 render vertical_stabilizer (fin) as opaque top-layer cue

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Golden collision tests (inline-constructed, predicate-unchanged proof)

Three goldens built from **synthetic inline aircraft** (the `TestBayIntrusion._build_layout` pattern, `test_collisions.py:275-371`) so the geometry is fully controlled and independent of placeholder fleet data. All use `heading_deg=0.0` and axis-aligned parts so world coords are easy to reason about; **verify** each lands as intended by running `check` and inspecting `result.conflicts`, nudging offsets until exact.

These tests **pass as soon as `vertical_stabilizer` exists (Task 1)** with no predicate change — that passing IS the proof that ADR-0023 D3 needs no new branch.

**Files:**
- Modify: `tests/test_collisions.py` (new `TestEmpennage` class + a module-level `_empennage_layout` helper)

- [ ] **Step 1: Add the helper + the three tests**

Append to `tests/test_collisions.py`:
```python
class TestEmpennage:
    """ADR-0023: the empennage as explicit tail surfaces. The predicate is
    unchanged — these lock that honest tail z-extents produce the physically
    correct verdict (fin in the wing layer blocks a nest that passes over it;
    a wing that clears the fin laterally still nests; a wide tailplane clips a
    neighbour's low part)."""

    def _layout(self, *, nester_offset_y_m: float, nester_kind: str = "wing",
                nester_z: tuple[float, float] = (2.0, 2.3)):
        from hangarfit.models import (
            Aircraft, Door, Hangar, Layout, Part, Placement, Wheels,
        )

        # HOST: a parked plane with a low aft fuselage (z 0..1.5), a low wide
        # tailplane (`tail`, z 1.2..1.5), and a tall thin centreline fin
        # (`vertical_stabilizer`, z 1.5..2.4 — into the 2.0..2.3 wing band).
        host = Aircraft(
            id="host", name="Host", wing_position="high", gear="tailwheel",
            movement_mode="always_own_gear", turn_radius_m=5.0, measured=False,
            parts=(
                Part(kind="fuselage_aft", length_m=3.0, width_m=0.85,
                     offset_x_m=-1.5, offset_y_m=0.0, angle_deg=0.0,
                     z_bottom_m=0.0, z_top_m=1.5),
                Part(kind="tail", length_m=1.0, width_m=3.0,
                     offset_x_m=-2.8, offset_y_m=0.0, angle_deg=0.0,
                     z_bottom_m=1.2, z_top_m=1.5),
                Part(kind="vertical_stabilizer", length_m=1.2, width_m=0.15,
                     offset_x_m=-2.8, offset_y_m=0.0, angle_deg=0.0,
                     z_bottom_m=1.5, z_top_m=2.4),
            ),
            wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-3.0),
        )
        # NESTER: a single part whose footprint sits over the host's tail region.
        nester = Aircraft(
            id="nester", name="Nester", wing_position="high", gear="tailwheel",
            movement_mode="always_own_gear", turn_radius_m=5.0, measured=False,
            parts=(
                Part(kind=nester_kind, length_m=4.0, width_m=4.0,
                     offset_x_m=0.0, offset_y_m=0.0, angle_deg=0.0,
                     z_bottom_m=nester_z[0], z_top_m=nester_z[1]),
            ),
            wheels=Wheels(main_offset_x_m=0.0, track_m=1.8, third_wheel_offset_x_m=-3.0),
        )
        hangar = Hangar(
            length_m=40.0, width_m=20.0, door=Door(center_x_m=10.0, width_m=12.0),
            maintenance_bay=None, clearance_m=0.3, wing_layer_clearance_m=0.2,
        )
        return Layout(
            fleet={"host": host, "nester": nester}, hangar=hangar,
            placements=(
                Placement(plane_id="host", x_m=10.0, y_m=10.0, heading_deg=0.0, on_carts=False),
                Placement(plane_id="nester", x_m=10.0, y_m=10.0 + nester_offset_y_m,
                          heading_deg=0.0, on_carts=False),
            ),
            maintenance_plane=None,
        )

    def test_fin_blocks_wing_nesting(self) -> None:
        """#520 safety case: a wing footprint passing OVER the host's centreline
        fin (fin z_top 2.4 in the wing band) conflicts — silently valid today."""
        # nester directly over host (offset 0): its wing covers the fin.
        result = check(self._layout(nester_offset_y_m=0.0))
        assert not result.valid
        assert "vertical_stabilizer_wing_overlap" in _conflict_kinds(result), result.conflicts

    def test_wing_clears_fin_laterally_is_valid(self) -> None:
        """#520 nuance: shift the nester wing so its footprint passes OUTBOARD
        of the thin centreline fin (no plan-view overlap with the fin) -> still
        valid (the lateral-clearance pass-through)."""
        # Tune offset so the wing footprint clears the fin in plan view but not
        # the (low, z-disjoint) tail/aft fuselage. Verify by inspecting conflicts.
        result = check(self._layout(nester_offset_y_m=6.0))
        assert result.valid, result.conflicts

    def test_wide_tailplane_clips_neighbour_low_part(self) -> None:
        """#519: a neighbour's low part (here a fuselage_aft at z 0..1.5) that
        overlaps the host's now-realistic ~3 m tailplane in plan view at a
        shared z-band conflicts (free space under the old narrow model)."""
        result = check(self._layout(
            nester_offset_y_m=2.5, nester_kind="fuselage_aft", nester_z=(0.0, 1.5)))
        assert not result.valid
        assert any(k.endswith("_tail_overlap") for k in _conflict_kinds(result)), result.conflicts
```

- [ ] **Step 2: Run the three tests and TUNE offsets to land exactly**

Run: `pytest tests/test_collisions.py::TestEmpennage -q`
Expected after tuning: PASS. If a case doesn't land:
- Print `[(c.kind, c.detail) for c in check(...).conflicts]` to see what overlapped.
- For `test_fin_blocks...`: the nester wing (4×4 at offset 0) must cover the host fin at host-local `offset_x=-2.8`. If the world overlap misses, adjust `nester_offset_y_m` toward the host tail.
- For `test_wing_clears_fin_laterally...`: increase `nester_offset_y_m` until the wing footprint no longer overlaps the fin **and** check reports `valid` (no tail/fin conflict). The fin is thin (0.15 m on centreline), so a lateral shift clears it while the low tail stays z-disjoint from the high wing.
- For `test_wide_tailplane...`: the neighbour `fuselage_aft` (z 0..1.5) must overlap the host `tail` (z 1.2..1.5 → z-overlap) in plan view.
> These offsets are tuned by observation exactly as the existing wing-over-tail fixtures were; the det(−1) transform makes hand-prediction error-prone, so iterate against `check` output.

- [ ] **Step 3: Commit**

```bash
git add tests/test_collisions.py
git commit -m "test(collisions): #518 empennage goldens — fin blocks nest, lateral clear, wide tailplane

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add tail surfaces to `data/fleet.yaml` (all 9 aircraft)

Insert the two parts **after the `wing` part** in each aircraft's `parts:` list (before `struts:` where present). Values below are derived from each plane's real fuselage/wing geometry: `tail`/`vertical_stabilizer` `offset_x_m` ≈ aft-fuselage end + chord/2; tailplane z held below `wing.z_bottom_m − 0.2` (stays overhangable); fin z from the fuselage top up to the published overall height. All `measured: false` (placeholders).

**Files:**
- Modify: `data/fleet.yaml`

- [ ] **Step 1: Add a header comment** near the top of `data/fleet.yaml` documenting the addition:
```yaml
# Empennage (ADR-0023, #518/#519/#520): each aircraft carries a `tail` (horizontal
# stabilizer/elevator — wide, low for conventional/cruciform tails) and a
# `vertical_stabilizer` (fin+rudder — thin, tall, rising to the published overall
# height into the wing layer). Spans/chords are published-spec-absent estimates;
# tail configs + overall heights are sourced. All measured: false.
```

- [ ] **Step 2: Insert the two parts per aircraft** (exact blocks):

`scheibe_falke`:
```yaml
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
```
`aviat_husky`: tail `0.9?`→ `length_m: 1.0, width_m: 3.0, offset_x_m: -4.66, z 1.2..1.5`; fin `length_m: 1.3, width_m: 0.15, offset_x_m: -4.51, z 1.5..2.01`.
`fuji`: tail `length_m: 1.1, width_m: 3.2, offset_x_m: -3.84, z 1.3..1.6`; fin `length_m: 1.3, width_m: 0.15, offset_x_m: -3.74, z 1.6..2.02`.
`wild_thing`: tail `length_m: 0.9, width_m: 2.9, offset_x_m: -3.18, z 1.1..1.4`; fin `length_m: 1.0, width_m: 0.15, offset_x_m: -3.13, z 1.4..1.9`.
`zlin_savage`: tail `length_m: 0.9, width_m: 2.8, offset_x_m: -4.35, z 1.2..1.5`; fin `length_m: 1.1, width_m: 0.15, offset_x_m: -4.25, z 1.5..2.03`.
`cessna_140`: tail `length_m: 1.1, width_m: 3.2, offset_x_m: -4.37, z 1.2..1.5`; fin `length_m: 1.2, width_m: 0.15, offset_x_m: -4.32, z 1.5..1.91`.
`cessna_150`: tail `length_m: 1.1, width_m: 3.4, offset_x_m: -3.06, z 1.2..1.5`; fin `length_m: 1.4, width_m: 0.15, offset_x_m: -2.91, z 1.5..2.11`.
`ctsl` (cruciform — tailplane low/mid, kept below wing layer): tail `length_m: 0.9, width_m: 2.6, offset_x_m: -3.18, z 1.1..1.4`; fin (tall, 2.34) `length_m: 1.2, width_m: 0.15, offset_x_m: -3.03, z 1.4..2.34`.
`fk9_mkii`: tail `length_m: 0.9, width_m: 3.2, offset_x_m: -2.77, z 1.1..1.4`; fin `length_m: 1.1, width_m: 0.15, offset_x_m: -2.67, z 1.4..2.15`.

> Use the same YAML shape as the `scheibe_falke` block for every aircraft, substituting the values above. `offset_y_m: 0.0` and (implicit) `angle_deg: 0` for all.

- [ ] **Step 3: Confirm the fleet loads**

Run: `python -c "from hangarfit.loader import load_fleet; f = load_fleet('data/fleet.yaml'); [print(k, [p.kind for p in a.parts]) for k,a in f.items()]"`
> (Use the actual loader entry point — check `loader.py` for `load_fleet`/`_load_fleet`; if the only public path is `load_layout`, load a fixture instead.)
Expected: each aircraft now lists `... wing, tail, vertical_stabilizer (, strut, strut)`. No `LoaderError`.

- [ ] **Step 4: Do NOT commit yet** — committing happens after Task 8 re-pins the fallout, so the data + the fixed expectations land together. Proceed to Task 5.

---

## Task 5: Add tail surfaces to `examples/herrenteich/fleet.yaml` (all 8, incl. the T-tail)

Same shape. Aircraft shared with `data/` (`aviat_husky`, `zlin_savage`, `cessna_140`, `ctsl`, `fk9_mkii`) use the **same** values as Task 4. Herrenteich-specific:

**Files:**
- Modify: `examples/herrenteich/fleet.yaml`

- [ ] **Step 1: Insert per aircraft.** Shared five → Task 4 values. Herrenteich-specific:

`scheibe_falke` (fuselage 7.58×1.1): tail `length_m: 0.9, width_m: 2.6, offset_x_m: -3.34, z 1.2..1.5`; fin `length_m: 1.1, width_m: 0.15, offset_x_m: -3.24, z 1.5..1.68`.
`wild_thing` (fuselage 6.49): tail `length_m: 0.9, width_m: 2.9, offset_x_m: -3.13, z 1.1..1.4`; fin `length_m: 1.0, width_m: 0.15, offset_x_m: -3.08, z 1.4..1.9`.
`stemme_s10` (**T-tail** — tailplane at the fin top, in/near the wing band; fuselage 8.42×1.18, overall height 1.80):
```yaml
      - kind: tail
        # T-tail: horizontal stabilizer sits HIGH, at the fin top (ADR-0023).
        length_m: 0.8
        width_m: 2.8
        offset_x_m: -5.31
        offset_y_m: 0.0
        z_bottom_m: 1.5
        z_top_m: 1.8
      - kind: vertical_stabilizer
        length_m: 1.0
        width_m: 0.15
        offset_x_m: -5.21
        offset_y_m: 0.0
        z_bottom_m: 1.4
        z_top_m: 1.8
```
> The folded Stemme's published height (1.80 m) is below the 2.0 m wing layer, so its real tail does not actually block a neighbour's high wing — that is honest. The T-tail *conflict* is demonstrated by the synthetic golden (Task 3), not this real plane.

- [ ] **Step 2: Confirm it loads** (same command as Task 4 Step 3, on `examples/herrenteich/fleet.yaml`). No commit yet.

---

## Task 6: Audit the collisions predicate + metrics (comments only)

- [ ] **Step 1: Add a clarifying comment in `collisions.py`** at `_is_wing_over_cockpit` (line 315) confirming the fin is deliberately *not* in the cockpit exception:
```python
    # NB: only fuselage_front (cockpit) drops the height clause. A
    # vertical_stabilizer (fin) is NOT here on purpose (ADR-0023): it keeps the
    # uniform two-clause rule, so a wing conflicts with a fin only when it both
    # overlaps the thin centreline fin in plan view AND their z-bands meet —
    # i.e. wing-over-tail stays legal iff the wing clears the fin laterally.
```

- [ ] **Step 2: Add a comment in `metrics.py`** at `_OVERHANGABLE` (line 20) noting the fin's exclusion:
```python
# (vertical_stabilizer is deliberately absent: a wing over a fin is a conflict,
# not an overhang — ADR-0023.)
```

- [ ] **Step 3: Run the metrics + collisions suites** to confirm no logic regressed:

Run: `pytest tests/test_metrics.py tests/test_collisions.py -q`
Expected: `TestEmpennage` green; **other failures here are the intended fallout** addressed in Task 8 (the inline metrics tests use their own fixtures — note any that flip).

---

## Task 7: Update arc42 §8 "The parts model"

**Files:**
- Modify: `docs/architecture/08-crosscutting-concepts.md`

- [ ] **Step 1: The PartKind set statement (line 74-75).** Change:
```
The closed set of `PartKind` values is `{"fuselage_front",
"fuselage_aft", "wing", "strut", "tail"}`.
```
to:
```
The closed set of `PartKind` values is `{"fuselage_front",
"fuselage_aft", "wing", "strut", "tail", "vertical_stabilizer"}`.
The empennage is now two explicit surfaces (ADR-0023): `tail` (the
horizontal stabilizer — wide, usually low) and `vertical_stabilizer`
(the fin + rudder — thin, tall, rising into the wing layer), no longer
folded into `fuselage_aft`.
```

- [ ] **Step 2: The front/aft prose (lines 26-27).** Change `fuselage_aft` (cabin-aft + tail)` to `fuselage_aft` (cabin-aft tube)` and add one sentence: "The tail *surfaces* are now separate `tail` + `vertical_stabilizer` parts (ADR-0023), not part of `fuselage_aft`."

- [ ] **Step 3: Add a short empennage paragraph** after the PartKind-set paragraph explaining the fin's role in nesting (legal iff the wing clears the fin laterally; conventional/cruciform/T-tail expressed by per-part z). Keep it ≤ 6 lines; link ADR-0023.

- [ ] **Step 4: Touch the "aft fuselage / tail" mentions** (caption ~line 72, fleet note ~line 98-99, symmetry note ~line 428) so they don't imply the tail is *inside* `fuselage_aft` — re-word to "aft fuselage (and the low `tail` surface)" where the height-disjoint pass-through is described, and note the fin is the exception that can block it.

- [ ] **Step 5: Commit the docs** (with Tasks 1-3 already committed; this can be its own commit):
```bash
git add docs/architecture/08-crosscutting-concepts.md src/hangarfit/collisions.py src/hangarfit/metrics.py
git commit -m "docs(arc42): #518 empennage tail surfaces in the parts model + predicate/metric notes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Run the full suite, triage the fallout, re-pin (the breaking change)

This is the iterative heart. Adding tail surfaces to `data/fleet.yaml` changes the geometry of every fixture's planes; existing collision goldens pin exact penetration areas / conflict-kind sets / counts and will move.

**Triage criterion for each failing test/fixture:**
- **A new conflict that is physically real** (a wing over a fin; a wide tailplane over a neighbour's low part) → the flip is the feature. Re-pin the expectation (or rename `valid_*` → `invalid_*`) and record it in the audit.
- **A `valid_*` fixture that should stay a positive control** (e.g. wing-over-tail that should still demonstrate legal nesting) → re-tune its placement so the wing clears the fin **laterally** (preserving a valid wing-over-tail-but-clears-fin control), rather than deleting the case.
- **A changed penetration `m²` / conflict count** in an `invalid_*` golden → recompute and re-pin the exact number; add the new tail-related kinds to the expected set.
- **Never** tune fin/tailplane *dimensions* to make a test pass — dimensions are fixed by Tasks 4-5; only placements/expectations move.

- [ ] **Step 1: Run the whole suite and capture failures**

Run: `pytest -q -m "" 2>&1 | tee /tmp/empennage-fallout.txt | tail -40`
(`-m ""` runs slow+serial too, so the audit sees everything.)

- [ ] **Step 2: Triage the known-likely flips first.** For each, decide per the criterion and apply:
  - `valid_wing_over_tail.yaml` / `valid_high_over_low_aft_z_disjoint.yaml`: the `ctsl` wing overhangs `fuji`'s aft — now likely covers `fuji`'s fin (z 1.6..2.02 ∩ ctsl wing 1.9..2.2) → flips invalid. **Action:** keep one as the *lateral-clearance positive control* by nudging `ctsl`'s `x_m`/`fuji`'s heading so the wing tip passes outboard of `fuji`'s centreline fin (re-verify `valid`); convert the other to `invalid_wing_over_fin.yaml` asserting `vertical_stabilizer_wing_overlap` (the #520 case on real data). Update `test_collisions.py` accordingly.
  - `invalid_wing_wing_same_height.yaml` (golden 5.54045 m²), `invalid_strut_blocks_nesting.yaml` (2.85 m²), `invalid_fuselage_*`, `invalid_wing_over_cockpit.yaml`: recompute penetration/kind-set/count with the new parts; re-pin.
  - `valid_left_side_nesting.yaml`, `valid_right_side_nesting.yaml`, `valid_all_nine_planes.yaml`: re-run; if they flip, re-tune placements to restore validity where the intent is a *valid* nest, else re-pin.
  - `examples/layouts/example.yaml`: re-run `hangarfit check examples/layouts/example.yaml`; if invalid, re-nudge placements (as #50 did) so it stays a valid demo, OR document it now requires more spacing.

- [ ] **Step 3: The Herrenteich real-layout check (spec step 4).**

Run: `hangarfit check examples/herrenteich/layout.yaml`
Record the verdict. If it flips to invalid, **do not** adjust fin heights — report it as a finding (real tight clearance vs. placeholder aggressiveness) and, if it's a real-data concern, file a follow-up issue. Note the conflict kinds in the audit.

- [ ] **Step 4: Iterate** Steps 1-2 until `pytest -q -m ""` is green, re-pinning each golden with the recomputed exact values.

- [ ] **Step 5: Write the fixture-flip audit.** Create `examples/herrenteich/AUDIT-518.md` *(or a section in the PR body)* listing every fixture/example whose validity or golden changed, the new verdict/value, and why. This satisfies the epic's "Document which currently-'valid' fixtures flip to invalid."

- [ ] **Step 6: Lint, format, type-check.**

```bash
ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/hangarfit/
```
Expected: clean. Fix any issues.

- [ ] **Step 7: Visual smoke (2D + 3D render the tail surfaces).**

```bash
hangarfit check examples/layouts/example.yaml --render /tmp/empennage-2d.png   # tail + fin drawn
hangarfit view tests/fixtures/valid_left_side_nesting.yaml -o /tmp/empennage-3d.html
```
Confirm the 2D PNG shows the thin centreline fins + wide tailplanes, and the viewer HTML builds (the fin/tail render as boxes via scene.py pass-through — no viewer rebuild needed). Surface both to the user with `SendUserFile`.

- [ ] **Step 8: Commit the data + re-pinned fixtures + audit.**

```bash
git add data/fleet.yaml examples/herrenteich/fleet.yaml tests/fixtures/ tests/test_collisions.py examples/herrenteich/AUDIT-518.md
git commit -m "feat(fleet): #519 #520 empennage tail surfaces + re-pin flipped fixtures

Adds tail (horizontal stab) + vertical_stabilizer (fin) parts to every aircraft
in data/ and examples/herrenteich/. Breaking change to the validity contract:
re-pins/renames the fixtures whose verdict or golden moved (see AUDIT-518).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: CHANGELOG + open the PR + review arc

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: CHANGELOG entry** under `## [Unreleased]`:
```markdown
### Changed
- **BREAKING (collision model): the empennage is now modelled as explicit tail
  surfaces (#518/#519/#520, ADR-0023).** Each aircraft carries a `tail`
  (horizontal stabilizer) and a new `vertical_stabilizer` (fin) part with honest
  z-extent, so the checker now rejects a wing nested over a neighbour's fin and a
  wing/strut/fuselage clipping a realistic-width tailplane — cases silently
  reported valid before. Some previously-"valid" layouts flip to invalid; see the
  PR's fixture-flip audit. The collision predicate itself is unchanged.
```

- [ ] **Step 2: Push + open the draft PR.**

```bash
git push -u origin feature/518-empennage-model
gh pr create --draft --base develop \
  --title "feat: model the empennage tail surfaces (horizontal stab + fin)" \
  --body "$(cat <<'EOF'
Closes #519
Closes #520
Refs #518

Models the empennage as two explicit oriented-rectangle Parts per ADR-0023: a
wide `tail` (horizontal stabilizer) and a thin tall `vertical_stabilizer` (fin).
The existing two-clause collision predicate is unchanged — honest z-extents make
it catch a fin in the wing layer (#520) and a tailplane wider than the fuselage
(#519). Per-part z expresses conventional / cruciform / T-tail (Stemme S10).

**Breaking change:** previously-"valid" wing-over-tail nestings that pass over a
fin, and side-by-side layouts overlapping a realistic tailplane, flip to invalid.
Fixture-flip audit: see AUDIT-518 / below.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
> `#518` is the epic — use `Refs #518` (it closes when both subs land), `Closes #519`/`Closes #520` in the body.

- [ ] **Step 3: Run the review arc** (per CLAUDE.md, every PR): `pr-review-toolkit:code-reviewer` (main pass) + `pr-review-toolkit:type-design-analyzer` (models.py changed) + `pr-review-toolkit:silent-failure-hunter` is **not** required (loader untouched) but `geometry-invariant-guard` should confirm `geometry.py`/`collisions.py` logic is untouched. Convert findings to diff threads, fix or reply+resolve, re-run if non-trivial.

- [ ] **Step 4: When the arc is clean**, `gh pr ready <n>` and tell the user it's clean and ready for final review. Do **not** merge.

---

## Self-Review (against the spec)

- **Spec coverage:** model (Task 1) ✓; data both files incl. T-tail (Tasks 4-5) ✓; predicate audited not changed (Task 6) ✓; 2D render (Task 2) + 3D via pass-through (Task 8 Step 7) ✓; metrics `_OVERHANGABLE` unchanged (Task 6) ✓; 3 golden cases (Task 3) ✓; closed-set test (Task 1) ✓; fixture-flip audit incl. Herrenteich (Task 8) ✓; §8 + ADR-0012 amendment (Task 7 / done in `02df1c1`) ✓; CHANGELOG (Task 9) ✓.
- **No-placeholder check:** all fleet values are concrete; code edits show full snippets; the only "observe-and-tune" steps (golden offsets, re-pinned penetration numbers) are inherently runtime-derived and specify the exact command + criterion, not a vague "fix it."
- **Type consistency:** `vertical_stabilizer` spelled identically in models, fuzz, visualize, tests, fleet YAML, docs. Conflict kind auto-derives `vertical_stabilizer_wing_overlap` (alphabetical: `vertical_stabilizer` < `wing`) and `..._tail_overlap` — matches the assertions in Task 3.
- **Open risk:** Task 8's blast radius is the real unknown (how many fixtures flip). Mitigated by the explicit triage criterion and inline execution with the user in the loop.
