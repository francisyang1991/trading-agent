#!/usr/bin/env python3
"""
MY PORTFOLIO TRACKER
====================
Track your actual positions, average costs, P&L, and get exit/add recommendations.

Features:
1. Track positions with avg cost, shares, entry date
2. Real-time P&L calculation
3. Position management recommendations (add/hold/trim/exit)
4. Integration with scanner signals
5. Performance history tracking

Usage:
    # Add a position
    python tools/my_portfolio.py add XPEV 21.00 100 --date 2026-01-14
    
    # View portfolio
    python tools/my_portfolio.py show
    
    # Get recommendations for all positions
    python tools/my_portfolio.py analyze
    
    # Update position (add to existing)
    python tools/my_portfolio.py add XPEV 22.50 50  # Adds 50 shares at $22.50
    
    # Close position
    python tools/my_portfolio.py close XPEV 25.00  # Sold all at $25.00
    
    # Partial close
    python tools/my_portfolio.py close XPEV 25.00 --shares 50
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# DATA STORAGE
# ============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
PORTFOLIO_FILE = DATA_DIR / "my_portfolio.json"
TRADES_FILE = DATA_DIR / "trade_history.json"


def load_portfolio() -> Dict:
    """Load portfolio from JSON file."""
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE, 'r') as f:
            return json.load(f)
    return {"positions": {}, "cash": 0, "last_updated": None}


def save_portfolio(portfolio: Dict):
    """Save portfolio to JSON file."""
    portfolio["last_updated"] = datetime.now().isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO_FILE, 'w') as f:
        json.dump(portfolio, f, indent=2, default=str)


def load_trades() -> List[Dict]:
    """Load trade history."""
    if TRADES_FILE.exists():
        with open(TRADES_FILE, 'r') as f:
            return json.load(f)
    return []


def save_trade(trade: Dict):
    """Append trade to history."""
    trades = load_trades()
    trade["timestamp"] = datetime.now().isoformat()
    trades.append(trade)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRADES_FILE, 'w') as f:
        json.dump(trades, f, indent=2, default=str)


# ============================================================================
# POSITION CLASS
# ============================================================================

@dataclass
class Position:
    """A portfolio position."""
    symbol: str
    shares: int
    avg_cost: float
    first_entry: str  # ISO date
    last_entry: str   # ISO date
    total_cost: float
    notes: str = ""
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Position':
        return cls(**data)
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def add_shares(self, shares: int, price: float, date: str = None):
        """Add shares to position (updates avg cost)."""
        new_cost = shares * price
        self.total_cost += new_cost
        self.shares += shares
        self.avg_cost = self.total_cost / self.shares
        self.last_entry = date or datetime.now().strftime("%Y-%m-%d")
    
    def remove_shares(self, shares: int) -> int:
        """Remove shares from position. Returns shares actually removed."""
        removed = min(shares, self.shares)
        self.shares -= removed
        self.total_cost = self.shares * self.avg_cost
        return removed


# ============================================================================
# ANALYSIS FUNCTIONS
# ============================================================================

def get_current_price(symbol: str) -> Optional[float]:
    """Get current price for a symbol."""
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1d")
        if not data.empty:
            return data['Close'].iloc[-1]
    except:
        pass
    return None


def get_technical_data(symbol: str) -> Dict:
    """Get technical indicators for position analysis."""
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="6mo")
        
        if data.empty or len(data) < 50:
            return {}
        
        close = data['Close']
        high = data['High']
        low = data['Low']
        
        # EMAs
        ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        
        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = (100 - (100 / (1 + rs))).iloc[-1]
        
        # ATR
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=14).mean().iloc[-1]
        
        # Support/Resistance
        recent_low = low.tail(20).min()
        recent_high = high.tail(20).max()
        
        # Momentum
        momentum_1w = (close.iloc[-1] / close.iloc[-5] - 1) * 100 if len(close) >= 5 else 0
        momentum_1m = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) >= 21 else 0
        
        current_price = close.iloc[-1]
        
        return {
            "price": current_price,
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "rsi": rsi,
            "atr": atr,
            "atr_pct": atr / current_price * 100,
            "support": recent_low,
            "resistance": recent_high,
            "dist_ema21": (current_price / ema21 - 1) * 100,
            "dist_ema50": (current_price / ema50 - 1) * 100,
            "momentum_1w": momentum_1w,
            "momentum_1m": momentum_1m,
            "above_ema9": current_price > ema9,
            "above_ema21": current_price > ema21,
            "above_ema50": current_price > ema50,
        }
    except Exception as e:
        print(f"   ⚠️ Error getting technical data for {symbol}: {e}")
        return {}


def analyze_position(pos: Position, tech: Dict) -> Dict:
    """Analyze a position and generate recommendations."""
    if not tech:
        return {"action": "HOLD", "reasoning": "Insufficient data", "alerts": []}
    
    price = tech["price"]
    pnl_pct = (price / pos.avg_cost - 1) * 100
    pnl_value = (price - pos.avg_cost) * pos.shares
    
    alerts = []
    action = "HOLD"
    reasoning = []
    
    # Stop loss check (2 ATR below entry)
    stop_loss = pos.avg_cost - 2 * tech["atr"]
    target_1 = pos.avg_cost + 1.5 * tech["atr"]
    target_2 = pos.avg_cost + 3 * tech["atr"]
    
    # SELL SIGNALS
    if price < stop_loss:
        action = "EXIT"
        alerts.append("⚠️ BELOW STOP LOSS")
        reasoning.append(f"Price ${price:.2f} below stop ${stop_loss:.2f}")
    
    elif pnl_pct < -15:
        action = "REVIEW"
        alerts.append("⚠️ LARGE LOSS")
        reasoning.append(f"Down {pnl_pct:.1f}% - review thesis")
    
    elif tech["rsi"] > 75 and pnl_pct > 10:
        action = "TRIM"
        alerts.append("📈 RSI overbought + profit")
        reasoning.append(f"RSI {tech['rsi']:.0f} overbought, consider taking profits")
    
    elif tech["dist_ema21"] > 15 and pnl_pct > 15:
        action = "TRIM"
        alerts.append("📈 Extended above EMA21")
        reasoning.append(f"{tech['dist_ema21']:+.1f}% above EMA21, consider trimming")
    
    # ADD SIGNALS
    elif tech["rsi"] < 35 and pnl_pct > -10 and tech["above_ema50"]:
        action = "ADD"
        alerts.append("📉 RSI oversold + trend intact")
        reasoning.append(f"RSI {tech['rsi']:.0f} oversold, above EMA50 - add opportunity")
    
    elif price < tech["ema21"] and price > tech["ema50"] and pnl_pct > -5:
        action = "ADD"
        alerts.append("📉 Pullback to EMA21")
        reasoning.append(f"Healthy pullback to EMA21 ${tech['ema21']:.2f}")
    
    # HOLD
    else:
        if tech["above_ema21"] and tech["above_ema50"]:
            reasoning.append("Trend intact - above key EMAs")
        elif tech["above_ema50"]:
            reasoning.append("Above EMA50 - long-term trend OK")
        else:
            reasoning.append("Below key EMAs - monitor closely")
            alerts.append("⚠️ Below EMA50")
    
    return {
        "action": action,
        "reasoning": " | ".join(reasoning) if reasoning else "No specific signals",
        "alerts": alerts,
        "pnl_pct": pnl_pct,
        "pnl_value": pnl_value,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "current_price": price,
    }


# ============================================================================
# PORTFOLIO COMMANDS
# ============================================================================

def add_position(symbol: str, price: float, shares: int, date: str = None, notes: str = ""):
    """Add or update a position."""
    portfolio = load_portfolio()
    symbol = symbol.upper()
    date = date or datetime.now().strftime("%Y-%m-%d")
    
    if symbol in portfolio["positions"]:
        # Update existing position
        pos = Position.from_dict(portfolio["positions"][symbol])
        old_shares = pos.shares
        old_avg = pos.avg_cost
        pos.add_shares(shares, price, date)
        print(f"\n✅ Updated {symbol} position:")
        print(f"   Added: {shares} shares @ ${price:.2f}")
        print(f"   Previous: {old_shares} shares @ ${old_avg:.2f}")
        print(f"   New: {pos.shares} shares @ ${pos.avg_cost:.2f}")
    else:
        # Create new position
        pos = Position(
            symbol=symbol,
            shares=shares,
            avg_cost=price,
            first_entry=date,
            last_entry=date,
            total_cost=shares * price,
            notes=notes
        )
        print(f"\n✅ Added new position: {symbol}")
        print(f"   {shares} shares @ ${price:.2f} = ${shares * price:,.2f}")
    
    portfolio["positions"][symbol] = pos.to_dict()
    save_portfolio(portfolio)
    
    # Log trade
    save_trade({
        "type": "BUY",
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "date": date,
        "notes": notes
    })


def close_position(symbol: str, price: float, shares: int = None):
    """Close or partially close a position."""
    portfolio = load_portfolio()
    symbol = symbol.upper()
    
    if symbol not in portfolio["positions"]:
        print(f"❌ No position found for {symbol}")
        return
    
    pos = Position.from_dict(portfolio["positions"][symbol])
    shares_to_close = shares or pos.shares
    shares_closed = pos.remove_shares(shares_to_close)
    
    # Calculate P&L
    pnl = (price - pos.avg_cost) * shares_closed
    pnl_pct = (price / pos.avg_cost - 1) * 100
    
    print(f"\n{'✅' if pnl >= 0 else '❌'} Closed {shares_closed} shares of {symbol}")
    print(f"   Sold @ ${price:.2f} (Avg cost: ${pos.avg_cost:.2f})")
    print(f"   P&L: ${pnl:+,.2f} ({pnl_pct:+.1f}%)")
    
    if pos.shares <= 0:
        # Full close
        del portfolio["positions"][symbol]
        print(f"   Position fully closed")
    else:
        # Partial close
        portfolio["positions"][symbol] = pos.to_dict()
        print(f"   Remaining: {pos.shares} shares")
    
    save_portfolio(portfolio)
    
    # Log trade
    save_trade({
        "type": "SELL",
        "symbol": symbol,
        "shares": shares_closed,
        "price": price,
        "avg_cost": pos.avg_cost,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "date": datetime.now().strftime("%Y-%m-%d"),
    })


def show_portfolio():
    """Display current portfolio with P&L."""
    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    
    if not positions:
        print("\n📊 Portfolio is empty")
        return
    
    print(f"\n{'='*100}")
    print(f"  MY PORTFOLIO - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*100}")
    
    print(f"\n{'Symbol':<8} {'Shares':>8} {'Avg Cost':>10} {'Current':>10} {'P&L':>12} {'P&L %':>8} {'Value':>12}")
    print("-" * 100)
    
    total_cost = 0
    total_value = 0
    total_pnl = 0
    
    rows = []
    for symbol, data in positions.items():
        pos = Position.from_dict(data)
        current_price = get_current_price(symbol)
        
        if current_price:
            value = current_price * pos.shares
            pnl = (current_price - pos.avg_cost) * pos.shares
            pnl_pct = (current_price / pos.avg_cost - 1) * 100
        else:
            value = pos.total_cost
            pnl = 0
            pnl_pct = 0
            current_price = pos.avg_cost
        
        total_cost += pos.total_cost
        total_value += value
        total_pnl += pnl
        
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        rows.append((symbol, pos.shares, pos.avg_cost, current_price, pnl, pnl_pct, value, pnl_icon))
    
    # Sort by value
    rows.sort(key=lambda x: x[6], reverse=True)
    
    for symbol, shares, avg_cost, current, pnl, pnl_pct, value, icon in rows:
        print(f"{symbol:<8} {shares:>8} ${avg_cost:>9.2f} ${current:>9.2f} "
              f"{icon} ${pnl:>+10,.2f} {pnl_pct:>+7.1f}% ${value:>11,.2f}")
    
    print("-" * 100)
    total_pnl_pct = (total_value / total_cost - 1) * 100 if total_cost > 0 else 0
    total_icon = "🟢" if total_pnl >= 0 else "🔴"
    print(f"{'TOTAL':<8} {'':<8} ${total_cost:>9,.0f} ${total_value:>9,.0f} "
          f"{total_icon} ${total_pnl:>+10,.2f} {total_pnl_pct:>+7.1f}% ${total_value:>11,.2f}")
    
    print(f"\n  Last updated: {portfolio.get('last_updated', 'N/A')}")


def analyze_portfolio():
    """Analyze all positions and provide recommendations."""
    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    
    if not positions:
        print("\n📊 Portfolio is empty")
        return
    
    print(f"\n{'='*100}")
    print(f"  PORTFOLIO ANALYSIS - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*100}")
    
    for symbol, data in positions.items():
        pos = Position.from_dict(data)
        print(f"\n{'─'*60}")
        print(f"  {symbol}")
        print(f"{'─'*60}")
        print(f"  Position: {pos.shares} shares @ ${pos.avg_cost:.2f} = ${pos.total_cost:,.2f}")
        print(f"  Entry: {pos.first_entry} | Last add: {pos.last_entry}")
        
        # Get technical data
        tech = get_technical_data(symbol)
        if not tech:
            print(f"  ⚠️ Could not fetch technical data")
            continue
        
        # Analyze
        analysis = analyze_position(pos, tech)
        
        current = analysis["current_price"]
        pnl = analysis["pnl_value"]
        pnl_pct = analysis["pnl_pct"]
        
        print(f"  Current: ${current:.2f} | P&L: ${pnl:+,.2f} ({pnl_pct:+.1f}%)")
        print(f"  RSI: {tech['rsi']:.0f} | dEMA21: {tech['dist_ema21']:+.1f}%")
        
        # Alerts
        if analysis["alerts"]:
            print(f"\n  ALERTS:")
            for alert in analysis["alerts"]:
                print(f"    {alert}")
        
        # Action
        action_colors = {
            "EXIT": "🔴",
            "TRIM": "🟡",
            "REVIEW": "🟠",
            "HOLD": "⚪",
            "ADD": "🟢"
        }
        action_icon = action_colors.get(analysis["action"], "⚪")
        print(f"\n  ACTION: {action_icon} {analysis['action']}")
        print(f"  {analysis['reasoning']}")
        
        # Key levels
        print(f"\n  KEY LEVELS:")
        print(f"    Stop Loss: ${analysis['stop_loss']:.2f} ({(analysis['stop_loss']/pos.avg_cost-1)*100:+.1f}%)")
        print(f"    Target 1:  ${analysis['target_1']:.2f} ({(analysis['target_1']/pos.avg_cost-1)*100:+.1f}%)")
        print(f"    Target 2:  ${analysis['target_2']:.2f} ({(analysis['target_2']/pos.avg_cost-1)*100:+.1f}%)")
        print(f"    Support:   ${tech['support']:.2f}")
        print(f"    Resistance: ${tech['resistance']:.2f}")
    
    # Summary
    print(f"\n{'='*100}")
    print(f"  SUMMARY")
    print(f"{'='*100}")
    
    actions = {}
    for symbol, data in positions.items():
        pos = Position.from_dict(data)
        tech = get_technical_data(symbol)
        if tech:
            analysis = analyze_position(pos, tech)
            action = analysis["action"]
            if action not in actions:
                actions[action] = []
            actions[action].append(symbol)
    
    for action in ["EXIT", "TRIM", "REVIEW", "ADD", "HOLD"]:
        if action in actions:
            print(f"  {action}: {', '.join(actions[action])}")


def show_trade_history(limit: int = 20):
    """Show recent trade history."""
    trades = load_trades()
    
    if not trades:
        print("\n📊 No trade history")
        return
    
    print(f"\n{'='*90}")
    print(f"  TRADE HISTORY (Last {limit})")
    print(f"{'='*90}")
    
    print(f"\n{'Date':<12} {'Type':<6} {'Symbol':<8} {'Shares':>8} {'Price':>10} {'P&L':>12}")
    print("-" * 70)
    
    for trade in trades[-limit:]:
        pnl_str = ""
        if trade["type"] == "SELL" and "pnl" in trade:
            pnl = trade["pnl"]
            pnl_str = f"${pnl:+,.2f}"
        
        print(f"{trade.get('date', 'N/A'):<12} {trade['type']:<6} {trade['symbol']:<8} "
              f"{trade['shares']:>8} ${trade['price']:>9.2f} {pnl_str:>12}")


def show_performance():
    """Show overall trading performance."""
    trades = load_trades()
    
    sells = [t for t in trades if t["type"] == "SELL" and "pnl" in t]
    
    if not sells:
        print("\n📊 No closed trades to analyze")
        return
    
    print(f"\n{'='*70}")
    print(f"  TRADING PERFORMANCE")
    print(f"{'='*70}")
    
    wins = [t for t in sells if t["pnl"] > 0]
    losses = [t for t in sells if t["pnl"] <= 0]
    
    total_pnl = sum(t["pnl"] for t in sells)
    win_rate = len(wins) / len(sells) * 100 if sells else 0
    
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    
    print(f"\n  Total Trades: {len(sells)}")
    print(f"  Wins: {len(wins)} | Losses: {len(losses)}")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"\n  Total P&L: ${total_pnl:+,.2f}")
    print(f"  Avg Win: ${avg_win:+,.2f}")
    print(f"  Avg Loss: ${avg_loss:+,.2f}")
    
    if avg_loss != 0:
        profit_factor = abs(sum(t["pnl"] for t in wins)) / abs(sum(t["pnl"] for t in losses))
        print(f"  Profit Factor: {profit_factor:.2f}")
    
    # Expectancy
    if sells:
        expectancy = total_pnl / len(sells)
        print(f"  Expectancy: ${expectancy:+,.2f} per trade")


# ============================================================================
# MAIN
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="My Portfolio Tracker")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Add position
    add_parser = subparsers.add_parser("add", help="Add or update a position")
    add_parser.add_argument("symbol", type=str, help="Stock symbol")
    add_parser.add_argument("price", type=float, help="Entry price")
    add_parser.add_argument("shares", type=int, help="Number of shares")
    add_parser.add_argument("--date", type=str, help="Entry date (YYYY-MM-DD)")
    add_parser.add_argument("--notes", type=str, default="", help="Notes")
    
    # Close position
    close_parser = subparsers.add_parser("close", help="Close a position")
    close_parser.add_argument("symbol", type=str, help="Stock symbol")
    close_parser.add_argument("price", type=float, help="Exit price")
    close_parser.add_argument("--shares", type=int, help="Shares to close (default: all)")
    
    # Show portfolio
    subparsers.add_parser("show", help="Show current portfolio")
    
    # Analyze portfolio
    subparsers.add_parser("analyze", help="Analyze positions with recommendations")
    
    # Trade history
    history_parser = subparsers.add_parser("history", help="Show trade history")
    history_parser.add_argument("--limit", type=int, default=20, help="Number of trades to show")
    
    # Performance
    subparsers.add_parser("performance", help="Show trading performance")
    
    args = parser.parse_args()
    
    if args.command == "add":
        add_position(args.symbol, args.price, args.shares, args.date, args.notes)
    elif args.command == "close":
        close_position(args.symbol, args.price, args.shares)
    elif args.command == "show":
        show_portfolio()
    elif args.command == "analyze":
        analyze_portfolio()
    elif args.command == "history":
        show_trade_history(args.limit)
    elif args.command == "performance":
        show_performance()
    else:
        # Default: show portfolio
        show_portfolio()


if __name__ == "__main__":
    main()
