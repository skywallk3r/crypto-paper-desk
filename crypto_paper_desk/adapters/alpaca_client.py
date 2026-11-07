"""
Alpaca paper broker adapter (official alpaca-py SDK).

Safety rules enforced here (in addition to config.py):
1. Refuse to construct a live TradingClient unless ALLOW_LIVE_TRADING is true.
2. Even with allow-live, place_order refuses unless paper=True on the client
   OR an explicit unsafe flag is passed (defaults False — never used by runner).
3. Dry-run returns a simulated order id without hitting the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from crypto_paper_desk.config import Settings


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    order_id: str | None
    status: str
    dry_run: bool
    detail: str = ""
    raw: dict[str, Any] | None = None


class LiveTradingRefused(RuntimeError):
    """Raised when code paths attempt real-money order placement."""


class AlpacaPaperBroker:
    """Thin wrapper around alpaca-py Crypto + Trading clients."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._trading = None
        self._data = None

        if not settings.alpaca_api_key or not settings.alpaca_secret_key:
            # Offline / dry-run without keys — market data will use empty bars
            # or demo fixture unless the runner supplies bars.
            return

        if not settings.alpaca_paper and not settings.allow_live_trading:
            raise LiveTradingRefused(
                "Refusing to init Alpaca trading client: paper=false without "
                "ALLOW_LIVE_TRADING=true"
            )

        # Import lazily so unit tests without the SDK installed can still import
        # package modules that don't touch the broker.
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical.crypto import CryptoHistoricalDataClient

        self._trading = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )
        # Crypto market data uses the same keys; data API is shared.
        self._data = CryptoHistoricalDataClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )

    @property
    def connected(self) -> bool:
        return self._trading is not None

    def get_account_equity(self) -> float:
        if not self._trading:
            return self.settings.paper_capital_usd
        account = self._trading.get_account()
        return float(account.equity)

    def get_bars(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Fetch recent crypto bars and normalize to indicator-friendly dicts.

        Symbol format for alpaca-py crypto: 'BTC/USD'.
        """
        symbol = symbol or self.settings.symbol
        timeframe = timeframe or self.settings.bar_timeframe

        if not self._data:
            return []

        from alpaca.data.requests import CryptoBarsRequest

        tf = _parse_timeframe(timeframe)
        end = datetime.now(timezone.utc)
        # Over-fetch calendar span so `limit` bars are available after weekends.
        start = end - timedelta(days=7)

        req = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
            limit=limit,
        )
        barset = self._data.get_crypto_bars(req)
        raw_bars: Sequence[Any]
        if hasattr(barset, "data"):
            raw_bars = barset.data.get(symbol, [])  # type: ignore[assignment]
        else:
            # Newer SDK may return a BarSet mapping-like object
            try:
                raw_bars = barset[symbol]  # type: ignore[index]
            except Exception:  # noqa: BLE001
                raw_bars = []

        out: list[dict[str, Any]] = []
        for b in raw_bars:
            out.append(
                {
                    "timestamp": getattr(b, "timestamp", None),
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": float(getattr(b, "volume", 0) or 0),
                }
            )
        return out[-limit:]

    def place_limit_buy(
        self,
        *,
        qty: float,
        limit_price: float,
        symbol: str | None = None,
        dry_run: bool | None = None,
        allow_unsafe_live: bool = False,
    ) -> OrderResult:
        """
        Place a limit buy on paper, or simulate in dry-run.

        allow_unsafe_live defaults False and is never set by the paper runner.
        """
        symbol = symbol or self.settings.symbol
        dry_run = self.settings.dry_run if dry_run is None else dry_run

        if dry_run:
            return OrderResult(
                ok=True,
                order_id=f"dryrun-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                status="simulated",
                dry_run=True,
                detail="dry_run_no_order_sent",
                raw={
                    "symbol": symbol,
                    "qty": qty,
                    "limit_price": limit_price,
                    "side": "buy",
                },
            )

        if not self.settings.alpaca_paper and not allow_unsafe_live:
            raise LiveTradingRefused(
                "Refusing live order. Paper only. Set ALPACA_PAPER=true."
            )

        if not self._trading:
            return OrderResult(
                False, None, "error", False, detail="trading_client_not_initialized"
            )

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        # Crypto on Alpaca typically uses GTC; paper accounts accept limit buys.
        req = LimitOrderRequest(
            symbol=symbol.replace("/", ""),  # BTCUSD for trading endpoint
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            limit_price=round(limit_price, 2),
        )
        # Prefer crypto pair form when supported
        try:
            req_pair = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.GTC,
                limit_price=round(limit_price, 2),
            )
            order = self._trading.submit_order(req_pair)
        except Exception:  # noqa: BLE001
            order = self._trading.submit_order(req)

        return OrderResult(
            ok=True,
            order_id=str(order.id),
            status=str(order.status),
            dry_run=False,
            detail="submitted",
            raw={"id": str(order.id), "status": str(order.status)},
        )


def _parse_timeframe(value: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    v = value.strip().lower()
    mapping = {
        "1min": TimeFrame(1, TimeFrameUnit.Minute),
        "1m": TimeFrame(1, TimeFrameUnit.Minute),
        "5min": TimeFrame(5, TimeFrameUnit.Minute),
        "5m": TimeFrame(5, TimeFrameUnit.Minute),
        "15min": TimeFrame(15, TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "1hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1h": TimeFrame(1, TimeFrameUnit.Hour),
        "1day": TimeFrame(1, TimeFrameUnit.Day),
        "1d": TimeFrame(1, TimeFrameUnit.Day),
    }
    # Normalize "15Min" style
    key = v.replace(" ", "")
    if key in mapping:
        return mapping[key]
    # Fallback 15m
    return TimeFrame(15, TimeFrameUnit.Minute)
