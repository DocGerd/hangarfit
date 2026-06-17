"""Export a trained HangarFitPolicy to ONNX (sub-project #5, epic #607). Needs the
[train] extra (torch). The exported graph is the per-step policy FORWARD — the value
head (critic) is dropped, since inference only needs the two action heads. The
``-inf`` legal-mask is applied inside the graph, so a downstream numpy ``argmax``
yields a legal action with no extra masking. Inference (onnxruntime) lives in
``ml/infer.py`` and needs no torch."""

from __future__ import annotations

import warnings
from pathlib import Path

import torch
from torch import Tensor, nn

from ml.encoding import ACTION_DIM, RASTER_CHANNELS, TOKEN_DIM, EncoderConfig
from ml.policy import HangarFitPolicy, to_batch

ONNX_INPUT_NAMES = ("raster", "tokens", "token_mask", "active_index", "legal_action_mask")
ONNX_OUTPUT_NAMES = ("kind_gear_logits", "magnitude_bin_logits")


class _ForwardWrapper(nn.Module):
    """Positional-input wrapper over the dict-input policy forward, returning only the
    two action-head logit tensors (drops the scalar value head)."""

    def __init__(self, policy: HangarFitPolicy) -> None:
        super().__init__()
        self.policy = policy

    def forward(
        self,
        raster: Tensor,
        tokens: Tensor,
        token_mask: Tensor,
        active_index: Tensor,
        legal_action_mask: Tensor,
    ) -> tuple[Tensor, Tensor]:
        out = self.policy(
            {
                "raster": raster,
                "tokens": tokens,
                "token_mask": token_mask,
                "active_index": active_index,
                "legal_action_mask": legal_action_mask,
            }
        )
        return out.kind_gear_logits, out.magnitude_bin_logits


def _dummy_inputs(config: EncoderConfig) -> tuple[Tensor, ...]:
    """A single-sample batch with the encoder's shapes; values are irrelevant to the
    traced graph (masks all-True so nothing is force-masked during tracing)."""
    n = config.max_objects
    raster = torch.zeros(1, RASTER_CHANNELS, config.grid_h, config.grid_w, dtype=torch.float32)
    tokens = torch.zeros(1, n, TOKEN_DIM, dtype=torch.float32)
    token_mask = torch.ones(1, n, dtype=torch.bool)
    active_index = torch.zeros(1, dtype=torch.long)
    legal = torch.ones(1, ACTION_DIM, dtype=torch.bool)
    return raster, tokens, token_mask, active_index, legal


def export_onnx(
    policy: HangarFitPolicy,
    path: str | Path,
    *,
    example=None,
    opset: int = 17,
) -> None:
    """Trace ``policy``'s forward to an ONNX file at ``path``. Pass ``example`` (an
    ``ObservationTensors``) to trace real shapes, else a default-config dummy is used.
    Batch ``B`` and token count ``N`` are dynamic axes."""
    # The legacy TorchScript ONNX exporter leaves the module in train() mode on return,
    # which would silently re-activate the encoder dropout for any later forward the caller
    # makes on this policy. Capture and restore the original mode so export has no side
    # effect on the caller's model.
    was_training = policy.training
    policy.eval()
    wrapper = _ForwardWrapper(policy)
    args: tuple[Tensor, ...]
    if example is not None:
        b = to_batch([example])
        args = (
            b["raster"],
            b["tokens"],
            b["token_mask"],
            b["active_index"],
            b["legal_action_mask"],
        )
    else:
        args = _dummy_inputs(EncoderConfig())
    dynamic_axes = {
        "raster": {0: "B"},
        "tokens": {0: "B", 1: "N"},
        "token_mask": {0: "B", 1: "N"},
        "active_index": {0: "B"},
        "legal_action_mask": {0: "B"},
        "kind_gear_logits": {0: "B"},
        "magnitude_bin_logits": {0: "B"},
    }
    # Disable the TransformerEncoder/Layer sparsity fast paths for the export trace: in
    # eval mode they dispatch the fused ``aten::_transformer_encoder_layer_fwd`` and
    # ``aten::_nested_tensor_from_mask`` ops, which have no ONNX symbolic at any opset.
    # The standard attention path they fall back to is numerically identical (a perf
    # optimization only). The toggle is process-global, so restore it in ``finally`` —
    # training/inference elsewhere keep the fast path.
    fastpath_was_enabled = torch.backends.mha.get_fastpath_enabled()
    torch.backends.mha.set_fastpath_enabled(False)
    try:
        # We deliberately use the legacy TorchScript exporter (dynamo=False): the dynamo
        # exporter does not yet handle this model's masked attention cleanly. It and the
        # tracing of PolicyOutput's shape asserts emit known, benign DeprecationWarning /
        # TracerWarning noise — scope-suppress so callers' output stays pristine.
        with warnings.catch_warnings(), torch.no_grad():
            warnings.simplefilter("ignore", category=DeprecationWarning)
            warnings.simplefilter("ignore", category=torch.jit.TracerWarning)
            torch.onnx.export(
                wrapper,
                args,
                str(path),
                input_names=list(ONNX_INPUT_NAMES),
                output_names=list(ONNX_OUTPUT_NAMES),
                dynamic_axes=dynamic_axes,
                opset_version=opset,
                dynamo=False,
            )
    finally:
        torch.backends.mha.set_fastpath_enabled(fastpath_was_enabled)
        policy.train(was_training)
