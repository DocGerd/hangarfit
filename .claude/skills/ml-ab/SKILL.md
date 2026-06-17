---
name: ml-ab
description: This skill should be used when the user invokes "/ml-ab" to run the #607 4c-ii basin-escape A/B — a control (no knobs) vs treatment (all four default-neutral knobs) training run from ml/README.md, side by side, and summarise the train-time trajectory difference. Accepts optional schedule=, iters=, seed=, rollout-len=, out= arguments. Long-running (trains two policies); torch [train] extra required.
disable-model-invocation: true
argument-hint: "[schedule=curriculum|trivial] [iters=N] [seed=N] [rollout-len=N] [out=DIR]"
---

Run the #607 sub-project 4c-ii **basin-escape A/B**: a *control* run (no knobs — the default-neutral baseline) against a *treatment* run (all four basin-escape knobs at their README-recommended values), then summarise the train-time signals side by side. This wraps the A/B documented in `ml/README.md` so the #698 run-to-mastery work has a one-command starting point.

**Arguments from the invocation**: $ARGUMENTS

**This is a long-running command.** It trains two policies sequentially (minutes each at the smoke defaults; longer if you raise `iters`/`rollout-len`). Tell the user the expected shape before starting, and stream both runs' output to log files.

**This is a SMOKE A/B, not the run-to-mastery study.** `python -m ml.train` prints `mean_ep_reward`, `n_eps`, and promotions (plus `loss`/`entropy` in the *trivial* schedule only) — it does **not** print `valid_placed` / `terminal_fraction` / reach-rate. Those are eval-time metrics. So this skill demonstrates the knobs move the *train-time* signal in the expected direction; the **definitive** valid-rate / reach comparison is `python -m ml.eval` on saved checkpoints (deferred to #698). Say so in the summary; never claim this A/B proves mastery.

## Step 1 — Parse and validate arguments

Parse these named arguments from `$ARGUMENTS` (shell-style `key=value`, space-separated). All are optional; apply the default when absent. Unknown keys are an error — stop immediately and name the unrecognised key.

| Arg | Default | Valid values |
|-----|---------|--------------|
| `schedule` | `curriculum` | `curriculum` or `trivial` |
| `iters` | `30` | positive integer (curriculum → `--max-iters-per-stage`; trivial → `--iterations`) |
| `seed` | `0` | non-negative integer |
| `rollout-len` | `1024` | positive integer |
| `out` | `/tmp/ml-ab-<seed>` | a writable directory path (created if absent) |

Validation:
- `schedule` must be exactly `curriculum` or `trivial`.
- `iters`, `seed`, `rollout-len` must parse as integers in the stated range.
- If any argument is invalid, stop immediately and print a clear error naming the argument and expected format. Do NOT start training.

## Step 2 — Preconditions (stop on any failure)

1. **Run from the repo root.** Confirm the cwd contains `ml/`, `pyproject.toml`, and `data/` (the `ml/` package loads data via a repo-root-relative path and is not installed by the editable install). If not, stop and print: `Error: run /ml-ab from the hangarfit repo root (ml/ is a top-level, non-installed package).`
2. **torch must be importable.** Run `python -c 'import torch'`. If it fails, stop and print: `Error: ml.train needs the [train] extra. Install it with: pip install -e ".[train]"` — do NOT attempt the runs.
3. Create the `out` directory if it does not exist. If creation fails, stop and print the error verbatim.

## Step 3 — Build the two commands

Shared prefix (substitute the parsed values; `ITERFLAG` is `--max-iters-per-stage` for curriculum, `--iterations` for trivial):

```
python -m ml.train --schedule <schedule> ITERFLAG <iters> --seed <seed> --rollout-len <rollout-len>
```

- **Control** = the shared prefix, no knobs.
- **Treatment** = the shared prefix plus the four README-recommended knob values, verbatim:
  ```
  --r-valid-park 2.0 --dense-slot-potential \
  --entropy-start 0.05 --entropy-end 0.005 --entropy-anneal-iters 40 \
  --normalize-returns
  ```

Print both full commands to the user before running them, so the run is reproducible by hand.

## Step 4 — Run both, sequentially, teeing to logs

Run control first, then treatment (same seed → the only difference is the knobs):

```bash
<control command>   2>&1 | tee "<out>/control.log"
<treatment command> 2>&1 | tee "<out>/treatment.log"
```

If either run exits non-zero, capture it but continue to Step 5 with whatever was produced, and flag the failure in the summary (a crashed run is itself an A/B signal). Do not hide a non-zero exit.

## Step 5 — Extract and compare the train-time signals

The log format depends on the schedule (read it from the actual logs, do not assume):
- **curriculum** prints `[<stage>] iter N  mean_ep_reward=...  n_eps=...` per iteration and `[<stage>] promoted by competency|cap` per rung. The reachable signals are: the **final `mean_ep_reward`** per stage, **which stages were reached**, and **how each promotion happened** (`competency` is the goal; `cap` means the rung timed out without mastering).
- **trivial** prints `iter N  mean_ep_reward=...  loss=...  entropy=...`. The extra signal here is the **entropy trajectory** (first vs last) — with the treatment's `--entropy-start/--entropy-anneal-iters`, entropy should begin higher and decay.

Extract from each log (robust to schedule):
```bash
# final mean_ep_reward of each run
grep -oE 'mean_ep_reward=[-+0-9.]+' "<out>/control.log"   | tail -1
grep -oE 'mean_ep_reward=[-+0-9.]+' "<out>/treatment.log" | tail -1
# promotions (curriculum)
grep -E 'promoted by' "<out>/control.log";   grep -E 'promoted by' "<out>/treatment.log"
# entropy first/last (trivial)
grep -oE 'entropy=[0-9.]+' "<out>/control.log"   | sed -n '1p;$p'
grep -oE 'entropy=[0-9.]+' "<out>/treatment.log" | sed -n '1p;$p'
```

Present a side-by-side summary:

```
## /ml-ab summary — control vs treatment (<schedule>, iters=<iters>, seed=<seed>)

| signal                       | control            | treatment          |
|------------------------------|--------------------|--------------------|
| final mean_ep_reward         | <c>                | <t>                |
| promotions (competency/cap)  | <c>                | <t>                |   # curriculum
| entropy first → last         | <c>                | <t>                |   # trivial
| run exit status              | <c>                | <t>                |

Logs: <out>/control.log, <out>/treatment.log
```

## Step 6 — Verdict and honest caveat

State the read of the train-time signal, with the README's expectations:
- **Expected treatment fingerprint:** the place-nothing basin loosens — `mean_ep_reward` no worse than control and ideally climbing; (curriculum) promotions happening `by competency` rather than `by cap`; (trivial) entropy starting higher and decaying.
- **What this does NOT show:** `valid_placed` / reach-rate. Those require `python -m ml.eval --checkpoint <ckpt>` on a saved policy against the frozen benchmark — the definitive measure, deferred to #698. To produce checkpoints for that, re-run each side with `--save <out>/control.pt` / `--save <out>/treatment.pt` and then eval both.

End with one line stating whether the treatment moved the train-time signal in the expected direction at this smoke budget — and that mastery is explicitly out of scope for this skill.

## Constraints

- Never claim the A/B proves the knobs achieve mastery / a target valid-rate — it is a train-time smoke (the README says so).
- Never alter the four treatment knob values away from the README-recommended set; this skill's job is to reproduce that documented A/B, not invent a new sweep.
- Never modify any source under `ml/`, `src/`, or `tests/`; this skill only runs the trainer and reads logs.
- Always run both sides with the **same seed** — the knobs must be the only difference.
- If torch is absent or the cwd is not the repo root, stop in Step 2; do not start a partial run.
