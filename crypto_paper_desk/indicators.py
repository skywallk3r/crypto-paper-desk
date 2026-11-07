"""
Technical indicators used by Rank #1 (RSI, ATR, VWAP, flush mid).

Pure functions over OHLCV sequences — no I/O. Bars are dict-like with keys:
open, high, low, close, volume (and optional timestamp).
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


Bar = dict  # typed loosely so adapters can pass SDK objects via .__dict__ / mapping


def _closes(bars: Sequence[Bar]) -> np.ndarray:
    return np.array([float(b["close"]) for b in bars], dtype=float)


def _highs(bars: Sequence[Bar]) -> np.ndarray:
    return np.array([float(b["high"]) for b in bars], dtype=float)


def _lows(bars: Sequence[Bar]) -> np.ndarray:
    return np.array([float(b["low"]) for b in bars], dtype=float)


def rsi(bars: Sequence[Bar], period: int = 14) -> float | None:
    """Wilder RSI on close. Returns None until enough bars exist."""
    if len(bars) < period + 1:
        return None
    closes = _closes(bars)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def atr(bars: Sequence[Bar], period: int = 14) -> float | None:
    """Average True Range (Wilder). Returns None until enough bars exist."""
    if len(bars) < period + 1:
        return None
    highs = _highs(bars)
    lows = _lows(bars)
    closes = _closes(bars)
    prev_close = closes[:-1]
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - prev_close), np.abs(lows[1:] - prev_close)),
    )
    # Wilder smoothing
    atr_val = tr[:period].mean()
    for i in range(period, len(tr)):
        atr_val = (atr_val * (period - 1) + tr[i]) / period
    return float(atr_val)


def vwap(bars: Sequence[Bar]) -> float | None:
    """
    Session / window VWAP over the provided bars.

    For Rank #1 we pass the last ~1h of 15m bars (typically 4 bars) so this is
    effectively a 1h VWAP of the recent window — not a full exchange session VWAP.
    """
    if not bars:
        return None
    typical = []
    volumes = []
    for b in bars:
        h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        vol = float(b.get("volume") or 0.0)
        typical.append((h + l + c) / 3.0)
        volumes.append(vol)
    vols = np.array(volumes, dtype=float)
    typs = np.array(typical, dtype=float)
    if vols.sum() <= 0:
        return float(typs.mean())
    return float(np.sum(typs * vols) / vols.sum())


def flush_range_mid(bars: Sequence[Bar]) -> tuple[float, float, float] | None:
    """
    Mid of the high/low range over the provided bars (flush window).

    Returns (mid, flush_high, flush_low) or None.
    """
    if not bars:
        return None
    high = float(max(float(b["high"]) for b in bars))
    low = float(min(float(b["low"]) for b in bars))
    mid = (high + low) / 2.0
    return mid, high, low


def recent_swing_extreme(bars: Sequence[Bar], side: str = "low") -> float | None:
    """Post-flush swing extreme used for stop placement."""
    if not bars:
        return None
    if side == "low":
        return float(min(float(b["low"]) for b in bars))
    return float(max(float(b["high"]) for b in bars))
