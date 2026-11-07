"""
Configuration loader with hard paper-trading safety gates.

Engineering choices:
- Paper is the only supported path for this desk. Live requires an explicit
  override env that still defaults OFF.
- Secrets come from environment / .env — never hard-coded, never committed.
- Capital and risk caps match desk intake ($10k paper, 2% / $200 per trade).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when configuration is invalid or unsafe."""


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(value: str | None, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    return float(value)


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the paper desk."""

    # Alpaca
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_paper: bool
    alpaca_base_url: str

    # Safety
    allow_live_trading: bool
    dry_run: bool

    # Desk capital / risk
    paper_capital_usd: float
    max_risk_per_trade_usd: float
    daily_loss_kill_pct: float

    # Symbol / cadence
    symbol: str
    bar_timeframe: str
    poll_seconds: int

    # Strategy thresholds (Rank #1)
    liquidation_min_usd: float
    liquidation_long_pct_min: float
    fng_max: int
    rsi_oversold: float
    atr_period: int
    atr_stop_mult: float
    chase_atr_mult: float
    target_r_min: float
    target_r_max: float
    time_stop_hours: float
    weekend_time_stop_hours: float
    weekend_book_fraction: float

    # External adapters
    coinglass_api_key: str | None
    fear_greed_url: str

    # Persistence
    journal_path: Path
    journal_backend: str  # "sqlite" | "csv"

    @property
    def is_safe_to_trade(self) -> bool:
        """True only when paper mode is on (or live is explicitly allowed)."""
        if self.alpaca_paper:
            return True
        return self.allow_live_trading


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """
    Load settings from the environment.

    Refuses to return a live-trading configuration unless
    ALLOW_LIVE_TRADING=true. Even then, dry-run defaults to True so the
    operator must also set DRY_RUN=false consciously.
    """
    if env_file:
        load_dotenv(env_file, override=False)

    alpaca_paper = _as_bool(os.getenv("ALPACA_PAPER"), default=True)
    allow_live = _as_bool(os.getenv("ALLOW_LIVE_TRADING"), default=False)
    dry_run = _as_bool(os.getenv("DRY_RUN"), default=True)

    # Hard safety: live without explicit override is a hard stop.
    if not alpaca_paper and not allow_live:
        raise ConfigError(
            "ALPACA_PAPER is false/unset-as-false and ALLOW_LIVE_TRADING is not "
            "true. This desk will not place live/real-money orders. Set "
            "ALPACA_PAPER=true (recommended) or, only with explicit approval, "
            "ALLOW_LIVE_TRADING=true."
        )

    if not alpaca_paper and allow_live:
        # Still force dry-run unless the operator also clears it — belt + suspenders.
        if dry_run:
            # Live + dry-run is allowed as a "connect but don't send" posture.
            pass
        # Note: we still refuse live order submission in the broker adapter.

    api_key = (os.getenv("ALPACA_API_KEY") or "").strip()
    secret_key = (os.getenv("ALPACA_SECRET_KEY") or "").strip()

    # Keys are required only when leaving pure offline dry-run simulation.
    require_keys = not dry_run or _as_bool(os.getenv("REQUIRE_ALPACA_KEYS"), default=False)
    if require_keys and (not api_key or not secret_key):
        raise ConfigError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY are required when DRY_RUN=false "
            "(or REQUIRE_ALPACA_KEYS=true). Copy .env.example → .env and fill paper keys."
        )

    paper_url = "https://paper-api.alpaca.markets"
    live_url = "https://api.alpaca.markets"
    default_url = paper_url if alpaca_paper else live_url
    base_url = (os.getenv("ALPACA_BASE_URL") or default_url).rstrip("/")

    journal_backend = (os.getenv("JOURNAL_BACKEND") or "sqlite").strip().lower()
    if journal_backend not in {"sqlite", "csv"}:
        raise ConfigError("JOURNAL_BACKEND must be 'sqlite' or 'csv'")

    journal_path = Path(
        os.getenv("JOURNAL_PATH")
        or ("journals/trades.db" if journal_backend == "sqlite" else "journals/trades.csv")
    )

    return Settings(
        alpaca_api_key=api_key,
        alpaca_secret_key=secret_key,
        alpaca_paper=alpaca_paper,
        alpaca_base_url=base_url,
        allow_live_trading=allow_live,
        dry_run=dry_run,
        paper_capital_usd=_as_float(os.getenv("PAPER_CAPITAL_USD"), 10_000.0),
        max_risk_per_trade_usd=_as_float(os.getenv("MAX_RISK_PER_TRADE_USD"), 200.0),
        daily_loss_kill_pct=_as_float(os.getenv("DAILY_LOSS_KILL_PCT"), 0.02),
        symbol=(os.getenv("SYMBOL") or "BTC/USD").strip(),
        bar_timeframe=(os.getenv("BAR_TIMEFRAME") or "15Min").strip(),
        poll_seconds=int(_as_float(os.getenv("POLL_SECONDS"), 60)),
        liquidation_min_usd=_as_float(os.getenv("LIQUIDATION_MIN_USD"), 1_000_000_000.0),
        liquidation_long_pct_min=_as_float(os.getenv("LIQUIDATION_LONG_PCT_MIN"), 0.80),
        fng_max=int(_as_float(os.getenv("FNG_MAX"), 30)),
        rsi_oversold=_as_float(os.getenv("RSI_OVERSOLD"), 30.0),
        atr_period=int(_as_float(os.getenv("ATR_PERIOD"), 14)),
        atr_stop_mult=_as_float(os.getenv("ATR_STOP_MULT"), 1.5),
        chase_atr_mult=_as_float(os.getenv("CHASE_ATR_MULT"), 0.5),
        target_r_min=_as_float(os.getenv("TARGET_R_MIN"), 1.0),
        target_r_max=_as_float(os.getenv("TARGET_R_MAX"), 1.5),
        time_stop_hours=_as_float(os.getenv("TIME_STOP_HOURS"), 4.0),
        weekend_time_stop_hours=_as_float(os.getenv("WEEKEND_TIME_STOP_HOURS"), 3.0),
        weekend_book_fraction=_as_float(os.getenv("WEEKEND_BOOK_FRACTION"), 0.60),
        coinglass_api_key=(os.getenv("COINGLASS_API_KEY") or "").strip() or None,
        fear_greed_url=(
            os.getenv("FEAR_GREED_URL") or "https://api.alternative.me/fng/?limit=1"
        ).strip(),
        journal_path=journal_path,
        journal_backend=journal_backend,
    )
