"""
CoinGlass (or news-derived) liquidations adapter.

Graceful degrade:
- If COINGLASS_API_KEY is missing, returns unavailable (strategy skips trigger
  unless LIQUIDATION_FORCE_PASS=true for local dry-run demos).
- If the API errors, same unavailable posture — never invent cascade numbers.

The CoinGlass REST shape varies by plan; this stub targets a common
liquidation history endpoint and normalizes to (total_usd, long_pct).
Operators can also inject a manual reading via env for paper rehearsals:

  LIQUIDATION_MANUAL_TOTAL_USD=1050000000
  LIQUIDATION_MANUAL_LONG_PCT=0.885
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class LiquidationSnapshot:
    total_usd_24h: float | None
    long_pct: float | None
    available: bool
    source: str
    detail: str = ""

    def passes_gate(self, min_total: float, min_long_pct: float) -> bool:
        if not self.available or self.total_usd_24h is None or self.long_pct is None:
            return False
        return self.total_usd_24h >= min_total and self.long_pct >= min_long_pct


class CoinGlassClient:
    """
    Thin client. Real CoinGlass URLs / headers may need plan-specific tweaks;
    keep secrets in env only.
    """

    DEFAULT_URL = "https://open-api.coinglass.com/public/v2/liquidation_history"

    def __init__(
        self,
        api_key: str | None = None,
        url: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.url = url or os.getenv("COINGLASS_URL") or self.DEFAULT_URL
        self.timeout = timeout

    def fetch_btc_24h(self) -> LiquidationSnapshot:
        # Manual override for paper rehearsals (no fake live feed).
        manual_total = os.getenv("LIQUIDATION_MANUAL_TOTAL_USD")
        manual_long = os.getenv("LIQUIDATION_MANUAL_LONG_PCT")
        if manual_total and manual_long:
            try:
                return LiquidationSnapshot(
                    total_usd_24h=float(manual_total),
                    long_pct=float(manual_long),
                    available=True,
                    source="manual_env",
                    detail="LIQUIDATION_MANUAL_* override",
                )
            except ValueError:
                return LiquidationSnapshot(
                    None, None, False, "manual_env", "invalid_manual_values"
                )

        if not self.api_key:
            return LiquidationSnapshot(
                total_usd_24h=None,
                long_pct=None,
                available=False,
                source="coinglass",
                detail="missing_api_key",
            )

        headers = {
            "accept": "application/json",
            "coinglassSecret": self.api_key,
        }
        params = {"symbol": "BTC", "time_type": "h24"}
        try:
            resp = requests.get(
                self.url, headers=headers, params=params, timeout=self.timeout
            )
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
            return self._normalize(payload)
        except Exception as exc:  # noqa: BLE001
            return LiquidationSnapshot(
                None,
                None,
                False,
                "coinglass",
                detail=f"error:{type(exc).__name__}:{exc}",
            )

    def _normalize(self, payload: dict[str, Any]) -> LiquidationSnapshot:
        """
        Best-effort normalize across common CoinGlass response shapes.

        Expected-ish: data list with long/short liquidation notionals, or
        aggregated totalVol / longVol / shortVol fields.
        """
        data = payload.get("data", payload)

        # Aggregated object
        if isinstance(data, dict):
            total = _first_number(data, ("totalVol", "total_vol", "total", "volUsd"))
            long_v = _first_number(data, ("longVol", "long_vol", "longVolUsd", "buyVol"))
            short_v = _first_number(
                data, ("shortVol", "short_vol", "shortVolUsd", "sellVol")
            )
            if total is not None and long_v is not None and short_v is not None:
                denom = long_v + short_v
                long_pct = (long_v / denom) if denom > 0 else None
                return LiquidationSnapshot(
                    total, long_pct, True, "coinglass", "aggregated"
                )

        # List of rows — sum last 24h if timestamps present, else sum all
        if isinstance(data, list) and data:
            long_sum = 0.0
            short_sum = 0.0
            for row in data:
                if not isinstance(row, dict):
                    continue
                long_sum += float(
                    _first_number(row, ("longVol", "long_vol", "longVolUsd")) or 0
                )
                short_sum += float(
                    _first_number(row, ("shortVol", "short_vol", "shortVolUsd")) or 0
                )
            total = long_sum + short_sum
            long_pct = (long_sum / total) if total > 0 else None
            if total > 0:
                return LiquidationSnapshot(
                    total, long_pct, True, "coinglass", "summed_rows"
                )

        return LiquidationSnapshot(
            None, None, False, "coinglass", "unrecognized_payload"
        )


def _first_number(obj: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in obj and obj[k] is not None:
            try:
                return float(obj[k])
            except (TypeError, ValueError):
                continue
    return None
