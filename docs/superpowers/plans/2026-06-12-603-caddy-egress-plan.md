# #603 Caddy clear-egress routability gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A hard-door mover (the rescue Caddy) must be able to drive OUT the door against the full parked scene; if it can't, the layout is tow-unroutable (exit 3). One oracle, no geometric nearest-door check.

**Architecture:** Per the revised spec (`docs/superpowers/specs/2026-06-11-603-caddy-egress-verifier-design.md`), the geometric nearest-door rule was dropped (falsified by `layout_full.yaml`). The HARD gate is a routability oracle `egress_first_conflict()` that reuses #602's generalized `plan_path` (Reeds–Shepp reversibility: egress feasible iff a door→slot entry path exists against the full parked scene). It's wired into `solver._tow_plan_layouts` via the existing `NoFeasiblePlanError` → exit-3 path. A per-object `hard_door_mover` flag gates it (data-driven; inert/byte-identical otherwise).

**Tech stack:** Python 3.12, frozen dataclasses, shapely, pytest. Closed-form + RNG-free.

**Branch:** `feature/603-caddy-egress` (off post-#602 `develop`; spec committed at `6d5c396`).

---

## File / change map

| File | Change |
|---|---|
| `src/hangarfit/models.py` | `GroundObject.hard_door_mover: bool = False` + `__post_init__` guard (fixed_obstacle may not set it). |
| `src/hangarfit/loader.py` | `_ALLOWED_MOVER_KEYS += "hard_door_mover"`; `_build_mover` reads it via `_to_bool`. |
| `data/catalog/vw_caddy.yaml` | add `hard_door_mover: true`. |
| `src/hangarfit/towplanner.py` | new `egress_first_conflict(target, mover_id, ...) -> Conflict | None`. |
| `src/hangarfit/solver.py` | wire the egress gate into `_tow_plan_layouts` (import `egress_first_conflict`). |
| `docs/adr/0026-caddy-hard-door-egress.md` | new ADR (records the rejected geometric rule + the packing-wall finding). |
| `docs/architecture/08-crosscutting-concepts.md` | arc42 §8 entry. |
| `CLAUDE.md` | Quick-Reference pointer row. |
| `tests/test_models_ground_object.py`, `tests/test_loader_catalog.py` | flag + guard + loader tests. |
| `tests/test_towplanner_mover_routing.py` | egress oracle tests (clear/blocked, @slow + fast). |
| `tests/test_solver_towplanner.py` (or similar) | solver exit-3 integration + inert canary. |
| `tests/test_herrenteich_dataset.py` | the `layout_full` correctly-flagged-blocked finding. |

---

## Task 1: `hard_door_mover` flag (model + loader + catalog)

**Files:** `src/hangarfit/models.py`, `src/hangarfit/loader.py`, `data/catalog/vw_caddy.yaml`; tests in `tests/test_models_ground_object.py` + `tests/test_loader_catalog.py`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_models_ground_object.py
def test_mover_can_be_hard_door_mover() -> None:
    m = GroundObject(id="caddy", name="c", parts=(_rect_part(),),
                     object_class="placed_routed_mover", motion_mode="steerable",
                     turn_radius_m=5.5, hard_door_mover=True)
    assert m.hard_door_mover is True

def test_hard_door_mover_defaults_false() -> None:
    m = GroundObject(id="tr", name="t", parts=(_rect_part(),),
                     object_class="placed_routed_mover", motion_mode="towed")
    assert m.hard_door_mover is False

def test_fixed_obstacle_rejects_hard_door_mover() -> None:
    with pytest.raises(ValueError, match="hard_door_mover"):
        GroundObject(id="obst", name="o", parts=(_rect_part(),),
                     object_class="fixed_obstacle", hard_door_mover=True)
```
```python
# tests/test_loader_catalog.py
def test_mover_hard_door_mover_loaded() -> None:
    raw = {"type": "car", "id": "c1", "name": "Car", "parts": [
        {"kind": "ground", "length_m": 4.0, "width_m": 1.8, "offset_x_m": 0.0,
         "offset_y_m": 0.0, "z_bottom_m": 0.0, "z_top_m": 1.8}],
        "turn_radius_m": 5.5, "hard_door_mover": True}
    obj = _build_catalog_object(raw, source=Path("inline"))
    assert obj.hard_door_mover is True
```

- [ ] **Step 2: Run, verify FAIL** (`pytest tests/test_models_ground_object.py tests/test_loader_catalog.py -k hard_door -v`): `TypeError: unexpected keyword argument 'hard_door_mover'`.

- [ ] **Step 3: Implement.**

`models.py` `GroundObject`: add the field as the LAST field, after `measured: bool = False`:
```python
    hard_door_mover: bool = False
```
In `__post_init__`, inside the existing `if self.object_class == "fixed_obstacle":` branch (alongside the motion_mode / turn_radius_m rejections), add:
```python
            if self.hard_door_mover:
                raise ValueError(
                    f"GroundObject {self.id!r}: a fixed_obstacle must not set "
                    f"hard_door_mover=True — only a placed_routed_mover may be a "
                    f"hard-door (egress) mover"
                )
```

`loader.py`: extend the whitelist and read the key in `_build_mover`:
```python
_ALLOWED_MOVER_KEYS = frozenset(
    {"id", "name", "parts", "measured", "motion_mode", "turn_radius_m", "hard_door_mover"}
)
```
and in the `GroundObject(...)` constructed by `_build_mover`, add:
```python
        hard_door_mover=_to_bool(entry.get("hard_door_mover", False), "hard_door_mover"),
```

`data/catalog/vw_caddy.yaml`: add after `measured: false`:
```yaml
hard_door_mover: true    # rescue vehicle: must keep a clear drive-out egress (#603)
```

- [ ] **Step 4: Run + verify PASS + suite slice + types.**
`pytest tests/test_models_ground_object.py tests/test_loader_catalog.py tests/test_loader.py tests/test_herrenteich_dataset.py -q` ; `mypy src/hangarfit/` ; `ruff check src/ tests/`.
(`_ALLOWED_FIXED_OBSTACLE_KEYS` does NOT include the key, so a `fixed_obstacle` YAML with `hard_door_mover` is rejected by the loader allowlist before the model guard — fine; add a loader-rejection test if convenient.)

- [ ] **Step 5: Commit** (stage `models.py`, `loader.py`, `vw_caddy.yaml`, the two test files):
```bash
git commit -m "feat(603): GroundObject.hard_door_mover flag + loader/catalog wiring

Refs #603"
```

---

## Task 2: the `egress_first_conflict` routability oracle

**Files:** `src/hangarfit/towplanner.py`; test `tests/test_towplanner_mover_routing.py`.

- [ ] **Step 1: Failing tests** (reuse the file's `_hangar`/`_ground_part`/`make_test_aircraft` helpers; `egress_first_conflict` from `hangarfit.towplanner`). Build a hard-door-mover Caddy with a clear corridor (→ None) and one boxed in by an aircraft across its only corridor (→ conflict):

```python
def _caddy(hdm: bool = True) -> GroundObject:
    return GroundObject(id="caddy", name="c",
        parts=(_ground_part(length_m=4.5, width_m=1.8),),
        object_class="placed_routed_mover", motion_mode="steerable",
        turn_radius_m=5.5, hard_door_mover=hdm)

def test_egress_clear_returns_none() -> None:
    from hangarfit.towplanner import egress_first_conflict
    hangar = _hangar()
    caddy = _caddy()
    layout = Layout(fleet={}, hangar=hangar, placements=(),
        ground_objects={caddy.id: caddy},
        ground_object_placements=(Placement("caddy", x_m=10.0, y_m=6.0, heading_deg=90.0, on_carts=False),))
    assert egress_first_conflict(layout, "caddy") is None

@pytest.mark.slow
def test_egress_blocked_returns_caddy_egress_conflict() -> None:
    from hangarfit.towplanner import egress_first_conflict
    hangar = _hangar()
    ac = make_test_aircraft(id="wall")
    caddy = _caddy()
    # an aircraft slab spanning the corridor between the caddy (deep) and the door
    layout = Layout(fleet={ac.id: ac}, hangar=hangar,
        placements=(Placement("wall", x_m=10.0, y_m=4.0, heading_deg=90.0, on_carts=False),),
        ground_objects={caddy.id: caddy},
        ground_object_placements=(Placement("caddy", x_m=10.0, y_m=30.0, heading_deg=90.0, on_carts=False),))
    c = egress_first_conflict(layout, "caddy")
    assert c is not None and c.kind == "caddy_egress" and "caddy" in c.planes
```
> Pick coordinates that actually produce clear vs blocked (iterate). The clear case must keep the corridor genuinely open; the blocked case must wall it off (a wide aircraft across the full door-to-slot lane).

- [ ] **Step 2: Run, verify FAIL** (`egress_first_conflict` undefined).

- [ ] **Step 3: Implement** in `towplanner.py` (near `path_first_conflict` / `plan_path`; `Conflict` is already imported, `_MAX_EXPANSIONS`/`entry_poses`/`plan_path`/`Pose`/`NoFeasiblePlanError` are module-local):

```python
def egress_first_conflict(
    target: Layout,
    mover_id: str,
    *,
    heuristic: Literal["euclidean", "grid"] = "grid",
    max_expansions: int | None = None,
) -> Conflict | None:
    """First conflict blocking ``mover_id``'s drive-OUT through the door, else None.

    By Reeds-Shepp reversibility (ADR-0010) an egress (slot -> out the door) is
    feasible iff an entry (door-cone -> slot) path exists against the FULL parked
    scene. Reuses #602's plan_path with the mover routed as a GroundObject; the
    mover is EXCLUDED from ``placed`` (path_first_conflict re-injects it per
    sample). Honors the fuel-trailer keep-out + every other parked body.
    Closed-form, RNG-free. Returns a ``caddy_egress`` Conflict when blocked."""
    mover = target.ground_objects[mover_id]
    slot = next(gp for gp in target.ground_object_placements if gp.plane_id == mover_id)
    placed = Layout(
        fleet=target.fleet,
        hangar=target.hangar,
        placements=target.placements,
        maintenance_plane=target.maintenance_plane,
        ground_objects=target.ground_objects,
        ground_object_placements=tuple(
            gp for gp in target.ground_object_placements if gp.plane_id != mover_id
        ),
    )
    cone = entry_poses(slot, target.hangar)
    budget = _MAX_EXPANSIONS if max_expansions is None else max_expansions
    try:
        plan_path(
            mover,
            cone[0],
            Pose.from_placement(slot),
            hangar=target.hangar,
            placed=placed,
            mover_on_carts=False,
            entries=cone,
            heuristic=heuristic,
            max_expansions=budget,
        )
        return None
    except NoFeasiblePlanError as exc:
        return Conflict.single(
            kind="caddy_egress",
            plane=mover_id,
            detail=(
                f"hard-door mover {mover_id!r} cannot drive out the door "
                f"(no clear egress corridor): {exc.conflict.detail}"
            ),
        )
```

- [ ] **Step 4: Run + verify PASS + @slow + types** (`pytest tests/test_towplanner_mover_routing.py -q`, `pytest -m slow tests/test_towplanner_mover_routing.py -q`, `mypy src/hangarfit/towplanner.py`, `ruff check`).

- [ ] **Step 5: Commit** (`towplanner.py` + the test):
```bash
git commit -m "feat(603): egress_first_conflict — Caddy drive-out routability oracle

Refs #603"
```

---

## Task 3: wire the egress gate into the solver (exit 3)

**Files:** `src/hangarfit/solver.py`; tests in `tests/test_solver_towplanner.py` + `tests/test_herrenteich_dataset.py`.

- [ ] **Step 1: Failing tests.**

```python
# tests/test_solver_towplanner.py  (or the solver-tow integration file)
def test_hard_door_mover_egress_blocked_is_unroutable() -> None:
    """A layout whose hard-door mover cannot egress is recorded as un-routable
    (plans[i] is None, mover id in diagnostics.unroutable_planes)."""
    # Build a SMALL scenario (load_scenario of a fixture, or synthesize) whose
    # caddy is boxed in. solve(..., plan_paths=True). Then:
    assert result.plans[0] is None
    assert "caddy" in result.diagnostics.unroutable_planes

def test_no_hard_door_mover_tow_plan_byte_identical() -> None:
    """Inert: a scenario with no hard-door mover tow-plans byte-identically to
    pre-#603 (the egress gate never runs)."""
    a = solve(scen, seed=42, ... , plan_paths=True)
    b = solve(scen, seed=42, ... , plan_paths=True)
    assert [ (p is None, getattr(p, "moves", None)) for p in a.plans ] == [ ... b ... ]
```
```python
# tests/test_herrenteich_dataset.py
def test_full_set_caddy_egress_blocked_finding() -> None:
    """Documents the packing-wall finding: the egress oracle correctly reports
    layout_full.yaml's Caddy as egress-blocked (the rescue vehicle is boxed in;
    fixing it is gated on re-nesting / Stage C)."""
    lay = load_layout(repo_root / "examples/herrenteich/layout_full.yaml")
    from hangarfit.towplanner import egress_first_conflict
    c = egress_first_conflict(lay, "vw_caddy")
    assert c is not None and c.kind == "caddy_egress"
```

- [ ] **Step 2: Run, verify FAIL.**

- [ ] **Step 3: Implement.** In `solver.py`: import `egress_first_conflict` from `hangarfit.towplanner` (line ~51 alongside `plan_fill`). In `_tow_plan_layouts`, replace the `built.append(plan_fill(...))` direct appends with a computed `plan` + the egress gate, so a blocked egress raises through the EXISTING `except NoFeasiblePlanError` path:

```python
            if tow_heuristic == "grid" and tow_max_expansions is None:
                plan = plan_fill(layout, apron_dropped_out=layout_drops)
            else:
                plan = plan_fill(
                    layout,
                    heuristic=tow_heuristic,
                    max_expansions=tow_max_expansions,
                    apron_dropped_out=layout_drops,
                )
            # #603: a hard-door mover (e.g. the rescue Caddy) must be able to drive
            # OUT the door against the full parked scene, else the layout is
            # operationally useless -> record it un-routable (exit 3) via the same
            # NoFeasiblePlanError path as a boxed-in plane. Inert (byte-identical)
            # when no hard-door mover is present (the loop body never runs).
            for gp in layout.ground_object_placements:
                if layout.ground_objects[gp.plane_id].hard_door_mover:
                    egress = egress_first_conflict(
                        layout,
                        gp.plane_id,
                        heuristic=tow_heuristic,
                        max_expansions=tow_max_expansions,
                    )
                    if egress is not None:
                        raise NoFeasiblePlanError(gp.plane_id, egress)
            built.append(plan)
            apron_drops.extend(layout_drops)
```
(The existing `except NoFeasiblePlanError as e:` block below appends `None`, records `e.plane_id`, and logs `e.conflict.kind`/`detail` — now `caddy_egress` — unchanged.)

- [ ] **Step 4: Run + verify PASS + the broader determinism canaries** (`pytest tests/test_solver_towplanner.py tests/test_herrenteich_dataset.py -q`; `pytest -m "" tests/test_solver_canaries.py tests/test_solver_parallel.py -q`; `mypy src/hangarfit/`; `ruff check`).

- [ ] **Step 5: Commit** (`solver.py` + tests):
```bash
git commit -m "feat(603): wire Caddy egress gate into solver -> exit-3 tow-routability

Refs #603"
```

---

## Task 4: ADR-0026 + arc42 + CLAUDE.md

**Files:** `docs/adr/0026-caddy-hard-door-egress.md` (new), `docs/architecture/08-crosscutting-concepts.md`, `CLAUDE.md`.

- [ ] **Step 1: Write ADR-0026** (match the repo's ADR template — Status: Accepted, Context, Decision, Consequences). Record:
  - The HARD rule = clear-egress **routability** gate (exit 3), reusing #602's `plan_path` (Reeds–Shepp reversibility); the `hard_door_mover` data flag; inert/byte-identical otherwise.
  - **Why the geometric nearest-door rule was REJECTED:** falsified by `layout_full.yaml` (cessna_140 min-y 0.01 forward of the Caddy 5.82; front-most unachievable in a packed hangar). "Nearest the door" is the SOFT term (#614).
  - **Known-hard finding:** `layout_full`'s Caddy is fundamentally egress-blocked (96-pose scan: 0 routable); making it routable is gated on re-nesting (Stage C). The oracle correctly reports it blocked.
  - Stays under ADR-0003 (deterministic, closed-form).
- [ ] **Step 2: arc42 §8** — add a short subsection by the maintenance-bay / structural-notch keep-out rules: the Caddy hard-door **egress-routability** gate (a routability / exit-3 rule, distinct from the static keep-outs; reuses the tow planner).
- [ ] **Step 3: CLAUDE.md** — add a Quick-Reference row pointing at ADR-0026 + the arc42 entry.
- [ ] **Step 4: Commit** (the 3 docs):
```bash
git commit -m "docs(603): ADR-0026 Caddy egress gate + arc42 + CLAUDE.md pointer

Refs #603"
```

---

## Task 5: determinism + verification

**Files:** tests as needed; run the guards.

- [ ] **Step 1: Inert canary** (non-slow, ≥1 per new path): confirm a no-hard-door-mover scenario's `_tow_plan_layouts` output is byte-identical (covered by Task 3's `test_no_hard_door_mover_tow_plan_byte_identical`; ensure it's non-slow).
- [ ] **Step 2: Full suite + types + lint** (`pytest -q`; `pytest -m slow -q`; `mypy src/hangarfit/`; `ruff check src/ tests/`; `ruff format --check src/ tests/`). All green.
- [ ] **Step 3: determinism-guard** subagent (mandatory — touches `towplanner.py` + `solver.py`): fixed-seed double-solve byte-identical; confirm the egress gate is inert when no hard-door mover.
- [ ] **Step 4: Commit** any added canary:
```bash
git commit -m "test(603): egress-gate inert canary + determinism verification

Refs #603"
```

---

## Self-review (against the spec)

**Spec coverage:** flag + guard + loader/catalog → Task 1 ✓. `egress_first_conflict` (reuses #602 plan_path, full-scene, fuel-trailer keep-out, `caddy_egress` kind) → Task 2 ✓. Solver exit-3 wiring (NoFeasiblePlanError path, inert when no hard-door mover) → Task 3 ✓. ADR-0026 (rejected geometric rule + packing-wall finding) + arc42 + CLAUDE.md → Task 4 ✓. Determinism inert canary + determinism-guard → Task 5 ✓. `layout_full` correctly-flagged-blocked → Task 3 ✓.

**No geometric nearest-door builder** in `collisions.py` — correctly absent (rejected design).

**Placeholders:** Task 3's solver-integration test bodies use prose for fixture wiring (synthesize a boxed-in caddy scenario / reuse a fixture) — fill by mirroring `tests/test_solver_towplanner.py` conventions; the production code is fully specified.

**Type consistency:** `egress_first_conflict(target, mover_id, *, heuristic, max_expansions)` returns `Conflict | None`; the solver raises `NoFeasiblePlanError(gp.plane_id, egress)` consuming it; `hard_door_mover` is the field name across model/loader/catalog/solver.

**GOTCHA:** the egress oracle must EXCLUDE the mover from `placed.ground_object_placements` (path_first_conflict re-injects it per-sample) — same contract as #602's plan_fill mover routing. The `next(... if gp.plane_id == mover_id)` assumes the mover IS placed in `target`; it always is when called from `_tow_plan_layouts` (iterating `target.ground_object_placements`).
