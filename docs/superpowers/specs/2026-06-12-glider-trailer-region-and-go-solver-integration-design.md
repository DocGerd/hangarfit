# Design Spec — Glider-trailer placement + soft right-region preference (ground objects as solver-placed bodies)

**Status:** Design / planning (2026-06-12). Audience: maintainer + future contributors.
**Issue:** #604 (milestone #34, Ground objects + Herrenteich calibration — Stage A of the learned-backend initiative).
**Companion ADRs:** ADR-0003 (determinism), ADR-0008 (spread soft-preference post-pass + #320 back-bias amendment), ADR-0010 (Reeds–Shepp / cart motion + #602 towed-trailer amendment).

---

## Intent

#604 makes a **glider trailer** a first-class *placed + routed mover* and adds a **soft right-region preference** scored as a layered `_spread` post-pass term — the soft-tier sibling of the #603 hard Caddy egress gate.

The non-obvious finding that shapes this work: **the deterministic solver does not place ground objects today.** `_run_one_restart` builds every `Layout(...)` from aircraft placements only; `Scenario` carries ground-object *defs* + *ids* but no placements; the Herrenteich `scenario.yaml` lists no ground objects; the CLI `solve` path never touches them; and the #603 egress gate at `solver.py:767` is therefore inert during `solve` (solved layouts carry no `ground_object_placements`). Ground-object poses exist only in hand-authored *layout* files consumed by `check` / `view`.

So "the solver places the trailer" (#604 part A) is a **new capability built essentially from scratch**, not a tweak — and the soft region term (#604 part B) only does anything once the solver actually *moves* the trailers. This spec therefore covers **both**: threading ground objects into the RR-MC search, and the region scoring term layered on top.

The catalog objects already exist (`data/catalog/glider_trailer_1.yaml`, `glider_trailer_2.yaml` — towed carts), `collisions.check` already treats placed movers as `ground` parts (#601), and the tow planner is already polymorphic over `Aircraft | GroundObject` (#602/#603). So the routing and collision halves of part A need **no new code** — the work is solver integration + the scoring term + scenario/diagnostics plumbing.

### Locked decisions (from brainstorming, 2026-06-12)

1. **Full RR-MC integration.** GO movers are full search citizens: sampled at restart init, perturbed in the hard `_descent_step`, spread in the post-pass, routed by `plan_fill`, egress-gated by `egress_first_conflict`. Fixed obstacles enter the solve scene as authored static keep-outs (the issue requires placement "outside the fuel-trailer keep-out").
2. **Success bar = substrate + tractable demo.** Build the full machinery; verify the region pull **end-to-end** on a new small tractable fixture where the solver genuinely places + routes + right-biases the trailers. Also opt Herrenteich in, accepting that its diagnostics / egress oracle report intractability honestly if the real 8-aircraft set cannot pack routably (the #599 wall; the #603 pattern of "ship the machinery, the oracle flags it").

---

## Success definition ("best")

Lexicographic; the hard gate dominates absolutely; the soft region term only re-ranks among already-hard-valid layouts.

| Tier | Rule | Locus |
|---|---|---|
| **HARD (gate)** | Every body (aircraft + placed movers) is valid under `collisions.check` (inside hangar incl. notch, outside fixed-obstacle keep-outs, no overlap) **AND** every mover is routable (`plan_fill`) **AND** every `hard_door_mover` has a clear egress lane (`egress_first_conflict`). Violation ⇒ reject (collision-tier) / exit-3. | `collisions.check`, `towplanner` |
| **SOFT (tie-break)** | A per-object right/left region preference: a preferring object closer to its preferred wall is better. Off-side when the preferred side is geometrically impossible is acceptable, **not** a failure. Strictly secondary to `min_pairwise_gap_m` (the primary basin key, #267) and never overrides the hard gate. | `solver._region_energy` |

ADR-0008 spread + #320 back-bias remain the other secondary soft terms; the region term composes additively with them in the same `_spread` hill-climb.

---

## Architecture — ground objects as solver-placed bodies (Approach 1: unified placeable-id set)

The chosen approach is a single unified set of *placeable* bodies that **degenerates byte-identically to today when a scenario has no ground objects**.

- **Placeable ids** = `scenario.fleet_in` (aircraft) `++ sorted(mover_ids)` where `mover_ids` are the `placed_routed_mover` ground objects in the scenario. When there are no movers, this is exactly `fleet_in` and iteration order / RNG draws are unchanged.
- **`_body(scenario, id) -> Aircraft | GroundObject`** dispatch replaces every `scenario.fleet[pid]` lookup in the placement / perturbation / energy code. A parallel `_body_parts_world(...)` returns world parts for either kind (aircraft use the memoized `cached_parts_world`; movers reuse the same world-parts path the towplanner uses).
- **Fixed obstacles** are *not* placeable. They are static authored poses, injected only at `Layout` construction. They never enter the search dicts and consume no RNG.
- **Centralized `_build_layout(scenario, placements)`** splits the unified placements dict into aircraft `placements=` vs mover `ground_object_placements=`, and appends the static fixed-obstacle placements + `ground_objects=` defs. This single helper replaces the ~6 inline `Layout(...)` builds in the restart body, `_spread`, `_nose_out`, and `_descent_step`, so ground objects flow through scoring uniformly.

### The determinism contract (load-bearing invariant)

> **Ground objects absent ⇒ byte-identical to today.** No new RNG draws (no movers to sample/perturb), no new `Layout` args (empty GO fields, proven byte-identical by `test_empty_ground_objects_byte_identical`), and the region term is not added (gated on `bool(scenario.region_preferences)`). Every existing fixture/scenario/canary has no ground objects, so all current determinism canaries and the `determinism-guard` remain green untouched.
>
> **Movers present ⇒ deterministic by construction.** Mover initial poses are seeded from the same `rng` in `sorted`-id order after aircraft; perturbation uses the same fixed candidate-generation RNG order; the region term is **RNG-free** (no draws, no candidate-set change) and summed in `sorted`-id order. Same scenario + same seed ⇒ bit-identical output, `max_restarts`-scoped (ADR-0003 as amended #267/#544). A new movers-active canary pins this.

This mirrors the back-bias (#320) and priority (#441) inert contracts, applied one level up (whole-body integration rather than a single energy term).

---

## Components / changes by module

### `models.py`
- **`RegionPreference`** — new frozen dataclass: `side: Literal["left", "right"]`, `weight: float`. Validation mirrors `PlaneConstraint.priority`: `weight` finite and `≥ 0`; `side` in the allowed set. `weight == 0.0` is permitted and inert.
- **`Scenario.region_preferences: Mapping[str, RegionPreference]`** — keyed by any placeable body id (aircraft or mover); default empty `MappingProxyType({})` ⇒ inert. `__post_init__` validates every key resolves to a placeable id. General/per-object — *not* Herrenteich-hardcoded. (Rejected alternative: a `SearchConfig.region_weight` global scalar + per-object side. "Where the trailers belong" is hangar/club data, not a search knob, so it lives in the scenario, like `pin`/`priority`.)
- **`Scenario`** — distinguish mover ids (placed by the solver) from fixed-obstacle authored poses. Add `Scenario.fixed_obstacle_placements: tuple[Placement, ...]` (authored static keep-out poses; the fuel trailer's real fixed location). Mover ids derive from `ground_objects` filtered by `ground_object_defs[id].object_class == "placed_routed_mover"`.
- **`SolverDiagnostics.region_alignment`** — per-layout, per-preferring-object normalized alignment in `[0, 1]` (1.0 = at the preferred wall), index-aligned with `layouts` (mirrors `min_pairwise_gap_m`: optional, validated finite/non-NaN, `len == len(layouts)` when populated). Represented as `tuple[Mapping[str, float], ...]` (one id→alignment map per layout), or `()` when no preferences.

### `solver.py`
- **`_body` / `_body_parts_world`** dispatch helpers (Aircraft | GroundObject).
- **`_initial_placements`** samples mover poses after aircraft, in `sorted(mover_ids)` order, using the mover's part-bbox margins (movers always `on_carts=False`; excluded from `_enumerate_cart_buckets`).
- **`_perturb_plane` / `_generate_candidates` / `_descent_step`** treat movers as perturbable bodies via `_body`. The 180° flip variant applies. Cart config never perturbed (unchanged).
- **`_inter_plane_energy` / `_spread_quality` / `_back_bias_energy`** iterate the unified placeable set and look up geometry via `_body_parts_world`. (The repulsion energy now naturally repels movers from aircraft and from each other — desirable.)
- **`_region_energy(placements, scenario)`** — new, RNG-free:

  ```
  R = Σ_{o ∈ region_preferences}  weight_o · d_o ,
      where  d_o = (W − x_o) / W   if side_o == "right"     # minimized as x_o → W (right wall)
             d_o =       x_o / W    if side_o == "left"      # minimized as x_o → 0 (left wall)
      and    W   = hangar.width_m                            # normalize so one weight reads across hangar sizes
  ```

  Summed over preferring ids in `sorted` order (order-stable float sum). Folded into `_spread`'s local `_energy()` exactly like back-bias, gated `region_active = bool(scenario.region_preferences)`; when inert the term is never added (byte-identical energy).
- **`_spread`** — `movable` now includes mover ids (pinned bodies still excluded); the region term re-ranks candidates *within* the validity-gated hill-climb. `min_pairwise_gap_m` stays the **primary** cross-basin key; region is intra-basin secondary (matching back-bias — documented, not basin-selection).
- **`_build_layout(scenario, placements)`** centralizes Layout construction with GO splitting + fixed-obstacle injection.
- The #603 **egress gate now actually fires in `solve`** (movers are in the layout) → exit-3 when a `hard_door_mover` is trapped; inert when no hard-door mover present.
- **Diagnostics**: compute `region_alignment` per selected basin (RNG-free) and populate it in `_build_found_result`.

### `loader.py` + scenario/catalog
- Scenario `ground_objects:` parsing extended to author **fixed-obstacle poses** (`{object, x_m, y_m, heading_deg}`, reusing the layout GO-placement shape) and **mover entries** with an optional `region_preference: {side, weight}`. Loader allowlists extended (the hardcoded `{object, x_m, y_m, heading_deg}` frozenset at `loader.py:595` and the scenario GO parse at `loader.py:803`).
- Glider-trailer catalog objects already exist — reused as-is, no new catalog files.
- **New tractable demo fixture** under `tests/fixtures/`: a small scenario (≈2–3 aircraft + 2 glider trailers in a roomy hangar) the solver can genuinely place + route + right-bias. Herrenteich `scenario.yaml` opted in (2 trailers as movers with a right preference + fuel-trailer fixed keep-out).

### `cli.py`
- Surface `region_alignment` in the human `solve` summary (a line alongside "min gap") and in `--json` (per-layout, per-object; `null`/omitted when no preferences). Mirror the `min_pairwise_gap_m` serialization (no invalid JSON tokens).

### Docs
- **ADR-0008 amendment** (2026-06-12, #604): the right/left-region soft term — Background / Change (energy box, two-line aligned like the back-bias) / Default & toggle (per-object scenario data, default inert) / Determinism (RNG-free re-ranking) / Known limitation (a space-tight basin may keep a trailer off the preferred side; the pull is a preference, not a guarantee). Secondary to `min_pairwise_gap_m`.
- **ADR-0010** cross-reference note: movers are now *solver-placed* (towed = cart motion already per the #602 amendment; no new motion model).
- **`docs/architecture/08-crosscutting-concepts.md`** — extend the "Soft preferences" entry (region term) and the "Ground objects" entry (now solver-placed in `solve`, not only authored in layouts).
- A CLAUDE.md Quick-Ref row for the ground-object ADRs is **deferred** until the user's local `M CLAUDE.md` graphify edit is resolved (do not touch that file).

---

## Data flow (solve, with movers)

```
scenario.yaml ──load──▶ Scenario{ fleet_in, ground_objects(ids), ground_object_defs,
                                    fixed_obstacle_placements, region_preferences }
   │
   ▼  per restart (seeded)
_initial_placements ─▶ unified placements{ aircraft + movers }   (fixed obstacles static)
   │
   ▼  hard descent (movers perturbable)            ── _build_layout splits aircraft/GO,
_descent_step ──▶ valid layout (_score == (0,0.0)) ── injects fixed-obstacle keep-outs ──▶ _score
   │
   ▼  soft post-pass (validity-gated)
_spread ─▶ _energy = inter_plane + back_bias·w_b + region·(per-object)   (region RNG-free)
   │
   ▼  basin pool ── primary key min_pairwise_gap_m ── select diverse
_tow_plan_layouts ─▶ plan_fill (routes aircraft then movers, best-effort)
   │                  └─ egress_first_conflict for each hard_door_mover ─▶ exit-3 if trapped
   ▼
SolveResult{ layouts, plans, diagnostics{ min_pairwise_gap_m, region_alignment, … } }
```

---

## Error handling / edge cases
- **Unroutable mover** — best-effort today (`path=None`, does not abort the fill; #197/ADR-0007). #604 keeps that; surfacing it on stderr is the sibling #612, out of scope here.
- **Trapped hard-door mover** — exit-3 via the existing `NoFeasiblePlanError` path (now reachable in `solve`).
- **Infeasible scenario** (e.g. real Herrenteich) — normal `exhausted_budget` / exit-3 with honest diagnostics; no special-casing.
- **Mover that can only fit off its preferred side** — validity wins; the region term simply does not pull it across an invalidating move (only-valid-moves-accepted invariant of `_spread`).
- **Fixed-obstacle / hangar-bounds margins for movers** — the perturbation bbox-margin logic must use the mover's part footprint, not an aircraft default.

## Testing
- **Inert trio** (mirrors the priority tests, `test_solver_spread.py`):
  1. helper-level: `_region_energy` and total `_energy` byte-identical when `region_preferences` empty;
  2. solve-level: `solve(scenario_no_GO)` placements byte-identical to today, `max_restarts`-scoped;
  3. determinism-with-active-region: same seed + active preferences ⇒ byte-identical placements.
- **No-GO byte-identity**: existing `test_empty_ground_objects_byte_identical` + the parallel `test_solver_parallel.py` canaries stay green unmodified; the machine-independent `test_solve_deterministic_best_partial_under_max_restarts` (seed=17, max_restarts=3) stays green (re-calibrate only if RNG draw counts shift — they must not, since the fixture has no movers).
- **Region effect**: on the tractable demo fixture, `weight=0` vs `weight>0` shifts the trailer toward the preferred wall (mirrors `test_solve_back_fill_*`).
- **End-to-end**: tractable fixture `solve` places + routes both trailers (plans non-None), egress-gated; `region_alignment` reported and improves with weight.
- **CLI**: human summary + `--json` surface `region_alignment` (per-layout/per-object; null-safe like `min_pairwise_gap_m`).
- **`determinism-guard`** subagent on the solver diff (mandatory; runs the solver twice on a fixed seed and diffs); **`geometry-invariant-guard`** not triggered (no geometry.py/collisions.py change expected).

## Non-goals
- No new motion planner (towed = cart per #602; steerable = own-gear per #602).
- No change to `collisions.check` semantics or the `(conflict_count, total_penetration_m2)` score tuple — region lives entirely in the soft post-pass.
- No hard right-side constraint (that tier is the #603 Caddy egress, already shipped).
- `min_pairwise_gap_m` stays the primary basin key (#267) — region is purely intra-basin secondary in this cut.
- Unroutable-mover stderr surfacing is #612; soft door-priority / requested-sequence tie-break is #614 — both separate.
- No ML; this is deterministic Stage A substrate the learned backend (Stage C) will later read as a reward-shaping term.

## Risks / open questions
- **Determinism blast radius** is the main risk: ground-object integration touches ~10 solver functions. Mitigated by the "absent ⇒ byte-identical" invariant + the inert trio + `determinism-guard`. Every change is additive and degenerate-to-identical when no movers.
- **Region-alignment diagnostic shape** (`tuple[Mapping[str,float], ...]`) — confirm it serializes cleanly to `--json` and passes the `SolverDiagnostics` index-alignment validator; refine during writing-plans if the map shape is awkward.
- **Herrenteich intractability** is expected, not a bug; the tractable demo fixture is the real end-to-end proof.
- **`_descent_step` mover participation** may make some currently-feasible aircraft-only scenarios harder to solve within budget when movers are added — acceptable; movers only exist in the new fixtures.
