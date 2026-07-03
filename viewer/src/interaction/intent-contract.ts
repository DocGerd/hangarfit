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
}
