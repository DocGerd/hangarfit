# Configurable cart inventory for `cart_eligible` planes — design (issue #210)

**Status:** Proposed — design for review (no code yet)
**Tracks:** [#210 Towplanner v2: configurable cart inventory for cart_eligible planes](https://github.com/DocGerd/hangarfit/issues/210)
**Author:** Claude (Opus 4.7), for review by @DocGerd
**Deferred from:** [ADR-0007](../../adr/0007-tow-path-planner-v1-scope.md) *Open question* section + tow-path spike Risk #5
**Aligns with:** [Towplanner v2 design (Reeds–Shepp + entry-cone)](2026-05-26-towplanner-v2-design.md) — this is the *cart-resource* half of the v2 milestone; that doc is the *motion-model* half.

This document is a **design proposal only**. It recommends decisions but commits no code. The user reviews the direction (especially the two open forks in §1 and §2, and the governance call in §3) before any implementation issue is opened.

---

## Ratified decisions — 2026-05-27 (user review)

The user reviewed this design and chose:

| Decision | Choice | Note |
|---|---|---|
| **§2.4 dolly vs tug** (load-bearing) | **Dolly** — a carted plane rests on its cart in the final layout | The per-layout *count* is the correct invariant; #210 reduces to "make the `1` configurable." No tug re-model. |
| **Fork A — source of `max_carts`** (§1) | **A1 `data/hangar.yaml`, default 1**, *plus* a **`--max-carts N` CLI override now** | *Extends the §1 recommendation.* Durable truth lives on `Hangar`; the CLI override is **in scope for #210** (not deferred). The override mutates the loaded `Hangar.max_carts` before any `Layout` is built, so it reaches `Layout.__post_init__` via the same `self.hangar.max_carts` read — no separate plumbing. |
| **Fork B — binding scope** (§2.3) | **Per-layout** (no per-sequence gate) | The K alternatives are a mutually-exclusive menu; `MovesPlan` stays tally-free. |
| **`max_carts = 0`** | "No spare carts for the eligible pool" — any `cart_eligible`-on-cart rejected; `always_cart` unaffected | |
| **Governance** (§3.2) | **Amend ADR-0007** (Amendments section) | Dolly + per-layout closes a question ADR-0007 parked; not a new ADR. |

**Scope consequence of the CLI override — add to the §3.1 impact map:**
- `src/hangarfit/cli.py`: `solve`/`check` gain `--max-carts N`; when given, it overrides `hangar.max_carts` on the loaded `Hangar` (a `dataclasses.replace` on the frozen `Hangar`) before layouts are built. Validation (`>= 0`) reuses `Hangar.__post_init__`.
- New CLI test: `--max-carts 2` admits two `cart_eligible` planes onto carts; absent ⇒ the data-file value stands.

The rest of this document is the reasoning that produced these choices; the options it weighs are retained as the decision record.

---

## 0. Problem statement (the two deferred forks)

Today, carts are a finite shared resource **only for `cart_eligible` planes**. `always_cart` planes are guaranteed their own carts and never draw from the limited pool. The current rule is a hard-coded inventory of **one** cart for the eligible pool, enforced in `Layout.__post_init__` (`src/hangarfit/models.py:397-405`):

```python
cart_count = sum(
    1
    for p in self.placements
    if p.on_carts and self.fleet[p.plane_id].movement_mode == "cart_eligible"
)
if cart_count > 1:
    raise ValueError(
        f"At most one cart_eligible plane may have on_carts=True (got {cart_count})"
    )
```

Two questions ADR-0007 deliberately left open:

1. **Configurable size** — replace the hard-coded `> 1` rejection with a configurable `max_carts` for the `cart_eligible` pool. *Where* does `max_carts` live, what is the schema, and what is the default?
2. **Binding scope** — the cap binds per *layout* today. A tow *sequence* spanning multiple candidate layouts could place a *different* `cart_eligible` plane on the (single) cart in each layout, and the planner happily plans all of them because each layout independently satisfies the per-layout cap. Should the inventory instead bind across the whole sequence?

Two invariants must survive whatever we choose:

- **`always_cart` planes stay outside inventory accounting.** They get their own carts; they never consume from the `max_carts` pool.
- **`MovesPlan` carries no cart-usage tally** (it says so in its own docstring, citing this ADR-0007 open question). ADR-0007 promised that adding one later is *non-breaking*. We preserve that property or justify breaking it.

---

## 1. Fork A — where does `max_carts` live?

The eligible-pool cart count is a **cross-reference invariant** of a `Layout` (it counts placements against fleet movement modes). The question is which input file supplies the *limit* the invariant checks against.

### Option A1 — `data/hangar.yaml` *(recommended)*

A new optional top-level field on the hangar floor-plan file:

```yaml
# data/hangar.yaml
length_m: 25.0
width_m: 18.0
door: { center_x_m: 9.0, width_m: 12.0 }
maintenance_bay: { center_x_m: 13.5, width_m: 9.0, depth_m: 9.0 }
clearance_m: 0.3
wing_layer_clearance_m: 0.2
max_carts: 1        # NEW — spare carts available for the cart_eligible pool
```

**Why it fits here.** A cart is a *physical thing the club owns and stores in this hangar* — the same category as the door width or the maintenance-bay rectangle. The number of spare carts is a property of the *site*, not of any individual airframe and not of a particular parking puzzle. `Hangar` already carries the other site-scoped scalars (`clearance_m`, `wing_layer_clearance_m`) that are defaulted in the loader, so the precedent is exact.

**Loader path.** `load_hangar` (`loader.py:109`) already defaults two scalars with `raw.get("clearance_m", 0.3)`. `max_carts` follows the identical idiom:

```python
max_carts=_to_int(raw.get("max_carts", 1), "max_carts"),   # default 1 ⇒ today's cap
```

(`_to_int` is a new sibling of the existing `_to_float`; integer because a fractional cart is meaningless. See §4.2.)

**Tradeoffs.**
- (+) Backward-compatible by construction: absence ⇒ default `1` ⇒ today's behaviour, byte-for-byte. No existing `hangar.yaml` or fixture changes.
- (+) `Hangar` is already threaded everywhere a `Layout` is built (loader, solver, towplanner all hold a `Hangar`), so the value is *already in scope* at every enforcement point with zero new plumbing.
- (+) Matches the mental model: "how many spare carts does *this hangar* have?"
- (−) Slight scope creep on `Hangar`, which has so far been pure floor-plan geometry. Mitigated: `clearance_m` is already a non-geometric policy scalar living there, so the type is not purely geometric today.

### Option A2 — fleet config (`data/fleet.yaml`)

A top-level `max_carts:` key on the fleet file, *outside* the per-aircraft entries.

**Tradeoffs.**
- (+) Keeps `Hangar` purely geometric.
- (−) **Category error.** `fleet.yaml` is a registry of *aircraft* — each entry is one airframe's wing type, gear, struts. Cart *count* is not a property of any aircraft; it is a property of the hangar's equipment. A top-level non-aircraft key on a per-aircraft registry is a smell (the loader's `load_fleet` iterates entries; a sibling scalar is an exception it must special-case).
- (−) The fleet is conceptually reusable across hangars (the same club fleet could be checked against two different hangars). Cart inventory is per-site, so it does not belong on the portable fleet.

### Option A3 — CLI flag (`--max-carts N`)

A `solve` / `check` flag overriding the data-file value.

**Tradeoffs.**
- (+) Easy what-if exploration ("what if we bought a second cart?").
- (−) **It is not durable.** The cart count is a stable fact about the club's equipment, not a per-invocation knob. Putting it only on the CLI means every script and every `Layout` constructed in code (tests, solver internals) has to re-supply it, and the *data* never records the truth. The CLI design doc (§2 of `2026-05-21-cli-design.md`) already drew this line: durable facts live in the data files; flags are overrides.
- (−) A flag cannot reach `Layout.__post_init__`, which is where the invariant lives — it would have to be plumbed as a constructor argument all the way down, which is exactly the threading Option A1 avoids by riding on `Hangar`.

### Recommendation — A1 (`data/hangar.yaml`), default `1`

`max_carts` is a site-equipment fact, so it lives on the hangar floor plan beside `clearance_m`. Default `1` makes absence reproduce today's behaviour exactly. A CLI override (A3) can be added *later* as a non-breaking convenience on top of A1, but is **out of scope** for #210 — the data field is the load-bearing decision; the flag is sugar.

**Where the limit physically lands.** Carrying `max_carts` on `Hangar` (not `Layout`) is deliberate: `Layout` already holds a `Hangar`, so `Layout.__post_init__` reads `self.hangar.max_carts` with no new field on `Layout` and no new constructor argument. This keeps `Layout`'s shape unchanged (only its invariant body changes) and means every existing call site that builds a `Layout` from a loaded hangar gets the limit for free.

---

## 2. Fork B — per-layout vs per-sequence binding

### 2.1 The two scopes, concretely

**Per-layout (today's scope).** The cap is checked once per `Layout`, against the placements in *that one* layout. `Layout.__post_init__` is the enforcement point. A `solve(..., alternatives=K)` run returns up to K independent layouts; each is validated on its own.

**Per-sequence.** A *sequence* here is the **set of K alternative layouts a single `solve` returns together** (`SolveResult.layouts`), each with its tow plan in `SolveResult.plans`. Per-sequence binding would mean: across all K alternatives, no *more than `max_carts` distinct `cart_eligible` planes* are ever placed on a cart.

> **Terminology caution.** "Sequence" is overloaded. ADR-0007's *tow sequence* is the **entry order within one layout** (`back_first_order`, the `moves` tuple of one `MovesPlan`) — every plane in one layout is towed in, one at a time, and a single shared cart could be **reused** serially as each carted plane reaches its slot. That intra-layout reuse is a *different* axis from the cross-*layout* question #210 actually asks. This doc uses **"per-sequence" to mean across the K alternatives of one `SolveResult`**, which is the scope the issue's example describes ("a tow *sequence* spanning multiple candidate layouts"). §2.4 addresses the intra-layout reuse axis explicitly, because it changes what the *per-layout* count should even mean.

### 2.2 The scenario where they diverge

`max_carts = 1`. Fleet has two `cart_eligible` planes, `cessna_140` and `cessna_150`. Run `solve(scenario, alternatives=2)`:

- **Alternative 0:** `cessna_140` on a cart, `cessna_150` on own gear. Per-layout cart count = 1. **Valid.**
- **Alternative 1:** `cessna_150` on a cart, `cessna_140` on own gear. Per-layout cart count = 1. **Valid.**

Per-layout binding: both alternatives pass; the user is offered two layouts, *each individually realisable with the single cart*. Per-sequence binding: the two alternatives *together* would use the cart for two different planes, which a naive reading calls a violation — **but the planes are never on carts at the same time.** Alternatives are *mutually exclusive choices*: the operator picks **one** layout to actually build. They never coexist physically.

### 2.3 Why per-sequence is the wrong binding for `SolveResult` alternatives — recommend **per-layout**

The K alternatives in a `SolveResult` are an **either/or menu**, not a plan executed in series. The hangar ends up in *exactly one* of them. Cart inventory is consumed by the layout the operator *commits to*, and that single committed layout is already validated per-layout. Binding the inventory *across* the menu would reject a perfectly buildable alternative purely because a *different, never-built* alternative also wanted the cart. That is a false positive — it shrinks the choice set for no physical reason.

Concretely, per-sequence binding on `alternatives=K` would make the realisable choice set depend on *which other alternatives the search happened to surface* — a non-local, order-dependent, and frankly surprising coupling. It also breaks the ADR-0003 determinism story in spirit: the validity of alternative 1 would depend on alternative 0's cart usage, so the alternatives are no longer independent draws.

**Recommendation: keep per-layout binding.** The inventory is consumed by the *committed* layout; each candidate layout is independently realisable; the menu is either/or. There is no real-world reading of "spanning multiple candidate layouts" under which two mutually-exclusive alternatives co-consume one cart.

### 2.4 The axis that *does* matter: intra-layout serial reuse

The genuinely interesting cart-resource question is **within a single layout**, and it is *upstream* of #210's framing: does a parked carted plane *keep* its cart, or is the cart pulled back out and reused for the next plane?

- **If carts stay under parked planes** (the parking layout is the resting state, carts left in place): then `max_carts` is exactly "how many `cart_eligible` planes may be *simultaneously parked on carts*" — which is precisely the per-layout count today. The count is the right semantics; only the literal `1` becomes `max_carts`.
- **If a single cart is towed back out and reused** for each plane in turn (the cart is a *tug*, not a permanent dolly): then a layout could park *any* number of `cart_eligible` planes "on carts" using one physical cart serially, and the per-layout *count* is the wrong invariant entirely.

The fleet's `always_cart` planes (gliders, etc.) and the `on_carts=True` resting state in `Placement` strongly imply the **dolly** reading: a carted plane *rests* on its cart in the final layout. Under the dolly reading, the per-layout count is correct and #210 reduces cleanly to "make the `1` configurable." **This doc assumes the dolly reading** (consistent with `Placement.on_carts` being a resting-state field and with `always_cart` planes occupying their carts permanently). If the user intends the tug reading, that is a *larger* re-model (cart as a reusable mobile resource with a return trip) and should be a separate ADR — flagged in §5 Open Questions.

### 2.5 If per-sequence were nonetheless wanted — where the accounting would live

For completeness (and because the user may define "sequence" as a real *multi-layout execution plan* in a future rearrangement feature, not today's either/or menu): per-sequence accounting must **not** go on `MovesPlan`, to preserve the ADR-0007 non-breaking guarantee. The natural home is a **new sequence-level container** computed at `solve`-time, not on the existing plan type:

- A small advisory diagnostic on `SolverDiagnostics` (e.g. `max_concurrent_carts_used: int`) — *additive*, defaulted, non-breaking, mirroring the existing advisory fields (`min_pairwise_gap_m`, `valid_basins_found`). This *reports* cross-layout cart pressure without *gating* on it.
- Or a dedicated `TowSequence`/`FillCampaign` aggregate type introduced **only when** a true multi-layout execution feature (rearrangement, ADR-0007's deferred v2 scope) lands. That feature does not exist yet, so introducing the container now would be speculative.

Either way, `MovesPlan` stays tally-free. The non-breaking guarantee holds because the accounting is *added beside* the existing types, never *into* `MovesPlan`.

**Recommendation: do not add per-sequence accounting now.** Keep per-layout binding (§2.3). If a future rearrangement feature needs a genuine multi-layout execution plan, introduce the accounting there as an additive diagnostic, not on `MovesPlan`.

---

## 3. Module impact map & governance

### 3.1 Files touched (under the recommended A1 + per-layout choice)

| File | Change | Notes |
|---|---|---|
| `src/hangarfit/models.py` | `Hangar` gains `max_carts: int = 1` field + `__post_init__` validation (`>= 0`); `Layout.__post_init__` replaces the literal `> 1` with `> self.hangar.max_carts` and updates the error message to name the limit. | `Hangar` is a frozen slotted dataclass; adding a field with a default is source- and pickle-compatible for keyword constructors. **Positional constructor callers break** (a new field in the middle of the signature) — audit needed; all current call sites use keywords (loader, fixtures), so low risk, but a test sweep is mandatory. |
| `src/hangarfit/loader.py` | `load_hangar` reads `max_carts` via `raw.get("max_carts", 1)` through a new `_to_int` coercion helper; out-of-range/non-int values wrap into `LoaderError`. | Mirrors the existing `clearance_m` default idiom exactly. |
| `data/hangar.yaml` | Add `max_carts: 1` with an explanatory comment (placeholder, like the rest of the file). | Optional in the schema; explicit here for documentation value. |
| `data/fleet.yaml` | **No change.** | Rejected Option A2 keeps fleet aircraft-only. |
| `src/hangarfit/towplanner.py` | **No change to logic.** The planner already routes whatever layout it is handed; `MovesPlan` stays tally-free. The towplanner docstring at `MovesPlan` (line ~216) that cites the ADR-0007 open question should be updated to point at the resolution (this design / its ADR). | Doc-only touch. |
| `tests/` | New cases per §4. | |
| `docs/adr/` | ADR amendment vs new ADR — see §3.2. | |
| `docs/architecture/08-crosscutting-concepts.md` | If `max_carts` becomes a documented site scalar, add it beside the default-clearances note. | Doc sweep. |

### 3.2 Governance — amend ADR-0007 or write a new ADR?

This decision **resolves an explicit Open question of ADR-0007**, and the resolution *agrees with* ADR-0007's framing (carts are a finite eligible-pool resource; `always_cart` stays out; `MovesPlan` stays tally-free). It does not reverse any ADR-0007 decision.

- **Amend ADR-0007** *(recommended)* — add an `## Amendments` entry (ADR-0007 already has an Amendments section, used for the #262 door-cone refinement) titled e.g. *"2026-05-xx — cart inventory configurable, per-layout binding (#210)"*. It records: `max_carts` on `hangar.yaml` (default 1), per-layout binding retained, `MovesPlan` stays tally-free, dolly-reading assumed. This keeps the cart-resource decision *with* the ADR that raised the question, exactly as the door-cone refinement was kept with the ADR that introduced the door gate.
- **New ADR** — warranted only if the decision were a genuinely novel cross-cutting choice. It is not: it is the *closure* of a question ADR-0007 explicitly parked. A new ADR would orphan the resolution from the question.

**Recommendation: amend ADR-0007** (Amendments section), not a new ADR. *Exception:* if the user chooses the **tug reading** of §2.4 (cart as reusable mobile resource), that *is* a new model and **warrants a new ADR** (next number per the `docs/adr/README.md` convention — `ls docs/adr/[0-9][0-9][0-9][0-9]-*.md | tail -1` + 1, currently ⇒ `0011`).

### 3.3 Required reviews (per CLAUDE.md)

- **`pr-review-toolkit:type-design-analyzer`** — mandatory: `models.py` changes (new `Hangar` field + changed `Layout` invariant).
- **`pr-review-toolkit:silent-failure-hunter`** — mandatory: `loader.py` changes (new `_to_int` coercion + default).
- **`determinism-guard`** — *not* triggered by the recommended scope (no `solver.py`/`towplanner.py` *logic* change; the towplanner touch is docstring-only). If implementation ends up touching `towplanner.py` logic, this becomes mandatory.
- **`geometry-invariant-guard`** — not triggered (no `geometry.py`/`collisions.py` change).

---

## 4. Test plan

All in `tests/` against the recommended A1 + per-layout design. Existing cart-rule tests (the current "at most one" assertions) must be located and updated to the parameterised form.

### 4.1 Models — `Layout` invariant against `Hangar.max_carts`

| # | Test | Assertion |
|---|---|---|
| 1 | `test_max_carts_default_one_unchanged` | A `Hangar` built without `max_carts` (loaded or constructed) ⇒ `max_carts == 1`; two `cart_eligible` planes on carts still raises (byte-identical to today's behaviour). **The backward-compat anchor.** |
| 2 | `test_max_carts_two_allows_two_eligible_on_carts` | `Hangar(max_carts=2)`, two `cart_eligible` planes on carts ⇒ `Layout` constructs **without** error; three ⇒ raises naming the limit `2`. |
| 3 | `test_max_carts_N_general` | Parameterised over `N ∈ {1, 2, 3}`: N eligible-on-carts valid, N+1 raises. |
| 4 | `test_always_cart_excluded_from_inventory` | `Hangar(max_carts=1)`, *all three* `always_cart` planes on carts **plus** one `cart_eligible` on a cart ⇒ valid (always_cart never counts); add a *second* `cart_eligible` ⇒ raises. **The exclusion invariant — must still hold.** |
| 5 | `test_max_carts_zero_forbids_eligible_carts` | `Hangar(max_carts=0)`: any `cart_eligible` on a cart raises; `always_cart` planes on carts still valid (decision: 0 means "no spare carts for the eligible pool"). |
| 6 | `test_hangar_max_carts_negative_rejected` | `Hangar(max_carts=-1)` ⇒ `ValueError` from `Hangar.__post_init__`. |

### 4.2 Loader — schema + coercion

| # | Test | Assertion |
|---|---|---|
| 7 | `test_load_hangar_absent_max_carts_defaults_one` | A `hangar.yaml` with no `max_carts` ⇒ loaded `Hangar.max_carts == 1`. Regression guard for backward-compat. |
| 8 | `test_load_hangar_max_carts_value` | `max_carts: 3` ⇒ `Hangar.max_carts == 3`. |
| 9 | `test_load_hangar_max_carts_non_int_errors` | `max_carts: "two"` / `max_carts: 1.5` ⇒ `LoaderError` (fractional/ non-int cart rejected). Confirms `_to_int` raises, not silently truncates. |
| 10 | `test_load_hangar_max_carts_negative_errors` | `max_carts: -1` ⇒ `LoaderError` (loader wraps the `Hangar.__post_init__` `ValueError`). |

### 4.3 Integration / determinism

| # | Test | Assertion |
|---|---|---|
| 11 | `test_solve_alternatives_independent_per_layout` | `solve(scenario, alternatives=2)` with `max_carts=1` and two `cart_eligible` planes ⇒ both returned alternatives are individually valid (per-layout binding; no cross-layout rejection). Confirms §2.3. |
| 12 | `test_max_carts_determinism_unchanged` | `max_carts` does not perturb the RNG stream: a fixed-seed `solve` with `max_carts=1` is byte-identical to a pre-change golden (the field only *loosens/tightens a validity gate*, it does not enter the search trajectory). |

### 4.4 Fixtures

- One new `tests/fixtures/hangar_two_carts.yaml` (`max_carts: 2`) for tests 2/3/8/11; or construct `Hangar` directly in-code where no YAML round-trip is needed (preferred for the models tests to avoid fixture proliferation, per the CLI-design precedent of reusing fixtures and minimising new ones).

---

## 5. Open questions for the user (decide at review)

1. **Fork A (source):** confirm `max_carts` on `data/hangar.yaml` (recommended A1) vs fleet config (A2) vs CLI flag (A3). *Recommendation: A1, default `1`.*
2. **Fork B (binding):** confirm **per-layout** binding for `SolveResult` alternatives (recommended) — i.e. the K alternatives are an either/or menu, each independently realisable. *Recommendation: per-layout; no per-sequence gate now.*
3. **Dolly vs tug (§2.4) — the load-bearing semantic question.** Does a carted plane *rest* on its cart in the final layout (dolly ⇒ per-layout *count* is the right invariant, #210 is just "make `1` configurable"), or is one cart towed back out and reused serially (tug ⇒ the count invariant is wrong and a larger re-model is needed)? This doc **assumes dolly**. If tug, this becomes a **new ADR** and a bigger change.
4. **`max_carts = 0` semantics:** confirm `0` means "no spare carts for the eligible pool" (so any `cart_eligible`-on-cart is rejected, but `always_cart` planes are unaffected). Test 5 encodes this; the alternative is to forbid `0` entirely at the schema level.
5. **CLI override (A3) as a follow-up?** Add `--max-carts N` later as non-breaking sugar over the data field, or never? Out of scope for #210 as written; flag if wanted as a separate issue.
6. **Governance:** amend ADR-0007 (recommended for the dolly/per-layout outcome) vs new ADR (only if tug). Confirm.

---

## 6. Summary of recommendations

| Decision | Recommendation | One-line rationale |
|---|---|---|
| **Source of `max_carts`** | `data/hangar.yaml`, default `1` | A cart is site equipment, same category as `clearance_m`; `Hangar` is already in scope at every enforcement point. |
| **Binding scope** | Per-layout (no per-sequence gate) | The K alternatives are a mutually-exclusive menu; binding across them is a false positive that shrinks choice for no physical reason. |
| **Where the limit lives** | On `Hangar`, read by `Layout.__post_init__` as `self.hangar.max_carts` | No new `Layout` field/constructor arg; every existing call site gets the limit free. |
| **`MovesPlan` tally** | Stays tally-free | Preserves the ADR-0007 non-breaking guarantee; per-sequence accounting (if ever) goes on a new additive diagnostic, never into `MovesPlan`. |
| **Governance** | Amend ADR-0007 (Amendments section) | Closes a question ADR-0007 explicitly parked; agrees with its framing — not a new cross-cutting decision. New ADR *only* if the user picks the tug reading. |

This is a design proposal. No code, no ADR, and no implementation issue has been created — the user approves the direction (the open questions in §5, especially the dolly-vs-tug call) before any of that follows.
