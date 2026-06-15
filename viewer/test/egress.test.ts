// node --test units for the 3D egress-lane builder (#652). Mirrors paths.test.ts:
// the impure THREE builder is exercised directly (line count, skip rule, colour,
// dashed material). addEgressLanes reads only SCENE.egress_lanes and BRAND.egressLane,
// so a partial cast keeps the fixture honest about its true dependencies.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { addEgressLanes } from '../src/egress.ts';
import type { BrandTokens } from '../src/brand-contract.ts';
import type { SceneV2 } from '../src/scene-contract.ts';

const BRAND = { egressLane: '#D6A23E' } as unknown as BrandTokens;

function sceneWithLanes(egress_lanes: Record<string, [number, number][]>): SceneV2 {
  return { egress_lanes } as unknown as SceneV2;
}

test('addEgressLanes: one dashed amber line per corridor; single-point corridor skipped', () => {
  const SCENE = sceneWithLanes({
    zeta: [
      [0, 0],
      [1, 1],
      [2, 2],
    ],
    alpha: [
      [5, 0],
      [5, 4],
    ],
    solo: [[9, 9]], // single point → not a line, skipped
  });
  const scene = new THREE.Scene();
  const { lines } = addEgressLanes(scene, SCENE, BRAND);

  assert.equal(lines.length, 2, 'two ≥2-point corridors get a line; the single-point one is skipped');
  assert.equal(scene.children.length, 2, 'each line is added to the scene');

  const mat = lines[0].material as THREE.LineDashedMaterial;
  assert.ok(mat instanceof THREE.LineDashedMaterial, 'keep-clear lane uses a dashed material');
  assert.equal('#' + mat.color.getHexString(), BRAND.egressLane.toLowerCase(), 'lane uses BRAND.egressLane');
  // computeLineDistances() must have run, else LineDashedMaterial renders solid.
  assert.ok(lines[0].geometry.getAttribute('lineDistance'), 'lineDistance attribute present (dashes render)');
});

test('addEgressLanes: empty egress_lanes builds nothing (inert)', () => {
  const scene = new THREE.Scene();
  const { lines } = addEgressLanes(scene, sceneWithLanes({}), BRAND);
  assert.equal(lines.length, 0);
  assert.equal(scene.children.length, 0);
});
