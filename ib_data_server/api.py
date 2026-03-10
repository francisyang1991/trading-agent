#!/usr/bin/env python3
"""
IB Data Server — FastAPI read API
===================================
Lightweight HTTP API serving OHLCV bars from the local SQLite database.
Runs on the Mac mini; queried from MacBook over local network / Tailscale.

Usage:
    uvicorn ib_data_server.api:app --host 0.0.0.0 --port 8100
    # or
    python -m ib_data_server.api
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .db import get_connection, init_schema, query_bars, list_symbols, add_symbol

app = FastAPI(
    title="IB Historical Data API",
    description="Read-only OHLCV bar data from IBKR, served from local SQLite.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

DB_PATH = os.environ.get("IB_DATA_DB", None)


def _conn():
    conn = get_connection(DB_PATH)
    init_schema(conn)
    return conn


def health_payload():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/health")
@app.get("/api/health")
def health():
    return health_payload()


def get_bars_payload(
    symbol: str,
    bar_size: str,
    start: Optional[str],
    end: Optional[str],
    limit: int,
):
    conn = _conn()
    try:
        rows = query_bars(conn, symbol.upper(), bar_size, start, end, limit)
    finally:
        conn.close()

    if not rows:
        raise HTTPException(404, f"No bars for {symbol.upper()} ({bar_size})")

    return {
        "symbol": symbol.upper(),
        "bar_size": bar_size,
        "count": len(rows),
        "bars": rows,
    }


def _normalize_interval(interval: str) -> str:
    value = interval.strip().lower()
    aliases = {
        "1d": "1 day",
        "1day": "1 day",
        "1 day": "1 day",
        "1m": "1 min",
        "1min": "1 min",
        "1 min": "1 min",
        "5m": "5 mins",
        "5min": "5 mins",
        "5 mins": "5 mins",
        "15m": "15 mins",
        "15min": "15 mins",
        "15 mins": "15 mins",
        "30m": "30 mins",
        "30min": "30 mins",
        "30 mins": "30 mins",
        "1h": "1 hour",
        "1hour": "1 hour",
        "1 hour": "1 hour",
    }
    return aliases.get(value, interval)


def _history_payload(rows: list[dict], bar_size: str) -> list[dict]:
    daily = bar_size == "1 day"
    payload = []
    for row in rows:
        ts = row["ts"]
        if daily:
            ts = ts.split("T", 1)[0].split(" ", 1)[0]
        payload.append(
            {
                "Date": ts,
                "Open": row["open"],
                "High": row["high"],
                "Low": row["low"],
                "Close": row["close"],
                "Volume": row["volume"],
            }
        )
    return payload


@app.get("/bars/{symbol}")
def get_bars(
    symbol: str,
    bar_size: str = Query("1 day", description="Bar size: '1 min', '5 mins', '1 day', etc."),
    start: Optional[str] = Query(None, description="Start date (ISO-8601)"),
    end: Optional[str] = Query(None, description="End date (ISO-8601)"),
    limit: int = Query(5000, ge=1, le=50000),
):
    return get_bars_payload(symbol, bar_size, start, end, limit)


@app.get("/api/bars/{symbol}")
def get_api_bars(
    symbol: str,
    bar_size: str = Query("1 day", description="Bar size: '1 min', '5 mins', '1 day', etc."),
    start: Optional[str] = Query(None, description="Start date (ISO-8601)"),
    end: Optional[str] = Query(None, description="End date (ISO-8601)"),
    limit: int = Query(5000, ge=1, le=50000),
):
    return get_bars_payload(symbol, bar_size, start, end, limit)


def _period_to_start(period: str) -> Optional[str]:
    try:
        now = datetime.utcnow()
        p = period.lower()
        if p.endswith("y"):
            years = int(p[:-1])
            return now.replace(year=now.year - years).date().isoformat()
        if p.endswith("mo"):
            months = int(p[:-2])
            return (now - timedelta(days=months * 30)).date().isoformat()
        if p.endswith("d"):
            days = int(p[:-1])
            return (now - timedelta(days=days)).date().isoformat()
    except Exception:
        return None
    return None


@app.get("/api/history/{symbol}")
@app.get("/api/ohlcv/{symbol}")
def get_api_history(
    symbol: str,
    period: str = Query("1y"),
    interval: str = Query("1d"),
    limit: int = Query(5000, ge=1, le=50000),
):
    bar_size = _normalize_interval(interval)
    start = _period_to_start(period)
    payload = get_bars_payload(symbol, bar_size, start, None, limit)
    return _history_payload(payload["bars"], bar_size)


@app.get("/symbols")
def get_symbols():
    conn = _conn()
    try:
        syms = list_symbols(conn)
    finally:
        conn.close()
    return {"symbols": syms}


@app.post("/symbols")
def post_symbol(
    symbol: str,
    sec_type: str = "STK",
    exchange: str = "SMART",
    currency: str = "USD",
    notes: Optional[str] = None,
):
    conn = _conn()
    try:
        add_symbol(conn, symbol.upper(), sec_type, exchange, currency, notes)
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "symbol": symbol.upper()}


@app.get("/stats")
def stats():
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT symbol) as n_symbols, COUNT(*) as n_bars, "
            "MIN(ts) as earliest, MAX(ts) as latest FROM bars"
        ).fetchone()
        last_ingest = conn.execute(
            "SELECT symbol, bar_size, fetched_at, rows_upserted, status "
            "FROM ingest_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()

    return {
        "n_symbols": row["n_symbols"],
        "n_bars": row["n_bars"],
        "earliest": row["earliest"],
        "latest": row["latest"],
        "recent_ingests": [dict(r) for r in last_ingest],
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("IB_DATA_PORT", "8100"))
    uvicorn.run("ib_data_server.api:app", host="0.0.0.0", port=port, reload=False)
