#!/usr/bin/env python3
"""
Pipeline Skill — CLI Bridge for Claude Agent
=============================================
Allows Claude Code (via @claudecode) to invoke the signal pipeline,
scanner, portfolio manager, and analyzers as a single command.

Usage (from Claude Code CLI):
    python pipeline_skill.py scan                      # Scan universe + Discord signals
    python pipeline_skill.py candidates                # Show current candidates
    python pipeline_skill.py analyze TICKER            # Deep analyze a single ticker
    python pipeline_skill.py approve TICKER [TICKER2]  # Approve tickers for execution
    python pipeline_skill.py positions                 # Show current positions
    python pipeline_skill.py portfolio                 # Portfolio health + suggestions

All output is plain text (no Discord formatting) so Claude can parse and
relay it in its own format.
"""

import os
import sys
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

# Setup path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../core_analysis'))

logging.basicConfig(level=logging.WARNING, format="%(message)s")
log = logging.getLogger("pipeline-skill")

# Late imports to avoid startup noise
import signal_pipeline
import trade_executor


# =========================================================================
# Helpers
# =========================================================================

DATA_FILE = os.path.join(os.path.dirname(__file__), '../../data/real_discord_messages_goku_wilson_60d.txt')
UNIVERSE_FILE = os.path.join(os.path.dirname(__file__), '../../../config/stock_universe.yaml')
CANDIDATES_HISTORY_DIR = os.path.join(os.path.dirname(__file__), '../../data/candidates_history')


def load_universe_tickers(max_tickers: int = 100) -> List[str]:
    """Load stock universe from YAML config.

    Priority: high_conviction > momentum_leaders > volatile_momentum > quick_test > all_symbols
    Returns up to max_tickers unique tickers.
    """
    try:
        import yaml
        with open(UNIVERSE_FILE, 'r') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        log.warning(f"Could not load universe: {e}")
        return []

    seen = set()
    ordered = []

    # Priority lists first
    for key in ['high_conviction', 'volatile_momentum', 'quick_test']:
        for sym in data.get(key, []):
            s = str(sym).upper().strip('"')
            if s not in seen:
                seen.add(s)
                ordered.append(s)

    # Then themed categories
    themes = data.get('themes', {})
    priority_themes = [
        'mag7', 'ai_chips', 'ai_software', 'momentum_leaders',
        'fintech', 'cybersecurity', 'clean_energy', 'crypto',
        'wilson', 'saiyan', 'moonvest',
    ]
    for theme_key in priority_themes:
        theme = themes.get(theme_key, {})
        for sym in theme.get('symbols', []):
            s = str(sym).upper().strip('"')
            if s not in seen:
                seen.add(s)
                ordered.append(s)

    # Fill up from all_symbols
    for sym in data.get('all_symbols', []):
        s = str(sym).upper().strip('"')
        if s not in seen:
            seen.add(s)
            ordered.append(s)

    return ordered[:max_tickers]


def save_candidates_with_timestamp(candidates: List[signal_pipeline.SignalCandidate]):
    """Save candidates to both the live file and a dated history file."""
    # Save to live pipeline file (for !approve, !portfolio, etc.)
    signal_pipeline.save_candidates(candidates)

    # Save dated copy for historical replay/audit
    os.makedirs(CANDIDATES_HISTORY_DIR, exist_ok=True)
    date_str = datetime.now().strftime('%Y%m%d_%H%M')
    history_file = os.path.join(CANDIDATES_HISTORY_DIR, f'candidates_{date_str}.json')
    data = [c.to_dict() for c in candidates]
    with open(history_file, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    log.info(f"Saved {len(candidates)} candidates to {history_file}")


# =========================================================================
# Commands
# =========================================================================

async def cmd_scan(max_universe: int = 80) -> str:
    """Run full pipeline scan: Discord signals + universe tickers.

    Scans up to max_universe tickers from the stock universe config,
    merged with tickers mentioned in Discord.
    """
    lines = [f"=== Pipeline Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n"]

    # Phase 1: Discord signals
    lines.append("Phase 1: Collecting Discord signals...")
    discord_signals = await signal_pipeline.collect_discord_signals(DATA_FILE, days=3)
    discord_tickers = sorted(discord_signals.keys(), key=lambda t: discord_signals[t]['mentions'], reverse=True)
    lines.append(f"  Found {len(discord_tickers)} tickers from Discord")
    if discord_tickers[:10]:
        top = ', '.join(f"{t}({discord_signals[t]['mentions']})" for t in discord_tickers[:10])
        lines.append(f"  Top: {top}")

    # Phase 2: Load universe tickers
    lines.append(f"\nPhase 2: Loading stock universe (max {max_universe})...")
    universe = load_universe_tickers(max_universe)
    lines.append(f"  Loaded {len(universe)} universe tickers")

    # Merge: Discord tickers first, then universe fill
    all_tickers = list(dict.fromkeys(discord_tickers + universe))  # preserve order, dedup
    lines.append(f"\nPhase 3: Scanning {len(all_tickers)} unique tickers via GCloud API...")

    # Scan in batches (API rate limit)
    scanner_results = {}
    batch_size = 10
    scanned = 0
    for i in range(0, len(all_tickers), batch_size):
        batch = all_tickers[i:i + batch_size]
        batch_results = await signal_pipeline.collect_scanner_signals(
            trade_executor.call_api, batch
        )
        scanner_results.update(batch_results)
        scanned += len(batch)
        lines.append(f"  Scanned {scanned}/{len(all_tickers)} — got {len(batch_results)} results")
        await asyncio.sleep(0.5)  # Courtesy delay between batches

    lines.append(f"\n  Scanner returned data for {len(scanner_results)} tickers")

    # Phase 4: Build and rank candidates
    lines.append("\nPhase 4: Building candidates and scoring...")
    candidates = signal_pipeline.build_candidates(discord_signals, scanner_results)
    lines.append(f"  Built {len(candidates)} candidates")

    # Save with timestamp
    save_candidates_with_timestamp(candidates)

    # Phase 5: Summary
    buy_candidates = [c for c in candidates if c.action == "BUY"]
    wait_candidates = [c for c in candidates if c.action == "WAIT"]

    lines.append(f"\n{'=' * 50}")
    lines.append(f"RESULTS: {len(buy_candidates)} BUY | {len(wait_candidates)} WAIT | {len(candidates)} total")
    lines.append(f"{'=' * 50}\n")

    for i, c in enumerate(candidates[:15], 1):
        icon = {"BUY": "BUY ", "WAIT": "WAIT", "AVOID": "SKIP", "NO_TRADE": " -- "}.get(c.action, "    ")
        price_str = f"${c.current_price:.2f}" if c.current_price else "N/A"
        zone = ""
        if c.scanner_buy_low and c.scanner_buy_high:
            zone = f" Zone: ${c.scanner_buy_low:.2f}-${c.scanner_buy_high:.2f}"
        stop = f" Stop: ${c.scanner_stop:.2f}" if c.scanner_stop else ""
        target = f" Tgt: ${c.scanner_target_1:.2f}" if c.scanner_target_1 else ""
        ev = f" EV:{c.scanner_ev:.1f}%" if c.scanner_ev else ""
        rr = f" R:R {c.scanner_rr:.1f}" if c.scanner_rr else ""
        disc = f" Discord:{c.discord_mentions}" if c.discord_mentions else ""

        lines.append(
            f"  {i:2d}. [{icon}] ${c.ticker:5s} Score:{c.conviction_score:.1f} "
            f"Price:{price_str}{zone}{stop}{target}{ev}{rr}{disc}"
        )

    lines.append(f"\nCandidates saved to pipeline_candidates.json + candidates_history/")
    return "\n".join(lines)


async def cmd_candidates() -> str:
    """Show current pipeline candidates."""
    candidates = signal_pipeline.load_candidates()
    if not candidates:
        return "No pipeline candidates found. Run 'scan' first."

    lines = [f"=== Current Candidates ({len(candidates)}) ===\n"]
    lines.append(f"{'#':>3} {'Action':6} {'Ticker':6} {'Score':6} {'Price':>8} {'BuyZone':>18} {'Stop':>8} {'Target':>8} {'EV':>6} {'R:R':>5}")
    lines.append("-" * 90)

    for i, c in enumerate(candidates[:20], 1):
        action = c.action or "N/A"
        price = f"${c.current_price:.2f}" if c.current_price else "N/A"
        zone = f"${c.scanner_buy_low:.2f}-${c.scanner_buy_high:.2f}" if c.scanner_buy_low else "N/A"
        stop = f"${c.scanner_stop:.2f}" if c.scanner_stop else "N/A"
        target = f"${c.scanner_target_1:.2f}" if c.scanner_target_1 else "N/A"
        ev = f"{c.scanner_ev:.1f}%" if c.scanner_ev else "N/A"
        rr = f"{c.scanner_rr:.1f}" if c.scanner_rr else "N/A"

        lines.append(f"{i:3d} {action:6s} {c.ticker:6s} {c.conviction_score:5.1f} {price:>8} {zone:>18} {stop:>8} {target:>8} {ev:>6} {rr:>5}")

    return "\n".join(lines)


async def cmd_analyze(ticker: str) -> str:
    """Deep analyze a single ticker using GCloud scanner + price context."""
    lines = [f"=== Analysis: ${ticker.upper()} ===\n"]

    # Scanner analysis
    try:
        data = await trade_executor.call_api(f"/api/analyze/{ticker.upper()}")
        if data and data.get("success"):
            scan = data.get('scan', {})
            tech = data.get('technical', {})

            lines.append(f"Action:    {scan.get('action', 'N/A')}")
            lines.append(f"Regime:    {scan.get('regime', 'N/A')}")
            lines.append(f"Strategy:  {scan.get('strategy', 'N/A')}")
            price = tech.get('price') or scan.get('price', 0)
            lines.append(f"Price:     ${price:.2f}" if price else "Price: N/A")
            lines.append(f"RSI:       {tech.get('rsi', 'N/A')}")

            bz_low = scan.get('buy_zone_low', 0)
            bz_high = scan.get('buy_zone_high', 0)
            if bz_low and bz_high:
                lines.append(f"Buy Zone:  ${bz_low:.2f} - ${bz_high:.2f}")
            lines.append(f"Stop Loss: ${scan.get('stop_loss', 0):.2f}")
            lines.append(f"Target 1:  ${scan.get('target_1', 0):.2f}")
            lines.append(f"Target 2:  ${scan.get('target_2', 0):.2f}")
            lines.append(f"EV:        {scan.get('expected_value', 0):.1f}%")
            lines.append(f"R:R:       {scan.get('risk_reward', 0):.1f}:1")
            lines.append(f"Win Rate:  {scan.get('win_rate', 0) * 100:.0f}%")
            lines.append(f"Position:  {scan.get('position_size_pct', 0) * 100:.1f}% of portfolio")
        else:
            lines.append(f"Scanner returned no data for {ticker}")
    except Exception as e:
        lines.append(f"Scanner error: {e}")

    # Discord context
    lines.append(f"\n--- Discord Signals ---")
    discord_signals = await signal_pipeline.collect_discord_signals(DATA_FILE, days=7)
    sig = discord_signals.get(ticker.upper(), {})
    if sig:
        lines.append(f"Mentions:  {sig.get('mentions', 0)}")
        lines.append(f"Bullish:   {sig.get('bullish', 0)}")
        lines.append(f"Bearish:   {sig.get('bearish', 0)}")
        lines.append(f"Authors:   {', '.join(sig.get('authors', []))}")
        for s in sig.get('snippets', [])[:3]:
            lines.append(f"  > {s}")
    else:
        lines.append(f"No Discord mentions for {ticker} in last 7 days")

    return "\n".join(lines)


async def cmd_positions() -> str:
    """Show current portfolio positions."""
    try:
        positions = await trade_executor.call_api("/api/positions")
    except Exception as e:
        return f"Error fetching positions: {e}"

    if isinstance(positions, dict) and "error" in positions:
        return f"API error: {positions['error']}"

    if not positions or (isinstance(positions, list) and len(positions) == 0):
        return "No open positions."

    lines = [f"=== Open Positions ({len(positions)}) ===\n"]
    lines.append(f"{'Symbol':8} {'Qty':>6} {'AvgCost':>9} {'MktPrice':>9} {'P&L':>10} {'P&L%':>7}")
    lines.append("-" * 55)

    total_pnl = 0
    for pos in positions:
        sym = pos.get('symbol', '?')
        qty = pos.get('quantity', 0)
        avg = pos.get('avg_cost', 0)
        mkt = pos.get('market_price', 0)
        pnl = pos.get('pnl', 0)
        total_pnl += pnl
        pnl_pct = ((mkt / avg - 1) * 100) if avg > 0 else 0

        lines.append(f"{sym:8s} {qty:6d} ${avg:8.2f} ${mkt:8.2f} ${pnl:9.2f} {pnl_pct:6.1f}%")

    lines.append("-" * 55)
    lines.append(f"{'Total':8s} {'':6s} {'':9s} {'':9s} ${total_pnl:9.2f}")

    return "\n".join(lines)


async def cmd_portfolio() -> str:
    """Portfolio health check with management suggestions."""
    # Get positions
    try:
        positions = await trade_executor.call_api("/api/positions")
    except Exception as e:
        return f"Error: {e}"

    if isinstance(positions, dict) and "error" in positions:
        return f"API error: {positions['error']}"

    if not positions:
        return "No open positions to review."

    # Load latest candidates
    candidates = signal_pipeline.load_candidates()

    # Generate suggestions
    report = signal_pipeline.format_portfolio_suggestions(positions, candidates)
    return f"=== Portfolio Review ===\n\n{report}"


async def cmd_approve(tickers: List[str]) -> str:
    """Approve tickers from pipeline candidates for execution."""
    candidates = signal_pipeline.load_candidates()
    if not candidates:
        return "No pipeline candidates. Run 'scan' first."

    results = []
    for ticker in tickers:
        ticker = ticker.upper().replace("$", "")
        match = next((c for c in candidates if c.ticker == ticker), None)
        if not match:
            results.append(f"  SKIP {ticker}: not in candidates")
            continue

        params = signal_pipeline.generate_order_params(match, "limit")
        signal_pipeline.save_approved(ticker, params)

        try:
            result = await trade_executor.call_api("/api/trade", method="POST", payload=params)
            if result.get("success"):
                results.append(
                    f"  OK   {ticker}: {params['order_type']} @ ${params.get('limit_price', 0):.2f} "
                    f"| Stop ${params.get('stop_loss', 0):.2f} | Target ${params.get('target', 0):.2f}"
                )
            else:
                results.append(f"  FAIL {ticker}: {result.get('error', 'unknown')}")
        except Exception as e:
            results.append(f"  ERR  {ticker}: {str(e)[:80]}")

    header = f"=== Approve Results ({len(tickers)} tickers) ===\n"
    return header + "\n".join(results)


# =========================================================================
# Main
# =========================================================================

async def main():
    if len(sys.argv) < 2:
        print("""
Pipeline Skill — CLI interface for Claude agent

Commands:
  scan [N]              Full pipeline scan (N = max universe tickers, default 80)
  candidates            Show current ranked candidates
  analyze TICKER        Deep analyze a single ticker
  approve T1 [T2 ...]   Approve tickers for trading
  positions             Show open positions
  portfolio             Portfolio review + suggestions

Examples:
  python pipeline_skill.py scan
  python pipeline_skill.py scan 50
  python pipeline_skill.py analyze NVDA
  python pipeline_skill.py approve PBR HOOD NVDA
""")
        return

    command = sys.argv[1].lower()

    if command == "scan":
        max_n = int(sys.argv[2]) if len(sys.argv) > 2 else 80
        result = await cmd_scan(max_universe=max_n)
    elif command == "candidates":
        result = await cmd_candidates()
    elif command == "analyze" and len(sys.argv) > 2:
        result = await cmd_analyze(sys.argv[2])
    elif command == "approve" and len(sys.argv) > 2:
        result = await cmd_approve(sys.argv[2:])
    elif command == "positions":
        result = await cmd_positions()
    elif command == "portfolio":
        result = await cmd_portfolio()
    else:
        result = f"Unknown command: {command}. Run without args for help."

    print(result)


if __name__ == "__main__":
    asyncio.run(main())
