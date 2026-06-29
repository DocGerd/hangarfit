# Per-commitment economics lever — banked marginal valid-coverage credit

- **Issue:** #812 (part of the #736 training-improvement backlog)
- **Status:** Design — approved mechanism + scope, pending spec review
- **Provenance:** chosen by a 17-agent adversarial design panel (4 proposers × 3 skeptic lenses + synthesizer) over a per-milestone carrot (latch form), a convex abstention stick, and a commitment-insurance scheme.
- **Scope decision (user, 2026-06-23):** carrot **alone** — the convex-stick adjunct is explicitly out of scope (deferred).

---

## 1. Problem

The empty-start `trio-notch` curriculum rung (3 aircraft, real Herrenteich notch hangar) plateaus at `valid_placed` (vp) ~0.33 on both seeds and never masters. Two prior levers were KILLED:

- **Witness-anchored start-state scaffold** (#736/#794): pre-park 1 of 3, drive 2 in → still vp 0.333.
- **Spatial-token cross-attention representation** (#809/#810): refuted — `trio-notch-anchored` converged to vp 0.333 *exactly* = the global-pool control, confirming a **representation change cannot move a reward-economics argmax**.

**Accepted root cause — marginal-commitment economics.** The policy validly parks one aircraft (the freebie; banks `r_first_valid` + `valid_park`), then **abstains** on the 2nd/3rd because the marginal economics make *place-nothing-new* the reward argmax:

- Committing the 2nd/3rd object risks the hard collision penalty `w_col·overlap` (clipped to −50 by `reward_clip`) when the tight notch gap is missed, **plus** the #714 terminal collapse to `eff_fraction=0` on an invalid whole-layout.
- The graded valid-park bonus `r_valid_park·exp(−misfit/grade_scale)` decays to ~0 when the gap is not found.
- Abstaining (keep the freebie, drive to step-budget) is a bounded small loss.

So for a modest success probability *p* on the 2nd object, **E[commit] < E[abstain]**. Signature on the plateau: `valid_rate≈1.0`, `fraction_placed≈0.33`, `vp≈0.33` (place-nothing-new). The *other* failure face is invalid-pile (`fraction` high, `valid_rate` low) — **a fix must not reopen it.**

## 2. The mechanism — banked marginal valid-coverage credit

Add **one** term to `step_reward` (`ml/reward.py`), appended **last** in the sum (preserving the existing trailing-`first_valid` partial-sum byte-identity):

```python
valid_progress = (
    w.r_valid_progress * float(max(0, ctx.valid_park_count - 1))
    if ctx.park_valid else 0.0
)
return hard + movement + soft + terminal + shaping + valid_park + first_valid + valid_progress
```

Semantics, keyed on the existing `ctx.park_valid` tri-state:

| `park_valid` | meaning | `valid_progress` |
|---|---|---|
| `None` | non-Park step | `0.0` |
| `False` | invalid Park (overlap / oob / egress) | `0.0` ← **structural pile firewall** |
| `True` | valid whole-layout Park | `r_valid_progress · max(0, valid_park_count − 1)` |

The `max(0, n−1)` offset means the **1st** valid object pays 0 (the existing `r_first_valid` owns the breakthrough kick off the place-nothing pole), the **2nd** pays `r_valid_progress`, the **3rd** pays `2·r_valid_progress`. This pays the **specific marginal objects (#2/#3)** the policy abstains on — `r_valid_park` is count-blind and `r_first_valid` fires once.

### Why banked-per-step, not terminal

The credit is booked at the **step the valid Park occurs**, so it enters GAE's `δ_t` at full weight. A *later* invalidating commit only discounts back in at `(γλ)^k`, so it cannot claw the banked credit away. This **decouples marginal-commit credit from the collapsible #714 terminal whole-layout flag** — which is the entire wall. (A success-only *terminal* carrot would be zeroed by the same `validity_conditional_terminal` collapse that already fails to fix this.)

## 3. The knob (`ml/types.py` `RewardWeights`)

```python
r_valid_progress: float = 0.0
# Banked per-valid-Park credit, scaled by the marginal valid-object count beyond
# the freebie: pays r_valid_progress*(n-1) on a Park where the WHOLE layout is valid
# (n = # driven-in objects). 0.0 -> term identically 0 -> byte-identical to today.
# Recipe lever for the trio-notch rung; the 4c-ii default stays 0.0.
```

**Default-neutrality (4c-ii):** `0.0 · anything = 0.0` across every `ctx` state → the returned scalar is bit-identical to today; no determinism / byte-identity canary needs re-baselining (mirrors the `r_valid_park` / `r_first_valid` default-zero byte-identity tests).

## 4. Env hook (`ml/env.py`)

Add one **internal** `RewardContext` field (additive, not an observation/SCHEMA change):

```python
valid_park_count: int = 0   # len(_parked) on a valid Park; 0 otherwise. Internal reward input only.
```

Populate it on the Park branch where the context is built (`env.py:~293`), reusing values already in hand:

```python
valid_park_count = len(self._parked) if park_valid else 0
```

- `park_valid` is already computed at `env.py:276` (`score.collisions_valid and not score.egress_blocked` — the authoritative product checker).
- `len(self._parked)` already includes the just-appended object at that point.
- The movement-primitive context (`env.py:~353`) leaves `valid_park_count` at its default `0`.

**No new oracle call, no new env running-state, no observation/tensor/SCHEMA change.** Optionally surface `valid_park_count` in `StepInfo.terms` as an additive, default-0 metrics key for vp-plateau diagnosis (grafted from the runner-up — diagnostic only; the reward already carries the count).

## 5. Argmax-shift argument (the EV that makes it work)

At the 2nd-object decision, let `p = P(2nd Park valid)`, with the recipe weights (`r_terminal=50`, `r_unplaced_penalty=25`, `r_valid_park=30`, `reward_clip=50`, `validity_conditional_terminal` ON):

- **E[abstain]** (drive the 2nd to budget; freebie already valid): `eff_fraction=1/3` → `terminal = 50·(1/3) − 25·(2/3) ≈ 0`. The freebie's `first_valid`+`valid_park` are sunk.
- **E[commit]** `= p·(valid_park≈30 + valid_progress=r_valid_progress + improved terminal eff_frac 2/3) + (1−p)·(hard≈−50 + terminal collapse → −r_unplaced_penalty≈−25)`.

Break-even *p* for `E[commit] > E[abstain]` at the **2nd-object** decision (illustrative single-step sketch; `dE = p·(55 + r_valid_progress) − (1−p)·75`, where the success branch counts `valid_park≈30 + r_valid_progress·1 + a realized terminal improvement ≈+25`, and `r_valid_progress·1` because `max(0, n−1)=1` at `n=2`):

| `r_valid_progress` | break-even *p* (2nd object) |
|---|---|
| 0 (today) | ~0.58 |
| 8 | ~0.54 |
| 15 | ~0.52 |
| 25 | ~0.48 |

The per-unit shift at the 2nd object is deliberately modest, but the term **compounds**: the **3rd-object** decision gets `+p·2·r_valid_progress` (twice the pull, since `max(0, n−1)=2` at `n=3`) while still staying under `reward_clip` — the clip-safe depth-targeting that beat the escalating-carrot's clip-colliding 3× payout. These figures are illustrative; the magnitude that actually moves the policy is empirical (§9's {6, 8, 12} sweep).

The key property: `valid_progress` adds an **unopposed** `+p·r_valid_progress` — it is structurally 0 on the *failure* branch and on *abstain*, so unlike a success-only carrot scaling `p·big` against the `(1−p)·catastrophe`, it does not have to overcome the collision tail. **For any p>0 a finite `r_valid_progress` flips the decision.** It is not potential-based (no `γΦ(s′)−Φ(s)` telescope), so Ng–Harada–Russell policy-invariance does not apply — it genuinely moves the optimum. Advantage normalization is affine/order-preserving, so it is not washed out.

## 6. Pile-safety (the firewall)

Gated on `park_valid is True` (the same product checker as #714/#694) → identically `0.0` on **any** invalid/overlapping Park. Because `_parked` is append-only with frozen poses, the moment overlap exists the whole layout is invalid, `park_valid` is `False` for that Park and every later one (you cannot un-append the overlapping pose), so the count is frozen and the invalid-pile basin pays **exactly zero** coverage credit — **by construction, not by tuning**. An overlapping Park still eats the full clipped −`w_col` stick, so a pile is strictly worse than a clean valid Park. It does not weaken `validity_conditional_terminal` or the L4 trust-region clip.

## 7. Determinism

The reward stays a pure function of `(ctx, weights)`: `float · int · bool` arithmetic, no RNG/clock. `valid_park_count` is reset implicitly each episode (it is recomputed per step from `_parked`, which `_reset_state` clears). CPU training stays bit-identical for a fixed seed (ADR-0027).

## 8. Testing

1. **Default-neutral byte-identity** — with `r_valid_progress=0.0`, the reward stream is bit-identical to today across a fixed action sequence (mirror the `r_valid_park`/`r_first_valid` byte-identity tests).
2. **Clip-headroom guard (grafted)** — a loud assertion/test that `r_valid_park + r_valid_progress·(k_max−1) + r_first_valid ≤ reward_clip`, so a future weight bump cannot *silently* clip away the banked marginal credit before GAE. For trio (`k_max=3`) at the recipe default: `30 + 8·2 + 0 = 46 < 50` ✓.
3. **Pile-firewall regression (grafted)** — a `[valid freebie, invalid pile, invalid pile]` episode earns **exactly 0** cumulative `r_valid_progress`, and `valid_park_count == 0` on each `park_valid=False` step. Pins the `if ctx.park_valid` / `else 0.0` gate so a future refactor cannot silently let an invalid Park carry a nonzero marginal count.
4. **Positive credit** — a `[valid, valid, valid]` trio episode banks `r_valid_progress·(0+1+2)` cumulatively (the 1st pays 0).
5. **Determinism** — `ml-rl-guard`'s fixed-action reward-stream diff with the knob on.

`ml-rl-guard` applies (touches `ml/reward.py`, `ml/types.py`, `ml/env.py`, `tests/ml/`).

## 9. Recipe + success criterion (separate from the merge)

The merged knob default stays `0.0`. The tuned trio-notch run sets `r_valid_progress` and runs **on top of** the existing #720 economics (do **not** drop `valid_park_grade_scale` — see §10):

```
… (the #736 notch recipe) … --r-valid-progress 8.0
```

**Success:** a two-seed `ml.gate` A/B on the `trio-notch` recipe, sweeping `r_valid_progress ∈ {6, 8, 12}` → WIN = vp clears the ~0.33 ceiling (target ≥0.6 on both seeds). The magnitude is **empirical**, not derivable a priori; this is gathered after merge, not part of the unit-tested change.

## 10. Caveats & scope boundaries

- **Carrot alone.** The convex-stick adjunct (`unplaced_penalty_exponent`) is **out of scope** for #812. Rationale: its own skeptic showed the terminal abstain gradient is already +25 today yet fails — deepening the *non-binding* branch is under-scaled vs the binding −50/step collision tail. Revisit only if the carrot A/B leaves a residual near-zero abstain floor.
- **Economics, not capability.** This lowers the economic *bar* but does not grant the *search capability* to find the valid tight-notch pose. It must compose with `valid_park_grade_scale > 0` (the uphill gradient into the slot), not replace it.
- **Easy-coverage risk.** It may move vp only *partially* — it can bank the two roomy objects while still abstaining on the genuinely tight notch pose. Every banked step is whole-layout-valid (no invalid commitment is ever paid), and the honest `valid_placed` gate still catches non-mastery, so this is a *partial-win* risk, not a correctness risk.

## 11. Open questions (for the human / the A/B)

1. **Magnitude:** is `r_valid_progress=8` enough to flip `E[commit]>E[abstain]` at the observed plateau *p*, yet under `reward_clip=50` alongside `r_valid_park=30`? → the {6,8,12} sweep answers it.
2. **`r_first_valid` interaction:** keep `r_first_valid` firing at count 1 (the offset means the carrot doesn't double-pay it), or fold it in? Default: keep both independent (preserves default-neutrality and prior recipes).
3. **Partial-win threshold:** if vp moves to e.g. 0.5 (two roomy objects) but not 0.9, is that a WIN worth shipping the knob for, or the trigger for the deferred convex-stick / a capability lever?
