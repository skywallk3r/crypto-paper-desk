"""
Risk controls: position sizing, weekend size-down, daily-loss kill switch.

Desk intake:
- Paper capital $10,000
- Max risk $200 (2%) per trade
- Kill switch: 2% daily loss
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class SizeResult:
    qty: float
    risk_usd: float
    notional_usd: float
    weekend_scaled: bool
    reason: str


def is_weekend(now: datetime | None = None) -> bool:
    """UTC Saturday/Sunday — crypto trades 24/7 but weekend books are thinner."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.weekday() >= 5  # Sat=5, Sun=6


def position_size(
    *,
    entry: float,
    stop: float,
    capital_usd: float,
    max_risk_usd: float,
    weekend_book_fraction: float = 0.60,
    now: datetime | None = None,
) -> SizeResult:
    """
    units = min(max_risk / stop_dist, capital / entry), then weekend scale.

    Capital often binds before the $200 risk ceiling on tight ATR stops.
    """
    if entry <= 0 or stop <= 0:
        return SizeResult(0.0, 0.0, 0.0, False, "invalid_entry_or_stop")

    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return SizeResult(0.0, 0.0, 0.0, False, "zero_stop_distance")

    risk_units = max_risk_usd / stop_dist
    capital_units = capital_usd / entry
    qty = min(risk_units, capital_units)

    weekend = is_weekend(now)
    if weekend:
        qty *= weekend_book_fraction

    # Round down to a sensible crypto precision (Alpaca BTC often 1e-6–1e-9;
    # we use 6 dp for paper clarity).
    qty = float(int(qty * 1_000_000) / 1_000_000)
    notional = qty * entry
    risk = qty * stop_dist

    if qty <= 0:
        return SizeResult(0.0, 0.0, 0.0, weekend, "qty_rounded_to_zero")

    reason = "ok_weekend_scaled" if weekend else "ok"
    return SizeResult(qty, risk, notional, weekend, reason)


@dataclass
class KillSwitchState:
    """Tracks realized + marked P&L for the UTC day."""

    day_start_equity: float
    realized_pnl: float = 0.0

    def daily_loss_pct(self, current_equity: float) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return (self.day_start_equity - current_equity) / self.day_start_equity

    def tripped(self, current_equity: float, kill_pct: float) -> bool:
        """True when drawdown from day-start equity meets/exceeds kill_pct."""
        return self.daily_loss_pct(current_equity) >= kill_pct
