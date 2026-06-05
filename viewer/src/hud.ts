// ── HUD wiring + render loop ─────────────────────────────────────────────────
import type * as THREE from 'three';
import { byId, disableControl } from './dom.ts';
import type { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { Timeline } from './timeline.ts';

export interface HudDeps {
  timeline: Timeline;
  home: () => void;
  controls: OrbitControls;
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  cam: THREE.PerspectiveCamera;
}

/** Wire the play/scrub/step/speed controls + resize, then start the render loop. */
export function startHud(deps: HudDeps): void {
  const { timeline, home, controls, renderer, scene, cam } = deps;
  const { total: TOTAL, hasAnim, segs: SEGS, applyTime } = timeline;

  let t = 0;
  let playing = false;
  let speed = 1;
  const scrub = byId<HTMLInputElement>('scrub');
  const playBtn = byId('play');
  const speedSel = byId<HTMLSelectElement>('speed');
  speedSel.addEventListener('change', () => { speed = parseFloat(speedSel.value); });

  if (!hasAnim) {
    ['play', 'prev', 'next', 'scrub', 'speed'].forEach(disableControl);
  }

  scrub.addEventListener('input', () => {
    t = (Number(scrub.value) / 1000) * TOTAL;
    playing = false;
    playBtn.textContent = '▶';
    applyTime(t);
  });
  playBtn.addEventListener('click', () => {
    if (!hasAnim) return;
    playing = !playing;
    playBtn.textContent = playing ? '❚❚' : '▶';
    if (t >= TOTAL) t = 0;
  });
  const stepTo = (dir: number): void => {
    const bounds = [0, ...SEGS.map((s) => s.end_s)];
    let i = bounds.findIndex((b) => b > t + 1e-6);
    if (i < 0) i = bounds.length - 1;
    t = dir > 0 ? bounds[Math.min(i, bounds.length - 1)] : bounds[Math.max(0, i - 2)];
    playing = false;
    playBtn.textContent = '▶';
    if (hasAnim) scrub.value = String((t / TOTAL) * 1000);
    applyTime(t);
  };
  byId('next').addEventListener('click', () => stepTo(1));
  byId('prev').addEventListener('click', () => stepTo(-1));
  byId('reset').addEventListener('click', home);

  window.addEventListener('resize', () => {
    cam.aspect = window.innerWidth / window.innerHeight;
    cam.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  // ── render loop ──────────────────────────────────────────────────────────
  let last = performance.now();
  const loop = (now: number): void => {
    requestAnimationFrame(loop);
    const dt = (now - last) / 1000;
    last = now;
    if (playing && hasAnim) {
      t += dt * speed;
      if (t >= TOTAL) {
        t = TOTAL;
        playing = false;
        playBtn.textContent = '▶';
      }
      scrub.value = String((t / TOTAL) * 1000);
    }
    applyTime(t);
    controls.update();
    renderer.render(scene, cam);
  };
  applyTime(0);
  requestAnimationFrame(loop);
}
