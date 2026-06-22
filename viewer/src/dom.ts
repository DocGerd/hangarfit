// DOM helpers shared across the viewer modules.
//
// `banner()` is the load-bearing fail-loud edge (ADR-0017): the anchor
// self-check and the WebGL-unavailable path surface here, never via a thrown
// error that would blank the page. The banner text for the self-check is built
// by the caller (main.ts) so the comparison itself stays pure/DOM-free
// (node-tested in #440).

/**
 * `getElementById` narrowed to the requested element type. The viewer's HTML
 * (assembled by `viewer.py`) always carries these ids, so a missing one is a
 * real assembly bug — throw a clear error rather than propagate `null` (this
 * mirrors the original's implicit null-deref crash, with a legible message).
 * Not used inside `checkAnchors`, so it does not weaken the fail-loud contract.
 */
export function byId<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (el === null) throw new Error(`viewer: missing #${id} element`);
  return el as T;
}

/** Unhide the `#banner` and set its text. Never throws on a present banner. */
export function banner(msg: string): void {
  const b = byId('banner');
  b.hidden = false;
  b.textContent = msg;
}

/** Disable a HUD form control by id (button / range / select all carry the same
 * `.disabled` IDL attribute — typing as a button suffices to reach it). */
export function disableControl(id: string): void {
  byId<HTMLButtonElement>(id).disabled = true;
}

/** Re-enable a HUD form control by id (the inverse of `disableControl`; used when a
 * #666 solution switch lands on an animated solution after a static one). */
export function enableControl(id: string): void {
  byId<HTMLButtonElement>(id).disabled = false;
}
