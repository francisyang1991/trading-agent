"""
Expected Value Calculator
=========================

Functions for calculating expected value, Kelly criterion, and risk/reward
for trading decisions.

Usage:
    from src.strategy import calculate_ev, calculate_kelly, estimate_distribution
    
    # Calculate expected value
    ev = calculate_ev(win_rate=0.55, avg_win=5.0, avg_loss=3.0)
    
    # Calculate Kelly position size
    kelly = calculate_kelly(win_rate=0.55, avg_win=5.0, avg_loss=3.0)
    
    # Estimate from historical data
    dist = estimate_distribution(price_data, forward_days=5)
"""

from typing import Dict, Optional, Tuple
import pandas as pd
import numpy as np


def calculate_ev(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Calculate Expected Value of a trade.
    
    EV = P(win) * E(win) - P(loss) * E(loss)
    
    Args:
        win_rate: Probability of winning (0 to 1)
        avg_win: Average win amount (percentage)
        avg_loss: Average loss amount (percentage, positive value)
        
    Returns:
        Expected value as percentage
        
    Example:
        >>> ev = calculate_ev(0.55, 5.0, 3.0)  # 55% win rate, +5% avg win, -3% avg loss
        >>> print(f"EV: {ev:.2f}%")  # EV: 1.40%
    """
    return win_rate * avg_win - (1 - win_rate) * avg_loss


def calculate_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Calculate Kelly Criterion for optimal position sizing.
    
    Kelly f* = (p*b - q) / b
    Where:
        p = probability of win
        q = probability of loss (1 - p)
        b = win/loss ratio (avg_win / avg_loss)
    
    Result is capped at 0.25 (25%) for safety.
    
    Args:
        win_rate: Probability of winning (0 to 1)
        avg_win: Average win amount (percentage)
        avg_loss: Average loss amount (percentage, positive value)
        
    Returns:
        Kelly fraction (0 to 0.25)
        
    Example:
        >>> kelly = calculate_kelly(0.55, 5.0, 3.0)
        >>> position_size = account_value * kelly
    """
    if avg_loss == 0:
        return 0
    
    b = avg_win / avg_loss  # Win/loss ratio
    q = 1 - win_rate        # Loss probability
    
    kelly = (win_rate * b - q) / b
    
    # Cap at 25% and floor at 0%
    return max(0, min(0.25, kelly))


def calculate_half_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Calculate Half-Kelly for more conservative position sizing.
    
    Many traders use half-Kelly to reduce variance while maintaining
    most of the expected growth rate.
    
    Args:
        win_rate: Probability of winning (0 to 1)
        avg_win: Average win amount (percentage)
        avg_loss: Average loss amount (percentage, positive value)
        
    Returns:
        Half-Kelly fraction (0 to 0.125)
    """
    return calculate_kelly(win_rate, avg_win, avg_loss) / 2


def estimate_distribution(
    data: pd.DataFrame,
    forward_days: int = 5,
    close_col: str = 'Close'
) -> Dict[str, float]:
    """
    Estimate forward return distribution from historical data.
    
    Uses historical price data to estimate win rate and average
    win/loss magnitudes.
    
    Args:
        data: DataFrame with price data
        forward_days: Number of days to look forward for returns
        close_col: Column name for close prices
        
    Returns:
        Dict with 'win_rate', 'avg_win', 'avg_loss' keys
        
    Example:
        >>> dist = estimate_distribution(price_df, forward_days=5)
        >>> print(f"Win rate: {dist['win_rate']:.1%}")
        >>> print(f"Avg win: {dist['avg_win']:.2f}%")
    """
    close = data[close_col] if close_col in data.columns else data['close']
    
    # Calculate forward returns
    forward_returns = (close.shift(-forward_days) / close - 1) * 100
    forward_returns = forward_returns.dropna()
    
    if len(forward_returns) < 50:
        # Not enough data, return neutral estimates
        return {'win_rate': 0.5, 'avg_win': 5.0, 'avg_loss': 5.0}
    
    wins = forward_returns[forward_returns > 0]
    losses = forward_returns[forward_returns <= 0]
    
    return {
        'win_rate': len(wins) / len(forward_returns),
        'avg_win': wins.mean() if len(wins) > 0 else 0.0,
        'avg_loss': abs(losses.mean()) if len(losses) > 0 else 0.0
    }


def calculate_risk_reward(
    entry: float,
    stop_loss: float,
    target: float
) -> float:
    """
    Calculate risk/reward ratio.
    
    R:R = (Target - Entry) / (Entry - Stop)
    
    Args:
        entry: Entry price
        stop_loss: Stop loss price
        target: Target price
        
    Returns:
        Risk/reward ratio (e.g., 2.0 means 2:1 R:R)
        
    Example:
        >>> rr = calculate_risk_reward(entry=100, stop_loss=95, target=110)
        >>> print(f"R:R: {rr:.1f}x")  # R:R: 2.0x
    """
    risk = entry - stop_loss
    reward = target - entry
    
    if risk <= 0:
        return 0.0
    
    return reward / risk


def calculate_position_size(
    account_value: float,
    entry: float,
    stop_loss: float,
    risk_per_trade_pct: float = 0.02,
    max_position_pct: float = 0.15
) -> Tuple[int, float]:
    """
    Calculate position size based on fixed risk per trade.
    
    Determines how many shares to buy such that if the stop loss is hit,
    the loss equals risk_per_trade_pct of account value.
    
    Args:
        account_value: Total account value
        entry: Expected entry price
        stop_loss: Stop loss price
        risk_per_trade_pct: Maximum risk per trade (default 2%)
        max_position_pct: Maximum position as % of account (default 15%)
        
    Returns:
        Tuple of (shares, position_value)
        
    Example:
        >>> shares, value = calculate_position_size(
        ...     account_value=100000,
        ...     entry=50.0,
        ...     stop_loss=47.5,
        ...     risk_per_trade_pct=0.02
        ... )
        >>> print(f"Buy {shares} shares (${value:.0f})")
    """
    risk_per_share = entry - stop_loss
    
    if risk_per_share <= 0:
        return 0, 0.0
    
    # Max dollars to risk
    max_risk_dollars = account_value * risk_per_trade_pct
    
    # Shares based on risk
    shares_from_risk = int(max_risk_dollars / risk_per_share)
    
    # Cap by max position size
    max_position_value = account_value * max_position_pct
    shares_from_max = int(max_position_value / entry)
    
    shares = min(shares_from_risk, shares_from_max)
    position_value = shares * entry
    
    return shares, position_value


def is_positive_ev(win_rate: float, avg_win: float, avg_loss: float) -> bool:
    """Check if expected value is positive."""
    return calculate_ev(win_rate, avg_win, avg_loss) > 0


def meets_minimum_criteria(
    ev: float,
    risk_reward: float,
    ev_threshold: float = 0.5,
    rr_threshold: float = 1.5
) -> bool:
    """
    Check if trade meets minimum EV and R:R thresholds.
    
    Default thresholds from scanner configuration:
    - EV > 0.5%
    - R:R > 1.5x
    
    Args:
        ev: Expected value percentage
        risk_reward: Risk/reward ratio
        ev_threshold: Minimum EV (default 0.5%)
        rr_threshold: Minimum R:R (default 1.5x)
        
    Returns:
        True if both thresholds met
    """
    return ev > ev_threshold and risk_reward > rr_threshold
