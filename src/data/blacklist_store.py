"""
Persistent symbol blacklist repository backed by SQLite.
"""

from __future__ import annotations

from datetime import datetime


class DataBlacklistStore:
    def __init__(self, data_manager):
        self._dm = data_manager

    def load(self) -> set[str]:
        conn = self._dm.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS data_blacklist (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                first_failed_at TEXT,
                last_failed_at TEXT
            )
            """
        )
        cur.execute("SELECT symbol FROM data_blacklist")
        symbols = {str(row[0]).upper() for row in cur.fetchall()}
        conn.commit()
        conn.close()
        return symbols

    def save(self, symbols: set[str], reason: str) -> None:
        if not symbols:
            return
        now = datetime.utcnow().isoformat()
        conn = self._dm.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS data_blacklist (
                symbol TEXT PRIMARY KEY,
                reason TEXT,
                first_failed_at TEXT,
                last_failed_at TEXT
            )
            """
        )
        for symbol in sorted(symbols):
            cur.execute(
                """
                INSERT INTO data_blacklist (symbol, reason, first_failed_at, last_failed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    reason=excluded.reason,
                    last_failed_at=excluded.last_failed_at
                """,
                (symbol, reason, now, now),
            )
        conn.commit()
        conn.close()
