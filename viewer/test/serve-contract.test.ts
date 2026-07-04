import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseServeConfig, solveRequestInit } from '../src/serve-contract.ts';

test('parseServeConfig returns null when the blob is absent', () => {
  assert.equal(parseServeConfig(null), null);
  assert.equal(parseServeConfig(undefined), null);
  assert.equal(parseServeConfig(''), null);
});

test('parseServeConfig parses a present blob', () => {
  const cfg = parseServeConfig('{"schema":"hangarfit.serve-config/v1"}');
  assert.equal(cfg?.schema, 'hangarfit.serve-config/v1');
});

test('solveRequestInit posts the yaml body', () => {
  const init = solveRequestInit('fleet_in: [a]\n');
  assert.equal(init.method, 'POST');
  assert.equal(init.body, 'fleet_in: [a]\n');
  assert.match(String((init.headers as Record<string, string>)['Content-Type']), /yaml/);
});
