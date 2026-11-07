"""
crypto_paper_desk — Alpaca paper trading desk for crypto strategies.

Default mode is paper / dry-run. This package refuses live trading unless
ALLOW_LIVE_TRADING is explicitly set (and even then live is not recommended).
"""

__version__ = "0.1.0"
__strategy__ = "btc_liquidation_flush_mean_reversion"
