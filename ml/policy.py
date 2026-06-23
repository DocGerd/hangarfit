"""Cold-joint policy network (sub-project #3, epic #607). torch nn.Module:
CNN(raster) + masked self-attention(tokens) -> active-object embedding ->
legal-mask-gated (kind,gear) head + K-way magnitude head + value head, plus the
ObservationTensors -> batched-tensor adapter. Requires the [train] extra (torch)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import torch
from torch import Tensor, nn

from ml import action_space
from ml.action_space import MAGNITUDE_DIM
from ml.encoding import (
    ACTION_DIM,
    RASTER_CHANNELS,
    TOKEN_DIM,
    ObservationTensors,
    reassemble_raster,
)
from ml.types import Park, Primitive


@dataclass(frozen=True, slots=True)
class PolicyOutput:
    """One forward pass: the policy logits + scalar value, batch-first.

    ``kind_gear_logits`` covers the ACTION_DIM=9 canonical action slots: indices 0-7
    are the ``(kind, gear)`` movement actions in ``encoding._CANONICAL_ACTIONS`` order
    and index PARK_INDEX=8 is PARK. Illegal slots (per the per-observation legal mask)
    are set to ``-inf`` so they vanish under softmax. ``magnitude_bin_logits`` is the
    K-way (MAGNITUDE_DIM=5) magnitude head; ``value`` is the per-sample scalar critic.
    """

    kind_gear_logits: Tensor  # (B, ACTION_DIM) — illegal slots are -inf
    magnitude_bin_logits: Tensor  # (B, MAGNITUDE_DIM)
    value: Tensor  # (B,)

    def __post_init__(self) -> None:
        assert self.kind_gear_logits.shape[-1] == ACTION_DIM
        assert self.magnitude_bin_logits.shape[-1] == MAGNITUDE_DIM
        assert self.kind_gear_logits.shape[0] == self.value.shape[0]


def _sincos_pos_2d(h: int, w: int, d_model: int) -> Tensor:
    """Fixed (non-learned) 2D sin/cos positional encoding for an ``h × w`` grid,
    flattened ROW-MAJOR to ``(h*w, d_model)`` so cell index ``row*w + col`` matches
    ``Tensor.flatten(2)``. The first ``d_model/2`` channels encode the row, the second
    half the column. Deterministic (no RNG). Requires ``d_model % 4 == 0``."""
    if d_model % 4 != 0:
        raise ValueError(f"_sincos_pos_2d needs d_model % 4 == 0, got {d_model}")
    d_half = d_model // 2

    def _1d(length: int, dim: int) -> Tensor:
        pos = torch.arange(length, dtype=torch.float32).unsqueeze(1)  # (length, 1)
        idx = torch.arange(0, dim, 2, dtype=torch.float32)  # (dim/2,)
        div = torch.exp(-math.log(10000.0) * idx / dim)  # (dim/2,)
        out = torch.zeros(length, dim, dtype=torch.float32)
        out[:, 0::2] = torch.sin(pos * div)
        out[:, 1::2] = torch.cos(pos * div)
        return out

    row_pe = _1d(h, d_half)  # (h, d_half)
    col_pe = _1d(w, d_half)  # (w, d_half)
    grid = torch.zeros(h, w, d_model, dtype=torch.float32)
    grid[:, :, :d_half] = row_pe.unsqueeze(1)  # row varies along dim 0
    grid[:, :, d_half:] = col_pe.unsqueeze(0)  # col varies along dim 1
    return grid.reshape(h * w, d_model)  # row-major flatten


def to_batch(
    obs: Sequence[ObservationTensors], *, static_block: np.ndarray | None = None
) -> dict[str, Tensor]:
    """Stack a list of ObservationTensors into batched torch tensors. The only
    torch seam on the input side (ml/encoding.py stays numpy).

    #752: vectorized-rollout obs carry ONLY the DYNAMIC raster channels, as uint8 — the
    worker drops the static block (oob/bay/apron/door) from the wire. The dtype is the
    discriminator: when the rasters are uint8 we re-prepend the rung's cached ``static_block``
    (shape ``(STATIC_CHANNELS, H, W)``) and widen to float32 via :func:`reassemble_raster`,
    reproducing the full :func:`encode` raster bit-for-bit. Full float32 obs — every
    non-vectorized caller — pass straight through, so ``static_block`` is required ONLY for
    trimmed obs (and is harmlessly ignored otherwise)."""
    trimmed = obs[0].raster.dtype == np.uint8
    if any((o.raster.dtype == np.uint8) != trimmed for o in obs):
        raise ValueError(
            "to_batch received a mix of full (float32) and trimmed (uint8) rasters; a batch "
            "must be uniformly one or the other (the vec rollout is all-trimmed, every other "
            "caller all-full). A mixed batch would reassemble a full obs into a wrong-shaped "
            "raster."
        )
    if trimmed:
        if static_block is None:
            raise ValueError(
                "to_batch got uint8 (dynamic-only) rasters but no static_block to re-prepend "
                "— the vectorized rollout must pass the rung's cached static block "
                "(encoding.static_block / reassemble_raster, #752)."
            )
        raster = np.stack([reassemble_raster(static_block, o.raster) for o in obs])
    else:
        raster = np.stack([o.raster for o in obs])
    return {
        "raster": torch.from_numpy(raster),
        "tokens": torch.from_numpy(np.stack([o.tokens for o in obs])),
        "token_mask": torch.from_numpy(np.stack([o.token_mask for o in obs])),
        "active_index": torch.tensor([o.active_index for o in obs], dtype=torch.long),
        "legal_action_mask": torch.from_numpy(np.stack([o.legal_action_mask for o in obs])),
    }


class HangarFitPolicy(nn.Module):
    """Policy + value network over ObservationTensors. Acts on the active object:
    a legal-mask-gated (kind,gear) head + a K-way magnitude head + a scalar value."""

    def __init__(
        self,
        *,
        d_model: int = 128,
        n_layers: int = 2,
        n_heads: int = 4,
        cnn_channels: tuple[int, ...] = (16, 32, 64),
        spatial_tokens: bool = False,
    ) -> None:
        super().__init__()
        self.spatial_tokens = spatial_tokens
        convs: list[nn.Module] = []
        in_ch = RASTER_CHANNELS
        for ch in cnn_channels:
            convs += [nn.Conv2d(in_ch, ch, kernel_size=3, stride=2, padding=1), nn.ReLU()]
            in_ch = ch
        if spatial_tokens:
            # keep the feature MAP (B, C, H, W); the spatial path consumes it directly
            self.cnn = nn.Sequential(*convs)
        else:
            self.cnn = nn.Sequential(
                *convs, nn.AdaptiveAvgPool2d(1), nn.Flatten()
            )  # (B, cnn_channels[-1])
        self.cnn_proj = nn.Linear(cnn_channels[-1], d_model)  # g: -> D
        self.token_proj = nn.Linear(TOKEN_DIM, d_model)  # tokens 24 -> D
        self.fuse = nn.Linear(2 * d_model, d_model)  # concat(token, g) 2D -> D
        layer = nn.TransformerEncoderLayer(
            d_model, n_heads, dim_feedforward=4 * d_model, batch_first=True
        )
        self.encoder = nn.TransformerEncoder(layer, n_layers)
        self.kind_head = nn.Sequential(
            nn.Linear(d_model, d_model), nn.ReLU(), nn.Linear(d_model, ACTION_DIM)
        )
        self.mag_head = nn.Sequential(
            nn.Linear(d_model, d_model), nn.ReLU(), nn.Linear(d_model, MAGNITUDE_DIM)
        )
        value_in = 3 * d_model if spatial_tokens else 2 * d_model
        self.value_head = nn.Sequential(
            nn.Linear(value_in, d_model), nn.ReLU(), nn.Linear(d_model, 1)
        )
        # ON-only modules are registered LAST so the OFF branch's module-registration order
        # (and therefore its param-init RNG stream + state_dict keys) is byte-identical to today.
        if spatial_tokens:
            self.spatial_proj = nn.Linear(cnn_channels[-1], d_model)  # per-cell -> D

    def forward(self, batch: dict[str, Tensor]) -> PolicyOutput:
        if self.spatial_tokens:
            return self._forward_spatial(batch)
        raster, tokens = batch["raster"], batch["tokens"]
        token_mask, active_index = batch["token_mask"], batch["active_index"]
        legal = batch["legal_action_mask"]
        g = self.cnn_proj(self.cnn(raster))  # (B, D)
        tok = self.token_proj(tokens)  # (B, N, D)
        g_b = g.unsqueeze(1).expand(-1, tok.shape[1], -1)  # (B, N, D)
        fused = self.fuse(torch.cat([tok, g_b], dim=-1))  # (B, N, D)
        emb = self.encoder(fused, src_key_padding_mask=~token_mask)  # (B, N, D)
        idx = active_index.clamp(min=0).view(-1, 1, 1).expand(-1, 1, emb.shape[-1])
        active_emb = emb.gather(1, idx).squeeze(1)  # (B, D)
        m = token_mask.unsqueeze(-1).to(emb.dtype)  # (B, N, 1)
        pooled = (emb * m).sum(1) / m.sum(1).clamp(min=1.0)  # (B, D)
        kind_logits = self.kind_head(active_emb).masked_fill(~legal, float("-inf"))
        mag_logits = self.mag_head(active_emb)
        value = self.value_head(torch.cat([pooled, g], dim=-1)).squeeze(-1)
        return PolicyOutput(kind_logits, mag_logits, value)

    def _forward_spatial(self, batch: dict[str, Tensor]) -> PolicyOutput:
        """ON-branch forward: object tokens cross-attend to per-cell spatial tokens.
        ``g = feat.mean((2,3))`` reproduces the old AdaptiveAvgPool2d(1)+Flatten exactly;
        the spatial tokens + a critic spatial summary are purely additive."""
        raster, tokens = batch["raster"], batch["tokens"]
        token_mask, active_index = batch["token_mask"], batch["active_index"]
        legal = batch["legal_action_mask"]
        feat = self.cnn(raster)  # (B, C, H, W) — no pool/flatten on this branch
        h, w = feat.shape[2], feat.shape[3]
        g = self.cnn_proj(feat.mean(dim=(2, 3)))  # (B, D) == AdaptiveAvgPool2d(1)+Flatten
        pos = _sincos_pos_2d(h, w, g.shape[-1]).to(feat.device, feat.dtype)  # (H*W, D)
        sp = self.spatial_proj(feat.flatten(2).mT) + pos  # (B, H*W, D)
        tok = self.token_proj(tokens)  # (B, N, D)
        n_obj = tok.shape[1]
        g_b = g.unsqueeze(1).expand(-1, n_obj, -1)  # (B, N, D)
        fused_obj = self.fuse(torch.cat([tok, g_b], dim=-1))  # (B, N, D)
        seq = torch.cat([fused_obj, sp], dim=1)  # (B, N + H*W, D)
        sp_pad = torch.zeros(sp.shape[0], sp.shape[1], dtype=torch.bool, device=sp.device)
        pad = torch.cat([~token_mask, sp_pad], dim=1)  # (B, N + H*W); spatial rows valid
        emb = self.encoder(seq, src_key_padding_mask=pad)  # (B, N + H*W, D)
        emb_obj = emb[:, :n_obj, :]  # (B, N, D)
        idx = active_index.clamp(min=0).view(-1, 1, 1).expand(-1, 1, emb_obj.shape[-1])
        active_emb = emb_obj.gather(1, idx).squeeze(1)  # (B, D)
        m = token_mask.unsqueeze(-1).to(emb.dtype)  # (B, N, 1)
        pooled_obj = (emb_obj * m).sum(1) / m.sum(1).clamp(min=1.0)  # (B, D)
        pooled_spatial = emb[:, n_obj:, :].mean(dim=1)  # (B, D) — all spatial rows valid
        kind_logits = self.kind_head(active_emb).masked_fill(~legal, float("-inf"))
        mag_logits = self.mag_head(active_emb)
        value = self.value_head(torch.cat([pooled_obj, g, pooled_spatial], dim=-1)).squeeze(-1)
        return PolicyOutput(kind_logits, mag_logits, value)

    @torch.no_grad()
    def act(
        self, obs: ObservationTensors, *, turn_radius_m: float, deterministic: bool = False
    ) -> tuple[tuple[int, int], float, Primitive | Park]:
        """Sample a masked (kind_gear, mag_bin) for a single live observation; return
        the indices, the joint log-prob, and the decoded Primitive | Park. Only ever
        returns a legal (kind,gear) (illegal logits are -inf).

        Preconditions: ``obs`` is a single, live observation (``active_index >= 0``) —
        a terminal observation has an all-False legal mask, which would make the kind
        ``Categorical`` collapse to NaN. ``deterministic=True`` requires ``eval()`` mode:
        in ``train()`` the encoder dropout makes the argmax non-reproducible, so it is
        rejected. (Stochastic sampling in ``train()`` is fine and not blocked.)
        """
        if deterministic and self.training:
            raise RuntimeError(
                "deterministic act() requires eval() mode (dropout is active in train())"
            )
        if obs.active_index < 0:
            raise ValueError(
                "act() called on a terminal observation (active_index < 0); the policy "
                "is only valid on live observations"
            )
        out = self(to_batch([obs]))
        kind_dist = torch.distributions.Categorical(logits=out.kind_gear_logits)
        mag_dist = torch.distributions.Categorical(logits=out.magnitude_bin_logits)
        if deterministic:
            kind_idx = out.kind_gear_logits.argmax(-1)
            mag_idx = out.magnitude_bin_logits.argmax(-1)
        else:
            kind_idx = kind_dist.sample()
            mag_idx = mag_dist.sample()
        log_prob = float(kind_dist.log_prob(kind_idx) + mag_dist.log_prob(mag_idx))
        decoded = action_space.decode(int(kind_idx), int(mag_idx), turn_radius_m=turn_radius_m)
        return (int(kind_idx), int(mag_idx)), log_prob, decoded
