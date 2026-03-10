#!/usr/bin/env python3
"""
COMPREHENSIVE MARKET SCANNER
=============================
Scan the entire US stock market to find the best opportunities.

Features:
1. Scan ALL tradable US stocks (S&P 500, NASDAQ, etc.)
2. Find "Recovery Champions" - stocks that bounced from major drawdowns
3. Screen for solid fundamentals
4. Identify momentum plays and undervalued opportunities
5. Categorize by themes (AI, Space, Crypto, etc.)

Usage:
    # Quick scan (default watchlist)
    python tools/market_scanner.py
    
    # Full market scan (takes longer)
    python tools/market_scanner.py --full
    
    # Focus on recovery plays
    python tools/market_scanner.py --recovery
    
    # Scan specific theme
    python tools/market_scanner.py --theme ai_chips
    
    # Output to file
    python tools/market_scanner.py --full -o scan_results.txt
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import argparse
from datetime import datetime
from typing import List, Optional
import warnings
warnings.filterwarnings('ignore')

from src.universe.universe_manager import (
    UniverseManager, StockInfo, MarketCap,
    get_all_us_stocks, THEME_KEYWORDS
)
from src.universe.screener import (
    ComprehensiveScreener, ScreenResult,
    FundamentalScreener, RecoveryScreener, MomentumScreener, UndervaluedScreener
)
from src.universe.filters import (
    filter_tradable, filter_recovery_plays, filter_by_theme, filter_by_market_cap
)


def print_header(title: str, file=None):
    """Print section header."""
    line = "=" * 100
    print(f"\n{line}", file=file)
    print(f"  {title}", file=file)
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", file=file)
    print(f"{line}\n", file=file)


def print_stock_table(
    stocks: List[StockInfo],
    title: str,
    limit: int = 20,
    file=None
):
    """Print stock table."""
    print(f"\n{'─' * 100}", file=file)
    print(f"  {title} ({len(stocks)} total, showing top {min(len(stocks), limit)})", file=file)
    print(f"{'─' * 100}", file=file)
    
    print(f"{'Symbol':<8} {'Name':<25} {'Price':>10} {'MktCap':>12} {'DD%':>8} {'Rec%':>8} {'Sector':<20}", file=file)
    print("-" * 100, file=file)
    
    for stock in stocks[:limit]:
        name = stock.name[:24] if stock.name else ""
        cap = f"${stock.market_cap/1e9:.1f}B" if stock.market_cap >= 1e9 else f"${stock.market_cap/1e6:.0f}M"
        sector = stock.sector[:19] if stock.sector else ""
        
        print(f"{stock.symbol:<8} {name:<25} ${stock.price:>9.2f} {cap:>12} "
              f"{stock.max_drawdown:>7.1f}% {stock.recovery_from_low:>7.1f}% {sector:<20}", file=file)


def print_screen_results(
    results: List[ScreenResult],
    title: str,
    limit: int = 20,
    file=None
):
    """Print screening results."""
    print(f"\n{'─' * 110}", file=file)
    print(f"  {title} ({len(results)} total, showing top {min(len(results), limit)})", file=file)
    print(f"{'─' * 110}", file=file)
    
    print(f"{'Symbol':<8} {'Grade':>6} {'Score':>6} {'Fund':>6} {'Mom':>6} {'Value':>6} {'Recov':>6} {'Price':>10} {'Type':<20}", file=file)
    print("-" * 110, file=file)
    
    for r in results[:limit]:
        play_type = []
        if r.is_recovery_play:
            play_type.append("Recovery")
        if r.is_momentum_play:
            play_type.append("Momentum")
        if r.is_value_play:
            play_type.append("Value")
        if r.is_quality:
            play_type.append("Quality")
        
        type_str = "+".join(play_type) if play_type else "Mixed"
        
        print(f"{r.symbol:<8} {r.grade:>6} {r.total_score:>5.0f} {r.fundamental_score:>5.0f} "
              f"{r.momentum_score:>5.0f} {r.value_score:>5.0f} {r.recovery_score:>5.0f} "
              f"${r.stock_info.price:>9.2f} {type_str:<20}", file=file)


def print_detailed_picks(
    results: List[ScreenResult],
    title: str,
    limit: int = 10,
    file=None
):
    """Print detailed analysis of top picks."""
    print(f"\n{'=' * 100}", file=file)
    print(f"  {title}", file=file)
    print(f"{'=' * 100}", file=file)
    
    for r in results[:limit]:
        stock = r.stock_info
        
        print(f"\n{'─' * 80}", file=file)
        print(f"  {r.symbol} - Grade {r.grade} (Score: {r.total_score:.0f}/100)", file=file)
        print(f"{'─' * 80}", file=file)
        
        print(f"  Company: {stock.name}", file=file)
        print(f"  Sector:  {stock.sector} | Industry: {stock.industry}", file=file)
        print(f"  Price:   ${stock.price:.2f} | Market Cap: ${stock.market_cap/1e9:.2f}B", file=file)
        
        print(f"\n  PRICE ACTION:", file=file)
        print(f"    52W High: ${stock.high_52w:.2f} | 52W Low: ${stock.low_52w:.2f}", file=file)
        print(f"    Drawdown: {stock.max_drawdown:.1f}% | Recovery: {stock.recovery_from_low:.1f}%", file=file)
        print(f"    Distance from High: {stock.dist_from_high:.1f}%", file=file)
        
        print(f"\n  FUNDAMENTALS:", file=file)
        pe = f"{stock.pe_ratio:.1f}" if stock.pe_ratio else "N/A"
        peg = f"{stock.peg_ratio:.2f}" if stock.peg_ratio else "N/A"
        margin = f"{stock.profit_margin:.1%}" if stock.profit_margin else "N/A"
        roe = f"{stock.roe:.1%}" if stock.roe else "N/A"
        print(f"    P/E: {pe} | PEG: {peg} | Margin: {margin} | ROE: {roe}", file=file)
        
        print(f"\n  SCORES:", file=file)
        print(f"    Fundamental: {r.fundamental_score:.0f} | Momentum: {r.momentum_score:.0f} | "
              f"Value: {r.value_score:.0f} | Recovery: {r.recovery_score:.0f}", file=file)
        
        if stock.themes:
            print(f"\n  THEMES: {', '.join(stock.themes)}", file=file)
        
        if r.strengths:
            print(f"\n  STRENGTHS: {', '.join(r.strengths[:5])}", file=file)
        if r.weaknesses:
            print(f"  WATCH: {', '.join(r.weaknesses[:3])}", file=file)
        
        print(f"\n  ANALYSIS: {r.reasoning}", file=file)


def print_by_theme(stocks: List[StockInfo], file=None):
    """Print stocks grouped by theme."""
    print(f"\n{'=' * 100}", file=file)
    print(f"  STOCKS BY THEME", file=file)
    print(f"{'=' * 100}", file=file)
    
    for theme in sorted(THEME_KEYWORDS.keys()):
        themed = filter_by_theme(stocks, [theme])
        if themed:
            print(f"\n  {theme.upper()} ({len(themed)} stocks)", file=file)
            symbols = [s.symbol for s in themed[:15]]
            print(f"    {', '.join(symbols)}", file=file)


def main():
    parser = argparse.ArgumentParser(description="Comprehensive Market Scanner")
    parser.add_argument("--full", action="store_true", help="Full market scan (slower)")
    parser.add_argument("--recovery", action="store_true", help="Focus on recovery plays")
    parser.add_argument("--momentum", action="store_true", help="Focus on momentum plays")
    parser.add_argument("--value", action="store_true", help="Focus on value plays")
    parser.add_argument("--theme", type=str, help="Focus on specific theme")
    parser.add_argument("--min-cap", type=float, default=500, help="Min market cap ($M)")
    parser.add_argument("--min-score", type=float, default=50, help="Min total score")
    parser.add_argument("-o", "--output", type=str, help="Output file path")
    parser.add_argument("--workers", type=int, default=20, help="Parallel workers")
    
    args = parser.parse_args()
    
    # Setup output
    output_file = None
    if args.output:
        output_file = open(args.output, 'w', encoding='utf-8')
    
    def progress(done, total):
        pct = done / total * 100
        print(f"\r  Scanning: {done}/{total} ({pct:.0f}%)  ", end="", flush=True)
    
    print("\n🔍 COMPREHENSIVE MARKET SCANNER")
    print("=" * 50)
    
    # Initialize manager
    manager = UniverseManager()
    
    # Get symbols to scan
    if args.theme:
        if args.theme in THEME_KEYWORDS:
            symbols = THEME_KEYWORDS[args.theme].get('symbols', [])
            print(f"\n📊 Scanning theme: {args.theme} ({len(symbols)} symbols)")
        else:
            print(f"❌ Unknown theme: {args.theme}")
            print(f"   Available: {', '.join(THEME_KEYWORDS.keys())}")
            return
    elif args.full:
        symbols = get_all_us_stocks()
        print(f"\n📊 Full market scan: {len(symbols)} symbols")
    else:
        # Default: quick scan with major stocks
        symbols = get_all_us_stocks()[:300]  # Top 300
        print(f"\n📊 Quick scan: {len(symbols)} symbols")
    
    # Scan
    print("\n⏳ Scanning universe (this may take a few minutes)...")
    stocks = manager.scan_universe(
        symbols=symbols,
        min_price=5.0,
        min_volume=300000,
        min_market_cap=args.min_cap * 1_000_000,
        max_workers=args.workers,
        progress_callback=progress
    )
    print(f"\n✅ Scan complete: {len(stocks)} stocks passed filters")
    
    if not stocks:
        print("\n❌ No stocks found matching criteria")
        return
    
    # Run comprehensive screening
    print("\n📈 Running comprehensive screening...")
    screener = ComprehensiveScreener()
    results = screener.screen_all(stocks, min_score=args.min_score)
    
    # Print results
    file = output_file
    print_header("MARKET SCAN RESULTS", file=file)
    
    # Summary
    print(f"  Total Scanned: {len(symbols)}", file=file)
    print(f"  Passed Filters: {len(stocks)}", file=file)
    print(f"  Passed Screening: {len(results)}", file=file)
    
    # Grade distribution
    grades = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0}
    for r in results:
        grades[r.grade] = grades.get(r.grade, 0) + 1
    
    print(f"\n  Grade Distribution:", file=file)
    for grade in ['A', 'B', 'C', 'D', 'F']:
        count = grades[grade]
        bar = "█" * (count // 2)
        print(f"    {grade}: {count:3} {bar}", file=file)
    
    # Recovery plays
    recovery_plays = [r for r in results if r.is_recovery_play]
    if recovery_plays or args.recovery:
        print_screen_results(recovery_plays, "🔄 RECOVERY CHAMPIONS", limit=20, file=file)
        
        # Detailed for top recovery plays
        print_detailed_picks(recovery_plays[:5], "TOP RECOVERY PLAYS - DETAILED", limit=5, file=file)
    
    # Momentum plays
    momentum_plays = [r for r in results if r.is_momentum_play]
    if momentum_plays or args.momentum:
        print_screen_results(momentum_plays, "🚀 MOMENTUM LEADERS", limit=15, file=file)
    
    # Value plays
    value_plays = [r for r in results if r.is_value_play]
    if value_plays or args.value:
        print_screen_results(value_plays, "💰 VALUE OPPORTUNITIES", limit=15, file=file)
    
    # Quality plays
    quality_plays = [r for r in results if r.is_quality and r.grade in ['A', 'B']]
    if quality_plays:
        print_screen_results(quality_plays, "⭐ QUALITY STOCKS", limit=15, file=file)
    
    # Top overall picks
    top_picks = [r for r in results if r.grade in ['A', 'B']]
    if top_picks:
        print_detailed_picks(top_picks, "🏆 TOP PICKS - DETAILED ANALYSIS", limit=10, file=file)
    
    # By theme
    print_by_theme(stocks, file=file)
    
    # Summary table
    print(f"\n{'=' * 100}", file=file)
    print("  ACTIONABLE SUMMARY", file=file)
    print(f"{'=' * 100}\n", file=file)
    
    print("  RECOVERY CHAMPIONS (bounced from major drawdown):", file=file)
    for r in recovery_plays[:10]:
        s = r.stock_info
        print(f"    {r.symbol:8} | Score: {r.total_score:.0f} | DD: {s.max_drawdown:.0f}% → Rec: {s.recovery_from_low:.0f}% | ${s.price:.2f}", file=file)
    
    print("\n  MOMENTUM LEADERS (strong trend, near highs):", file=file)
    for r in momentum_plays[:10]:
        s = r.stock_info
        print(f"    {r.symbol:8} | Score: {r.total_score:.0f} | {s.dist_from_high:.0f}% from high | ${s.price:.2f}", file=file)
    
    print("\n  VALUE OPPORTUNITIES (beaten down quality):", file=file)
    for r in value_plays[:10]:
        s = r.stock_info
        pe = f"P/E {s.pe_ratio:.0f}" if s.pe_ratio else ""
        print(f"    {r.symbol:8} | Score: {r.total_score:.0f} | {s.dist_from_high:.0f}% off high | {pe} | ${s.price:.2f}", file=file)
    
    print(f"\n{'=' * 100}", file=file)
    print(f"  END OF SCAN - {len(results)} stocks analyzed", file=file)
    print(f"{'=' * 100}", file=file)
    
    if output_file:
        output_file.close()
        print(f"\n✅ Results saved to: {args.output}")
    
    # Quick summary to console
    print("\n" + "=" * 60)
    print("  QUICK SUMMARY")
    print("=" * 60)
    print(f"\n  🔄 Recovery Champions: {len(recovery_plays)}")
    if recovery_plays:
        top3 = ", ".join([r.symbol for r in recovery_plays[:3]])
        print(f"     Top: {top3}")
    
    print(f"\n  🚀 Momentum Leaders: {len(momentum_plays)}")
    if momentum_plays:
        top3 = ", ".join([r.symbol for r in momentum_plays[:3]])
        print(f"     Top: {top3}")
    
    print(f"\n  💰 Value Plays: {len(value_plays)}")
    if value_plays:
        top3 = ", ".join([r.symbol for r in value_plays[:3]])
        print(f"     Top: {top3}")
    
    print(f"\n  ⭐ Grade A Picks: {grades['A']}")
    a_picks = [r.symbol for r in results if r.grade == 'A']
    if a_picks:
        print(f"     {', '.join(a_picks[:10])}")
    
    print()


if __name__ == "__main__":
    main()
