Fleet definitions for the flying club.

ALL DIMENSIONS BELOW ARE PLACEHOLDERS — replace with real measurements
before trusting any layout the collision checker validates. Every
entry has `measured: false` as a grep handle so you can audit what
still needs real numbers.

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
