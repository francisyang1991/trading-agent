#!/usr/bin/env python3
"""
Wyckoff Market Structure Visualization Tool

Draws annotated candlestick charts showing:
- Swing highs/lows
- Equal highs/lows (liquidity pools)
- BOS (Break of Structure) events
- CHOCH (Change of Character) events
- Liquidity sweeps
- Demand/supply zones
- Wyckoff phase annotations

Usage:
    python tools/wyckoff_chart.py                        # SPY daily, last 120 bars
    python tools/wyckoff_chart.py --csv data/daily/SPY.csv --last 200
    python tools/wyckoff_chart.py --ticker TSLA --interval 1h --days 30
    python tools/wyckoff_chart.py --output chart.png

Data sources:
    --csv <path>     : Load from local CSV file
    --ticker <SYM>   : Download from yfinance (if network available)
    --synthetic      : Generate synthetic data showing Wyckoff patterns
"""

import argparse
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.indicators.market_structure import (
    MarketStructureAnalyzer,
    StructureAnalysis,
    StructureBreakType,
    StructureDirection,
)
from src.indicators.wyckoff_model import (
    WyckoffModel,
    WyckoffAnalysis,
    MarketContext,
    WyckoffPhase,
)
from src.indicators.trend import TrendIndicators


# =============================================================================
# DATA GENERATION
# =============================================================================

def generate_wyckoff_data(n_bars: int = 300, seed: int = 42) -> pd.DataFrame:
    """
    Generate synthetic OHLCV data that exhibits clear Wyckoff phases:
    1. Markdown (downtrend with lower lows / lower highs)
    2. Accumulation (range with spring / sell-side sweep)
    3. Markup (uptrend with higher highs / higher lows + pullbacks)
    4. Distribution (range with UTAD / buy-side sweep)
    5. Markdown again

    The data is designed to produce clear swing points for structure detection.
    """
    rng = np.random.RandomState(seed)
    dates = pd.date_range(start='2024-06-01', periods=n_bars, freq='h')

    prices = []
    base_price = 250.0
    base_vol = 1_000_000
    phase_bars = n_bars // 5

    for phase in range(5):
        for i in range(phase_bars):
            progress = i / phase_bars

            if phase == 0:
                # MARKDOWN: staircase down from 250 to 200 with clear swing structure
                # Create lower highs and lower lows
                cycle = np.sin(progress * 5 * np.pi)  # 2.5 cycles = 5 swings
                trend = -50 * progress + 6 * cycle
                vol_mult = 1.0 + 0.4 * abs(cycle)

            elif phase == 1:
                # ACCUMULATION: range 195-210 with spring at ~40%
                cycle = np.sin(progress * 6 * np.pi)  # 3 cycles
                if 0.35 < progress < 0.45:
                    # Spring: sharp dip below range then V-recovery
                    spring_prog = (progress - 0.35) / 0.10
                    trend = -50 - 10 * np.sin(spring_prog * np.pi)
                    vol_mult = 2.5
                elif progress > 0.75:
                    # SOS: break above range
                    trend = -50 + 18 * (progress - 0.75) / 0.25
                    vol_mult = 2.0
                else:
                    trend = -50 + 6 * cycle
                    vol_mult = 0.6 + 0.4 * abs(cycle)

            elif phase == 2:
                # MARKUP: staircase up from 200 to 310 with clear pullbacks
                cycle = np.sin(progress * 6 * np.pi)  # 3 cycles
                trend = -50 + 110 * progress + 5 * cycle * (1 - 0.3 * progress)
                vol_mult = 1.3 + 0.5 * max(0, cycle)  # volume on rallies

            elif phase == 3:
                # DISTRIBUTION: range 300-315 with UTAD at ~35%
                cycle = np.sin(progress * 6 * np.pi)
                if 0.30 < progress < 0.42:
                    # UTAD: spike above range then rejection
                    utad_prog = (progress - 0.30) / 0.12
                    trend = 60 + 14 * np.sin(utad_prog * np.pi)
                    vol_mult = 2.2
                elif progress > 0.70:
                    # SOW: break below range
                    trend = 60 - 22 * (progress - 0.70) / 0.30
                    vol_mult = 1.8
                else:
                    trend = 60 + 7 * cycle
                    vol_mult = 0.7 + 0.3 * abs(cycle)

            else:
                # MARKDOWN: staircase down from 290 to 220
                cycle = np.sin(progress * 5 * np.pi)
                trend = 40 - 70 * progress + 5 * cycle
                vol_mult = 1.0 + 0.5 * progress

            noise = rng.normal(0, 0.4)
            price = base_price + trend + noise
            vol = int(base_vol * vol_mult * (0.7 + 0.6 * rng.random()))

            # Generate OHLC with clear wicks
            body = rng.normal(0, 0.8)
            wick_up = abs(rng.normal(0, 0.6))
            wick_down = abs(rng.normal(0, 0.6))

            close = price
            open_ = close - body
            high = max(open_, close) + wick_up
            low = min(open_, close) - wick_down

            bar_idx = phase * phase_bars + i
            prices.append({
                'date': dates[bar_idx] if bar_idx < len(dates) else dates[-1],
                'open': round(open_, 2),
                'high': round(high, 2),
                'low': round(low, 2),
                'close': round(close, 2),
                'volume': vol,
            })

    df = pd.DataFrame(prices)
    return df


def load_csv_data(csv_path: str) -> pd.DataFrame:
    """Load OHLCV data from a CSV file."""
    df = pd.read_csv(csv_path)
    # Normalize column names
    col_map = {}
    for col in df.columns:
        cl = col.lower().strip()
        if cl in ('date', 'datetime', 'timestamp', 'time'):
            col_map[col] = 'date'
        elif cl in ('open', 'o'):
            col_map[col] = 'open'
        elif cl in ('high', 'h'):
            col_map[col] = 'high'
        elif cl in ('low', 'l'):
            col_map[col] = 'low'
        elif cl in ('close', 'c'):
            col_map[col] = 'close'
        elif cl in ('adj_close', 'adj close'):
            # Only use adj_close if no 'close' column exists
            if 'close' not in [c.lower() for c in df.columns]:
                col_map[col] = 'close'
        elif cl in ('volume', 'vol', 'v'):
            col_map[col] = 'volume'
    df = df.rename(columns=col_map)
    return df


def try_download_yfinance(ticker: str, interval: str, days: int) -> pd.DataFrame:
    """Try to download data via yfinance. Returns empty DataFrame on failure."""
    try:
        import yfinance as yf
        period_map = {
            '5m': min(days, 59),     # yfinance limit
            '15m': min(days, 59),
            '1h': min(days, 729),
            '1d': min(days, 3650),
        }
        max_days = period_map.get(interval, days)
        t = yf.Ticker(ticker)
        h = t.history(period=f'{max_days}d', interval=interval)
        if h.empty:
            return pd.DataFrame()
        h = h.reset_index()
        # Rename columns
        rename = {}
        for col in h.columns:
            cl = col.lower()
            if 'date' in cl or 'time' in cl:
                rename[col] = 'date'
            elif cl == 'open':
                rename[col] = 'open'
            elif cl == 'high':
                rename[col] = 'high'
            elif cl == 'low':
                rename[col] = 'low'
            elif cl == 'close':
                rename[col] = 'close'
            elif cl == 'volume':
                rename[col] = 'volume'
        h = h.rename(columns=rename)
        return h[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
    except Exception as e:
        print(f"[WARN] yfinance download failed: {e}")
        return pd.DataFrame()


# =============================================================================
# CHART DRAWING
# =============================================================================

def draw_candlesticks(ax, data: pd.DataFrame):
    """Draw candlestick chart on matplotlib axes."""
    for i in range(len(data)):
        row = data.iloc[i]
        o, h, l, c = row['open'], row['high'], row['low'], row['close']

        color = '#26a69a' if c >= o else '#ef5350'  # green / red
        body_color = color

        # Wick
        ax.plot([i, i], [l, h], color='#555555', linewidth=0.6, zorder=1)

        # Body
        body_bottom = min(o, c)
        body_height = abs(c - o)
        if body_height < 0.01:
            body_height = 0.01
        rect = Rectangle(
            (i - 0.35, body_bottom), 0.7, body_height,
            facecolor=body_color, edgecolor=body_color, linewidth=0.5, zorder=2,
        )
        ax.add_patch(rect)


def draw_volume(ax, data: pd.DataFrame):
    """Draw volume bars on a subplot."""
    for i in range(len(data)):
        row = data.iloc[i]
        color = '#26a69a' if row['close'] >= row['open'] else '#ef5350'
        ax.bar(i, row['volume'], width=0.7, color=color, alpha=0.5)


def annotate_chart(
    ax_price,
    ax_vol,
    data: pd.DataFrame,
    analysis: StructureAnalysis,
    wyckoff: WyckoffAnalysis,
):
    """Add all Wyckoff market structure annotations to the chart."""
    n = len(data)

    # 1. Swing highs (red ▼)
    for idx, price in analysis.swing_highs:
        if 0 <= idx < n:
            ax_price.annotate(
                '▼', xy=(idx, price), fontsize=7, color='#e53935',
                ha='center', va='bottom',
                xytext=(0, 4), textcoords='offset points',
            )

    # 2. Swing lows (green ▲)
    for idx, price in analysis.swing_lows:
        if 0 <= idx < n:
            ax_price.annotate(
                '▲', xy=(idx, price), fontsize=7, color='#43a047',
                ha='center', va='top',
                xytext=(0, -4), textcoords='offset points',
            )

    # 3. Equal highs (dotted orange lines)
    for eq in analysis.equal_highs:
        ax_price.axhline(
            y=eq.price, color='#ff9800', linestyle=':', linewidth=1.0, alpha=0.7,
        )
        ax_price.text(
            n - 1, eq.price, f' EQH({eq.strength})',
            fontsize=7, color='#ff9800', va='bottom',
        )

    # 4. Equal lows (dotted blue lines)
    for eq in analysis.equal_lows:
        ax_price.axhline(
            y=eq.price, color='#1e88e5', linestyle=':', linewidth=1.0, alpha=0.7,
        )
        ax_price.text(
            n - 1, eq.price, f' EQL({eq.strength})',
            fontsize=7, color='#1e88e5', va='top',
        )

    # 5. BOS events
    for brk in analysis.structure_breaks:
        if brk.break_type == StructureBreakType.BOS and 0 <= brk.index < n:
            color = '#2196f3' if brk.direction == StructureDirection.BULLISH else '#f44336'
            arrow = '\u2191' if brk.direction == StructureDirection.BULLISH else '\u2193'
            label = f'BOS{arrow}'
            ax_price.axvline(x=brk.index, color=color, linestyle='--', linewidth=0.8, alpha=0.5)
            y_pos = brk.price
            ax_price.annotate(
                label, xy=(brk.index, y_pos),
                fontsize=7, fontweight='bold', color=color,
                ha='center', va='bottom' if brk.direction == StructureDirection.BULLISH else 'top',
                xytext=(0, 8 if brk.direction == StructureDirection.BULLISH else -8),
                textcoords='offset points',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor=color, alpha=0.8),
            )

    # 6. CHOCH events
    for brk in analysis.structure_breaks:
        if brk.break_type == StructureBreakType.CHOCH and 0 <= brk.index < n:
            color = '#9c27b0' if brk.direction == StructureDirection.BULLISH else '#ff5722'
            arrow = '\u2191' if brk.direction == StructureDirection.BULLISH else '\u2193'
            label = f'CHOCH{arrow}'
            ax_price.axvline(x=brk.index, color=color, linestyle='-.', linewidth=1.0, alpha=0.6)
            y_pos = brk.price
            ax_price.annotate(
                label, xy=(brk.index, y_pos),
                fontsize=8, fontweight='bold', color=color,
                ha='center', va='bottom' if brk.direction == StructureDirection.BULLISH else 'top',
                xytext=(0, 12 if brk.direction == StructureDirection.BULLISH else -12),
                textcoords='offset points',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#fff3e0', edgecolor=color, alpha=0.9),
            )

    # 7. Liquidity sweeps (diamond markers)
    for sweep in analysis.liquidity_map.recent_sweeps:
        if 0 <= sweep.index < n:
            if sweep.direction == 'sell_side':
                color = '#4caf50'
                marker_y = sweep.sweep_price
                va = 'top'
                label = 'SWEEP\u2193'
            else:
                color = '#f44336'
                marker_y = sweep.sweep_price
                va = 'bottom'
                label = 'SWEEP\u2191'

            ax_price.plot(
                sweep.index, marker_y, marker='D', color=color,
                markersize=8, zorder=5, markeredgecolor='black', markeredgewidth=0.5,
            )
            ax_price.annotate(
                label, xy=(sweep.index, marker_y),
                fontsize=6, color=color, fontweight='bold',
                ha='center', va=va,
                xytext=(0, -10 if va == 'top' else 10),
                textcoords='offset points',
            )

    # 8. Demand zones (green rectangles)
    for zone in analysis.demand_zones:
        width = n - zone.origin_index
        rect = Rectangle(
            (zone.origin_index, zone.low), width, zone.high - zone.low,
            facecolor='#4caf50', alpha=0.12, edgecolor='#4caf50',
            linewidth=0.8, linestyle='--', zorder=0,
        )
        ax_price.add_patch(rect)
        ax_price.text(
            zone.origin_index + 1, zone.low, 'DEMAND',
            fontsize=6, color='#2e7d32', fontweight='bold', va='top',
        )

    # 9. Supply zones (red rectangles)
    for zone in analysis.supply_zones:
        width = n - zone.origin_index
        rect = Rectangle(
            (zone.origin_index, zone.low), width, zone.high - zone.low,
            facecolor='#f44336', alpha=0.12, edgecolor='#f44336',
            linewidth=0.8, linestyle='--', zorder=0,
        )
        ax_price.add_patch(rect)
        ax_price.text(
            zone.origin_index + 1, zone.high, 'SUPPLY',
            fontsize=6, color='#c62828', fontweight='bold', va='bottom',
        )

    # 10. Broken structure levels (gray dashed)
    for brk in analysis.structure_breaks:
        if 0 <= brk.index < n:
            ax_price.axhline(
                y=brk.broken_level, color='gray', linestyle='--',
                linewidth=0.5, alpha=0.3,
            )


def build_chart(
    data: pd.DataFrame,
    analysis: StructureAnalysis,
    wyckoff: WyckoffAnalysis,
    title: str = "Wyckoff Market Structure",
    output_path: str = None,
):
    """Build the complete annotated chart."""
    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, figsize=(24, 14),
        gridspec_kw={'height_ratios': [4, 1]},
        sharex=True,
    )

    fig.patch.set_facecolor('#fafafa')
    ax_price.set_facecolor('#fafafa')
    ax_vol.set_facecolor('#fafafa')

    # Draw candlesticks
    draw_candlesticks(ax_price, data)

    # Draw volume
    draw_volume(ax_vol, data)

    # Add annotations
    annotate_chart(ax_price, ax_vol, data, analysis, wyckoff)

    # Title with Wyckoff info
    phase_name = wyckoff.phase.phase.value.upper()
    context_name = wyckoff.context.context.value.replace('_', ' ').upper()
    conf = wyckoff.context.confidence
    struct_dir = analysis.direction.value.upper()

    full_title = (
        f"{title}\n"
        f"Phase: {phase_name} | Context: {context_name} (conf: {conf:.2f}) | "
        f"Structure: {struct_dir} | Score: {wyckoff.wyckoff_score:.2f}"
    )
    ax_price.set_title(full_title, fontsize=13, fontweight='bold', pad=15)

    # Setup details text box
    setup = wyckoff.setup
    if setup.is_valid:
        dir_str = "LONG" if setup.direction == 1 else "SHORT"
        setup_text = (
            f"SETUP: {dir_str} (conf: {setup.confidence:.2f})\n"
            f"Entry: {setup.entry_price:.2f} | Stop: {setup.stop_price:.2f}\n"
            f"TP1: {setup.tp1:.2f} | TP2: {setup.tp2:.2f} | TP3: {setup.tp3:.2f}\n"
            f"R:R = {setup.risk_reward:.1f}\n"
            f"{setup.reasoning}"
        )
        ax_price.text(
            0.02, 0.98, setup_text,
            transform=ax_price.transAxes, fontsize=8,
            verticalalignment='top', fontfamily='monospace',
            bbox=dict(boxstyle='round', facecolor='lightyellow', edgecolor='orange', alpha=0.9),
        )

    # X-axis labels (show every Nth date)
    n = len(data)
    step = max(1, n // 20)
    xtick_positions = list(range(0, n, step))
    if 'date' in data.columns:
        xtick_labels = [str(data.iloc[i]['date'])[:16] for i in xtick_positions]
    else:
        xtick_labels = [str(i) for i in xtick_positions]
    ax_vol.set_xticks(xtick_positions)
    ax_vol.set_xticklabels(xtick_labels, rotation=45, ha='right', fontsize=7)

    ax_price.set_ylabel('Price', fontsize=10)
    ax_vol.set_ylabel('Volume', fontsize=10)
    ax_price.grid(True, alpha=0.15)
    ax_vol.grid(True, alpha=0.15)

    # Legend
    legend_elements = [
        Line2D([0], [0], marker='v', color='w', markerfacecolor='#e53935', markersize=8, label='Swing High'),
        Line2D([0], [0], marker='^', color='w', markerfacecolor='#43a047', markersize=8, label='Swing Low'),
        Line2D([0], [0], color='#ff9800', linestyle=':', linewidth=1.5, label='Equal Highs'),
        Line2D([0], [0], color='#1e88e5', linestyle=':', linewidth=1.5, label='Equal Lows'),
        Line2D([0], [0], color='#2196f3', linestyle='--', linewidth=1, label='BOS (Bullish)'),
        Line2D([0], [0], color='#f44336', linestyle='--', linewidth=1, label='BOS (Bearish)'),
        Line2D([0], [0], color='#9c27b0', linestyle='-.', linewidth=1.5, label='CHOCH (Bullish)'),
        Line2D([0], [0], color='#ff5722', linestyle='-.', linewidth=1.5, label='CHOCH (Bearish)'),
        Line2D([0], [0], marker='D', color='w', markerfacecolor='#4caf50', markersize=8, label='Sweep (Sell-side)'),
        Line2D([0], [0], marker='D', color='w', markerfacecolor='#f44336', markersize=8, label='Sweep (Buy-side)'),
        mpatches.Patch(facecolor='#4caf50', alpha=0.2, edgecolor='#4caf50', label='Demand Zone'),
        mpatches.Patch(facecolor='#f44336', alpha=0.2, edgecolor='#f44336', label='Supply Zone'),
    ]
    ax_price.legend(
        handles=legend_elements, loc='upper right',
        fontsize=7, framealpha=0.9, ncol=2,
    )

    plt.tight_layout()

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Chart saved to: {output_path}")
    else:
        output_path = 'wyckoff_chart.png'
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Chart saved to: {output_path}")

    plt.close()
    return output_path


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Wyckoff Market Structure Visualization')
    parser.add_argument('--csv', type=str, help='Path to CSV file with OHLCV data')
    parser.add_argument('--ticker', type=str, help='Ticker symbol for yfinance download')
    parser.add_argument('--interval', type=str, default='1h',
                        help='Interval for yfinance (5m, 15m, 1h, 1d)')
    parser.add_argument('--days', type=int, default=30,
                        help='Number of days of data to download')
    parser.add_argument('--synthetic', action='store_true',
                        help='Generate synthetic Wyckoff pattern data')
    parser.add_argument('--last', type=int, default=0,
                        help='Use only last N bars')
    parser.add_argument('--output', type=str, default=None,
                        help='Output file path (default: wyckoff_chart.png)')
    parser.add_argument('--swing-lookback', type=int, default=5,
                        help='Swing point lookback period')
    parser.add_argument('--title', type=str, default=None,
                        help='Chart title')

    args = parser.parse_args()

    # Load data
    data = pd.DataFrame()

    if args.csv:
        print(f"Loading data from {args.csv}...")
        data = load_csv_data(args.csv)
        title = args.title or f"Wyckoff Analysis — {os.path.basename(args.csv)}"

    elif args.ticker:
        print(f"Downloading {args.ticker} {args.interval} data ({args.days} days)...")
        data = try_download_yfinance(args.ticker, args.interval, args.days)
        title = args.title or f"Wyckoff Analysis — {args.ticker} {args.interval}"

    elif args.synthetic:
        print("Generating synthetic Wyckoff data...")
        data = generate_wyckoff_data(n_bars=300)
        title = args.title or "Wyckoff Analysis — Synthetic Data (Accumulation/Distribution)"

    else:
        # Default: try SPY CSV, fall back to synthetic
        spy_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'daily', 'SPY.csv')
        if os.path.exists(spy_path):
            print(f"Loading SPY daily data from {spy_path}...")
            data = load_csv_data(spy_path)
            title = args.title or "Wyckoff Analysis — SPY Daily"
        else:
            print("No data source specified. Generating synthetic data...")
            data = generate_wyckoff_data(n_bars=300)
            title = args.title or "Wyckoff Analysis — Synthetic Data"

    if data.empty:
        print("ERROR: No data loaded. Use --csv, --ticker, or --synthetic.")
        sys.exit(1)

    # Trim to last N bars if specified
    if args.last > 0:
        data = data.tail(args.last)

    data = data.reset_index(drop=True)
    print(f"Loaded {len(data)} bars")

    # Run analysis
    print("Running market structure analysis...")
    trend = TrendIndicators(swing_lookback=args.swing_lookback)
    structure_analyzer = MarketStructureAnalyzer(
        trend_indicators=trend,
        swing_lookback=args.swing_lookback,
    )
    wyckoff_model = WyckoffModel(
        trend_indicators=trend,
        structure_analyzer=structure_analyzer,
    )

    analysis = structure_analyzer.analyze(data)
    wyckoff = wyckoff_model.analyze(data)

    # Print summary
    print(f"\n=== ANALYSIS SUMMARY ===")
    print(f"Structure direction: {analysis.direction.value}")
    print(f"Swing highs: {len(analysis.swing_highs)}")
    print(f"Swing lows: {len(analysis.swing_lows)}")
    print(f"Equal highs: {len(analysis.equal_highs)}")
    print(f"Equal lows: {len(analysis.equal_lows)}")
    print(f"Structure breaks: {len(analysis.structure_breaks)}")
    for brk in analysis.structure_breaks:
        print(f"  [{brk.index}] {brk.break_type.value.upper()} {brk.direction.value} @ {brk.price:.2f} (broke {brk.broken_level:.2f})")
    print(f"Liquidity sweeps: {len(analysis.liquidity_map.recent_sweeps)}")
    for sw in analysis.liquidity_map.recent_sweeps:
        print(f"  [{sw.index}] {sw.direction} sweep @ {sw.sweep_price:.2f}, reclaimed={sw.is_reclaimed}")
    print(f"Demand zones: {len(analysis.demand_zones)}")
    print(f"Supply zones: {len(analysis.supply_zones)}")
    print(f"\nContext: {wyckoff.context.context.value} (conf: {wyckoff.context.confidence:.2f})")
    print(f"Phase: {wyckoff.phase.phase.value} (score: {wyckoff.phase.dominant_score:.2f})")
    print(f"  acc={wyckoff.phase.accumulation_score:.2f} dist={wyckoff.phase.distribution_score:.2f} "
          f"markup={wyckoff.phase.markup_score:.2f} markdown={wyckoff.phase.markdown_score:.2f}")
    print(f"Setup valid: {wyckoff.setup.is_valid}")
    if wyckoff.setup.is_valid:
        dir_str = "LONG" if wyckoff.setup.direction == 1 else "SHORT"
        print(f"  Direction: {dir_str}, Confidence: {wyckoff.setup.confidence:.2f}")
        print(f"  Entry: {wyckoff.setup.entry_price:.2f}, Stop: {wyckoff.setup.stop_price:.2f}")
        print(f"  TP1: {wyckoff.setup.tp1:.2f}, TP2: {wyckoff.setup.tp2:.2f}, TP3: {wyckoff.setup.tp3:.2f}")
        print(f"  R:R: {wyckoff.setup.risk_reward:.1f}")
    print(f"\nWyckoff Score: {wyckoff.wyckoff_score:.2f}")

    # Draw chart
    print("\nGenerating chart...")
    output = args.output or 'wyckoff_chart.png'
    build_chart(data, analysis, wyckoff, title=title, output_path=output)

    print("Done!")


if __name__ == '__main__':
    main()
