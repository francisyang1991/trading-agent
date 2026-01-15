"""
Performance Metrics
===================

Calculate comprehensive trading performance metrics.
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from ..core.types import Trade


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics."""
    # Returns
    total_return: float = 0.0
    total_return_pct: float = 0.0
    annualized_return: float = 0.0
    
    # Risk
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration_days: int = 0
    
    # Trading
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    # P&L
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    
    # Per trade
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_win_loss_ratio: float = 0.0
    avg_trade_pnl: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    
    # Streaks
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    current_streak: int = 0
    
    # Time
    avg_holding_days: float = 0.0
    avg_winning_holding_days: float = 0.0
    avg_losing_holding_days: float = 0.0
    
    # By category
    metrics_by_strategy: Dict = field(default_factory=dict)
    metrics_by_symbol: Dict = field(default_factory=dict)


def calculate_metrics(
    trades: List[Trade],
    equity_curve: Optional[pd.Series] = None,
    initial_capital: float = 100000.0,
    risk_free_rate: float = 0.04
) -> PerformanceMetrics:
    """
    Calculate comprehensive performance metrics.
    
    Args:
        trades: List of completed trades
        equity_curve: Optional daily equity series
        initial_capital: Starting capital
        risk_free_rate: Risk-free rate for Sharpe
        
    Returns:
        PerformanceMetrics object
    """
    metrics = PerformanceMetrics()
    
    if not trades:
        return metrics
    
    # Basic counts
    metrics.total_trades = len(trades)
    winning = [t for t in trades if t.pnl > 0]
    losing = [t for t in trades if t.pnl <= 0]
    
    metrics.winning_trades = len(winning)
    metrics.losing_trades = len(losing)
    metrics.win_rate = len(winning) / len(trades) * 100 if trades else 0
    
    # P&L metrics
    pnls = [t.pnl for t in trades]
    metrics.total_return = sum(pnls)
    metrics.total_return_pct = metrics.total_return / initial_capital * 100
    
    metrics.gross_profit = sum(t.pnl for t in winning) if winning else 0
    metrics.gross_loss = abs(sum(t.pnl for t in losing)) if losing else 0
    metrics.profit_factor = (
        metrics.gross_profit / metrics.gross_loss 
        if metrics.gross_loss > 0 else float('inf')
    )
    
    # Per trade metrics
    metrics.avg_trade_pnl = np.mean(pnls) if pnls else 0
    metrics.avg_win = np.mean([t.pnl for t in winning]) if winning else 0
    metrics.avg_loss = abs(np.mean([t.pnl for t in losing])) if losing else 0
    metrics.avg_win_loss_ratio = (
        metrics.avg_win / metrics.avg_loss 
        if metrics.avg_loss > 0 else float('inf')
    )
    
    metrics.largest_win = max(pnls) if pnls else 0
    metrics.largest_loss = min(pnls) if pnls else 0
    
    # Streaks
    metrics.max_consecutive_wins = _calculate_streak(trades, win=True)
    metrics.max_consecutive_losses = _calculate_streak(trades, win=False)
    
    # Holding time
    if trades:
        holding_days = [t.holding_days for t in trades if hasattr(t, 'holding_days')]
        metrics.avg_holding_days = np.mean(holding_days) if holding_days else 0
        
        winning_days = [t.holding_days for t in winning if hasattr(t, 'holding_days')]
        metrics.avg_winning_holding_days = np.mean(winning_days) if winning_days else 0
        
        losing_days = [t.holding_days for t in losing if hasattr(t, 'holding_days')]
        metrics.avg_losing_holding_days = np.mean(losing_days) if losing_days else 0
    
    # Risk metrics from equity curve
    if equity_curve is not None and len(equity_curve) > 1:
        returns = equity_curve.pct_change().dropna()
        
        # Volatility (annualized)
        metrics.volatility = returns.std() * np.sqrt(252)
        
        # Sharpe ratio
        excess_returns = returns - risk_free_rate / 252
        if returns.std() > 0:
            metrics.sharpe_ratio = np.sqrt(252) * excess_returns.mean() / returns.std()
        
        # Sortino ratio (downside deviation)
        downside = returns[returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            metrics.sortino_ratio = np.sqrt(252) * excess_returns.mean() / downside.std()
        
        # Max drawdown
        cumulative = (1 + returns).cumprod()
        rolling_max = cumulative.expanding().max()
        drawdown = (cumulative - rolling_max) / rolling_max
        metrics.max_drawdown = abs(drawdown.min()) * 100
        
        # Drawdown duration
        is_dd = drawdown < 0
        if is_dd.any():
            dd_groups = (is_dd != is_dd.shift()).cumsum()
            dd_lengths = is_dd.groupby(dd_groups).sum()
            metrics.max_drawdown_duration_days = int(dd_lengths.max())
        
        # Annualized return
        total_days = len(equity_curve)
        years = total_days / 252
        if years > 0:
            final_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
            metrics.annualized_return = ((1 + final_return) ** (1 / years) - 1) * 100
    
    return metrics


def _calculate_streak(trades: List[Trade], win: bool) -> int:
    """Calculate max consecutive wins/losses."""
    max_streak = 0
    current = 0
    
    for trade in trades:
        is_win = trade.pnl > 0
        
        if is_win == win:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    
    return max_streak


def calculate_risk_metrics(returns: pd.Series, risk_free_rate: float = 0.04) -> Dict:
    """Calculate risk-adjusted metrics from returns."""
    if len(returns) < 2:
        return {}
    
    # Annualized metrics
    ann_return = returns.mean() * 252
    ann_vol = returns.std() * np.sqrt(252)
    
    # Sharpe
    excess = returns - risk_free_rate / 252
    sharpe = np.sqrt(252) * excess.mean() / returns.std() if returns.std() > 0 else 0
    
    # Sortino
    downside = returns[returns < 0]
    sortino = 0
    if len(downside) > 0 and downside.std() > 0:
        sortino = np.sqrt(252) * excess.mean() / downside.std()
    
    # Calmar
    cumulative = (1 + returns).cumprod()
    rolling_max = cumulative.expanding().max()
    drawdown = (cumulative - rolling_max) / rolling_max
    max_dd = abs(drawdown.min())
    calmar = ann_return / max_dd if max_dd > 0 else 0
    
    return {
        'annualized_return': ann_return * 100,
        'annualized_volatility': ann_vol * 100,
        'sharpe_ratio': sharpe,
        'sortino_ratio': sortino,
        'calmar_ratio': calmar,
        'max_drawdown': max_dd * 100,
    }
