# crypto-paper-desk

Alpaca **paper-only** crypto desk. First strategy: **BTC Liquidation-Flush Mean Reversion** (15m → 1–4h).

> **WARNING — PAPER ONLY.** This project defaults to dry-run and requires `ALPACA_PAPER=true`. It will **refuse** to run against live/real-money accounts. Do not put production keys in git. Do not set `ALLOW_LIVE_TRADING=true` unless you have explicit written approval (the runner still will not submit live orders).

## Strategy summary (Rank #1)

| Rule | Spec |
| --- | --- |
| Universe | BTC-USD (Alpaca crypto paper) |
| Capital / risk | $10,000 paper · max **$200 (2%)** risk per trade |
| External triggers | 24h liquidations ≥ **$1B** with longs ≥ **~80%** **and** Fear & Greed ≤ **30** |
| Chart triggers | 15m RSI recovery from **&lt;30** **or** reclaim of flush mid after a lower-high reject of the bounce high |
| Entry | Limit toward 1h VWAP / flush mid; **no chase** &gt;0.5×ATR(14,15m) through mid |
| Stop | 1.5×ATR beyond post-flush swing extreme **or** hard invalidation under flush low — whichever is tighter |
| Exit | 1.0–1.5R at VWAP/mid, or time-stop **4h** (≤**3h** Sat/Sun) |
| Size | `min($200 / stop_dist, $10,000 / entry)`; weekend **50–70%** of book |
| Kill switch | **2% daily loss** from day-start equity → halt |

Thesis: after a long-dominated liquidation cascade and Extreme Fear, fade the first *failed* extension back toward post-flush equilibrium — **do not** blindly buy fear.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — paste Alpaca *paper* API key + secret
```

### How to set keys

1. Create / open an [Alpaca](https://alpaca.markets/) account.
2. Switch the dashboard to **Paper Trading**.
3. Generate API keys under Paper Trading → API Keys.
4. Put them in `.env` as `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.
5. Keep `ALPACA_PAPER=true` and `DRY_RUN=true` until you intentionally submit paper orders (`DRY_RUN=false` still stays on the paper endpoint).

Optional:
- `COINGLASS_API_KEY` — enables the live liquidations gate. Without it the strategy **skips** (graceful degrade). For local rehearsal you may set `LIQUIDATION_MANUAL_TOTAL_USD` / `LIQUIDATION_MANUAL_LONG_PCT` (see `.env.example`).
- Fear & Greed uses the public [alternative.me](https://api.alternative.me/fng/) API (no key).

## One command to paper-run (dry-run)

```bash
python -m crypto_paper_desk --once --demo-bars
```

What this does:
- Loads `.env` (paper flag enforced)
- Fetches Fear & Greed (or degrades)
- Uses CoinGlass / manual liquidation snapshot (or skips if unavailable)
- With `--demo-bars`, uses synthetic OHLCV if Alpaca data keys are missing
- Logs every signal / decision / order attempt to `journals/trades.db`
- **Does not** submit orders while `DRY_RUN=true`

Submit real **paper** orders (still not live money):

```bash
# In .env: DRY_RUN=false, ALPACA_PAPER=true, keys set
python -m crypto_paper_desk --once
```

Polling loop:

```bash
python -m crypto_paper_desk --loop
```

## Kill switch

If equity is down **≥ 2%** from the UTC day-start equity snapshot, the runner logs `kill_switch` and halts. Tune with `DAILY_LOSS_KILL_PCT` (default `0.02`).

## Package layout

```
crypto_paper_desk/
  config.py                 # env + paper safety gates
  indicators.py             # RSI / ATR / VWAP / flush mid
  risk.py                   # sizing, weekend scale, kill switch
  journal.py                # SQLite or CSV trade journal
  runner.py                 # CLI cycle / loop
  adapters/
    alpaca_client.py        # official alpaca-py, live orders refused
    coinglass.py            # liquidations stub + graceful degrade
    fear_greed.py           # alternative.me
  strategy/
    liquidation_flush_mr.py # Rank #1 rules
```

## Engineering choices

- **Plain Python + official `alpaca-py`** — no invented AgenKit / agenkit.xyz dependency.
- **Dry-run default** — `DRY_RUN=true`; paper flag required.
- **Live refused** — `ALPACA_PAPER=false` without `ALLOW_LIVE_TRADING=true` raises at config load; the broker adapter still will not submit live orders from the paper runner.
- **Graceful degrade** on missing CoinGlass key / FNG outage → skip, don’t crash, don’t invent cascade prints.
- **Journal everything** — signals, skips, kill switch, order attempts.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Disclaimer

Paper trading for research. Not investment advice. Scores in the morning brief are provisional until a Strategist out-of-sample pass lands.
