"""Resume checkpoint for curriculum training (#710). A rich, ``weights_only``-safe artifact
carrying the policy, Adam optimizer, and return-normalizer state PLUS the policy architecture
and the curriculum position (which rungs are already done), so a long box-rung gate run can
survive a crash and resume from the next unfinished rung. Requires the [train] extra (torch).

Distinct from ``--save`` (ml/train.py), which writes a BARE ``state_dict`` for the ONNX-export
/ ml.eval consumer (loaded there with ``weights_only=True``): that path must stay a plain
state_dict, so resume gets its OWN richer format here. The payload is a plain dict of tensors +
Python scalars/strings (no custom classes), so it likewise loads under ``weights_only=True`` —
the same arbitrary-code-execution-safe posture ml.eval uses."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import torch

from ml.policy import HangarFitPolicy
from ml.ppo import ReturnNormalizer

# Bump when the on-disk payload shape changes incompatibly; load refuses a mismatch (loud).
CHECKPOINT_VERSION = 1

# Keys every v1 payload must carry; load fails loud (not a bare KeyError) if any is absent.
_REQUIRED_KEYS = (
    "policy_kwargs",
    "policy_state",
    "optimizer_state",
    "normalizer_state",
    "completed_stages",
)


@dataclass(frozen=True, slots=True)
class TrainingCheckpoint:
    """Decoded resume checkpoint. ``policy_kwargs`` reconstructs a same-shaped policy before
    ``load_state_dict`` (a shape mismatch would otherwise raise); ``completed_stages`` are the
    rung names already fully trained, so a resumed run skips them."""

    # str-keyed dicts; values stay Any because policy_kwargs is ``**``-splatted into
    # HangarFitPolicy (heterogeneous params, e.g. cnn_channels: tuple[int, ...]) and
    # policy_state/optimizer_state are torch state_dicts — any narrower value type would
    # break the splat / the state_dict round-trip.
    policy_kwargs: dict[str, Any]
    policy_state: dict[str, Any]
    optimizer_state: dict[str, Any]
    normalizer_state: dict[str, float | int] | None
    completed_stages: list[str] = field(default_factory=list)
    # Round-trip field; on any successfully loaded instance it is always == CHECKPOINT_VERSION
    # (the loader is the enforcement point — a constructed mismatch is never returned by load).
    version: int = CHECKPOINT_VERSION


def save_checkpoint(
    path: str | Path,
    *,
    policy: HangarFitPolicy,
    optimizer: torch.optim.Optimizer,
    normalizer: ReturnNormalizer | None,
    policy_kwargs: dict | None,
    completed_stages: Sequence[str],
) -> None:
    """Write a resume checkpoint to ``path`` ATOMICALLY (temp file + ``os.replace``), so a
    crash mid-write can never corrupt an existing checkpoint — the whole point of the per-rung
    ``--checkpoint-out``. (Plain ``torch.save`` truncate-then-writes in place, which would
    leave a corrupt file on an ill-timed crash.)

    ``policy_kwargs`` MUST be the kwargs the policy was constructed with (the architecture) so
    a resume rebuilds a matching-shape policy; ``None`` is stored as ``{}`` (own defaults).
    ``completed_stages`` is the ordered list of rung names already fully trained this ladder."""
    payload = {
        "version": CHECKPOINT_VERSION,
        "policy_kwargs": dict(policy_kwargs or {}),
        "policy_state": policy.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "normalizer_state": normalizer.state_dict() if normalizer is not None else None,
        "completed_stages": list(completed_stages),
    }
    target = str(path)
    # tmp MUST stay in target's directory so os.replace is a same-filesystem atomic rename;
    # the pid suffix avoids two concurrent writers clobbering each other's in-flight temp.
    tmp = f"{target}.{os.getpid()}.tmp"
    try:
        torch.save(payload, tmp)
        os.replace(tmp, target)
    except BaseException:
        # A failed write must not corrupt the existing checkpoint (os.replace never ran) nor
        # leave stray temp litter that masks the real cause (disk full / permission).
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load_checkpoint(path: str | Path) -> TrainingCheckpoint:
    """Read a resume checkpoint written by :func:`save_checkpoint`. Loaded with
    ``weights_only=True`` (no arbitrary-code deserialization) onto CPU; the caller moves the
    reconstructed policy to its device. Raises ValueError on a version mismatch."""
    payload = torch.load(str(path), map_location="cpu", weights_only=True)
    if not isinstance(payload, dict):
        raise ValueError(
            f"checkpoint {path!s} is not a resume checkpoint (got {type(payload).__name__}); "
            f"did you pass a bare --save state_dict? Use the file written by --checkpoint-out"
        )
    version = payload.get("version")
    if version != CHECKPOINT_VERSION:
        raise ValueError(
            f"checkpoint version {version!r} != supported {CHECKPOINT_VERSION} "
            f"(re-run from scratch or migrate the checkpoint)"
        )
    missing = [k for k in _REQUIRED_KEYS if k not in payload]
    if missing:
        raise ValueError(
            f"checkpoint {path!s} is missing required keys {missing} "
            f"(corrupt or truncated resume checkpoint — re-run from scratch)"
        )
    return TrainingCheckpoint(
        policy_kwargs=dict(payload["policy_kwargs"]),
        policy_state=payload["policy_state"],
        optimizer_state=payload["optimizer_state"],
        normalizer_state=payload["normalizer_state"],
        completed_stages=list(payload["completed_stages"]),
        version=version,
    )
