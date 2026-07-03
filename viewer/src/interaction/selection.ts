// viewer/src/interaction/selection.ts
import type { Intent, MustPosition, EditorContext } from './intent-contract.ts';

export function initialIntent(ctx: EditorContext): Intent {
  return {
    selectedPlaneIds: Object.keys(ctx.currentPoses).sort(),
    priorities: {},
    mustPositions: {},
    doorOrder: [],
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
  if (selected) { delete priorities[id]; delete mustPositions[id]; }
  return { selectedPlaneIds, priorities, mustPositions, doorOrder };
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
