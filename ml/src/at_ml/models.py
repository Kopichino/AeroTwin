"""RUL model architectures (Doc 07 section 7.3).

Four deep models plus baselines, all sharing the contract ``(B, W, F) -> (B,)``
so the trainer, evaluator and exporter are architecture-agnostic and the
comparison is genuinely like-for-like.

C-MAPSS is small (20k-60k windows), so every model here is deliberately
constrained and heavily regularised. Risk R3 in Doc 00 is overfitting, and the
mitigation is capacity discipline rather than early stopping alone.
"""

from __future__ import annotations

import math
from typing import Literal, cast

import torch
from torch import Tensor, nn

Architecture = Literal["lstm", "tcn", "transformer", "informer", "cnn", "mlp"]


class MeanPool(nn.Module):
    """Average over the time axis."""

    def forward(self, x: Tensor) -> Tensor:
        return x.mean(dim=1)


class AttentionPool(nn.Module):
    """Learned attention pooling over timesteps.

    Preferred to taking the last hidden state: degradation evidence is spread
    across the window, and a learned query lets the model weight the cycles that
    actually carry signal.
    """

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.score = nn.Linear(dim, 1)

    def forward(self, x: Tensor) -> Tensor:
        weights = torch.softmax(self.score(x).squeeze(-1), dim=1)
        return torch.einsum("btd,bt->bd", x, weights)


class RULHead(nn.Module):
    """Shared regression head.

    Softplus keeps the output non-negative: a negative remaining life is not a
    physically meaningful prediction, and letting the model emit one wastes
    capacity learning a constraint we can simply impose.
    """

    def __init__(self, dim: int, hidden: int = 64, dropout: float = 0.2) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(dim, hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, 1),
        )

    def forward(self, x: Tensor) -> Tensor:
        return nn.functional.softplus(self.net(x).squeeze(-1))


# ── LSTM ─────────────────────────────────────────────────────────────────────


class LSTMRUL(nn.Module):
    """Bidirectional LSTM with attention pooling. The reference deep model."""

    def __init__(
        self,
        n_features: int,
        hidden: int = 96,
        layers: int = 2,
        dropout: float = 0.3,
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            n_features,
            hidden,
            num_layers=layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if layers > 1 else 0.0,
        )
        self.norm = nn.LayerNorm(hidden * 2)
        self.pool = AttentionPool(hidden * 2)
        self.head = RULHead(hidden * 2, dropout=dropout)

    def forward(self, x: Tensor) -> Tensor:
        out, _ = self.lstm(x)
        return cast(Tensor, self.head(self.pool(self.norm(out))))


# ── Temporal Convolutional Network ───────────────────────────────────────────


class TCNBlock(nn.Module):
    """Dilated causal residual block."""

    def __init__(self, channels: int, dilation: int, kernel: int = 3, dropout: float = 0.2):
        super().__init__()
        self.pad = (kernel - 1) * dilation
        self.conv1 = nn.utils.parametrizations.weight_norm(
            nn.Conv1d(channels, channels, kernel, dilation=dilation)
        )
        self.conv2 = nn.utils.parametrizations.weight_norm(
            nn.Conv1d(channels, channels, kernel, dilation=dilation)
        )
        self.drop = nn.Dropout(dropout)
        self.act = nn.GELU()

    def forward(self, x: Tensor) -> Tensor:
        residual = x
        # Left-pad only: the receptive field must never include future cycles.
        out = self.act(self.conv1(nn.functional.pad(x, (self.pad, 0))))
        out = self.drop(out)
        out = self.act(self.conv2(nn.functional.pad(out, (self.pad, 0))))
        return cast(Tensor, self.drop(out)) + residual


class TCNRUL(nn.Module):
    """Dilated TCN. Fast and very stable on short sequences."""

    def __init__(
        self,
        n_features: int,
        channels: int = 64,
        levels: int = 5,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.project = nn.Conv1d(n_features, channels, 1)
        self.blocks = nn.Sequential(
            *[TCNBlock(channels, 2**level, dropout=dropout) for level in range(levels)]
        )
        self.pool = AttentionPool(channels)
        self.head = RULHead(channels, dropout=dropout)

    def forward(self, x: Tensor) -> Tensor:
        out = self.blocks(self.project(x.transpose(1, 2)))
        return cast(Tensor, self.head(self.pool(out.transpose(1, 2))))


# ── Transformer ──────────────────────────────────────────────────────────────


class PositionalEncoding(nn.Module):
    """Fixed sinusoidal positions.

    Chosen over learned embeddings because the window is short and the training
    set small: learned positions are extra parameters with nothing to learn from.
    """

    def __init__(self, dim: int, max_len: int = 128) -> None:
        super().__init__()
        position = torch.arange(max_len).unsqueeze(1).float()
        div = torch.exp(torch.arange(0, dim, 2).float() * (-math.log(10000.0) / dim))
        encoding = torch.zeros(max_len, dim)
        encoding[:, 0::2] = torch.sin(position * div)
        encoding[:, 1::2] = torch.cos(position * div)
        self.register_buffer("encoding", encoding.unsqueeze(0))

    def forward(self, x: Tensor) -> Tensor:
        encoding = cast(Tensor, self.encoding)
        return x + encoding[:, : x.size(1)]


class TransformerRUL(nn.Module):
    """Pre-LN transformer encoder with a learned CLS token."""

    def __init__(
        self,
        n_features: int,
        d_model: int = 96,
        heads: int = 4,
        layers: int = 2,
        ff: int = 192,
        dropout: float = 0.25,
    ) -> None:
        super().__init__()
        self.project = nn.Linear(n_features, d_model)
        self.pos = PositionalEncoding(d_model)
        self.cls = nn.Parameter(torch.zeros(1, 1, d_model))
        nn.init.trunc_normal_(self.cls, std=0.02)

        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=heads,
            dim_feedforward=ff,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,  # pre-LN trains far more stably at this scale
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=layers)
        self.norm = nn.LayerNorm(d_model)
        self.head = RULHead(d_model, dropout=dropout)

    def forward(self, x: Tensor) -> Tensor:
        out = self.pos(self.project(x))
        cls = self.cls.expand(out.size(0), -1, -1)
        out = self.encoder(torch.cat([cls, out], dim=1))
        return cast(Tensor, self.head(self.norm(out[:, 0])))


# ── Informer-style ───────────────────────────────────────────────────────────


class ProbSparseAttention(nn.Module):
    """ProbSparse self-attention (Informer).

    Scores only the top-u most informative queries, where u = c*ln(L), measuring
    "informative" by how far a query's attention distribution departs from
    uniform. On a 20-30 step window the efficiency win is theoretical; it is
    evaluated here to test whether the sparsity acts as a useful regulariser.
    The report states whatever the result turns out to be.
    """

    def __init__(self, d_model: int, heads: int, factor: int = 5, dropout: float = 0.1):
        super().__init__()
        self.heads = heads
        self.dim = d_model // heads
        self.factor = factor
        self.qkv = nn.Linear(d_model, d_model * 3)
        self.out = nn.Linear(d_model, d_model)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: Tensor) -> Tensor:
        batch, length, _ = x.shape
        qkv = self.qkv(x).view(batch, length, 3, self.heads, self.dim)
        q, k, v = qkv.permute(2, 0, 3, 1, 4)

        scores = torch.einsum("bhqd,bhkd->bhqk", q, k) / math.sqrt(self.dim)

        u = min(length, max(1, int(self.factor * math.log(max(length, 2)))))
        if u < length:
            # Sparsity measurement: max score minus mean score per query.
            sparsity = scores.max(dim=-1).values - scores.mean(dim=-1)
            top = sparsity.topk(u, dim=-1).indices

            mask = torch.zeros(batch, self.heads, length, dtype=torch.bool, device=x.device)
            mask.scatter_(-1, top, True)
            # Uninformative queries fall back to the value mean rather than being
            # dropped, so every position still produces an output.
            attended = torch.softmax(scores, dim=-1) @ v
            fallback = v.mean(dim=2, keepdim=True).expand_as(attended)
            out = torch.where(mask.unsqueeze(-1), attended, fallback)
        else:
            out = torch.softmax(scores, dim=-1) @ v

        out = out.transpose(1, 2).reshape(batch, length, -1)
        return cast(Tensor, self.drop(self.out(out)))


class InformerBlock(nn.Module):
    """ProbSparse attention plus feed-forward, with distilling convolution."""

    def __init__(self, d_model: int, heads: int, ff: int, dropout: float, distil: bool):
        super().__init__()
        self.attn = ProbSparseAttention(d_model, heads, dropout=dropout)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.ff = nn.Sequential(
            nn.Linear(d_model, ff), nn.GELU(), nn.Dropout(dropout), nn.Linear(ff, d_model)
        )
        self.drop = nn.Dropout(dropout)
        self.distil = (
            nn.Sequential(nn.Conv1d(d_model, d_model, 3, padding=1), nn.ELU(), nn.MaxPool1d(2))
            if distil
            else None
        )

    def forward(self, x: Tensor) -> Tensor:
        x = x + self.drop(self.attn(self.norm1(x)))
        x = x + self.drop(self.ff(self.norm2(x)))
        if self.distil is not None and x.size(1) > 2:
            x = self.distil(x.transpose(1, 2)).transpose(1, 2)
        return x


class InformerRUL(nn.Module):
    """Informer-style encoder: ProbSparse attention with progressive distilling."""

    def __init__(
        self,
        n_features: int,
        d_model: int = 96,
        heads: int = 4,
        layers: int = 3,
        ff: int = 192,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.project = nn.Linear(n_features, d_model)
        self.pos = PositionalEncoding(d_model)
        self.blocks = nn.ModuleList(
            [
                InformerBlock(d_model, heads, ff, dropout, distil=(i < layers - 1))
                for i in range(layers)
            ]
        )
        self.norm = nn.LayerNorm(d_model)
        self.pool = AttentionPool(d_model)
        self.head = RULHead(d_model, dropout=dropout)

    def forward(self, x: Tensor) -> Tensor:
        out = self.pos(self.project(x))
        for block in self.blocks:
            out = block(out)
        return cast(Tensor, self.head(self.pool(self.norm(out))))


# ── baselines ────────────────────────────────────────────────────────────────


class CNNRUL(nn.Module):
    """1D CNN baseline. Proves the sequence models earn their complexity."""

    def __init__(self, n_features: int, channels: int = 64, dropout: float = 0.2) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(n_features, channels, 5, padding=2),
            nn.GELU(),
            nn.Conv1d(channels, channels, 3, padding=1),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.head = RULHead(channels, dropout=dropout)

    def forward(self, x: Tensor) -> Tensor:
        return cast(Tensor, self.head(self.net(x.transpose(1, 2)).mean(dim=2)))


class MLPRUL(nn.Module):
    """Flattened-window MLP. The weakest honest baseline."""

    def __init__(self, n_features: int, window: int, hidden: int = 128, dropout: float = 0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Flatten(),
            nn.Linear(n_features * window, hidden),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.head = RULHead(hidden, dropout=dropout)

    def forward(self, x: Tensor) -> Tensor:
        return cast(Tensor, self.head(self.net(x)))


def build_model(architecture: Architecture, n_features: int, window: int) -> nn.Module:
    """Instantiate an architecture by name."""
    if architecture == "lstm":
        return LSTMRUL(n_features)
    if architecture == "tcn":
        return TCNRUL(n_features)
    if architecture == "transformer":
        return TransformerRUL(n_features)
    if architecture == "informer":
        return InformerRUL(n_features)
    if architecture == "cnn":
        return CNNRUL(n_features)
    if architecture == "mlp":
        return MLPRUL(n_features, window)
    raise ValueError(f"unknown architecture: {architecture}")


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)
