// ── HUD wiring + render loop ─────────────────────────────────────────────────
import type * as THREE from 'three';
import { byId, disableControl, enableControl } from './dom.ts';
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

/** A handle to re-point the HUD at a different solution's timeline (#666 compare).
 * Single-mode never calls it, so the single viewer is behaviour-identical. */
export interface HudHandle {
  setActiveTimeline: (timeline: Timeline) => void;
}

const ANIM_CONTROLS = ['play', 'prev', 'next', 'scrub', 'speed'];

/** Wire the play/scrub/step/speed controls + resize, then start the render loop.
 * The active timeline is mutable (swapped by `setActiveTimeline` in compare mode);
 * the render loop and listeners read it through `active`, so a switch costs no
 * re-binding. */
export function startHud(deps: HudDeps): HudHandle {
  const { home, controls, renderer, scene, cam } = deps;
  let active = deps.timeline;

  let t = 0;
  let playing = false;
  let speed = 1;
  const scrub = byId<HTMLInputElement>('scrub');
  const playBtn = byId('play');
  const speedSel = byId<HTMLSelectElement>('speed');
  speedSel.addEventListener('change', () => {
    speed = parseFloat(speedSel.value);
  });

  const applyAnimEnabled = (): void => {
    for (const id of ANIM_CONTROLS) (active.hasAnim ? enableControl : disableControl)(id);
  };
  applyAnimEnabled();

  scrub.addEventListener('input', () => {
    t = (Number(scrub.value) / 1000) * active.total;
    playing = false;
    playBtn.textContent = '▶';
    active.applyTime(t);
  });
  playBtn.addEventListener('click', () => {
    if (!active.hasAnim) return;
    playing = !playing;
    playBtn.textContent = playing ? '❚❚' : '▶';
    if (t >= active.total) t = 0;
  });
  const stepTo = (dir: number): void => {
    const bounds = [0, ...active.segs.map((s) => s.end_s)];
    let i = bounds.findIndex((b) => b > t + 1e-6);
    if (i < 0) i = bounds.length - 1;
    t = dir > 0 ? bounds[Math.min(i, bounds.length - 1)] : bounds[Math.max(0, i - 2)];
    playing = false;
    playBtn.textContent = '▶';
    if (active.hasAnim) scrub.value = String((t / active.total) * 1000);
    active.applyTime(t);
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
    if (playing && active.hasAnim) {
      t += dt * speed;
      if (t >= active.total) {
        t = active.total;
        playing = false;
        playBtn.textContent = '▶';
      }
      scrub.value = String((t / active.total) * 1000);
    }
    active.applyTime(t);
    controls.update();
    renderer.render(scene, cam);
  };
  active.applyTime(0);
  requestAnimationFrame(loop);

  return {
    setActiveTimeline(timeline: Timeline): void {
      // Switch solutions: re-point the loop, rewind to t=0, and re-sync the HUD
      // chrome (play glyph, scrubber, anim-enabled state) to the new timeline.
      active = timeline;
      t = 0;
      playing = false;
      playBtn.textContent = '▶';
      scrub.value = '0';
      applyAnimEnabled();
      active.applyTime(0);
    },
  };
}
