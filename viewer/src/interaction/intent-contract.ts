// viewer/src/interaction/intent-contract.ts
export interface MustPosition { x: number; y: number; heading: number; onCarts: boolean; }
export interface Intent {
  selectedPlaneIds: string[];
  priorities: Record<string, number>;
  mustPositions: Record<string, MustPosition>;
  // Door-proximity ranking (#907 → Scenario.door_order, #614): an ordered,
  // exclusive/partial subset of the selected planes, #1 (index 0) nearest the
  // door. Empty ⇒ no `door_order` key is exported (byte path unchanged).
  doorOrder: string[];
  // Catalog-palette ground objects added from an empty hangar (#910 → Scenario
  // .ground_objects bare-id entries). Only placed_routed_mover ids are
  // offline-exportable (a fixed_obstacle needs an authored pose the offline
  // editor can't produce); the export filters via the catalog. Empty ⇒ no
  // `ground_objects` key is exported (byte path unchanged).
  groundObjectIds: string[];
}
export interface CurrentPose { x_m: number; y_m: number; heading_deg: number; on_carts: boolean; }
export interface EditorContext {
  fleet: string;
  hangar: string;
  maintenance: { plane: string } | null;
  currentPoses: Record<string, CurrentPose>;
  cartEligible: Record<string, boolean>;
  // The door edge (scene/v2 hangar geometry), rendered as a hint in the ranking
  // panel so the user sees which wall the door is on and where along it. Absent
  // on older editor-context blobs.
  door?: { center_x_m: number; width_m: number };
  // The full catalog to build the "add from an empty hangar" palette (#910):
  // EVERY fleet aircraft (not just the placed ones) plus every ground object,
  // keyed by id. `kind` is "aircraft" for planes and the GroundObject
  // object_class ("fixed_obstacle" | "placed_routed_mover") for objects, which
  // gates what the palette can add offline (aircraft + movers, not fixed
  // obstacles). Absent on older editor-context blobs.
  catalog?: Record<string, { name: string; kind: string }>;
}
