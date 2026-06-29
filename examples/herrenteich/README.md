# Airfield Herrenteich — real dataset

A **real-world** dataset for the club's main-building hangar. The hangar,
layout, and scenario files live here; the aircraft (the eight usual occupants
plus the permanent Fuji FA-200-180 added in #657) are defined once in the central
catalog (`data/catalog/`) and pulled in by this directory's `fleet.yaml`
manifest — the real published-spec numbers ARE the catalog's numbers (#595/#594,
no per-world duplication). Nothing here is wired into the default CLI paths —
point the tools at these files explicitly.

```bash
# Validate the "everyone home" layout (all eight usual occupants):
hangarfit check examples/herrenteich/layout.yaml --render herrenteich.png

# Validate the real 'today' layout (all 9 aircraft + Duo trailer + fuel + Caddy):
hangarfit check examples/herrenteich/layout_today.yaml --render today.png

# 3D viewer:
hangarfit view examples/herrenteich/layout.yaml -o herrenteich.html

# Full toolchain end-to-end on a solvable + tow-routable subset:
hangarfit solve examples/herrenteich/scenario_demo.yaml \
    --render demo.png --render-paths --seed 3   # solve + tow paths around the notch
hangarfit view  examples/herrenteich/scenario_demo.yaml -o demo.html --seed 3
```

## Files

| File | What it is |
|---|---|
| `hangar.yaml` | The real hangar — **15.08 m × 31.76 m**, door **13.46 m** wide. Measured 2026-06-04 from the architect's DWG. L-shaped (back-right office notch). |
| `fleet.yaml`  | The aircraft usually hangared here — the eight usual occupants plus the permanent **Fuji FA-200-180** (#657, the only low-winger; a placeholder for a future C150) — plus (since #605) the four non-aircraft floor occupants under `ground_objects:` (Caddy, 2 glider trailers, fixed fuel trailer). Envelope (span/length/height) from published specs; part-level dimensions (wing chord, fuselage width, tail spans, gear track/wheelbase) sourced from EASA/FAA TCDS + manufacturer manuals where published (refreshed 2026-06-08, #536); the rest derived/estimated and flagged inline. |
| `layout.yaml` | A **valid** arrangement with all eight usual aircraft parked at once (`hangarfit check` → exit 0). Aircraft only — no ground clutter. This is where the "all eight fit" promise lives. |
| `layout_today.yaml` | **The real 'today' layout (#664)** — the club's actual in-hangar set as described on 2026-06-15: all **nine** aircraft (incl. the Scheibe Falke) + the **one** Duo Discus glider trailer (the spare trailer is stored elsewhere) + the fixed fuel trailer + the rescue Caddy with a clear drive-out egress. This real composition drove the clearance recalibration (0.20 → 0.10 m — see below). Valid (`hangarfit check` → exit 0). |
| `layout_full.yaml` | An **alternative scenario (#657/#659)** — what if **both** glider trailers must stay inside *and* the Caddy keeps its egress? That is one body over capacity, so it parks only **seven** aircraft (the Scheibe goes outside), fishbone. Kept as the "both trailers inside" what-if; the real day keeps the aircraft instead (see `layout_today.yaml`). Valid (`hangarfit check` → exit 0). |
| `scenario.yaml` | The solver input for the all-eight "everyone home" scenario (does not fully route — see below). |
| `scenario_demo.yaml` | A 3-aircraft subset that **solves and fully tow-routes** end-to-end in the L-shaped hangar — the working toolchain demo. |

> **Dolly gliders are hand-positioned (#667 Rung A).** As of #667 Stage 0, the
> dolly-borne gliders are marked `hand_placed: true` in these layouts — the
> Scheibe Falke + Stemme S10 in `layout.yaml` / `layout_today.yaml`, and the
> Stemme only in `layout_full.yaml` (the Scheibe parks outside there). The fill
> planner then treats them as fixed keep-outs and does **not** emit a tow path for
> them (they go in by hand), so `view` / `solve --render-paths` route only the
> powered aircraft around them. This is inert to static validity (`hangarfit
> check` is unaffected). The lone-Stemme own-gear straight-in noted below is still
> true as a *capability*; the dense shipped layouts just model it on its dolly.

## Three things this dataset is honest about

**1. The hangar is L-shaped, and since ADR-0018 (#527) the model knows it.**
The real back-right corner is notched out (~2.36 m × 9.10 m) — that's
office/annex space, not hangar floor. `hangar.yaml`'s `structural_notches` block
carves it out of the floor, so `hangarfit check` (and the solver / tow planner)
now reject any layout that parks — or even overhangs — a plane in it. The
earlier rectangular model kept planes clear of the notch only by hand; spike
**#424** designed the fix and **#528** shipped it.

**2. `layout.yaml` is the tool's arrangement, not the club's real parking.**
The product solver (`hangarfit solve`) cannot produce the **all-eight** layout:
finding an eight-way nested arrangement (around an 18 m glider, in a narrow
L-shaped hangar) within budget is beyond the search. The all-eight `layout.yaml`
was found by driving the real, part-based collision checker directly. **A
solvable + tow-routable subset is `scenario_demo.yaml`** — three aircraft the
solver places clear of the notch and the tow planner routes from the door around
it (the end-to-end demo above). Replace the all-eight placements with the club's
real parking positions when known.

**3. The real day keeps all nine aircraft + one trailer — and that recalibrated
the clearance (#664).** The real hangar parks more than aircraft: a VW Caddy, glider
trailers, and a fixed "Maul" fuel trailer. Two on-site rules are hard (#657): the
**fuel trailer** sits front-left near the door (pushed straight in, parked last),
and the **rescue Caddy** must keep a **clear drive-out egress** — it leaves without
anyone moving anything else (#603/#652). Real hangars pack **fishbone** (aircraft
nosed in at mixed angles), which interleaves the wings far tighter than an
orthogonal nest. When PK described the **actual** arrangement (2026-06-15), it was
**all nine aircraft + ONE glider trailer (the Duo) + fuel + Caddy** —
`layout_today.yaml`. The club keeps the aircraft and leaves the *spare* trailer
outside; the earlier `layout_full.yaml` had done the opposite (both trailers in →
one aircraft out), which is why it dropped the Scheibe.

Reproducing the real set exposed a **calibration gap**, not a model bug: an offline
checker-driven search could not find *any* valid arrangement of all nine aircraft +
the trailer + fuel + Caddy at the previous `clearance_m 0.20` (its best still left a
few conflicts) but seats them cleanly at **0.10 m**, and PK confirmed the real
wingtip-to-part gaps **vary a lot** and on
dense days are very tight (well under 0.20 m). So the horizontal clearance was
recalibrated **0.20 → 0.10 m** (`layout_today.yaml`'s tightest gap is ~0.10 m); the
0.15 m vertical (wing-layer) clearance was not the binding constraint and is
unchanged. Lowering the horizontal clearance only relaxes the constraint, so
`layout.yaml`, `layout_full.yaml`, and `scenario_demo.yaml` all stay valid. (History:
the #605 placeholder `0.3 / 0.2` was first set to `0.20 / 0.15` from the all-8 + 4-GO
frontier of ≤0.22/0.15.) The Caddy is modelled **multi-part** (#658 — a low van body
a high wing may overhang, plus a small roof-gear rack on top) so it isn't a
full-height wall. Mover tow-routing (#602), the Caddy clear-egress gate (#603/#652),
and ground-object rendering (#606) have all shipped; **reliably packing this dense a
12-body set is beyond the deterministic search** — both `layout_today.yaml` and
`layout_full.yaml` are offline-search arrangements of the real composition, and the
joint dense-placement+routing problem remains an open hard problem. The dedicated
#667 shuffle-aware tow-routing program (Rungs A–E, all merged 2026-06-29) shipped
move-aside repair as a byte-identical capability seam (Rung E, #869), but as
measured it does **not** crack the dense all-8: that fill stays budget-bound (it
bails on `zlin_savage` at the 8000-expansion cap, so phase-1 search raises before
move-aside can engage), so the dense all-8 route stays out of reach and the
`fk9_mkii`↔`cessna_140` pair remains a documented manual-insertion case.

## Notable aircraft

- **Scheibe SF-25E Super Falke** — 18 m span, wider than the 15.08 m hangar
  width, so it parks lengthwise. It is really a **low-wing** glider (EASA TCDS),
  but its wing is modelled in the high layer: as a tiltable monowheel it raises
  one wingtip high to nest over neighbours, and the single-z model can only
  represent that by placing the wing high. A flat *low* wing would be an 18 m
  wall no all-eight arrangement can clear (search-verified) — so the z-layer is a
  deliberate tilt abstraction while the *dimensions* are real (see `fleet.yaml`
  header).
- **Stemme S10** — hangared **wings folded** (11.4 m span; 23 m unfolded), which
  is what lets a 23 m glider through a 13.46 m door. A **taildragger** (twin
  retractable mains + tailwheel, EASA TCDS) — corrected from monowheel this refresh.
  The 11.4 m folded width is verified (EASA TCDS A.054 + Jane's + AOPA) and clears
  the door by ~2 m; a lone Stemme routes in through the door **on its own gear**
  (probed: 1-segment straight-in). The dolly (`always_cart` in `fleet.yaml`) is for
  maneuvering it within the *dense multi-plane* fill, not a width limit — that
  joint placement+routing difficulty is tracked on #607 (and the #667 shuffle-aware
  tow-routing program, Rungs A–E now merged), not a folded-span error.

> All fleet dimensions carry `measured: false` — the envelope is published spec
> and the part-level dimensions are 3-view/TCDS-**sourced** but not on-site
> surveyed, so the viewer/PNG still show the "PLACEHOLDER DATA" honesty banner.
> The hangar rectangle itself is from the DWG.
