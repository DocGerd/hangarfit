// viewer/src/interaction/calculate.ts — the serve-mode "Calculate" control (#445).
// Dormant offline (main.ts only mounts it when a #serve-config blob is present).
// Never composes geometry: it POSTs the exported Scenario YAML and hands the
// Python-computed scene straight to the caller's re-render (ADR-0002).
import { banner, byId, clearBanner } from '../dom.ts';
import { intentToScenarioYaml } from './export.ts';
import { solveRequestInit } from '../serve-contract.ts';
import type { SolveResponse } from '../serve-contract.ts';
import type { Intent, EditorContext } from './intent-contract.ts';

export function mountCalculate(opts: {
  getIntent: () => Intent;
  ctx: EditorContext;
  reRender: (resp: SolveResponse) => void;
}): { markUnsolved(): void } {
  const btn = document.createElement('button');
  btn.id = 'calculate';
  btn.type = 'button';
  btn.textContent = 'Calculate';
  const exportBtn = byId<HTMLButtonElement>('export');
  exportBtn.parentElement?.insertBefore(btn, exportBtn);

  // #911 PR B: a dragged-and-converted pin (see editor.ts's manipulator) changes
  // the intent without re-solving, so the last-rendered scene may no longer match
  // it. Surface that as a visible "unsolved" marker on the Calculate button itself
  // rather than a separate element — cleared once a solve actually reflects it.
  const markUnsolved = (): void => {
    btn.classList.add('unsolved');
    btn.textContent = 'Calculate ●';
  };
  const clearUnsolved = (): void => {
    btn.classList.remove('unsolved');
    btn.textContent = 'Calculate';
  };

  async function run(): Promise<void> {
    btn.disabled = true;
    clearBanner();
    try {
      const yaml = intentToScenarioYaml(opts.getIntent(), opts.ctx);
      const resp = await fetch('/solve', solveRequestInit(yaml));
      if (!resp.ok) {
        let msg = `${resp.status}`;
        try {
          msg = (JSON.parse(await resp.text()) as { error?: string }).error ?? msg;
        } catch {
          /* non-JSON body: keep the status code */
        }
        banner('Calculate failed: ' + msg);
        return;
      }
      opts.reRender((await resp.json()) as SolveResponse);
      clearUnsolved(); // a successful solve reflects the current intent
    } catch (e) {
      banner('Calculate failed: ' + (e as Error).message);
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener('click', () => void run());
  return { markUnsolved };
}
