# Airfield Herrenteich — real dataset

A self-contained, **real-world** dataset for the club's main-building hangar,
kept separate from the synthetic placeholders in `data/` (which stay as the
project's stable demo/test fixtures). Nothing here is wired into the default
CLI paths — point the tools at these files explicitly.

```bash
# Validate the "everyone home" layout (all eight usual occupants):
hangarfit check herrenteich/layout.yaml --render herrenteich.png

# 3D viewer:
hangarfit view herrenteich/layout.yaml -o herrenteich.html
```

## Files

| File | What it is |
|---|---|
| `hangar.yaml` | The real hangar — **15.08 m × 31.76 m**, door **13.46 m** wide. Measured 2026-06-04 from the architect's DWG. |
| `fleet.yaml`  | The eight aircraft usually hangared here. Dimensions looked up from published specs and second-source verified (2026-06-04). |
| `layout.yaml` | A **valid** arrangement with all eight planes parked at once (`hangarfit check` → exit 0). |
| `scenario.yaml` | The solver input for the "everyone home" scenario. |

## Two things this dataset is honest about

**1. The hangar is L-shaped; the model is a rectangle.** The real back-right
corner is notched out (~2.36 m × 9.10 m) — that's office/annex space, not
hangar floor. The model only has a rectangle, so `hangar.yaml` records the
bounding rectangle and the files keep planes clear of the notch by hand.
Teaching the model the notch is spike **#424**.

**2. `layout.yaml` is the tool's arrangement, not the club's real parking.**
The product solver (`hangarfit solve`) cannot produce this layout. (**#425**
— fixed — once made its trivial-infeasibility gate sum *bounding boxes*, Σ ≈
606 m² > the 479 m² rectangular floor, and bail, because an 18 m-span motor
glider is mostly empty air; the gate now sums actual part footprints, Σ ≈
160 m² « 479, and no longer bails.) It still won't reproduce this layout: the
rectangular model ignores the office **notch** (#424) the layout keeps clear
by hand, and finding an all-eight nested arrangement within budget is a
separate search-feasibility question. The real, part-based collision checker
accepts a nested layout, so this one was found by driving that checker
directly. Replace the placements with the club's real parking positions when
known.

## Notable aircraft

- **Scheibe SF-25E Super Falke** — 18 m span, wider than the 15.08 m hangar
  width, so it parks lengthwise. Being a monowheel it can be *tilted* (one wing up,
  the other down), which is how a glider that wide nests with its neighbours;
  the single-layer wing model is a simplification of that tilt (see
  `fleet.yaml` header).
- **Stemme S10** — hangared **wings folded** (11.4 m span; 23 m unfolded), which
  is what lets a 23 m glider through a 13.46 m door.

> All fleet dimensions carry `measured: false` — they are published specs, not
> on-site measurements, so the viewer/PNG show the "PLACEHOLDER DATA" honesty
> banner. The hangar rectangle itself is from the DWG.
