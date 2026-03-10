"""
SQLite database for IBKR historical bar data.

Schema supports multiple bar sizes (1min → 1day) with UPSERT semantics.
Designed for single-writer (nightly ingest) + multi-reader (API queries).
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

DB_PATH = os.environ.get(
    "IB_DATA_DB",
    os.path.join(os.path.dirname(__file__), "ib_historical.db"),
)


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64 MB
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_session(db_path: Optional[str] = None):
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bars (
            symbol      TEXT    NOT NULL,
            bar_size    TEXT    NOT NULL,   -- '1 min', '5 mins', '1 day', etc.
            ts          TEXT    NOT NULL,   -- ISO-8601 UTC timestamp
            open        REAL    NOT NULL,
            high        REAL    NOT NULL,
            low         REAL    NOT NULL,
            close       REAL    NOT NULL,
            volume      INTEGER NOT NULL DEFAULT 0,
            wap         REAL,
            bar_count   INTEGER,
            PRIMARY KEY (symbol, bar_size, ts)
        );

        CREATE INDEX IF NOT EXISTS idx_bars_symbol_ts
            ON bars (symbol, bar_size, ts DESC);

        CREATE TABLE IF NOT EXISTS ingest_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL,
            bar_size    TEXT    NOT NULL,
            fetched_at  TEXT    NOT NULL,   -- ISO-8601
            rows_upserted INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER,
            status      TEXT    NOT NULL DEFAULT 'ok',
            error       TEXT
        );

        CREATE TABLE IF NOT EXISTS symbols (
            symbol      TEXT    PRIMARY KEY,
            sec_type    TEXT    NOT NULL DEFAULT 'STK',
            exchange    TEXT    NOT NULL DEFAULT 'SMART',
            currency    TEXT    NOT NULL DEFAULT 'USD',
            enabled     INTEGER NOT NULL DEFAULT 1,
            added_at    TEXT    NOT NULL,
            notes       TEXT
        );
    """)


def upsert_bars(conn: sqlite3.Connection, symbol: str, bar_size: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO bars (symbol, bar_size, ts, open, high, low, close, volume, wap, bar_count)
        VALUES (:symbol, :bar_size, :ts, :open, :high, :low, :close, :volume, :wap, :bar_count)
        ON CONFLICT (symbol, bar_size, ts) DO UPDATE SET
            open      = excluded.open,
            high      = excluded.high,
            low       = excluded.low,
            close     = excluded.close,
            volume    = excluded.volume,
            wap       = excluded.wap,
            bar_count = excluded.bar_count
        """,
        [
            {
                "symbol": symbol,
                "bar_size": bar_size,
                "ts": r["ts"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r.get("volume", 0),
                "wap": r.get("wap"),
                "bar_count": r.get("bar_count"),
            }
            for r in rows
        ],
    )
    return len(rows)


def log_ingest(
    conn: sqlite3.Connection,
    symbol: str,
    bar_size: str,
    rows_upserted: int,
    duration_ms: int,
    status: str = "ok",
    error: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO ingest_log (symbol, bar_size, fetched_at, rows_upserted, duration_ms, status, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol, bar_size, datetime.utcnow().isoformat(), rows_upserted, duration_ms, status, error),
    )


def query_bars(
    conn: sqlite3.Connection,
    symbol: str,
    bar_size: str = "1 day",
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 5000,
) -> list[dict]:
    sql = "SELECT * FROM bars WHERE symbol = ? AND bar_size = ?"
    params: list = [symbol, bar_size]
    if start:
        sql += " AND ts >= ?"
        params.append(start)
    if end:
        sql += " AND ts <= ?"
        params.append(end)
    sql += " ORDER BY ts ASC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_symbols(conn: sqlite3.Connection, enabled_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM symbols"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY symbol"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def add_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    notes: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO symbols (symbol, sec_type, exchange, currency, enabled, added_at, notes)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT (symbol) DO UPDATE SET
            sec_type = excluded.sec_type,
            exchange = excluded.exchange,
            currency = excluded.currency,
            enabled  = 1,
            notes    = excluded.notes
        """,
        (symbol, sec_type, exchange, currency, datetime.utcnow().isoformat(), notes),
    )
