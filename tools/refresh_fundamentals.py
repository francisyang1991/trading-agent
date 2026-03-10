#!/usr/bin/env python3
"""
REFRESH FUNDAMENTAL SCORES
===========================
Pre-compute fundamental composite scores for all known stocks and store in DB.
Scanner reads from this table at scan time (no live API calls).

Scores are based on FundamentalAnalyzer which computes:
- Quality score (ROIC, FCF, margins, earnings quality)
- Value score (PE, EV/EBITDA, price/sales)
- Growth score (revenue CAGR, EPS growth, FCF growth)
- Health score (debt/equity, current ratio, Altman Z)
- Overall = weighted average (quality 30%, value 25%, growth 25%, health 20%)

Usage:
    # Refresh all stocks in stock_fundamentals table
    python3 tools/refresh_fundamentals.py

    # Refresh specific tickers
    python3 tools/refresh_fundamentals.py AAPL NVDA IREN

    # Refresh stocks with recent earnings (run daily during earnings season)
    python3 tools/refresh_fundamentals.py --earnings

    # Refresh stale scores (>30 days old or missing)
    python3 tools/refresh_fundamentals.py --stale
    python3 tools/refresh_fundamentals.py --stale 14  # custom age in days

Run frequency: Weekly or --earnings daily during earnings season.
"""

import sys
import os
import time
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.universe.fundamentals import FundamentalAnalyzer

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'stock_cache.db')


def create_table(conn: sqlite3.Connection):
    """Create fundamental_scores table if it doesn't exist."""
    conn.execute('''CREATE TABLE IF NOT EXISTS fundamental_scores (
        symbol TEXT PRIMARY KEY,
        quality_score REAL DEFAULT 0,
        value_score REAL DEFAULT 0,
        growth_score REAL DEFAULT 0,
        health_score REAL DEFAULT 0,
        overall_score REAL DEFAULT 0,
        grade TEXT DEFAULT 'F',
        pe_ratio REAL,
        forward_pe REAL,
        roe REAL,
        gross_margin REAL,
        revenue_growth REAL,
        earnings_growth REAL,
        debt_to_equity REAL,
        fcf_yield REAL,
        piotroski_f REAL,
        source TEXT DEFAULT 'yfinance',
        updated_at TEXT
    )''')
    conn.commit()


def refresh_symbol(fa: FundamentalAnalyzer, symbol: str, conn: sqlite3.Connection) -> bool:
    """Refresh fundamental score for a single symbol. Returns True on success."""
    try:
        metrics = fa.analyze(symbol, force_refresh=True)
        if not metrics:
            return False

        conn.execute('''INSERT OR REPLACE INTO fundamental_scores
            (symbol, quality_score, value_score, growth_score, health_score,
             overall_score, grade, pe_ratio, forward_pe, roe, gross_margin,
             revenue_growth, earnings_growth, debt_to_equity, fcf_yield,
             piotroski_f, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'yfinance', ?)''',
            (symbol,
             metrics.quality_score,
             metrics.value_score,
             metrics.growth_score,
             metrics.financial_health_score,
             metrics.overall_fundamental_score,
             metrics.fundamental_grade,
             metrics.pe_ratio,
             metrics.forward_pe,
             metrics.roe,
             metrics.gross_margin,
             metrics.revenue_growth_yoy,
             metrics.earnings_growth_yoy,
             metrics.debt_to_equity,
             metrics.fcf_yield,
             metrics.piotroski_f_score,
             datetime.now().isoformat()))
        conn.commit()
        return True
    except Exception as e:
        print(f"    ERROR {symbol}: {e}")
        return False


def get_all_symbols(conn: sqlite3.Connection) -> list:
    """Get all known symbols from stock_fundamentals table."""
    rows = conn.execute("SELECT DISTINCT symbol FROM stock_fundamentals ORDER BY symbol").fetchall()
    return [r[0] for r in rows]


def get_earnings_tickers() -> list:
    """Get tickers that reported earnings in the last 7 days using yfinance."""
    try:
        import yfinance as yf
        from datetime import timedelta
        today = datetime.now()
        # Get all known symbols and check their last earnings date
        conn = sqlite3.connect(DB_PATH)
        all_symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stock_fundamentals ORDER BY symbol").fetchall()]
        conn.close()

        earners = []
        print("  Checking recent earnings dates...")
        for sym in all_symbols:
            try:
                ticker = yf.Ticker(sym)
                cal = ticker.calendar
                if cal is not None and not cal.empty:
                    # Check if earnings date is within last 7 days
                    for col in cal.columns:
                        dates = cal[col]
                        for d in dates:
                            if hasattr(d, 'date'):
                                d = d.date()
                            if isinstance(d, str):
                                from datetime import date as dt_date
                                d = dt_date.fromisoformat(d)
                            diff = (today.date() - d).days if hasattr(d, 'days') is False else 999
                            try:
                                diff = (today.date() - d).days
                            except Exception:
                                continue
                            if 0 <= diff <= 7:
                                earners.append(sym)
                                break
            except Exception:
                continue
            if len(earners) % 10 == 0 and len(earners) > 0:
                time.sleep(1)

        return list(set(earners))
    except Exception as e:
        print(f"    Earnings check failed: {e}")
        return []


def get_stale_symbols(conn: sqlite3.Connection, max_age_days: int = 30) -> list:
    """Get symbols whose fundamental scores are older than max_age_days."""
    cutoff = (datetime.now() - __import__('datetime').timedelta(days=max_age_days)).isoformat()
    rows = conn.execute("""
        SELECT sf.symbol FROM stock_fundamentals sf
        LEFT JOIN fundamental_scores fs ON sf.symbol = fs.symbol
        WHERE fs.symbol IS NULL OR fs.updated_at < ?
        ORDER BY sf.symbol
    """, (cutoff,)).fetchall()
    return [r[0] for r in rows]


def main():
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)
    fa = FundamentalAnalyzer()

    # Determine which symbols to refresh
    if len(sys.argv) > 1 and sys.argv[1] == '--earnings':
        # Refresh only stocks that reported earnings recently
        print("=== Earnings mode: refreshing stocks with recent earnings ===")
        symbols = get_earnings_tickers()
        if not symbols:
            print("  No recent earnings found. Nothing to refresh.")
            conn.close()
            return
    elif len(sys.argv) > 1 and sys.argv[1] == '--stale':
        # Refresh stale scores (>30 days old or missing)
        max_age = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        symbols = get_stale_symbols(conn, max_age)
        print(f"=== Stale mode: refreshing {len(symbols)} stocks older than {max_age} days ===")
    elif len(sys.argv) > 1 and sys.argv[1] != '--from-db':
        symbols = [s.upper() for s in sys.argv[1:]]
    else:
        symbols = get_all_symbols(conn)

    print(f"=== Refreshing fundamental scores for {len(symbols)} stocks ===")
    print(f"DB: {DB_PATH}")
    print()

    success = 0
    failed = 0

    for i, sym in enumerate(symbols):
        pct = (i + 1) / len(symbols) * 100
        ok = refresh_symbol(fa, sym, conn)
        if ok:
            metrics = fa.cache.get(sym)
            score = metrics.overall_fundamental_score if metrics else 0
            grade = metrics.fundamental_grade if metrics else '?'
            print(f"  [{i+1}/{len(symbols)} {pct:.0f}%] {sym}: {score:.0f}/100 ({grade})")
            success += 1
        else:
            print(f"  [{i+1}/{len(symbols)} {pct:.0f}%] {sym}: SKIPPED (no data)")
            failed += 1

        # Rate limiting: 0.5s between calls to avoid yfinance throttling
        if (i + 1) % 5 == 0:
            time.sleep(1)

    print()
    print(f"=== Done: {success} updated, {failed} failed/skipped ===")
    conn.close()


if __name__ == "__main__":
    main()
