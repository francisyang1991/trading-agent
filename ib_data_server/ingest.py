#!/usr/bin/env python3
"""
IB Historical Data Ingestor
============================
Connects to IB Gateway on the Mac mini, fetches historical bars for all
configured symbols, and upserts into the local SQLite database.

Run nightly via launchd/cron after market close.

Usage:
    python ingest.py                       # ingest all enabled symbols (daily bars)
    python ingest.py --symbols AAPL NVDA   # specific symbols
    python ingest.py --bar-size "5 mins"   # intraday
    python ingest.py --duration "60 D"     # custom lookback
    python ingest.py --backfill            # full 1-year backfill
"""

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ib_data_server.db import db_session, init_schema, upsert_bars, log_ingest, list_symbols

try:
    from ib_async import IB, Stock, Future, Contract
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False

from loguru import logger

IB_HOST = os.environ.get("IB_HOST", "127.0.0.1")
IB_PORT = int(os.environ.get("IB_PORT", "4002"))
IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID", "10"))

DURATION_DEFAULTS = {
    "1 min": "2 D",
    "5 mins": "5 D",
    "15 mins": "10 D",
    "1 hour": "30 D",
    "1 day": "365 D",
}

SEC_TYPE_MAP = {
    "STK": Stock,
    "FUT": Future,
}


def _make_contract(symbol: str, sec_type: str = "STK", exchange: str = "SMART", currency: str = "USD") -> Contract:
    cls = SEC_TYPE_MAP.get(sec_type, Stock)
    if sec_type == "FUT":
        return cls(localSymbol=symbol, exchange=exchange)
    return cls(symbol, exchange, currency)


async def _fetch_bars(ib: IB, contract: Contract, bar_size: str, duration: str) -> list[dict]:
    """Fetch historical bars from IB and return as list of dicts."""
    await asyncio.sleep(0.2)  # rate limit courtesy

    try:
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Contract qualification failed for {contract}")
    except Exception as e:
        raise ValueError(f"Contract qualification error: {e}") from e

    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=True,
        formatDate=2,  # UTC ISO format
    )

    if bars is None:
        return []

    return [
        {
            "ts": str(b.date),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": int(b.volume) if b.volume else 0,
            "wap": float(b.average) if hasattr(b, "average") and b.average else None,
            "bar_count": int(b.barCount) if hasattr(b, "barCount") and b.barCount else None,
        }
        for b in bars
    ]


async def ingest(
    symbols: list[str] | None = None,
    bar_size: str = "1 day",
    duration: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Main ingest routine. Returns summary stats."""
    if not IB_AVAILABLE:
        raise RuntimeError("ib_async not installed. pip install ib_async")

    duration = duration or DURATION_DEFAULTS.get(bar_size, "365 D")

    with db_session(db_path) as conn:
        init_schema(conn)

        if not symbols:
            syms = list_symbols(conn)
            if not syms:
                logger.warning("No symbols configured. Use `python manage.py add-symbols` first.")
                return {"status": "no_symbols"}
            symbol_configs = [(s["symbol"], s["sec_type"], s["exchange"], s["currency"]) for s in syms]
        else:
            symbol_configs = [(s, "STK", "SMART", "USD") for s in symbols]

    ib = IB()
    logger.info(f"Connecting to IB Gateway at {IB_HOST}:{IB_PORT} (clientId={IB_CLIENT_ID})")
    await ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    logger.info("Connected to IB Gateway")

    results = {"total": 0, "success": 0, "failed": 0, "rows": 0, "errors": []}

    try:
        for sym, sec_type, exchange, currency in symbol_configs:
            results["total"] += 1
            t0 = time.time()
            try:
                contract = _make_contract(sym, sec_type, exchange, currency)
                bars = await _fetch_bars(ib, contract, bar_size, duration)
                elapsed_ms = int((time.time() - t0) * 1000)

                with db_session(db_path) as conn:
                    count = upsert_bars(conn, sym, bar_size, bars)
                    log_ingest(conn, sym, bar_size, count, elapsed_ms)

                results["success"] += 1
                results["rows"] += count
                logger.info(f"{sym}: upserted {count} bars ({elapsed_ms}ms)")

            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                error_msg = str(e)
                results["failed"] += 1
                results["errors"].append({"symbol": sym, "error": error_msg})
                logger.error(f"{sym}: FAILED - {error_msg}")

                with db_session(db_path) as conn:
                    log_ingest(conn, sym, bar_size, 0, elapsed_ms, status="error", error=error_msg)

            await asyncio.sleep(1.0)  # pacing between symbols

    finally:
        ib.disconnect()
        logger.info("Disconnected from IB Gateway")

    return results


def main():
    parser = argparse.ArgumentParser(description="IB Historical Data Ingestor")
    parser.add_argument("--symbols", nargs="+", help="Symbols to ingest (default: all enabled)")
    parser.add_argument("--bar-size", default="1 day", help="Bar size (default: '1 day')")
    parser.add_argument("--duration", help="Lookback duration (default: auto based on bar size)")
    parser.add_argument("--backfill", action="store_true", help="Full 1-year backfill")
    parser.add_argument("--db", help="Database path (default: ib_historical.db)")
    args = parser.parse_args()

    duration = args.duration
    if args.backfill:
        duration = "365 D"

    results = asyncio.run(
        ingest(
            symbols=args.symbols,
            bar_size=args.bar_size,
            duration=duration,
            db_path=args.db,
        )
    )

    logger.info(f"Ingest complete: {results['success']}/{results['total']} symbols, {results['rows']} total bars")
    if results["errors"]:
        for e in results["errors"]:
            logger.error(f"  FAILED: {e['symbol']} - {e['error']}")

    sys.exit(1 if results.get("failed", 0) > 0 else 0)


if __name__ == "__main__":
    main()
