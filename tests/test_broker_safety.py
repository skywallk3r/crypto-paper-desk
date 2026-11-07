"""Broker adapter must refuse live order submission from the paper path."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from crypto_paper_desk.adapters.alpaca_client import AlpacaPaperBroker, LiveTradingRefused
from crypto_paper_desk.config import load_settings


class BrokerSafetyTests(unittest.TestCase):
    def test_dry_run_order_does_not_need_client(self) -> None:
        env = {
            "ALPACA_PAPER": "true",
            "DRY_RUN": "true",
            "ALPACA_API_KEY": "",
            "ALPACA_SECRET_KEY": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            settings = load_settings(env_file=None)
            broker = AlpacaPaperBroker(settings)
            result = broker.place_limit_buy(qty=0.01, limit_price=75_000)
            self.assertTrue(result.ok)
            self.assertTrue(result.dry_run)
            self.assertTrue(str(result.order_id).startswith("dryrun-"))

    def test_live_submit_refused(self) -> None:
        env = {
            "ALPACA_PAPER": "false",
            "ALLOW_LIVE_TRADING": "true",
            "DRY_RUN": "false",
            "ALPACA_API_KEY": "x",
            "ALPACA_SECRET_KEY": "y",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            # Avoid constructing real SDK clients
            with mock.patch(
                "crypto_paper_desk.adapters.alpaca_client.AlpacaPaperBroker.__init__",
                lambda self, settings: setattr(self, "settings", settings)
                or setattr(self, "_trading", object())
                or setattr(self, "_data", None),
            ):
                settings = load_settings(env_file=None)
                broker = AlpacaPaperBroker(settings)
                with self.assertRaises(LiveTradingRefused):
                    broker.place_limit_buy(
                        qty=0.01, limit_price=75_000, dry_run=False, allow_unsafe_live=False
                    )


if __name__ == "__main__":
    unittest.main()
