Catalog of per-object aircraft definitions (#595) — the single source of static
aircraft data, referenced by both the demo `data/fleet.yaml` manifest and
`examples/herrenteich/fleet.yaml`.

DIMENSIONS ARE A MIX. The eight Airfield Herrenteich occupants (cessna_140,
ctsl, wild_thing, aviat_husky, scheibe_falke, fk9_mkii, stemme_s10, zlin_savage)
carry published-spec / TCDS-sourced numbers — see "Real-spec provenance" below.
`fuji` and `cessna_150` (not based at Herrenteich) remain eyeballed placeholders.
Every entry keeps `measured: false` because none are on-site tape/laser
measurements; treat the sourced figures as authoritative over the placeholders.

Conventions (see `docs/architecture/08-crosscutting-concepts.md`
"The coordinate convention" and "The parts model" for the full version):
- Plane-local coords: +x = forward (toward nose), +y = right (toward right wingtip)
- Origin of plane-local frame = a per-aircraft anchor used as the placement
  reference (where Placement.x_m/y_m positions the plane in world coords).
  The main-gear centroid is a *derived* point, accessible as
  (wheels.main_offset_x_m, 0) in plane-local coords. See ADR-0013.
- Each Part is an oriented rectangle: length_m runs along plane +x,
  width_m runs along plane +y
- Heights z_bottom_m / z_top_m are above-ground in world coords
- struts block (optional) is expanded by the loader into two mirrored
  strut Parts; cantilever aircraft omit the block entirely
- wheels block (required) declares the β-schema main_offset_x_m, track_m,
  and third_wheel_offset_x_m fields per aircraft (see ADR-0013). Monowheel
  aircraft set only main_offset_x_m. The loader cross-checks the
  wheel-derived wheelbase against turn_radius_m for own-gear aircraft.
- a `kind: fuselage` part is auto-split by the loader into a
  `fuselage_front` (cockpit) + `fuselage_aft` (tail) pair at the wing
  trailing-edge station (`wing.offset_x_m - wing.length_m/2`); no per-plane
  edit is needed and there is NO `wing_root_x_m` field. The split lets the
  collision rule allow a wing over another plane's tail but reject a wing
  over its cockpit (hard conflict, height ignored). See ADR-0012 +
  `docs/architecture/08-crosscutting-concepts.md` "The parts model".
  (Explicit `fuselage_front`/`fuselage_aft` parts are a valid override the
  loader does not split; an aircraft with a `fuselage` part needs a `wing`
  part to derive the break.)
- turn_radius_m: own-gear minimum taxi turn radius (m). LOAD-BEARING since
  Phase 3a — consumed by the `towplanner` module's Reeds–Shepp / Hybrid-A*
  path planning (it was an unused placeholder through Phase 1/2a). `null`
  on always_cart planes: they have no own-gear taxi radius, so the
  towplanner substitutes 0 (pivot-in-place on the cart) via
  `Aircraft.effective_turn_radius_m()` rather than baking 0 into the data
  (ADR-0007, fork 4).
- Empennage (ADR-0023, #518/#519/#520): each aircraft carries a `tail` (the
  horizontal stabilizer / elevator — wide, held below the wing layer for
  conventional/cruciform tails so it stays overhangable) and a
  `vertical_stabilizer` (the fin + rudder — thin, on the centreline, rising
  from the fuselage top to the published overall height, into the wing layer).
  Spans/chords are published-spec-absent ESTIMATES; tail configs + overall
  heights are sourced. All measured: false. A wing nested over another plane's
  tail now conflicts with that plane's fin unless it clears it laterally.

## Ground objects (#601)

The catalog supports non-aircraft floor occupants via three `type:` values.
See [ADR-0025](../../docs/adr/0025-ground-object-taxonomy.md) for the full
rationale; the summary is below.

### `type:` vocabulary

| `type:` | `object_class` | motion default | use case |
|---|---|---|---|
| `fixed_obstacle` | `"fixed_obstacle"` | none (no motion fields allowed) | parked fuel trailer, fixed equipment |
| `car` | `"placed_routed_mover"` | `"steerable"` | self-driven vehicle (e.g. VW Caddy) |
| `trailer` | `"placed_routed_mover"` | `"towed"` | towed trailer (e.g. glider trailer) |

### Parts convention for ground objects

All ground-object parts use `kind: ground` — a footprint-only kind distinct
from the aircraft part kinds. The collision checker treats `kind: ground` parts
as keep-out geometry for fixed obstacles and as pairwise collision bodies for
movers. A `ground` part is never overhangable and is never split by the
fuselage front/aft loader logic.

Typical ground-object parts are single-rectangle footprints:

```yaml
type: fixed_obstacle
id: fuel_trailer
name: Fuel trailer
measured: false
parts:
  - kind: ground
    length_m: 4.20       # fore-aft in plane-local coords (object +x)
    width_m:  1.80       # lateral (object +y)
    offset_x_m: 0.0
    offset_y_m: 0.0
    angle_deg: 0.0
    z_bottom_m: 0.0
    z_top_m: 1.5
```

Heights (`z_bottom_m` / `z_top_m`) should represent the real physical extent
so the two-clause predicate can detect an aircraft wing passing over versus
genuinely clearing the object.

### Motion fields (movers only)

`car` and `trailer` entries may carry an optional `motion_mode:` override
(valid values: `"steerable"`, `"towed"`) and an optional `turn_radius_m:`
(the minimum taxi / tow turn radius in metres, positive). Fixed obstacles
must not carry either field; the loader rejects them.

```yaml
type: car
id: example_car               # illustration only — the real entry is vw_caddy (below)
name: Example car
measured: false
motion_mode: steerable        # explicit override; default for car is already steerable
turn_radius_m: 5.5
parts:
  - kind: ground
    length_m: 4.59
    width_m:  1.85
    offset_x_m: 0.0
    offset_y_m: 0.0
    angle_deg: 0.0
    z_bottom_m: 0.0
    z_top_m: 1.85
```

### Real Herrenteich ground-object entries (#605)

The real Airfield Herrenteich ground objects are now authored in this catalog
(added by #605, on top of #601's taxonomy + loader):

| id | `type:` | envelope L×W×H (m) | basis (`measured: false`) |
|---|---|---|---|
| `vw_caddy` | `car` | 4.88 × 1.79 × 2.04 | VW Caddy Maxi (long-wheelbase) manufacturer L/W; height = 1.84 m stock roofline + ~0.20 m roof-stowed gear (on-site, PK). **Multi-part** (#658): a body box (0→1.84 m) + a small ~1.0×0.8 m roof-rack box (1.84→2.04 m), so a high wing may overhang the van and only has to clear the localized rack |
| `glider_trailer_1` | `trailer` | 10.5 × 2.0 × 2.0 | Duo Discus (two-seat) closed trailer — the longer one (#657); fuselage ~8.7 m + nose/towbar overhang |
| `glider_trailer_2` | `trailer` | 9.0 × 1.75 × 1.45 | single-seat 15 m-class closed trailer (owner-measured Cobra, #657) |
| `maul_fuel_trailer` | `fixed_obstacle` | 4.5 × 2.0 × 1.9 | Maul road-trailer envelope (estimated) |

All carry `measured: false` (published/typical specs, not an on-site survey).
Mover tow-routing (#602), the hard Caddy clear-egress gate (#603/#652), and
ground-object rendering in the 2D PNG + 3D viewer (#606) have all shipped. The
realistic `examples/herrenteich/layout_full.yaml` (#657/#659) parks the fuel
trailer hard against the left wall by the door (straight-in, last), the Caddy near
the door with a clear drive-out egress, and the Duo trailer along the right wall —
all four GOs are inside. Fitting them plus a Caddy rescue path needs FISHBONE
(angled) aircraft parking, and even then the hangar is one aircraft over capacity,
so it holds SEVEN of the eight aircraft (the Scheibe Falke parks outside). All four
GOs remain available in the herrenteich `fleet.yaml` manifest.

## Real-spec provenance (Airfield Herrenteich occupants, #536/#594)

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

MODELLING NOTES (see the Conventions section above for the full parts-model
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
