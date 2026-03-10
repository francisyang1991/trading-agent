"""
Intraday 1-Minute Bar Cache
============================
Unified caching layer for intraday 1m OHLCV data used by T0 strategy tools.

Data sources (checked in order):
  1. Local SQLite cache (intraday_1m table in stock_cache.db)
  2. IBKR historical database (ib_data_server/ib_historical.db)
  3. yfinance download (result cached to SQLite for next time)

The cache stores RTH-only 1m bars with Eastern timezone timestamps.

Usage:
    from src.data.intraday_cache import get_intraday_1m

    # Get 7 days of 1m bars for PLTR (uses cache, fetches only if needed)
    df = get_intraday_1m("PLTR", days=7)

    # Force refresh from source
    df = get_intraday_1m("PLTR", days=7, force_refresh=True)

    # Get data for multiple symbols
    data = get_intraday_1m_batch(["PLTR", "AMD", "KLAC"], days=7)
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_DB = os.path.join(_PROJECT_ROOT, "data", "stock_cache.db")
_IBKR_DB = os.path.join(_PROJECT_ROOT, "ib_data_server", "ib_historical.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS intraday_1m (
    symbol      TEXT    NOT NULL,
    ts          TEXT    NOT NULL,   -- ISO-8601 Eastern time
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, ts)
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_intraday_1m_symbol_ts
    ON intraday_1m (symbol, ts DESC);
"""

_CREATE_META = """
CREATE TABLE IF NOT EXISTS intraday_meta (
    symbol          TEXT PRIMARY KEY,
    source          TEXT NOT NULL,       -- 'yfinance' or 'ibkr'
    last_updated    TEXT NOT NULL,       -- ISO-8601
    first_bar       TEXT,
    last_bar        TEXT,
    bar_count       INTEGER DEFAULT 0
);
"""


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_CREATE_TABLE + _CREATE_INDEX + _CREATE_META)


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or _CACHE_DB
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Cache read/write
# ---------------------------------------------------------------------------

def _read_cache(symbol: str, days: int, conn: sqlite3.Connection) -> Optional[pd.DataFrame]:
    """Read cached 1m bars. Returns None if cache is stale or empty."""
    # Check metadata
    meta = conn.execute(
        "SELECT last_updated, first_bar, last_bar, bar_count FROM intraday_meta WHERE symbol = ?",
        (symbol,)
    ).fetchone()

    if meta is None:
        return None

    last_updated = datetime.fromisoformat(meta["last_updated"])
    now_utc = datetime.now(timezone.utc)

    # Cache is stale if older than 4 hours (intraday data changes daily)
    if (now_utc - last_updated.replace(tzinfo=timezone.utc)).total_seconds() > 4 * 3600:
        # But on weekends, cache from Friday is fine
        today = now_utc.weekday()
        updated_day = last_updated.weekday()
        if today in (5, 6) and updated_day == 4:
            pass  # Weekend, Friday data is fresh
        elif today == 0 and updated_day == 4 and now_utc.hour < 14:
            pass  # Monday pre-market, Friday data is fine
        else:
            return None

    # Read bars
    cutoff = (now_utc - timedelta(days=days + 1)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM intraday_1m "
        "WHERE symbol = ? AND ts >= ? ORDER BY ts ASC",
        (symbol, cutoff)
    ).fetchall()

    if not rows:
        return None

    df = pd.DataFrame([dict(r) for r in rows])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts")
    if df.index.tz is None:
        df.index = df.index.tz_localize("US/Eastern")

    return df


def _write_cache(symbol: str, df: pd.DataFrame, source: str,
                 conn: sqlite3.Connection) -> None:
    """Write 1m bars to cache (upsert)."""
    if df.empty:
        return

    # Prepare data — ensure Eastern timestamps as strings
    idx = df.index
    if hasattr(idx, 'tz') and idx.tz is not None:
        idx_str = idx.strftime("%Y-%m-%d %H:%M:%S")
    else:
        idx_str = idx.strftime("%Y-%m-%d %H:%M:%S")

    rows = []
    for i in range(len(df)):
        rows.append((
            symbol, idx_str[i],
            float(df["open"].iloc[i]), float(df["high"].iloc[i]),
            float(df["low"].iloc[i]), float(df["close"].iloc[i]),
            int(df["volume"].iloc[i]),
        ))

    conn.executemany(
        """INSERT INTO intraday_1m (symbol, ts, open, high, low, close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (symbol, ts) DO UPDATE SET
               open=excluded.open, high=excluded.high,
               low=excluded.low, close=excluded.close,
               volume=excluded.volume""",
        rows,
    )

    # Update metadata
    now_utc = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO intraday_meta (symbol, source, last_updated, first_bar, last_bar, bar_count)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (symbol) DO UPDATE SET
               source=excluded.source, last_updated=excluded.last_updated,
               first_bar=excluded.first_bar, last_bar=excluded.last_bar,
               bar_count=excluded.bar_count""",
        (symbol, source, now_utc, idx_str[0], idx_str[-1], len(df)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# IBKR DB fallback
# ---------------------------------------------------------------------------

def _read_ibkr(symbol: str, days: int) -> Optional[pd.DataFrame]:
    """Try to read 1m bars from the IBKR historical database."""
    if not os.path.exists(_IBKR_DB):
        return None

    try:
        conn = sqlite3.connect(_IBKR_DB, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days + 1)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT ts, open, high, low, close, volume FROM bars "
            "WHERE symbol = ? AND bar_size = '1 min' AND ts >= ? ORDER BY ts ASC",
            (symbol, cutoff)
        ).fetchall()
        conn.close()

        if not rows or len(rows) < 100:
            return None

        df = pd.DataFrame([dict(r) for r in rows])
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("ts")
        df.index = df.index.tz_convert("US/Eastern")
        return df

    except Exception:
        return None


# ---------------------------------------------------------------------------
# yfinance download
# ---------------------------------------------------------------------------

def _download_yfinance(symbol: str, days: int) -> pd.DataFrame:
    """Download 1m bars from yfinance (max 8 days)."""
    import yfinance as yf

    days = max(1, min(days, 7))  # yfinance 1m limit: 8 days
    df = yf.download(symbol, period=f"{days}d", interval="1m",
                     auto_adjust=False, progress=False)
    if df.empty:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    def _pick(col):
        c = df[col]
        return c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c

    out = pd.DataFrame(index=df.index)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        out[col.lower()] = _pick(col)
    out = out.dropna()

    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_convert("US/Eastern")

    return out


def _filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to RTH only (9:30-16:00 ET, weekdays)."""
    if df.empty:
        return df
    mask = [
        (idx.weekday() <= 4)
        and (idx.time() >= pd.Timestamp("09:30").time())
        and (idx.time() <= pd.Timestamp("16:00").time())
        for idx in df.index
    ]
    return df.loc[mask].copy()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_intraday_1m(
    symbol: str,
    days: int = 7,
    force_refresh: bool = False,
    rth_only: bool = True,
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Get 1-minute intraday bars with caching.

    Checks sources in order:
      1. Local SQLite cache (unless force_refresh)
      2. IBKR historical database
      3. yfinance download (result cached)

    Args:
        symbol: Stock ticker
        days: Number of days of data
        force_refresh: Skip cache, re-download
        rth_only: Filter to regular trading hours only
        db_path: Override cache database path

    Returns:
        DataFrame with columns: open, high, low, close, volume
        Index: DatetimeIndex in US/Eastern timezone
    """
    conn = _get_conn(db_path)

    # 1. Check local cache
    if not force_refresh:
        cached = _read_cache(symbol, days, conn)
        if cached is not None and len(cached) >= 100:
            if rth_only:
                cached = _filter_rth(cached)
            conn.close()
            return cached

    # 2. Try IBKR DB
    ibkr_df = _read_ibkr(symbol, days)
    if ibkr_df is not None and len(ibkr_df) >= 100:
        _write_cache(symbol, ibkr_df, "ibkr", conn)
        if rth_only:
            ibkr_df = _filter_rth(ibkr_df)
        conn.close()
        return ibkr_df

    # 3. Fall back to yfinance
    yf_df = _download_yfinance(symbol, days)
    if not yf_df.empty:
        _write_cache(symbol, yf_df, "yfinance", conn)

    conn.close()

    if rth_only and not yf_df.empty:
        yf_df = _filter_rth(yf_df)

    return yf_df


def get_intraday_1m_batch(
    symbols: List[str],
    days: int = 7,
    force_refresh: bool = False,
    rth_only: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Get 1m bars for multiple symbols."""
    results = {}
    for sym in symbols:
        try:
            df = get_intraday_1m(sym, days, force_refresh, rth_only)
            if not df.empty:
                results[sym] = df
        except Exception:
            continue
    return results


def cache_stats(db_path: Optional[str] = None) -> Dict:
    """Get cache statistics."""
    conn = _get_conn(db_path)
    stats = {}

    total = conn.execute("SELECT COUNT(*) FROM intraday_1m").fetchone()[0]
    symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM intraday_1m").fetchone()[0]
    stats["total_bars"] = total
    stats["cached_symbols"] = symbols

    meta = conn.execute(
        "SELECT symbol, source, last_updated, bar_count FROM intraday_meta ORDER BY last_updated DESC"
    ).fetchall()
    stats["symbols"] = [dict(m) for m in meta]

    conn.close()
    return stats
