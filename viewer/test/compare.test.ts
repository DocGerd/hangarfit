import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  wrapIndex,
  clampIndex,
  optionLabels,
  formatSummary,
  foundLabel,
} from '../src/compare.ts';
import type { CompareManifest, CompareSolution, SolutionSummary } from '../src/scene-contract.ts';

function sol(label: string, s: Partial<SolutionSummary>): CompareSolution {
  return {
    label,
    // The scene body is irrelevant to the pure compare logic; a minimal stub suffices.
    scene: { schema: 'hangarfit.scene/v2' } as CompareSolution['scene'],
    summary: {
      min_gap_m: 1.0,
      planes_moved_vs_first: 0,
      mean_shift_m: 0.0,
      routable: true,
      ...s,
    },
  };
}

test('wrapIndex: steps forward and wraps past the end', () => {
  assert.equal(wrapIndex(0, 3, 1), 1);
  assert.equal(wrapIndex(2, 3, 1), 0); // wraps to first
});

test('wrapIndex: steps backward and wraps before the start', () => {
  assert.equal(wrapIndex(1, 3, -1), 0);
  assert.equal(wrapIndex(0, 3, -1), 2); // wraps to last
});

test('wrapIndex: single solution stays put', () => {
  assert.equal(wrapIndex(0, 1, 1), 0);
  assert.equal(wrapIndex(0, 1, -1), 0);
});

test('clampIndex: bounds an out-of-range or non-finite index', () => {
  assert.equal(clampIndex(5, 3), 2);
  assert.equal(clampIndex(-1, 3), 0);
  assert.equal(clampIndex(1, 3), 1);
  assert.equal(clampIndex(NaN, 3), 0);
  assert.equal(clampIndex(1.9, 3), 1); // truncates
});

test('optionLabels: one label per solution with its gap', () => {
  const labels = optionLabels([sol('#1', { min_gap_m: 1.23 }), sol('#2', { min_gap_m: 0.9 })]);
  assert.deepEqual(labels, ['#1 — gap 1.23 m', '#2 — gap 0.90 m']);
});

test('optionLabels: a single-plane null gap shows n/a', () => {
  assert.deepEqual(optionLabels([sol('#1', { min_gap_m: null })]), ['#1 — gap n/a']);
});

test('formatSummary: solution #1 has no moved-vs-#1 term', () => {
  const out = formatSummary(sol('#1', { min_gap_m: 1.23, planes_moved_vs_first: 0, routable: true }));
  assert.equal(out, 'min gap 1.23 m · tow-routable');
});

test('formatSummary: a later solution adds the diversity delta and routability', () => {
  const out = formatSummary(
    sol('#2', { min_gap_m: 0.98, planes_moved_vs_first: 3, mean_shift_m: 2.14, routable: false }),
  );
  assert.equal(out, 'min gap 0.98 m · 3 moved vs #1 (avg 2.1 m) · not tow-routable');
});

test('foundLabel: partial result mirrors solve "Found n of N"', () => {
  const m: CompareManifest = {
    schema: 'hangarfit.viewer-compare/v1',
    count_requested: 3,
    count_found: 2,
    solutions: [sol('#1', {}), sol('#2', {})],
  };
  assert.equal(foundLabel(m), 'Found 2 of 3 requested');
});

test('foundLabel: full result is a plain pluralized count', () => {
  const base = { schema: 'hangarfit.viewer-compare/v1', solutions: [] as CompareSolution[] };
  assert.equal(foundLabel({ ...base, count_requested: 2, count_found: 2 }), '2 solutions');
  assert.equal(foundLabel({ ...base, count_requested: 1, count_found: 1 }), '1 solution');
});
