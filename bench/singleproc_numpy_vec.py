"""python -m bench.singleproc_numpy_vec — spike #757 (Wave 3, epic #760).

GO/NO-GO bench for the open "use more resources" question: can stepping ALL N envs in ONE
process with numpy-batched box geometry beat the incumbent ``SubprocVectorEnv`` (N spawn
workers + pickle IPC + per-worker torch/shapely RAM) on **transitions/sec AND peak RAM**?

This is a THROWAWAY measurement harness, NOT a production vec-env: it does not touch the
shipped ``SubprocVectorEnv`` / ``SyncVectorEnv`` and is never imported by training. It exists
to answer #757 with numbers, then the verdict lands in ``docs/spikes/singleproc-numpy-vec.md``.

Three stepped backends, all on a BOX curriculum rung (every part is an oriented rectangle, so
the geometry CAN be pure-numpy SAT — no GEOS), driven by a FIXED random action stream (no
policy forward; we isolate env-stepping throughput):

* ``sync``     — the real ``SyncVectorEnv`` of real ``_EnvWorker`` (in-process reference; GEOS).
* ``subproc``  — the real ``SubprocVectorEnv`` (N spawn workers; GEOS; the incumbent).
* ``npvec``    — the prototype: N envs stepped in one process, batching the box geometry across
  an ``(N, parts, 4, 2)`` numpy tensor (affine transform + SAT overlap + SAT point-in-rect
  raster). It REPRODUCES the env-step's geometry COST (apply-arc → per-pose swept clearance →
  Park score → encode raster) on representative box bodies; it is NOT bit-exact validity (a
  sibling #735 Lever-B spike owns the GEOS-equivalence proof). For a throughput verdict that
  caveat is fine — we are sizing whether SIMD-batched box geometry can dominate the irreducible
  GEOS ``Polygon()`` / predicate cost, not asserting reward parity.

Like the rest of ``bench/`` this binds on a FIXED step COUNT (``steps × n_envs`` transitions),
NOT a wall-clock budget. Peak RAM is ``ru_maxrss`` of SELF (+ CHILDREN for subproc). Run each
(backend, N) cell in its OWN fresh process so ``ru_maxrss`` (a high-water mark) is per-cell.
The real-backend rows step torch-free ``_EnvWorker``s; the ``subproc`` row reuses
``ml.train._build_stage_worker`` as its picklable spawn factory, which imports torch, so that
row needs the ``[train]`` extra. ``npvec``/``sync`` need only numpy + shapely + ``ml`` on path.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import resource
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from hangarfit.geometry import aircraft_parts_world
from hangarfit.loader import load_fleet, load_hangar
from hangarfit.models import Aircraft, Hangar, Placement
from ml.action_space import decode
from ml.curriculum import DEFAULT_LADDER, make_episode_sampler, stage_rng
from ml.encoding import EncoderConfig
from ml.stage_builder import build_stage_env, effective_fleet_ids
from ml.types import Park
from ml.vector_env import _EnvWorker

# ---------------------------------------------------------------------------
# Fixed action stream (shared across backends so the WORK is identical)
# ---------------------------------------------------------------------------

_N_KIND = 9  # ACTION_DIM-1 movement kinds + PARK at index 8 (see ml.encoding)
_N_MAG = 5  # MAGNITUDE_DIM


def _action_stream(seed: int, n_envs: int, total_steps: int) -> list[list[tuple[int, int]]]:
    """``total_steps`` vectors of ``n_envs`` ``(kind_idx, mag_idx)`` actions. Deterministic
    given the seed, so every backend steps the SAME action sequence and the comparison is of
    machinery, not luck. Park (kind 8) lands ~1/9 of the time — matching the measured ~11%
    park fraction on the trio-box rung."""
    rng = random.Random(seed)
    return [
        [(rng.randrange(_N_KIND), rng.randrange(_N_MAG)) for _ in range(n_envs)]
        for _ in range(total_steps)
    ]


# ---------------------------------------------------------------------------
# Box-geometry numpy kernels (oriented rectangles only — valid on a box rung)
# ---------------------------------------------------------------------------
#
# Every box-fleet part is a scalar oriented rectangle (data/catalog/*.yaml carry no
# local_vertices), so a part's world footprint is 4 corners under the det(-1) compass
# transform documented in src/hangarfit/geometry.py. We batch that transform + the
# overlap/containment SAT predicates across (N, parts, 4, 2) tensors, replacing GEOS.


@dataclass(frozen=True, slots=True)
class _BoxBody:
    """A box body reduced to its part rectangles in plane-local (forward u, right v) frame.

    ``corners_local`` is (parts, 4, 2) local (u, v) corners; ``z`` is (parts, 2) [bottom, top].
    Derived once per body from ``aircraft_parts_world`` at the identity pose (so the local
    rectangle == the part the GEOS path builds), then transformed per pose in numpy."""

    corners_local: np.ndarray  # (parts, 4, 2) float64
    z: np.ndarray  # (parts, 2) float64


def _box_body(body: Aircraft) -> _BoxBody:
    """Extract a body's part rectangles in plane-local coords. We read each world polygon at
    the identity placement (heading 0, origin), whose ring IS the plane-local footprint (the
    identity transform is the identity), then reduce it to its 4-corner axis-aligned bounding
    rectangle so the per-body tensor is uniform (P, 4, 2). A handful of box-fleet wings are
    tapered (6-vertex) polygons, not plain rectangles; using their bounding rect keeps the
    affine batch homogeneous and is representative of the box-rung SAT cost (4 corners/part is
    exactly what an oriented-rect SAT path carries). This is a THROUGHPUT proxy, not bit-exact
    geometry — the #735 Lever-B spike owns exact oriented-rect equivalence."""
    parts = aircraft_parts_world(
        body, Placement(plane_id=body.id, x_m=0.0, y_m=0.0, heading_deg=0.0, on_carts=False)
    )
    corners = []
    zs = []
    for wp in parts:
        xmin, ymin, xmax, ymax = wp.polygon.bounds
        corners.append([(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)])
        zs.append((wp.z_bottom_m, wp.z_top_m))
    return _BoxBody(np.asarray(corners, dtype=np.float64), np.asarray(zs, dtype=np.float64))


def _affine_batch(corners_local: np.ndarray, poses: np.ndarray) -> np.ndarray:
    """Batched det(-1) compass transform of local part corners to world.

    ``corners_local`` (P, 4, 2) local (u, v); ``poses`` (M, 3) [x, y, heading_deg]. Returns
    (M, P, 4, 2) world corners. Mirrors local_to_world::

        world_x = x + u*sin(h) + v*cos(h)
        world_y = y + u*cos(h) - v*sin(h)
    """
    h = np.radians(poses[:, 2])  # (M,)
    s = np.sin(h)[:, None, None]  # (M,1,1)
    c = np.cos(h)[:, None, None]
    u = corners_local[None, :, :, 0]  # (1,P,4)
    v = corners_local[None, :, :, 1]
    wx = poses[:, 0][:, None, None] + u * s + v * c  # (M,P,4)
    wy = poses[:, 1][:, None, None] + u * c - v * s
    return np.stack([wx, wy], axis=-1)  # (M,P,4,2)


def _aabb_overlap_area_batch(rects_a: np.ndarray, rects_b: np.ndarray) -> np.ndarray:
    """Vectorized AABB overlap area for two broadcastable rect tensors ``(..., 4, 2)``.

    Returns ``(...)`` overlap areas. We use the axis-aligned-bound overlap (cheap, fully
    vectorizable, no Python loop) as the cost-and-magnitude stand-in for GEOS
    ``intersection().area`` — the #735 Lever-B spike owns exact oriented-rect SAT clipping;
    a throughput spike only needs the SAME order of batched float ops the real path would run.
    This is the kernel the whole SIMD premise rests on: one numpy expression over the entire
    (poses × parts × obstacles) cross-product, NOT a Python loop."""
    a_min = rects_a.min(axis=-2)  # (..., 2)
    a_max = rects_a.max(axis=-2)
    b_min = rects_b.min(axis=-2)
    b_max = rects_b.max(axis=-2)
    dx = np.minimum(a_max[..., 0], b_max[..., 0]) - np.maximum(a_min[..., 0], b_min[..., 0])
    dy = np.minimum(a_max[..., 1], b_max[..., 1]) - np.maximum(a_min[..., 1], b_min[..., 1])
    return np.clip(dx, 0.0, None) * np.clip(dy, 0.0, None)


def _point_in_rects_raster_batch(world_rects: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Binary occupancy ``(H*W,)`` for a union of oriented rectangles via numpy point-in-rect.

    The numpy-SAT replacement for shapely.contains_xy in the encoder's _rasterize: a cell
    centre is inside an oriented rect iff it projects within both edge spans. ``world_rects``
    is ``(R, 4, 2)``, ``pts`` is ``(H*W, 2)``. We accumulate over rects in a short Python loop
    (R = parts, small) rather than materializing the ``(H*W, R, 2)`` cross-product — measured
    ~3x cheaper than the einsum form, and STILL ~4x slower than GEOS contains_xy (see the spike
    write-up): point-in-polygon is exactly where shapely's optimized C beats hand-rolled numpy."""
    inside = np.zeros(pts.shape[0], dtype=bool)
    for rect in world_rects:  # R = parts per body (small); the per-rect test is vectorized
        p0, p1, p3 = rect[0], rect[1], rect[3]
        e1 = p1 - p0
        e2 = p3 - p0
        d = pts - p0  # (H*W, 2)
        t1 = d @ e1
        t2 = d @ e2
        inside |= (t1 >= 0) & (t1 <= e1 @ e1) & (t2 >= 0) & (t2 <= e2 @ e2)
    return inside


# ---------------------------------------------------------------------------
# The single-process numpy-batched prototype env-set
# ---------------------------------------------------------------------------


class _NumpyVecProto:
    """N box envs stepped in ONE process with numpy-batched geometry.

    Reproduces the env-step's GEOMETRY COST profile (not its reward semantics): a movement
    action samples an arc into M poses, the swept clearance batches the mover's part affine
    over (M, P, 4, 2) and SAT-tests each pose-part against the parked obstacle rects; a Park
    action runs the layout overlap SAT; every step encodes the raster via numpy point-in-rect
    over the same grid the real encoder uses. The point is to measure whether batching these
    across envs reclaims the GEOS cost — so the kernels do representative work, not validity."""

    def __init__(self, body: _BoxBody, hangar: Hangar, config: EncoderConfig, n_envs: int) -> None:
        self._body = body
        self._hangar = hangar
        self._cfg = config
        self._n = n_envs
        # per-env pose [x, y, heading_deg] of the active mover, seeded at the door
        self._pose = np.tile(
            np.array([hangar.door.center_x_m, -(hangar.apron_depth_m / 2.0), 0.0]),
            (n_envs, 1),
        ).astype(np.float64)
        # per-env parked obstacle rects (start empty; grows on Park). A list of (P,4,2) arrays.
        self._parked: list[list[np.ndarray]] = [[] for _ in range(n_envs)]
        # the raster grid (shared, static) — same geometry the real encoder uses
        origin_x, origin_y = 0.0, -config.apron_band_m
        self._xs = origin_x + (np.arange(config.grid_w) + 0.5) * config.cell_m
        self._ys = origin_y + (np.arange(config.grid_h) + 0.5) * config.cell_m

    @property
    def num_envs(self) -> int:
        return self._n

    def _body_turn_radius(self) -> float:
        return 7.0  # representative own-gear taxi radius (fuji); box rungs are own-gear

    def _arc_poses(self, pose: np.ndarray, kind: int, mag: int) -> np.ndarray:
        """Sample a movement primitive into world poses (M, 3), mirroring DubinsArc.sample's
        density (the finer of 0.05 m / 1 deg). Straight 'S'/'T' translate; L/R sweep heading.
        A coarse but representative integrator — the point is the pose COUNT and per-pose work,
        which dominate the swept-clearance cost, not exact arc shape."""
        from ml.action_space import PIVOT_BINS_DEG, TRANSLATION_BINS

        prim = decode(kind, mag, turn_radius_m=self._body_turn_radius())
        if isinstance(prim, Park):  # never called for Park
            return pose[None, :]
        if prim.kind in ("L", "R") and self._body_turn_radius() == 0.0:
            sweep_deg = PIVOT_BINS_DEG[mag]
            steps = max(1, math.ceil(sweep_deg / 1.0))
            out = np.tile(pose, (steps + 1, 1))
            sign = 1.0 if prim.kind == "L" else -1.0
            out[:, 2] = pose[2] + sign * np.linspace(0.0, sweep_deg, steps + 1)
            return out
        dist = TRANSLATION_BINS[mag]
        steps = max(1, math.ceil(dist / 0.05))
        out = np.tile(pose, (steps + 1, 1))
        hh = math.radians(pose[2])
        # 'S' advances along forward (world +y at h=0 via det(-1)); 'T' perpendicular
        if prim.kind == "T":
            dx, dy = math.cos(hh), -math.sin(hh)
        else:
            dx, dy = math.sin(hh), math.cos(hh)
        ts = np.linspace(0.0, dist * prim.gear, steps + 1)
        out[:, 0] = pose[0] + dx * ts
        out[:, 1] = pose[1] + dy * ts
        return out

    def step(self, actions: list[tuple[int, int]]) -> None:
        """Step all N envs in ONE batched pass. Geometry-cost-faithful; discards results
        (throughput bench). The whole point of the spike is that the heavy work is numpy-
        batched ACROSS envs, not a per-env Python loop — so the swept-clearance SAT and the
        raster run as broadcast expressions over all N envs' geometry."""
        n = self._n
        parts = self._body.corners_local
        n_parts = parts.shape[0]

        # ---- 1. swept-clearance cost: batch every env's arc poses, then one SAT cross-product.
        # Pad each env's arc to the max pose count M so the affine is one (N*M, P, 4, 2) call.
        per_env_poses = [self._arc_poses(self._pose[i], *actions[i]) for i in range(n)]
        m = max(p.shape[0] for p in per_env_poses)
        padded = np.zeros((n, m, 3), dtype=np.float64)
        for i, p in enumerate(per_env_poses):
            padded[i, : p.shape[0]] = p
            padded[i, p.shape[0] :] = p[-1]  # repeat last (clamp) so the batch is uniform
            self._pose[i] = p[-1]
        world = _affine_batch(parts, padded.reshape(n * m, 3)).reshape(n, m, n_parts, 4, 2)
        # SAT each (env, pose, mover-part) against that env's parked obstacle rects — vectorized
        # over the pose×part axes; the only Python loop is over the (small, growing) parked set.
        for i in range(n):
            if not self._parked[i]:
                continue
            obstacles = np.concatenate(self._parked[i], axis=0)  # (O, 4, 2)
            mover = world[i].reshape(m * n_parts, 4, 2)  # (M*P, 4, 2)
            # broadcast (M*P, 1, 4, 2) vs (1, O, 4, 2) -> (M*P, O) overlap areas, one expression
            _aabb_overlap_area_batch(mover[:, None], obstacles[None, :])

        # ---- 2. Park bookkeeping: commit the active footprint for envs that PARKed, respawn.
        active_now = _affine_batch(parts, self._pose)  # (N, P, 4, 2) at current pose
        for i, (kind, mag) in enumerate(actions):
            if isinstance(decode(kind, mag, turn_radius_m=self._body_turn_radius()), Park):
                self._parked[i].append(active_now[i].copy())
                self._pose[i] = [self._hangar.door.center_x_m, -4.0, 0.0]

        # ---- 3. encode raster: rebuild the active footprints (poses changed by respawn), then
        # one point-in-rect over the full grid for each env's union of active+parked rects.
        active_final = _affine_batch(parts, self._pose)  # (N, P, 4, 2)
        cx, cy = np.meshgrid(self._xs, self._ys)
        pts = np.stack([cx.ravel(), cy.ravel()], axis=-1)  # (H*W, 2)
        for i in range(n):
            rects = [active_final[i], *self._parked[i]]
            all_rects = np.concatenate(rects, axis=0)
            _point_in_rects_raster_batch(all_rects, pts)


# ---------------------------------------------------------------------------
# Backends + driver
# ---------------------------------------------------------------------------


def _peak_rss_mib(*, include_children: bool) -> float:
    """ru_maxrss peak resident set (MiB). On Linux ru_maxrss is in KiB."""
    self_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    total_kb = self_kb
    if include_children:
        total_kb += resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return total_kb / 1024.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _stage_for(rung: str):
    try:
        return next(s for s in DEFAULT_LADDER if s.name == rung)
    except StopIteration:
        raise SystemExit(
            f"unknown box rung {rung!r}; box rungs are: trivial, pair-box, trio-box"
        ) from None


def _build_real_workers(rung: str, n_envs: int, seed: int) -> list[_EnvWorker]:
    stage = _stage_for(rung)
    enc = EncoderConfig()
    pool = effective_fleet_ids(stage)
    n = stage.difficulty.max_objects
    workers = []
    for wi in range(n_envs):
        env = build_stage_env(stage)
        rng = stage_rng(seed, 0, worker_index=wi)
        nxt = make_episode_sampler(stage, pool, n, rng)
        workers.append(_EnvWorker(env, enc, nxt))
    return workers


@dataclass
class BackendResult:
    backend: str
    rung: str
    n_envs: int
    transitions: int
    wall_s: float
    transitions_per_s: float
    peak_rss_mib: float


def _run_sync(rung: str, n_envs: int, seed: int, steps: int) -> BackendResult:
    from ml.vector_env import SyncVectorEnv

    workers = _build_real_workers(rung, n_envs, seed)
    vec = SyncVectorEnv(workers)
    vec.reset()
    stream = _action_stream(seed, n_envs, steps)
    t0 = time.perf_counter()
    for actions in stream:
        vec.step(actions)
    wall = time.perf_counter() - t0
    vec.close()
    trans = steps * n_envs
    return BackendResult(
        "sync", rung, n_envs, trans, wall, trans / wall, _peak_rss_mib(include_children=False)
    )


def _run_subproc(rung: str, n_envs: int, seed: int, steps: int, start_method: str) -> BackendResult:
    from functools import partial

    from ml.train import _build_stage_worker  # picklable spawn factory (imports torch)
    from ml.vector_env import SubprocVectorEnv

    stage = _stage_for(rung)
    enc = EncoderConfig()
    pool = effective_fleet_ids(stage)
    n = stage.difficulty.max_objects
    worker_fns = [
        partial(_build_stage_worker, stage, 0, pool, n, seed, None, enc, wi) for wi in range(n_envs)
    ]
    vec = SubprocVectorEnv(worker_fns, start_method=start_method)
    vec.reset()
    stream = _action_stream(seed, n_envs, steps)
    t0 = time.perf_counter()
    for actions in stream:
        vec.step(actions)
    wall = time.perf_counter() - t0
    vec.close()
    trans = steps * n_envs
    return BackendResult(
        "subproc", rung, n_envs, trans, wall, trans / wall, _peak_rss_mib(include_children=True)
    )


def _run_npvec(rung: str, n_envs: int, seed: int, steps: int) -> BackendResult:
    from dataclasses import replace

    stage = _stage_for(rung)
    enc = EncoderConfig()
    fleet = load_fleet(str(_repo_root() / stage.fleet_path))
    hangar = replace(
        load_hangar(str(_repo_root() / stage.hangar_path)), apron_depth_m=stage.apron_depth_m
    )
    body = _box_body(next(iter(fleet.values())))  # representative box body
    proto = _NumpyVecProto(body, hangar, enc, n_envs)
    stream = _action_stream(seed, n_envs, steps)
    t0 = time.perf_counter()
    for actions in stream:
        proto.step(actions)
    wall = time.perf_counter() - t0
    trans = steps * n_envs
    return BackendResult(
        "npvec", rung, n_envs, trans, wall, trans / wall, _peak_rss_mib(include_children=False)
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="spike #757 single-process numpy-vec vs subproc bench")
    ap.add_argument("--backend", choices=("sync", "subproc", "npvec"), required=True)
    ap.add_argument("--rung", default="trio-box", help="box rung (trivial/pair-box/trio-box)")
    ap.add_argument("--n-envs", type=int, default=8)
    ap.add_argument("--steps", type=int, default=2000, help="vectors of actions (per-env steps)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--start-method", default="spawn", help="subproc start method")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.backend == "sync":
        r = _run_sync(args.rung, args.n_envs, args.seed, args.steps)
    elif args.backend == "subproc":
        r = _run_subproc(args.rung, args.n_envs, args.seed, args.steps, args.start_method)
    else:
        r = _run_npvec(args.rung, args.n_envs, args.seed, args.steps)

    if args.json:
        print(json.dumps(asdict(r)))
    else:
        print(
            f"{r.backend:8s} rung={r.rung} N={r.n_envs:2d}  "
            f"{r.transitions_per_s:9.1f} transitions/s  "
            f"peak_rss={r.peak_rss_mib:8.1f} MiB  "
            f"({r.transitions:,} trans / {r.wall_s:.2f}s)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
