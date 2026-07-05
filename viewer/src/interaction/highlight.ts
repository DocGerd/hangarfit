// viewer/src/interaction/highlight.ts — the pure emissive-hue decision for the
// editor's plane highlight (#904). Three-way precedence over the SINGLE emissive
// channel: a focused plane shows FOCUS_EMISSIVE regardless of membership (so
// "which plane am I editing" reads independently of "is it in the fleet"); an
// unfocused plane shows its membership hue (original emissive if selected, amber
// EXCLUDED_EMISSIVE if not). Pure numbers-in/number-out: NO three/DOM import, so
// it is unit-tested in isolation without the untested raycaster path. The focused
// hue doubles as #911's TransformControls gizmo-target indicator.

/** Amber "excluded from the fleet" emissive (unchanged from editor.ts's old inline literal). */
export const EXCLUDED_EMISSIVE = 0x552200;

/** Blue "currently focused for editing" emissive — deliberately distinct from the amber excluded hue. */
export const FOCUS_EMISSIVE = 0x2266ff;

/**
 * The emissive hex a plane's highlight should show.
 * @param selected whether the plane is in the fleet (`isSelected(intent, id)`)
 * @param focused  whether the plane is the one being edited (`id === focusedId`)
 * @param orig     the plane's original (pre-highlight) emissive hex
 */
export function focusAwareHex(selected: boolean, focused: boolean, orig: number): number {
  if (focused) return FOCUS_EMISSIVE;
  return selected ? orig : EXCLUDED_EMISSIVE;
}
