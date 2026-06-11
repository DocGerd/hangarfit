# Airfield Herrenteich — real dataset

A **real-world** dataset for the club's main-building hangar. The hangar,
layout, and scenario files live here; the eight aircraft are defined once in the
central catalog (`data/catalog/`) and pulled in by this directory's `fleet.yaml`
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
| `fleet.yaml`  | The eight aircraft usually hangared here. Envelope (span/length/height) from published specs; part-level dimensions (wing chord, fuselage width, tail spans, gear track/wheelbase) sourced from EASA/FAA TCDS + manufacturer manuals where published (refreshed 2026-06-08, #536); the rest derived/estimated and flagged inline. |
| `layout.yaml` | A **valid** arrangement with all eight planes parked at once (`hangarfit check` → exit 0). |
| `scenario.yaml` | The solver input for the all-eight "everyone home" scenario (does not fully route — see below). |
| `scenario_demo.yaml` | A 3-aircraft subset that **solves and fully tow-routes** end-to-end in the L-shaped hangar — the working toolchain demo. |

## Two things this dataset is honest about

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

> All fleet dimensions carry `measured: false` — the envelope is published spec
> and the part-level dimensions are 3-view/TCDS-**sourced** but not on-site
> surveyed, so the viewer/PNG still show the "PLACEHOLDER DATA" honesty banner.
> The hangar rectangle itself is from the DWG.
