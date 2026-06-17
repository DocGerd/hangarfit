"""Torch-free inference for the learned backend (sub-project #5, epic #607).

Runs a trained policy exported to ONNX (``ml/export.py``) with onnxruntime + numpy —
NO torch in this module. ``solve_learned_impl`` (later task) drives the cold-joint env
to a terminal layout and returns a ``SolveResult`` behind the deterministic verifier.

Determinism (ADR-0027): the proposer's tier-1 contract is within-build bit-identity
(fixed weights + seed + pinned CPUExecutionProvider). The verifier stays strict and is
the sole arbiter of validity — an invalid proposal yields a no-layout SolveResult."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ml.action_space import decode
from ml.encoding import ObservationTensors
from ml.export import ONNX_OUTPUT_NAMES
from ml.types import Park, Primitive


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
