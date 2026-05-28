# Wheels as canonical per-aircraft data (#322)

Status: **DESIGN APPROVED 2026-05-28** — awaiting implementation plan.

Issue: [#322 fleet/geometry: wheel positions should be physically realistic](https://github.com/DocGerd/hangarfit/issues/322).
Blocks: [#321](https://github.com/DocGerd/hangarfit/issues/321) (cart glyph at wheel positions), partially conditions [#320](https://github.com/DocGerd/hangarfit/issues/320) (back-bias placement).

---

## 1. Problem

Two surface representations of the same physical property — *where the wheels of each aircraft are* — disagree in the current code:

- `data/fleet.yaml` carries `turn_radius_m` per aircraft, used by the Reeds–Shepp tow-path planner since Phase 3a. No wheel positions.
- `src/hangarfit/visualize.py` invents wheel-glyph positions on the fly via heuristic fractions of fuselage half-length (`_NOSE_GEAR_FRAC = 0.85`, `_MAIN_GEAR_FWD_FRAC = 0.30`, `_MAIN_GEAR_TAILDRAGGER_FWD_FRAC = 0.45`, `_MAIN_GEAR_LATERAL_FRAC = 1.6`). The module's own comment admits these are "intentionally approximate".

There is no link between the two: a contributor can change `turn_radius_m` to a value that doesn't match the rendered wheelbase, or vice versa, and nothing notices.

A separate latent inconsistency: the `fleet.yaml` header comment states *"Origin of plane-local frame = main-gear / cart centroid"*, but visualize.py draws the main gear *offset* from origin by `_MAIN_GEAR_FWD_FRAC * fus_half_len`. The data follows visualize.py's convention (origin ≈ a per-aircraft anchor near CG/wing-root), not the header's stated rule. The header is aspirational.

## 2. Goals

Make wheel positions **first-class per-aircraft data** in `fleet.yaml`, with the following properties:

1. Wheels are the canonical source for the renderer (visualize.py reads them; no heuristic).
2. `turn_radius_m` remains independent (empirical taxi radius, not derived from wheelbase + steering geometry), but is **cross-checked at load time** against the wheel-derived wheelbase for plausibility.
3. The schema is compact and symmetry-enforcing (no left/right mismatch possible).
4. The `fleet.yaml` header docs are reconciled with the data.

### Non-goals

- **Wheel collision participation.** Wheels do not enter the parts model. If/when wheel-pad collisions become relevant, that is a separate ADR + parts-model extension.
- **`turn_radius_m` derivation.** No `max_steer_angle_deg` field; no derived radius; no removal of `turn_radius_m`. The empirical-vs-derived tradeoff was settled in favour of empirical (see §5 — Decisions).
- **Cart glyph relocation.** That is #321's job. This design leaves the cart code (`_draw_cart_glyph`) untouched.
- **Origin re-anchoring.** Moving every aircraft's part-offsets so the plane-local origin lands exactly at the main-gear centroid was considered (schema option γ) and rejected — the data churn would touch every fixture file with hard-coded placement coordinates and yields no functional gain under the chosen scope.
- **Towplanner pivot-point refinement.** The pivot stays at `placement.x_m / y_m` (the plane-local origin), as today. Future work (#263 nose-out) might want the pivot to be the main-gear centroid; that is out of scope here.
- **Real wheel measurements.** Backfill values come from published specs and conservative estimates. The `measured: false` flag stays as-is — real measurements continue under #79 and the broader audit.

## 3. Schema

A new **required** `wheels:` block on every aircraft. Three shapes by `gear`:

```yaml
# tailwheel (Husky, Zlin, Cessna 140)
wheels:
  main_offset_x_m: 0.20          # plane-local +x: forward of origin
  track_m: 1.80                  # mains lateral spread (each main at y = ±0.90)
  third_wheel_offset_x_m: -3.40  # NEGATIVE = tail wheel (aft of origin)

# nosewheel (Fuji, Wild Thing, Cessna 150, CTSL, FK9)
wheels:
  main_offset_x_m: -0.10
  track_m: 1.80
  third_wheel_offset_x_m: 2.50   # POSITIVE = nose wheel (forward of origin)

# monowheel (Falke)
wheels:
  main_offset_x_m: 0.0
  # NO track_m, NO third_wheel_offset_x_m
  # Outriggers remain render-only via the wing footprint (current behaviour).
```

**Loader rules** (`src/hangarfit/loader.py`):

- A `wheels:` block is **required**. Missing → `InvalidFleetError`.
- Key set depends on `gear`:
  - `monowheel` → exactly `{main_offset_x_m}`.
  - `nosewheel` / `tailwheel` → exactly `{main_offset_x_m, track_m, third_wheel_offset_x_m}`.
- Extra or missing keys for the gear type → `InvalidFleetError` with a clear message.
- `track_m > 0`, `main_offset_x_m` and `third_wheel_offset_x_m` finite, `track_m` finite.
- For `nosewheel`: `third_wheel_offset_x_m > main_offset_x_m` (nose is forward of mains).
- For `tailwheel`: `third_wheel_offset_x_m < main_offset_x_m` (tail is aft of mains).

## 4. Cross-check

After parsing, for each aircraft with `turn_radius_m is not None` (i.e. not `always_cart`) and a defined wheelbase (i.e. not monowheel):

```python
wheelbase = abs(third_wheel_offset_x_m - main_offset_x_m)
if not (0.5 * wheelbase <= turn_radius_m <= 5.0 * wheelbase):
    raise InvalidFleetError(
        f"Aircraft {aircraft.id!r}: turn_radius_m={turn_radius_m} is "
        f"implausible given wheelbase={wheelbase:.2f}m "
        f"(expected {0.5*wheelbase:.2f}..{5.0*wheelbase:.2f}). "
        f"Either fix the wheel positions or fix turn_radius_m."
    )
```

**Skipped** for:

- `always_cart` aircraft (Falke, Wild Thing, Zlin Savage) — `turn_radius_m` is `null`; the cart pivots in place.
- Monowheel aircraft — no third wheel, no wheelbase concept. Falke happens to be both `always_cart` and `monowheel`, so it is skipped on both counts.

**Why this band:** The real physical relationship is `turn_radius ≈ wheelbase / tan(max_steer_angle)`. For typical small-aircraft steer angles (20–35°), this puts the multiplier in the 1.4×–2.7× range. A 0.5×–5× band catches the failure modes that matter (unit mixups, swapped main/third offsets, typos producing nonsense wheelbases) without forcing a steering model into the data or requiring per-aircraft fudge factors. Adjust the constants only if a real-fleet measurement reveals a band-edge case.

**Hard error, not warning:** silent warnings get ignored in CI logs. A fleet that doesn't pass the band has either bad wheel data or a bad `turn_radius_m`; both need to be looked at before the data ships.

## 5. Decisions and alternatives

| Decision | Choice | Rejected alternative | Why |
|---|---|---|---|
| Scope of motion-model coupling | **B — render + cross-check** | A (render-only): doesn't fully resolve the "two representations disagree" framing. C (collision participation): forces a parts-model extension + canary re-bake; too much blast radius for one PR. | B closes the inconsistency at the schema level without forcing a motion-model rewrite. Wheel-collision can become a follow-up ADR if/when needed. |
| Schema shape | **β — compact measurements** | α (explicit per-wheel x/y): verbose; loader must enforce L/R symmetry by convention rather than structure. γ (compact + re-anchor origins): data churn across every fleet entry and every test fixture with a hard-coded placement coordinate, with no functional payoff. | β documents intent (this is a wheelbase, this is a track), encodes symmetry structurally, stays compact. |
| `turn_radius_m` policy | **Independent + cross-checked** | Derive from wheelbase + steering model: requires a `max_steer_angle_deg` field we don't have and that varies per aircraft. Replace entirely: same problem. | `turn_radius_m` is empirical (measured taxi radius). Modelling steering geometry honestly needs more data than we have; keeping `turn_radius_m` as an independent empirical value is more truthful. |
| Cross-check strictness | **Loose band, hard error** | Tight steered-tricycle band (1.73× wheelbase ± 50%): would force a per-aircraft override for taildraggers. Warning-only: silent rot. No automated check (test-only): doesn't fail at the CLI for downstream users. | Loose-and-hard is the smallest mechanism that catches the failure modes that actually happen. |
| `wheels:` block presence | **Required** | Optional with heuristic fallback: leaves a known landmine (any future aircraft missing the block falls back to the heuristic, recreating the inconsistency). | Single source of truth. visualize.py's heuristic constants delete cleanly. |
| `measured` flag granularity | **Single existing flag** | New `wheels_measured` sub-flag: more bookkeeping for no current benefit (we don't have a partial-measurement workflow). | Wheel positions are just more dimensions; `measured: false` already covers everything until it's flipped to `true`. |
| Origin convention | **Keep per-aircraft anchor, fix the docs** | Re-anchor every aircraft so origin = main-gear centroid (option γ): data churn across all fixtures. Leave the docs wrong: known landmine. | Cheapest fix that ends the latent docs-vs-data contradiction. The "origin = main-gear centroid" intent is recovered as a *derived* point via `wheels.main_offset_x_m`, accessible whenever the planner needs it (e.g. for future #263). |

## 6. Components

### 6.1 `src/hangarfit/models.py`

```python
@dataclass(frozen=True)
class Wheels:
    """Plane-local wheel positions for one aircraft.

    Origin is the per-aircraft anchor that ``Placement.x_m / y_m`` refers to —
    the same origin every other Part offset is measured from. Each main wheel
    sits at ``(main_offset_x_m, ±track_m/2)``; the third (nose or tail) wheel,
    if present, sits at ``(third_wheel_offset_x_m, 0)``.
    """

    main_offset_x_m: float
    track_m: float | None
    third_wheel_offset_x_m: float | None

    @property
    def positions(self) -> list[tuple[float, float]]:
        """Plane-local ``(x, y)`` of every wheel.

        Returns 1 entry for monowheel (``(main_offset_x_m, 0)``) or 3 entries
        for tricycle/tailwheel (two mains at ``(main_offset_x_m, ±track_m/2)``
        plus the third wheel at ``(third_wheel_offset_x_m, 0)``). The order is
        stable: mains first (``+y`` then ``-y``), then the third wheel.
        """

    @property
    def wheelbase_m(self) -> float | None:
        """``abs(third - main)``, or ``None`` for monowheel."""
```

`Wheels.positions` is the **only** accessor every consumer should use. Visualize, future #321 cart-glyph relocation, and any future towplanner pivot-point work all go through it.

```python
@dataclass(frozen=True)
class Aircraft:
    # ... existing fields ...
    wheels: Wheels   # required (no default)
```

### 6.2 `src/hangarfit/loader.py`

- New `_parse_wheels(entry: Mapping[str, Any], gear: Gear, aircraft_id: str) -> Wheels` helper.
- Called from the existing per-aircraft parse path.
- After the full `Aircraft` is constructed, a `_validate_wheels_vs_turn_radius(aircraft)` runs the §4 cross-check.
- All errors raise `InvalidFleetError` with a `f"Aircraft {id!r}: …"` prefix matching the existing loader convention.

### 6.3 `src/hangarfit/visualize.py`

**Delete:**

- `_NOSE_GEAR_FRAC`, `_MAIN_GEAR_FWD_FRAC`, `_MAIN_GEAR_TAILDRAGGER_FWD_FRAC`, `_MAIN_GEAR_LATERAL_FRAC` constants.
- The `if/elif aircraft.gear` branches inside `_draw_gear_glyph` (~30 lines).
- The fuselage-segment reconstruction (`nose_x`, `fus_aft_x`, `fus_cx`, `fus_half_len`, `fus_half_wid`) inside `_draw_gear_glyph` — no longer needed once wheel positions are data-driven.

**Replace `_draw_gear_glyph` body with:**

```python
def _draw_gear_glyph(ax, placement, aircraft):
    if placement.on_carts:
        _draw_cart_glyph(ax, placement)
        return
    for u, v in aircraft.wheels.positions:
        wx, wy = local_to_world(u, v, placement)
        _add_wheel(ax, wx, wy)
```

`_draw_cart_glyph`, `_CART_DECK_HALF_LENGTH_M`, `_CART_DECK_HALF_WIDTH_M` are **not** touched — that is #321's surface.

### 6.4 `data/fleet.yaml`

- Backfill each of the 9 aircraft with a `wheels:` block (see §7).
- Update the header comment block: replace the "Origin = main-gear / cart centroid" paragraph with one stating that origin is a per-aircraft anchor and that the main-gear centroid is `(wheels.main_offset_x_m, 0)`.

### 6.5 New ADR

`docs/adr/0013-wheels-canonical-data.md`, peer to ADR-0001 (parts model) and ADR-0012 (fuselage split). Captures:

- **Decision:** Wheels are explicit data in `fleet.yaml`; `turn_radius_m` is independent + cross-checked.
- **Context:** the docs-vs-data inconsistency described in §1.
- **Alternatives considered:** §5 table.
- **Consequences:** cart-glyph rework (#321) becomes mechanical; #263 nose-out gets a documented main-gear-centroid accessor when it eventually needs one; wheel-collision is left as a future ADR.

## 7. Fleet data backfill

For each of the 9 fleet entries, supply a `wheels:` block whose values come from:

- **Published specifications** where available: Cessna 140 / 150, Fuji FA-200, Aviat Husky, Flight Design CTSL, FK9 Mk II, Scheibe SF-25E Falke.
- **Conservative estimates** for the ultralight subset (Wild Thing, Zlin Savage) — derive from fuselage dimensions and class-typical wheelbase/track.

**Acceptance for backfill:** every current `turn_radius_m` value passes the 0.5×–5× cross-check against the backfilled wheelbase. If any aircraft fails the band, prefer adjusting the wheel positions (we are reading those off published specs anyway) over adjusting `turn_radius_m` — adjusting `turn_radius_m` would shift Reeds–Shepp solutions and re-shuffle canaries.

`measured: false` stays on every aircraft — wheel positions inherit the same flag.

## 8. Determinism / canary impact

**Expected: none.** Option B was deliberately chosen so `turn_radius_m` values don't change, which means:

- Reeds–Shepp solutions don't shift.
- `tests/test_solver_canaries.py` and `tests/test_towplanner_*.py` baselines unchanged.
- The `determinism-guard` subagent passes without canary regeneration.

**Risk:** if a single fleet entry can't be backfilled with a wheelbase that keeps the current `turn_radius_m` inside the band (and the band itself is correct per §4), we have to either fudge that aircraft's wheelbase, widen the band, or accept a canary re-bake. The expected case from spec-checking is that all 9 entries fit the loose 0.5×–5× band comfortably; that judgment is deferred to backfill time and is the gate that decides whether this PR stays small or grows.

## 9. Tests

### New

- `tests/test_loader_wheels.py`:
  - Happy path: every gear type loads a valid aircraft.
  - Missing `wheels:` block → `InvalidFleetError`.
  - Wrong key set for `gear` (e.g. `track_m` on monowheel, missing `third_wheel_offset_x_m` on nosewheel) → `InvalidFleetError`.
  - Sign-direction violations (nosewheel with negative `third_wheel_offset_x_m`, tailwheel with positive) → `InvalidFleetError`.
  - Cross-check pass and fail (both ends of the band) → respectively load and `InvalidFleetError`.
  - Cross-check skipped for `always_cart` (no error even when wheelbase would otherwise fail).

- `tests/test_visualize_wheels.py`:
  - Loaded aircraft renders without exception.
  - Glyph circles land at expected world coords (use `_add_wheel` mock or capture Axes children).
  - `on_carts=True` still falls through to `_draw_cart_glyph` (regression guard for the #321 surface).

### Extended

- `tests/test_models.py`: `Wheels.positions` returns the right list for each gear type; `Wheels.wheelbase_m` is `None` for monowheel.

### Helper

`tests/conftest.py`:

```python
def make_test_aircraft(*, gear: Gear = "nosewheel", **overrides) -> Aircraft:
    """Build a minimal valid Aircraft for tests, with sensible wheel defaults."""
```

Existing inline-aircraft fixtures migrate to this helper to absorb the new boilerplate. Fixtures that deliberately construct invalid aircraft (loader error tests) keep their explicit dict-of-dict form.

## 10. PR shape

- **Branch:** `feature/wheels-canonical-322`, off `develop`.
- **Base:** `develop`.
- **Expected size:** ~600 LOC, dominated by `fleet.yaml` data + the new ADR.
- **Review subagents:**
  - `geometry-invariant-guard` — defense in depth, even though geometry.py is not edited (wheel coords feed through `local_to_world`).
  - `determinism-guard` — sanity check that towplanner.py is untouched.
  - `pr-review-toolkit:code-reviewer` — main pass.
  - `pr-review-toolkit:type-design-analyzer` — models.py adds a new dataclass.
  - `pr-review-toolkit:silent-failure-hunter` — loader gains error paths.
  - `pr-review-toolkit:comment-analyzer` — fleet.yaml header rewrite + ADR text.
- **Unblocks:** #321 (cart glyph at wheel positions). Partially conditions #320 (back-bias placement) by making wheel-derived turn radius accessible for future tow-routability checks.

## 11. Acceptance criteria

1. `data/fleet.yaml` carries a `wheels:` block on every aircraft.
2. `src/hangarfit/loader.py` rejects an aircraft missing `wheels:` or with a wrong key set for its gear.
3. `src/hangarfit/loader.py` rejects an aircraft whose `turn_radius_m` falls outside the 0.5×–5× wheelbase band (own-gear, non-monowheel only).
4. `src/hangarfit/visualize.py` reads wheel positions from `aircraft.wheels.positions`; the `_NOSE_GEAR_FRAC` / `_MAIN_GEAR_*_FRAC` constants are deleted.
5. `data/fleet.yaml` header comment block reconciled with the actual origin convention.
6. New ADR-0013 committed under `docs/adr/`.
7. `pytest -q` green; `ruff check` and `ruff format --check` clean; `mypy src/hangarfit/` clean.
8. Determinism canaries unchanged (or, if any fleet entry forces a re-bake, that re-bake is in this PR with the determinism-guard subagent's blessing).
