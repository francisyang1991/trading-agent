#!/usr/bin/env python3
"""
Management CLI for the IB Data Server.

Commands:
    python manage.py init-db                        # Create schema
    python manage.py add-symbols AAPL NVDA GOOGL    # Add symbols to track
    python manage.py list-symbols                   # Show tracked symbols
    python manage.py seed                           # Add common watchlist
    python manage.py stats                          # Show DB stats
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ib_data_server.db import db_session, init_schema, add_symbol, list_symbols


SEED_SYMBOLS = [
    # Mega caps
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    # Semis
    "AMD", "INTC", "AVGO", "QCOM", "MU", "MRVL",
    # Cloud / SaaS
    "CRM", "SNOW", "PLTR", "NET", "DDOG", "ZS",
    # Financials
    "JPM", "GS", "V", "MA", "COF",
    # Energy / Materials
    "XOM", "CVX", "CCJ", "ALB",
    # China
    "BABA", "PDD", "JD",
    # Consumer
    "SHOP", "APP", "FOUR",
    # ETFs
    "SPY", "QQQ", "IWM", "XLF", "XLE", "XLK",
]


def cmd_init_db(args):
    with db_session(args.db) as conn:
        init_schema(conn)
    print(f"Schema initialized: {args.db or 'default path'}")


def cmd_add_symbols(args):
    with db_session(args.db) as conn:
        init_schema(conn)
        for sym in args.symbols:
            add_symbol(conn, sym.upper(), args.sec_type, args.exchange, args.currency)
            print(f"  Added: {sym.upper()}")


def cmd_list_symbols(args):
    with db_session(args.db) as conn:
        init_schema(conn)
        syms = list_symbols(conn, enabled_only=not args.all)
    if not syms:
        print("No symbols configured.")
        return
    for s in syms:
        status = "enabled" if s["enabled"] else "disabled"
        print(f"  {s['symbol']:8s} {s['sec_type']:4s} {s['exchange']:8s} {status}")


def cmd_seed(args):
    with db_session(args.db) as conn:
        init_schema(conn)
        for sym in SEED_SYMBOLS:
            add_symbol(conn, sym)
        print(f"Seeded {len(SEED_SYMBOLS)} symbols.")


def cmd_stats(args):
    with db_session(args.db) as conn:
        init_schema(conn)
        row = conn.execute(
            "SELECT COUNT(DISTINCT symbol) as n_sym, COUNT(*) as n_bars, "
            "MIN(ts) as earliest, MAX(ts) as latest FROM bars"
        ).fetchone()
        print(f"Symbols: {row['n_sym']}  Bars: {row['n_bars']}  Range: {row['earliest']} → {row['latest']}")

        recent = conn.execute(
            "SELECT symbol, bar_size, fetched_at, rows_upserted, status "
            "FROM ingest_log ORDER BY id DESC LIMIT 5"
        ).fetchall()
        if recent:
            print("\nRecent ingests:")
            for r in recent:
                print(f"  {r['fetched_at'][:16]}  {r['symbol']:8s} {r['bar_size']:8s} {r['rows_upserted']:5d} rows  [{r['status']}]")


def main():
    parser = argparse.ArgumentParser(description="IB Data Server management")
    parser.add_argument("--db", help="Database path override")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init-db", help="Initialize database schema")
    sub.add_parser("stats", help="Show database statistics")
    sub.add_parser("seed", help="Seed common watchlist symbols")

    p_add = sub.add_parser("add-symbols", help="Add symbols to track")
    p_add.add_argument("symbols", nargs="+", help="Symbols to add")
    p_add.add_argument("--sec-type", default="STK")
    p_add.add_argument("--exchange", default="SMART")
    p_add.add_argument("--currency", default="USD")

    p_list = sub.add_parser("list-symbols", help="List tracked symbols")
    p_list.add_argument("--all", action="store_true", help="Include disabled")

    args = parser.parse_args()

    commands = {
        "init-db": cmd_init_db,
        "add-symbols": cmd_add_symbols,
        "list-symbols": cmd_list_symbols,
        "seed": cmd_seed,
        "stats": cmd_stats,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
