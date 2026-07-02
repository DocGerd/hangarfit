import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  initialIntent, isSelected, toggleSelection, setPriority,
  pinAtCurrent, unpin, setPinField, setOnCarts,
} from '../src/interaction/selection.ts';
import type { EditorContext } from '../src/interaction/intent-contract.ts';

const CTX: EditorContext = {
  fleet: 'data/fleet.yaml', hangar: 'data/hangar.yaml',
  maintenance: { plane: 'fuji' },
  currentPoses: {
    husky: { x_m: 2.1, y_m: 14.3, heading_deg: 0, on_carts: false },
    ctsl: { x_m: 5.0, y_m: 3.0, heading_deg: 90, on_carts: false },
  },
  cartEligible: { husky: false, ctsl: false },
};

test('initialIntent selects all rendered planes, no constraints', () => {
  const i = initialIntent(CTX);
  assert.deepEqual(i.selectedPlaneIds, ['ctsl', 'husky']); // sorted
  assert.deepEqual(i.priorities, {});
  assert.deepEqual(i.mustPositions, {});
});

test('deselect drops the plane and its constraints', () => {
  let i = initialIntent(CTX);
  i = setPriority(i, 'husky', 2);
  i = pinAtCurrent(i, 'husky', CTX);
  i = toggleSelection(i, 'husky');
  assert.ok(!isSelected(i, 'husky'));
  assert.equal(i.priorities.husky, undefined);
  assert.equal(i.mustPositions.husky, undefined);
});

test('reselect adds back with no constraints', () => {
  let i = toggleSelection(initialIntent(CTX), 'husky'); // drop
  i = toggleSelection(i, 'husky');                      // add back
  assert.ok(isSelected(i, 'husky'));
  assert.equal(i.priorities.husky, undefined);
});

test('pinAtCurrent copies the Python-emitted scalar pose', () => {
  const i = pinAtCurrent(initialIntent(CTX), 'husky', CTX);
  assert.deepEqual(i.mustPositions.husky, { x: 2.1, y: 14.3, heading: 0, onCarts: false });
});

test('unpin removes a pin', () => {
  let i = pinAtCurrent(initialIntent(CTX), 'husky', CTX);
  i = unpin(i, 'husky');
  assert.equal(i.mustPositions.husky, undefined);
});

test('setPinField edits only a pinned plane', () => {
  let i = pinAtCurrent(initialIntent(CTX), 'ctsl', CTX);
  i = setPinField(i, 'ctsl', 'x', 6.5);
  assert.equal(i.mustPositions.ctsl.x, 6.5);
  const j = setPinField(initialIntent(CTX), 'ctsl', 'x', 6.5); // not pinned → no-op
  assert.equal(j.mustPositions.ctsl, undefined);
});

test('setOnCarts edits only a pinned plane', () => {
  let i = pinAtCurrent(initialIntent(CTX), 'husky', CTX);
  i = setOnCarts(i, 'husky', true);
  assert.equal(i.mustPositions.husky.onCarts, true);
  const j = setOnCarts(initialIntent(CTX), 'husky', true); // not pinned → no-op
  assert.equal(j.mustPositions.husky, undefined);
});

test('setPriority(null) clears', () => {
  let i = setPriority(initialIntent(CTX), 'husky', 4);
  i = setPriority(i, 'husky', null);
  assert.equal(i.priorities.husky, undefined);
});

test('functions are pure (no input mutation)', () => {
  const i0 = initialIntent(CTX);
  const snapshot = JSON.stringify(i0);
  toggleSelection(i0, 'husky');
  setPriority(i0, 'husky', 9);
  pinAtCurrent(i0, 'husky', CTX);
  assert.equal(JSON.stringify(i0), snapshot);
});

test('pin-editing functions do not mutate their input', () => {
  const pinned = pinAtCurrent(initialIntent(CTX), 'husky', CTX);
  const snapshot = JSON.stringify(pinned);
  unpin(pinned, 'husky');
  setPinField(pinned, 'husky', 'x', 99);
  setOnCarts(pinned, 'husky', true);
  assert.equal(JSON.stringify(pinned), snapshot);
});
