// viewer/src/interaction/intent-contract.ts
export interface MustPosition { x: number; y: number; heading: number; onCarts: boolean; }
export interface Intent {
  selectedPlaneIds: string[];
  priorities: Record<string, number>;
  mustPositions: Record<string, MustPosition>;
}
export interface CurrentPose { x_m: number; y_m: number; heading_deg: number; on_carts: boolean; }
export interface EditorContext {
  fleet: string;
  hangar: string;
  maintenance: { plane: string } | null;
  currentPoses: Record<string, CurrentPose>;
  cartEligible: Record<string, boolean>;
}
