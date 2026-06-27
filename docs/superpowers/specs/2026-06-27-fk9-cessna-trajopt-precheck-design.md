# Design spec — #844b continuous-trajectory-optimization **determinism-first pre-check** (Gate 0)

- **Status:** PROPOSED (brainstorm complete; awaiting spec review → writing-plans).
- **Frontier:** `#844` Herrenteich all-8 tow-routing — the `fk9_mkii ↔ cessna_140` front-door "parallel-park" nook.
- **Relationship to siblings:** This is the *cheap front gate* for follow-up **(b)**, the parked
  continuous-trajectory-optimization spike. It is **not** the build. It does **not** touch the separable
  husky-ordering follow-up **(a)**. It explicitly does **not** re-tread the refuted heuristic class (`#840`).
- **One-line intent:** Decide — as cheaply as possible, *before building any optimizer* — whether continuous
  trajectory optimization could **ever ship** under the ADR-0003 determinism contract. A clean NO-GO here
  retires the XL build; a PASS unlocks a *separate, later* convergence gate (Gate 1).

---

## 1. Motivation — the measured problem (recap)

The `fk9_mkii ↔ cessna_140` corridor is a **documented manual-insertion case**: the auto-router cannot tow-route
the front-door pair, the club hand-shuffles it on own gear daily, and that inability is *expected, not a bug*
(`docs/spikes/herrenteich-fk9-cessna-lateral-shuffle.md:282-296`).

The method-class search around this nook is nearly exhausted:

- **Heuristic / search-guidance class — REFUTED (`#840`).** An exact heading-aware backward-SE(2) cost-to-go
  field found the path in **108,991** expansions vs the deployed position-only heuristic's **96,949**
  (0.89×, ~12 % *worse*). Root cause is an **intrinsic near-C\* A\* plateau**: completeness forces expanding
  every state with `f* ≤ C*`, so *no* heuristic-class method — deterministic field **or** learned guidance —
  shrinks it (`lateral-shuffle.md:246-280`).
- **Analytic parallel-park macro — REFUTED.** Never routed the pair at the deployed 0.5 m/15° grid at any
  budget (`lateral-shuffle.md:148-195`).
- **Learned-M1 (learned cost-to-go in the A\* loop) — killed transitively.** It can only approximate the exact
  field that already lost, and ONNX-in-loop float ties break ADR-0003 byte-identity anyway
  (`lateral-shuffle.md:271-280`).

That leaves **continuous trajectory optimization** (direct collocation / SCP / gradient-NLP) as the *only
surviving method class* — the A\*-plateau argument is search-specific and does **not** bind a non-search
gradient/NLP descent. But it is scoped as "a far larger bet, deferred."

**The dominant unknown that makes it a large bet is not convergence — it is determinism.** ADR-0003 requires
byte-identical plans; an iterative optimizer's raw float output is the same hazard that killed learned-M1. If
the determinism wall is fatal, the method collapses to **find-then-cache**, which is *dominated by the
already-rejected witness-cache* (`lateral-shuffle.md:284`, `2026-06-26-heading-aware-cost-to-go-design.md:153`).
So determinism is the cheapest thing to falsify first.

### 1.1 What the feasibility witness already proves

This is **not** the retracted `#832` infeasible-population trap. The corridor has a **gold-standard MOTION
witness**: own-gear Hybrid-A\* at 0.25 m/10° found a real no-carts `fk9 → cessna` path —
**96,949 expansions, ~39 min, exact-oracle-validated by `path_first_conflict`** — while the deployed
0.5 m/15° grid finds none (`lateral-shuffle.md:29,77-84,105-108`). So a collision-free own-gear trajectory
**provably exists**; the open problem is search *efficiency*, not feasibility. The static anchor is
`examples/herrenteich/layout.yaml`, a valid all-8 that passes `collisions.check`.

### 1.2 The reframe that sets this gate's shape (from the code)

`Move`/`MovesPlan`/`Segment` (`towplanner.py:236,262,87`) already store **continuous floats**
(`length_m`, `turn_radius_m`, `heading_deg`). The emitted plan is **not** a coarse quantized grid — the
0.5 m/15° grid is only the A\* *search* discretization; the plan carries continuous **closed-form**
Reeds–Shepp parameters (`_lsl`/`_rsr`/… solvers), and the module already reasons about ULP-level tie
determinism (`towplanner.py:389`).

Therefore the determinism wall is **not** "quantize continuous output to a grid." It is sharper:

> **Closed-form Reeds–Shepp** = a fixed, short arithmetic sequence → reproducible (it *is* today's byte-identity
> baseline). **An iterative optimizer** = a convergence loop whose result depends on termination tolerances and
> (BLAS-threaded) linear algebra → not obviously reproducible.

The escape hatch — the thing Gate 0 must test — is whether the optimizer's *contribution* **reduces to a
discrete maneuver structure** (a Reeds–Shepp *word* / cusp sequence) that the existing deterministic closed-form
machinery then realizes byte-identically. **Optimizer picks the *shape*; the deterministic solver fills the
*numbers*.** If yes → there is a shippable path. If the value lives only in continuous parameters the
deterministic pipeline cannot reproduce → the wall stands.

---

## 2. The question this spec answers

> **Is there any concrete, plausibly bit-stable path by which a continuous-trajectory-optimization solution for
> the fk9↔cessna nook becomes a deterministic `MovesPlan` that (a) satisfies the ADR-0003 contract and
> (b) is something the existing A\* search could not already reach — without collapsing to the rejected
> witness-cache?**

If NO → continuous-traj-opt is unshippable or dominated → STOP (the nook stays a manual-insertion case).
If plausibly YES → proceed to Gate 1 (convergence), a *separate* decision.

---

## 3. Why determinism-first (sequencing rationale)

Two sub-gates exist: **convergence** (does an optimizer find a clean trajectory in seconds?) and
**determinism** (can its output ship byte-identically?). We run determinism **first** because:

1. **It is the cheaper falsifier.** Gate 0 is mostly reading ADR-0003 + the RS/`MovesPlan` code plus one tiny
   reuse experiment — ~1 h, no optimizer, no new dependency. Convergence-first would sink the optimizer-build
   effort *before* learning whether the result can ever ship.
2. **It is the dominant risk.** The spike docs name determinism, not convergence, as the wall that collapses the
   method to the rejected witness-cache.
3. **It can be decisive on paper.** If the contract demands cross-machine bit-identity and no discrete-word
   reduction exists, the answer is NO-GO with no code written.

Rejected sequencings: **convergence-first** (sinks build cost before the dominant risk is tested) and
**both-together** (no early-exit savings if determinism is hopeless).

---

## 4. The gated plan

### Gate 0 — determinism-shippability (this spec; ~1 h; NO optimizer)

Three steps, in order; **any one** can produce the NO-GO.

**Step 0.1 — Pin the exact determinism requirement (the linchpin).**
Read ADR-0003 and the `determinism-guard` contract and settle precisely: does byte-identity mean
**same-machine** (the double-solve canary re-runs on one machine and diffs) or **cross-machine**? This single
fact swings the verdict:
- *Same-machine* → a fixed-seed, fixed-BLAS-thread, fixed-tolerance optimizer is *plausibly* reproducible, so
  the wall may be passable even without a discrete-word reduction.
- *Cross-machine* → an iterative solver's raw float output is almost certainly dead, and only the
  reduce-to-discrete-word escape (Step 0.3) can survive.

**Step 0.2 — Characterize today's deterministic seam.**
Confirm the closed-form RS solvers + `path_first_conflict` (`towplanner.py:1306`) are the byte-identity
baseline, and identify the exact seam where an optimizer's output would have to enter to be realized as a
deterministic `MovesPlan`. Output: a precise statement of "what is deterministic today and where new
non-determinism would be introduced."

**Step 0.3 — The reduction test (tiny experiment; reuses the existing witness; still no optimizer).**
Use the **already-found 96,949-expansion own-gear witness trajectory** as a stand-in for "a continuous
solution," and test the load-bearing question:
- Can that trajectory be **reduced to a discrete Reeds–Shepp word / cusp sequence** that the deterministic
  closed-form machinery realizes — and is the realization **byte-stable** across repeated construction?
- Is that realization something **A\* could already reach** by searching longer/finer (in which case traj-opt
  adds nothing over search), or genuinely beyond search?
- Does any shippable form require carrying the optimizer's raw continuous params (→ collapses to find-then-cache
  → the rejected witness-cache)?

### Gate 1 — convergence (DEFERRED; separate sign-off; only if Gate 0 PASSES)

Build a *minimal* optimizer on the real isolated pair, measure a `path_first_conflict`-clean trajectory found
in **seconds** (vs the 39-min A\*), test cold-start vs witness-warm-start sensitivity, and decide the
dependency/formulation (scipy-only vs CasADi/IPOPT — note **scipy is not currently a dependency**; numpy is
transitively available). **Out of scope for this spec.**

### Gate 2+ — full build (DEFERRED). Flag-gated default-OFF planner mode, its own determinism canary, ADR,
guards (`determinism-guard`, `geometry-invariant-guard`), and the all-8 end-to-end check (which *also* needs
follow-up (a) husky-ordering resolved). Out of scope.

---

## 5. Success & kill criteria (falsifiable)

**NO-GO (unshippable)** — STOP, document:
- Step 0.1 establishes the contract requires **cross-machine** bit-identity, **and** Step 0.3 shows no
  discrete-word reduction (a shippable plan would have to carry the optimizer's raw float output).

**NO-GO (dominated)** — STOP, document:
- The only deterministic realization of the witness is one **A\* already reaches** by searching (traj-opt adds
  nothing over the existing search), **or**
- Every shippable form collapses to **find-then-cache** — i.e. it reduces to the already-rejected witness-cache
  for the fixed Herrenteich pair, with no generalization argument.

**PASS → Gate 1** — only if Step 0.3 exhibits a **concrete, plausibly bit-stable** realization path: a
discrete-word reduction the closed-form machinery realizes byte-identically, *or* a same-machine contract
(Step 0.1) plus a reproducible-by-construction optimizer output. PASS records *exactly* which path survived and
what Gate 1 must then prove.

The verdict must be **one of the three above**, stated with the measured evidence — not a hedge.

---

## 6. Determinism & shippability constraints (standing requirements)

- ADR-0003 byte-identity is the bar; the `MovesPlan` is the artifact that must be identical.
- The existing planner determinism canaries (`tests/test_solver_canaries.py`, the `determinism-guard`
  double-solve) must remain **untouched** — Gate 0 introduces no production code path, so they are not at risk.
- Any *future* (Gate 2) shippable optimizer mode must be **flag-gated default-OFF** with its own canary, exactly
  as the `#840` `heuristic_fn` seam is inert by default (`towplanner.py:2429,2546-2547`, no production caller).

---

## 7. Scope & footprint discipline

- **Deliverable:** a **spike doc** (`docs/spikes/herrenteich-fk9-cessna-trajopt-determinism-precheck.md` or a
  new `## Gate 0` section appended to the existing lateral-shuffle spike), recording the three-step verdict with
  evidence. This mirrors how `#840`/`#842` produced their verdicts.
- **No optimizer. No `scipy`/CasADi. No new dependency. No `src/` change.** Step 0.3's reuse experiment is a
  throwaway local script (numpy is available transitively); it is committed only if it earns being a reusable
  harness (it likely does not).
- `bench/` runs in the required `bench correctness` CI check, so Gate 0 deliberately stays **out of** `bench/`
  unless a committed harness is justified — the default is analysis + spike doc.

---

## 8. The separable husky-ordering blocker (success-incompleteness guard)

Even a full Gate-2 success on this nook does **not** auto-route the all-8 on its own: the husky front-cluster
entry-ordering blocker (follow-up **a**) is separable and unresolved (`lateral-shuffle.md:110-119`). Gate 0's
verdict is about the *nook method's shippability*, not the all-8 headline. The all-8 remains a manual-insertion
arrangement until *both* follow-ups land.

---

## 9. Decisions captured (from this brainstorm)

1. Pursue follow-up **(b)** via a **pre-check**, not a direct build (user, 2026-06-27).
2. Sequence **determinism-first** (Gate 0 before any convergence test) (user, 2026-06-27).
3. Step 0.3 reduction test **reuses the existing 96,949-exp witness** as the stand-in continuous solution
   (no optimizer; cheapest credible test).
4. Deliverable is a **spike-doc verdict**; no dependency and no `src/` change. The Step 0.3 reuse experiment
   is a throwaway local script **by default** (committed only if it demonstrably earns being a reusable
   harness — per §7, it likely does not).
5. The fk9↔cessna corridor remains a **documented manual-insertion case** unless a later gate overturns it;
   caching the witness is rejected (brittle/unfaithful) and is the dominance floor a NO-GO falls back to.

---

## 10. Open questions for the implementation plan

- Does the determinism-guard / ADR-0003 wording commit to **cross-machine** byte-identity, or only same-machine
  reproducibility? (Step 0.1 resolves this; it is the linchpin and may not be fully explicit in the ADR.)
- What is the exact, faithful **discrete representation** of the witness trajectory (how many cusps / RS
  segments), and is constructing it from the witness deterministic? (Step 0.3.)
- Where exactly does the witness path live / how is it reproduced for the reuse experiment — re-run the
  0.25 m/10° own-gear search (~39 min) once and cache the resulting trajectory locally, or is a sampled pose
  sequence already captured anywhere from the `#840` work?
