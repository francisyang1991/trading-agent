#!/usr/bin/env python3
"""
SIGNAL JOURNAL
==============
Track scanner signals and their outcomes to learn what works.

Features:
1. Log daily scanner results automatically
2. Track signal outcomes (hit target, stopped out, expired)
3. Calculate signal accuracy by type, regime, sector
4. Learn from mistakes and successes
5. Improve scanner parameters based on historical performance

Usage:
    # Log today's scanner signals
    python tools/signal_journal.py log results/scan_20260114_110817.txt
    
    # Review signal outcomes
    python tools/signal_journal.py review
    
    # Update outcomes for past signals
    python tools/signal_journal.py update XPEV --outcome WIN --exit_price 25.00
    
    # Performance analysis
    python tools/signal_journal.py analyze
    
    # Show pending signals (not yet resolved)
    python tools/signal_journal.py pending
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import re
import yfinance as yf
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# DATA STORAGE
# ============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
JOURNAL_FILE = DATA_DIR / "signal_journal.json"


def load_journal() -> Dict:
    """Load signal journal from JSON file."""
    if JOURNAL_FILE.exists():
        with open(JOURNAL_FILE, 'r') as f:
            return json.load(f)
    return {"signals": [], "stats": {}, "last_updated": None}


def save_journal(journal: Dict):
    """Save signal journal to JSON file."""
    journal["last_updated"] = datetime.now().isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_FILE, 'w') as f:
        json.dump(journal, f, indent=2, default=str)


# ============================================================================
# SIGNAL PARSING
# ============================================================================

def parse_scan_file(filepath: str) -> List[Dict]:
    """Parse a scanner results file and extract signals."""
    signals = []
    
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Extract date from filename or content
        date_match = re.search(r'scan_(\d{8})_', filepath)
        if date_match:
            date_str = date_match.group(1)
            signal_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        else:
            signal_date = datetime.now().strftime("%Y-%m-%d")
        
        lines = content.split('\n')
        
        current_section = None
        
        for line in lines:
            line = line.strip()
            
            # Detect sections
            if 'ACTIONABLE OPPORTUNITIES' in line:
                current_section = 'BUY'
            elif 'WAIT LIST' in line or 'WATCHLIST' in line:
                current_section = 'WAIT'
            elif 'AVOID' in line:
                current_section = 'AVOID'
            
            # Parse signal lines (format: SYMBOL  Price $XXX | Entry ...)
            if current_section and line and line[0].isalpha():
                parts = line.split()
                if parts and len(parts[0]) <= 5 and parts[0].isalpha():
                    symbol = parts[0].upper()
                    
                    # Extract price
                    price_match = re.search(r'Price\s*\$?\s*([\d.]+)', line)
                    price = float(price_match.group(1)) if price_match else None
                    
                    # Extract entry zone
                    entry_match = re.search(r'Entry\s*\$?([\d.]+)-\$?([\d.]+)', line)
                    entry_low = float(entry_match.group(1)) if entry_match else None
                    entry_high = float(entry_match.group(2)) if entry_match else None
                    
                    # Extract stop
                    stop_match = re.search(r'Stop\s*\$?\s*([\d.]+)', line)
                    stop_loss = float(stop_match.group(1)) if stop_match else None
                    
                    # Extract targets
                    t1_match = re.search(r'T1\s*\$?\s*([\d.]+)', line)
                    target_1 = float(t1_match.group(1)) if t1_match else None
                    
                    t2_match = re.search(r'T2\s*\$?\s*([\d.]+)', line)
                    target_2 = float(t2_match.group(1)) if t2_match else None
                    
                    # Extract EV
                    ev_match = re.search(r'EV\s*([+-]?[\d.]+)%', line)
                    ev = float(ev_match.group(1)) if ev_match else None
                    
                    # Extract R:R
                    rr_match = re.search(r'R:R\s*1:([\d.]+)', line)
                    risk_reward = float(rr_match.group(1)) if rr_match else None
                    
                    signal = {
                        "symbol": symbol,
                        "date": signal_date,
                        "signal_type": current_section,
                        "price_at_signal": price,
                        "entry_low": entry_low,
                        "entry_high": entry_high,
                        "stop_loss": stop_loss,
                        "target_1": target_1,
                        "target_2": target_2,
                        "expected_value": ev,
                        "risk_reward": risk_reward,
                        "outcome": None,  # WIN, LOSS, EXPIRED, MISSED
                        "exit_price": None,
                        "exit_date": None,
                        "pnl_pct": None,
                        "notes": "",
                    }
                    
                    # Only add if we got meaningful data
                    if price or entry_low:
                        signals.append(signal)
        
        return signals
        
    except Exception as e:
        print(f"Error parsing file: {e}")
        return []


# ============================================================================
# JOURNAL FUNCTIONS
# ============================================================================

def log_signals(filepath: str):
    """Log signals from a scan file to the journal."""
    signals = parse_scan_file(filepath)
    
    if not signals:
        print(f"❌ No signals found in {filepath}")
        return
    
    journal = load_journal()
    
    # Check for duplicates (same symbol + date)
    existing = {(s["symbol"], s["date"]) for s in journal["signals"]}
    
    new_count = 0
    for signal in signals:
        key = (signal["symbol"], signal["date"])
        if key not in existing:
            journal["signals"].append(signal)
            existing.add(key)
            new_count += 1
    
    save_journal(journal)
    
    print(f"\n✅ Logged {new_count} new signals from {filepath}")
    print(f"   BUY signals: {len([s for s in signals if s['signal_type'] == 'BUY'])}")
    print(f"   WAIT signals: {len([s for s in signals if s['signal_type'] == 'WAIT'])}")
    print(f"   Total in journal: {len(journal['signals'])}")


def update_outcome(symbol: str, outcome: str, exit_price: float = None, notes: str = ""):
    """Update the outcome for a signal."""
    journal = load_journal()
    symbol = symbol.upper()
    
    # Find most recent unresolved signal for this symbol
    target_signal = None
    for signal in reversed(journal["signals"]):
        if signal["symbol"] == symbol and signal["outcome"] is None:
            target_signal = signal
            break
    
    if not target_signal:
        print(f"❌ No unresolved signal found for {symbol}")
        return
    
    target_signal["outcome"] = outcome.upper()
    target_signal["exit_date"] = datetime.now().strftime("%Y-%m-%d")
    
    if exit_price:
        target_signal["exit_price"] = exit_price
        entry_price = target_signal.get("entry_low") or target_signal.get("price_at_signal")
        if entry_price:
            target_signal["pnl_pct"] = (exit_price / entry_price - 1) * 100
    
    if notes:
        target_signal["notes"] = notes
    
    save_journal(journal)
    
    print(f"\n✅ Updated {symbol} signal:")
    print(f"   Date: {target_signal['date']}")
    print(f"   Outcome: {outcome}")
    if exit_price:
        print(f"   Exit: ${exit_price:.2f}")
        if target_signal.get("pnl_pct"):
            print(f"   P&L: {target_signal['pnl_pct']:+.1f}%")


def auto_check_outcomes():
    """Auto-check outcomes for pending signals based on price action."""
    journal = load_journal()
    
    pending = [s for s in journal["signals"] if s["outcome"] is None and s["signal_type"] == "BUY"]
    
    if not pending:
        print("No pending BUY signals to check")
        return
    
    print(f"\n🔍 Checking {len(pending)} pending signals...")
    
    updated = 0
    for signal in pending:
        symbol = signal["symbol"]
        
        try:
            ticker = yf.Ticker(symbol)
            # Get data from signal date to now
            signal_date = datetime.strptime(signal["date"], "%Y-%m-%d")
            data = ticker.history(start=signal_date, end=datetime.now() + timedelta(days=1))
            
            if data.empty:
                continue
            
            entry_price = signal.get("entry_high") or signal.get("price_at_signal")
            stop_loss = signal.get("stop_loss")
            target_1 = signal.get("target_1")
            
            if not entry_price:
                continue
            
            # Check if price hit target or stop
            high_since = data["High"].max()
            low_since = data["Low"].min()
            current_price = data["Close"].iloc[-1]
            
            outcome = None
            exit_price = None
            
            if target_1 and high_since >= target_1:
                outcome = "WIN"
                exit_price = target_1
            elif stop_loss and low_since <= stop_loss:
                outcome = "LOSS"
                exit_price = stop_loss
            elif (datetime.now() - signal_date).days > 30:
                # Expired after 30 days
                outcome = "EXPIRED"
                exit_price = current_price
            
            if outcome:
                signal["outcome"] = outcome
                signal["exit_price"] = exit_price
                signal["exit_date"] = datetime.now().strftime("%Y-%m-%d")
                signal["pnl_pct"] = (exit_price / entry_price - 1) * 100
                updated += 1
                
                icon = "✅" if outcome == "WIN" else ("❌" if outcome == "LOSS" else "⏰")
                print(f"   {icon} {symbol}: {outcome} @ ${exit_price:.2f} ({signal['pnl_pct']:+.1f}%)")
        
        except Exception as e:
            continue
    
    if updated > 0:
        save_journal(journal)
        print(f"\n✅ Updated {updated} signal outcomes")
    else:
        print("   No outcomes to update")


def show_pending():
    """Show pending (unresolved) signals."""
    journal = load_journal()
    
    pending = [s for s in journal["signals"] if s["outcome"] is None]
    
    if not pending:
        print("\n✅ No pending signals")
        return
    
    # Group by type
    buy_signals = [s for s in pending if s["signal_type"] == "BUY"]
    wait_signals = [s for s in pending if s["signal_type"] == "WAIT"]
    
    print(f"\n{'='*80}")
    print(f"  PENDING SIGNALS ({len(pending)} total)")
    print(f"{'='*80}")
    
    if buy_signals:
        print(f"\n  BUY SIGNALS ({len(buy_signals)}):")
        print(f"  {'Symbol':<8} {'Date':<12} {'Entry':>15} {'Stop':>10} {'Target':>10}")
        print(f"  {'-'*60}")
        for s in buy_signals:
            entry = f"${s.get('entry_low', 0):.2f}-${s.get('entry_high', 0):.2f}" if s.get('entry_low') else "N/A"
            stop = f"${s.get('stop_loss', 0):.2f}" if s.get('stop_loss') else "N/A"
            target = f"${s.get('target_1', 0):.2f}" if s.get('target_1') else "N/A"
            print(f"  {s['symbol']:<8} {s['date']:<12} {entry:>15} {stop:>10} {target:>10}")
    
    if wait_signals:
        print(f"\n  WAIT/WATCH ({len(wait_signals)}):")
        for s in wait_signals[:10]:  # Show first 10
            print(f"  {s['symbol']:<8} {s['date']:<12}")


def analyze_performance():
    """Analyze signal performance."""
    journal = load_journal()
    
    resolved = [s for s in journal["signals"] if s["outcome"] is not None]
    
    if not resolved:
        print("\n❌ No resolved signals to analyze")
        return
    
    print(f"\n{'='*80}")
    print(f"  SIGNAL PERFORMANCE ANALYSIS")
    print(f"{'='*80}")
    
    # Overall stats
    wins = [s for s in resolved if s["outcome"] == "WIN"]
    losses = [s for s in resolved if s["outcome"] == "LOSS"]
    expired = [s for s in resolved if s["outcome"] == "EXPIRED"]
    
    total = len(resolved)
    win_rate = len(wins) / total * 100 if total > 0 else 0
    
    print(f"\n  OVERALL:")
    print(f"  Total Signals: {total}")
    print(f"  Wins: {len(wins)} | Losses: {len(losses)} | Expired: {len(expired)}")
    print(f"  Win Rate: {win_rate:.1f}%")
    
    # Average P&L
    pnls = [s["pnl_pct"] for s in resolved if s.get("pnl_pct") is not None]
    if pnls:
        avg_pnl = sum(pnls) / len(pnls)
        avg_win = sum(s["pnl_pct"] for s in wins if s.get("pnl_pct")) / len(wins) if wins else 0
        avg_loss = sum(s["pnl_pct"] for s in losses if s.get("pnl_pct")) / len(losses) if losses else 0
        
        print(f"\n  P&L:")
        print(f"  Average P&L: {avg_pnl:+.2f}%")
        print(f"  Avg Win: {avg_win:+.2f}%")
        print(f"  Avg Loss: {avg_loss:+.2f}%")
        
        # Expected Value
        ev = win_rate/100 * avg_win + (1 - win_rate/100) * avg_loss
        print(f"  Expected Value: {ev:+.2f}%")
    
    # By signal type
    print(f"\n  BY SIGNAL TYPE:")
    for sig_type in ["BUY", "WAIT"]:
        type_signals = [s for s in resolved if s["signal_type"] == sig_type]
        if type_signals:
            type_wins = len([s for s in type_signals if s["outcome"] == "WIN"])
            type_wr = type_wins / len(type_signals) * 100
            print(f"  {sig_type}: {len(type_signals)} signals, {type_wr:.1f}% win rate")
    
    # By EV bucket
    print(f"\n  BY EXPECTED VALUE:")
    ev_buckets = {"EV < 0": [], "0-1%": [], "1-2%": [], "2%+": []}
    for s in resolved:
        ev = s.get("expected_value")
        if ev is None:
            continue
        if ev < 0:
            ev_buckets["EV < 0"].append(s)
        elif ev < 1:
            ev_buckets["0-1%"].append(s)
        elif ev < 2:
            ev_buckets["1-2%"].append(s)
        else:
            ev_buckets["2%+"].append(s)
    
    for bucket, signals in ev_buckets.items():
        if signals:
            bucket_wins = len([s for s in signals if s["outcome"] == "WIN"])
            bucket_wr = bucket_wins / len(signals) * 100
            print(f"  {bucket}: {len(signals)} signals, {bucket_wr:.1f}% win rate")
    
    # Lessons learned
    print(f"\n  INSIGHTS:")
    if win_rate > 55:
        print(f"  ✅ Scanner is generating profitable signals (WR > 55%)")
    elif win_rate > 45:
        print(f"  ⚠️ Win rate is marginal - review signal criteria")
    else:
        print(f"  ❌ Low win rate - scanner needs improvement")
    
    # Check if high EV signals perform better
    high_ev = [s for s in resolved if s.get("expected_value", 0) >= 1.5]
    if high_ev:
        high_ev_wins = len([s for s in high_ev if s["outcome"] == "WIN"])
        high_ev_wr = high_ev_wins / len(high_ev) * 100
        if high_ev_wr > win_rate:
            print(f"  ✅ High EV signals (≥1.5%) have better win rate: {high_ev_wr:.1f}%")
        else:
            print(f"  ⚠️ High EV signals underperforming - review EV calculation")


def show_recent(days: int = 7):
    """Show recent signals and their outcomes."""
    journal = load_journal()
    
    cutoff = datetime.now() - timedelta(days=days)
    recent = [s for s in journal["signals"] 
              if datetime.strptime(s["date"], "%Y-%m-%d") >= cutoff]
    
    if not recent:
        print(f"\n📊 No signals in last {days} days")
        return
    
    print(f"\n{'='*90}")
    print(f"  RECENT SIGNALS (Last {days} days)")
    print(f"{'='*90}")
    
    print(f"\n{'Symbol':<8} {'Date':<12} {'Type':<6} {'EV':>8} {'Outcome':<10} {'P&L':>8}")
    print("-" * 60)
    
    for s in sorted(recent, key=lambda x: x["date"], reverse=True):
        ev = f"{s.get('expected_value', 0):+.1f}%" if s.get('expected_value') else "N/A"
        outcome = s.get("outcome") or "PENDING"
        pnl = f"{s.get('pnl_pct', 0):+.1f}%" if s.get("pnl_pct") else ""
        
        outcome_icon = {
            "WIN": "✅",
            "LOSS": "❌",
            "EXPIRED": "⏰",
            "PENDING": "⏳"
        }.get(outcome, "")
        
        print(f"{s['symbol']:<8} {s['date']:<12} {s['signal_type']:<6} {ev:>8} "
              f"{outcome_icon} {outcome:<8} {pnl:>8}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Signal Journal")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Log signals
    log_parser = subparsers.add_parser("log", help="Log signals from scan file")
    log_parser.add_argument("filepath", type=str, help="Path to scan results file")
    
    # Update outcome
    update_parser = subparsers.add_parser("update", help="Update signal outcome")
    update_parser.add_argument("symbol", type=str, help="Stock symbol")
    update_parser.add_argument("--outcome", type=str, required=True, 
                               choices=["WIN", "LOSS", "EXPIRED", "MISSED"],
                               help="Signal outcome")
    update_parser.add_argument("--exit_price", type=float, help="Exit price")
    update_parser.add_argument("--notes", type=str, default="", help="Notes")
    
    # Auto-check
    subparsers.add_parser("check", help="Auto-check outcomes for pending signals")
    
    # Show pending
    subparsers.add_parser("pending", help="Show pending signals")
    
    # Analyze
    subparsers.add_parser("analyze", help="Analyze signal performance")
    
    # Recent
    recent_parser = subparsers.add_parser("recent", help="Show recent signals")
    recent_parser.add_argument("--days", type=int, default=7, help="Days to look back")
    
    args = parser.parse_args()
    
    if args.command == "log":
        log_signals(args.filepath)
    elif args.command == "update":
        update_outcome(args.symbol, args.outcome, args.exit_price, args.notes)
    elif args.command == "check":
        auto_check_outcomes()
    elif args.command == "pending":
        show_pending()
    elif args.command == "analyze":
        analyze_performance()
    elif args.command == "recent":
        show_recent(args.days)
    else:
        # Default: show recent + pending
        show_recent(7)
        show_pending()


if __name__ == "__main__":
    main()
