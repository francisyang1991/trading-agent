#!/usr/bin/env python3
"""
Sync blacklist from Nasdaq symbol feeds.

Adds symbols that Yahoo typically cannot serve well:
- Financial Status = D (deficient) or Q (bankrupt)

Note: Test Issue and ETF are already excluded by listed_symbols; we only add D/Q.

Usage:
    python tools/sync_blacklist_from_nasdaq.py
    python tools/sync_blacklist_from_nasdaq.py --dry-run
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.blacklist_store import DataBlacklistStore
from src.data_manager import DataManager


def _fetch_table(url: str):
    import pandas as pd
    with urlopen(url, timeout=20) as resp:
        payload = resp.read().decode("utf-8", errors="ignore")
    lines = [line for line in payload.splitlines() if line and "File Creation Time" not in line]
    df = pd.read_csv(StringIO("\n".join(lines)), sep="|")
    return df


def run(args) -> int:
    dm = DataManager()
    blacklist = DataBlacklistStore(dm)

    to_add = set()
    nasdaq_url = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
    other_url = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

    for url, label in [(nasdaq_url, "nasdaq"), (other_url, "other")]:
        try:
            df = _fetch_table(url)
        except Exception as e:
            print(f"⚠️ {label} fetch failed: {e}")
            continue

        if df.empty:
            continue
        cols = list(df.columns)
        sym_col = "ACT Symbol" if "ACT Symbol" in cols else "Symbol"
        fs_col = "Financial Status" if "Financial Status" in cols else None
        for _, row in df.iterrows():
            sym = str(row.get(sym_col, "")).upper().strip()
            if not sym:
                continue
            # Financial Status: D=deficient, Q=bankrupt (Yahoo often returns empty)
            if fs_col:
                fs = str(row.get(fs_col, "N")).upper()
                if fs in ("D", "Q"):
                    to_add.add(sym)

    existing = blacklist.load()
    new_symbols = to_add - existing
    if not new_symbols:
        print("No new symbols to add to blacklist.")
        return 0

    print(f"Adding {len(new_symbols)} symbols to blacklist (Financial Status D/Q)")
    if args.dry_run:
        for s in sorted(new_symbols)[:20]:
            print(f"  {s}")
        if len(new_symbols) > 20:
            print(f"  ... and {len(new_symbols) - 20} more")
        return 0

    blacklist.save(new_symbols, reason="sync_blacklist_from_nasdaq:deficient_or_bankrupt")
    print(f"Added {len(new_symbols)} symbols.")
    return 0


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Sync blacklist from Nasdaq feeds")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be added")
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
