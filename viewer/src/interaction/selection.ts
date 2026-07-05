// viewer/src/interaction/selection.ts
import type { Intent, MustPosition, EditorContext } from './intent-contract.ts';

export function initialIntent(ctx: EditorContext): Intent {
  return {
    // Only aircraft are fleet_in members. currentPoses now also carries placed
    // movers (#912) so the drag gizmo can arm them — exclude those here, else a
    // mover would export into fleet_in (the loader rejects a non-aircraft there).
    selectedPlaneIds: Object.keys(ctx.currentPoses)
      .filter((id) => ctx.catalog?.[id]?.kind !== 'placed_routed_mover')
      .sort(),
    priorities: {},
    mustPositions: {},
    doorOrder: [],
    // Palette additions start empty — an untouched editor exports the same bytes
    // as before #910 (no `ground_objects` key). Even a layout that already
    // carries placed movers seeds this empty: the editor's intent surface owns
    // only what the user explicitly adds (consistent with the shipped behavior
    // where trailers render but are not captured by the export).
    groundObjectIds: [],
    // Cart-mode overrides start empty (#909) — no `movement_mode` is exported
    // until the user changes a plane's mode, so the byte path is unchanged.
    cartModeOverrides: {},
    // Mover pins start empty — an untouched editor exports byte-identically (#912).
    moverPins: {},
  };
}

export function isSelected(intent: Intent, id: string): boolean {
  return intent.selectedPlaneIds.includes(id);
}

export function toggleSelection(intent: Intent, id: string): Intent {
  const selected = isSelected(intent, id);
  const selectedPlaneIds = selected
    ? intent.selectedPlaneIds.filter((x) => x !== id)
    : [...intent.selectedPlaneIds, id].sort();
  const priorities = { ...intent.priorities };
  const mustPositions = { ...intent.mustPositions };
  // Deselecting drops the plane's constraints AND its door-proximity rank —
  // you can't rank a plane that isn't in the layout.
  const doorOrder = selected ? intent.doorOrder.filter((x) => x !== id) : intent.doorOrder;
  const cartModeOverrides = { ...intent.cartModeOverrides };
  if (selected) { delete priorities[id]; delete mustPositions[id]; delete cartModeOverrides[id]; }
  // This is a fresh literal (not a `{ ...intent }` spread), so groundObjectIds,
  // cartModeOverrides, and moverPins must be carried explicitly — a plane toggle
  // never touches the palette or mover pins, and deselecting drops the plane's
  // cart-mode override.
  return {
    selectedPlaneIds, priorities, mustPositions, doorOrder,
    groundObjectIds: intent.groundObjectIds, cartModeOverrides,
    moverPins: intent.moverPins,
  };
}

/** Set (or clear, with ``null``) a plane's cart-mode override (#909). The value
 * is a movement mode ("always_own_gear" | "cart_eligible" | "always_cart"); the
 * caller passes ``null`` when the user picks the plane's base mode, so an
 * unchanged mode exports no `movement_mode` key (byte path unchanged). */
export function setCartModeOverride(intent: Intent, id: string, mode: string | null): Intent {
  const cartModeOverrides = { ...intent.cartModeOverrides };
  if (mode === null) delete cartModeOverrides[id]; else cartModeOverrides[id] = mode;
  return { ...intent, cartModeOverrides };
}

export function setPriority(intent: Intent, id: string, priority: number | null): Intent {
  const priorities = { ...intent.priorities };
  if (priority === null) delete priorities[id]; else priorities[id] = priority;
  return { ...intent, priorities };
}

export function pinAtCurrent(intent: Intent, id: string, ctx: EditorContext): Intent {
  const c = ctx.currentPoses[id];
  if (!c) return intent;
  const mp: MustPosition = { x: c.x_m, y: c.y_m, heading: c.heading_deg, onCarts: c.on_carts };
  return { ...intent, mustPositions: { ...intent.mustPositions, [id]: mp } };
}

// #911 PR B: a sibling of pinAtCurrent that sources the pin from a Python-converted
// dragged pose (POST /convert) instead of currentPoses. onCarts is not part of the
// pose conversion — the caller carries it from the plane's existing pin or currentPose.
export function pinAtPose(
  intent: Intent,
  id: string,
  pose: { x_m: number; y_m: number; heading_deg: number },
  onCarts: boolean,
): Intent {
  const mp: MustPosition = { x: pose.x_m, y: pose.y_m, heading: pose.heading_deg, onCarts };
  return { ...intent, mustPositions: { ...intent.mustPositions, [id]: mp } };
}

// #912 PR B: a mover-pose sibling of pinAtPose. A placed_routed_mover dragged in
// the editor converts (POST /convert, Python-owned inverse) to a 3-field pose set
// here (no onCarts — movers never ride carts). Exported as a ground_objects
// mapping entry, distinct from the aircraft `mustPositions` pin map.
export function setMoverPin(
  intent: Intent,
  id: string,
  pose: { x: number; y: number; heading: number },
): Intent {
  return { ...intent, moverPins: { ...intent.moverPins, [id]: pose } };
}

export function unpin(intent: Intent, id: string): Intent {
  const mustPositions = { ...intent.mustPositions };
  delete mustPositions[id];
  return { ...intent, mustPositions };
}

export function setPinField(intent: Intent, id: string, field: 'x' | 'y' | 'heading', value: number): Intent {
  const cur = intent.mustPositions[id];
  if (!cur) return intent;
  return { ...intent, mustPositions: { ...intent.mustPositions, [id]: { ...cur, [field]: value } } };
}

export function setOnCarts(intent: Intent, id: string, onCarts: boolean): Intent {
  const cur = intent.mustPositions[id];
  if (!cur) return intent;
  return { ...intent, mustPositions: { ...intent.mustPositions, [id]: { ...cur, onCarts } } };
}

// --- Door-proximity ranking (#907 → Scenario.door_order) ----------------------

/** Append a selected, not-yet-ranked plane to the end of the door order (least
 * near the door). No-op for an unselected or already-ranked plane. */
export function addToDoorOrder(intent: Intent, id: string): Intent {
  if (!isSelected(intent, id) || intent.doorOrder.includes(id)) return intent;
  return { ...intent, doorOrder: [...intent.doorOrder, id] };
}

/** Remove a plane from the door order, preserving the order of the rest. */
export function removeFromDoorOrder(intent: Intent, id: string): Intent {
  if (!intent.doorOrder.includes(id)) return intent;
  return { ...intent, doorOrder: intent.doorOrder.filter((x) => x !== id) };
}

/** Move the plane at index ``from`` to index ``to`` in the door order. No-op for
 * out-of-range or identical indices — callers pass indices derived from existing
 * list positions (always in range), so the bounds guard is purely defensive. */
export function moveInDoorOrder(intent: Intent, from: number, to: number): Intent {
  const n = intent.doorOrder.length;
  if (from < 0 || from >= n || to < 0 || to >= n || from === to) return intent;
  const doorOrder = [...intent.doorOrder];
  const [item] = doorOrder.splice(from, 1);
  doorOrder.splice(to, 0, item);
  return { ...intent, doorOrder };
}

// --- Catalog palette: ground objects (#910 → Scenario.ground_objects) ---------

/** Toggle a ground object in/out of the palette additions. Adding keeps the list
 * sorted (order is irrelevant — the solver places movers — so a canonical order
 * keeps the export deterministic). The op is kind-agnostic; the editor gates
 * which kinds are offered and the export filters to movers via the catalog. */
export function toggleGroundObject(intent: Intent, id: string): Intent {
  const present = intent.groundObjectIds.includes(id);
  const groundObjectIds = present
    ? intent.groundObjectIds.filter((x) => x !== id)
    : [...intent.groundObjectIds, id].sort();
  return { ...intent, groundObjectIds };
}
