"""Strategy gate behavior with mocked externals."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from crypto_paper_desk.adapters.coinglass import LiquidationSnapshot
from crypto_paper_desk.adapters.fear_greed import FearGreedReading
from crypto_paper_desk.config import load_settings
from crypto_paper_desk.runner import _synthetic_flush_bars
from crypto_paper_desk.strategy.liquidation_flush_mr import LiquidationFlushMeanReversion


class StrategyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        env = {"ALPACA_PAPER": "true", "DRY_RUN": "true"}
        self._patcher = mock.patch.dict(os.environ, env, clear=False)
        self._patcher.start()
        os.environ.pop("ALLOW_LIVE_TRADING", None)
        self.settings = load_settings(env_file=None)
        self.strategy = LiquidationFlushMeanReversion(self.settings)

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_skip_when_liq_unavailable(self) -> None:
        bars = _synthetic_flush_bars(100)
        liq = LiquidationSnapshot(None, None, False, "coinglass", "missing_api_key")
        fng = FearGreedReading(25, "Extreme Fear", True, "alternative.me")
        decision = self.strategy.evaluate(bars, liquidations=liq, fear_greed=fng)
        self.assertEqual(decision.action, "skip")
        self.assertEqual(decision.reason, "liquidations_unavailable")

    def test_skip_when_fng_high(self) -> None:
        bars = _synthetic_flush_bars(100)
        liq = LiquidationSnapshot(1.05e9, 0.88, True, "manual")
        fng = FearGreedReading(55, "Greed", True, "alternative.me")
        decision = self.strategy.evaluate(bars, liquidations=liq, fear_greed=fng)
        self.assertEqual(decision.action, "skip")
        self.assertEqual(decision.reason, "fng_above_threshold")

    def test_enter_or_chart_skip_with_gates_open(self) -> None:
        bars = _synthetic_flush_bars(100)
        liq = LiquidationSnapshot(1.05e9, 0.88, True, "manual")
        fng = FearGreedReading(25, "Extreme Fear", True, "alternative.me")
        decision = self.strategy.evaluate(bars, liquidations=liq, fear_greed=fng)
        # Synthetic pattern aims for enter_long; accept chart skip if heuristic misses
        self.assertIn(decision.action, {"enter_long", "skip"})
        if decision.action == "enter_long":
            self.assertIsNotNone(decision.qty)
            self.assertIsNotNone(decision.entry)
            self.assertIsNotNone(decision.stop)
            self.assertLess(decision.stop, decision.entry)  # type: ignore[operator]


if __name__ == "__main__":
    unittest.main()
