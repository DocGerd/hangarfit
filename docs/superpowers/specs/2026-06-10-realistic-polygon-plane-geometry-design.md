# Realistic polygon plane geometry + viewer (tilt-ready) — design

- **Date:** 2026-06-10
- **Status:** Design — approved in brainstorming, pending written-spec review.
- **Issues:** #548 (polygon parts Phase 1), #549 (scene/v2 viewer), + a new small data/regression issue carved from #548.
- **Refs:** spike #541 (`docs/spikes/polygon-part-geometry-feasibility.md`), ADR-0001 (mesh deferral), ADR-0002 (det(−1) transform), ADR-0003 (determinism), ADR-0012 (fuselage split), ADR-0017 (3D viewer), ADR-0023 (empennage).
- **Decision panel:** 8-agent deliberation (5 lenses → synthesis → adversarial challenge), 2026-06-10. Its code-verified findings are folded in below.

---

## 1. Goal & scope

Make plane geometry realistic enough to:

1. **Eliminate false collision / pathfinding conflicts** at tight nesting spots (the measured value: a tapered glider wingtip nests where its bounding rectangle falsely conflicts — a robust 0.10–0.30 m verdict-flip window on the real Herrenteich layout).
2. **Let a human read the 3D viewer and map it to the real hangar** for planning — "that's the Scheibe nested under the Stemme tail."

Deliver an **honest 2.5D** representation now — polygon footprints for collision, extruded prisms for the viewer — with seams left open for two future enhancements: a **wing-tilt/roll degree of freedom** (for tandem-gear gliders parked wing-down) and **true-3D curved meshes**.

### In scope
- Optional polygon footprint on `Part`, with a load-time canonicalization determinism invariant.
- The collision/transform build-path for polygon parts (already polygon-generic per spike Q1).
- A parametrized `planform:` loader schema (root/tip chord → loader-expanded hexagon).
- Realistic viewer: `scene/v2` + extruded-prism rendering (N-gon outlines).
- Glider taper data (Scheibe SF-25E wing) + the flip-window regression proving the value.

### Out of scope (explicitly)
- A tilt/roll DoF (design *toward* it only — see §6).
- True-3D curved meshes / airfoil sections (ADR-0001's deferral stands; design *toward* it only).
- A raw `vertices:` author primitive (no honest data source today — see §3).
- Viewer planning UI: dimension labels, click-to-measure, scale grid (a separate future effort).
- The folded Stemme wing as a taper (folding ≠ taper — stays a rectangle; see §5).

---

## 2. Delivery — a 3-PR stack

All three branches base on `develop` and merge in order (per CLAUDE.md's stacking rule: feature PRs base on `develop`, never on a parent feature branch, or CI/issue-linkage silently no-ops).

| PR | Issue | Delivers | Byte-identity status |
|---|---|---|---|
| **1 — Polygon model & collision** | #548 (re-scoped: data moves out) | `Part.local_vertices` + canonicalization invariant + `aircraft_parts_world` build-path + loader `planform:` schema + determinism solve-scenario + unit suites | Real fleet **byte-identical** (no fleet data uses the feature yet) |
| **2 — Viewer realism** | #549 | `scene/v2` + `ExtrudeGeometry` + N-gon anchors/labels + committed `viewer.js` rebuild | Box parts render identically; N-gon path exercised by tests |
| **3 — Glider data & value proof** | new small issue (carved from #548) | Scheibe wing taper authored + flip-window regression | First visible/measurable change — both collision & viewer already render N-gon |

**Why this order.** Landing the viewer (PR2) *before* the taper data (PR3) means the "3D viewer shows boxes while the collision verdict uses a taper" contradiction **never exists on `develop`**, and the transitional `scene._anchors`-pin workaround (needed only if collision goes polygon while the viewer stays boxes) is **never written**. It also isolates the determinism crux (PR1's canonicalization) from the viewer bit-identity surface (PR2's `viewer.js` rebuild + JS parity oracle) — the two most byte-identity-sensitive surfaces in the codebase — into separate, tractable reviews.

---

## 3. PR1 — Polygon model & collision

### 3.1 `models.Part`
Add a trailing, defaulted field so every existing keyword construction stays valid and scalar parts are unchanged:

```python
local_vertices: tuple[tuple[float, float], ...] | None = None
```

A tuple-of-tuples (not a list) for `frozen=True, slots=True` hashability. `None` ⇒ today's scalar oriented rectangle (back-compat).

### 3.2 Canonicalization invariant (the determinism crux)
When `local_vertices is not None`, `Part.__post_init__` canonicalizes via a module-level, unit-testable `_canonicalize_ring(verts)` helper:

1. Reject non-finite coordinates (NaN/inf), fewer than 3 vertices, self-intersecting (non-simple) rings, and degenerate/collinear rings (signed area ≈ 0).
2. **Force CCW** by signed-area sign.
3. **Rotate to a lexicographically-minimum start vertex** (canonical start).
4. **Drop the closing duplicate** (store the open ring).

Two equivalent orderings of the *same* shape must produce a **byte-identical** `Part`. The geometry layer must **never** re-orient at solve time — Shapely's `Polygon()` preserves vertex order verbatim, so order is author-controlled the moment vertices are supplied (spike Q5). This is what protects ADR-0003 (every `coords[:-1]` consumer reports the *first* violator; the JS oracle compares per-corner at 1e-6).

### 3.3 Loader-asserted bbox-subset invariant
The canonical ring must be a subset of the `length_m × width_m` local bbox. This:
- keeps `length_m`/`width_m` the true bounding box for all scalar consumers (`metrics`, scene boxes, `towplanner` apron),
- keeps `_check_sum_areas` a sound lower bound (polygon area ≤ bbox area),
- and **fails loud** if a `planform:` block is later ported to an aircraft whose bbox differs (the data/fleet `length_m: 1.2` vs herrenteich `1.01` drift the panel flagged) instead of silently producing a non-subset polygon.

### 3.4 `geometry.aircraft_parts_world`
Add a branch: when `local_vertices` is set, route those canonical vertices through `local_to_world` directly (skip `oriented_rect`, no closing-dup to strip); else today's `oriented_rect` path. **Every** declared vertex rides the same det(−1) affine — no centroid/bbox shortcut (geometry-invariant-guard verifies this).

### 3.5 Loader `planform:` schema
`loader._build_part` gains a `planform: {root_chord_m, tip_chord_m}` block, mirroring the `struts:` idiom exactly (`_build_struts_spec`): required-key check + unknown-key rejection + loader expansion into `local_vertices`. Validation: `0 < tip_chord_m <= root_chord_m` (a glider wing does not taper outward).

**No raw `vertices:` field.** Panel verdict: high-confidence, unanimous. 0/8 fleet aircraft expose a dimensioned outline in any TCDS; the lone traceable 3-view (Cessna 140) is constant-chord. A raw `vertices:` primitive would ship with zero honest data, *look* surveyed (which `measured:false` understates), and turn every canonicalization branch into a load-bearing gate against adversarial author rings. `Part.local_vertices` is the shared internal store, so `vertices:` is a strictly-additive future branch behind the same invariant if a surveyed outline ever lands. YAGNI.

### 3.6 Determinism solve-scenario fixture
Add a **new** scenario fixture (a taper glider actually *placed*, not bay-parked) fed to a `solve()` double-run for byte-identity. Necessary because the canaries run `solve(scenario)`, the canary fixture excludes the Scheibe, and every existing scenario that lists the Scheibe parks it as the maintenance plane (geometrically absent). Without this, the polygon ring + canonicalization would ship with **zero** ADR-0003 contract coverage. (A `check`-only test exercises collision geometry but never the seeded restart loop the contract is about.)

---

## 4. PR2 — Viewer realism

- **`scene/v2`**: each part carries explicit footprint vertices + `[z_bottom, z_top]` (a versioned bump from v1's scalar `length`/`width`/`angle` box). The Python transform stays authoritative (ADR-0017).
- **`viewer.js`**: `BoxGeometry` → `ExtrudeGeometry`, de-risked by the in-tree `ShapeGeometry` L-floor precedent (#530).
- **`anchors.ts` / `labels.ts`**: generalized from a fixed 4-corner oracle to N vertices, kept bit-identical to the Python `scene._anchors` oracle (the only cross-language check; `viewer.js` is not pytest-covered). Committed `viewer.js` rebuild under the `viewer-build-drift` guard (#438).

Because PR2 lands before any real taper data (PR3), box parts render identically and the N-gon rendering path is proven by tests, not by a visible fleet change.

---

## 5. PR3 — Glider data & the geometric decisions (panel-verified)

- **Scheibe SF-25E wing only.** Symmetric double-taper with **no sweep, root kink at y=0** → a **hexagon** (both leading and trailing edge recede toward each tip; *not* a 4-vertex "trapezoid"). The convention is pinned and documented in the loader and ADR-0024.
- **Folded Stemme wing stays a rectangle.** Folding swings the outer panels — it is not a taper; a linear-taper polygon would fabricate a planform that does not physically exist in the hangared (folded) config (a provenance violation, not a fidelity gain). The measured flip is the Scheibe *wing* over the Stemme *empennage* (tail + fin rectangles), so the Stemme wing is off the critical path.
- **Flip-window regression.** Assert the **loader-built** hexagon reproduces the spike's measured 0.10–0.30 m rect-rejects / taper-accepts window — the value proof, grounded in the *shipped* parametrization (not a hand-built measurement polygon).

### 5.1 The mean-chord-vs-bbox resolution
The herrenteich Scheibe wing `length_m: 1.01` is the **mean** chord (= area 18.20 / span 18.00). An honest linear taper has root > mean > tip, so a single trapezoid cannot simultaneously be a strict subset of a `length_m = 1.01` bbox **and** conserve the published mean chord.

**Resolution:** set `root_chord_m = 1.01` (the existing bbox length) and `tip_chord_m = 0.45 × root` (the spike's measured taper ratio). The polygon stays a **strict subset** of the existing bbox, the flip reproduces, and there is **no golden re-pin**. Wing area is intentionally **under-conserved** — the *conservative* footprint direction: the bounding box over-claims, the taper sits safely inside it, and the area-gate reads scalars (`length×width`), so it stays sound. The under-conservation is documented in ADR-0024.

(Rejected alternative: grow `length_m` to the root chord — forces a herrenteich golden re-pin and scalar-consumer churn for no validity gain.)

---

## 6. Forward-compat seams (design-toward, NOT built)

### 6.1 Wing tilt / roll DoF
- Keep **all** part geometry vertex-routed through the single `local_to_world` (no centroid/bbox shortcuts) — a future roll transform extends the pipeline rather than rewriting consumers.
- Keep `[z_bottom, z_top]` explicit per part.
- **Version the scene schema** (`scene/v2`) so a future roll angle / per-vertex height extends it without a contract rewrite.
- Generalize the viewer off a hardcoded "4 corners" (PR2 does this anyway for N-gon).
- **Known extension point (documented, not coded):** a tilted wing is not a vertical prism — its height varies across span — so `WorldPart` would become a sloped solid and the collision predicate would gain a height-profile term. Noted as the explicit place tilt support will land.

### 6.2 True-3D meshes (future B)
- `scene/v2` carries enough (vertices + height band) that a future renderer swaps `ExtrudeGeometry` for a loaded mesh in one isolated geometry-construction site.
- ADR-0001's mesh deferral stands; this design does not preclude it.

---

## 7. Testing & determinism strategy

- **Canonicalization unit matrix — lands first**: CCW forcing, lex-min-start rotation, closing-dup drop, reject non-finite / <3-vert / self-intersecting / degenerate; two equivalent orderings → identical `Part`.
- Geometry per-vertex det(−1) at a **non-axis-aligned heading** (geometry-invariant-guard requirement).
- Loader `planform:` parse, `tip > root` rejection, unknown-key rejection, non-subset-of-bbox rejection.
- **New placed-taper determinism scenario** fed to a `solve()` double-run (byte-identity).
- Scalar-fleet golden byte-identity (the cleanest proof of the no-regression half).
- **(PR2)** scene/v2 schema + N-gon anchor ↔ Python-oracle parity.
- **(PR3)** the flip-window regression.

---

## 8. Guard arc, ADRs, CHANGELOG

- **PR1:** geometry-invariant-guard (geometry.py) + type-design-analyzer (models.py) + silent-failure-hunter (loader) + determinism double-solve (new taper scenario) + comment-analyzer (ADR/docstrings). **ADR-0024** refines ADR-0001's mesh deferral → optional polygon parts + canonicalization invariant + the deliberate area under-conservation.
- **PR2:** comment-analyzer + the scene-v2 schema reference doc; extend ADR-0017 (or a new ADR) for the v1→v2 seam.
- Each PR carries its own `CHANGELOG.md [Unreleased]` entry, opens as a **draft**, runs the full `/pr-review` arc, and flips to ready only when the review arc is clean. The user is the sole merger.

---

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Author vertex order breaks byte-identity (ADR-0003) | Load-time canonicalization invariant + the unit matrix landing first |
| Viewer parity banner on N-gon (`_anchors` ↔ `viewer.js` mismatch) | PR ordering (viewer before data) avoids the interim entirely; PR2 generalizes both the oracle and the JS to N vertices in lockstep |
| Polygon ring ships with no determinism coverage | New *placed*-taper solve-scenario, not a check-only test |
| `planform:` ported to a different bbox silently goes non-subset | Loader-asserted bbox-subset invariant fails loud |
| Loader-built hexagon ≠ the spike's measured polygon | Regression asserts the shipped parametrization reproduces the 0.10–0.30 m flip window |
| Fabricating a folded-Stemme taper | Stemme wing stays a rectangle |

---

## 10. Open decisions — resolved

- **Schema:** `planform_only` (no raw `vertices:`). High confidence, unanimous.
- **Where (fleet data):** herrenteich-only (Scheibe wing); `data/fleet.yaml` untouched.
- **Area conservation:** intentionally under-conserved; `root = bbox length`, no golden re-pin (§5.1).
- **Viewer scope:** extruded-prism realism now; true-3D and planning UI are future, design-toward only.
- **Tilt:** design-toward (seams in §6); not built.
