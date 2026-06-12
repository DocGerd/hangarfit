# #603 — Caddy HARD clear-egress (drive-out routability) gate

**Date:** 2026-06-11 (design); **revised 2026-06-12** — collapsed to a single
clear-egress routability oracle after real-data falsified the geometric
nearest-door rule (see §2).
**Issue:** #603 (Stage A / Epic #600, milestone #34).
**Branch:** `feature/603-caddy-egress` (off `develop`, after #602 merged).
**Builds on:** #595/#601 (ground-object model + loader), #605/#611 (`vw_caddy`
catalog entry + `layout_full.yaml`), **#602** (the Caddy car motion model + the
`Aircraft | GroundObject`-generalized `plan_path`/`path_first_conflict` the egress
oracle reuses).
**Status:** design **approved** 2026-06-12 (routability-only).

---

## 1. Scope (this PR)

The VW Caddy is the club's **emergency-egress** rescue vehicle: it must be able to
**drive out the door** in an emergency, against everything else parked. A layout
that traps it is operationally useless and must be **rejected**.

This PR adds **one** HARD rule to the deterministic verifier: a **clear-egress
routability** gate. For any object the catalog flags a **hard-door mover**, the
planner must be able to route it from its parked pose out through the door against
the **full parked scene**; if it cannot, the layout is **tow-unroutable (exit 3)**
— the same tier as an un-tow-routable aircraft, named on stderr.

**Two-oracle → one-oracle.** The original design (two-oracle: a geometric
"nearest-door" `Conflict` in `collisions.check` at exit 2 + a clear-egress
routability oracle at exit 3) was **collapsed to routability-only** after the
geometric rule was falsified by real data (§2). "Nearest the door" survives only
as a **SOFT** preference — already filed as **#614** (door-priority tie-breaker).

**Out of scope (siblings / deferred):** the Caddy motion model (#602, consumed);
the catalog taxonomy (#595/#601); the fuel-trailer keep-out + glider-trailer soft
region (#601/#604); the SOFT door-priority term (#614); making `layout_full`'s
Caddy actually egress-routable (the packing wall — Stage C, see §2); any ML.

## 2. Decisions locked (this session) — with the falsifying evidence

| Decision | Choice | Evidence / rationale |
|---|---|---|
| **HARD rule shape** | **Clear-egress routability only** (exit 3). No geometric nearest-door `Conflict`. | A "front-most among all routable bodies in the door window" rule is **unachievable in a packed hangar** and **false-fires on the real `layout_full.yaml`**: `cessna_140` (min-y 0.01) and `ctsl` (0.03) sit forward of the Caddy (5.82); even within the Caddy's own x-lane, `cessna_140` is in front. Aircraft noses live at y≈0, so the Caddy can never be the absolute front-most body. The routability check is the **precise operational gate** and does not false-fire. |
| **"Nearest the door"** | A **SOFT** preference → **#614**, not a HARD gate. | Hard front-most is unachievable; the soft door-priority term (filed) captures "the Caddy should be near the door" without rejecting valid layouts. |
| **Reporting tier** | **Exit 3** (tow-unroutable), via the existing `NoFeasiblePlanError` → `solver` → stderr path. | `collisions.check` cannot import `towplanner` (import cycle), and the egress is inherently a routability question — it belongs in the routability oracle, exactly like aircraft tow-routability. |
| **`layout_full.yaml`** | **Ship the machinery; the oracle correctly reports the Caddy egress-blocked.** Do **not** modify `layout_full`. | A bounded scan of 96 door-adjacent Caddy poses found **2** collision-free and **0** egress-routable — the #599 packing wall. Making the Caddy egress-routable needs re-nesting (Stage C / the learned backend). #603 delivers the deterministic verifier the learned backend validates against; the blocked Caddy is a **true finding**, documented. |

## 3. Substrate (post-#602, verified)

- **Exit-3 contract:** `cli.py` returns exit 3 when `--render-paths` and every
  `result.plans` entry is `None`; `_warn_unroutable()` prints
  `"warning: layout N: no feasible tow path (plane X could not be routed) …"` per
  un-routed layout. `solver._tow_plan_layouts()` calls `plan_fill` per layout,
  catches `NoFeasiblePlanError`, appends `None`, records the blocking id in
  `SolverDiagnostics.unroutable_planes`.
- **#602 routing oracle (reused unchanged):** `plan_path(mover: Aircraft |
  GroundObject, entry, goal, *, hangar, placed, mover_on_carts, entries, …)`;
  `path_first_conflict` re-injects the mover into the per-sample `Layout` (a GO
  mover → `ground_object_placement`). `NoFeasiblePlanError(plane_id, conflict)`.
  `entry_poses(slot, hangar)` → door-cone; `Pose` lives in `towplanner`.
- **Reeds–Shepp reversibility (ADR-0010):** an egress (slot → out the door) is
  feasible **iff** an entry (door → slot) path exists against the same obstacles.
  So the egress oracle reuses `plan_path` (door-cone → Caddy slot) against the
  **full parked scene minus the Caddy** (the Caddy is re-injected per-sample).
- **`GroundObject`** (post-#602): `id, name, parts, object_class, motion_mode,
  turn_radius_m, measured` + the #602 radius guards. `_ALLOWED_MOVER_KEYS` in
  `loader.py`; `_build_mover` reads mover keys via `_to_bool`/`_to_float`.

## 4. Architecture / components

### 4.1 `models.py` — `GroundObject.hard_door_mover: bool = False`

New field after `measured` (last field, default `False` → byte-identical).
`__post_init__` guard (after the towed-forbids-radius guard): if `hard_door_mover`
and `object_class != "placed_routed_mover"` → `ValueError` (only a mover may be a
hard-door mover; a `fixed_obstacle` must not set it).

### 4.2 `loader.py` + catalog

`_ALLOWED_MOVER_KEYS += "hard_door_mover"`; `_build_mover` reads
`hard_door_mover=_to_bool(entry.get("hard_door_mover", False), "hard_door_mover")`.
`data/catalog/vw_caddy.yaml`: add `hard_door_mover: true`. **Data-driven, never
hardcoded** — a layout with no hard-door mover is byte-identical (the oracle is
skipped entirely).

### 4.3 `towplanner.py` — the clear-egress routability oracle

```python
def egress_first_conflict(target: Layout, mover_id: str, *,
                          heuristic="grid", max_expansions=None) -> Conflict | None:
    """First conflict blocking <mover_id>'s drive-OUT through the door, else None.

    By Reeds-Shepp reversibility (ADR-0010) an egress is feasible iff an entry
    (door-cone -> slot) path exists against the FULL parked scene. Reuses #602's
    plan_path with the mover routed as a GroundObject; the mover is EXCLUDED from
    `placed` (path_first_conflict re-injects it per sample). Honors the fuel-trailer
    keep-out + every other parked body. Closed-form, RNG-free."""
```
- Build `placed` = `target` with the mover removed from
  `ground_object_placements` (kept in `ground_objects`). `cone =
  entry_poses(mover_slot, hangar)`. `plan_path(mover, cone[0],
  Pose.from_placement(mover_slot), …, placed=placed, mover_on_carts=False,
  entries=cone)`. Catch `NoFeasiblePlanError` → return its `conflict` (kind
  `caddy_egress`); success → `None`.

### 4.4 `solver.py` — wire the oracle into the exit-3 path

In `_tow_plan_layouts()`, **after** a layout's `plan_fill` succeeds: for each
ground object with `hard_door_mover=True`, call `egress_first_conflict`. If it
returns a conflict, treat the layout as un-routable — append `None` to `plans`,
record the mover id in `unroutable` (→ exit 3 + the existing stderr warning). The
loop body runs **only** when a hard-door mover is present ⇒ inert / byte-identical
otherwise (no extra `plan_path` calls, no plan change).

### 4.5 Docs

- **New ADR-0026** (`docs/adr/0026-caddy-hard-door-egress.md`): the clear-egress
  routability gate (exit 3); **why the geometric nearest-door rule was rejected**
  (falsified by `layout_full` — record the evidence); "nearest the door" is SOFT
  (#614); the `layout_full` Caddy is a documented known-hard case (packing wall,
  Stage C). Stays under ADR-0003 (deterministic, closed-form).
- **arc42 §8** Crosscutting Concepts: a short entry by the maintenance-bay /
  structural-notch rules describing the Caddy hard-door **egress-routability** gate
  (a routability/exit-3 rule, distinct from the static keep-outs).
- **CLAUDE.md** Quick Reference: a pointer row.

## 5. Determinism (ADR-0003)

- The egress oracle is closed-form (reuses `plan_path`/`path_first_conflict`); no
  new RNG, fixed cone/primitive order.
- **Inert when no hard-door mover:** `_tow_plan_layouts` skips the oracle entirely
  ⇒ `plans` + `diagnostics` byte-identical to pre-#603 (assert with `==`).
- `determinism-guard` is run (touches `towplanner.py` + `solver.py`).

## 6. Testing

- **Egress oracle (synthetic, `test_towplanner_*`):**
  - **clear** — a hard-door mover with an unobstructed door corridor →
    `egress_first_conflict` returns `None` (one `@slow` route + one fast unit).
  - **blocked** — a body parked across the mover's only corridor →
    `egress_first_conflict` returns a `caddy_egress` conflict.
  - The fuel-trailer fixed keep-out is honored as a corridor obstacle.
- **Solver exit-3 integration:** a small scenario whose hard-door mover cannot
  egress → `solve(..., plan_paths=True)` yields a `None` plan + the mover id in
  `diagnostics.unroutable_planes` (and the CLI returns exit 3 under
  `--render-paths`).
- **Inert canary (non-slow):** a scenario with **no** hard-door mover →
  `_tow_plan_layouts` / `plan_fill` output byte-identical to pre-#603.
- **Real-data finding:** a test asserts the oracle **correctly classifies
  `examples/herrenteich/layout_full.yaml`'s Caddy as egress-blocked** (documents
  the packing-wall finding; proves the machinery runs on the real set).

## 7. Non-goals

- The Caddy motion model (#602, consumed). The catalog taxonomy (#595/#601).
- The fuel-trailer keep-out + glider-trailer soft region (#601/#604).
- The **geometric nearest-door** rule — **rejected** (falsified); "near the door"
  is the SOFT **#614** term.
- Making `layout_full`'s Caddy egress-routable (re-nesting / Stage C).
- ADR-0008 spread; any ML.

## 8. Acceptance criteria

- [ ] `GroundObject.hard_door_mover` flag + `__post_init__` guard (only movers) +
      loader wiring + `vw_caddy.yaml` sets it true.
- [ ] `egress_first_conflict` routes the mover out via #602's `plan_path` against
      the full parked scene; returns a `caddy_egress` conflict when blocked, `None`
      when clear; honors the fuel-trailer keep-out.
- [ ] Wired into `solver._tow_plan_layouts` → blocked egress surfaces as exit-3
      tow-unroutable (named on stderr) via the existing `NoFeasiblePlanError` path.
- [ ] **Inert / byte-identical** when no hard-door mover (canary); determinism-guard
      passes.
- [ ] Tests as in §6, incl. the `layout_full` correctly-flagged-blocked finding.
- [ ] ADR-0026 (records the rejected geometric rule + the packing-wall finding) +
      arc42 §8 + CLAUDE.md pointer.

## 9. Sequencing & dependencies

- Built on `feature/603-caddy-egress` off the post-#602 `develop`.
- Reuses #602's generalized `plan_path` unchanged. New ADR-0026; under ADR-0003.
- SOFT door-priority (#614) and the `layout_full` egress re-nest (Stage C) are
  separate.
