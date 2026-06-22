# Spike #757 — single-process numpy-vectorized envs vs `SubprocVectorEnv`

**Wave 3, epic #760 ("use more resources").** Status: **NO-GO** (measured).

## The question

The incumbent vectorized-training path (`ml/vector_env.py`) is `SubprocVectorEnv`: N spawn
worker processes, each holding its own `HangarFitEnv` + shapely/torch import, exchanging encoded
observations with the learner over a pickle pipe. It parallelizes the per-env shapely geometry
across cores, at the cost of per-worker RAM (private torch + shapely pages) and pickle IPC.

#757 asks the obvious "use more resources" alternative: step **all N envs in ONE process** with
the geometry **numpy-batched** across an `(N, parts, verts)` tensor (SIMD over envs instead of
process-level parallelism). The open risk the team-lead flagged: the irreducible GEOS `Polygon()`
construction + GEOS predicates — the actual #381 bottleneck — may be **un-batchable**, defeating
the SIMD win. A NO-GO that documents *why* subproc wins is itself the deliverable.

## Methodology

- Throwaway harness `bench/singleproc_numpy_vec.py` (does **not** touch the shipped
  `SubprocVectorEnv`/`SyncVectorEnv`). Three backends, all on the **`trio-box`** curriculum rung
  (3 objects; every part is an oriented rectangle — `data/catalog/*.yaml` carry no
  `local_vertices` — so the geometry *can* be pure-numpy SAT, the best case for the question):
  - `sync` — the real `SyncVectorEnv` of real `_EnvWorker`s (in-process GEOS reference).
  - `subproc` — the real `SubprocVectorEnv`, `spawn` (the incumbent).
  - `npvec` — the prototype: N envs in one process, batching the **affine transform**, the
    **swept-clearance SAT** (over the `(N, poses, parts)` cross-product), and the **raster**
    (numpy point-in-rect) across all envs.
- **Fixed action stream** (seeded random, ~1/9 Park — matches the measured ~11% park fraction),
  identical across backends. **No policy forward** — we isolate env-stepping throughput.
- Binds on a fixed step COUNT (`steps × n_envs` transitions), like the rest of `bench/`. Peak RAM
  = `ru_maxrss` of SELF (+ CHILDREN for `subproc`). Each (backend, N) cell runs in its **own fresh
  process** so the high-water `ru_maxrss` is per-cell.
- The `npvec` prototype reproduces the env-step's geometry **cost profile**, not bit-exact
  validity (it uses an AABB-overlap proxy for the oriented-rect SAT area; the exact oriented-rect
  GEOS-equivalence is the sibling #735 Lever-B spike's job). For a *throughput* verdict that is
  sufficient — we are sizing whether SIMD-batched box geometry can dominate the GEOS cost.

Reproduce (from a checkout with the `ml` package on path; `subproc` needs the `[train]` extra):

```bash
python -m bench.singleproc_numpy_vec --backend npvec   --rung trio-box --n-envs 8 --steps 800
python -m bench.singleproc_numpy_vec --backend sync     --rung trio-box --n-envs 8 --steps 800
python -m bench.singleproc_numpy_vec --backend subproc  --rung trio-box --n-envs 8 --steps 800
```

## Measured results

`trio-box`, 800 per-env steps, seed 0, on the dev box (32 logical cores, WSL2). Single run per
cell — these are order-of-magnitude figures, not a tight benchmark; the conclusion holds with a
wide margin so run-to-run jitter does not change the verdict.

| backend | N | transitions/sec | peak RSS (MiB) |
|---|---:|---:|---:|
| `npvec` (1 process, numpy-batched) | 4 | 42.9 | 48.8 |
| `npvec` | 8 | 41.9 | 45.6 |
| `npvec` | 16 | 43.3 | 47.3 |
| `sync` (1 process, GEOS — reference) | 4 | 65.8 | 44.7 |
| `sync` | 8 | 66.5 | 47.1 |
| `sync` | 16 | 66.0 | 51.7 |
| `subproc` (N spawn workers, GEOS — incumbent) | 4 | 142.0 | 1128.2 |
| `subproc` | 8 | 223.6 | 1135.6 |
| `subproc` | 16 | **315.2** | 1139.6 |

Read it as three regimes:

- **`subproc` is the only one that scales with N** — throughput rises 142 → 224 → 315 t/s as it
  spreads whole-env GEOS work across cores. It pays for it in RAM: ~1.1 GiB resident (16 spawn
  workers each privately importing torch + shapely), ~24× the single-process backends.
- **`sync` is flat at ~66 t/s** — one process, GEOS, single-threaded, no parallelism. This is the
  ceiling a single-process design can reach with the *real* (GEOS) geometry.
- **`npvec` is flat at ~43 t/s — and is *slower than `sync`*.** Batching the box geometry in numpy
  did not beat even the single-threaded GEOS reference, let alone `subproc`: the numpy raster
  (finding 2) is slower than GEOS `contains_xy`, and the swept-clearance batching (finding 1) wins
  only on the minority affine. Single-process stepping also throws away the parallelism that is the
  entire point of "use more resources" (finding 3).

## Time attribution: where the env step actually spends its time

Profiled the **real** `_EnvWorker.step` path on `trio-box` (cProfile + wall-clock split, 1500-2000
steps):

| Phase | Share of env-step wall-clock | Batchable in numpy? |
|---|---|---|
| `env.step` geometry (swept-path clearance, dominated by `swept_intrusion_m2` → `_motion_clear` + `aircraft_parts_world`) | **~80%** | Partly — see below |
| `encode` (rasterize: affine + `contains_xy`) | **~20%** | Yes (affine), but the raster point-test is *slower* in numpy |

Within the cProfile leaf attribution of the env step's GEOS work:

| GEOS leaf | tottime share | Nature |
|---|---|---|
| `aircraft_parts_world` Polygon construction (`linearrings`/`polygons`/`Polygon.__new__` + `oriented_rect` + `local_to_world`) | ~17% | **batchable affine** |
| `shapely.contains_xy` (the rasterizer) | ~3% | batchable affine, but the point-test is GEOS-faster (below) |
| GEOS predicates — `intersection` / `intersects` / `union_all` (overlap area in the swept-clearance loop) | ~15% | **hard to batch** |
| Python/shapely dispatch glue (`shapely.decorators` wrapper hit ~5.7M times, coords iteration, `_motion_clear` orchestration) | ~65% | the per-call C-dispatch tax — *grows*, doesn't shrink, under batching |

The cumulative picture: **`swept_intrusion_m2` is ~85% of env-step cumulative time**, and inside
it `_motion_clear` (the per-pose oracle) + `aircraft_parts_world` (rebuilding the mover's part
polygons at every sampled arc pose) dominate. A single movement primitive samples its arc at the
finer of 0.05 m / 1° into **dozens of poses**, and *each* pose builds the mover polygons and runs
GEOS clearance/overlap predicates against the parked obstacles. `aircraft_parts_world` is called
**~89× per env step** on `trio-box` — exactly the #381 bottleneck, concentrated in the swept loop.

## Two findings that kill the SIMD win

**1. The dominant cost is the per-pose swept-clearance loop, which is *ragged* across envs and
predicate-bound, not affine-bound.** The batchable affine (Polygon construction, ~17%) and the
raster (~3%) are a minority; the swept-path clearance — per-pose GEOS predicates over a
per-action-variable number of poses — is the majority. Different envs take different actions →
different arc lengths → different pose counts, so the `(N, poses, parts)` tensor is ragged and must
be padded to the max, wasting work. And the *clearance predicate itself* (`_motion_clear` +
overlap area) is a GEOS `intersects`/`distance`/`intersection` call, not a few float ops — the SAT
proxy can stand in for the *arithmetic*, but the real env uses GEOS here and GEOS does not batch
across envs in one call.

**2. numpy SAT is *slower* than GEOS for the point-in-polygon raster — the exact place we hoped to
win.** Microbenchmark (15 rects, 18 432-cell grid, the `trio-box` raster):

| Rasterizer | ms/raster | vs GEOS |
|---|---|---|
| shapely `contains_xy` (union → one C call) — **the incumbent** | ~0.47 | 1.0× |
| numpy SAT, per-rect loop-accumulate | ~1.9 | **4.1× slower** |
| numpy SAT, `(H*W, R, 2)` einsum broadcast | ~5.8 | **12.4× slower** |

`contains_xy` is already optimized GEOS C over a unioned geometry; hand-rolled numpy point-in-rect
pays Python/array-allocation overhead per part and loses. So the 20% of the step that *is* the
raster gets **worse**, not better, in pure numpy.

**3. The prototype does not scale with N.** `npvec` throughput is essentially **flat from N=4 to
N=16** — one process steps N envs serially through the (numpy) raster loop, so adding envs adds
proportional work with no parallelism. `subproc` is the opposite: it spreads the GEOS cost across
cores and its aggregate transitions/sec *rises* with N (until cores/RAM saturate). The whole point
of "use more resources" is parallelism, and the single-process design throws it away.

## Verdict: **NO-GO**

Stepping all N envs in one process with numpy-batched box geometry does **not** beat
`SubprocVectorEnv` on transitions/sec, and the modest single-process RAM saving does not justify it:

- The env-step cost is **~80% the per-pose swept-clearance loop**, which is GEOS-predicate-bound and
  ragged across envs — the part numpy batching helps least.
- The one phase we hoped numpy would win (the raster) is **4–12× slower** in numpy than GEOS
  `contains_xy`.
- The batchable affine (`aircraft_parts_world`, ~17%) is real but a minority, and even there the
  per-call shapely **dispatch glue (~65% of leaf time)** does not shrink under batching — it is a
  fixed per-`Polygon`-object tax that an `(N, parts, verts)` numpy path only avoids if it *also*
  replaces every downstream GEOS predicate with a numpy equivalent (and #735 shows the equivalence
  is delicate; finding 2 shows it is also slower for the raster).
- Single-process stepping **does not scale with N** (no parallelism), which defeats the "use more
  resources" premise; `subproc`'s aggregate throughput rises with cores while `npvec`'s is flat.

**Why `subproc` wins:** the real bottleneck is GEOS — `Polygon()` construction *and* the swept-path
clearance predicates — and GEOS work is **embarrassingly parallel across envs at the process level
but not vectorizable across envs in one numpy call**. Spreading whole-env GEOS work across worker
processes (the incumbent) extracts the parallelism the hardware offers; collapsing it into one
process to "SIMD the geometry" trades that parallelism for a numpy raster that is itself slower than
the GEOS one. The right lever for *more throughput* is the rest of the throughput epic —
cheaper per-pose geometry (#735 SAT for the oriented-rect predicates, where it *is* faster than
GEOS for area/overlap), IPC/cache trims (#752/#753), and packing more workers per box (#747) — not
a single-process numpy vec-env.

**What a GO would have required** (for the record): a vec-env reshaped so that (a) the swept-clearance
predicate is replaced wholesale by a batched numpy/SAT oracle proven equivalent to `_motion_clear`
(gated on #735), (b) arc sampling is made uniform across envs (fixed pose count) so the
`(N, poses, parts)` tensor is dense, and (c) the raster is replaced by something that beats GEOS
`contains_xy` (e.g. scanline fill in C, or dropping the raster channel — cf. #752's "drop static
channels"). That is a much larger architectural change than the per-pose-cheaper-geometry levers
already in the epic, and finding 2 makes (c) actively unattractive.
