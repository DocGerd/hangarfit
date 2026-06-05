// Typed mirror of the `hangarfit.scene/v1` contract (Python `scene.py`).
//
// This is the EXTENSION SEAM (ADR-0020): an additive scene field becomes an
// additive interface field here. It is the schema-faithful mirror of
// `docs/architecture/scene-v1-schema.md`; a Python key-set parity test in
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
}

export interface PlaneData {
  id: string;
  color: string; // '#RRGGBB'
  boxes: BoxData[];
  wheels: [number, number][]; // plane-local wheel (u,v) positions (ADR-0013)
  on_carts: boolean;
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

export interface HangarData {
  width_m: number;
  length_m: number;
  door: DoorData;
  maintenance_bay: MaintenanceBay; // always emitted (a dict; `closed` distinguishes occupied)
}

export interface SegmentData {
  plane_id: string;
  start_s: number;
  end_s: number;
  samples: Affine[];
}

export interface TimelineData {
  segments: SegmentData[];
  total_s: number;
}

export interface Readouts {
  min_gap_m: number | null;
  min_wing_over_tail_clearance_m: number | null;
}

export interface SceneV1 {
  schema: string; // always "hangarfit.scene/v1"
  units: string; // always "m"
  coordinate_note: string; // human reminder of the coordinate convention
  hangar: HangarData;
  planes: PlaneData[];
  conflicts: string[];
  /** Final parked pose per plane id. */
  final_poses: Record<string, Affine>;
  /** Oracle world box corners per plane: [box][corner][x|y]. */
  anchors: Record<string, number[][][]>;
  /** Oracle world wheel positions per plane: [wheel][x|y]. */
  gear_anchors: Record<string, number[][]>;
  timeline: TimelineData;
  placeholder: boolean; // always emitted (true iff any placed aircraft is unmeasured)
  readouts: Readouts | null; // always emitted; null when the layout is invalid
}
