"""
alternative.me Crypto Fear & Greed Index adapter.

Public API (no key required): https://api.alternative.me/fng/
Graceful degrade: on network/parse failure returns unavailable status so the
strategy can skip rather than throw.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class FearGreedReading:
    value: int | None
    classification: str | None
    available: bool
    source: str
    detail: str = ""

    @property
    def is_extreme_or_fear_gate(self) -> bool:
        """True when we have a value (caller compares against FNG_MAX)."""
        return self.available and self.value is not None


class FearGreedClient:
    def __init__(self, url: str = "https://api.alternative.me/fng/?limit=1", timeout: float = 10.0):
        self.url = url
        self.timeout = timeout

    def fetch(self) -> FearGreedReading:
        try:
            resp = requests.get(self.url, timeout=self.timeout)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            rows = data.get("data") or []
            if not rows:
                return FearGreedReading(
                    None, None, False, "alternative.me", "empty_payload"
                )
            row = rows[0]
            value = int(row["value"])
            classification = str(row.get("value_classification") or "")
            return FearGreedReading(
                value=value,
                classification=classification,
                available=True,
                source="alternative.me",
            )
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash the desk
            return FearGreedReading(
                value=None,
                classification=None,
                available=False,
                source="alternative.me",
                detail=f"error:{type(exc).__name__}:{exc}",
            )
