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
| `layout_full.yaml` | **The realistic in-hangar set (#657/#659)** — seven of the eight aircraft + **all four** GOs, packed **fishbone** (Caddy near the door with a clear drive-out egress, fuel hard against the left wall by the door, Duo trailer on the right wall). With the rescue Caddy keeping its egress and all four GOs inside, the floor is one aircraft over capacity, so the Scheibe Falke parks outside. Valid at the calibrated clearances (`hangarfit check` → exit 0). |
| `scenario.yaml` | The solver input for the all-eight "everyone home" scenario (does not fully route — see below). |
| `scenario_demo.yaml` | A 3-aircraft subset that **solves and fully tow-routes** end-to-end in the L-shaped hangar — the working toolchain demo. |

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

**3. With the rescue Caddy + fuel inside, the standard parking breaks — which is
the whole point.** The real hangar parks more than aircraft: a VW Caddy, two
glider trailers, and a fixed "Maul" fuel trailer. Two on-site rules are hard
(#657): the **fuel trailer** sits front-left hard against the wall by the door
(pushed straight in, parked last), and the **rescue Caddy** must keep a **clear
drive-out egress** — it has to leave without anyone moving anything else
(#603/#652). Real hangars pack **fishbone** (aircraft nosed in at mixed angles, not
square), which interleaves the wings and packs far tighter than an orthogonal nest;
`layout_full.yaml` parks the aircraft this way (a few near-square, the rest angled).
Even so, fitting all four ground objects **plus** the Caddy's rescue path leaves the
hangar one aircraft over capacity (an exhaustive search, orthogonal and fishbone,
confirms it), so `layout_full.yaml` parks **seven** aircraft + **all four** GOs,
with the Scheibe Falke left outside. That is not a failure — it is exactly the
"standard layout has broken, find a valid alternative" job hangarfit exists for. (Getting even this far needed the #605
**clearance calibration**: the placeholder `clearance_m 0.3` /
`wing_layer_clearance_m 0.2` are too loose for a real club hangar packed this
densely, so `hangar.yaml` was calibrated to **0.20 / 0.15**. Lowering a clearance
only relaxes the constraint, so `layout.yaml` and `scenario_demo.yaml` stay valid.)
The Caddy here is modelled **multi-part** (#658 — a low van body a high wing may
overhang, plus a small roof-gear rack on top) so it isn't a full-height wall.
Mover tow-routing (#602), the Caddy clear-egress gate (#603/#652), and
ground-object rendering in the PNG + 3D viewer (#606) have all shipped; routing the
full set's 18 m Scheibe and joint placement+routing remain the open hard problem
(#607).

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
  joint placement+routing difficulty is tracked on #607, not a folded-span error.

> All fleet dimensions carry `measured: false` — the envelope is published spec
> and the part-level dimensions are 3-view/TCDS-**sourced** but not on-site
> surveyed, so the viewer/PNG still show the "PLACEHOLDER DATA" honesty banner.
> The hangar rectangle itself is from the DWG.
