"""
Rank #1 — BTC Liquidation-Flush Mean Reversion (15m → 1–4h)

Thesis (from morning brief 2026-11-07):
After a long-dominated liquidation cascade (≥$1B 24h, longs ≫ shorts) and
Extreme Fear, fade the first *failed* extension away from the post-flush
equilibrium back toward the 1h VWAP / mid of the flush range — do NOT blindly
buy fear.

Triggers (all required unless noted):
1. External liquidations gate: 24h ≥ $1B AND longs ≥ ~80%
2. Fear & Greed ≤ 30
3. Chart: 15m RSI recovery from <30  OR  reclaim of flush mid after a
   lower-high reject of the bounce high

Entry: limit toward 1h VWAP / flush mid; no chase >0.5×ATR(14,15m) through mid
Stop: 1.5×ATR beyond post-flush swing extreme OR hard invalidation under flush
      low — whichever is tighter
Exit: 1.0–1.5R at VWAP/mid, or time-stop 4h (≤3h weekend)
Size: min($200/stop_dist, $10k/entry); weekend 50–70% of book
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from crypto_paper_desk.adapters.coinglass import LiquidationSnapshot
from crypto_paper_desk.adapters.fear_greed import FearGreedReading
from crypto_paper_desk.config import Settings
from crypto_paper_desk import indicators
from crypto_paper_desk.risk import position_size


@dataclass
class SignalDecision:
    action: str  # enter_long | hold | skip | flatten_time
    reason: str
    symbol: str
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    qty: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LiquidationFlushMeanReversion:
    """Stateful evaluator for one scan cycle (stateless across restarts)."""

    name = "btc_liquidation_flush_mean_reversion"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(
        self,
        bars_15m: Sequence[dict[str, Any]],
        *,
        liquidations: LiquidationSnapshot,
        fear_greed: FearGreedReading,
        now: datetime | None = None,
    ) -> SignalDecision:
        now = now or datetime.now(timezone.utc)
        symbol = self.settings.symbol
        s = self.settings

        meta: dict[str, Any] = {
            "liq_available": liquidations.available,
            "liq_total": liquidations.total_usd_24h,
            "liq_long_pct": liquidations.long_pct,
            "liq_source": liquidations.source,
            "liq_detail": liquidations.detail,
            "fng_available": fear_greed.available,
            "fng_value": fear_greed.value,
            "fng_class": fear_greed.classification,
            "fng_detail": fear_greed.detail,
            "bars": len(bars_15m),
        }

        # --- External gates ---
        if not liquidations.available:
            return SignalDecision(
                "skip",
                "liquidations_unavailable",
                symbol,
                meta=meta,
            )
        if not liquidations.passes_gate(s.liquidation_min_usd, s.liquidation_long_pct_min):
            return SignalDecision(
                "skip",
                "liquidations_gate_failed",
                symbol,
                meta=meta,
            )

        if not fear_greed.available or fear_greed.value is None:
            return SignalDecision("skip", "fng_unavailable", symbol, meta=meta)
        if fear_greed.value > s.fng_max:
            return SignalDecision("skip", "fng_above_threshold", symbol, meta=meta)

        if len(bars_15m) < max(s.atr_period + 5, 30):
            return SignalDecision("skip", "insufficient_bars", symbol, meta=meta)

        atr_val = indicators.atr(bars_15m, s.atr_period)
        rsi_series_ok = indicators.rsi(bars_15m, 14)
        if atr_val is None or rsi_series_ok is None:
            return SignalDecision("skip", "indicators_not_ready", symbol, meta=meta)

        # Flush window: last ~24×1h ≈ last 96×15m bars (or whatever we have, capped)
        flush_window = list(bars_15m[-96:]) if len(bars_15m) >= 20 else list(bars_15m)
        mid_pack = indicators.flush_range_mid(flush_window)
        if mid_pack is None:
            return SignalDecision("skip", "no_flush_range", symbol, meta=meta)
        flush_mid, flush_high, flush_low = mid_pack

        # 1h VWAP ≈ last 4×15m bars
        vwap_1h = indicators.vwap(list(bars_15m[-4:]))
        if vwap_1h is None:
            return SignalDecision("skip", "vwap_unavailable", symbol, meta=meta)

        # Equilibrium reference: average of VWAP and flush mid (desk choice)
        equilibrium = (vwap_1h + flush_mid) / 2.0
        last = bars_15m[-1]
        last_close = float(last["close"])

        # RSI recovery from <30: look back for an RSI print < oversold, then
        # current RSI back above oversold.
        rsi_now = rsi_series_ok
        rsi_recovered = self._rsi_recovered_from_oversold(bars_15m, s.rsi_oversold)

        # Lower-high reject of bounce high then reclaim of flush mid
        reclaim = self._reclaim_after_lower_high_reject(
            bars_15m, flush_mid, flush_high
        )

        meta.update(
            {
                "atr": atr_val,
                "rsi": rsi_now,
                "rsi_recovered": rsi_recovered,
                "reclaim": reclaim,
                "flush_mid": flush_mid,
                "flush_high": flush_high,
                "flush_low": flush_low,
                "vwap_1h": vwap_1h,
                "equilibrium": equilibrium,
                "last_close": last_close,
            }
        )

        if not (rsi_recovered or reclaim):
            return SignalDecision(
                "skip", "chart_trigger_not_met", symbol, meta=meta
            )

        # No chase: price must not be >0.5×ATR through mid/equilibrium
        chase_limit = equilibrium + s.chase_atr_mult * atr_val
        if last_close > chase_limit:
            meta["chase_limit"] = chase_limit
            return SignalDecision("skip", "chase_too_far", symbol, meta=meta)

        entry = min(equilibrium, last_close)  # limit toward mid — do not chase
        # Prefer limit at equilibrium if below last; else at last if we're under mid
        if last_close >= equilibrium:
            entry = equilibrium
        else:
            entry = last_close  # already discounted toward mid

        swing_low = indicators.recent_swing_extreme(flush_window, "low") or flush_low
        atr_stop = swing_low - s.atr_stop_mult * atr_val
        # Hard invalidation under flush low — tighter of the two vs thesis
        # "whichever is tighter" for a long = higher stop price
        hard_inval = flush_low * 0.999  # slight buffer under flush low
        stop = max(atr_stop, hard_inval)

        # If stop ends up above entry (pathological), skip
        if stop >= entry:
            return SignalDecision("skip", "stop_above_entry", symbol, meta=meta)

        stop_dist = entry - stop
        target_r = (s.target_r_min + s.target_r_max) / 2.0
        target = entry + target_r * stop_dist
        # Prefer taking profit near VWAP/mid if that is closer than full R target
        near_eq = max(vwap_1h, flush_mid)
        if entry < near_eq < target:
            target = near_eq

        sizing = position_size(
            entry=entry,
            stop=stop,
            capital_usd=s.paper_capital_usd,
            max_risk_usd=s.max_risk_per_trade_usd,
            weekend_book_fraction=s.weekend_book_fraction,
            now=now,
        )
        meta["sizing"] = {
            "qty": sizing.qty,
            "risk_usd": sizing.risk_usd,
            "notional_usd": sizing.notional_usd,
            "weekend_scaled": sizing.weekend_scaled,
            "reason": sizing.reason,
        }

        if sizing.qty <= 0:
            return SignalDecision("skip", f"size_{sizing.reason}", symbol, meta=meta)

        return SignalDecision(
            action="enter_long",
            reason="gates_and_chart_ok",
            symbol=symbol,
            entry=round(entry, 2),
            stop=round(stop, 2),
            target=round(target, 2),
            qty=sizing.qty,
            meta=meta,
        )

    def _rsi_recovered_from_oversold(
        self, bars: Sequence[dict[str, Any]], oversold: float
    ) -> bool:
        """True if a recent bar had RSI < oversold and current RSI >= oversold."""
        # Compute RSI on expanding windows for last N bars (expensive but N~30)
        lookback = min(40, len(bars) - 15)
        if lookback < 5:
            return False
        values: list[float] = []
        for i in range(len(bars) - lookback, len(bars) + 1):
            if i < 15:
                continue
            val = indicators.rsi(bars[:i], 14)
            if val is not None:
                values.append(val)
        if len(values) < 3:
            return False
        had_oversold = any(v < oversold for v in values[:-1])
        return had_oversold and values[-1] >= oversold

    def _reclaim_after_lower_high_reject(
        self,
        bars: Sequence[dict[str, Any]],
        flush_mid: float,
        flush_high: float,
    ) -> bool:
        """
        Heuristic: bounce makes a high below flush_high (lower high), rejects,
        then a later close reclaims flush_mid.
        """
        if len(bars) < 10:
            return False
        window = list(bars[-20:])
        highs = [float(b["high"]) for b in window]
        closes = [float(b["close"]) for b in window]

        bounce_high_idx = max(range(len(highs)), key=lambda i: highs[i])
        bounce_high = highs[bounce_high_idx]
        # Lower-high vs flush high (failed extension)
        if bounce_high >= flush_high:
            return False
        # Rejection: a bar after the bounce high closes below its open-ish /
        # or subsequent high is lower
        after = window[bounce_high_idx + 1 :]
        if not after:
            return False
        rejected = any(float(b["close"]) < float(b["open"]) for b in after[:3]) or (
            len(after) >= 2 and float(after[0]["high"]) < bounce_high
        )
        if not rejected:
            return False
        # Reclaim mid on a later close
        reclaim_closes = closes[bounce_high_idx + 1 :]
        return any(c >= flush_mid for c in reclaim_closes)
