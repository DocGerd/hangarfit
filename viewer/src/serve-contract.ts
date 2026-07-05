// viewer/src/serve-contract.ts — the client side of the `hangarfit serve` seam (#445).
// A viewer-HTML-level marker blob (#serve-config), NOT part of scene/v2. Pure &
// node-tested; the DOM/fetch wiring lives in interaction/calculate.ts.
import type { SceneV2 } from './scene-contract.ts';
import type { EditorContext } from './interaction/intent-contract.ts';

export interface ServeConfig {
  schema: string;
}

// The `POST /solve` response: the solved scene plus a REFRESHED editor-context so
// the client can re-base "pin at current pose" on the new solved poses (the
// browser must never derive them — the forbidden determinant-−1 inversion,
// ADR-0002). The server (build_editor_context) is the sole author of both.
export interface SolveResponse {
  scene: SceneV2;
  editorContext: EditorContext;
}

/** Parse the injected `#serve-config` blob; null when absent (offline export). */
export function parseServeConfig(text: string | null | undefined): ServeConfig | null {
  if (!text) return null;
  return JSON.parse(text) as ServeConfig;
}

/** The fetch init for POST /solve — a raw Scenario YAML body. */
export function solveRequestInit(yaml: string): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/x-yaml' }, body: yaml };
}

// #911 drag-to-fix: POST /convert turns a dragged WORLD floor pose into a scenario
// pin. Python owns the determinant-−1 inverse (ADR-0002); the client never computes
// heading↔yaw. world_yaw_rad is read off the gizmo proxy's rotation.z.
export interface ConvertRequest { x: number; y: number; world_yaw_rad: number; }
export interface ConvertResponse { x_m: number; y_m: number; heading_deg: number; }
export function convertRequestInit(req: ConvertRequest): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req) };
}
