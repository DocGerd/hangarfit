# Free-castering tow-pivot capability — design

- **Date:** 2026-05-29
- **Status:** Proposed (awaiting user spec review → implementation plan)
- **Target milestone:** #28 (v0.9.0)
- **Subsystem:** `towplanner` motion model (via `models.py` accessor + fleet data)
- **Related:** [ADR-0010](../../adr/0010-reeds-shepp-motion-model.md) (Reeds–Shepp motion),
  [ADR-0007](../../adr/0007-tow-path-planner-v1-scope.md) (cart = own-gear with `turn_radius_m = 0`),
  [ADR-0013](../../adr/0013-wheels-canonical-data.md) (canonical per-aircraft data),
  issue **#263** (nose-out — enabled by this), issue **#320** (back-bias — complemented by this).

---

## 1. Problem

The tow-path planner models every own-gear plane as a car-like Reeds–Shepp vehicle with a
fixed minimum turning radius (`Aircraft.turn_radius_m`). For the Aviat Husky that radius is
`5.0 m`. But the Husky is a **free-castering taildragger**: when towed by hand or a tail-tug,
its tailwheel swivels freely and the airframe rotates essentially *within its own length*
about the main-gear axle. The `5.0 m` figure models **powered self-taxi**, not **towing** —
and the planner's job is to plan *tows*.

The consequence is observable. In the six-plane fill (`tests/fixtures/solve_fresh_six_planes.yaml`,
`--no-spread --seed 1`) the planner returns **exit 3**, and the blocking plane is *literally the
Husky*:

```
layout not tow-routable by the tow-path planner: plane 'aviat_husky' blocked
(no_feasible_path: no in-bounds tow path found within 2000 expansions)
```

The static layout is valid; the planner just can't thread a 5.0 m-radius car through the gap a
real handler would pivot the Husky into. The model is **conservative to the point of being wrong
for the towing use case.**

## 2. Goal & non-goals

**Goal.** Let a plane declared free-castering be planned with the realistic *towing* motion —
pivot in place, push straight, pivot — which is both physically faithful for a hand/tail-tug
tow and the thing that makes tight fills routable.

**Non-goals.**
- Not modelling powered taxi (the `turn_radius_m` arc stays as the documented powered figure).
- Not adding a probabilistic planner (RRT-Connect remains deferred per ADR-0010).
- Not re-centering the pivot on an arbitrary main-gear offset (see §4.3 alternative (b), deferred).
- Not changing which planes ride carts or the cart-eligibility rules.

## 3. Decision summary

| Axis | Decision | Why |
|---|---|---|
| Objective | Model realistic towing (pivot + push), which also maximizes routability | A tow planner should model towing; pivot+push is how a free-caster is actually towed |
| Declaration | Explicit per-plane `free_castering: bool` flag in `fleet.yaml`, default `false` | ADR-0013 ethos: declare canonical capability, don't infer from `gear` (steerable/lockable tailwheels don't fully caster) |
| Data-model shape | **Orthogonal boolean, NOT a new `MovementMode`** | Keeps `on_carts`/`movement_mode` invariants and cart-pool accounting untouched (§4.1) |
| Motion realization | Reuse the existing `turn_radius_m == 0` cart-pivot machinery | No new primitive / word / cost constant / geometry math → determinism surface unchanged |
| Pivot center | About the plane **datum** (existing behavior); main-gear-offset pivot deferred | 0.20 m error for the Husky (main gear ≈ datum); offsetting would touch motion math + trigger geometry-guard |

## 4. Design

### 4.1 Data model — orthogonal boolean, not a movement mode (load-bearing)

`free_castering` is a capability **orthogonal to `movement_mode`**, added as a new boolean field
on `Aircraft` (default `False`). It is deliberately **not** a new `MovementMode` value:

- A free-castering Husky is **still on its own gear** — `on_carts = False`. The `Layout`
  consistency invariant (`always_cart ↔ on_carts`, `models.py:488`) and the solver's cart-pool
  accounting key off `movement_mode`. A new movement mode would tangle with both; an orthogonal
  boolean touches neither.
- It composes: `always_own_gear + free_castering` (the Husky today) and
  `cart_eligible + free_castering` (a tailwheel that pivots on its own gear *and* can ride a
  cart) both fall out correctly with no special-casing. `always_cart + free_castering` is
  redundant but harmless (already `0.0`); the loader stays permissive (the flag is gear- and
  mode-agnostic, since free-castering nosewheels also exist).

### 4.2 The accessor — the single behavioral change

`Aircraft.effective_turn_radius_m()` (`models.py:299`) is the *one* place the planner resolves a
plane's planning radius. Today:

```python
if self.movement_mode == "always_cart":
    return 0.0
return self.required_turn_radius_m()
```

becomes:

```python
if self.movement_mode == "always_cart" or self.free_castering:
    return 0.0
return self.required_turn_radius_m()
```

A flagged plane returns `0.0`, and from `plan_path:1679` (`r = mover.effective_turn_radius_m()`)
that `0.0` flows through the **existing** cart-pivot path with no further branching:

- `_primitives(0.0)` → the 4-primitive fan (pivot-L, straight-fwd, pivot-R, straight-rev)
- `DubinsArc.pose_at` r==0 branch → pivot-in-place integration (position fixed, heading steps)
- `DubinsArc.sample` r==0 branch → angular density for collision sampling of the pivot
- `_seg_cost` r==0 branch → pivot cost in radians × `_TURN_PENALTY`
- `plan_reeds_shepp(turn_radius_m=0.0)` → delegates to `_plan_cart` (pivot → push → pivot,
  reverse-enabled)

`required_turn_radius_m()` and the `__post_init__` validation are unchanged: a free-castering
own-gear plane still carries a positive `turn_radius_m` (the documented powered-taxi figure); the
planner simply stops consulting it.

### 4.3 Pivot center

The r==0 pivot rotates about the plane's **datum** (local origin, `local_to_world`,
`geometry.py:119`). The physically-true center is the **main-gear axle** (`main_offset_x_m`).

- **(a) Pivot about the datum — CHOSEN.** Reuse the machinery verbatim; `towplanner.py` and
  `geometry.py` are untouched, so **no geometry-invariant-guard review is required**. Faithful
  for the Husky: its main gear sits at `main_offset_x_m = 0.20 m`, i.e. 20 cm (sub-wheel-width)
  from the datum. Documented assumption: *accurate when datum ≈ main gear; the flag is opt-in, so
  only flag planes where this holds.*
- **(b) Pivot about `main_offset_x_m` — DEFERRED.** Physically general, but requires offsetting
  the pivot center inside `pose_at` → touches motion math → triggers the geometry-invariant-guard
  sign-flip review and a new heading canary, and breaks the "planner untouched" property — for a
  0.20 m gain on the only currently-flagged plane. Recorded in the ADR as the future refinement if
  a plane with a far-offset datum is ever flagged.

### 4.4 Change surface

| File | Change | Notes |
|---|---|---|
| `src/hangarfit/models.py` | Add `free_castering: bool = False` to `Aircraft`; add `or self.free_castering` to the `0.0` branch of `effective_turn_radius_m()` | `MovementMode` and `__post_init__` unchanged |
| `src/hangarfit/loader.py` | Parse `free_castering` (default `False`) | Boolean; no coupling to `gear` |
| `data/fleet.yaml` | `aviat_husky: free_castering: true` + comment; update header doc block | Other planes default `false`; club fills in as known |
| `src/hangarfit/towplanner.py` | **None** | The headline property — flagged planes reuse the existing deterministic r==0 path |
| `src/hangarfit/geometry.py`, `collisions.py` | **None** | No motion math change under decision (a) |

## 5. Determinism

The tow planner is **RNG-free**; determinism is structural (fixed primitive-fan order, fixed
Reeds–Shepp word order with strict-`<` tie-break, a monotonic `(f, counter, node)` heap
tie-break, total-order sorts). Because flagged planes reuse the **existing** r==0 path, this
change adds **no new primitive, word, cost constant, ordering surface, or RNG**. The determinism
contract (ADR-0003, `max_restarts`-scoped per the #267 amendment) holds by construction.

A determinism canary (§7) proves it empirically: a free-castering-Husky scenario must produce a
**byte-identical `MovesPlan`** across two runs of the same scenario + seed.

## 6. Real-world basis

A free-castering tailwheel has no rudder linkage; the aircraft is steered by differential braking
and, with the tailwheel free-swivelling, can be rotated about a main wheel **within its own
length** (FAA *Airplane Flying Handbook* ch. 14; EAA; AOPA). A tail-tug makes the tailwheel a
steered, pushed rear point (trailer kinematics); the handler swings the tail and the body rotates
about the main-gear line. The faithful planner abstraction is therefore **rotation about the
main-gear axle with near-zero translation**, with the **wingtips** sweeping the binding circle.

Key numbers (Aviat Husky): wingspan **10.82 m** (the value already in `fleet.yaml`'s wing
`width_m`), so the swept circle has radius ≈ **5.4 m** — *wingtips*, not the tail, dominate the
footprint. The existing pivot already samples the full angular sweep for collision checking
(`DubinsArc.sample` r==0 branch), so the wingtip sweep is collision-checked. Because the Husky's
main gear is ~0 m from its datum, the datum pivot (§4.3a) is a faithful main-gear pivot for it.

## 7. Testing

- **Unit (`models`):** `effective_turn_radius_m()` returns `0.0` for a free-castering own-gear
  plane; field defaults `False`; `cart_eligible + free_castering → 0.0`.
- **Loader:** `free_castering` parsed; defaults `False`; round-trips.
- **Planner integration:** a flagged own-gear plane yields a pivot-style path (a `turn_radius_m == 0`
  `DubinsArc` containing pivot segments).
- **🎯 Payoff test:** with `aviat_husky.free_castering = true`, the six-plane fill
  (`solve_fresh_six_planes`, `--no-spread --seed 1`) routes the Husky — turning the observed
  exit-3 into a fully-routed fill. Directly measurable against the motivating failure.
- **Determinism canary:** a free-castering-Husky scenario produces a byte-identical `MovesPlan`
  across two runs (extends the canary suite); passes by construction, proven by the test.

## 8. ADR & docs

A new ADR — *"Free-castering tow-pivot capability"* — records the orthogonal-flag decision, the
datum-pivot approximation with (b) as the deferred alternative, and the towing-vs-powered-taxi
rationale. It cross-references ADR-0010 (motion), ADR-0007 (cart = r=0), and ADR-0013 (canonical
per-aircraft data — same ethos). **Number allocated via `/new-adr` at implementation time**
(committed ADRs stop at 0013; 0014 is earmarked for #263 in an issue comment, so this will likely
be 0015 or later). The ADR lands **with** the implementation PR, per the project's #320/#263
convention. Also: `fleet.yaml` header note + a one-line CLAUDE.md/arc42 pointer.

*Alternative considered:* amend ADR-0010 instead. Rejected — this adds a fleet-schema field and a
capability concept (ADR-0013-shaped), so a new ADR reads cleaner than bolting it onto the
motion-vocabulary ADR.

## 9. Interactions with deferred milestone-#28 work

- **#320 (back-bias):** complementary, no conflict. Back-packing leaves the door cone clear;
  tighter turns only raise its towability floor. Neither reasons about turn geometry. Any order.
- **#263 (nose-out):** this is a direct **enabler**. #263 names the tailwheel gear class and
  depends on the planner cheaply realizing nose-out exits; the free-castering pivot makes that
  cheaper still. Sequence tow-pivot first for maximal nose-out payoff; #263 ships correctly
  without it. No #263 assumption changes.

## 10. Review plan

- `pr-review-toolkit:code-reviewer` — main pass.
- `pr-review-toolkit:type-design-analyzer` — `models.py` changes (new field + accessor).
- `determinism-guard` — planner *behavior* changes via the accessor (even though `towplanner.py`
  is untouched), so the byte-identical-plan contract is re-verified.
- **No `geometry-invariant-guard`** — decision (a) touches no geometry math. *(Decision (b) would
  require it.)*

## 11. Caveats & known limitations

- **`DubinsArc.length_m` mixes radians (pivot segments) and metres (straights)** — a pre-existing
  quirk that, with this change, appears in an *own-gear* plane's path for the first time. The
  renderer consumes paths via `sample()` (pose-based), not `length_m`, so visuals are unaffected.
  A code comment will warn against treating a flagged plane's `length_m` as a physical distance.
- **Datum-pivot approximation** (§4.3a): faithful only where the datum ≈ main gear. True for the
  Husky; the opt-in flag keeps it from being applied where it isn't.
- **`cart_eligible`-on-a-cart pre-existing gap:** `effective_turn_radius_m()` keys on the
  `always_cart` movement mode, not a placement's `on_carts` state, so a `cart_eligible` plane
  placed on a cart is *not* given pivot motion today. Out of scope here; noted for completeness.

## 12. Out of scope / future

- Main-gear-offset pivot (§4.3b) — a fidelity ADR if a far-offset-datum plane is ever flagged.
- Flagging planes beyond the Husky — a club data call as real measurements arrive.
- Modelling powered self-taxi distinctly from towing.

## 13. Process

- Branch: `feature/free-castering-tow-pivot` off `develop`.
- Tracking issue: file in milestone #28 at implementation time (`Closes #N` in the PR body), per
  "no code without an issue".
- Supervised work only (touches determinism-guarded planner behavior); no unattended worktree
  dispatch.
