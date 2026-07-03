// viewer/src/interaction/calculate.ts — the serve-mode "Calculate" control (#445).
// Dormant offline (main.ts only mounts it when a #serve-config blob is present).
// Never composes geometry: it POSTs the exported Scenario YAML and hands the
// Python-computed scene straight to the caller's re-render (ADR-0002).
import { banner, byId, clearBanner } from '../dom.ts';
import { intentToScenarioYaml } from './export.ts';
import { solveRequestInit } from '../serve-contract.ts';
import type { Intent, EditorContext } from './intent-contract.ts';
import type { SceneV2 } from '../scene-contract.ts';

export function mountCalculate(opts: {
  getIntent: () => Intent;
  ctx: EditorContext;
  reRender: (scene: SceneV2) => void;
}): void {
  const btn = document.createElement('button');
  btn.id = 'calculate';
  btn.type = 'button';
  btn.textContent = 'Calculate';
  const exportBtn = byId<HTMLButtonElement>('export');
  exportBtn.parentElement?.insertBefore(btn, exportBtn);

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
      opts.reRender((await resp.json()) as SceneV2);
    } catch (e) {
      banner('Calculate failed: ' + (e as Error).message);
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener('click', () => void run());
}
