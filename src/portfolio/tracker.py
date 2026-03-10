"""
Portfolio Tracker
=================
Core portfolio tracking logic - tracks positions, P&L, and provides analysis.

This module is meant to be used by AI assistants and tools, not directly by users.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
import yfinance as yf

logger = logging.getLogger(__name__)

# Data paths
DATA_DIR = Path(__file__).parent.parent.parent / "data"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
TRADES_FILE = DATA_DIR / "trade_history.json"


@dataclass
class Position:
    """A portfolio position."""
    symbol: str
    shares: int
    avg_cost: float
    first_entry: str
    last_entry: str
    total_cost: float
    notes: str = ""
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Position':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def add_shares(self, shares: int, price: float, date: str = None):
        """Add shares (updates avg cost)."""
        new_cost = shares * price
        self.total_cost += new_cost
        self.shares += shares
        self.avg_cost = self.total_cost / self.shares
        self.last_entry = date or datetime.now().strftime("%Y-%m-%d")
    
    def remove_shares(self, shares: int) -> int:
        """Remove shares. Returns actual shares removed."""
        removed = min(shares, self.shares)
        self.shares -= removed
        self.total_cost = self.shares * self.avg_cost
        return removed


@dataclass
class Trade:
    """A trade record."""
    timestamp: str
    type: str  # BUY, SELL
    symbol: str
    shares: int
    price: float
    date: str
    notes: str = ""
    avg_cost: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None


class PortfolioTracker:
    """
    Manages portfolio state and trade history.
    
    Usage:
        tracker = PortfolioTracker()
        tracker.add_position("XPEV", 21.00, 100, "2026-01-14", "Scanner signal")
        tracker.get_portfolio_summary()
    """
    
    def __init__(self):
        self.portfolio = self._load_portfolio()
        self.trades = self._load_trades()
    
    def _load_portfolio(self) -> Dict:
        """Load portfolio from disk."""
        if PORTFOLIO_FILE.exists():
            try:
                with open(PORTFOLIO_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning("Portfolio file corrupted, starting fresh")
        return {"positions": {}, "last_updated": None}
    
    def _save_portfolio(self):
        """Save portfolio to disk."""
        self.portfolio["last_updated"] = datetime.now().isoformat()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(self.portfolio, f, indent=2, default=str)
    
    def _load_trades(self) -> List[Dict]:
        """Load trade history."""
        if TRADES_FILE.exists():
            try:
                with open(TRADES_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning("Trade history corrupted, starting fresh")
        return []
    
    def _save_trade(self, trade: Trade):
        """Append trade to history."""
        self.trades.append(asdict(trade))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(TRADES_FILE, 'w') as f:
            json.dump(self.trades, f, indent=2, default=str)
    
    def _get_current_price(self, symbol: str) -> Optional[float]:
        """Fetch current price."""
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="1d")
            if not data.empty:
                return float(data['Close'].iloc[-1])
        except Exception as e:
            logger.warning(f"Failed to get price for {symbol}: {e}")
        return None
    
    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================
    
    def add_position(
        self,
        symbol: str,
        price: float,
        shares: int,
        date: str = None,
        notes: str = ""
    ) -> Dict:
        """
        Add or update a position.
        
        Returns:
            Dict with position details and status
        """
        symbol = symbol.upper()
        date = date or datetime.now().strftime("%Y-%m-%d")
        
        result = {"symbol": symbol, "action": "ADD", "date": date}
        
        if symbol in self.portfolio["positions"]:
            # Update existing
            pos = Position.from_dict(self.portfolio["positions"][symbol])
            old_shares = pos.shares
            old_avg = pos.avg_cost
            pos.add_shares(shares, price, date)
            result["previous"] = {"shares": old_shares, "avg_cost": old_avg}
            result["message"] = f"Added {shares} shares to {symbol}"
        else:
            # New position
            pos = Position(
                symbol=symbol,
                shares=shares,
                avg_cost=price,
                first_entry=date,
                last_entry=date,
                total_cost=shares * price,
                notes=notes
            )
            result["message"] = f"New position: {symbol}"
        
        self.portfolio["positions"][symbol] = pos.to_dict()
        self._save_portfolio()
        
        # Log trade
        trade = Trade(
            timestamp=datetime.now().isoformat(),
            type="BUY",
            symbol=symbol,
            shares=shares,
            price=price,
            date=date,
            notes=notes
        )
        self._save_trade(trade)
        
        result["position"] = pos.to_dict()
        result["current_price"] = self._get_current_price(symbol)
        
        return result
    
    def close_position(
        self,
        symbol: str,
        price: float,
        shares: int = None
    ) -> Dict:
        """
        Close or partially close a position.
        
        Returns:
            Dict with exit details and P&L
        """
        symbol = symbol.upper()
        
        if symbol not in self.portfolio["positions"]:
            return {"error": f"No position found for {symbol}"}
        
        pos = Position.from_dict(self.portfolio["positions"][symbol])
        shares_to_close = shares or pos.shares
        shares_closed = pos.remove_shares(shares_to_close)
        
        # Calculate P&L
        pnl = (price - pos.avg_cost) * shares_closed
        pnl_pct = (price / pos.avg_cost - 1) * 100
        
        result = {
            "symbol": symbol,
            "action": "SELL",
            "shares_closed": shares_closed,
            "exit_price": price,
            "avg_cost": pos.avg_cost,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "win": pnl > 0
        }
        
        if pos.shares <= 0:
            del self.portfolio["positions"][symbol]
            result["position_status"] = "CLOSED"
        else:
            self.portfolio["positions"][symbol] = pos.to_dict()
            result["position_status"] = "PARTIAL"
            result["remaining_shares"] = pos.shares
        
        self._save_portfolio()
        
        # Log trade
        trade = Trade(
            timestamp=datetime.now().isoformat(),
            type="SELL",
            symbol=symbol,
            shares=shares_closed,
            price=price,
            date=datetime.now().strftime("%Y-%m-%d"),
            avg_cost=pos.avg_cost,
            pnl=pnl,
            pnl_pct=pnl_pct
        )
        self._save_trade(trade)
        
        return result
    
    # =========================================================================
    # PORTFOLIO QUERIES
    # =========================================================================
    
    def get_positions(self) -> Dict[str, Position]:
        """Get all positions as Position objects."""
        return {
            symbol: Position.from_dict(data)
            for symbol, data in self.portfolio.get("positions", {}).items()
        }
    
    def get_portfolio_summary(self) -> Dict:
        """
        Get portfolio summary with current values and P&L.
        
        Returns:
            Dict with positions, totals, and performance
        """
        positions = []
        total_cost = 0
        total_value = 0
        total_pnl = 0
        
        for symbol, data in self.portfolio.get("positions", {}).items():
            pos = Position.from_dict(data)
            current_price = self._get_current_price(symbol) or pos.avg_cost
            
            value = current_price * pos.shares
            pnl = (current_price - pos.avg_cost) * pos.shares
            pnl_pct = (current_price / pos.avg_cost - 1) * 100
            
            positions.append({
                "symbol": symbol,
                "shares": pos.shares,
                "avg_cost": pos.avg_cost,
                "current_price": current_price,
                "value": value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "first_entry": pos.first_entry,
                "notes": pos.notes
            })
            
            total_cost += pos.total_cost
            total_value += value
            total_pnl += pnl
        
        # Sort by value
        positions.sort(key=lambda x: x["value"], reverse=True)
        
        return {
            "positions": positions,
            "total_cost": total_cost,
            "total_value": total_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": (total_value / total_cost - 1) * 100 if total_cost > 0 else 0,
            "position_count": len(positions),
            "last_updated": self.portfolio.get("last_updated")
        }
    
    def get_trade_history(self, limit: int = 50) -> List[Dict]:
        """Get recent trade history."""
        return self.trades[-limit:]
    
    def get_closed_trades(self) -> List[Dict]:
        """Get all closed trades (sells with P&L)."""
        return [t for t in self.trades if t.get("type") == "SELL" and t.get("pnl") is not None]
    
    def get_performance_stats(self) -> Dict:
        """Calculate trading performance statistics."""
        closed = self.get_closed_trades()
        
        if not closed:
            return {"message": "No closed trades"}
        
        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        
        total_pnl = sum(t["pnl"] for t in closed)
        win_rate = len(wins) / len(closed) * 100 if closed else 0
        
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        
        total_wins = sum(t["pnl"] for t in wins) if wins else 0
        total_losses = abs(sum(t["pnl"] for t in losses)) if losses else 0.001
        profit_factor = total_wins / total_losses if total_losses > 0 else 0
        
        expectancy = total_pnl / len(closed) if closed else 0
        
        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "expectancy": expectancy
        }


# Singleton instance for easy access
_tracker_instance = None

def get_tracker() -> PortfolioTracker:
    """Get singleton portfolio tracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = PortfolioTracker()
    return _tracker_instance
