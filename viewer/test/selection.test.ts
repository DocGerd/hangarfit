import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  initialIntent, isSelected, toggleSelection, setPriority,
  pinAtCurrent, unpin, setPinField, setOnCarts,
  addToDoorOrder, removeFromDoorOrder, moveInDoorOrder,
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

// --- #907 door-proximity ranking (door_order) ---------------------------------

test('initialIntent starts with an empty doorOrder', () => {
  assert.deepEqual(initialIntent(CTX).doorOrder, []);
});

test('addToDoorOrder appends selected, unranked planes in call order', () => {
  let i = addToDoorOrder(initialIntent(CTX), 'husky');
  assert.deepEqual(i.doorOrder, ['husky']);
  i = addToDoorOrder(i, 'ctsl');
  assert.deepEqual(i.doorOrder, ['husky', 'ctsl']); // #1 nearest the door = first
});

test('addToDoorOrder never duplicates an already-ranked plane', () => {
  let i = addToDoorOrder(initialIntent(CTX), 'husky');
  i = addToDoorOrder(i, 'husky');
  assert.deepEqual(i.doorOrder, ['husky']);
});

test('addToDoorOrder ignores an unselected plane', () => {
  let i = toggleSelection(initialIntent(CTX), 'husky'); // drop husky from the set
  i = addToDoorOrder(i, 'husky');
  assert.deepEqual(i.doorOrder, []); // can't rank a plane that isn't in the layout
});

test('removeFromDoorOrder drops one plane, keeping the rest ordered', () => {
  let i = addToDoorOrder(initialIntent(CTX), 'husky');
  i = addToDoorOrder(i, 'ctsl');
  i = removeFromDoorOrder(i, 'husky');
  assert.deepEqual(i.doorOrder, ['ctsl']);
});

test('moveInDoorOrder reorders by index', () => {
  let i = addToDoorOrder(initialIntent(CTX), 'husky');
  i = addToDoorOrder(i, 'ctsl'); // [husky, ctsl]
  i = moveInDoorOrder(i, 1, 0);  // pull ctsl to #1
  assert.deepEqual(i.doorOrder, ['ctsl', 'husky']);
});

test('moveInDoorOrder: forward moves on a 3-element order splice correctly', () => {
  // A 3-plane context: the remove-then-insert splice seam (a forward move
  // inserts AFTER the removal shifts the array) only shows up with ≥3 items.
  const CTX3: EditorContext = {
    fleet: 'f', hangar: 'h', maintenance: null,
    currentPoses: {
      a: { x_m: 0, y_m: 0, heading_deg: 0, on_carts: false },
      b: { x_m: 1, y_m: 1, heading_deg: 0, on_carts: false },
      c: { x_m: 2, y_m: 2, heading_deg: 0, on_carts: false },
    },
    cartEligible: { a: false, b: false, c: false },
  };
  let i = initialIntent(CTX3);
  i = addToDoorOrder(i, 'a');
  i = addToDoorOrder(i, 'b');
  i = addToDoorOrder(i, 'c'); // [a, b, c]
  assert.deepEqual(moveInDoorOrder(i, 0, 2).doorOrder, ['b', 'c', 'a']); // a → end (forward)
  assert.deepEqual(moveInDoorOrder(i, 0, 1).doorOrder, ['b', 'a', 'c']); // a → one forward
  assert.deepEqual(moveInDoorOrder(i, 2, 0).doorOrder, ['c', 'a', 'b']); // c → front (backward)
});

test('moveInDoorOrder is a no-op for out-of-range or identical indices', () => {
  const i = addToDoorOrder(initialIntent(CTX), 'husky');
  assert.deepEqual(moveInDoorOrder(i, 0, 5).doorOrder, ['husky']);
  assert.deepEqual(moveInDoorOrder(i, -1, 0).doorOrder, ['husky']);
  assert.deepEqual(moveInDoorOrder(i, 0, 0).doorOrder, ['husky']);
});

test('deselecting a ranked plane also un-ranks it', () => {
  let i = addToDoorOrder(initialIntent(CTX), 'husky');
  i = toggleSelection(i, 'husky');
  assert.deepEqual(i.doorOrder, []);
});

test('reselecting a deselected plane does not restore its rank', () => {
  let i = addToDoorOrder(initialIntent(CTX), 'husky'); // [husky]
  i = toggleSelection(i, 'husky');                     // deselect → un-ranks
  i = toggleSelection(i, 'husky');                     // reselect
  assert.ok(isSelected(i, 'husky'));
  assert.deepEqual(i.doorOrder, []); // rank is NOT restored — the user must re-rank
});

test('door-order ops do not mutate their input', () => {
  const base = addToDoorOrder(initialIntent(CTX), 'husky');
  const snapshot = JSON.stringify(base);
  addToDoorOrder(base, 'ctsl');
  removeFromDoorOrder(base, 'husky');
  moveInDoorOrder(base, 0, 0);
  assert.equal(JSON.stringify(base), snapshot);
});
