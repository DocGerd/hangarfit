"""Cold-joint policy network (sub-project #3, epic #607). torch nn.Module:
CNN(raster) + masked self-attention(tokens) -> active-object embedding ->
legal-mask-gated (kind,gear) head + K-way magnitude head + value head, plus the
ObservationTensors -> batched-tensor adapter. Requires the [train] extra (torch)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import torch
from torch import Tensor, nn

from ml import action_space
from ml.action_space import MAGNITUDE_DIM
from ml.encoding import ACTION_DIM, RASTER_CHANNELS, TOKEN_DIM, ObservationTensors
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


def to_batch(obs: Sequence[ObservationTensors]) -> dict[str, Tensor]:
    """Stack a list of ObservationTensors into batched torch tensors. The only
    torch seam on the input side (ml/encoding.py stays numpy)."""
    return {
        "raster": torch.from_numpy(np.stack([o.raster for o in obs])),
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
    ) -> None:
        super().__init__()
        convs: list[nn.Module] = []
        in_ch = RASTER_CHANNELS
        for ch in cnn_channels:
            convs += [nn.Conv2d(in_ch, ch, kernel_size=3, stride=2, padding=1), nn.ReLU()]
            in_ch = ch
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
        self.value_head = nn.Sequential(
            nn.Linear(2 * d_model, d_model), nn.ReLU(), nn.Linear(d_model, 1)
        )

    def forward(self, batch: dict[str, Tensor]) -> PolicyOutput:
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
