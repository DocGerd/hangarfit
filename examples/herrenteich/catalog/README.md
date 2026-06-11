Airfield Herrenteich — real fleet (the aircraft usually hangared here).

Kept separate from the synthetic data/fleet.yaml (the project's stable
demo/test fixture). Roster per the operator (2026-06-04): Cessna 140,
Flight Design CTSL, Wild Thing, Aviat Husky, Scheibe SF-25E, FK9 Mk II,
Stemme S10 (hangared WINGS-FOLDED), Zlin Savage. (Fuji and the Cessna 150
are not based here.)

DIMENSIONS — provenance refresh 2026-06-08 (#536). The primary envelope
(span / length / height) was looked up from published manufacturer /
type-certificate specs on 2026-06-04 and second-source verified. This refresh
additionally SOURCED the part-level dimensions that ARE published — wing chord
(= wing area ÷ span), fuselage/cabin width, a few tail spans, and a few gear
track/wheelbase figures — from EASA/FAA TCDS, manufacturer maintenance manuals,
and flight manuals (citations per-field below). Verified primary dims:

  id             span(m)  len(m)  height(m)  notes
  cessna_140     10.16    6.55    1.91       strut high-wing taildragger
  ctsl            8.59    6.60    2.34       cantilever high-wing, nosewheel (EASA TCDS)
  wild_thing      9.15    6.49    1.90       ULBI Wild Thing WT-02, strut (WT-01 baseline)
  aviat_husky    10.82    6.88    2.26       Husky A-1C, strut taildragger
  scheibe_falke  18.00    7.58    1.85       SF-25E motor glider (real LOW wing; modelled HIGH — tilt)
  fk9_mkii        9.85    5.85    2.15       FK9 Mk II, strut high-wing
  stemme_s10     23.00*   8.42    1.80       *11.40 m FOLDED (hangar config); TAILDRAGGER
  zlin_savage     9.31    6.39    2.03       Savage Cub, strut taildragger

CONFIG CORRECTIONS applied this refresh (sources disagreed with the prior
model; primary TCDS/manufacturer sources win):
- stemme_s10: the S10 is a TAILDRAGGER (two inward-retracting narrow-track main
  wheels, track 1.15 m, + a steered tailwheel; main→tail wheelbase 5.40 m), NOT a
  monowheel (EASA TCDS A.054; maintenance manual). Modelled here on its extended
  gear in the wings-folded hangar configuration.
- ctsl: the CTSL tail is CONVENTIONAL-low (a fuselage-roof all-moving stabilator;
  EASA TCDS A.537 + Flight Design maintenance manual), NOT cruciform (that was a
  secondary-source label). Geometry unchanged — the stabilator already sits low.

NOTE NOT applied (deliberate modelling abstraction kept): the real SF-25E is a
LOW-wing aircraft (EASA TCDS A.098 — lowered from the SF-25B on), but its wing
stays modelled in the HIGH z-layer. The SF-25E is a tiltable monowheel glider:
resting on its single mainwheel it raises one wingtip high (the other drops onto
a tip outrigger), so a raised half-wing CAN overhang a neighbour. The single-z
parts model can only represent that nesting by placing the wing high; a flat
18 m LOW wing would instead be a wall that no all-eight arrangement can clear
(verified: extensive search finds no valid all-8 with the wing low). So the wing
is modelled high as a deliberate tilt simplification — its DIMENSIONS are real,
only the z-layer is a modelling choice. Revisit when a tilt-aware model exists.

measured: false is kept on every entry — these are PUBLISHED/SOURCED specs, not
on-site tape/laser measurements, so they still raise the viewer's "PLACEHOLDER
DATA" honesty banner. Flip to true once verified on site.

WHAT IS STILL DERIVED/ESTIMATED (genuinely unpublished for these light types —
no accessible dimensioned three-view): the part STATIONS (offset_x fore-aft
positions), most tail chords, all fin chords, and all lift-strut attach points.
These are geometrically derived from the sourced envelope and flagged inline.
A fully-measured fleet needs on-site survey or the actual TCDS three-view.

MODELLING NOTES (see data/fleet.yaml header for the full parts-model
conventions and ADR-0012/0013):
- Each Part is an oriented rectangle: length_m along plane +x (fore-aft),
  width_m along plane +y (lateral = wingspan for the wing part). Wing length_m
  is the chord; wing width_m is the span. Exception: the scheibe_falke wing
  carries an optional `planform:` taper (a 6-vertex hexagon footprint, ADR-0024)
  — length_m/width_m stay its bounding box; every other part stays a rectangle.
- Part z_bottom/z_top encode the vertical layer the nesting rule uses (a high
  wing may legally overhang a neighbour's lower tail/fuselage when z-disjoint).
  High wings sit ~1.9-2.3 m; fuselages ~0-1.5 m.
- stemme_s10 is modelled in its WINGS-FOLDED hangar configuration: the wing part
  width is the folded 11.40 m span (unfolded 23 m would not enter the 13.46 m
  door). Folded outer panels raise the effective height.
- Empennage (ADR-0023, #518/#519/#520): each aircraft carries a `tail` (the
  horizontal stabilizer) + a `vertical_stabilizer` (fin+rudder rising to the
  published height column above). stemme_s10 is the one T-tail (stabilizer at
  the fin top); all others (incl. ctsl, corrected) are conventional-low. Tail
  configs + heights + the listed tail spans (cessna, ctsl) are sourced; other
  tail spans and all tail/fin chords are estimates. All measured: false.
