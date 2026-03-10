#!/usr/bin/env python3
"""
Interactive Replay Engine v4 — Full VolumePullbackEntrySignal
==============================================================
Uses the ACTUAL 1200-line VolumePullbackEntrySignal class with its
full scoring system (74 config parameters, candle body/wick analysis,
absorption patterns, double-bottom detection, resistance targets).

No lookahead. Day-by-day walk-forward simulation.

Usage:
    python replay_engine.py --start 2024-01-01 --end 2025-12-31 -d
    python replay_engine.py --start 2024-01-01 --end 2026-02-10 --capital 100000 -i
"""

import os, sys, json, re, argparse, time, warnings
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

import pandas as pd
import numpy as np

# Project root for imports
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _SCRIPT_DIR)
warnings.filterwarnings('ignore')

# Import the FULL signal class
from src.signals.entry.volume_pullback_entry import VolumePullbackEntrySignal, VolumePullbackConfig

# =========================================================================
# Paths
# =========================================================================
DATA_DIR = os.path.join(_SCRIPT_DIR, '../../data')
DISCORD_DATA = os.path.join(DATA_DIR, 'real_discord_messages_goku_wilson_60d.txt')
UNIVERSE_FILE = os.path.join(_PROJECT_ROOT, 'config/stock_universe.yaml')
RESULTS_DIR = os.path.join(DATA_DIR, 'replay_results')

# =========================================================================
# Strategy Parameters — matching original +110% backtest
# =========================================================================
DEFAULT_CAPITAL = 100_000
MAX_POSITION_PCT = 0.20      # 20% per position (equity / 5)
MIN_CASH_PCT = 0.02          # 2% cash buffer
MAX_POSITIONS = 5            # 5 concurrent (matches original)
STOP_LOSS_PCT = 0.025        # 2.5% hard stop cap
MAX_DRAWDOWN_CIRCUIT = 0.15  # 15% portfolio DD circuit breaker
COMMISSION_PER_TRADE = 1.00
MAX_HOLD_DAYS = 20           # Max hold before fallback exit
FALLBACK_WIN_PCT = 5.0       # +5% gain → fallback win
FALLBACK_LOSS_PCT = 8.0      # -8% loss → fallback loss
COOLDOWN_BARS = 5            # Min days between entries on same ticker

# ANSI
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"
CYAN = "\033[96m"; BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"

def clr(val, fmt="+,.0f"):
    s = f"${val:{fmt}}" if 'f' in fmt else f"{val:{fmt}}"
    return f"{GREEN}{s}{RESET}" if val >= 0 else f"{RED}{s}{RESET}"

def pct(val, fmt="+.1f"):
    s = f"{val:{fmt}}%"
    return f"{GREEN}{s}{RESET}" if val >= 0 else f"{RED}{s}{RESET}"


# =========================================================================
# Data Classes
# =========================================================================

@dataclass
class Position:
    ticker: str
    entry_price: float
    entry_date: str
    shares: int
    stop_loss: float
    target_1: float
    target_2: float
    score: float
    cost_basis: float = 0
    status: str = "open"
    exit_price: float = 0
    exit_date: str = ""
    pnl: float = 0
    pnl_pct: float = 0
    peak_price: float = 0
    entry_day_idx: int = 0
    partial_exited: bool = False
    original_shares: int = 0
    max_gain_pct: float = 0
    max_loss_pct: float = 0
    realized_partial: float = 0  # Cash from partial exit


@dataclass
class DayLog:
    date: str
    scanned: int = 0
    signals: int = 0
    orders: int = 0
    closes: int = 0
    portfolio_value: float = 0
    cash: float = 0
    daily_pnl: float = 0
    cum_pnl: float = 0


# =========================================================================
# Price Data with Disk Cache
# =========================================================================

_price_cache: Dict[str, Dict[str, Dict]] = {}
_df_cache: Dict[str, pd.DataFrame] = {}
_df_dates: Dict[str, List[str]] = {}
CACHE_DIR = os.path.join(DATA_DIR, 'price_cache')


def _cache_path(start, end):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"prices_{start}_{end}.json.gz")

def _load_disk_cache(start, end):
    import gzip
    fp = _cache_path(start, end)
    if not os.path.exists(fp): return {}
    try:
        age = (time.time() - os.path.getmtime(fp)) / 3600
        if age > 18: return {}
        with gzip.open(fp, 'rt') as f: return json.load(f)
    except: return {}

def _save_disk_cache(data, start, end):
    import gzip
    try:
        with gzip.open(_cache_path(start, end), 'wt') as f: json.dump(data, f)
    except: pass


def load_price_data(tickers, start_date, end_date):
    import yfinance as yf
    start_dt = datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=90)
    end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=5)
    dl_start, dl_end = start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d')

    if not _price_cache:
        t0 = time.time()
        disk = _load_disk_cache(dl_start, dl_end)
        if disk:
            _price_cache.update(disk)
            print(f"  Loaded {len(disk)} tickers from disk cache ({time.time()-t0:.1f}s)")

    need = [t for t in tickers if t not in _price_cache]
    if not need: return
    print(f"  Downloading {len(need)} tickers...", end="", flush=True)
    for i in range(0, len(need), 50):
        batch = need[i:i+50]
        try:
            data = yf.download(batch, start=dl_start, end=dl_end,
                               progress=False, auto_adjust=True, threads=True)
            if data.empty: continue
            for t in batch:
                _price_cache[t] = {}
                try:
                    df = data if len(batch)==1 else (
                        data.xs(t, axis=1, level=1) if t in data.columns.get_level_values(1) else None)
                    if df is None or df.empty: continue
                    for idx, row in df.iterrows():
                        d = idx.strftime('%Y-%m-%d')
                        _price_cache[t][d] = {
                            'open': float(row.get('Open',0)), 'high': float(row.get('High',0)),
                            'low': float(row.get('Low',0)), 'close': float(row.get('Close',0)),
                            'volume': int(row.get('Volume',0)),
                        }
                except: continue
        except: pass
    loaded = sum(1 for t in need if _price_cache.get(t))
    print(f" {loaded}/{len(need)}")
    _save_disk_cache(_price_cache, dl_start, dl_end)


def _build_df_cache():
    """Pre-build DataFrames for all tickers (called once)."""
    for ticker, prices in _price_cache.items():
        if not prices: continue
        dates = sorted(prices.keys())
        if len(dates) < 70: continue
        rows = [{'Open': prices[d]['open'], 'High': prices[d]['high'],
                 'Low': prices[d]['low'], 'Close': prices[d]['close'],
                 'Volume': prices[d]['volume']} for d in dates]
        df = pd.DataFrame(rows, index=pd.to_datetime(dates))
        _df_cache[ticker] = df
        _df_dates[ticker] = dates


def get_close(ticker, date_str):
    p = _price_cache.get(ticker, {}).get(date_str)
    return p['close'] if p else 0


def get_price(ticker, date_str):
    return _price_cache.get(ticker, {}).get(date_str)


# =========================================================================
# Universe Loading
# =========================================================================

def load_universe(max_tickers=500):
    try:
        import yaml
        with open(UNIVERSE_FILE, 'r') as f: data = yaml.safe_load(f)
    except: return ['AAPL','MSFT','GOOGL','AMZN','NVDA','META','TSLA']
    seen = set(); ordered = []
    def _add(syms):
        for s in syms:
            s = str(s).upper().strip('"')
            if s not in seen and s != 'ON' and len(s) <= 5:
                seen.add(s); ordered.append(s)
    for k in ['high_conviction','volatile_momentum','quick_test']: _add(data.get(k,[]))
    for t in data.get('themes',{}).values(): _add(t.get('symbols',[]))
    _add(data.get('all_symbols',[]))
    return ordered[:max_tickers]


# =========================================================================
# Signal Generator Setup — the FULL scoring engine
# =========================================================================

def build_signal_generator(require_downtrend=True) -> VolumePullbackEntrySignal:
    """Build signal generator with config matching the original backtest."""
    return VolumePullbackEntrySignal(
        config=VolumePullbackConfig(
            # Push detection
            min_green_candles=3,
            min_volume_increase_ratio=1.0,
            # Pullback
            max_pullback_bars=7,
            ema9_tolerance_pct=10.0,
            max_pullback_pct=18.0,
            max_below_ema9_pct=4.0,
            pullback_vol_ratio=0.85,
            # Trigger
            min_breakout_vol_ratio=1.1,
            # Confidence
            min_confidence=0.55,           # Lower to get more signals
            min_confidence_tradeable=0.65,  # Slightly relaxed
            min_risk_reward_tradeable=1.0,
            # Prior resistance
            require_prior_resistance=require_downtrend,
            require_downtrend_move=require_downtrend,
            min_breakout_pct=1.0,
            support_hold_tolerance_pct=8.0,
            # Targets
            target_method="auto",
            max_target1_pct=12.0,
            # Partial exits
            partial_exit_at_target1=True,
            partial_exit_size=0.5,
            move_stop_to_entry_on_partial=True,
            # Pullback flexibility
            allow_multi_pullback_rounds=True,
            max_pullback_bars_extended=12,
            allow_shallow_pullback=True,
            # Candle strength
            min_trigger_body_ratio=0.4,
            max_trigger_upper_wick_ratio=0.5,
            # Volume overrides
            allow_strong_green_override_volume=True,
            allow_strong_body_override_volume=True,
            allow_pullback_absorption_override=True,
        )
    )


def precompute_indicators(df: pd.DataFrame, sig: VolumePullbackEntrySignal) -> Dict:
    """Precompute indicators once per ticker."""
    close = df['Close']; high = df['High']; low = df['Low']; vol = df['Volume']
    return {
        'ema9': close.ewm(span=sig.config.ema_fast, adjust=False).mean(),
        'ema21': close.ewm(span=sig.config.ema_medium, adjust=False).mean(),
        'ema50': close.ewm(span=sig.config.ema_trend, adjust=False).mean(),
        'volume_ma': vol.rolling(window=20).mean(),
        'atr': sig._calculate_atr(high, low, close, 14),
    }


# =========================================================================
# Replay Engine
# =========================================================================

class ReplayEngine:
    def __init__(self, start_date, end_date, capital=DEFAULT_CAPITAL,
                 universe_size=500, max_positions=MAX_POSITIONS,
                 interactive=False, debug=False, require_downtrend=True,
                 min_score=0, trend_filter=False, min_confidence=0.55):
        self.start_date = start_date
        self.end_date = end_date
        self.capital = capital
        self.cash = capital
        self.universe_size = universe_size
        self.max_positions = max_positions
        self.interactive = interactive
        self.debug = debug
        self.require_downtrend = require_downtrend
        self.min_score = min_score
        self.trend_filter = trend_filter
        self.min_confidence = min_confidence

        self.positions: List[Position] = []
        self.closed: List[Position] = []
        self.daily_log: List[DayLog] = []
        self.peak = capital
        self.max_dd = 0
        self.day_idx = 0
        self.circuit_breaker = False
        self.circuit_breaker_day = 0
        self.last_entry: Dict[str, int] = {}  # ticker → day_idx of last entry

    def _pv(self, day):
        mkt = sum(p.shares * get_close(p.ticker, day) for p in self.positions
                  if get_close(p.ticker, day) > 0)
        return self.cash + mkt

    # ─── Process positions ──────────────────────────────────

    def _process_positions(self, day):
        events = []
        still_open = []

        for pos in self.positions:
            pr = get_price(pos.ticker, day)
            if not pr:
                still_open.append(pos); continue

            close_val, lo, hi = pr['close'], pr['low'], pr['high']
            days_held = self.day_idx - pos.entry_day_idx
            if hi > pos.peak_price: pos.peak_price = hi

            gain = (hi / pos.entry_price - 1) * 100
            loss = (1 - lo / pos.entry_price) * 100
            if gain > pos.max_gain_pct: pos.max_gain_pct = gain
            if loss > pos.max_loss_pct: pos.max_loss_pct = loss

            # Hard 2.5% stop cap
            hard_stop = pos.entry_price * (1 - STOP_LOSS_PCT)
            if pos.stop_loss < hard_stop and not pos.partial_exited:
                pos.stop_loss = hard_stop

            closed = False
            reason = ""

            # Partial exit at T1 (sell 50%)
            if not pos.partial_exited and hi >= pos.target_1 and pos.target_1 > pos.entry_price:
                half = pos.shares // 2
                if half > 0:
                    partial_cash = half * pos.target_1
                    partial_pnl = (pos.target_1 - pos.entry_price) * half
                    self.cash += partial_cash
                    pos.realized_partial += partial_pnl
                    pos.shares -= half
                    pos.partial_exited = True
                    pos.stop_loss = pos.entry_price  # Move to breakeven
                    events.append(f"  {CYAN}⇡ T1 {pos.ticker}: {half}sh @ ${pos.target_1:.2f} "
                                  f"(+${partial_pnl:.0f}) stop→BE{RESET}")

            # Stop loss
            if lo <= pos.stop_loss:
                pos.exit_price = pos.stop_loss
                reason = "T1+STOP" if pos.partial_exited else "STOP"
                pos.status = "target_1_partial+stop" if pos.partial_exited else "stop_loss"
                closed = True
            # Target 2
            elif hi >= pos.target_2 and pos.target_2 > pos.entry_price:
                pos.exit_price = pos.target_2
                reason = "TARGET2"
                pos.status = "target_2"
                closed = True
            # Fallback at max hold
            elif days_held >= MAX_HOLD_DAYS:
                pos.exit_price = close_val
                if pos.max_gain_pct >= FALLBACK_WIN_PCT:
                    reason = "FALLBACK_WIN"; pos.status = "fallback_win"
                elif pos.max_loss_pct >= FALLBACK_LOSS_PCT:
                    reason = "FALLBACK_LOSS"; pos.status = "fallback_loss"
                else:
                    reason = f"TIME({days_held}d)"; pos.status = "time_exit"
                closed = True

            if closed:
                pos.exit_date = day
                runner_pnl = (pos.exit_price - pos.entry_price) * pos.shares - COMMISSION_PER_TRADE
                pos.pnl = runner_pnl + pos.realized_partial
                pos.pnl_pct = (pos.pnl / (pos.original_shares * pos.entry_price)) * 100 if pos.entry_price > 0 else 0
                self.cash += pos.shares * pos.exit_price
                self.closed.append(pos)
                icon = f"{GREEN}✓" if pos.pnl > 0 else f"{RED}✗"
                events.append(f"  {icon} {reason} {pos.ticker}: ${pos.entry_price:.2f}→${pos.exit_price:.2f} "
                              f"P&L {clr(pos.pnl)} ({pct(pos.pnl_pct)}){RESET}")
            else:
                still_open.append(pos)

        self.positions = still_open
        return events

    # ─── Place orders ───────────────────────────────────────

    def _place_orders(self, candidates, day):
        if self.circuit_breaker:
            return []
        held = {p.ticker for p in self.positions}
        orders = []

        for ticker, entry_price, stop, t1, t2, score in candidates:
            if len(self.positions) >= self.max_positions: break
            if ticker in held: continue
            if self.last_entry.get(ticker, -COOLDOWN_BARS) + COOLDOWN_BARS > self.day_idx: continue

            pv = self._pv(day)
            available = max(0, self.cash - pv * MIN_CASH_PCT)
            pos_val = min(pv * MAX_POSITION_PCT, available)
            if pos_val < 200: continue
            shares = int(pos_val / entry_price)
            if shares <= 0: continue
            cost = shares * entry_price + COMMISSION_PER_TRADE
            if cost > available: continue

            # Enforce 2.5% stop cap
            stop = max(stop, entry_price * (1 - STOP_LOSS_PCT))

            pos = Position(
                ticker=ticker, entry_price=entry_price,
                entry_date=day, shares=shares, stop_loss=stop,
                target_1=t1, target_2=t2, score=score,
                cost_basis=cost, peak_price=entry_price,
                entry_day_idx=self.day_idx, original_shares=shares,
            )
            self.positions.append(pos)
            self.cash -= cost
            held.add(ticker)
            self.last_entry[ticker] = self.day_idx
            orders.append({'ticker': ticker, 'shares': shares, 'entry': entry_price,
                           'stop': stop, 't1': t1, 't2': t2, 'score': score, 'cost': cost})
        return orders

    # ─── Display ────────────────────────────────────────────

    def _show_header(self, day, n, total):
        pv = self._pv(day)
        pnl = pv - self.capital
        dd = (self.peak - pv) / self.peak * 100 if self.peak > 0 else 0
        print(f"\n{'═'*85}")
        print(f"  {BOLD}Day {n}/{total}  │  {day}  │  PV: ${pv:,.0f}  │  "
              f"P&L: {clr(pnl)}  │  DD: {RED if dd>5 else GREEN}{dd:.1f}%{RESET}{RESET}")
        print(f"{'═'*85}")

    def _show_positions(self, day):
        if not self.positions:
            print(f"  {DIM}No open positions{RESET}"); return
        print(f"\n  {BOLD}Positions ({len(self.positions)}/{self.max_positions}):{RESET}")
        for p in self.positions:
            now = get_close(p.ticker, day)
            if now <= 0: continue
            pnl_p = (now/p.entry_price-1)*100
            pnl_d = (now-p.entry_price)*p.shares + p.realized_partial
            days = self.day_idx - p.entry_day_idx
            c = GREEN if pnl_p >= 0 else RED
            part = "½" if p.partial_exited else " "
            print(f"  {part}{p.ticker:>5} ${p.entry_price:7.2f}→${now:7.2f} "
                  f"{c}{pnl_p:+6.1f}%{RESET} {c}${pnl_d:+7.0f}{RESET} {days:3d}d "
                  f"Stop:${p.stop_loss:.2f} T1:${p.target_1:.2f} Score:{p.score:.0f}")

    # ─── Main loop ──────────────────────────────────────────

    def run(self):
        print(f"\n{BOLD}{'═'*85}")
        print(f"  REPLAY ENGINE v4 — Full VolumePullbackEntrySignal (no lookahead)")
        print(f"  Period: {self.start_date} → {self.end_date} | Capital: ${self.capital:,.0f}")
        print(f"  Positions: {self.max_positions} @ {MAX_POSITION_PCT*100:.0f}% | "
              f"Stop: {STOP_LOSS_PCT*100:.1f}% | Downtrend: {'required' if self.require_downtrend else 'optional'}")
        print(f"{'═'*85}{RESET}\n")

        # Build signal generator
        sig_gen = build_signal_generator(self.require_downtrend)
        sig_gen.config.min_confidence = self.min_confidence
        sig_gen.config.min_confidence_tradeable = max(self.min_confidence, 0.60)
        print(f"  Signal config: {len(VolumePullbackConfig.__dataclass_fields__)} params, "
              f"min_conf={self.min_confidence:.2f}, min_score={self.min_score}, "
              f"trend_filter={self.trend_filter}")

        # Load universe
        universe = load_universe(self.universe_size)
        all_tickers = list(set(universe + ['SPY']))
        print(f"  Universe: {len(all_tickers)} tickers")

        # Load prices
        load_price_data(all_tickers, self.start_date, self.end_date)
        if not _df_cache:
            t0 = time.time()
            _build_df_cache()
            print(f"  Built DF cache: {len(_df_cache)} tickers ({time.time()-t0:.1f}s)")

        # Precompute indicators per ticker
        t0 = time.time()
        indicators: Dict[str, Dict] = {}
        index_maps: Dict[str, Dict[str, int]] = {}
        for ticker, df in _df_cache.items():
            indicators[ticker] = precompute_indicators(df, sig_gen)
            index_maps[ticker] = {d.strftime('%Y-%m-%d'): i for i, d in enumerate(df.index)}
        print(f"  Precomputed indicators: {len(indicators)} tickers ({time.time()-t0:.1f}s)")

        # Trading days
        start_dt = datetime.strptime(self.start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(self.end_date, '%Y-%m-%d')
        trading_days = []
        cur = start_dt
        while cur <= end_dt:
            if cur.weekday() < 5:
                d = cur.strftime('%Y-%m-%d')
                if get_close('SPY', d) > 0: trading_days.append(d)
            cur += timedelta(days=1)

        print(f"  Trading days: {len(trading_days)}")
        if self.interactive:
            print(f"\n  {YELLOW}[Interactive] ENTER=next | q=quit | s=skip{RESET}")

        prev_val = self.capital

        for day_num, day in enumerate(trading_days, 1):
            self.day_idx = day_num

            # 1. Process positions
            events = self._process_positions(day)

            # 2. Circuit breaker
            pv = self._pv(day)
            if pv > self.peak: self.peak = pv
            dd = (self.peak - pv) / self.peak
            if dd > self.max_dd: self.max_dd = dd
            if dd > MAX_DRAWDOWN_CIRCUIT and not self.circuit_breaker:
                self.circuit_breaker = True; self.circuit_breaker_day = self.day_idx
                events.append(f"  {RED}{BOLD}⚠ CIRCUIT BREAKER — DD {dd*100:.1f}%{RESET}")
            elif self.circuit_breaker:
                days_paused = self.day_idx - self.circuit_breaker_day
                if days_paused >= 10 or dd < MAX_DRAWDOWN_CIRCUIT * 0.7:
                    self.circuit_breaker = False; self.peak = pv
                    events.append(f"  {GREEN}✓ Circuit breaker released{RESET}")

            # 3. Scan for VP signals (using full scoring engine)
            candidates = []
            sig_count = 0
            for ticker in all_tickers:
                df = _df_cache.get(ticker)
                if df is None: continue
                idx = index_maps.get(ticker, {}).get(day)
                if idx is None: continue
                if idx < sig_gen.config.ema_trend + 20: continue

                signal = sig_gen.generate_at_index(
                    ticker, df, idx, indicators=indicators.get(ticker))

                if signal is None: continue
                if signal.confidence < sig_gen.config.min_confidence: continue

                md = signal.metadata or {}
                if not md.get('is_tradeable', False): continue

                sig_count += 1
                entry_price = float(signal.price or df['Close'].iloc[idx])
                stop = float(signal.stop_loss or md.get('stop_loss', 0))
                t1 = float(md.get('target_1_50pct', signal.take_profit or 0))
                t2 = float(md.get('target_2_full', t1))
                score = float(md.get('score', signal.confidence * 100))

                if t1 <= entry_price: continue  # Skip if T1 below entry
                if score < self.min_score: continue  # Score filter

                # Trend filter: price must be above EMA50
                if self.trend_filter:
                    ind = indicators.get(ticker)
                    if ind is not None:
                        ema50_val = ind['ema50'].iloc[idx]
                        if entry_price < ema50_val: continue

                candidates.append((ticker, entry_price, stop, t1, t2, score))

            # Sort by score (highest first)
            candidates.sort(key=lambda x: x[5], reverse=True)

            # 4. Place orders
            orders = self._place_orders(candidates, day)

            # 5. Log
            pv = self._pv(day)
            daily_pnl = pv - prev_val
            cum_pnl = pv - self.capital
            self.daily_log.append(DayLog(
                date=day, scanned=len(all_tickers), signals=sig_count,
                orders=len(orders), closes=len(events),
                portfolio_value=pv, cash=self.cash,
                daily_pnl=daily_pnl, cum_pnl=cum_pnl,
            ))

            if self.interactive or self.debug:
                self._show_header(day, day_num, len(trading_days))
                self._show_positions(day)
                if events: print(f"\n  {BOLD}Events:{RESET}"); [print(e) for e in events]
                if candidates[:5]:
                    print(f"\n  {BOLD}{GREEN}▶ VP Signals ({sig_count}):{RESET}")
                    for t, ep, st, t1, t2, sc in candidates[:5]:
                        print(f"    {GREEN}{t:>6}{RESET} ${ep:7.2f} Score:{sc:.0f} "
                              f"Stop:${st:.2f} T1:${t1:.2f} T2:${t2:.2f}")
                if orders:
                    print(f"\n  {BOLD}★ Orders ({len(orders)}):{RESET}")
                    for o in orders:
                        print(f"    BUY {o['shares']} {CYAN}{o['ticker']}{RESET} @ ${o['entry']:.2f} "
                              f"Score:{o['score']:.0f} Stop:${o['stop']:.2f} T1:${o['t1']:.2f}")
                print(f"\n  Day:{clr(daily_pnl)}  Cum:{clr(cum_pnl)} ({pct((pv/self.capital-1)*100)})")
            else:
                c = GREEN if daily_pnl >= 0 else RED
                print(f"  {day}  Sig:{sig_count:3d}  Ord:{len(orders):2d}  Open:{len(self.positions):2d}  "
                      f"${pv:9,.0f}  {c}Day:{daily_pnl:+7,.0f}{RESET}  "
                      f"Cum:{cum_pnl:+8,.0f}  {pct((pv/self.capital-1)*100)}")

            prev_val = pv
            if self.interactive:
                try:
                    inp = input(f"\n  {DIM}[ENTER|q|s]{RESET} ")
                    if inp.strip().lower() == 'q': break
                    elif inp.strip().lower() == 's': self.interactive = False
                except (EOFError, KeyboardInterrupt): break

        # Close remaining
        fd = trading_days[-1] if trading_days else self.end_date
        for p in list(self.positions):
            c = get_close(p.ticker, fd)
            if c > 0:
                p.exit_price = c; p.exit_date = fd; p.status = "end_of_period"
                runner_pnl = (c - p.entry_price) * p.shares
                p.pnl = runner_pnl + p.realized_partial
                p.pnl_pct = (p.pnl / (p.original_shares * p.entry_price)) * 100 if p.entry_price > 0 else 0
                self.cash += p.shares * c
                self.closed.append(p)
        self.positions = []

        self._print_summary()
        self._save_results()

    def _print_summary(self):
        final = self.cash
        pnl = final - self.capital
        w = [t for t in self.closed if t.pnl > 0]
        l = [t for t in self.closed if t.pnl <= 0]
        tot = len(self.closed)
        wr = len(w)/tot*100 if tot > 0 else 0
        aw = sum(t.pnl for t in w)/len(w) if w else 0
        al = sum(t.pnl for t in l)/len(l) if l else 0
        gp = sum(t.pnl for t in w)
        gl = abs(sum(t.pnl for t in l))
        pf = gp/gl if gl > 0 else float('inf')

        # Exit breakdown
        by_status = defaultdict(list)
        for t in self.closed: by_status[t.status].append(t)

        print(f"\n{BOLD}{'═'*85}")
        print(f"  RESULTS — Full VolumePullbackEntrySignal (no lookahead)")
        print(f"{'═'*85}{RESET}")
        print(f"  Period:    {self.start_date} → {self.end_date} ({len(self.daily_log)} days)")
        print(f"  Capital:   ${self.capital:,.0f} → ${final:,.0f}")
        print(f"  P&L:       {clr(pnl)} ({pct((final/self.capital-1)*100)})")
        print(f"  Trades:    {tot} ({len(w)}W / {len(l)}L)")
        print(f"  Win Rate:  {wr:.1f}%")
        print(f"  Avg Win:   ${aw:+,.0f}  |  Avg Loss: ${al:+,.0f}")
        print(f"  PF:        {pf:.2f}")
        print(f"  Max DD:    {self.max_dd*100:.1f}%")

        if by_status:
            print(f"\n  {BOLD}Exit Breakdown:{RESET}")
            for st, trades in sorted(by_status.items()):
                avg = sum(t.pnl for t in trades)/len(trades)
                print(f"    {st:25s}: {len(trades):3d} trades, avg {clr(avg)}")

        if self.closed:
            by_pnl = sorted(self.closed, key=lambda t: t.pnl, reverse=True)
            print(f"\n  {BOLD}Top Winners:{RESET}")
            for t in by_pnl[:5]:
                if t.pnl > 0:
                    print(f"    {GREEN}{t.ticker:6s}{RESET} {clr(t.pnl)} ({pct(t.pnl_pct)})  "
                          f"{t.entry_date}→{t.exit_date} [{t.status}]")
            print(f"  {BOLD}Top Losers:{RESET}")
            for t in by_pnl[-5:]:
                if t.pnl < 0:
                    print(f"    {RED}{t.ticker:6s}{RESET} {clr(t.pnl)} ({pct(t.pnl_pct)})  "
                          f"{t.entry_date}→{t.exit_date} [{t.status}]")
        print(f"{'═'*85}\n")

    def _save_results(self):
        os.makedirs(RESULTS_DIR, exist_ok=True)
        tag = f"replay_{self.start_date}_to_{self.end_date}"
        fp = os.path.join(RESULTS_DIR, f"{tag}.json")
        data = {
            'summary': {
                'start_date': self.start_date, 'end_date': self.end_date,
                'initial_capital': self.capital, 'final_capital': self.cash,
                'total_pnl': self.cash - self.capital,
                'total_return_pct': (self.cash/self.capital - 1) * 100,
                'total_trades': len(self.closed),
                'win_rate': len([t for t in self.closed if t.pnl > 0])/max(len(self.closed),1)*100,
                'max_drawdown_pct': self.max_dd * 100,
            },
            'trades': [asdict(t) for t in self.closed],
            'daily': [asdict(d) for d in self.daily_log],
        }
        with open(fp, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        print(f"  Saved: {fp}")

        # Equity chart
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt

            dates = [d.date for d in self.daily_log]
            equities = [d.portfolio_value for d in self.daily_log]
            if dates and equities:
                fig, ax = plt.subplots(figsize=(12, 5))
                ax.plot(pd.to_datetime(dates), equities, color='blue', linewidth=1.5)
                ax.set_title(f"Portfolio Equity ({self.start_date} – {self.end_date})")
                ax.set_xlabel("Date"); ax.set_ylabel("Equity ($)")
                ax.grid(True, alpha=0.3); fig.tight_layout()
                png = os.path.join(RESULTS_DIR, f"{tag}_equity.png")
                fig.savefig(png, dpi=100); plt.close(fig)
                print(f"  Chart: {png}")
                # CSV
                csv = os.path.join(RESULTS_DIR, f"{tag}_equity.csv")
                eq_df = pd.DataFrame({'date': dates, 'equity': equities})
                eq_df['peak'] = eq_df['equity'].cummax()
                eq_df['drawdown'] = (eq_df['equity'] - eq_df['peak']) / eq_df['peak'] * 100
                eq_df.to_csv(csv, index=False)
                print(f"  CSV: {csv}")
        except Exception as e:
            print(f"  Chart error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Replay Engine v4")
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', default=None)
    parser.add_argument('--capital', type=float, default=DEFAULT_CAPITAL)
    parser.add_argument('--universe', type=int, default=500)
    parser.add_argument('--max-positions', type=int, default=MAX_POSITIONS)
    parser.add_argument('--no-downtrend', action='store_true',
                        help='Do NOT require prior downtrend (allows uptrend pullbacks)')
    parser.add_argument('--min-score', type=float, default=0,
                        help='Min VP score to trade (0=all, 60=quality filter)')
    parser.add_argument('--min-confidence', type=float, default=0.55,
                        help='Min signal confidence (0.55=default, 0.65=strict)')
    parser.add_argument('--trend-filter', action='store_true',
                        help='Only trade above EMA50 (uptrend filter)')
    parser.add_argument('-i', '--interactive', action='store_true')
    parser.add_argument('-d', '--debug', action='store_true')
    args = parser.parse_args()
    ReplayEngine(
        args.start, args.end or args.start, args.capital,
        args.universe, args.max_positions, args.interactive, args.debug,
        require_downtrend=not args.no_downtrend,
        min_score=args.min_score, trend_filter=args.trend_filter,
        min_confidence=args.min_confidence,
    ).run()


if __name__ == "__main__":
    main()
