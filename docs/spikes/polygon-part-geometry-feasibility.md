# Spike: higher-fidelity polygon part geometry from official aircraft docs — feasibility & value

- **Status:** Findings + recommendation. Time-boxed feasibility assessment; no
  production code or fleet-data change lands here beyond throwaway measurement
  probes. A GO phase spawns its own implementation epic + an ADR refining
  ADR-0001's mesh deferral.
- **Date:** 2026-06-09
- **Spike issue:** [#541](https://github.com/DocGerd/hangarfit/issues/541)
- **Recommendation:** **PARTIAL** (phased — see the end).
- **Context:** *"A fuselage is not a box in real."* Today every aircraft is a list
  of oriented-**rectangle** `Part`s (ADR-0001). The two fidelity steps since —
  ADR-0012 (fuselage front/aft split) and ADR-0023 (empennage surfaces) — both
  raised realism by **adding rectangles**, never by making one part
  non-rectangular. This spike asks whether the next step should be **polygon
  parts** (an N-gon footprint), vertices traced/derived from manufacturer 3-views,
  EASA/FAA TCDS, and spec sheets.

---

## TL;DR

1. **The central claim is confirmed (with caveats): the safety-critical
   collision/transform core is polygon-generic — polygon parts drop in with zero
   behavioural change.** Every collision predicate is `PartKind`-keyed + pure
   Shapely; the det(−1) transform is genuinely per-vertex; the #454 AABB
   broad-phase and the #453 pose-cache key are both vertex-count-agnostic. The one
   rectangle-coupled symbol in the guarded modules is `geometry.oriented_rect`, and
   it stays as the build path for any part still declared by scalars (mixed
   fleets).
2. **The value is real but narrow — hence PARTIAL, not GO.** A measured
   rectangle-vs-tapered-polygon experiment on the real Herrenteich layout shows a
   tapered **glider** wingtip nests where its bounding rectangle falsely conflicts —
   a robust **0.10–0.30 m flip window** (measured on the Scheibe wing over the
   Stemme empennage). But the value is **taper-specific**: ~6 of the 8 fleet
   aircraft are published **constant-chord** light types where a taper polygon ≈
   the rectangle (a clean negative control confirmed no flip there).
3. **The cost is concentrated, not in the checker.** It sits in **data authoring**,
   a **load-time vertex-canonicalization determinism invariant**, and the
   **`scene/v1`→`scene/v2` viewer seam** (the largest single piece). The collision
   core is ~S; 2D rendering is ~zero (already polygon-based).
4. **Official sources barely support polygon *outlines*.** TCDS publish **scalars**
   (span/length/area), not outlines; **0/8** fleet aircraft expose a dimensioned
   3-view *in the TCDS*, only **1/8** (Cessna 140) has a separate dimensioned
   3-view. Honest authoring is **parametrized** (root/tip chord + taper expanded by
   the loader), not surveyed — and only the 2 gliders genuinely differ from a
   rectangle.

**Recommendation: PARTIAL.** Approve a phased build of the low-blast-radius,
opt-in **(c) + (d-param)** combination — an optional polygon-vertex `Part` field
that keeps every scalar fleet byte-identical, authored as parametrized tapers for
the **gliders only**. Defer the fuselage-outline (b) and 3-view tracing (d-trace).

---

## Q1 — Pipeline rectangularity audit (per-module)

Four read-only auditors classified every `Part`/`WorldPart` consumer; a cross-check
verifier found **no contradictions** and refined where the work sits.

| Module | polygon-ready / coupled | Verdict | Effort | The coupling |
|---|---|---|---|---|
| `collisions.py` + `geometry.py` (the guarded core) | 14 / 1 | **SUPPORTS strongly** | **S** | only `oriented_rect` (the local-frame rectangle builder); `check`, `_parts_conflict`, `polygon_overlap[_area]`, `floor.covers`, the #454 AABB reject, the det(−1) per-vertex transform, and the geometry-free pose-cache key are all N-gon-general |
| `scene.py` + `viewer/` + `scene/v1` schema | 4 / 5 | **PARTIAL (largest piece)** | **M–L** | `_plane_blocks` emits scalar `length/width/angle`; `planes.ts` `BoxGeometry`; `anchors.ts` 4-corner oracle; `labels.ts` reads `length_m` (a consumer the issue trace missed); `scene-contract.ts` `BoxData` |
| `loader.py` + solver area-gate + `metrics.py` + `models.Part` | 5 / 3 / 1 | **SUPPORTS (qualified)** | **M** | `models.Part` needs an optional vertex field + canonicalization; `_build_part` a vertex branch; `_split_fuselage` is the one genuinely-new algorithm (box-interval → Shapely clip); struts stay rectangles; `_check_sum_areas` gets *tighter* (polygon area ≤ bbox → still a sound lower bound) |
| `visualize.py` + `towplanner.py` | 11 / 2 | **SUPPORTS** | **S** | 2D already draws `part.polygon.exterior.coords` → ~zero; only `towplanner._plane_fore_aft_length_m` (scalar `offset+length/2`, advisory — feeds `derive_apron_depth` only) needs the polygon x-extent |

**Net:** the checker + transform are free; rectangularity concentrates in **data
authoring**, the **`scene/v2` viewer seam**, and a few **scalar loader helpers** —
exactly the issue's trace, now audited.

---

## Q8 — Does it change a PARKING DECISION? (the value question, measured)

The decisive experiment. On the real `examples/herrenteich/layout.yaml` (a valid,
tightly-nested all-8 arrangement) the probe replaced glider **wing** rectangles
with realistic symmetric **tapered trapezoids** (a strict *subset* of the bbox,
built through the canonical det(−1) `local_to_world`), then crowded a glider toward
a neighbour and compared the rectangle verdict vs the polygon verdict at each step.

**Result — a robust flip exists, glider-specific:**

```
crowd scheibe_falke toward the Stemme empennage (realistic taper 0.45):
  d=0.06m  rect=VALID    taper=VALID
  d=0.10m  rect=INVALID  taper=VALID   <<< FLIP  (removed: tail_wing_overlap,
  d=0.30m  rect=INVALID  taper=VALID   <<< FLIP   vertical_stabilizer_wing_overlap)
  d=0.36m  rect=INVALID  taper=INVALID        (both foul once crowded past the taper)
  >>> FLIP WINDOW: 0.10–0.30 m of crowding (~0.22 m wide)

NEGATIVE CONTROL — crowd a constant-chord light a/c (taper 1.0 = rectangle):
  rect == taper at EVERY step  →  NO FLIP   (proves the value is taper-specific)
```

The tapered wingtip clears the Stemme tail + fin where the **rectangle over-claims
footprint at the tip and falsely rejects a physically-valid nest** — genuine
*validity* value (eliminating false NO-conflicts), not cosmetics, exactly where the
tool nests tightest. `metrics.min_wing_over_tail_clearance_m` sharpens for free.

**But the value is concentrated and thin.** The flip was measured on the **Scheibe**
wing (one of the two realistically-tapered gliders — Scheibe SF-25E AR 17.8, Stemme
S10 AR 14.0), in one crowding direction, across a ~0.22 m band; the Stemme's tested
crowding directions did not flip in this layout (they share the same
taper-vs-rectangle footprint gap, but no neighbour sat in the flip band). The
negative control and the data research (below) confirm ~6 of 8 fleet aircraft are
constant-chord, where polygon fidelity buys essentially nothing. → A real but
narrow, taper-specific win — the empirical basis for **PARTIAL**.

---

## Q2 — Data-source usability (official docs as polygon input)

Web research across the 8 Herrenteich aircraft:

| Aircraft | TCDS | dimensioned 3-view? | outline obtainable as |
|---|---|---|---|
| Scheibe SF-25E | EASA.A.098 | none | scalars only (span/area/length) → can't even parametrize a taper |
| Stemme S10 | EASA.A.054 | none | scalars only (span/area/length) |
| Aviat Husky A-1C | FAA + EASA | undimensioned | parametrizable (span/length/area/cabin width) |
| ULBI Wild Thing WT-02 | none (UL) | none | weak (span/length/area/chord from Wikipedia/ultraligero) |
| Zlin Savage Cub | none (UL) | undimensioned | parametrizable; **constant chord 1.56 m** (no taper) |
| Cessna 140 | FAA A-768 | **dimensioned** (drs.faa.gov) | the one true traceable outline; constant chord (NACA 2412) |
| Flight Design CTSL | EASA.A.537 | none | weak (LSA pattern omits airframe dims) |
| B&F Technik FK9 Mk II | none (Mk II) | none | not traceable |

**Q2 verdict:** TCDS publish **scalars, not outlines** — **0/8** give a dimensioned
outline *in the TCDS*; only **1/8** (Cessna 140) has a separate dimensioned 3-view,
and it is constant-chord. So fleet-wide polygon authoring is necessarily
**parametrized** (root/tip chord + taper → loader-expanded vertices), honest about
being derived not surveyed, and **only the 2 gliders' parametrized planform
materially differs from a rectangle**. This independently corroborates PARTIAL.

---

## Q4 — Performance & Q5 — Determinism

**Q4 (cheap, absorbed):** the collision hot loop is shapely `distance`/`intersection`,
whose cost grows weakly for small N-gons (6–12 verts). The #540 census measured the
#454 AABB broad-phase rejecting **97.6–99.2 %** of part-pairs (`.bounds` is sound
for any polygon, so the reject stays valid), and the #453 pose-cache memoizes the
build — so the marginal cost of a tapered N-gon over a 4-gon is small and absorbed.
Bind any real measurement on `max_restarts`.

**Q5 (the determinism crux):** today the world-ring vertex order is deterministic
*only because* `oriented_rect` hands a fixed CCW literal and `aircraft_parts_world`
maps `coords[:-1]` in sequence — Shapely's `Polygon()` preserves the given order
verbatim (it does **not** normalize winding). The moment a `Part` carries
author-supplied vertices, winding / start-vertex / closure become author-controlled,
and because every `coords[:-1]` consumer reports the *first* violator and the JS
oracle compares per-corner within 1e-6, two equivalent orderings of the same shape
would produce different bytes (ADR-0003 break). **Mandatory mitigation:**
`Part.__post_init__` canonicalizes at **load** (force CCW by signed-area sign,
rotate to lexicographically-min start, drop the closing dup, reject
non-simple/invalid/non-finite rings); the geometry layer must **never** re-orient at
solve time. The pose-cache key is geometry-free (stays valid for any N); the det(−1)
affine is untouched (vertices ride the same matrix — `geometry-invariant-guard` must
confirm the build path routes *every* declared vertex through `local_to_world`, no
centroid shortcut).

---

## Approaches — ranked by effort vs realized fidelity (factoring Q8)

| Approach | Fidelity gain | Effort | Verdict |
|---|---|---|---|
| **(c)** opt-in N-gon parts where data exists (mixed scalar/polygon fleet) | tunable | **Low–Med** | **RECOMMENDED** — rectangle parts stay byte-identical; lowest blast radius; best determinism story |
| **(d-param)** parametrized planform (root/tip chord + taper) the loader expands | medium (gliders) | **Med** | **RECOMMENDED with (c)** — machine-usable from what TCDS actually publish; YAML-ergonomic like `struts:` |
| (a) per-part convex polygon outline | med–high | Med | viable; drops into the checker; convex keeps Shapely cheap |
| (b) one fuselage outline replacing front/aft boxes | high (the "fuselage isn't a box" ask) | High | **DEFER** — relocates ADR-0012's cockpit/tail split into `loader._split_fuselage` as a Shapely clip (the one new algorithm); the `PartKind` taxonomy still carries collision semantics so the predicate is unaffected |
| (d-trace) trace outlines from 3-view drawings | highest | High + manual | **DEFER/NO** — only 1/8 has a dimensioned 3-view; hand-tracing is an accuracy/provenance liability |

---

## Recommendation: PARTIAL — a phased, opt-in build

The central claim holds (safety core is free, work is additive) and the value is
**real but glider-concentrated**, so:

1. **Phase 1 (the validity win, low risk):** add an optional `local_vertices` field
   to `Part` + the **load-time canonicalization invariant** + the polygon build-path
   branch in `aircraft_parts_world` (alongside `oriented_rect`). Scalar fleets stay
   **byte-identical**; author the **2 gliders** as parametrized tapers (d-param).
   Guarded by `geometry-invariant-guard` + `determinism-guard`.
2. **Phase 2 (cosmetic-but-large):** `scene/v2` (vertex list + extrude height) +
   `viewer.js` `BoxGeometry`→`ExtrudeGeometry` (de-risked by the in-tree
   `ShapeGeometry` L-floor precedent, #530) + `anchors.ts`/`labels.ts` generalized to
   N vertices, all kept bit-identical to the Python `_anchors` oracle + the committed
   `viewer.js` rebuild (viewer-build-drift guard).
3. **Fold in** the pre-existing bay-test thin-edge blind spot
   (`_first_vertex_in_bay` is still per-vertex where `floor.covers` was hardened per
   ADR-0018) as a tracked follow-up — polygon parts make it marginally more
   reachable.

**Provenance honesty:** parametrized polygons are no more "measured" than today's
scalars — the `measured: false` flag and the viewer "PLACEHOLDER DATA" banner stay
truthful.

### Filed follow-up issues (proposed — not yet filed, pending review)

- **ADR + Phase-1 epic:** refine ADR-0001's mesh deferral → optional polygon parts
  (c + d-param); `Part.local_vertices` + load-time canonicalization;
  `aircraft_parts_world` build-path branch; glider taper data. *(Validity win,
  byte-identical for scalar fleets.)*
- **`scene/v2` viewer seam:** schema delta + `viewer.js` ExtrudeGeometry rebuild +
  oracle/guard updates. *(Phase 2.)*
- **Fuselage-outline (approach b):** `_split_fuselage` box-interval → Shapely clip,
  if/when pursued. *(Re-opens ADR-0012 D2 at the loader, not the predicate.)*
- **Bay-test hardening:** `_first_vertex_in_bay` → polygon-vs-bay intersection
  (mirror the ADR-0018 `floor.covers` fix). *(Pre-existing; tracked.)*

---

## Out of scope

Implementation (each phase is its own issue), full 3D convex meshes (ADR-0001's
deferral stands for the *3D* case), and ML approaches.
