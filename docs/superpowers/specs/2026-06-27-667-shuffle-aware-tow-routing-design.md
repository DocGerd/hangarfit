# Design — Shuffle-aware tow routing (#667)

**Status:** approved design · 2026-06-27
**Issue:** [#667](https://github.com/DocGerd/hangarfit/issues/667) — *Shuffle-aware tow routing: lift the monotonic-placement constraint so dense fleets (full Herrenteich) get valid fill paths*
**Supersedes the "Design options (for discussion)" section of #667** with a concrete, decomposed program.

---

## 1. Problem

`hangarfit` can confirm the full Herrenteich fleet is **statically valid parked**
(`examples/herrenteich/layout.yaml`, `layout_today.yaml`) but cannot compute a
door-to-slot **tow fill sequence** for it. The routing ceiling for the L-shaped
hangar is ~4–7 bodies; the real day is 9 aircraft + ground objects. The real club
assembles the dense nest by **shuffling** — rolling a parked plane aside to let
another past, hand-positioning the dolly-borne gliders. The planner models none
of that.

This was diagnosed across the 2026-06 spikes
([`herrenteich-all8-tow-routing-rootcause.md`](../../spikes/herrenteich-all8-tow-routing-rootcause.md),
[`herrenteich-fk9-cessna-lateral-shuffle.md`](../../spikes/herrenteich-fk9-cessna-lateral-shuffle.md)).

### 1.1 What already ships (the issue body is partly stale)

Two pieces #667's body proposes have **already landed** and must not be re-built:

- **Stage 0 *mechanism*.** `Placement.hand_placed: bool = False`
  (`models.py:820`) exists. `_plan_fill` (`towplanner.py:1635-1652`) already
  partitions hand-placed bodies into fixed keep-outs and emits a path-less
  at-rest `Move(plane_id, pose, None)` for each. The loader rejects the flag on
  ground objects. **It is not activated in the shipped Herrenteich data** — no
  layout sets the flag.
- **Order-search backtracking.** The fill is no longer a one-pass monotonic
  greedy loop: `_place_rest` (`towplanner.py:1665-1749`) is a deterministic
  **backtracking DFS over placement order**, greedy-first. The byte-identity
  guarantee is built in (`towplanner.py:1654-1662`): the first feasible candidate
  at each level is recursed first, so when the greedy back-first order already
  works the search returns the identical plan untouched.

What is **genuinely open**: (a) *activating* Stage 0 in the real data;
(b) **move-aside relocation** — temporarily displacing an already-parked body so a
later body can route, then restoring it; (c) the **multi-leg plan data model**
that move-aside requires; (d) an objective **bench baseline** and a **reverse
teardown diagnostic** to characterise the wall.

## 2. The architectural bet (why this is not a repeat of #840/#844)

The refuted levers — heading-aware SE(2) heuristic (#840, NO-GO), analytic
parallel-park macro (#840, refuted), chartered-pair continuous traj-opt (#844b,
NO-GO dominated) — all tried to make a **single straight-to-final-pose A\* route**
thread the `fk9_mkii`↔`cessna_140` corridor. That is an **intrinsic near-C\* A\***
**plateau** (~97k expansions): completeness forces expanding every `f*≤C*` state,
so no heuristic-class method shrinks it.

**Move-aside operates on a different axis.** It does not make the search smarter;
it changes the **obstacle field** so that a route *exists at all*. The
`fk9↔cessna` pair has no monotone order but provably has a *shuffle* order — the
club does it by hand daily. Move-aside is therefore the **one untested lever** and
the only one that can beat a *mutual* block.

**Honest caveat (recorded, load-bearing for acceptance):** even fully built,
move-aside may still not seat the `fk9↔cessna` cm-scale parallel-park (it is
unproven against that specific geometry). Achievable with confidence:
husky-class ordering locks, dense **partial** fills, and faithful glider
modeling. *"Route the full all-8"* is a **stretch goal, not a guarantee.** Rung B
measures this objectively.

## 3. Determinism contract (ADR-0003) — the binding constraint

Every rung must keep the tow plan **byte-identical-bound**: same scenario + seed →
bit-identical `MovesPlan`. The whole tow module imports no `random`/`secrets`;
determinism is **structural** and must stay so.

- **Inert-path guarantee.** A layout that does **not** need the new capability
  must produce a **byte-for-byte identical** plan to today. For move-aside this is
  realised by **verify-before-displace**: a displacement is attempted only when
  *no* non-displacing candidate at the current DFS level can route the rest. The
  greedy-first recursion already returns the identical plan when the back-first
  order works (`towplanner.py:1654-1658`).
- **Deterministic tie-break keys** (no RNG, no wall-clock):
  1. *which body to displace* — deepest-first via `back_first_order`'s existing
     total order `(-y, +x, +plane_id)` (`towplanner.py:978`); hand-placed bodies
     are **never** moveable.
  2. *staging-pose enumeration* — a fixed-emit-order grid mirroring `entry_poses`
     (y-outer, x-middle, heading-inner) with exact-float dedup via a `seen` set
     used **only to dedup, never to order**.
  3. *route cost ties* — strict `<` so the earliest-enumerated wins (matches
     `_dubins_shortest`/`_rs_solve_normalised`).
  4. *A\* open-heap* — `(f, monotonic_counter, node)` so unorderable nodes never
     compare.
- **Bounding.** Move-aside expansions charge the existing global
  `total_used`/`total_budget` (dead branches included), **plus** a new
  deterministic **per-stuck-slot displacement cap** (count-based, not wall-clock)
  to prevent cycles (displace A → fail B → displace B → re-displace A …).
- **Cross-machine caveat (ADR-0003 #844 amendment).** Move-aside fires multiple
  transcendental-heavy `plan_path` calls per stuck slot (libm `sin/cos/atan2/acos`
  unpinned cross-host). The contract stays robustly **same-machine** and
  presumptively cross-machine; staging poses must carry **comfortable cost margin**
  so a sub-ULP boundary cannot flip viability across hosts.
- **Guards.** `determinism-guard` on every `towplanner.py`/`solver.py` PR;
  `geometry-invariant-guard` on any geometry touch; `scene-schema-guard` on the
  scene/v2 seam.

## 4. Decomposition — five rungs, lowest-risk first

Each rung is its own GitHub issue (sub-issue of #667) and its own PR. Ordered so
the lowest-risk, highest-legibility rung ships first; the risky data-model change
(D) lands with **zero behavior change** so move-aside (E) is a *localized* planner
change rather than planner+schema+viewer at once.

### Rung A — Activate Stage 0 hand-placed gliders (data only)

- **Scope.** Set `hand_placed: true` on the dolly-borne gliders **Scheibe Falke**
  and **Stemme S10** in every Herrenteich layout where they appear; keep
  `on_carts: true` (orthogonal — `on_carts` is the physical state shown in
  render; `hand_placed` is *how it got there*). Add a dedicated test fixture.
- **Decision (confirmed with user).** Both gliders are hand-placed. Layouts
  touched (verified against the data): `layout.yaml` (Scheibe + Stemme),
  `layout_today.yaml` (Scheibe + Stemme), `layout_full.yaml` (Stemme only — the
  Scheibe parks outside in that over-capacity what-if).
- **No production code change** — the mechanism ships (`models.py:820`,
  `towplanner.py:1640-1652`).
- **Files.** `examples/herrenteich/layout*.yaml`, a new
  `tests/fixtures/` hand-placed fixture, `tests/test_towplanner_*.py`,
  `CHANGELOG.md`.
- **Acceptance.** (1) Byte-identity preserved for any layout with zero
  hand-placed bodies (regression test, double-build + diff). (2) A hand-placed
  body emits a path-less at-rest `Move` and is treated as a keep-out by routed
  bodies. (3) `hangarfit check` on the edited layouts still exits 0 (static
  validity unchanged).
- **Risk.** Low. **Buys** faithful glider modeling and shrinks the towable set;
  does **not** touch the `fk9↔cessna` ceiling.

### Rung B — Bench ceiling regime + tripwire

- **Scope.** Add a `herrenteich_all_eight` (and a today-fleet) regime to
  `bench/regimes.py` (fixed seed, bounded `max_restarts`, `heavy=True`) and a
  `_SPEED_CEILING_S` entry in `bench/profile_pipeline.py`. Pure measurement; no
  production code.
- **Files.** `bench/regimes.py`, `bench/profile_pipeline.py`, optionally a short
  `docs/spikes/` writeup of the measured baseline.
- **Acceptance.** A deterministic digest of the routed-body count / timing for
  the Herrenteich-class scenarios, runnable in `bench`, documenting the residual
  wall objectively. RNG-free routing keeps the digest deterministic.
- **Risk.** Low. **Buys** the baseline every later rung is graded against.

### Rung C — Reverse-teardown read-only feasibility probe

- **Scope.** Generalise `egress_first_conflict` (`towplanner.py:1386`, which
  already routes one body slot→door backward, with `egress_path_out` plumbing and
  ADR-0010 entry⟺egress reversibility) into a **whole-fill teardown probe**:
  extract each body slot→door in **reverse-placed order** against shrinking
  partial state; emit a **diagnostic verdict only** — does a teardown/fill order
  exist? **No plan output, no data-model change → byte-identical.**
- **Files.** `src/hangarfit/towplanner.py` (read-only probe), a
  `tests/test_towplanner_*.py` determinism + correctness test, a `docs/spikes/`
  writeup.
- **Acceptance.** The probe reports whether a reverse order exists for the
  Herrenteich layouts, and confirms the predicted dual: `fk9↔cessna` blocks
  *extraction* too (reverse-alone has the same ceiling — it only removes, never
  relocates). This tells us reverse is a **diagnostic**, not the writer, *before*
  we invest in move-aside.
- **Risk.** Medium (new traversal) but read-only and byte-safe; `determinism-guard`
  required.

### Rung D — Multi-leg data-model + consumer seam (no planner change)

- **Scope.** Make a single body able to traverse **multiple legs** (to staging,
  then to final) without changing any plan *output* yet.
- **Data model.** Keep `Move` single-leg
  (`{plane_id, target_slot: Pose, path: DubinsArc | None}`, `towplanner.py:236`).
  Allow **multiple `Move` entries per `plane_id`** in `MovesPlan.moves` (one per
  leg, in execution order) and add an **additive discriminator** `leg_index:
  int = 0`. Existing single-leg producers keep `leg_index=0` and serialize
  identically → byte-safe. **Rejected:** reshaping `Move.path` into
  `tuple[DubinsArc, ...]` (forces every `.sample()`/`.length_m` site to enumerate
  and breaks the one-`target_slot`-per-body invariant scene/visualize rely on).
  A staging leg's `target_slot` is a **transient** pose (not in
  `target_layout.placements`); the body's final placement remains the single
  entry in `Layout.placements`. `SolveResult.plans` is structurally unaffected
  (`len(plans)==len(layouts)` holds; multi-leg lives inside one `MovesPlan`).
- **Consumers (all changes additive).**
  - `scene.py:274` `move_by_id = {m.plane_id: m}` → `dict[str, list[Move]]`
    (group, don't overwrite); `_timeline`/`_append_segment` (`278-291`) emit one
    segment **per leg** with summed sequential time; carry optional `leg_index` on
    the segment dict. The `back_first_order` loop (`298-299`) stays.
  - scene/v2: extend `timeline.segments[]` with optional `leg_index: int`
    (`docs/architecture/scene-v2-schema.md`); `SCHEMA` stays
    `hangarfit.scene/v2` (additive only; a v3 bump is disallowed by ADR-0017).
    `build_scene` stays byte-identical because **zero current producers emit >1
    leg.**
  - `viewer/src/scene-contract.ts:87` `SegmentData` gains `leg?: number`
    (key-set parity test, #440); `viewer/src/timeline.ts:71` `segByPlane` →
    array or composite `pid:leg` key; `affineAt` iterates legs to find the one
    containing time `t`. Rebuild + commit `viewer.js` (drift guard #438).
  - `visualize.py` `_draw_tow_paths` (`720-768`): the per-move `.sample()` loop
    enumerates legs, plotting each (optional distinct styling for a shuffle leg).
- **Animation (confirmed with user).** Staging legs **animate** (the viewer's
  purpose is to *show* the shuffle); accept slightly larger HTML.
- **Files.** `towplanner.py` (`Move`), `scene.py`, `visualize.py`,
  `viewer/src/timeline.ts`, `viewer/src/scene-contract.ts`,
  `src/hangarfit/_viewer_assets/viewer.js`,
  `docs/architecture/scene-v2-schema.md`, `tests/test_scene.py`,
  `tests/test_viewer.py`.
- **Acceptance.** Byte-identity provable (no multi-leg producer exists yet);
  `scene-schema-guard` + key-parity + viewer-build-drift all green; consumers
  handle (synthetic) multi-leg input in unit tests.
- **Risk.** Medium (wide but additive ripple).

### Rung E — Move-aside repair core (the open Stage 1)

- **Scope.** Extend `_place_rest` (`towplanner.py:1730`): when the greedy
  candidate deadlocks a later body and **no non-displacing candidate routes the
  rest**, temporarily remove a previously-committed body from `placed`, route the
  stuck body against the reduced set, commit a **staging `Move`** (via Rung D's
  multi-leg shape) + recurse, then **restore** the displaced body to its final
  pose before the next iteration.
- **Decisions (confirmed with user / recommended).**
  - **Depth cap = 1** displacement per stuck slot to start (depth-2 explodes the
    deterministic search + cycle surface; revisit after depth-1 ships).
  - **Staging-pose policy = apron-out first** (y<0, reuses ADR-0021): a body
    parked outside the door cannot jam later bodies (avoids the *viability
    paradox* where a center staging pose blocks the whole fill). Interior
    off-to-side wall pockets only if apron-out proves insufficient.
  - Separate **displacement-attempt cap** distinct from `_MAX_FILL_BACKTRACKS`
    (`towplanner.py:1898`), count-based, to bound cycles.
  - Preserve the **stuck-body Conflict-naming** contract (the #668 review
    contract at `1724`/`1743-1748`/`1761-1764` must name the *stuck* body, not the
    displaced one).
  - **Spread post-pass (ADR-0008):** intermediate/staging legs are **excluded**
    from the spread metric; spread operates on final placements only.
- **Files.** `src/hangarfit/towplanner.py`, `tests/test_solver_canaries.py`,
  `tests/test_towplanner_*.py`, `bench/` (re-baseline the ceiling),
  `CHANGELOG.md`.
- **Acceptance.** (1) A move-aside-exercising fixture gets a valid **multi-leg**
  fill plan; every intermediate shuffle leg is collision-free and in-bounds.
  (2) **Byte-identical** plans for layouts that don't need a shuffle
  (`determinism-guard` green; a canary that exercises the move-aside branch is
  added and double-solve-diffed). (3) The bench (Rung B) shows the routing
  ceiling raised on Herrenteich-class scenarios. (4) Bounded cost: a documented
  cap on shuffle depth / total moves.
- **Risk.** **High** — cycles, staging-pose count explosion, the viability
  paradox, inert-path byte-identity, cross-machine libm under repeated
  `plan_path`. `determinism-guard` + `geometry-invariant-guard` mandatory.

## 5. Explicit non-goals

- **Not** the learned backend (#607). Move-aside is a deterministic planner
  capability, complementary to #607 (it gives #607 a richer routability oracle).
- **Not** full TAMP / open-ended rearrangement (the issue's Stage 2). The program
  stops at **bounded depth-1 move-aside** and explicitly defers TAMP.
- **Not** wing-tilt-during-motion (the Scheibe routes without it), and **no**
  change to static validity or clearances.
- **No** guarantee the `fk9↔cessna` pair is routed; it may remain a documented
  manual-insertion case.

## 6. Sequencing & deferral of remaining decisions

- A → B → C ship first (low/low/medium risk, no data-model change). C's verdict
  informs whether reverse framing adds anything before D/E.
- D lands the seam byte-identically; E is the only behavior-changing rung.
- Rung-E sub-decisions (interior-pocket fallback, depth-2, exact displacement-cap
  value) are revisited **at rung E** with the bench baseline in hand, not now.

## 7. Test & guard strategy (per rung)

| Rung | Primary guards / tests |
|---|---|
| A | byte-identity regression; hand-placed keep-out + path-less-move unit; `check` exit-0 |
| B | deterministic bench digest |
| C | `determinism-guard`; reverse-probe correctness + determinism unit |
| D | `scene-schema-guard`; #440 key-parity; #438 viewer-build-drift; synthetic multi-leg consumer units |
| E | `determinism-guard` + `geometry-invariant-guard`; move-aside canary (double-solve diff); multi-leg plan validity (every leg collision-free, in-bounds) |
