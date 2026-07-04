import { test } from 'node:test';
import assert from 'node:assert/strict';
import { focusAwareHex, EXCLUDED_EMISSIVE, FOCUS_EMISSIVE } from '../src/interaction/highlight.ts';

const ORIG = 0x0a0a0a; // a representative base emissive (often near-black on plane materials)

test('focus takes visual precedence over membership', () => {
  assert.equal(focusAwareHex(true, true, ORIG), FOCUS_EMISSIVE); // focused member
  assert.equal(focusAwareHex(false, true, ORIG), FOCUS_EMISSIVE); // focused non-member
});

test('unfocused member shows its original emissive', () => {
  assert.equal(focusAwareHex(true, false, ORIG), ORIG);
});

test('unfocused non-member shows the excluded amber', () => {
  assert.equal(focusAwareHex(false, false, ORIG), EXCLUDED_EMISSIVE);
});

test('excluded amber constant is the historical hue (byte-compatible with the old inline literal)', () => {
  assert.equal(EXCLUDED_EMISSIVE, 0x552200);
});
