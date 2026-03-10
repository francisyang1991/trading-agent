#!/usr/bin/env python3
"""
Universe Screener — screen entire US stock market for tradable candidates.

Two-tier pipeline:
  Tier 1 (fast): Nasdaq Trader feeds → all listed US symbols (~10,000)
  Tier 2 (filter): yfinance 5-day data → price > $2, avg volume > 100k

Output: screened_universe.json in data/ with top N tradable symbols ranked by
activity score (abs_price_change × volume_ratio).

Usage:
  python -m tools.universe_screener                  # default filters
  python -m tools.universe_screener --min-price 5    # higher price floor
  python -m tools.universe_screener --top-n 1000     # more candidates

Designed to run daily as a cron/scheduler task. Cache TTL = 12 hours.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is on sys.path
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

import yfinance as yf
import pandas as pd

from src.universe.listed_symbols import load_all_listed_us_symbols

# ── Config ───────────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(_root, "data")
CACHE_FILE = os.path.join(DATA_DIR, "screened_universe.json")
CACHE_TTL_HOURS = 12
BATCH_SIZE = 500  # yfinance download batch size
DOWNLOAD_PERIOD = "5d"  # minimal data for quick price/volume check


# ── Cache ────────────────────────────────────────────────────────────────────

def _load_cache() -> dict | None:
    """Return cached result if still fresh."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        screened_at = datetime.fromisoformat(data.get("screened_at", "2000-01-01"))
        if screened_at.tzinfo is None:
            screened_at = screened_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - screened_at).total_seconds() / 3600
        if age_hours < CACHE_TTL_HOURS:
            return data
    except Exception:
        pass
    return None


def _save_cache(data: dict) -> None:
    """Persist screened universe to JSON."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Screener ─────────────────────────────────────────────────────────────────

def screen_universe(
    min_price: float = 2.0,
    min_volume: int = 100_000,
    top_n: int = 500,
    force: bool = False,
) -> dict:
    """
    Screen entire US stock market for tradable stocks.

    Steps:
      1. Load all listed US symbols from Nasdaq Trader feeds (~10,000)
      2. Bulk-fetch 5-day OHLCV via yfinance in batches
      3. Filter: last close > min_price, avg daily volume > min_volume
      4. Rank by activity score = |price_change_pct| × volume_ratio
      5. Return top N symbols with metadata

    Returns dict:
      {
        "symbols": [...],           # sorted list of screened tickers
        "details": [...],           # per-ticker stats (price, volume, score)
        "total_listed": int,        # total listed US symbols checked
        "total_passed": int,        # symbols passing filters
        "screened_at": str,         # ISO timestamp
        "filters": {...},           # applied filters
      }
    """
    # Check cache
    if not force:
        cached = _load_cache()
        if cached:
            print(f"[Universe Screener] Using cache ({len(cached.get('symbols', []))} symbols, "
                  f"screened at {cached.get('screened_at', '?')})")
            return cached

    print("[Universe Screener] Starting full market screen...")
    t0 = time.time()

    # Step 1: Get all listed US symbols
    all_symbols = load_all_listed_us_symbols()
    print(f"[Universe Screener] Loaded {len(all_symbols)} listed US symbols")

    # Step 2 & 3: Bulk-fetch price/volume and filter
    passed: list[dict] = []
    n_batches = (len(all_symbols) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(0, len(all_symbols), BATCH_SIZE):
        batch = all_symbols[batch_idx : batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1

        try:
            # Download 5-day OHLCV for batch
            batch_str = " ".join(batch)
            df = yf.download(
                batch_str,
                period=DOWNLOAD_PERIOD,
                group_by="ticker",
                progress=False,
                threads=True,
            )

            if df is None or df.empty:
                print(f"  Batch {batch_num}/{n_batches}: empty response")
                continue

            for ticker in batch:
                try:
                    # Extract ticker data from multi-level columns
                    if len(batch) > 1:
                        if ticker not in df.columns.get_level_values(0):
                            continue
                        ticker_df = df[ticker]
                    else:
                        ticker_df = df

                    if ticker_df is None or ticker_df.empty:
                        continue

                    close = ticker_df["Close"].dropna()
                    volume = ticker_df["Volume"].dropna()

                    if len(close) < 2 or len(volume) < 2:
                        continue

                    last_price = float(close.iloc[-1])
                    avg_vol = float(volume.mean())

                    # Apply filters
                    if last_price < min_price:
                        continue
                    if avg_vol < min_volume:
                        continue

                    # Quick metrics for ranking
                    price_change_pct = (close.iloc[-1] / close.iloc[0] - 1) * 100
                    # Recent volume vs period average
                    recent_vol = float(volume.iloc[-2:].mean()) if len(volume) >= 2 else avg_vol
                    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

                    passed.append({
                        "symbol": ticker,
                        "price": round(last_price, 2),
                        "avg_volume": int(avg_vol),
                        "price_change_pct": round(float(price_change_pct), 2),
                        "vol_ratio": round(vol_ratio, 2),
                    })
                except Exception:
                    continue

        except Exception as e:
            print(f"  Batch {batch_num}/{n_batches}: error — {e}")
            continue

        print(f"  Batch {batch_num}/{n_batches}: {len(passed)} passed so far")

    # Step 4: Rank by activity score
    for item in passed:
        item["activity_score"] = round(
            abs(item["price_change_pct"]) * item["vol_ratio"], 4
        )

    passed.sort(key=lambda x: x["activity_score"], reverse=True)

    # Step 5: Take top N
    screened = passed[:top_n]

    elapsed = time.time() - t0
    print(
        f"[Universe Screener] Done: {len(passed)} passed filters out of "
        f"{len(all_symbols)} listed, returning top {len(screened)} ({elapsed:.1f}s)"
    )

    result = {
        "symbols": [r["symbol"] for r in screened],
        "details": screened,
        "total_listed": len(all_symbols),
        "total_passed": len(passed),
        "screened_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "min_price": min_price,
            "min_volume": min_volume,
            "top_n": top_n,
        },
    }

    _save_cache(result)
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Screen US stock universe")
    parser.add_argument("--min-price", type=float, default=2.0, help="Min stock price ($)")
    parser.add_argument("--min-volume", type=int, default=100_000, help="Min avg daily volume")
    parser.add_argument("--top-n", type=int, default=500, help="Top N most active to return")
    parser.add_argument("--force", action="store_true", help="Force refresh (ignore cache)")
    args = parser.parse_args()

    result = screen_universe(
        min_price=args.min_price,
        min_volume=args.min_volume,
        top_n=args.top_n,
        force=args.force,
    )

    print(f"\nTop 20 most active:")
    for i, d in enumerate(result["details"][:20], 1):
        print(
            f"  {i:3d}. {d['symbol']:6s}  ${d['price']:8.2f}  "
            f"vol={d['avg_volume']:>10,}  chg={d['price_change_pct']:+.1f}%  "
            f"score={d['activity_score']:.2f}"
        )


if __name__ == "__main__":
    main()
