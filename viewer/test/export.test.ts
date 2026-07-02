import { test } from 'node:test';
import assert from 'node:assert/strict';
import { intentToScenarioYaml } from '../src/interaction/export.ts';
import { initialIntent, setPriority, pinAtCurrent, toggleSelection } from '../src/interaction/selection.ts';
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

test('export has the scenario_with_pin shape (pin + priority)', () => {
  let i = initialIntent(CTX);
  i = pinAtCurrent(i, 'husky', CTX);
  i = setPriority(i, 'ctsl', 3);
  const y = intentToScenarioYaml(i, CTX);
  assert.match(y, /^fleet: data\/fleet\.yaml$/m);
  assert.match(y, /^hangar: data\/hangar\.yaml$/m);
  assert.match(y, /^fleet_in: \[ctsl, fuji, husky\]$/m); // maintenance plane fuji unioned in
  assert.match(y, /^maintenance:\n {2}plane: fuji$/m);
  assert.match(y, /^ {2}husky:\n {4}pin: \{ x_m: 2.1, y_m: 14.3, heading_deg: 0.0, on_carts: false \}$/m);
  assert.match(y, /^ {2}ctsl:\n {4}priority: 3.0$/m);
});

test('a selected plane with neither pin nor priority emits no constraints entry', () => {
  const y = intentToScenarioYaml(initialIntent(CTX), CTX);
  assert.doesNotMatch(y, /constraints:/); // nobody constrained
});

test('deselected planes never appear', () => {
  let i = toggleSelection(initialIntent(CTX), 'ctsl'); // drop ctsl
  i = pinAtCurrent(i, 'husky', CTX);
  const y = intentToScenarioYaml(i, CTX);
  assert.match(y, /^fleet_in: \[fuji, husky\]$/m); // fuji (maintenance) always present; ctsl dropped
  assert.doesNotMatch(y, /ctsl/);
});

test('no maintenance block when ctx.maintenance is null', () => {
  const y = intentToScenarioYaml(initialIntent(CTX), { ...CTX, maintenance: null });
  assert.doesNotMatch(y, /maintenance:/);
});

test('a plane can carry both a pin and a priority', () => {
  let i = initialIntent(CTX);
  i = pinAtCurrent(i, 'husky', CTX);
  i = setPriority(i, 'husky', 2);
  const y = intentToScenarioYaml(i, CTX);
  // both sub-keys appear under the same constraint entry, pin before priority
  assert.match(
    y,
    /^ {2}husky:\n {4}pin: \{ x_m: 2.1, y_m: 14.3, heading_deg: 0.0, on_carts: false \}\n {4}priority: 2.0$/m,
  );
});
