# Tow-pivot capability (`tow_pivotable`) — design

- **Date:** 2026-05-29
- **Status:** **Shelved as a standalone change — to be implemented *together with* [#263](https://github.com/DocGerd/hangarfit/issues/263) (nose-out parked heading).** Design + characterization complete; coupled to #263 because the pivot's functional payoff is the nose-out motion.
- **Target milestone:** #28 (v0.9.0), folded into #263.
- **Subsystem:** `towplanner` motion model (via a `models.py` accessor + a fleet-data flag).
- **Related:** [ADR-0010](../../adr/0010-reeds-shepp-motion-model.md) (Reeds–Shepp motion),
  [ADR-0007](../../adr/0007-tow-path-planner-v1-scope.md) (cart = own-gear with `turn_radius_m = 0`),
  [ADR-0013](../../adr/0013-wheels-canonical-data.md) (canonical per-aircraft data),
  **#263** (nose-out — the motion this enables), #320 (back-bias — complemented).

> **Naming note.** An earlier draft called the flag `free_castering`. It is renamed
> **`tow_pivotable`** because the capability is reached by *two* mechanisms (see §3): a
> free-castering tailwheel, **or** pressing the tail down to lift a nosewheel off the ground.
> The planner-relevant fact is "can be pivoted about the main gear when towed," not the gear type.

---

## 1. Origin & honest problem statement

This started from an observation that the planner models every own-gear plane as a car-like
Reeds–Shepp vehicle with a fixed `turn_radius_m` (5.0 m for the Husky), which models *powered
self-taxi* — not *towing*. The hypothesis was that this conservatism is **why dense fills are
un-towable**.

**That hypothesis was tested and falsified** (see §6 Characterization). Modelling the Husky as a
zero-radius pivot does **not** make the six-plane fill routable: the binding constraint there is
**wing-transit geometry** (a 10.82 m wingspan cannot thread the corridor between already-placed
planes), which no turn-radius change addresses. A valid static layout does not guarantee an
evacuation path — which is exactly why the tow planner is a separate stage.

**What the capability *is* good for** (also measured, §6): physical **fidelity** of the towing
motion, **shorter/cleaner reorientation paths** (a 180° nose-out flip plans ~15% shorter as a
pivot than as an arc loop), and being the **enabler for #263 nose-out** — facing-the-door parking
becomes a cheap in-place pivot instead of a wide loop. Because that payoff is realized *through*
#263, this design is shelved as a standalone PR and folded into #263.

## 2. Goal & non-goals

**Goal.** Let a plane *declared* tow-pivotable be planned with the realistic towing motion —
pivot in place, push straight, pivot — modelling how it is actually hand/tail-tug manoeuvred.

**Non-goals.**
- **Not** a dense-fill towability fix (falsified — §6). That needs corridor-aware packing or the
  deferred #336 RRT-Connect, not this.
- Not modelling powered taxi (`turn_radius_m` stays as the documented powered figure).
- Not re-centering the pivot on an arbitrary main-gear offset (§4.3 alternative (b), deferred).
- Not changing cart-eligibility or which planes ride carts.

## 3. The capability and its two mechanisms

`tow_pivotable` means **the airframe can be rotated about its main-gear axle in place when
towed**, reached by either:

1. **Free-castering tailwheel** — the tailwheel swivels freely; lock/load one main and swing the
   tail, the airframe rotates within its own length (Aviat Husky).
2. **Nose-lift** — press the tail down so the nosewheel leaves the ground; the plane then pivots
   on its mains exactly like (1) (light nosewheel types — **FK9, CTSL** per the club operator).

Both are operational facts about ground handling, not gear-type inferences — hence an explicit,
declared flag (ADR-0013 ethos), not a `gear == "tailwheel"` heuristic.

### Scope (initial flag values)

| Plane | Gear | Wingspan | Main gear vs datum | `tow_pivotable` | Mechanism |
|---|---|---|---|---|---|
| aviat_husky | tailwheel | 10.82 m | +0.20 m | **true** | free-castering tailwheel |
| fk9_mkii | nosewheel | 9.85 m | −0.10 m | **true** | tail-down nose-lift |
| ctsl | nosewheel | 8.59 m | −0.10 m | **true** | tail-down nose-lift |
| cessna_140 | tailwheel | 10.16 m | +0.50 m | *club data call* | (tailwheel — likely, unconfirmed) |
| *(all others)* | — | — | — | false (default) | — |

The datum-pivot approximation (§4.3) holds for all of these — main gear within 0.5 m of the datum.

## 4. Design

### 4.1 Data model — orthogonal boolean, not a movement mode (load-bearing)

`tow_pivotable` is a capability **orthogonal to `movement_mode`**, added as a `bool` field on
`Aircraft` (default `False`). It is deliberately **not** a new `MovementMode` value:

- A tow-pivotable Husky is **still on its own gear** — `on_carts = False`. The `Layout`
  consistency invariant (`always_cart ↔ on_carts`, `models.py:488`) and the solver's cart-pool
  accounting key off `movement_mode`. A new movement mode would tangle with both; an orthogonal
  boolean touches neither.
- It composes: `always_own_gear + tow_pivotable` (the Husky) and `cart_eligible + tow_pivotable`
  (FK9, CTSL — pivot on own gear *and* can ride a cart) both fall out with no special-casing.
  `always_cart + tow_pivotable` is redundant-but-harmless (already `0.0`); the loader stays
  permissive (the flag is gear- and mode-agnostic).

### 4.2 The accessor — the single behavioral change

`Aircraft.effective_turn_radius_m()` (`models.py:299`) is the *one* place the planner resolves a
plane's planning radius:

```python
if self.movement_mode == "always_cart" or self.tow_pivotable:
    return 0.0
return self.required_turn_radius_m()
```

A flagged plane returns `0.0`, and from `plan_path:1679` that flows through the **existing**
cart-pivot path with no further branching (`_primitives(0.0)`, the `pose_at`/`sample`/`_seg_cost`
r==0 branches, `plan_reeds_shepp → _plan_cart`). `required_turn_radius_m()` and `__post_init__`
are unchanged — a tow-pivotable own-gear plane still carries its positive `turn_radius_m` (the
documented powered-taxi figure); the planner just stops consulting it.

### 4.3 Pivot center

The r==0 pivot rotates about the plane's **datum** (the `Pose` `x_m`/`y_m`, which is the
plane-local origin). The mechanism is `DubinsArc.pose_at` (`towplanner.py:140-145`): for an `L`/`R`
segment at `r==0`, position is held fixed and only the heading `theta` advances. (Note:
`geometry.local_to_world`, `geometry.py:119`, is *unrelated* — it maps part vertices for the
collision/visualize code, not for path integration.)

- **(a) Pivot about the datum — CHOSEN.** Reuse the machinery verbatim; the `pose_at` integrator
  and `geometry.py` are untouched → **no geometry-invariant-guard review required**. Faithful here
  (all flagged planes' main gear is ≤0.5 m from the datum).
- **(b) Pivot about `main_offset_x_m` — DEFERRED.** General, but offsetting the pivot inside
  `pose_at` touches motion math → triggers the geometry sign-flip guard + a new canary, for a
  sub-metre gain on the current fleet. ADR records it as the future refinement.

### 4.4 Change surface

| File | Change | Notes |
|---|---|---|
| `src/hangarfit/models.py` | Add `tow_pivotable: bool = False` to `Aircraft`; add `or self.tow_pivotable` to the `0.0` branch of `effective_turn_radius_m()` | `MovementMode`, `__post_init__` unchanged |
| `src/hangarfit/loader.py` | Parse `tow_pivotable` (default `False`) | Boolean; no coupling to `gear` |
| `data/fleet.yaml` | `tow_pivotable: true` on `aviat_husky`, `fk9_mkii`, `ctsl` + comments; header doc | Others default `false` |
| `src/hangarfit/towplanner.py` | **None** | Flagged planes reuse the existing deterministic r==0 path |
| `geometry.py`, `collisions.py` | **None** | No motion math change under decision (a) |

## 5. Determinism

The tow planner is **RNG-free**; determinism is structural (fixed primitive-fan order, fixed
Reeds–Shepp word order with strict-`<` tie-break, a monotonic `(f, counter, node)` heap
tie-break). Reusing the **existing** r==0 path adds **no new primitive, word, cost constant,
ordering surface, or RNG** — the determinism contract (ADR-0003, `max_restarts`-scoped per #267)
holds by construction. A canary (§7) proves byte-identical `MovesPlan` across two runs.

## 6. Characterization (the evidence; 2026-05-29)

Method: monkeypatch `effective_turn_radius_m → 0.0` for the flagged set and compare against
baseline, on real fixtures and constructed goals.

**(a) Routability — pivot converts ZERO cases, regresses none.**

| Fixture | baseline un-routable | pivot un-routable | converted |
|---|---|---|---|
| 3-plane | none | none | — |
| bay-3 | none | none | — |
| 6-plane | `aviat_husky` | `aviat_husky` | **none** |

**(b) Geometry — why.** At the 6-plane parked slots, rotational headroom is tight for the
big-wing planes (`aviat_husky 11/72`, `ctsl 15/72`, `cessna_140 17/72` clear headings;
`fk9 37/72`, `fuji 49/72`). And the Husky can translate only 2.0 m toward the door before a wing
hits `fuji`. The block is **wing transit through the inter-plane corridor**, not turn radius. The
pivot's swept disc = wingspan (~10.8 m), so the window where a pivot fits but an arc-loop doesn't
is narrow for these aircraft.

**(c) Path quality / nose-out — the real win.** In open space, a 180° nose-out flip plans as
**23.7 m (pivot)** vs **27.8 m (arc loop)** — ~15% shorter and cleaner. Straight-in is equivalent
(~12 m). This is the #263 motion; the pivot is what makes nose-out cheap.

**Conclusion:** `tow_pivotable` is a **fidelity + path-quality + #263-enabler** change, **not** a
towability unlock. Hence: fold into #263.

## 7. Testing (when implemented with #263)

- **Unit (`models`):** `effective_turn_radius_m()` returns `0.0` for a tow-pivotable own-gear
  plane; field defaults `False`; `cart_eligible + tow_pivotable → 0.0`.
- **Loader:** `tow_pivotable` parsed; defaults `False`; round-trips.
- **Planner integration:** a flagged own-gear plane yields a pivot-style path (a `turn_radius_m ==
  0` `DubinsArc` with pivot segments).
- **Path-quality (the honest payoff test):** a nose-out 180° goal plans **shorter** as a pivot
  than as an arc (the 23.7 vs 27.8 m result), in an empty hangar with a feasible (in-bounds-wing)
  goal.
- **No-regression:** the 3-plane and bay-3 fills route identically with the flag on (converted/
  regressed both empty).
- **Determinism canary:** a tow-pivotable scenario produces a byte-identical `MovesPlan` across
  two runs.
- **NOT a test:** "the six-plane fill becomes routable" — falsified (§6); do not assert it.

## 8. ADR & docs

The tow-pivot decision is recorded **in #263's ADR** (the nose-out ADR, earmarked ADR-0014) as a
sub-section — the orthogonal-flag decision, the datum-pivot approximation with (b) deferred, and
the towing-vs-powered-taxi rationale — rather than a separate ADR, since the two ship together.
Cross-references ADR-0010 / ADR-0007 / ADR-0013. Plus a `fleet.yaml` header note and a one-line
CLAUDE.md/arc42 pointer.

## 9. Interactions

- **#263 (nose-out):** the pivot is the **enabler** — nose-out flips become ~15% cheaper in-place
  pivots. Implement together. The pivot has no standalone consumer until #263 exists.
- **#320 (back-bias):** complementary, no conflict; neither reasons about turn geometry.

## 10. Review plan (when built)

`code-reviewer` (main) + `type-design-analyzer` (`models.py`) + `determinism-guard` (planner
behavior changes via the accessor). **No `geometry-invariant-guard`** under decision (a).

## 11. Caveats & known limitations

- **`DubinsArc.length_m` mixes radians (pivots) and metres (straights)** — pre-existing; appears
  in an own-gear path for the first time with this change. The renderer uses `sample()` (poses),
  not `length_m`, so visuals are fine; a comment will warn consumers.
- **Datum-pivot approximation** (§4.3a): faithful where datum ≈ main gear (true for all flagged
  planes); the opt-in flag keeps it from misapplying.
- **`cart_eligible`-on-a-cart pre-existing gap:** `effective_turn_radius_m()` keys on the
  `always_cart` mode, not a placement's `on_carts`, so a `cart_eligible` plane *placed on a cart*
  is not given pivot motion today. Out of scope; noted.

## 12. Out of scope / future

- Main-gear-offset pivot (§4.3b) — fidelity refinement if a far-offset-datum plane is flagged.
- The actual dense-fill towability lever: corridor/wingspan-aware packing in the solver, or #336
  RRT-Connect. This spec explicitly does **not** address that.

## 13. Process

- Design committed on `feature/free-castering-tow-pivot` (this spec).
- Durable design + characterization summary posted as a comment on **#263**.
- Implementation happens with #263 (supervised; determinism-guarded planner behavior).
