"""
Paper desk runner — one evaluation cycle or a polling loop.

Defaults to dry-run. Never places live/real-money orders.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

from crypto_paper_desk.adapters.alpaca_client import AlpacaPaperBroker, LiveTradingRefused
from crypto_paper_desk.adapters.coinglass import CoinGlassClient
from crypto_paper_desk.adapters.fear_greed import FearGreedClient
from crypto_paper_desk.config import ConfigError, Settings, load_settings
from crypto_paper_desk.journal import TradeJournal
from crypto_paper_desk.risk import KillSwitchState
from crypto_paper_desk.strategy.liquidation_flush_mr import LiquidationFlushMeanReversion


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crypto-paper-desk",
        description=(
            "Alpaca PAPER desk — BTC Liquidation-Flush Mean Reversion. "
            "Dry-run by default. Refuses live trading."
        ),
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run a single evaluation cycle and exit (default for safety).",
    )
    p.add_argument(
        "--loop",
        action="store_true",
        help="Poll continuously using POLL_SECONDS (paper/dry-run only).",
    )
    p.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env).",
    )
    p.add_argument(
        "--demo-bars",
        action="store_true",
        help="Use synthetic bars when Alpaca keys/data are unavailable (local demo).",
    )
    return p


def _synthetic_flush_bars(n: int = 100) -> list[dict[str, Any]]:
    """
    Deterministic synthetic BTC-like bars for offline dry-run demos.

    Pattern: selloff into oversold, bounce, lower-high reject, reclaim mid.
    """
    bars: list[dict[str, Any]] = []
    price = 76_000.0
    # Grind down
    for i in range(60):
        o = price
        c = price - 40
        h = o + 20
        l = c - 30
        bars.append(
            {"timestamp": i, "open": o, "high": h, "low": l, "close": c, "volume": 100 + i}
        )
        price = c
    # Flush low area ~74700
    for i in range(10):
        o = price
        c = price - 10
        bars.append(
            {
                "timestamp": 60 + i,
                "open": o,
                "high": o + 15,
                "low": c - 20,
                "close": c,
                "volume": 200,
            }
        )
        price = c
    flush_low_zone = price
    # Bounce
    for i in range(15):
        o = price
        c = price + 50
        bars.append(
            {
                "timestamp": 70 + i,
                "open": o,
                "high": c + 20,
                "low": o - 10,
                "close": c,
                "volume": 150,
            }
        )
        price = c
    bounce_high = price
    # Lower-high reject (red bars)
    for i in range(5):
        o = price
        c = price - 60
        bars.append(
            {
                "timestamp": 85 + i,
                "open": o,
                "high": o + 10,
                "low": c - 15,
                "close": c,
                "volume": 120,
            }
        )
        price = c
    # Reclaim toward mid
    mid_target = (bounce_high + flush_low_zone) / 2.0
    while price < mid_target and len(bars) < n:
        o = price
        c = price + 40
        bars.append(
            {
                "timestamp": len(bars),
                "open": o,
                "high": c + 10,
                "low": o - 5,
                "close": c,
                "volume": 130,
            }
        )
        price = c
    # Pad if needed
    while len(bars) < n:
        o = price
        bars.append(
            {
                "timestamp": len(bars),
                "open": o,
                "high": o + 20,
                "low": o - 20,
                "close": o + 5,
                "volume": 100,
            }
        )
        price = o + 5
    return bars[-n:]


def run_cycle(
    settings: Settings,
    *,
    broker: AlpacaPaperBroker,
    journal: TradeJournal,
    strategy: LiquidationFlushMeanReversion,
    kill: KillSwitchState,
    demo_bars: bool = False,
) -> dict[str, Any]:
    """One Scout→Chart→Risk→Trader paper cycle."""
    now = datetime.now(timezone.utc)
    equity = broker.get_account_equity()

    journal.log(
        "cycle_start",
        symbol=settings.symbol,
        strategy=strategy.name,
        equity=equity,
        dry_run=settings.dry_run,
        paper=settings.alpaca_paper,
        ts=now.isoformat(),
    )

    if kill.tripped(equity, settings.daily_loss_kill_pct):
        decision = {
            "action": "halt",
            "reason": "daily_loss_kill_switch",
            "equity": equity,
            "day_start_equity": kill.day_start_equity,
            "kill_pct": settings.daily_loss_kill_pct,
        }
        journal.log("kill_switch", symbol=settings.symbol, strategy=strategy.name, **decision)
        return decision

    liq_client = CoinGlassClient(api_key=settings.coinglass_api_key)
    fng_client = FearGreedClient(url=settings.fear_greed_url)
    liquidations = liq_client.fetch_btc_24h()
    fear_greed = fng_client.fetch()

    journal.log(
        "external_gates",
        symbol=settings.symbol,
        strategy=strategy.name,
        liquidations=liquidations.__dict__,
        fear_greed=fear_greed.__dict__,
    )

    bars = broker.get_bars(limit=120)
    if not bars and demo_bars:
        bars = _synthetic_flush_bars(100)
        journal.log(
            "bars_source",
            symbol=settings.symbol,
            strategy=strategy.name,
            source="synthetic_demo",
            count=len(bars),
        )
    else:
        journal.log(
            "bars_source",
            symbol=settings.symbol,
            strategy=strategy.name,
            source="alpaca" if bars else "empty",
            count=len(bars),
        )

    signal = strategy.evaluate(
        bars, liquidations=liquidations, fear_greed=fear_greed, now=now
    )
    signal_payload = signal.to_dict()
    journal.log(
        "signal",
        symbol=settings.symbol,
        strategy=strategy.name,
        decision=signal_payload,
    )

    result: dict[str, Any] = {"signal": signal.to_dict()}

    if signal.action != "enter_long":
        journal.log(
            "decision",
            symbol=settings.symbol,
            strategy=strategy.name,
            action="no_trade",
            reason=signal.reason,
        )
        return result

    # Risk already embedded in sizing; place paper / dry-run limit.
    assert signal.qty is not None and signal.entry is not None
    try:
        order = broker.place_limit_buy(
            qty=signal.qty,
            limit_price=signal.entry,
            symbol=signal.symbol,
        )
    except LiveTradingRefused as exc:
        journal.log(
            "order_refused",
            symbol=settings.symbol,
            strategy=strategy.name,
            error=str(exc),
        )
        result["order"] = {"ok": False, "detail": str(exc)}
        return result

    journal.log(
        "order",
        symbol=settings.symbol,
        strategy=strategy.name,
        order_id=order.order_id,
        status=order.status,
        dry_run=order.dry_run,
        detail=order.detail,
        qty=signal.qty,
        limit_price=signal.entry,
        stop=signal.stop,
        target=signal.target,
    )
    result["order"] = {
        "ok": order.ok,
        "order_id": order.order_id,
        "status": order.status,
        "dry_run": order.dry_run,
        "detail": order.detail,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = load_settings(args.env_file)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    # Belt-and-suspenders print
    mode = []
    mode.append("PAPER" if settings.alpaca_paper else "LIVE-BLOCKED")
    mode.append("DRY-RUN" if settings.dry_run else "SUBMIT-ORDERS")
    print(f"crypto-paper-desk starting [{'|'.join(mode)}] symbol={settings.symbol}")

    if not settings.alpaca_paper:
        print(
            "Refusing to run: ALPACA_PAPER must be true for this desk.",
            file=sys.stderr,
        )
        return 3

    journal = TradeJournal(settings.journal_path, settings.journal_backend)
    broker = AlpacaPaperBroker(settings)
    strategy = LiquidationFlushMeanReversion(settings)
    equity = broker.get_account_equity()
    kill = KillSwitchState(day_start_equity=equity)

    once = args.once or not args.loop
    if once:
        out = run_cycle(
            settings,
            broker=broker,
            journal=journal,
            strategy=strategy,
            kill=kill,
            demo_bars=args.demo_bars,
        )
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"Polling every {settings.poll_seconds}s (Ctrl+C to stop)…")
    try:
        while True:
            out = run_cycle(
                settings,
                broker=broker,
                journal=journal,
                strategy=strategy,
                kill=kill,
                demo_bars=args.demo_bars,
            )
            print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **out}, default=str))
            if out.get("action") == "halt" or out.get("reason") == "daily_loss_kill_switch":
                print("Kill switch tripped — exiting loop.", file=sys.stderr)
                return 4
            time.sleep(max(5, settings.poll_seconds))
    except KeyboardInterrupt:
        print("\nStopped by operator.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
