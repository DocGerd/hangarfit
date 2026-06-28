// Typed mirror of the `hangarfit.scene/v2` contract (Python `scene.py`).
//
// This is the EXTENSION SEAM (ADR-0020): an additive scene field becomes an
// additive interface field here. It is the schema-faithful mirror of
// `docs/architecture/scene-v2-schema.md`; a Python key-set parity test in
// `tests/test_scene.py` fails if `scene.py`'s emitted keys and these interfaces
// drift apart (the near-term desync guard — JSON-Schema single-source is the
// deferred principled fix, spike #444). The runtime `checkAnchors()` self-check
// still guards the transform *values* regardless.
//
// The viewer performs NO transform math (ADR-0002): every plane-local→world
// placement arrives here as a 2x3 affine computed in Python.

/** A 2x3 affine `[a, b, tx, c, d, ty]` mapping local (u,v) → world (x,y). */
export type Affine = [number, number, number, number, number, number];

/** One box of a plane's parts model, in plane-local coordinates. */
export interface BoxData {
  kind: string; // 'wing' | 'strut' | 'fuselage_front' | body kinds…
  cx: number;
  cy: number;
  cz: number;
  length_m: number;
  width_m: number;
  height_m: number;
  angle_deg: number;
  // scene/v2 (#549): explicit height band [z_bottom_m, z_top_m].
  z_band: [number, number];
  // scene/v2 (#549): plane-local (u,v) polygon footprint (angle+offset already
  // folded in, so the affine applies directly), or null for a scalar rectangle
  // — which renders byte-identically to v1 via the box path.
  vertices: [number, number][] | null;
}

export interface PlaneData {
  id: string;
  color: string; // '#RRGGBB'
  boxes: BoxData[];
  wheels: [number, number][]; // plane-local wheel (u,v) positions (ADR-0013)
  on_carts: boolean;
}

// A placed non-aircraft body (#606): a fixed obstacle (keep-out) or a
// placed/routed mover. Same `boxes` shape as a plane, but no wheels/carts; the
// per-class fill is brand-resolved Python-side (`color`), and `final_pose` is the
// placement (resting) affine — a fixed obstacle stays there, a placed-routed
// mover animates to it along its drive path via the timeline (#651).
export interface GroundObjectData {
  id: string;
  object_class: string; // 'fixed_obstacle' | 'placed_routed_mover'
  color: string; // '#RRGGBB' — brand fill by class, resolved in scene.py
  hard_door_mover: boolean; // the Caddy egress flag (highlight cue)
  boxes: BoxData[];
  final_pose: Affine; // plane-local → world affine at the placed pose
}

export interface DoorData {
  center_x_m: number;
  width_m: number;
}

export interface MaintenanceBay {
  closed: boolean; // true iff layout.maintenance_plane is set
  width_m: number;
  depth_m: number;
  center_x_m: number;
  plane_id: string | null; // the absent occupant, or null
}

// An always-on rectangular floor keep-out (ADR-0018): a corner/edge of the
// bounding rectangle that is NOT hangar floor (e.g. the Herrenteich office annex).
export interface StructuralNotchData {
  x_min_m: number;
  y_min_m: number;
  x_max_m: number;
  y_max_m: number;
}

export interface HangarData {
  width_m: number;
  length_m: number;
  door: DoorData;
  maintenance_bay: MaintenanceBay; // always emitted (a dict; `closed` distinguishes occupied)
  structural_notches: StructuralNotchData[]; // always emitted (empty for a rectangular hangar)
}

export interface SegmentData {
  plane_id: string;
  start_s: number;
  end_s: number;
  samples: Affine[];
  // #865 Rung D: execution-order index of this leg within its body's tow. Present
  // ONLY for a multi-leg body (move-aside staging + final, #667 Rung E); a
  // single-leg body omits it (byte-identical to the pre-Rung-D scene). Absent ⇒
  // treat as leg 0. Field name matches the Python `leg_index` key (#440 parity).
  leg_index?: number;
}

export interface TimelineData {
  segments: SegmentData[];
  total_s: number;
}

export interface Readouts {
  min_gap_m: number | null;
  min_wing_over_tail_clearance_m: number | null;
}

// ── #666 multi-solution compare container ────────────────────────────────────
// A viewer-HTML-level wrapper layered OVER N independent scene/v2 docs (read from a
// separate `<script id="solutions">` blob, NOT from `#scene`). Deliberately NOT part
// of SceneV2 — so `scene.build_scene` and the scene-contract key-parity guard stay
// untouched and each carried scene's bytes are byte-identical to a standalone render.

/** Per-solution compare metrics (Python-computed; the same numbers `solve` narrates). */
export interface SolutionSummary {
  min_gap_m: number | null; // tightest inter-plane gap (m); null for <2 planes
  planes_moved_vs_first: number; // planes shifted vs solution #1 (0 for #1)
  mean_shift_m: number; // mean (x,y) shift vs solution #1 (0 for #1)
  routable: boolean; // a tow plan was BUILT for this layout (always false under --no-animate)
}

export interface CompareSolution {
  label: string; // e.g. "#1"
  scene: SceneV2; // a full, self-contained scene/v2 doc
  summary: SolutionSummary;
}

/** The `#solutions` blob: N alternatives + the found/requested counts (#666). */
export interface CompareManifest {
  schema: string; // always "hangarfit.viewer-compare/v1"
  count_requested: number; // the --alternatives N the user asked for
  count_found: number; // how many diverse solutions were actually found
  solutions: CompareSolution[];
}

export interface SceneV2 {
  schema: string; // always "hangarfit.scene/v2"
  units: string; // always "m"
  coordinate_note: string; // human reminder of the coordinate convention
  hangar: HangarData;
  planes: PlaneData[];
  /** Placed ground objects (#606): fixed obstacles + movers. Empty when none. */
  ground_objects: GroundObjectData[];
  conflicts: string[];
  /** Final parked pose per plane id. */
  final_poses: Record<string, Affine>;
  /** Oracle world box corners per plane: [box][corner][x|y]. */
  anchors: Record<string, number[][][]>;
  /** Oracle world wheel positions per plane: [wheel][x|y]. */
  gear_anchors: Record<string, number[][]>;
  /** Oracle world box corners per ground object: [box][corner][x|y]. Empty when none. */
  go_anchors: Record<string, number[][][]>;
  /** Hard-door mover drive-out corridors (#652): sampled [x, y] world points per
   * mover id, for the egress-lane decal. Empty when no hard-door egress lane. */
  egress_lanes: Record<string, [number, number][]>;
  timeline: TimelineData;
  placeholder: boolean; // always emitted (true iff any placed aircraft is unmeasured)
  readouts: Readouts | null; // always emitted; null when the layout is invalid
}
