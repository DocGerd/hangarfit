// viewer/src/serve-contract.ts — the client side of the `hangarfit serve` seam (#445).
// A viewer-HTML-level marker blob (#serve-config), NOT part of scene/v2. Pure &
// node-tested; the DOM/fetch wiring lives in interaction/calculate.ts.
export interface ServeConfig {
  schema: string;
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
