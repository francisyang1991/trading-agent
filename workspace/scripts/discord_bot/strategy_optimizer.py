#!/usr/bin/env python3
"""
Strategy Optimizer — Systematic Parameter Sweep for Replay Engine
=================================================================
Iterates through strategy parameter combinations to find a configuration
that turns $50K → $100K using the replay engine's historical simulation.

Approach:
  1. Grid search over key parameters (stop %, target %, score threshold, etc.)
  2. Each combo runs a full replay on historical data
  3. Ranks by total return, then filters for risk metrics
  4. Reports top strategies and saves the best config

Usage:
    python strategy_optimizer.py
    python strategy_optimizer.py --start 2026-01-06 --end 2026-02-10
    python strategy_optimizer.py --quick   # Fewer combos, faster
"""

import os
import sys
import json
import time
import itertools
import argparse
from datetime import datetime
from dataclasses import dataclass
from typing import List, Dict, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Suppress yfinance warnings
import warnings
warnings.filterwarnings('ignore')

from replay_engine import (
    ReplayEngine, load_price_data, load_universe,
    load_discord_signals_for_date, _price_cache,
    get_close, get_price, compute_rsi, compute_ema,
    VirtualPosition, COMMISSION_PER_TRADE,
)


# =========================================================================
# Parameterized Strategy
# =========================================================================

@dataclass
class StrategyParams:
    """Tunable strategy parameters."""
    # Entry
    buy_threshold: float = 4.5       # Min conviction score to BUY
    min_rr: float = 1.5              # Min risk:reward ratio
    max_rsi: float = 65              # Max RSI for entry (avoid overbought)
    min_rsi: float = 25              # Min RSI for entry (avoid falling knives)
    require_ema_align: bool = True   # Require price > EMA8 > EMA21

    # Position sizing
    position_pct: float = 0.08       # % of capital per position
    max_positions: int = 8           # Max concurrent positions

    # Exit
    stop_pct: float = 0.04           # Stop loss %
    target_pct: float = 0.12         # Take profit %
    trailing_stop: bool = False      # Use trailing stop
    trail_pct: float = 0.03          # Trailing stop distance

    # Scoring weights (override offline scoring)
    ema_align_bonus: float = 3.0     # Bonus for full EMA alignment
    rsi_pullback_bonus: float = 1.5  # Bonus for RSI in sweet spot
    discord_weight: float = 1.0      # Multiplier for discord signals

    def to_dict(self):
        return self.__dict__.copy()

    def key(self) -> str:
        return (
            f"buy{self.buy_threshold}_stop{self.stop_pct}_tgt{self.target_pct}_"
            f"pos{self.position_pct}_maxp{self.max_positions}_"
            f"rsi{self.min_rsi}-{self.max_rsi}_rr{self.min_rr}_"
            f"trail{self.trailing_stop}"
        )


# =========================================================================
# Parameterized Scoring & Replay
# =========================================================================

def score_with_params(
    ticker: str,
    sim_date: str,
    discord_sig: dict,
    params: StrategyParams,
) -> dict:
    """Score a ticker using parameterized strategy."""
    price = get_close(ticker, sim_date)
    if price <= 0:
        return None

    rsi = compute_rsi(ticker, sim_date)
    ema8 = compute_ema(ticker, sim_date, 8)
    ema21 = compute_ema(ticker, sim_date, 21)
    ema50 = compute_ema(ticker, sim_date, 50)

    # RSI filter
    if rsi > params.max_rsi or rsi < params.min_rsi:
        return None

    mentions = discord_sig.get('mentions', 0)
    bullish = discord_sig.get('bullish', 0)
    bearish = discord_sig.get('bearish', 0)

    score = 0.0

    # Discord signals (weighted)
    if mentions >= 5:
        score += 2.0 * params.discord_weight
    elif mentions >= 3:
        score += 1.5 * params.discord_weight
    elif mentions >= 1:
        score += 0.5 * params.discord_weight
    if bullish > bearish:
        score += min((bullish - bearish) * 0.5, 1.0) * params.discord_weight
    elif bearish > bullish:
        score -= 0.5

    # EMA alignment scoring
    if ema8 > 0 and ema21 > 0 and ema50 > 0:
        if price > ema8 > ema21 > ema50:
            score += params.ema_align_bonus  # Full alignment
        elif price > ema8 > ema21:
            score += params.ema_align_bonus * 0.7
        elif price > ema21 > ema50:
            score += params.ema_align_bonus * 0.4
        elif price < ema21 and ema8 < ema21:
            score -= 1.5  # Downtrend penalty
    elif ema8 > 0 and ema21 > 0:
        if price > ema8 > ema21:
            score += params.ema_align_bonus * 0.7
        elif price > ema21:
            score += params.ema_align_bonus * 0.3

    # RSI pullback bonus
    if 35 < rsi < 50:
        score += params.rsi_pullback_bonus  # Ideal pullback zone
    elif 30 < rsi <= 35:
        score += params.rsi_pullback_bonus * 0.7
    elif 50 <= rsi < 60:
        score += params.rsi_pullback_bonus * 0.3
    elif rsi > 70:
        score -= 1.0

    # Support proximity
    if ema21 > 0 and price > 0:
        dist = (price - ema21) / price
        if 0 <= dist < 0.02:
            score += 1.0  # Right on EMA21
        elif 0.02 <= dist < 0.04:
            score += 0.5

    if discord_sig.get('has_images'):
        score += 0.3

    score = max(0, min(score, 10))

    # Risk/reward
    stop_price = price * (1 - params.stop_pct)
    target_price = price * (1 + params.target_pct)
    risk = price - stop_price
    reward = target_price - price
    rr = reward / risk if risk > 0 else 0

    if rr < params.min_rr:
        return None  # Skip if R:R too low

    # EMA alignment requirement
    if params.require_ema_align and ema8 > 0 and ema21 > 0:
        if not (price > ema8 and price > ema21):
            return None  # Skip if not above key MAs

    if score < params.buy_threshold:
        return None

    return {
        'ticker': ticker,
        'price': price,
        'rsi': rsi,
        'ema8': ema8,
        'ema21': ema21,
        'conviction_score': round(score, 1),
        'action': 'BUY',
        'stop_loss': round(stop_price, 2),
        'target': round(target_price, 2),
        'risk_reward': round(rr, 1),
    }


def run_parameterized_replay(
    params: StrategyParams,
    start_date: str,
    end_date: str,
    capital: float,
    universe: List[str],
    trading_days: List[str],
    discord_cache: Dict[str, Dict],
) -> Dict:
    """Run a single replay with given parameters. Returns summary dict."""
    cash = capital
    positions: List[VirtualPosition] = []
    closed_trades: List[VirtualPosition] = []
    peak_value = capital
    max_dd = 0
    daily_returns = []
    prev_value = capital

    for day in trading_days:
        # Check stops and targets
        still_open = []
        for pos in positions:
            p = get_price(pos.ticker, day)
            if not p:
                still_open.append(pos)
                continue

            hit_stop = False
            hit_target = False

            # Trailing stop update
            if params.trailing_stop and p['high'] > pos.entry_price:
                new_trail = p['high'] * (1 - params.trail_pct)
                if new_trail > pos.stop_loss:
                    pos.stop_loss = new_trail

            if p['low'] <= pos.stop_loss:
                hit_stop = True
                pos.exit_price = pos.stop_loss
            elif p['high'] >= pos.target:
                hit_target = True
                pos.exit_price = pos.target

            if hit_stop or hit_target:
                pos.exit_date = day
                pos.pnl = (pos.exit_price - pos.entry_price) * pos.shares - COMMISSION_PER_TRADE
                pos.pnl_pct = (pos.pnl / pos.cost_basis) * 100 if pos.cost_basis > 0 else 0
                pos.status = "stopped" if hit_stop else "target_hit"
                cash += pos.cost_basis + pos.pnl
                closed_trades.append(pos)
            else:
                still_open.append(pos)
        positions = still_open

        # Score candidates
        discord_signals = discord_cache.get(day, {})
        all_tickers = list(set(universe + list(discord_signals.keys())))

        candidates = []
        for ticker in all_tickers:
            dsig = discord_signals.get(ticker, {})
            result = score_with_params(ticker, day, dsig, params)
            if result:
                candidates.append(result)

        candidates.sort(key=lambda x: x['conviction_score'], reverse=True)

        # Place orders
        held_tickers = {p.ticker for p in positions}
        for cand in candidates:
            if len(positions) >= params.max_positions:
                break
            if cand['ticker'] in held_tickers:
                continue

            position_value = cash * params.position_pct
            if position_value < 100 or cash < position_value:
                continue

            entry_price = cand['price']
            shares = int(position_value / entry_price)
            if shares <= 0:
                continue
            cost = shares * entry_price + COMMISSION_PER_TRADE

            pos = VirtualPosition(
                ticker=cand['ticker'],
                direction="LONG",
                entry_price=entry_price,
                entry_date=day,
                shares=shares,
                stop_loss=cand['stop_loss'],
                target=cand['target'],
                conviction_score=cand['conviction_score'],
                cost_basis=cost,
            )
            positions.append(pos)
            cash -= cost
            held_tickers.add(cand['ticker'])

        # Portfolio value
        mkt_value = sum(
            p.shares * get_close(p.ticker, day) for p in positions
            if get_close(p.ticker, day) > 0
        )
        portfolio_value = cash + mkt_value
        daily_returns.append((portfolio_value - prev_value) / prev_value if prev_value > 0 else 0)
        prev_value = portfolio_value

        if portfolio_value > peak_value:
            peak_value = portfolio_value
        dd = (peak_value - portfolio_value) / peak_value * 100
        if dd > max_dd:
            max_dd = dd

    # Close remaining
    final_day = trading_days[-1] if trading_days else end_date
    for pos in list(positions):
        close = get_close(pos.ticker, final_day)
        if close > 0:
            pos.exit_price = close
            pos.pnl = (close - pos.entry_price) * pos.shares - COMMISSION_PER_TRADE
            pos.pnl_pct = (pos.pnl / pos.cost_basis) * 100 if pos.cost_basis > 0 else 0
            pos.status = "closed"
            cash += pos.cost_basis + pos.pnl
            closed_trades.append(pos)

    final_capital = cash
    total_trades = len(closed_trades)
    winners = [t for t in closed_trades if t.pnl > 0]
    losers = [t for t in closed_trades if t.pnl <= 0]
    win_rate = len(winners) / total_trades * 100 if total_trades > 0 else 0
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    avg_ret = sum(daily_returns) / len(daily_returns) if daily_returns else 0
    std_ret = (sum((r - avg_ret) ** 2 for r in daily_returns) / max(len(daily_returns), 1)) ** 0.5
    sharpe = (avg_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0

    return {
        'final_capital': final_capital,
        'total_pnl': final_capital - capital,
        'total_return_pct': (final_capital / capital - 1) * 100,
        'total_trades': total_trades,
        'win_rate': win_rate,
        'profit_factor': pf,
        'max_drawdown': max_dd,
        'sharpe': sharpe,
        'avg_win': sum(t.pnl for t in winners) / len(winners) if winners else 0,
        'avg_loss': sum(t.pnl for t in losers) / len(losers) if losers else 0,
        'params_key': params.key(),
    }


# =========================================================================
# Parameter Grid Generation
# =========================================================================

def generate_param_grid(quick: bool = False) -> List[StrategyParams]:
    """Generate parameter combinations to test."""
    if quick:
        grid = {
            'buy_threshold': [4.0, 5.0, 6.0],
            'stop_pct': [0.03, 0.05, 0.07],
            'target_pct': [0.08, 0.12, 0.18],
            'position_pct': [0.08, 0.10],
            'max_positions': [6, 10],
            'trailing_stop': [False, True],
        }
    else:
        grid = {
            'buy_threshold': [3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
            'stop_pct': [0.02, 0.03, 0.04, 0.05, 0.07, 0.10],
            'target_pct': [0.06, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30],
            'position_pct': [0.06, 0.08, 0.10],
            'max_positions': [5, 8, 10],
            'trailing_stop': [False, True],
            'max_rsi': [60, 65, 70],
            'require_ema_align': [True, False],
        }

    combos = []

    # Generate all combinations
    keys = list(grid.keys())
    for vals in itertools.product(*grid.values()):
        p = StrategyParams()
        for k, v in zip(keys, vals):
            setattr(p, k, v)

        # Skip obviously bad combos
        if p.target_pct <= p.stop_pct:
            continue  # Target must be larger than stop
        if p.target_pct / p.stop_pct < 1.5:
            continue  # R:R too low

        combos.append(p)

    return combos


# =========================================================================
# Main Optimizer
# =========================================================================

def run_optimizer(start_date: str, end_date: str, capital: float = 50_000, quick: bool = False):
    """Run the full optimization sweep."""
    goal = capital * 2  # Target: double the capital

    print(f"\n{'=' * 70}")
    print(f"  STRATEGY OPTIMIZER")
    print(f"  Goal: ${capital:,.0f} → ${goal:,.0f} ({((goal/capital)-1)*100:.0f}% return)")
    print(f"  Period: {start_date} to {end_date}")
    print(f"{'=' * 70}\n")

    # Load universe and prices once
    print("Step 1: Loading data...")
    universe = load_universe(80)
    print(f"  Universe: {len(universe)} tickers")

    # Pre-compute Discord signals for all dates
    from datetime import datetime as dt, timedelta
    start = dt.strptime(start_date, '%Y-%m-%d')
    end = dt.strptime(end_date, '%Y-%m-%d')
    current = start
    trading_days = []
    while current <= end:
        if current.weekday() < 5:
            trading_days.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    print(f"  Trading days: {len(trading_days)}")

    # Pre-cache Discord signals
    print("  Loading Discord signals...")
    discord_cache = {}
    for day in trading_days:
        discord_cache[day] = load_discord_signals_for_date(day, lookback_days=3)

    # Collect all tickers
    all_discord = set()
    for signals in discord_cache.values():
        all_discord.update(signals.keys())
    all_tickers = list(set(universe + list(all_discord)))
    print(f"  All tickers: {len(all_tickers)}")

    # Load prices once
    print("  Loading price data...")
    load_price_data(all_tickers, start_date, end_date)

    # Generate parameter grid
    params_list = generate_param_grid(quick=quick)
    print(f"\nStep 2: Testing {len(params_list)} parameter combinations...")
    print(f"{'#':>5} {'Return':>8} {'Trades':>6} {'WR%':>6} {'PF':>6} {'MaxDD':>6} {'Sharpe':>7} {'Config'}")
    print("-" * 90)

    results = []
    best_return = -999
    goal_reached = False

    for i, params in enumerate(params_list):
        result = run_parameterized_replay(
            params=params,
            start_date=start_date,
            end_date=end_date,
            capital=capital,
            universe=universe,
            trading_days=trading_days,
            discord_cache=discord_cache,
        )
        result['params'] = params.to_dict()
        results.append(result)

        ret = result['total_return_pct']
        marker = ""
        if ret > best_return:
            best_return = ret
            marker = " *** NEW BEST"
        if result['final_capital'] >= goal:
            marker = " *** GOAL REACHED!"
            goal_reached = True

        if i < 20 or ret > best_return - 5 or marker:
            print(
                f"{i+1:5d} {ret:+7.1f}% {result['total_trades']:6d} "
                f"{result['win_rate']:5.1f}% {result['profit_factor']:5.2f} "
                f"{result['max_drawdown']:5.1f}% {result['sharpe']:6.2f}  "
                f"stop={params.stop_pct:.0%} tgt={params.target_pct:.0%} "
                f"thr={params.buy_threshold} pos={params.position_pct:.0%} "
                f"trail={'Y' if params.trailing_stop else 'N'}"
                f"{marker}"
            )

        if goal_reached:
            # Keep searching a bit more for better solutions
            if i > 50 and ret < best_return * 0.5:
                pass  # Skip obviously bad ones after goal

    # Sort results
    results.sort(key=lambda x: x['total_return_pct'], reverse=True)

    # Print top 10
    print(f"\n{'=' * 70}")
    print(f"  TOP 10 STRATEGIES")
    print(f"{'=' * 70}")
    print(f"{'Rank':>4} {'Return':>9} {'Final$':>10} {'Trades':>6} {'WR%':>6} {'PF':>6} {'MaxDD':>6} {'Sharpe':>7}")
    print("-" * 65)

    for i, r in enumerate(results[:10], 1):
        color = "\033[92m" if r['total_return_pct'] > 0 else "\033[91m"
        reset = "\033[0m"
        print(
            f"{i:4d} {color}{r['total_return_pct']:+8.1f}%{reset} "
            f"${r['final_capital']:9,.0f} {r['total_trades']:6d} "
            f"{r['win_rate']:5.1f}% {r['profit_factor']:5.2f} "
            f"{r['max_drawdown']:5.1f}% {r['sharpe']:6.2f}"
        )
        p = r['params']
        print(
            f"     stop={p['stop_pct']:.0%} target={p['target_pct']:.0%} "
            f"threshold={p['buy_threshold']} position={p['position_pct']:.0%} "
            f"maxpos={p['max_positions']} trail={'Y' if p['trailing_stop'] else 'N'} "
            f"rsi={p.get('min_rsi', 25)}-{p.get('max_rsi', 65)} "
            f"ema={'Y' if p.get('require_ema_align', True) else 'N'}"
        )

    # Save best config
    best = results[0]
    best_params = best['params']

    output = {
        'optimization_date': datetime.now().isoformat(),
        'period': {'start': start_date, 'end': end_date},
        'initial_capital': capital,
        'goal': goal,
        'goal_reached': best['final_capital'] >= goal,
        'best_strategy': {
            'params': best_params,
            'result': {k: v for k, v in best.items() if k != 'params'},
        },
        'top_10': [
            {
                'rank': i + 1,
                'params': r['params'],
                'return_pct': r['total_return_pct'],
                'final_capital': r['final_capital'],
                'win_rate': r['win_rate'],
                'profit_factor': r['profit_factor'],
                'max_drawdown': r['max_drawdown'],
                'sharpe': r['sharpe'],
                'total_trades': r['total_trades'],
            }
            for i, r in enumerate(results[:10])
        ],
        'total_combos_tested': len(results),
    }

    outpath = os.path.join(
        os.path.dirname(__file__), '../../data/replay_results',
        f'optimizer_{start_date}_to_{end_date}.json'
    )
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n  Results saved to: {outpath}")

    if best['final_capital'] >= goal:
        print(f"\n  {'=' * 50}")
        print(f"  GOAL REACHED! Best strategy achieves ${best['final_capital']:,.0f}")
        print(f"  {'=' * 50}")
    else:
        print(f"\n  Best return: {best['total_return_pct']:+.1f}% (${best['final_capital']:,.0f})")
        print(f"  Goal of ${goal:,.0f} not yet reached. Try expanding the date range or parameter grid.")

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy Optimizer")
    parser.add_argument('--start', default='2026-01-06', help='Start date')
    parser.add_argument('--end', default='2026-02-10', help='End date')
    parser.add_argument('--capital', type=float, default=50_000, help='Starting capital')
    parser.add_argument('--quick', action='store_true', help='Quick mode (fewer combos)')

    args = parser.parse_args()
    run_optimizer(args.start, args.end, args.capital, args.quick)
