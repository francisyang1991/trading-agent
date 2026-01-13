#!/usr/bin/env python3
"""
Stock Chart Generator
=====================
Generates price charts with EMA overlays for visual analysis.
Uses index-based x-axis to avoid weekend/holiday gaps.

Usage:
    python tools/stock_chart.py AAPL
    python tools/stock_chart.py AAPL --save    # Save to file
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')


def calculate_ema(data: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return data.ewm(span=period, adjust=False).mean()


def plot_stock_chart(symbol: str, period: str = "6mo", save_to_file: bool = False):
    """
    Generate a stock chart with EMA overlays.
    Uses index-based x-axis to eliminate weekend/holiday gaps.
    
    Args:
        symbol: Stock symbol
        period: Time period (1mo, 3mo, 6mo, 1y, 2y)
        save_to_file: Whether to save the chart to a file
    """
    print(f"📊 Generating chart for {symbol}...")
    
    # Fetch data
    ticker = yf.Ticker(symbol)
    data = ticker.history(period=period)
    
    if data.empty:
        print(f"❌ No data found for {symbol}")
        return
    
    # Get stock info
    info = ticker.info
    stock_name = info.get('shortName', symbol)
    
    # Reset index to get sequential x-values (eliminates weekend gaps)
    data = data.reset_index()
    x = np.arange(len(data))
    
    # Calculate EMAs
    ema_periods = [9, 21, 50, 120, 200]
    ema_colors = ['#FFD700', '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4']
    
    for period_val in ema_periods:
        if len(data) >= period_val:
            data[f'EMA{period_val}'] = calculate_ema(data['Close'], period_val)
    
    # Create figure with subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), 
                                    gridspec_kw={'height_ratios': [3, 1]})
    fig.patch.set_facecolor('#1a1a2e')
    
    # Price chart - use index-based x-axis
    ax1.set_facecolor('#16213e')
    ax1.plot(x, data['Close'], color='#ffffff', linewidth=1.5, 
             label=f'{symbol} Price', alpha=0.9)
    
    # Plot EMAs
    for i, period_val in enumerate(ema_periods):
        if f'EMA{period_val}' in data.columns:
            ax1.plot(x, data[f'EMA{period_val}'], 
                    color=ema_colors[i], linewidth=1, 
                    label=f'EMA {period_val}', alpha=0.8)
    
    # Current price annotation
    current_price = data['Close'].iloc[-1]
    ax1.axhline(y=current_price, color='#ffffff', linestyle='--', alpha=0.5)
    ax1.annotate(f'${current_price:.2f}', 
                xy=(x[-1], current_price),
                xytext=(10, 0), textcoords='offset points',
                color='#ffffff', fontsize=10, fontweight='bold',
                va='center')
    
    # Title and labels
    ax1.set_title(f'{stock_name} ({symbol}) - Technical Analysis', 
                  color='#ffffff', fontsize=16, fontweight='bold', pad=20)
    ax1.set_ylabel('Price ($)', color='#ffffff', fontsize=12)
    ax1.tick_params(axis='both', colors='#ffffff')
    ax1.legend(loc='upper left', facecolor='#16213e', edgecolor='#ffffff',
               labelcolor='#ffffff', fontsize=9)
    ax1.grid(True, alpha=0.3, color='#ffffff')
    
    # Set x-axis labels at regular intervals (show dates)
    num_ticks = 8
    tick_positions = np.linspace(0, len(data) - 1, num_ticks, dtype=int)
    tick_labels = [data.iloc[i]['Date'].strftime('%Y-%m-%d') 
                   if 'Date' in data.columns else '' for i in tick_positions]
    
    ax1.set_xticks(tick_positions)
    ax1.set_xticklabels([])  # Hide labels on price chart
    ax1.set_xlim(-1, len(data))
    
    # Volume chart - use index-based x-axis
    ax2.set_facecolor('#16213e')
    colors = ['#4ECDC4' if data['Close'].iloc[i] >= data['Open'].iloc[i] 
              else '#FF6B6B' for i in range(len(data))]
    ax2.bar(x, data['Volume'], color=colors, alpha=0.7, width=0.8)
    
    ax2.set_ylabel('Volume', color='#ffffff', fontsize=12)
    ax2.set_xlabel('Date', color='#ffffff', fontsize=12)
    ax2.tick_params(axis='both', colors='#ffffff')
    ax2.grid(True, alpha=0.3, color='#ffffff')
    
    # Set same x-axis ticks for volume chart
    ax2.set_xticks(tick_positions)
    ax2.set_xticklabels(tick_labels, rotation=45, ha='right', color='#ffffff')
    ax2.set_xlim(-1, len(data))
    
    # Add EMA status text
    ema_status = []
    for period_val in ema_periods:
        if f'EMA{period_val}' in data.columns:
            ema_val = data[f'EMA{period_val}'].iloc[-1]
            position = "⬆️" if current_price > ema_val else "⬇️"
            distance = ((current_price - ema_val) / ema_val) * 100
            ema_status.append(f"EMA{period_val}: {position} {distance:+.1f}%")
    
    status_text = " | ".join(ema_status)
    fig.text(0.5, 0.02, status_text, ha='center', color='#ffffff', 
             fontsize=10, style='italic')
    
    plt.tight_layout()
    plt.subplots_adjust(bottom=0.1)
    
    if save_to_file:
        filename = f"results/{symbol}_chart_{datetime.now().strftime('%Y%m%d')}.png"
        os.makedirs('results', exist_ok=True)
        plt.savefig(filename, facecolor=fig.get_facecolor(), 
                    edgecolor='none', dpi=150, bbox_inches='tight')
        print(f"✅ Chart saved to {filename}")
    else:
        plt.show()
    
    plt.close()


def plot_multi_stock_comparison(symbols: list, period: str = "3mo", save_to_file: bool = False):
    """
    Generate a comparison chart for multiple stocks.
    Uses index-based x-axis to eliminate weekend/holiday gaps.
    
    Args:
        symbols: List of stock symbols
        period: Time period
        save_to_file: Whether to save the chart
    """
    print(f"📊 Generating comparison chart for {', '.join(symbols)}...")
    
    # Fetch data for all symbols
    stock_data = {}
    max_len = 0
    
    for symbol in symbols:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period=period)
        if not data.empty:
            data = data.reset_index()
            # Normalize to percentage change from start
            data['Normalized'] = (data['Close'] / data['Close'].iloc[0] - 1) * 100
            stock_data[symbol] = data
            max_len = max(max_len, len(data))
    
    if not stock_data:
        print("❌ No data found for any symbol")
        return
    
    # Create figure
    fig, ax = plt.subplots(figsize=(14, 8))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')
    
    # Color palette
    colors = ['#FFD700', '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', 
              '#DDA0DD', '#98D8C8', '#F7DC6F', '#BB8FCE', '#85C1E9']
    
    # Plot each stock using index-based x-axis
    reference_dates = None
    for i, (symbol, data) in enumerate(stock_data.items()):
        x = np.arange(len(data))
        ax.plot(x, data['Normalized'], 
                color=colors[i % len(colors)], linewidth=2, 
                label=f'{symbol}: {data["Normalized"].iloc[-1]:+.1f}%')
        if reference_dates is None or len(data) > len(reference_dates):
            reference_dates = data
    
    # Zero line
    ax.axhline(y=0, color='#ffffff', linestyle='--', alpha=0.5)
    
    # Title and labels
    ax.set_title('Stock Performance Comparison (Normalized %)', 
                 color='#ffffff', fontsize=16, fontweight='bold', pad=20)
    ax.set_ylabel('Return (%)', color='#ffffff', fontsize=12)
    ax.set_xlabel('Date', color='#ffffff', fontsize=12)
    ax.tick_params(axis='both', colors='#ffffff')
    ax.legend(loc='upper left', facecolor='#16213e', edgecolor='#ffffff',
              labelcolor='#ffffff', fontsize=10)
    ax.grid(True, alpha=0.3, color='#ffffff')
    
    # Set x-axis labels
    if reference_dates is not None:
        num_ticks = 6
        tick_positions = np.linspace(0, len(reference_dates) - 1, num_ticks, dtype=int)
        tick_labels = [reference_dates.iloc[i]['Date'].strftime('%Y-%m-%d') 
                       if 'Date' in reference_dates.columns else '' for i in tick_positions]
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(tick_labels, rotation=45, ha='right', color='#ffffff')
    
    plt.tight_layout()
    
    if save_to_file:
        filename = f"results/comparison_chart_{datetime.now().strftime('%Y%m%d')}.png"
        os.makedirs('results', exist_ok=True)
        plt.savefig(filename, facecolor=fig.get_facecolor(), 
                    edgecolor='none', dpi=150, bbox_inches='tight')
        print(f"✅ Chart saved to {filename}")
    else:
        plt.show()
    
    plt.close()


def main():
    """Main function."""
    print("\n📈 SAIYAN Stock Chart Generator")
    print("=" * 50)
    
    save_to_file = '--save' in sys.argv
    symbols = [s.upper() for s in sys.argv[1:] if not s.startswith('--')]
    
    if not symbols:
        symbols = ["AAPL"]
        print(f"No symbol provided. Using default: {symbols[0]}")
    
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Save to file: {save_to_file}")
    
    # Generate individual charts
    for symbol in symbols:
        plot_stock_chart(symbol, period="6mo", save_to_file=save_to_file)
    
    # Generate comparison chart if multiple symbols
    if len(symbols) > 1:
        plot_multi_stock_comparison(symbols, period="3mo", save_to_file=save_to_file)
    
    print("\n✅ Chart generation complete!")


if __name__ == "__main__":
    main()
