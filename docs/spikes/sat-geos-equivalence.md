# Spike: GEOS-vs-SAT oriented-rectangle boundary equivalence

- **Status:** Findings + verdict. This spike writes **no production code**; it
  proves/refutes whether a numpy SAT box oracle can replace GEOS on the box
  rungs. The lever graduates to its own implementation issue (#754) gated on
  this verdict.
- **Date:** 2026-06-22
- **Spike issue:** [#735](https://github.com/DocGerd/hangarfit/issues/735) (Lever B)
- **Part of:** epic [#760](https://github.com/DocGerd/hangarfit/issues/760)
  (Wave 3 — geometry levers), throughput epic
  [#607](https://github.com/DocGerd/hangarfit/issues/607).
- **Gates:** [#754](https://github.com/DocGerd/hangarfit/issues/754) — whether the
  vectorized numpy-SAT box collision oracle is GO (ship opt-in) or must be cut
  to Lever A (AABB-prefilter reuse) only.
- **Harness:** [`tests/spikes/test_sat_geos_equivalence.py`](../../tests/spikes/test_sat_geos_equivalence.py)
  (pytest + a standalone `python -m tests.spikes.test_sat_geos_equivalence`
  report driver).

---

## TL;DR — VERDICT: **conditional GO** (opt-in `--sat-collisions`, clearance > 0)

Float numpy SAT reproduces the GEOS oriented-rectangle collision surface **to
float-noise (~5e-15)** — small enough that it **never observably flips a verdict
on random or realistic geometry** (0 flips across the committed 20 000-pair random corpus; a 200 000-pair ad-hoc run, not committed, likewise showed 0).
It is therefore safe to ship Lever B **as an opt-in** `--sat-collisions` backend
for the box-curriculum rungs.

It is **NOT bit-for-bit identical** to GEOS, so it **cannot silently replace the
validity/determinism authority**:

1. **`clearance == 0` (`intersects and not touches`) — NO-GO for bit-identity,
   but irrelevant to the box rungs.** Float SAT and GEOS's topological
   `touches` DE-9IM predicate disagree on **293 / 20 000** pairs, *all* confined
   to the **measure-zero exact-touch boundary** (max separation among the
   mismatches: `1.1e-16 m`). The box rungs run at clearance `0.05 m` and never
   take this branch.
2. **`clearance == 0.05` (`distance < clearance`) — the live box-rung branch —
   is GO on real geometry, with one documented caveat.** **0** verdict flips on
   the committed 20 000-pair random corpus (a 200 000-pair ad-hoc run, not committed, likewise showed 0). A flip is only producible by a
   **surgically constructed** pair whose *true* separation lands within ~5e-16 m
   of exactly `0.05 m` (3 633 flips / 105 000 such adversarial pairs). The flip
   band is ~1 part in 1e14 of the clearance — random/real geometry never hits
   it, and at that separation GEOS's own verdict is itself float-arbitrary.

Therefore: **GO to build Lever B as an opt-in box-rung accelerator**, with CPU
shapely kept as the **single determinism + validity authority** (the #694
ml-rl-guard contract: *validity = the product checker, not the env oracle*).
SAT is an accelerator inside the *training rollout*, re-checked by shapely
wherever a bit-exact verdict is contractually required.

---

## What SAT had to reproduce

The GEOS surface the box oracle replaces lives in
[`src/hangarfit/geometry.py`](../../src/hangarfit/geometry.py):

| GEOS call | Semantics | Used by box rungs? |
|---|---|---|
| `polygon_overlap(p1, p2, clearance=0.05)` | `p1.distance(p2) < clearance` | **Yes — the live branch** |
| `polygon_overlap(p1, p2, clearance=0.0)` | `intersects and not touches` | No (clearance is 0.05) |
| `polygon_overlap_area(p1, p2)` | `intersection().area` (graded leak penalty) | Yes |

Pure SAT yields penetration *depth* for overlapping boxes — not the
min-separation *distance* GEOS compares against the clearance, nor the clipped
intersection *area* GEOS sums. So the harness implements three numpy kernels:

1. **`sat_interiors_overlap`** — the four edge-normal Separating-Axis test, with
   a strict `overlap > 0` (so a touching configuration is *not* an interior
   overlap, matching `intersects and not touches`).
2. **`convex_min_separation`** — exact closest-feature distance between two
   convex polygons (min over all vertex→edge distances, both ways). For convex
   inputs this equals the GJK closest distance; it returns `0.0` for
   overlapping or touching inputs, matching `shapely.Polygon.distance`.
3. **`sat_clip_area`** — Sutherland–Hodgman convex clip + shoelace area, matching
   `intersection().area`.

### The phantom-mismatch guard (ADR-0002)

The production world corners come from
[`oriented_rect`](../../src/hangarfit/geometry.py) (CCW local corners) routed
through the **determinant-−1** [`local_to_world`](../../src/hangarfit/geometry.py)
transform ([ADR-0002](../adr/0002-determinant-minus-one-transform.md)). To
isolate this spike to *"does SAT-on-corners equal GEOS-on-the-same-corners"* —
rather than confounding it with a re-implementation of the det-−1 transform —
the harness builds the corner floats **once** via the production helpers and
feeds the **identical** `(4, 2)` array to both the shapely `Polygon` and the
numpy kernels. The transform is upstream of both, so it cannot manufacture a
sign-flip divergence here. (A production Lever B kernel would still have to
reproduce the det-−1 corner construction in vectorized numpy; that is a separate
equivalence obligation for #754, not in scope for this boundary spike.)

---

## Corpus

Fixed seed (`np.random.default_rng(735_0760)`), **deterministic** so the verdict
is auditable. The bulk of randomly-placed pairs are trivially separated or
overlapping (uninteresting), so the corpus is **heavily weighted to the
correctness-risk shell**:

| Category | Share | Purpose |
|---|---|---|
| Boundary shell | 45 % | B pushed away from A at signed gaps straddling the clearance, random world direction, mixed headings (0/45/90/135/30/60/17.3/200/271/359°) |
| Tight near-contact | 30 % | small random nudges so a real slice lands in/just-outside the clearance band and the touch set |
| Exact edge-sharing | 10 % | identical boxes translated by exactly `2·half_width` → shared edge, distance 0, no interior overlap |
| Broad random fill | 15 % | validates no false alarms on easy pairs |

Census on the standalone 20 000-pair run: **7 327** interior-overlapping, **1 323**
boundary-touching, **11 350** disjoint, **117** inside the clearance band
`(0, 0.05)`. A `test_corpus_actually_exercises_the_boundary` guard asserts the
corpus hits both branches and a non-trivial in-band slice, so the equivalence
assertions are not vacuous.

A separate **surgical** probe (`_surgical_boundary_flip_census`,
105 000 pairs) does *not* rely on the random corpus landing on the boundary: it
places identical boxes a controlled exact gap apart along the plane-local `+u`
world direction and sweeps the gap through `nextafter`-scale neighbours of
`0.05 m` — the only way to actually exercise the ULP-flip case.

---

## Results

20 000-pair standalone run (numbers reproduce on
`python -m tests.spikes.test_sat_geos_equivalence`):

| Metric | Result |
|---|---|
| (a) overlap-verdict mismatch, **clearance 0** | **293 / 20 000** — all at separation ≤ `1.1e-16 m` (exact-touch boundary) |
| (a) overlap-verdict mismatch, **clearance 0.05** | **0** |
| (b) max abs distance delta | `3.55e-15 m` |
| (b) max distance ULP delta (separated pairs, d > 1e-9) | `2241` (≈ `5e-19` relative) |
| (b) **clearance-0.05 boundary flips (random corpus)** | **0** on the committed 20 000-pair corpus (a 200 000-pair ad-hoc run also showed 0) |
| (c) overlapping pairs measured | 7 279 |
| (c) max abs area delta | `5.33e-15 m²` |
| (c) max relative area delta | `3.14e-10` |
| **surgical** ULP-targeted clearance-0.05 flips | **3 633 / 105 000** — driven by a `5.0e-16 m` delta |

### (a) `clearance == 0` — `intersects and not touches`

293 mismatches, **every one at separation ≤ `1.1e-16 m`** (289 at exactly
`0.0`). The disagreement is intrinsic: GEOS's `touches` is a topological DE-9IM
predicate evaluated with GEOS's own snapping/precision model, whereas SAT's
verdict is raw float interval arithmetic. The two cannot agree bit-for-bit on
the **measure-zero exact-touch set** — 193 cases SAT calls a hair of overlap
where GEOS calls touch, 100 the reverse. This is a genuine **NO-GO for
bit-identity on the `clearance == 0` branch**, but **the box rungs run at
`0.05 m` and never take this branch**, so it does not gate Lever B.

### (b) min-separation distance — `distance < clearance`

The live box-rung branch. Distance agrees to **float noise** (`≤ 3.55e-15 m`
absolute; ≤ 2241 ULP on genuinely separated pairs). On the random corpus —
even on a 200 000-pair ad-hoc run (the committed corpus is 20 000) — there are **zero** `< 0.05` verdict flips.

The honest caveat: the surgical probe **proves a flip is possible**. When the
true separation is constructed to within a ULP of exactly `0.05 m`, GEOS rounds
to `0.050000000000000044` (one ULP *above*) while SAT lands at
`0.04999999999999993` (one ULP *below*) — a `1.1e-16 m` delta straddling the
literal threshold. The flip band is **~5e-16 m wide**, i.e. a true separation
must coincide with the clearance to **~1 part in 1e14**. Random and realistic
geometry never produces that coincidence (hence 0 random flips); and at that
separation GEOS's *own* in/out verdict is float-arbitrary.

### (c) intersection area — `intersection().area`

Sutherland–Hodgman clip + shoelace matches GEOS to **`≤ 5.33e-15 m²`** absolute
(`≤ 3.14e-10` relative). Far below any observable graded-penalty difference —
the penalty is a soft reward term, not a hard gate, so this noise is invisible
to the policy.

---

## Verdict and what Lever B can safely use

**Conditional GO.** Build #754 Lever B as the **opt-in** `--sat-collisions` box
oracle:

- **GO to use** the SAT/GJK **distance + `< clearance` test** as the per-pair
  collision gate on the box rungs — it matches GEOS to float noise and never
  flips on real geometry.
- **GO to use** the Sutherland–Hodgman **clip area** for the graded penalty.
- **GO to use** the SAT **interior-overlap** boolean for any *clearance-0*
  hard-overlap fast path — but only on the understanding that the exact-touch
  set is float-ambiguous in *both* engines.

**Conditions (the reason it is *conditional* GO, not unconditional):**

1. **Lever B is opt-in, not a silent swap.** CPU shapely remains the **single
   determinism + validity authority** (the #694 ml-rl-guard contract: *validity
   = the product checker, `collisions.check` + Caddy egress, not the env
   oracle*). SAT accelerates the *rollout's* collision queries; any verdict that
   must be bit-exact (final validity, the determinism-guard double-run) is
   decided by shapely.
2. **Float SAT is not a drop-in for the verdict near the clearance literal.**
   Because a ULP-coincidence with `0.05 m` can flip the `< clearance` test,
   SAT and shapely are *equivalent within float noise* but not *bit-identical*.
   This is acceptable for an accelerator re-gated by shapely; it would be
   unacceptable for replacing the authority.
3. **The det-−1 corner construction must be re-validated in vectorized numpy at
   #754.** This spike fed the *production* corners to both engines to isolate
   the kernel comparison. A production Lever B builds corners in numpy and owes
   its own equivalence check against
   [`local_to_world`](../../src/hangarfit/geometry.py) (ADR-0002) — the
   `geometry-invariant-guard` review applies.

**If the conditions cannot be met** (e.g. a future requirement that the box
oracle *be* the bit-exact authority): cut #754 to **Lever A only** (reuse the
existing `_aabbs_separated_beyond_clearance` AABB prefilter, which is a provable
lower bound and therefore byte-identical, per
[`collisions.py`](../../src/hangarfit/collisions.py)). Lever A carries none of
the boundary-flip risk because it never *decides* a pair — it only conservatively
skips the ones GEOS would also clear.

---

## Reproduce

```bash
# Pytest assertions (pins the proven equivalence + documents the divergences):
PYTHONPATH=$PWD/src python -m pytest tests/spikes/test_sat_geos_equivalence.py -v

# Standalone report (corpus census + the surgical ULP-boundary probe + verdict):
PYTHONPATH=$PWD/src python -m tests.spikes.test_sat_geos_equivalence
```
