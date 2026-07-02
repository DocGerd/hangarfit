// viewer/src/interaction/selection.ts
import type { Intent, MustPosition, EditorContext } from './intent-contract.ts';

export function initialIntent(ctx: EditorContext): Intent {
  return { selectedPlaneIds: Object.keys(ctx.currentPoses).sort(), priorities: {}, mustPositions: {} };
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
  if (selected) { delete priorities[id]; delete mustPositions[id]; } // can't constrain an absent plane
  return { selectedPlaneIds, priorities, mustPositions };
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
