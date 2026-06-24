"""ONNX export of HangarFitPolicy (sub-project #5, #607). Needs the [train] extra
(torch) to export and the [learned-infer] extra (onnxruntime) to run the round-trip."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
ort = pytest.importorskip("onnxruntime")

import numpy as np  # noqa: E402

from ml.encoding import EncoderConfig, encode  # noqa: E402
from ml.export import ONNX_INPUT_NAMES, ONNX_OUTPUT_NAMES, export_onnx  # noqa: E402
from ml.policy import HangarFitPolicy, to_batch  # noqa: E402
from ml.train import build_trivial_env  # noqa: E402


def _example_obs():
    env = build_trivial_env()
    obs = env.reset()
    return encode(obs, env.hangar, {**env.fleet, **env.ground_objects}, EncoderConfig())


def test_export_argmax_parity(tmp_path):
    torch.manual_seed(0)
    policy = HangarFitPolicy()
    policy.eval()
    obs_t = _example_obs()
    out_path = tmp_path / "policy.onnx"
    export_onnx(policy, out_path)

    batch = to_batch([obs_t])
    # The ONNX graph traces the STANDARD attention path: export disables the fused/nested
    # TransformerEncoder fast paths (they emit ops with no ONNX symbolic). Those paths are a
    # numerically-equivalent optimization, but on an UNTRAINED near-tie policy the tiny
    # fast-vs-standard delta can still flip an argmax. Compute the torch reference on the
    # SAME standard path so the comparison is apples-to-apples (a trained policy's decisive
    # logits make this distinction moot).
    prev_fastpath = torch.backends.mha.get_fastpath_enabled()
    torch.backends.mha.set_fastpath_enabled(False)
    try:
        with torch.no_grad():
            torch_out = policy(batch)
    finally:
        torch.backends.mha.set_fastpath_enabled(prev_fastpath)
    feed = {
        "raster": batch["raster"].numpy(),
        "tokens": batch["tokens"].numpy(),
        "token_mask": batch["token_mask"].numpy(),
        "active_index": batch["active_index"].numpy(),
        "legal_action_mask": batch["legal_action_mask"].numpy(),
    }
    sess = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    kind_logits, mag_logits = sess.run(list(ONNX_OUTPUT_NAMES), feed)

    assert tuple(ONNX_INPUT_NAMES) == (
        "raster",
        "tokens",
        "token_mask",
        "active_index",
        "legal_action_mask",
    )
    # Faithful export: the legal (finite) logits match within float tolerance. Illegal slots
    # are -inf in both (the masked_fill is inside the graph), so compare only finite entries.
    kt = torch_out.kind_gear_logits[0].numpy()
    finite = np.isfinite(kt)
    np.testing.assert_allclose(kind_logits[0][finite], kt[finite], atol=1e-3, rtol=1e-3)
    np.testing.assert_allclose(
        mag_logits[0], torch_out.magnitude_bin_logits[0].numpy(), atol=1e-3, rtol=1e-3
    )
    # The inference-relevant invariant: argmax of each head agrees.
    onnx_kind = int(np.argmax(kind_logits, axis=-1)[0])
    onnx_mag = int(np.argmax(mag_logits, axis=-1)[0])
    assert onnx_kind == int(torch_out.kind_gear_logits.argmax(-1)[0])
    assert onnx_mag == int(torch_out.magnitude_bin_logits.argmax(-1)[0])


def test_train_save_onnx_writes_file(tmp_path):
    from ml.train import train

    onnx_path = tmp_path / "trivial.onnx"
    train(iterations=1, rollout_len=16, save_onnx=str(onnx_path))
    assert onnx_path.exists() and onnx_path.stat().st_size > 0


def test_export_onnx_ego_policy_without_example_fails_clearly(tmp_path):
    # #827: a relative_encoder (28-wide) policy with the default 24-wide dummy must fail with a
    # clear message naming the deferred follow-up, NOT an obscure matmul RuntimeError.
    policy = HangarFitPolicy(relative_encoder=True)
    with pytest.raises(ValueError, match="relative_encoder"):
        export_onnx(policy, tmp_path / "ego.onnx")


def test_main_rejects_relative_encoder_with_save_onnx(tmp_path):
    # #827: --save-onnx + --relative-encoder must fail FAST (before any training run), not crash
    # at export after the whole run completes.
    from ml.train import main

    argv = ["--schedule", "trivial", "--relative-encoder", "--save-onnx", str(tmp_path / "x.onnx")]
    with pytest.raises(SystemExit):
        main(argv)
