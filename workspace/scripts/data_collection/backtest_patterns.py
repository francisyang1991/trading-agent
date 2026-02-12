"""
Backtest Patterns — Seed Initial Win Rates from 1-Year History
================================================================
Processes the knowledge hub Discord messages, extracts technical patterns,
fetches historical prices from IBKR (via /api/quotes) and yfinance,
then computes actual returns at 1/3/5/10/20 day horizons.

Saves results to workspace/data/pattern_performance.json (used by pattern_library).

Usage:
  python3 backtest_patterns.py [--limit 500]
"""

import os
import sys
import json
import re
import time
import logging
import argparse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backtest-patterns")

# Add pattern library to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.join(SCRIPT_DIR, "../discord_bot")
sys.path.insert(0, BOT_DIR)

from pattern_library import (
    extract_patterns, parse_raw_signal, compute_pattern_stats,
    format_pattern_stats_discord, PatternRecord, PATTERN_CATALOG,
)

# Data paths
KNOWLEDGE_HUB = os.path.join(SCRIPT_DIR, "../../data/knowledge_hub/discord_signals_1year.json")
PATTERN_DB_FILE = os.path.join(SCRIPT_DIR, "../../data/pattern_performance.json")

# IBKR API
TRADE_API_URL = os.environ.get("TRADE_API_URL", "http://34.75.9.166:8080")
TRADE_API_KEY = os.environ.get("TRADE_API_KEY", "saiyan-trade-2026")

# Track periods (days)
TRACK_PERIODS = [1, 3, 5, 10, 20]

# Goku raw signal channel
GOKU_RAW_CHANNEL = "1393715240474247249"

# ===================================================================
# Price Fetching — yfinance for historical dates
# ===================================================================

_yf_cache: Dict[str, "pd.DataFrame"] = {}


def _fetch_yf_history(ticker: str, start_date: str, end_date: str):
    """Fetch daily OHLC from yfinance for a date range. Returns DataFrame."""
    import yfinance as yf

    cache_key = f"{ticker}_{start_date}_{end_date}"
    if cache_key in _yf_cache:
        return _yf_cache[cache_key]

    try:
        data = yf.download(ticker, start=start_date, end=end_date, progress=False)
        if data is not None and not data.empty:
            _yf_cache[cache_key] = data
            return data
    except Exception as e:
        log.warning(f"yfinance error for {ticker}: {e}")

    return None


def _get_price_on_date(ticker: str, target_date: datetime, full_history) -> float:
    """Get closing price on or near a target date from a DataFrame."""
    if full_history is None or full_history.empty:
        return 0

    import pandas as pd

    target = pd.Timestamp(target_date.date())

    # Try exact date
    if target in full_history.index:
        close = full_history.loc[target]
        if hasattr(close, 'Close'):
            return float(close['Close'])
        return float(close.iloc[0]) if hasattr(close, 'iloc') else float(close)

    # Try nearest date within 3 days
    for delta in range(1, 4):
        for d in [target + pd.Timedelta(days=delta), target - pd.Timedelta(days=delta)]:
            if d in full_history.index:
                close = full_history.loc[d]
                if hasattr(close, 'Close'):
                    return float(close['Close'])
                return float(close.iloc[0]) if hasattr(close, 'iloc') else float(close)

    # Last resort: nearest available
    idx = full_history.index.get_indexer([target], method='nearest')
    if len(idx) > 0 and idx[0] >= 0 and idx[0] < len(full_history):
        row = full_history.iloc[idx[0]]
        if hasattr(row, 'Close'):
            return float(row['Close'])
        return float(row.iloc[0]) if hasattr(row, 'iloc') else float(row)

    return 0


# ===================================================================
# Main Backtesting Logic
# ===================================================================

def extract_tickers_from_content(content: str) -> List[str]:
    """Extract $TICKER references from message content."""
    tickers = re.findall(r'\$([A-Z]{1,5})\b', content)
    return list(set(tickers))


def process_knowledge_hub(limit: int = 0) -> List[dict]:
    """
    Process all knowledge hub messages:
    1. Extract patterns from each message
    2. Build list of PatternRecords with signal dates
    """
    if not os.path.exists(KNOWLEDGE_HUB):
        log.error(f"Knowledge hub not found: {KNOWLEDGE_HUB}")
        return []

    with open(KNOWLEDGE_HUB, 'r') as f:
        data = json.load(f)

    all_records = []
    seen_ids = set()

    for channel_id, msgs in data.items():
        for msg in msgs:
            content = msg.get('content', '')
            if not content.strip():
                continue

            author = msg.get('author', {}).get('username', 'unknown')
            ts = msg.get('timestamp', '')
            msg_id = msg.get('id', '')

            # Parse signal date
            try:
                sig_date = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except Exception:
                continue

            # Skip signals from last 20 days (not enough time for outcomes)
            if (datetime.now(timezone.utc) - sig_date).days < 20:
                continue

            # Extract tickers
            tickers = extract_tickers_from_content(content)

            # For Goku raw channel, use dedicated parser
            if channel_id == GOKU_RAW_CHANNEL:
                raw_records = parse_raw_signal(
                    content, author=author, channel_id=channel_id,
                    msg_id=msg_id, timestamp=ts
                )
                for rec in raw_records:
                    if rec.record_id not in seen_ids:
                        seen_ids.add(rec.record_id)
                        all_records.append(rec.to_dict())
                continue

            # For other channels, extract patterns per ticker
            for ticker in tickers:
                records = extract_patterns(
                    content, ticker, author=author, channel_id=channel_id,
                    msg_id=msg_id, timestamp=ts
                )
                for rec in records:
                    if rec.record_id not in seen_ids:
                        seen_ids.add(rec.record_id)
                        all_records.append(rec.to_dict())

    log.info(f"Extracted {len(all_records)} pattern records from knowledge hub")

    if limit > 0:
        all_records = all_records[:limit]
        log.info(f"Limited to {limit} records")

    return all_records


def batch_fetch_historical_prices(records: List[dict]) -> Dict[str, "pd.DataFrame"]:
    """
    For all unique tickers in the records, fetch full 1-year history from yfinance.
    Returns {ticker: DataFrame} for efficient lookups.
    """
    import yfinance as yf

    # Collect all unique tickers
    tickers = list(set(r.get('ticker', '') for r in records if r.get('ticker')))
    tickers = [t for t in tickers if t]  # Remove empty

    log.info(f"Fetching 1-year historical data for {len(tickers)} unique tickers...")

    histories = {}
    failed = set()

    # Batch download in chunks
    for i in range(0, len(tickers), 20):
        chunk = tickers[i:i + 20]
        ticker_str = " ".join(chunk)
        log.info(f"  Chunk {i//20 + 1}: {', '.join(chunk[:5])}{'...' if len(chunk) > 5 else ''}")

        try:
            data = yf.download(
                ticker_str,
                period="2y",  # 2 years to cover all lookback needs
                progress=False,
                threads=True,
                group_by='ticker',
            )

            if data is not None and not data.empty:
                if len(chunk) == 1:
                    # Single ticker: DataFrame directly
                    histories[chunk[0]] = data
                else:
                    # Multi-ticker: grouped by ticker
                    for t in chunk:
                        try:
                            if t in data.columns.get_level_values(0):
                                ticker_data = data[t].dropna(how='all')
                                if not ticker_data.empty:
                                    histories[t] = ticker_data
                                else:
                                    failed.add(t)
                            else:
                                failed.add(t)
                        except Exception:
                            failed.add(t)
        except Exception as e:
            log.warning(f"  Download error: {e}")
            for t in chunk:
                failed.add(t)

        time.sleep(0.5)  # Rate limit

    log.info(f"Fetched history for {len(histories)} tickers, {len(failed)} failed")
    return histories


def compute_outcomes(records: List[dict], histories: Dict) -> List[dict]:
    """
    For each pattern record, look up price at signal date and at +1/3/5/10/20 days.
    Compute returns and win/loss status.
    """
    import pandas as pd

    updated = 0
    for rec in records:
        ticker = rec.get('ticker', '')
        sig_date_str = rec.get('signal_date', '')

        if not ticker or not sig_date_str:
            continue

        hist = histories.get(ticker)
        if hist is None or hist.empty:
            continue

        try:
            sig_date = datetime.fromisoformat(sig_date_str.replace('Z', '+00:00'))
        except Exception:
            continue

        # Get price at signal
        price_at_signal = _get_price_on_date(ticker, sig_date, hist)
        if price_at_signal <= 0:
            continue

        rec['price_at_signal'] = round(price_at_signal, 2)
        direction = rec.get('direction', 'BULL')

        # Compute returns at each period
        returns = []
        for days in TRACK_PERIODS:
            target_date = sig_date + timedelta(days=days)
            price = _get_price_on_date(ticker, target_date, hist)
            if price > 0:
                rec[f'price_{days}d'] = round(price, 2)
                ret = ((price / price_at_signal) - 1) * 100
                if direction == 'BEAR':
                    ret = -ret  # Invert for short signals
                rec[f'return_{days}d'] = round(ret, 2)
                returns.append(ret)

        if returns:
            rec['peak_return'] = round(max(returns), 2)
            rec['worst_return'] = round(min(returns), 2)
            rec['is_winner'] = max(returns) >= 2.0  # +2% at any horizon
            rec['is_loser'] = min(returns) <= -5.0   # -5% at any horizon
            updated += 1

    log.info(f"Computed outcomes for {updated}/{len(records)} records")
    return records


def main():
    parser = argparse.ArgumentParser(description="Backtest patterns from knowledge hub")
    parser.add_argument("--limit", type=int, default=0, help="Limit records to process (0=all)")
    args = parser.parse_args()

    print("=" * 70)
    print("PATTERN BACKTESTER — Seeding Win Rates from 1-Year History")
    print("=" * 70)

    # Step 1: Extract patterns from knowledge hub
    print("\n[1/4] Extracting patterns from knowledge hub...")
    records = process_knowledge_hub(limit=args.limit)
    if not records:
        print("No patterns found. Run download_1year_history.py first.")
        return

    # Show pattern distribution
    pattern_counts = {}
    for r in records:
        name = r.get('pattern_name', 'UNKNOWN')
        pattern_counts[name] = pattern_counts.get(name, 0) + 1

    print(f"\nFound {len(records)} pattern instances across {len(pattern_counts)} pattern types:")
    for name, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        print(f"  {name:30s} {count:5d}")

    # Step 2: Fetch historical prices
    print(f"\n[2/4] Fetching historical price data...")
    histories = batch_fetch_historical_prices(records)

    # Step 3: Compute outcomes
    print(f"\n[3/4] Computing outcomes at 1/3/5/10/20 day horizons...")
    records = compute_outcomes(records, histories)

    # Step 4: Save and report
    print(f"\n[4/4] Saving results...")

    # Merge with any existing pattern DB
    existing = []
    if os.path.exists(PATTERN_DB_FILE):
        try:
            with open(PATTERN_DB_FILE, 'r') as f:
                existing = json.load(f)
            log.info(f"Loaded {len(existing)} existing pattern records")
        except Exception:
            pass

    # Merge: keep existing records, add new ones
    existing_ids = {r.get('record_id') for r in existing}
    new_records = [r for r in records if r.get('record_id') not in existing_ids]
    merged = existing + new_records

    with open(PATTERN_DB_FILE, 'w') as f:
        json.dump(merged, f, indent=2)

    print(f"\nSaved {len(merged)} total records ({len(new_records)} new) to:")
    print(f"  {os.path.abspath(PATTERN_DB_FILE)}")

    # Compute and display stats
    stats = compute_pattern_stats(merged)
    print("\n" + "=" * 100)
    print("PATTERN SCORECARD (Risk-Adjusted)")
    print("=" * 100)
    print(f"{'Pattern':<25} {'Grd':>3} {'EV':>7} {'PF':>6} {'WR':>6} {'#':>5}"
          f" {'AvgWin':>7} {'AvgLos':>7} {'MaxDD':>7} {'Hold':>5} {'Conf':>6}")
    print("-" * 100)

    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4, "?": 5}
    sorted_names = sorted(
        [n for n in stats if stats[n].tracked > 0],
        key=lambda n: (grade_order.get(stats[n].grade, 5), -stats[n].expected_value)
    )

    for name in sorted_names:
        s = stats[name]
        pf_str = f"{s.profit_factor:.1f}" if s.profit_factor < 50 else ">50"
        hold = s.best_hold if s.best_hold != "N/A" else "-"
        print(
            f"{s.display_name:<25} [{s.grade}] "
            f"{s.expected_value:>+5.1f}% {pf_str:>6} {s.win_rate*100:>5.0f}% {s.tracked:>5}"
            f" {s.avg_winner:>+6.1f}% {s.avg_loser:>+6.1f}% {s.max_drawdown:>+6.1f}%"
            f" {hold:>5} {s.confidence:>6}"
        )

    # Summary
    total_tracked = sum(s.tracked for s in stats.values())
    total_winners = sum(s.winners for s in stats.values())
    overall_wr = total_winners / total_tracked * 100 if total_tracked > 0 else 0
    all_ev = [s.expected_value for s in stats.values() if s.tracked >= 5]
    avg_ev = sum(all_ev) / len(all_ev) if all_ev else 0
    print("-" * 100)
    print(f"{'TOTAL':<25}      {avg_ev:>+5.1f}%        {overall_wr:>5.0f}% {total_tracked:>5}")
    print(f"\nGrade: [A] EV>2% PF>1.8 | [B] EV>1% PF>1.3 | [C] EV>0 | [D] ~breakeven | [F] losing")

    # Save stats summary as well
    stats_file = os.path.join(SCRIPT_DIR, "../../data/knowledge_hub/pattern_stats_summary.json")
    stats_dict = {k: v.to_dict() for k, v in stats.items()}
    with open(stats_file, 'w') as f:
        json.dump(stats_dict, f, indent=2)
    print(f"\nStats summary saved to: {stats_file}")


if __name__ == "__main__":
    main()
