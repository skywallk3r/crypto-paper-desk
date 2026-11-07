"""
Trade journal — every signal, decision, and order attempt is logged.

Backends:
- sqlite (default): journals/trades.db
- csv: journals/trades.csv

No secrets are written. Order payloads omit API keys.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradeJournal:
    """Append-only decision/order journal."""

    def __init__(self, path: Path, backend: str = "sqlite") -> None:
        self.path = Path(path)
        self.backend = backend
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if backend == "sqlite":
            self._init_sqlite()
        elif backend == "csv":
            self._init_csv()
        else:
            raise ValueError(f"Unsupported journal backend: {backend}")

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    strategy TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _init_csv(self) -> None:
        if not self.path.exists():
            with self.path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["ts", "event_type", "symbol", "strategy", "payload"]
                )
                writer.writeheader()

    def log(
        self,
        event_type: str,
        *,
        symbol: str | None = None,
        strategy: str | None = None,
        **payload: Any,
    ) -> None:
        """Persist one journal event."""
        row = {
            "ts": _utcnow(),
            "event_type": event_type,
            "symbol": symbol or "",
            "strategy": strategy or "",
            "payload": json.dumps(payload, default=str, sort_keys=True),
        }
        if self.backend == "sqlite":
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "INSERT INTO events (ts, event_type, symbol, strategy, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        row["ts"],
                        row["event_type"],
                        row["symbol"],
                        row["strategy"],
                        row["payload"],
                    ),
                )
                conn.commit()
        else:
            with self.path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["ts", "event_type", "symbol", "strategy", "payload"]
                )
                writer.writerow(row)
