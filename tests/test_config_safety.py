"""Paper-safety gates must refuse live by default."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from crypto_paper_desk.config import ConfigError, load_settings


class ConfigSafetyTests(unittest.TestCase):
    def test_paper_defaults_dry_run(self) -> None:
        env = {
            "ALPACA_PAPER": "true",
            "ALPACA_API_KEY": "",
            "ALPACA_SECRET_KEY": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            # Clear allow live / dry run if present
            os.environ.pop("ALLOW_LIVE_TRADING", None)
            os.environ.pop("DRY_RUN", None)
            settings = load_settings(env_file=None)
            self.assertTrue(settings.alpaca_paper)
            self.assertTrue(settings.dry_run)
            self.assertFalse(settings.allow_live_trading)

    def test_live_without_override_refused(self) -> None:
        env = {
            "ALPACA_PAPER": "false",
            "ALLOW_LIVE_TRADING": "false",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError):
                load_settings(env_file=None)

    def test_live_override_still_defaults_dry_run(self) -> None:
        env = {
            "ALPACA_PAPER": "false",
            "ALLOW_LIVE_TRADING": "true",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("DRY_RUN", None)
            settings = load_settings(env_file=None)
            self.assertFalse(settings.alpaca_paper)
            self.assertTrue(settings.allow_live_trading)
            self.assertTrue(settings.dry_run)


if __name__ == "__main__":
    unittest.main()
