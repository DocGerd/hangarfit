"""Torch-free onnxruntime inference for the learned backend (sub-project #5, #607)."""

from __future__ import annotations

import pytest

ort = pytest.importorskip("onnxruntime")
torch = pytest.importorskip("torch")  # OrtPolicy is torch-free, but we export with torch here

from ml.encoding import EncoderConfig, encode  # noqa: E402
from ml.export import export_onnx  # noqa: E402
from ml.infer import OrtPolicy  # noqa: E402
from ml.policy import HangarFitPolicy  # noqa: E402
from ml.train import build_trivial_env  # noqa: E402


def test_ortpolicy_matches_torch_act(tmp_path):
    torch.manual_seed(0)
    policy = HangarFitPolicy()
    policy.eval()
    env = build_trivial_env()
    obs = env.reset()
    obs_t = encode(obs, env.hangar, {**env.fleet, **env.ground_objects}, EncoderConfig())
    tr = obs.active.body.effective_turn_radius_m()

    onnx_path = tmp_path / "p.onnx"
    export_onnx(policy, onnx_path, example=obs_t)
    ort_pol = OrtPolicy(onnx_path)

    # OrtPolicy runs the ONNX graph (standard attention path). `policy.act` uses the fused
    # fast path by default, which on an UNTRAINED near-tie policy can flip the argmax. Compute
    # the torch reference on the same standard path so the comparison is apples-to-apples (a
    # trained policy's decisive logits make this moot). Discovered in Task 1.
    prev_fastpath = torch.backends.mha.get_fastpath_enabled()
    torch.backends.mha.set_fastpath_enabled(False)
    try:
        (_k, _m), _lp, torch_action = policy.act(obs_t, turn_radius_m=tr, deterministic=True)
    finally:
        torch.backends.mha.set_fastpath_enabled(prev_fastpath)
    ort_action = ort_pol.act(obs_t, turn_radius_m=tr)
    assert type(ort_action) is type(torch_action)
    assert ort_action == torch_action


import math  # noqa: E402

from hangarfit.loader import load_scenario  # noqa: E402
from hangarfit.towplanner import DubinsArc, Segment  # noqa: E402
from ml.geometry_oracle import apply_primitive  # noqa: E402
from ml.types import Primitive  # noqa: E402


def test_primitive_sequence_dubinsarc_endpoint_parity():
    """A multi-segment DubinsArc built from a primitive sequence integrates to the same
    pose as chaining apply_primitive — the guarantee build_moves_plan relies on."""
    from hangarfit.towplanner import Pose

    start = Pose(x_m=5.0, y_m=-4.0, heading_deg=0.0)
    prims = [
        Primitive(kind="S", magnitude=2.0, gear=1),
        Primitive(kind="L", magnitude=3.0, gear=1),
        Primitive(kind="S", magnitude=1.0, gear=1),
    ]
    tr = 4.0
    pose = start
    for p in prims:
        pose, _ = apply_primitive(pose, p, turn_radius_m=tr)
    arc = DubinsArc(
        start=start,
        end=pose,
        turn_radius_m=tr,
        segments=tuple(Segment(kind=p.kind, length_m=p.magnitude, gear=p.gear) for p in prims),
    )
    integrated = arc.pose_at(arc.length_m)
    assert math.isclose(integrated.x_m, pose.x_m, abs_tol=1e-6)
    assert math.isclose(integrated.y_m, pose.y_m, abs_tol=1e-6)
    assert math.isclose(integrated.heading_deg, pose.heading_deg, abs_tol=1e-6)


def test_env_from_scenario_queues_placeables(tmp_path):
    import pathlib

    from ml.infer import env_from_scenario

    root = pathlib.Path(__file__).resolve().parents[2]
    scenario = load_scenario(str(root / "tests/fixtures/scenario_minimal.yaml"))
    env = env_from_scenario(scenario)
    obs = env.reset()
    assert obs.active is not None
    assert set(env.requested_ids) == set(scenario.placeable_ids)
    assert env.hangar.apron_depth_m == 8.0


def test_rollout_builds_moves_plan(tmp_path):
    import pathlib

    from ml.infer import OrtPolicy, build_moves_plan, env_from_scenario, rollout

    torch.manual_seed(0)
    policy = HangarFitPolicy()
    policy.eval()
    onnx_path = tmp_path / "p.onnx"
    export_onnx(policy, onnx_path)
    ort_pol = OrtPolicy(onnx_path)

    root = pathlib.Path(__file__).resolve().parents[2]
    scenario = load_scenario(str(root / "tests/fixtures/scenario_minimal.yaml"))
    env = env_from_scenario(scenario)
    layout, driven, info = rollout(env, ort_pol)
    plan = build_moves_plan(layout, driven, env)
    # Every parked object has a Move; every Move targets a real slot pose.
    assert len(plan.moves) == len(driven)
    assert all(m.target_slot is not None for m in plan.moves)
