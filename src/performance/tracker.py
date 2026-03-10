"""
Performance Tracker
===================

Track and analyze trading performance in real-time.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from ..core.types import Trade, Position
from .metrics import calculate_metrics, PerformanceMetrics


@dataclass
class DailySnapshot:
    """Daily performance snapshot."""
    date: datetime
    equity: float
    cash: float
    positions_value: float
    daily_pnl: float
    daily_pnl_pct: float
    trades_today: int
    positions_count: int


class PerformanceTracker:
    """
    Real-time Performance Tracking.
    
    Features:
    - Equity curve tracking
    - Daily/weekly/monthly metrics
    - Trade-by-trade analysis
    - Attribution by strategy/symbol
    
    Example:
        tracker = PerformanceTracker(initial_capital=100000)
        
        # Record daily snapshot
        tracker.record_snapshot(equity=102500, cash=50000, positions_value=52500)
        
        # Record trade
        tracker.record_trade(trade)
        
        # Get metrics
        metrics = tracker.get_metrics()
        print(f"Sharpe: {metrics.sharpe_ratio:.2f}")
    """
    
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        
        # Trade history
        self._trades: List[Trade] = []
        
        # Equity curve
        self._equity_curve: List[DailySnapshot] = []
        
        # Current state
        self._current_equity = initial_capital
        self._high_water_mark = initial_capital
        self._current_drawdown = 0.0
    
    def record_snapshot(
        self,
        equity: float,
        cash: float,
        positions_value: float,
        date: Optional[datetime] = None,
        trades_today: int = 0,
        positions_count: int = 0
    ):
        """Record daily equity snapshot."""
        if date is None:
            date = datetime.now()
        
        # Calculate daily P&L
        if self._equity_curve:
            prev_equity = self._equity_curve[-1].equity
            daily_pnl = equity - prev_equity
            daily_pnl_pct = daily_pnl / prev_equity * 100 if prev_equity > 0 else 0
        else:
            daily_pnl = equity - self.initial_capital
            daily_pnl_pct = daily_pnl / self.initial_capital * 100
        
        snapshot = DailySnapshot(
            date=date,
            equity=equity,
            cash=cash,
            positions_value=positions_value,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            trades_today=trades_today,
            positions_count=positions_count
        )
        
        self._equity_curve.append(snapshot)
        
        # Update high water mark and drawdown
        self._current_equity = equity
        if equity > self._high_water_mark:
            self._high_water_mark = equity
        
        self._current_drawdown = (
            (self._high_water_mark - equity) / self._high_water_mark * 100
            if self._high_water_mark > 0 else 0
        )
    
    def record_trade(self, trade: Trade):
        """Record completed trade."""
        self._trades.append(trade)
    
    def get_metrics(self) -> PerformanceMetrics:
        """Calculate current performance metrics."""
        equity_series = None
        if self._equity_curve:
            equity_series = pd.Series(
                [s.equity for s in self._equity_curve],
                index=[s.date for s in self._equity_curve]
            )
        
        return calculate_metrics(
            trades=self._trades,
            equity_curve=equity_series,
            initial_capital=self.initial_capital
        )
    
    def get_equity_curve(self) -> pd.DataFrame:
        """Get equity curve as DataFrame."""
        if not self._equity_curve:
            return pd.DataFrame()
        
        data = [{
            'date': s.date,
            'equity': s.equity,
            'cash': s.cash,
            'positions_value': s.positions_value,
            'daily_pnl': s.daily_pnl,
            'daily_pnl_pct': s.daily_pnl_pct,
            'trades': s.trades_today,
            'positions': s.positions_count
        } for s in self._equity_curve]
        
        return pd.DataFrame(data).set_index('date')
    
    def get_summary(self) -> Dict:
        """Get performance summary."""
        metrics = self.get_metrics()
        
        return {
            # Overview
            'initial_capital': self.initial_capital,
            'current_equity': self._current_equity,
            'total_return': self._current_equity - self.initial_capital,
            'total_return_pct': (self._current_equity - self.initial_capital) / self.initial_capital * 100,
            
            # Drawdown
            'current_drawdown': self._current_drawdown,
            'max_drawdown': metrics.max_drawdown,
            'high_water_mark': self._high_water_mark,
            
            # Trading
            'total_trades': metrics.total_trades,
            'win_rate': metrics.win_rate,
            'profit_factor': metrics.profit_factor,
            
            # Risk-adjusted
            'sharpe_ratio': metrics.sharpe_ratio,
            'sortino_ratio': metrics.sortino_ratio,
            
            # Per trade
            'avg_win': metrics.avg_win,
            'avg_loss': metrics.avg_loss,
            'avg_win_loss_ratio': metrics.avg_win_loss_ratio,
        }
    
    def get_daily_returns(self) -> pd.Series:
        """Get daily returns series."""
        if not self._equity_curve:
            return pd.Series()
        
        equity = pd.Series(
            [s.equity for s in self._equity_curve],
            index=[s.date for s in self._equity_curve]
        )
        
        return equity.pct_change().dropna()
    
    def get_monthly_returns(self) -> pd.Series:
        """Get monthly returns."""
        daily = self.get_daily_returns()
        if daily.empty:
            return pd.Series()
        
        monthly = (1 + daily).resample('ME').prod() - 1
        return monthly * 100
    
    def get_trades_by_strategy(self) -> Dict[str, List[Trade]]:
        """Group trades by strategy."""
        by_strategy = {}
        
        for trade in self._trades:
            strategy = trade.strategy.value if trade.strategy else 'unknown'
            if strategy not in by_strategy:
                by_strategy[strategy] = []
            by_strategy[strategy].append(trade)
        
        return by_strategy
    
    def get_trades_by_symbol(self) -> Dict[str, List[Trade]]:
        """Group trades by symbol."""
        by_symbol = {}
        
        for trade in self._trades:
            symbol = trade.symbol
            if symbol not in by_symbol:
                by_symbol[symbol] = []
            by_symbol[symbol].append(trade)
        
        return by_symbol
    
    def print_summary(self):
        """Print performance summary."""
        summary = self.get_summary()
        
        print("\n" + "=" * 60)
        print("  PERFORMANCE SUMMARY")
        print("=" * 60)
        
        print(f"\n  RETURNS")
        print(f"  {'─' * 40}")
        print(f"  Initial Capital:  ${summary['initial_capital']:,.2f}")
        print(f"  Current Equity:   ${summary['current_equity']:,.2f}")
        print(f"  Total Return:     ${summary['total_return']:,.2f} ({summary['total_return_pct']:.2f}%)")
        
        print(f"\n  RISK")
        print(f"  {'─' * 40}")
        print(f"  Current Drawdown: {summary['current_drawdown']:.2f}%")
        print(f"  Max Drawdown:     {summary['max_drawdown']:.2f}%")
        print(f"  Sharpe Ratio:     {summary['sharpe_ratio']:.2f}")
        print(f"  Sortino Ratio:    {summary['sortino_ratio']:.2f}")
        
        print(f"\n  TRADING")
        print(f"  {'─' * 40}")
        print(f"  Total Trades:     {summary['total_trades']}")
        print(f"  Win Rate:         {summary['win_rate']:.1f}%")
        print(f"  Profit Factor:    {summary['profit_factor']:.2f}")
        print(f"  Avg Win:          ${summary['avg_win']:,.2f}")
        print(f"  Avg Loss:         ${summary['avg_loss']:,.2f}")
        print(f"  Win/Loss Ratio:   {summary['avg_win_loss_ratio']:.2f}")
        
        print()
