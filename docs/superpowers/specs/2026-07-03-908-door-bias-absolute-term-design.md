# #908 — Absolute door-distance soft term (design)

**Status:** design approved (agent-panel + user decisions, 2026-07-03). Implementation gated on #915 merge + `determinism-guard` review. Capability C2 of the editor redesign spec (`2026-07-03-editor-full-frontend-redesign-design.md`).

## Motivation

`Scenario.door_order` (#614) today is only a *relative*, post-hoc **selection tiebreak**
(`_door_order_deviation`, a Kendall-tau inversion count) — it orders already-valid basins but
never *steers* placement, and is provably inert for a lone `#1` (needs ≥2 placed bodies). The
#907 editor lets a user rank "#1 nearest the door", but the common exclusive single-`#1` export
therefore does nothing. #908 adds the missing capability: an **absolute** door-attraction term
that actively pulls the rank-1 plane toward the door (`y=0`) during the spread hill-climb —
best-effort ("**if possible**"), complementing (not replacing) the relative tiebreak.

## Settled decisions

- **Activation = hybrid** (panel 4/4). `SearchConfig.door_bias_weight: float = 0.0` (verbatim mirror
  of `back_bias_weight`, `models.py:1846`), so every `SearchConfig()`-constructed caller (determinism
  canaries, `ml/`, `bench`) is **byte-identical**. The `solve` CLI bakes `_DOOR_BIAS_DEFAULT_WEIGHT`
  when `scenario.door_order is not None` (mirror of the `--back-fill` bake, `cli.py:826`), with a
  `--no-door-bias` opt-out. The #907 editor exports through `solve`, so `door_order` steers out-of-box —
  no silent no-op, and no `float | None` sentinel (the CLI yields the three user states by computing the
  concrete float before constructing `SearchConfig`, consistent with models.py rejecting `None` for back_bias).
- **Weight = rank1_only** (panel 3/1). Only `door_order[0]` is pulled doorward; ranks 2..N are left to the
  shipped relative tiebreak. Clean two-stage split (absolute steer for the top pick + relative select for
  the tail), N-independent magnitude, cannot fight the primary selection key. `steep_decay` (`0.5**i`) is the
  reserved, forward-compatible upgrade if multi-VIP graded cascades are ever wanted; `linear` rejected
  (magnitude couples to N; can manufacture inversions the primary key then penalizes).

## Term

`_door_bias_energy(placements, scenario) -> float`:
- `y_{rank1} / length_m` when `door_order` is **truthy** (non-`None` **and** non-empty) and
  `door_order[0] ∈ placements`; else `0.0` (guards both `None` and the loader-permitted empty `()`,
  and a maintenance-away rank-1). The `search.door_bias_weight` multiplier is applied at the `_energy`
  call site, mirroring `back_bias_weight * _back_bias_energy`.
- The sign-flipped mirror of `_back_bias_energy`'s `(length−y)/length`: **minimized when rank-1 sits at the
  door (`y=0`)**. Division-only — **no `math.exp`** → cross-machine byte-safe (sidesteps the libm caveat).
- RNG-free; iterate any placed ids in `sorted` order for float-sum order-stability. Skip rank-1 if absent
  from `placements` (e.g. a maintenance-away plane), exactly like `_back_bias_energy` / `_region_energy` /
  `_door_order_deviation`. No fallback to rank-2 — simplest deterministic "if possible" semantics.

## Integration (`solver.py`)

Fold into `_spread`'s `_energy` closure (~1587), gated like `back_fill` / `region_active`:

```python
door_active = search.door_bias_weight > 0.0 and bool(scenario.door_order)  # truthy: non-None AND non-empty
...
if door_active:
    e += search.door_bias_weight * _door_bias_energy(trial, scenario)
```

Relax the `<2 planes` early-bail guard (~1569) with `and not door_active` so a lone `#1` is still pulled.

### Back-fill tug-of-war (the correctness-critical fix)

`back_bias` (default ON, ~1.0) pulls rank-1 **deep**; door-bias pulls it **doorward**; net gradient
`(door_w − back_w)/length`. If `door_w ≤ back_w` the feature **silently no-ops**. Fix: **exempt the rank-1
id from `_back_bias_energy`'s sum while door-bias is active** — "everyone back-fills except the door-seeker."
Unranked planes vacating the front then *helps* rank-1 reach the door. (Chosen over "calibrate `door_w` above
`back_w`" — more robust, no fragile weight tuning.)

## Determinism (ADR-0003)

- Library default byte-identical (weight `0.0` ⇒ term never summed; the additive identity, same as back_bias).
- RNG-free, wall-clock-free, division-only ⇒ `determinism-guard` double-solve stays bit-identical, cross-machine safe.
- `determinism-guard` review **required** (touches the solver scoring surface).
- Add a **new determinism canary** on a `door_order` + steering fixture — today's canaries never exercise `door_order`.

## Blast radius

Only `tests/test_solver_door_order.py` + `tests/fixtures/scenario_door_order*.yaml` change behavior (the intended
#908 effect). Those tests assert **only determinism + validity, not layout coordinates**, so nothing pre-existing
breaks — only new steering assertions to author. **Zero determinism canaries / shipped examples / `data/` scenarios
set `door_order`** (verified).

## Testing

1. **Unit** `_door_bias_energy`: rank-1 present / absent (maintenance-away) / single-`#1` / `y`-normalization; the
   back-fill exemption of rank-1 when door-active.
2. **Steering (merge gate):** a roomy fixture where rank-1 lands at **strictly smaller `y`** with the term on vs off.
3. **Determinism canary:** `door_order` + steering double-solve byte-identity (covers the steering path the current
   canaries miss).
4. **Byte-identity:** default (`door_bias_weight=0`) plans unchanged; `--no-door-bias` forces off; auto-on when
   `door_order` set.

## Scope / honesty

Best-effort "**if possible**": in dense fills (Herrenteich all-8) validity + inter-plane repulsion dominate, so the
term is cosmetic-within-slack. **Do not crank the weight** to force it (collapses spread, congests the door).

## ADR

Amend **ADR-0008** (inter-plane spread soft-preference) to record the door-bias steering term alongside
`back_bias` / `region` (back_bias amended ADR-0008 the same way), or add a short cross-reference note.

## CLI surface (mirror `--back-fill`)

`--door-bias` / `--no-door-bias` boolean pair; default = auto (on when `door_order` set). Exact flag naming +
whether to expose an explicit weight override deferred to the implementation plan.
