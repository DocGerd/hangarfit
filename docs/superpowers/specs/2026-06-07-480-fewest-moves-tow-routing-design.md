# Design spec — #480: fewest-moves tow routing (cusp-penalty cost + nose-out-gated rear cone)

- **Issue:** [#480](https://github.com/DocGerd/hangarfit/issues/480) — *towplanner v2: nose-out slots force a large in-hangar reorientation (entry cone is inward-only) — allow rear/reverse entry or apron-side pre-rotation*
- **Date:** 2026-06-07
- **Status:** Approved (design fork settled 2026-06-07: cusp cost + nose-out-gated rear cone)
- **Deciders:** Patrick Kuhn (DocGerd)
- **Touches:** `src/hangarfit/towplanner.py` (determinism-sensitive — ADR-0003 contract, `determinism-guard`)
- **Amends:** [ADR-0010](../../adr/0010-reeds-shepp-motion-model.md) (the reverse-cost model)
- **Supersedes (cross-version byte-identity only):** the [#412](https://github.com/DocGerd/hangarfit/issues/412) / [ADR-0021](../../adr/0021-tow-planner-staging-apron.md) **depth-0 byte-identical** guarantee
- **Related / downstream:** #263 (prefer nose-out parked heading — separate PR2), #505 (3D floor path — makes this visible), #412 (apron)

---

## 1. Problem

A plane whose **parked (final) heading is nose-out** (heading ≈ 180°, nose toward
the door) is currently reached by **driving deep into the hangar nose-first and
spinning ~180° in the cramped back corner**. Measured on a Herrenteich subset
(#480): `zlin_savage` sweeps **161°** and `fk9_mkii` **132°** of reorientation
*inside* the hangar; the nose-in plane sweeps ~0°.

Two independent code facts cause it:

1. **The entry cone is inward-only without an apron.** `entry_poses`
   (`towplanner.py:906`) seeds the rear-entry cone `_REVERSE_CONE_HEADINGS =
   {150,165,180,195,210}` **only when `apron_depth_m > 0`** (`:977`). With no
   apron, every plane must enter nose-pointing-inward (`_CONE_HEADINGS =
   {330,345,0,15,30}`), so a nose-out slot is reachable **only** by doing the
   whole reorientation inside.
2. **Reverse motion is taxed per-meter.** `_REVERSE_COST_FACTOR = 1.5`
   (`:519`) multiplies the *length* of every reverse leg in all three cost
   sites, so the planner prefers a **long forward loop** over a **short
   back-in**.

The paths are valid — this is a planner **quality** issue (awkward, and
pessimistic for routability because an in-hangar 160° turn needs swept clearance
a nicer approach would not).

## 2. Decision

Reframe the planner objective from "shortest, with reverse penalised" to
**"fewest moves"** — minimise direction changes, not reverse distance — and let
nose-out slots be **backed in** through the door without requiring an apron.

Two coordinated changes:

1. **Cost model:** replace the multiplicative reverse-length penalty
   (`_REVERSE_COST_FACTOR`, applied at three sites) with an **additive cusp
   penalty**:

   ```
   cost = total_length + CUSP_PENALTY · num_cusps
   ```

   - `total_length` = Σ|leg length| — the true metric length, **gear-agnostic**
     (a reverse metre costs the same as a forward metre).
   - `num_cusps` = number of **travel-direction reversals** along the path
     (`moves = cusps + 1`).
   - `CUSP_PENALTY` is **large-but-finite** (§6) — a single deterministic
     scalar, *not* a lexicographic `(cusps, length)` order, so an absurdly long
     detour to save one cusp still loses.
   - **Forward preference is retained as a deterministic enumeration-order
     tie-break** (forward primitives / words are enumerated first, so an exact
     cost tie keeps forward), replacing the old per-meter bias.

2. **Entry cone:** emit the rear-entry cone **iff the target slot's parked
   heading is nose-out-ish** — `|wrap180(target.heading_deg − 180°)| ≤
   _REAR_CONE_HALF_ANGLE_DEG` (≈ 45°) — **independent of apron depth**. Nose-in
   slots keep today's 5-heading forward grid byte-for-byte (no wasted
   expansions); only nose-out slots gain the 5 rear headings.

### Definition: cusp

A **cusp** is a reversal of travel direction (forward ↔ reverse, i.e. a `gear`
sign change) **between consecutive *translating* legs**. **In-place cart pivots**
(`r == 0` turns `L`/`R`, which rotate without translating) **do not translate and
are excluded** from cusp counting — they are free reorientations the cart model
already treats as cheap. For own-gear motion (`r > 0`) every leg translates, so
cusps are simply gear sign-changes between adjacent legs.

## 3. Changes by site

All three cost sites must score by the **same** `length + CUSP_PENALTY·cusps`
objective so the search g-cost, the analytic RS shot, and the cart choice agree.

### 3a. `entry_poses` (`:906`) — nose-out-gated rear cone

Today:

```python
if depth > 0.0:
    y_samples = (-depth / 2.0, -depth)
    headings  = _CONE_HEADINGS + _REVERSE_CONE_HEADINGS
else:
    y_samples = (0.0,)
    headings  = _CONE_HEADINGS
```

New rule — the rear cone is gated on **target nose-out**, the apron still gates
only the **y-samples**:

```python
nose_out = abs(_wrap180(target.heading_deg - 180.0)) <= _REAR_CONE_HALF_ANGLE_DEG
headings = _CONE_HEADINGS + (_REVERSE_CONE_HEADINGS if nose_out else ())
y_samples = (-depth / 2.0, -depth) if depth > 0.0 else (0.0,)
```

- Keeps the fixed, deterministic grid (emit order unchanged: x-outer, y-middle,
  heading-inner, dedup by exact-float key).
- **Migration note:** with an apron present today, the rear cone is emitted for
  *all* planes; under the new rule a nose-**in** plane with an apron loses its
  (useless) rear headings → its apron `MovesPlan` changes. Accepted (re-baseline,
  §7) — a nose-in plane never wins a rear-entry seed anyway.
- `_REAR_CONE_HALF_ANGLE_DEG` is a pinned module constant (≈ 45°, so the rear
  cone's own ±30° span plus a margin is covered). Document the choice.

### 3b. Hybrid-A* search g-cost (`_seg_cost:1450` + expansion loop `:2013+`)

- `_seg_cost` drops the `_REVERSE_COST_FACTOR` factor → returns the unweighted
  metric cost (straight metres / arc length + turn penalty / cart-pivot
  penalty), gear-agnostic.
- The **cusp charge is incremental, in the expansion step**, not inside
  `_seg_cost` (a single segment has no cusp): when expanding parent node `n`
  with primitive `p`, add `CUSP_PENALTY` to the child g iff `p` reverses travel
  direction relative to the **last translating leg** on `n`'s branch.
  - **Own-gear (`r > 0`):** every primitive translates ⇒ the predecessor is
    `n.seg`; charge iff `n.seg is not None and n.seg.gear != p.gear`.
  - **Cart (`r == 0`):** only straights translate; `n.seg` may be an in-place
    pivot. The child must compare against the **last translating gear** on the
    branch. Carry a `last_drive_gear: int` (or `Literal[1,-1] | None`) on
    `_SearchNode` so the comparison is O(1) and does not walk the parent chain.
    A pivot child inherits the parent's `last_drive_gear` unchanged; a straight
    child charges a cusp iff `last_drive_gear` is set and differs, then becomes
    the new `last_drive_gear`. (Own-gear sets `last_drive_gear = p.gear` on every
    child — uniform.)
  - `_SearchNode` stays `frozen=True`; add the field to the dataclass.
- **Heuristic:** `_build_grid_heuristic` is a free-space geodesic *lower bound*.
  Adding a non-negative cusp term only *raises* actual g, so the existing
  heuristic stays admissible (it may be looser, costing a few more expansions —
  acceptable, watch the budget §7). **No heuristic change required.**
- Determinism: expansion order is unchanged (fixed primitive fan order +
  `(f, counter)` heap tie-break). The cusp term is a deterministic function of
  the branch, so the contract holds.

### 3c. `_rs_solve_normalised` (`:731`) — cusp-weighted RS word selection

The analytic shot `plan_reeds_shepp(node.pose, goal, turn_radius_m=r)` (`:1968`)
selects the best Reeds–Shepp **word** for the remaining leg.

- Replace `cost = Σ e.t · 1.5^[reverse]` with
  `cost = Σ e.t + cusp_penalty_normalised · cusps(word)`, where `cusps(word)` =
  gear sign-changes between adjacent `_RSElement`s (every RS leg translates, so
  no pivot exclusion here).
- **Unit consistency (critical):** word cost is in **normalised** units
  (radius = 1); the chosen word is later scaled by `r` to metres. To make the
  RS choice agree with the metre-space objective, pass
  `cusp_penalty_normalised = CUSP_PENALTY / r` from `plan_reeds_shepp` (which
  knows `r`) into `_rs_solve_normalised`. Then minimising
  `Σ e.t + (CUSP_PENALTY/r)·cusps` is `argmin` of
  `(Σ e.t·r + CUSP_PENALTY·cusps)/r` = the metre objective. (`plan_reeds_shepp`
  delegates carts to `_plan_cart` *before* this point, so `r > 0` here.)
- Keep the strict-`<` tie-break so an exact tie deterministically keeps the
  earliest-enumerated word (forward-first).

### 3d. `_plan_cart` (`:420`) + `_cart_seg_weight` (`:479`) — drop the reverse tax

`_plan_cart` chooses between a forward and a reverse pivot-straight-pivot.

- Drop `_REVERSE_COST_FACTOR` from `_cart_seg_weight`; weight a straight by its
  metres (gear-agnostic) and a pivot by its radians (as today).
- **Both candidates are single-drive ⇒ 0 cusps** under the *translating-legs-
  only* rule: the lone straight is the only translating leg, so the
  pivot-straight-pivot has no *internal* travel-direction reversal whether the
  straight is forward or reverse. The cusp term is therefore **inert inside
  `_plan_cart`** (0 for both), and the choice **reduces to an unweighted length
  comparison** with `min(forward, reverse)` keeping forward on an exact tie
  (forward first → determinism). No explicit cusp term is needed here; this is
  the desired effect — backing a cart straight in is "one move," exactly like
  pulling it forward, and is chosen iff genuinely shorter (no per-meter reverse
  tax). The old ×1.5 was the only thing biasing against an equal-length back-in.

### 3e (note). The analytic-expansion boundary cusp is not separately charged

The returned path is `reconstructed search prefix + analytic suffix`. Cusps
*within* the prefix are charged in the search g-cost (§3b); cusps *within* the
analytic word in `_rs_solve_normalised` (§3c). The single **boundary** cusp
(last prefix translating gear vs first analytic leg gear) is **not** separately
charged — consistent with Hybrid-A*'s greedy analytic expansion, which returns
the analytic completion from the first low-`f` node with a collision-clean shot
rather than globally minimising total cost. This matches the current code's
accounting granularity (the old ×1.5 likewise never charged a boundary term); we
are not regressing it. The dominant path shaping comes from the search g-cost and
the analytic word choice, which the §8.1 swept-turning metric verifies end-to-end.

### 3f. Remove `_REVERSE_COST_FACTOR`

Delete the constant (`:519`) and all three usages. Replace
`test_reverse_cost_factor_value` (`tests/test_towplanner_reeds_shepp.py:142`)
with `test_cusp_penalty_value` pinning the new constant; keep its "changing this
requires an ADR-0010 update" docstring intent.

## 4. New module constants

| Constant | Purpose | Pinned by |
|---|---|---|
| `CUSP_PENALTY` (metres) | additive per-cusp cost (§6) | `test_cusp_penalty_value` + ADR-0010 amendment |
| `_REAR_CONE_HALF_ANGLE_DEG` (≈ 45°) | nose-out gate half-angle for the rear cone | a unit test on `entry_poses` heading set vs target heading |

(`_wrap180(deg)` — a small helper folding an angle to `(−180, 180]`; reuse an
existing helper if one exists, else add one beside the heading utilities.)

## 5. Why this design (vs the rejected forks)

- **Unconditional rear cone** (always 10 headings): simplest, but ~2× entry
  poses for *every* plane → more expansions per plane (the `_MAX_EXPANSIONS =
  8000` / global-cap budget tightens) even for nose-in slots that never use rear
  entry. Rejected: pay the cost only where it helps.
- **Cost change only, keep cone apron-gated:** smallest blast radius, but the
  no-apron nose-out case the issue *measured* stays broken. Rejected: under-
  delivers the issue.
- **Lexicographic `(cusps, length)`:** would let one saved cusp justify an
  unbounded detour. Rejected for the bounded `length + CUSP_PENALTY·cusps`
  scalar (also keeps a single deterministic comparison key).

## 6. Calibrating `CUSP_PENALTY`

Method (mirrors ADR-0010's `_REVERSE_COST_FACTOR = 1.5` justification):

- **Lower bound** — a genuine nose-out win must survive: the measured case is
  ~18 m back-in vs ~32 m forward loop. The back-in via a rear-entry seed is
  typically **0 cusps** (single reverse leg) ⇒ it already wins on length alone;
  where a back-in costs **1 cusp** (back in, then a short forward nudge), we need
  `18 + 1·CUSP_PENALTY < 32` ⇒ `CUSP_PENALTY < 14 m`.
- **Upper-bound intent** — `CUSP_PENALTY` should dominate the *typical* small
  length differences between equal-direction-change alternatives, so the planner
  doesn't trade an extra direction change for a couple of saved metres.
- **Proposed starting value:** `CUSP_PENALTY = 10.0` m (order of a plane length /
  the hangar's short dimension); **finalise empirically** against the Herrenteich
  nose-out fixtures (zlin_savage, fk9_mkii) and a synthetic regression fixture,
  then pin it with `test_cusp_penalty_value` and justify the number in the
  ADR-0010 amendment (same prose pattern as the old 1.5 paragraph).

## 7. Determinism & migration

- **The ADR-0003 contract holds.** Same scenario + same seed → byte-identical
  `SolveResult` / `MovesPlan`. The new cost is a deterministic, RNG-free function
  of the (fixed) inputs; expansion order, the fixed entry grid, and the
  `(f, counter)` / strict-`<` tie-breaks are unchanged. **`determinism-guard`
  must pass** (double-solve diff on a fixed seed).
- **Cross-version byte-identity is intentionally broken.** Changing the scoring
  metric re-selects RS words and entry poses, so **all tow goldens re-baseline**.
  This **supersedes the #412 / ADR-0021 depth-0 byte-identical guarantee** (the
  promise that an apron-depth-0 plan equals the pre-apron plan byte-for-byte) —
  call this out explicitly in the ADR-0010 amendment and the PR body. The
  *contract* (determinism within a version) is untouched; only the *historical
  stability* across this change is.
- **Goldens / tests to update:** `tests/test_towplanner*.py`,
  `tests/test_solver_towplanner.py`, the `serial`-marked determinism canaries,
  and any committed path goldens. Re-baseline by regenerating, then **inspect the
  diffs** to confirm the new paths are *better* (less in-hangar swept turning),
  not merely different.
- **Expansion budget:** nose-out slots now seed ~2× headings and the heuristic is
  slightly looser; verify no nose-out fixture regresses to `budget_exhausted`
  under the default `_MAX_EXPANSIONS = 8000`. If one does, that's a measured
  signal — surface it, don't silently raise the cap.

## 8. Test plan & acceptance

Acceptance (from the issue):

- [ ] A nose-out slot routes with **substantially reduced in-hangar swept
      turning** (backs in / pre-rotates), verified on the Herrenteich subset
      (`zlin_savage`, `fk9_mkii`) **and** a synthetic regression fixture.
- [ ] **No regression** in validity / path-validity / determinism (`bench`
      verdicts; `determinism-guard`).
- [ ] Decision recorded — ADR-0010 amendment.

Tests to add / change:

1. **Swept-turning metric** — a helper that sums `Σ|Δheading|` along a path while
   `y_m > 0` (the issue's own metric); assert it drops markedly for a nose-out
   fixture vs the pre-change baseline (snapshot the number).
2. **Cusp counting unit tests** — own-gear word with a reversal (1 cusp), pure
   reverse leg (0 cusps), forward-reverse-forward (2 cusps), cart
   pivot-straight⁻-pivot (1 cusp, pivots excluded).
3. **`entry_poses` gate** — nose-out target ⇒ rear headings present; nose-in
   target ⇒ exactly the 5 forward headings (no apron) / forward headings only
   (with apron); boundary at `_REAR_CONE_HALF_ANGLE_DEG`.
4. **`test_cusp_penalty_value`** + **remove `test_reverse_cost_factor_value`**.
5. **Determinism canaries** stay green (double-solve byte-identity) — run the
   `serial` set; run `determinism-guard`.
6. **Regenerated goldens** committed with an inspected, justified diff.
7. **`bench`** (`python -m bench.profile_pipeline`) validity/path-validity/
   determinism verdicts stay green for every regime.

## 9. Out of scope (this PR)

- **#263** (solver *prefers* a nose-out parked heading) — separate PR2. This PR
  makes a nose-out slot *cheap to reach when the solver happens to pick one*;
  #263 makes the solver pick more of them. Doing #263 here would conflate two
  determinism-sensitive changes.
- **#505** (draw the path on the 3D floor) — independent viewer PR; it *visualises*
  this improvement but doesn't depend on it.
- **Apron auto-deepen** (#503 Option 2) — unrelated; #503's warn cut is separate.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Cart cusp bookkeeping subtly wrong (pivot exclusion) | Dedicated cusp unit tests (§8.2); carts rarely hit the nose-out case (they pivot in place) so blast radius is small |
| `CUSP_PENALTY` mis-calibrated → reverse over/under-used | Empirical calibration against fixtures (§6); pinned + ADR-justified |
| Looser heuristic → a hard nose-out fixture blows the expansion budget | §7 check; surface, don't silently raise the cap |
| Golden churn hides a real regression | Inspect every golden diff; the swept-turning metric (§8.1) is the objective check, not just "different bytes" |
| Reviewer surprise at lost #412 depth-0 byte-identity | Called out explicitly in ADR-0010 amendment + PR body |
