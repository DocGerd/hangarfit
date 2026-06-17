"""Torch-free inference for the learned backend (sub-project #5, epic #607).

Runs a trained policy exported to ONNX (``ml/export.py``) with onnxruntime + numpy —
NO torch in this module. ``solve_learned_impl`` (later task) drives the cold-joint env
to a terminal layout and returns a ``SolveResult`` behind the deterministic verifier.

Determinism (ADR-0027): the proposer's tier-1 contract is within-build bit-identity
(fixed weights + seed + pinned CPUExecutionProvider). The verifier stays strict and is
the sole arbiter of validity — an invalid proposal yields a no-layout SolveResult."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from hangarfit.models import Layout, Scenario
from hangarfit.towplanner import DubinsArc, Move, MovesPlan, Pose, Segment
from ml.action_space import decode
from ml.encoding import EncoderConfig, ObservationTensors, encode
from ml.env import HangarFitEnv
from ml.export import ONNX_OUTPUT_NAMES
from ml.types import DifficultyConfig, Park, Primitive, StepInfo


class OrtPolicy:
    """A trained policy forward as an onnxruntime session. ``act`` mirrors
    ``HangarFitPolicy.act(deterministic=True)`` with numpy argmax — the ``-inf`` legal
    mask is already baked into the graph, so argmax always yields a legal action."""

    def __init__(self, onnx_path: str | Path) -> None:
        import onnxruntime as ort  # local import: onnxruntime is the [learned-infer] extra

        # Pin CPUExecutionProvider single-threaded for the ADR-0027 tier-1 bit-identity
        # contract (within-build double-run reproducibility).
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )

    def act(self, obs: ObservationTensors, *, turn_radius_m: float) -> Primitive | Park:
        if obs.active_index < 0:
            raise ValueError("OrtPolicy.act called on a terminal observation (active_index < 0)")
        feed = {
            "raster": obs.raster[None].astype(np.float32),
            "tokens": obs.tokens[None].astype(np.float32),
            "token_mask": obs.token_mask[None].astype(np.bool_),
            "active_index": np.asarray([obs.active_index], dtype=np.int64),
            "legal_action_mask": obs.legal_action_mask[None].astype(np.bool_),
        }
        kind_logits, mag_logits = self._session.run(list(ONNX_OUTPUT_NAMES), feed)
        kind_idx = int(np.argmax(kind_logits[0]))
        mag_idx = int(np.argmax(mag_logits[0]))
        return decode(kind_idx, mag_idx, turn_radius_m=turn_radius_m)


def env_from_scenario(scenario: Scenario, *, apron_depth_m: float = 8.0) -> HangarFitEnv:
    """Build a cold-joint env from a production ``Scenario`` (the inference counterpart
    of ``ml.benchmark.build_scenario_env``, which builds from a ``BenchScenario`` path).

    Movable bodies (aircraft + placed-routed movers) are driven in from an apron; fixed
    obstacles (immovable keep-outs) are PRE-PLACED at their surveyed poses, never driven —
    the same scene the deterministic verifier sees. The difficulty budgets are generous
    (a safety stop, not a curriculum cap)."""
    fixed_ids = [
        gid
        for gid in scenario.ground_objects
        if scenario.ground_object_defs[gid].object_class == "fixed_obstacle"
    ]
    placed = {p.plane_id for p in scenario.fixed_obstacle_placements}
    missing = [g for g in fixed_ids if g not in placed]
    if missing:
        raise ValueError(
            f"env_from_scenario: fixed obstacle(s) {missing} have no entry in "
            f"scenario.fixed_obstacle_placements — they would silently appear un-placed."
        )
    placeable = scenario.placeable_ids
    movers = {gid: scenario.ground_object_defs[gid] for gid in scenario.mover_ids}
    fixed_defs = {gid: scenario.ground_object_defs[gid] for gid in fixed_ids}
    per_object = 120
    difficulty = DifficultyConfig(
        max_objects=len(placeable),
        per_object_step_budget=per_object,
        total_step_budget=per_object * max(1, len(placeable)),
    )
    hangar = replace(scenario.hangar, apron_depth_m=apron_depth_m)
    return HangarFitEnv(
        hangar=hangar,
        fleet=scenario.fleet,
        requested_ids=placeable,
        ground_objects={**movers, **fixed_defs},
        fixed_placements=scenario.fixed_obstacle_placements,
        difficulty=difficulty,
    )


@dataclass(slots=True)
class _DrivenObject:
    """Record of one PARKED object: spawn pose, ordered primitives, and frozen end pose."""

    object_id: str
    start_pose: Pose
    end_pose: Pose
    primitives: list[Primitive] = field(default_factory=list)


def rollout(
    env: HangarFitEnv, policy: OrtPolicy, encoder: EncoderConfig | None = None
) -> tuple[Layout, list[_DrivenObject], StepInfo]:
    """Drive ``env`` to termination under ``policy`` (argmax). Record each PARKED object's
    spawn pose, the ordered primitives it was driven with, and its frozen (parked) pose.
    Objects abandoned at budget exhaustion are NOT recorded (they are not in the terminal
    layout). Returns (terminal_layout, driven_objects_in_park_order, last_step_info)."""
    enc = encoder or EncoderConfig()
    bodies = {**env.fleet, **env.ground_objects}
    obs = env.reset()
    driven: list[_DrivenObject] = []
    # The active object is identified at spawn; its primitives accumulate until a Park.
    current = _DrivenObject(obs.active.object_id, obs.active.pose, obs.active.pose)
    done = False
    info: StepInfo | None = None
    while not done and obs.active is not None:
        obs_t = encode(obs, env.hangar, bodies, enc)
        tr = obs.active.body.effective_turn_radius_m()
        action = policy.act(obs_t, turn_radius_m=tr)
        if isinstance(action, Park):
            current.end_pose = obs.active.pose  # the pose Park freezes
            driven.append(current)
        else:
            current.primitives.append(action)
        obs, _reward, done, info = env.step(action)
        if isinstance(action, Park) and not done and obs.active is not None:
            current = _DrivenObject(obs.active.object_id, obs.active.pose, obs.active.pose)
    if info is None:
        raise ValueError("rollout: episode produced no steps")
    return env._layout(), driven, info


def build_moves_plan(layout: Layout, driven: list[_DrivenObject], env: HangarFitEnv) -> MovesPlan:
    """Map each driven object's recorded primitives onto a DubinsArc tow Move (1:1
    Primitive->Segment). A zero-primitive object (parked at spawn) gets Move(path=None)
    — the established best-effort idiom, since DubinsArc.segments must be non-empty."""
    moves: list[Move] = []
    for d in driven:
        target = Pose(x_m=d.end_pose.x_m, y_m=d.end_pose.y_m, heading_deg=d.end_pose.heading_deg)
        if not d.primitives:
            moves.append(Move(plane_id=d.object_id, target_slot=target, path=None))
            continue
        tr = env._body(d.object_id).effective_turn_radius_m()
        segments = tuple(
            Segment(kind=p.kind, length_m=p.magnitude, gear=p.gear) for p in d.primitives
        )
        arc = DubinsArc(start=d.start_pose, end=target, turn_radius_m=tr, segments=segments)
        moves.append(Move(plane_id=d.object_id, target_slot=target, path=arc))
    return MovesPlan(target_layout=layout, moves=tuple(moves))
