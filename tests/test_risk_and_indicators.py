"""Risk sizing + indicator smoke tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from crypto_paper_desk import indicators
from crypto_paper_desk.risk import KillSwitchState, is_weekend, position_size


def _bars_trending(n: int = 50, start: float = 100.0, step: float = -1.0):
    bars = []
    px = start
    for i in range(n):
        o = px
        c = px + step
        bars.append(
            {
                "open": o,
                "high": max(o, c) + 1,
                "low": min(o, c) - 1,
                "close": c,
                "volume": 10,
            }
        )
        px = c
    return bars


class RiskTests(unittest.TestCase):
    def test_size_capital_binds(self) -> None:
        # Tight stop → risk units large → capital binds
        result = position_size(
            entry=75_000,
            stop=74_889,  # ~111 stop dist like the brief
            capital_usd=10_000,
            max_risk_usd=200,
            weekend_book_fraction=1.0,
            now=datetime(2026, 11, 6, tzinfo=timezone.utc),  # Friday
        )
        self.assertGreater(result.qty, 0)
        self.assertLessEqual(result.notional_usd, 10_000 + 1e-6)
        self.assertLessEqual(result.risk_usd, 200 + 1e-6)

    def test_weekend_scales_down(self) -> None:
        weekday = position_size(
            entry=75_000,
            stop=74_000,
            capital_usd=10_000,
            max_risk_usd=200,
            weekend_book_fraction=0.6,
            now=datetime(2026, 11, 6, tzinfo=timezone.utc),
        )
        weekend = position_size(
            entry=75_000,
            stop=74_000,
            capital_usd=10_000,
            max_risk_usd=200,
            weekend_book_fraction=0.6,
            now=datetime(2026, 11, 7, tzinfo=timezone.utc),  # Saturday
        )
        self.assertTrue(is_weekend(datetime(2026, 11, 7, tzinfo=timezone.utc)))
        self.assertAlmostEqual(weekend.qty, weekday.qty * 0.6, places=6)

    def test_kill_switch(self) -> None:
        ks = KillSwitchState(day_start_equity=10_000)
        self.assertFalse(ks.tripped(9_900, 0.02))
        self.assertTrue(ks.tripped(9_800, 0.02))


class IndicatorTests(unittest.TestCase):
    def test_atr_rsi_vwap_smoke(self) -> None:
        bars = _bars_trending(80)
        self.assertIsNotNone(indicators.atr(bars, 14))
        self.assertIsNotNone(indicators.rsi(bars, 14))
        self.assertIsNotNone(indicators.vwap(bars[-4:]))
        mid = indicators.flush_range_mid(bars)
        self.assertIsNotNone(mid)
        assert mid is not None
        self.assertGreater(mid[1], mid[2])  # high > low


if __name__ == "__main__":
    unittest.main()
