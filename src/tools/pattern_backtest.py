#!/usr/bin/env python3
"""
HISTORICAL PATTERN BACKTEST (src implementation)
================================================
End-to-end workflow for testing the Volume-Confirmed EMA9 Pullback pattern.

This module uses the SAME signal logic as the scanner:
- VolumePullbackEntrySignal (src/signals/entry/volume_pullback_entry.py)
- DataManager cache (src/data_manager.py)

Outputs:
- CSV of historical pattern instances with outcomes
- Phase scan report (optional)
- Rendered example charts (optional)
"""

from __future__ import annotations

import os
import re
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings

import pandas as pd
import numpy as np
import yaml
import mplfinance as mpf
import yfinance as yf

from ..data_manager import DataManager
from ..signals.entry.volume_pullback_entry import VolumePullbackEntrySignal, VolumePullbackConfig

warnings.filterwarnings("ignore")

DATA_MANAGER = DataManager()

ROOT = Path(__file__).resolve().parents[2]
EARNINGS_CACHE: Dict[str, Dict] = {}
EARNINGS_CACHE_LOCK = threading.Lock()
EARNINGS_CACHE_PATH = ROOT / "data" / "earnings_cache.json"
EARNINGS_MANUAL: Dict[str, List[str]] = {}
EARNINGS_MANUAL_PATH = ROOT / "data" / "earnings_manual.json"


@dataclass
class PatternInstance:
    symbol: str
    entry_date: str
    entry_price: float
    entry1_date: str
    entry1_price: float
    entry2_date: str
    entry2_price: float
    risk_per_trade_pct: float
    r_multiple: float
    score: float
    size_factor: float
    weighted_outcome_pct: float
    outcome: str
    outcome_date: str
    days_to_outcome: int
    max_gain_pct: float
    max_loss_pct: float
    hit_target: bool
    confidence: float
    is_tradeable: bool
    setup_grade: str
    exit_price: float
    exit_pct: float
    exit_reason: str
    
    # Pattern structure
    prior_resistance_date: str
    prior_resistance_price: float
    breakout_pct: float
    was_downtrend: bool
    push_start_date: str
    push_end_date: str
    push_high: float
    push_green_candles: int
    pullback_start_date: str
    pullback_end_date: str
    pullback_low: float
    pullback_pct: float
    pullback_bars: int
    dist_to_ema9_pct: float
    volume_divergence_ratio: float
    breakout_volume_ratio: float
    higher_low: bool
    pullback_held_support: bool
    
    # Trade levels
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward: float
    
    # Earnings filter
    earnings_blocker: bool = False
    days_to_earnings: int = -1
    blockers: str = ""


def load_universe(config_path: Optional[Path] = None) -> Dict:
    if config_path is None:
        config_path = ROOT / "config" / "stock_universe.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def clean_symbols(symbols: List[str]) -> List[str]:
    pattern = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")
    cleaned = []
    for s in symbols:
        if not isinstance(s, str):
            continue
        sym = s.strip().upper()
        if sym.startswith("$"):
            continue
        if not pattern.match(sym):
            continue
        cleaned.append(sym)
    return sorted(set(cleaned))


def load_earnings_cache(cache_path: Path) -> Dict[str, Dict]:
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def load_earnings_manual(manual_path: Path) -> Dict[str, List[str]]:
    if not manual_path.exists():
        return {}
    try:
        with open(manual_path, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {k: list(v) for k, v in data.items()}
    except Exception:
        return {}
    return {}


def save_earnings_cache(cache_path: Path):
    if not EARNINGS_CACHE:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(cache_path, "w") as f:
            json.dump(EARNINGS_CACHE, f, indent=2)
    except Exception:
        pass


def get_earnings_dates(symbol: str, stale_days: int = 30) -> List[datetime.date]:
    manual_dates = EARNINGS_MANUAL.get(symbol, [])
    manual_parsed = []
    for d in manual_dates:
        try:
            manual_parsed.append(datetime.fromisoformat(d).date())
        except Exception:
            continue

    now = datetime.utcnow()
    with EARNINGS_CACHE_LOCK:
        entry = EARNINGS_CACHE.get(symbol)
    if entry:
        fetched_at = entry.get("fetched_at")
        dates = entry.get("dates", [])
        try:
            fetched_dt = datetime.fromisoformat(fetched_at)
            if (now - fetched_dt) <= timedelta(days=stale_days):
                cached = [datetime.fromisoformat(d).date() for d in dates]
                return sorted({*cached, *manual_parsed})
        except Exception:
            pass
    
    # Fetch from yfinance
    dates: List[str] = []
    try:
        df = yf.Ticker(symbol).get_earnings_dates(limit=24)
        if df is not None and not df.empty:
            dates = [d.date().isoformat() for d in df.index]
    except Exception:
        dates = []
    
    with EARNINGS_CACHE_LOCK:
        EARNINGS_CACHE[symbol] = {"fetched_at": now.isoformat(), "dates": dates}
    fetched = [datetime.fromisoformat(d).date() for d in dates]
    return sorted({*fetched, *manual_parsed})


def is_near_earnings(entry_date: datetime, earnings_dates: List[datetime.date], window_days: int) -> Tuple[bool, int]:
    if not earnings_dates:
        return False, -1
    entry_day = entry_date.date()
    diffs = [abs((entry_day - d).days) for d in earnings_dates]
    min_diff = min(diffs) if diffs else -1
    return (min_diff >= 0 and min_diff <= window_days), min_diff


def evaluate_outcome(
    data: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    stop_loss: float,
    target_1: float,
    target_2: float,
    max_hold_bars: int = 20,
    fallback_win_gain_pct: float = 5.0,
    fallback_loss_pct: float = 8.0,
    exit_on_ema9_break: bool = False,
    min_gain_before_ema9_exit: float = 8.0,
    partial_exit_at_target1: bool = True,
    partial_exit_size: float = 0.5,
    move_stop_to_entry_on_partial: bool = True,
) -> Tuple[str, str, float, float, int, bool, float, float, str]:
    """
    Evaluate trade outcome after entry.
    
    Returns:
        outcome, outcome_date, max_gain, max_loss, days_to_outcome, hit_target
    """
    high = data["High"] if "High" in data.columns else data["high"]
    low = data["Low"] if "Low" in data.columns else data["low"]
    close = data["Close"] if "Close" in data.columns else data["close"]
    ema9 = close.ewm(span=9, adjust=False).mean()
    
    outcome = "PENDING"
    outcome_date = ""
    max_gain = 0.0
    max_loss = 0.0
    hit_target = False
    days_to = 0
    exit_price = entry_price
    exit_pct = 0.0
    exit_reason = "pending"
    
    target1_valid = target_1 > entry_price
    target2_valid = target_2 > entry_price
    partial_taken = False
    partial_size = max(0.0, min(float(partial_exit_size), 1.0)) if partial_exit_at_target1 else 0.0
    runner_size = 1.0 - partial_size
    partial_exit_pct = 0.0
    stop_for_runner = stop_loss
    for future_idx in range(entry_idx + 1, min(entry_idx + max_hold_bars + 1, len(data))):
        future_high = float(high.iloc[future_idx])
        future_low = float(low.iloc[future_idx])
        future_close = float(close.iloc[future_idx])
        future_ema9 = float(ema9.iloc[future_idx])
        
        gain = (future_high - entry_price) / entry_price * 100
        loss = (entry_price - future_low) / entry_price * 100
        
        max_gain = max(max_gain, gain)
        max_loss = max(max_loss, loss)
        days_to = future_idx - entry_idx
        
        # Partial exit at Target1
        if partial_exit_at_target1 and target1_valid and not partial_taken and future_high >= target_1:
            partial_taken = True
            hit_target = True
            partial_exit_pct = ((target_1 - entry_price) / entry_price * 100) * partial_size
            if move_stop_to_entry_on_partial:
                stop_for_runner = max(stop_for_runner, entry_price)
            if runner_size == 0:
                outcome = "SUCCESS"
                outcome_date = data.index[future_idx].strftime("%Y-%m-%d")
                exit_pct = partial_exit_pct
                exit_price = entry_price * (1 + exit_pct / 100)
                exit_reason = "target_1_full"
                break
        
        # Runner exit logic
        if runner_size > 0:
            if future_low <= stop_for_runner:
                runner_exit_pct = ((stop_for_runner - entry_price) / entry_price * 100) * runner_size
                exit_pct = partial_exit_pct + runner_exit_pct
                exit_price = entry_price * (1 + exit_pct / 100)
                outcome = "SUCCESS" if partial_taken else "FAILURE"
                outcome_date = data.index[future_idx].strftime("%Y-%m-%d")
                exit_reason = "target_1_partial+stop" if partial_taken else "stop_loss"
                break
            if (
                exit_on_ema9_break
                and gain >= min_gain_before_ema9_exit
                and future_close < future_ema9
            ):
                runner_exit_pct = ((future_close - entry_price) / entry_price * 100) * runner_size
                exit_pct = partial_exit_pct + runner_exit_pct
                exit_price = entry_price * (1 + exit_pct / 100)
                outcome = "SUCCESS" if (partial_taken or gain > 0) else "FAILURE"
                outcome_date = data.index[future_idx].strftime("%Y-%m-%d")
                exit_reason = "target_1_partial+ema9" if partial_taken else "ema9_break"
                break
            if target2_valid and future_high >= target_2:
                runner_exit_pct = ((target_2 - entry_price) / entry_price * 100) * runner_size
                exit_pct = partial_exit_pct + runner_exit_pct
                exit_price = entry_price * (1 + exit_pct / 100)
                outcome = "SUCCESS"
                outcome_date = data.index[future_idx].strftime("%Y-%m-%d")
                exit_reason = "target_2"
                break
    
    if outcome == "PENDING":
        # Fallback if not hit target/stop
        if max_gain > fallback_win_gain_pct:
            outcome = "SUCCESS"
            exit_pct = partial_exit_pct + (max_gain * runner_size)
            exit_price = entry_price * (1 + exit_pct / 100)
            exit_reason = "fallback_win"
        elif max_loss > fallback_loss_pct:
            outcome = "FAILURE"
            exit_pct = partial_exit_pct - (max_loss * runner_size)
            exit_price = entry_price * (1 + exit_pct / 100)
            exit_reason = "fallback_loss"
        if outcome_date == "" and entry_idx + max_hold_bars < len(data):
            outcome_date = data.index[min(entry_idx + max_hold_bars, len(data) - 1)].strftime("%Y-%m-%d")
    
    return outcome, outcome_date, max_gain, max_loss, days_to, hit_target, exit_price, exit_pct, exit_reason


def scan_historical_patterns(
    symbol: str,
    data: pd.DataFrame,
    signal_gen: VolumePullbackEntrySignal,
    min_confidence: float = 0.70,
    cooldown_bars: int = 5,
    max_hold_bars: int = 20,
    include_wait: bool = False,
    entry1_weight: float = 0.0,
    fallback_win_gain_pct: float = 5.0,
    fallback_loss_pct: float = 8.0,
    use_score_sizing: bool = False,
    score_full: float = 85.0,
    score_half: float = 70.0,
    score_quarter: float = 55.0,
    score_min: float = 55.0,
    earnings_dates: Optional[List[datetime.date]] = None,
    earnings_window_days: int = 0,
    exit_on_ema9_break: bool = False,
    min_gain_before_ema9_exit: float = 8.0,
) -> List[PatternInstance]:
    """
    Scan historical data for Volume Pullback patterns using the signal class.
    """
    if len(data) < 80:
        return []
    
    patterns: List[PatternInstance] = []
    last_signal_idx = -cooldown_bars
    
    # Precompute indicators once for speed
    close = data["Close"]
    high = data["High"]
    low = data["Low"]
    volume = data["Volume"]
    ema9 = close.ewm(span=signal_gen.config.ema_fast, adjust=False).mean()
    ema21 = close.ewm(span=signal_gen.config.ema_medium, adjust=False).mean()
    ema50 = close.ewm(span=signal_gen.config.ema_trend, adjust=False).mean()
    volume_ma = volume.rolling(window=20).mean()
    atr = signal_gen._calculate_atr(high, low, close, 14)
    indicators = {
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "volume_ma": volume_ma,
        "atr": atr,
    }
    
    for idx in range(signal_gen.config.ema_trend + 20, len(data) - max_hold_bars - 1):
        if idx - last_signal_idx < cooldown_bars:
            continue
        
        signal = signal_gen.generate_at_index(symbol, data, idx, indicators=indicators)
        
        if signal is None or signal.confidence < min_confidence:
            continue
        
        metadata = signal.metadata or {}
        is_tradeable = bool(metadata.get("is_tradeable", False))
        
        score = float(metadata.get("score", signal.confidence * 100))
        size_factor = 0.0
        if use_score_sizing:
            if score >= score_full:
                size_factor = 1.0
            elif score >= score_half:
                size_factor = 0.5
            elif score >= score_quarter:
                size_factor = 0.25
            else:
                size_factor = 0.0
            
            if score < score_min:
                continue
        else:
            if not include_wait and not is_tradeable:
                continue
        
        entry2_price = float(signal.price or data["Close"].iloc[idx])
        
        entry1_idx = metadata.get("entry1_idx")
        if entry1_idx is None:
            entry1_idx = idx
        entry1_price = float(metadata.get("entry1_price", entry2_price))
        entry2_price = float(metadata.get("trigger_price", entry2_price))
        
        if entry1_weight > 0:
            entry_price = (entry1_price * entry1_weight) + (entry2_price * (1 - entry1_weight))
        else:
            entry_price = entry2_price
        stop_loss = float(signal.stop_loss or metadata.get("stop_loss", 0.0))
        target_1 = float(metadata.get("target_1_50pct", signal.take_profit or 0.0))
        target_2 = float(metadata.get("target_2_full", target_1))
        risk_reward = float(metadata.get("risk_reward", 0.0))
        
        outcome, outcome_date, max_gain, max_loss, days_to, hit_target, exit_price, exit_pct, exit_reason = evaluate_outcome(
            data=data,
            entry_idx=idx,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            max_hold_bars=max_hold_bars,
            fallback_win_gain_pct=fallback_win_gain_pct,
            fallback_loss_pct=fallback_loss_pct,
            exit_on_ema9_break=exit_on_ema9_break,
            min_gain_before_ema9_exit=min_gain_before_ema9_exit,
            partial_exit_at_target1=signal_gen.config.partial_exit_at_target1,
            partial_exit_size=signal_gen.config.partial_exit_size,
            move_stop_to_entry_on_partial=signal_gen.config.move_stop_to_entry_on_partial,
        )
        
        # Risk and R-multiple
        risk_per_trade_pct = (entry_price - stop_loss) / entry_price * 100 if entry_price > 0 else 0.0
        if risk_per_trade_pct > 0:
            r_multiple = exit_pct / risk_per_trade_pct
        else:
            r_multiple = 0.0
        
        weighted_outcome = exit_pct * (size_factor if use_score_sizing else 1.0)
        
        # Resolve dates from indices
        prior_idx = metadata.get("prior_resistance_idx")
        push_start_idx = int(metadata.get("push_start_idx", idx))
        push_end_idx = int(metadata.get("push_end_idx", idx))
        pullback_start_idx = int(metadata.get("pullback_start_idx", idx))
        pullback_end_idx = int(metadata.get("pullback_end_idx", idx))
        
        prior_date = (
            data.index[prior_idx].strftime("%Y-%m-%d")
            if prior_idx is not None and hasattr(data.index[prior_idx], "strftime")
            else ""
        )
        
        # Earnings filter
        earnings_blocker = False
        days_to_earnings = -1
        blockers = list(metadata.get("blockers", []))
        if earnings_window_days > 0 and earnings_dates is not None:
            earnings_blocker, days_to_earnings = is_near_earnings(
                entry_date=data.index[idx],
                earnings_dates=earnings_dates,
                window_days=earnings_window_days,
            )
            if earnings_blocker:
                blockers.append(f"Earnings window ±{earnings_window_days}d (min Δ={days_to_earnings}d)")
                if not include_wait:
                    continue
                is_tradeable = False
        
        patterns.append(
            PatternInstance(
                symbol=symbol,
                entry_date=data.index[idx].strftime("%Y-%m-%d"),
                entry_price=entry_price,
                entry1_date=data.index[int(entry1_idx)].strftime("%Y-%m-%d") if entry1_idx is not None else "",
                entry1_price=entry1_price,
                entry2_date=data.index[idx].strftime("%Y-%m-%d"),
                entry2_price=entry2_price,
                risk_per_trade_pct=float(risk_per_trade_pct),
                r_multiple=float(r_multiple),
                score=float(score),
                size_factor=float(size_factor),
                weighted_outcome_pct=float(weighted_outcome),
                outcome=outcome,
                outcome_date=outcome_date,
                days_to_outcome=int(days_to),
                max_gain_pct=float(max_gain),
                max_loss_pct=float(max_loss),
                hit_target=bool(hit_target),
                confidence=float(signal.confidence),
                is_tradeable=bool(is_tradeable),
                setup_grade=str(metadata.get("setup_grade", "")),
                exit_price=float(exit_price),
                exit_pct=float(exit_pct),
                exit_reason=str(exit_reason),
                prior_resistance_date=prior_date,
                prior_resistance_price=float(metadata.get("prior_resistance", 0.0) or 0.0),
                breakout_pct=float(metadata.get("breakout_pct", 0.0)),
                was_downtrend=bool(metadata.get("was_downtrend", False)),
                push_start_date=data.index[push_start_idx].strftime("%Y-%m-%d"),
                push_end_date=data.index[push_end_idx].strftime("%Y-%m-%d"),
                push_high=float(metadata.get("push_high", 0.0)),
                push_green_candles=int(metadata.get("push_green_candles", 0)),
                pullback_start_date=data.index[pullback_start_idx].strftime("%Y-%m-%d"),
                pullback_end_date=data.index[pullback_end_idx].strftime("%Y-%m-%d"),
                pullback_low=float(metadata.get("pullback_low", 0.0)),
                pullback_pct=float(metadata.get("pullback_pct", 0.0)),
                pullback_bars=int(metadata.get("pullback_bars", 0)),
                dist_to_ema9_pct=float(metadata.get("dist_to_ema9_pct", 0.0)),
                volume_divergence_ratio=float(metadata.get("volume_divergence_ratio", 0.0)),
                breakout_volume_ratio=float(metadata.get("breakout_volume_ratio", 0.0)),
                higher_low=bool(metadata.get("higher_low", False)),
                pullback_held_support=bool(metadata.get("pullback_held_support", False)),
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                risk_reward=risk_reward,
                earnings_blocker=bool(earnings_blocker),
                days_to_earnings=int(days_to_earnings),
                blockers="; ".join(blockers),
            )
        )
        
        last_signal_idx = idx
    
    return patterns


def classify_current_phase(symbol: str, data: pd.DataFrame, signal_gen: VolumePullbackEntrySignal) -> Dict:
    """
    Classify the current phase for a symbol using the same signal logic.
    """
    if data is None or data.empty or len(data) < 60:
        return {"symbol": symbol, "phase": "NONE"}
    
    close = data["Close"]
    open_price = data["Open"]
    high = data["High"]
    low = data["Low"]
    volume = data["Volume"]
    
    ema9 = close.ewm(span=signal_gen.config.ema_fast, adjust=False).mean()
    
    current_idx = len(data) - 1
    current_close = close.iloc[current_idx]
    current_open = open_price.iloc[current_idx]
    current_vol = volume.iloc[current_idx]
    is_green = current_close > current_open
    
    push_info = signal_gen._find_push_phase(close, open_price, volume, high, low)
    if not push_info:
        return {"symbol": symbol, "phase": "NONE"}
    
    push_start_idx = push_info["start_idx"]
    push_end_idx = push_info["end_idx"]
    push_high = push_info["push_high"]
    
    prior_info = signal_gen._find_prior_resistance(high, close, push_start_idx)
    if signal_gen.config.require_prior_resistance and not prior_info:
        return {"symbol": symbol, "phase": "NONE"}
    
    if prior_info:
        prior_resistance, _ = prior_info
        broke_resistance = push_high > prior_resistance
        was_downtrend = close.iloc[push_start_idx] < prior_resistance * 0.98
        if signal_gen.config.require_prior_resistance and (not broke_resistance or not was_downtrend):
            return {"symbol": symbol, "phase": "NONE"}
    
    pullback_info = signal_gen._check_pullback_phase(
        close,
        high,
        low,
        volume,
        ema9,
        push_end_idx,
        push_high,
        push_info["push_avg_volume"],
    )
    
    if not pullback_info:
        return {
            "symbol": symbol,
            "phase": "PHASE_1_BREAKOUT",
            "push_start_date": data.index[push_start_idx].strftime("%Y-%m-%d"),
            "push_end_date": data.index[push_end_idx].strftime("%Y-%m-%d"),
            "push_high": float(push_high),
        }
    
    pullback_start_idx = pullback_info["pullback_start_idx"]
    pullback_end_idx = pullback_info["pullback_end_idx"]
    pullback_low = pullback_info["pullback_low"]
    pullback_pct = pullback_info["pullback_pct"]
    dist_to_ema9 = pullback_info["dist_to_ema9_pct"]
    
    trig_vol_ratio = float(current_vol / pullback_info["pullback_avg_volume"]) if pullback_info["pullback_avg_volume"] > 0 else 0.0
    
    if is_green and trig_vol_ratio >= signal_gen.config.min_breakout_vol_ratio:
        phase = "PHASE_4_COMPLETE" if current_close >= push_high else "PHASE_3_TRIGGER"
    else:
        phase = "PHASE_2_PULLBACK"
    
    return {
        "symbol": symbol,
        "phase": phase,
        "push_start_date": data.index[push_start_idx].strftime("%Y-%m-%d"),
        "push_end_date": data.index[push_end_idx].strftime("%Y-%m-%d"),
        "push_high": float(push_high),
        "pullback_start_date": data.index[pullback_start_idx].strftime("%Y-%m-%d"),
        "pullback_end_date": data.index[pullback_end_idx].strftime("%Y-%m-%d"),
        "pullback_low": float(pullback_low),
        "pullback_pct": float(pullback_pct),
        "dist_to_ema9": float(dist_to_ema9),
        "trigger_is_green_today": bool(is_green),
        "trigger_volume_ratio_today": float(trig_vol_ratio),
    }


def render_examples(
    patterns: List[PatternInstance],
    period: str,
    output_dir: str,
    max_success: int = 2,
    max_failure: int = 2,
    lookback_bars: int = 60,
    forward_bars: int = 30,
    examples: Optional[List[PatternInstance]] = None,
):
    """
    Render best success and failure examples as candlestick charts.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    if examples is None:
        successes = [p for p in patterns if p.outcome == "SUCCESS"]
        failures = [p for p in patterns if p.outcome == "FAILURE"]
        
        successes.sort(key=lambda p: p.max_gain_pct, reverse=True)
        failures.sort(key=lambda p: p.max_loss_pct, reverse=True)
        
        examples = successes[:max_success] + failures[:max_failure]
    
    for p in examples:
        data = DATA_MANAGER.get_daily_data(p.symbol, period=period)
        if data is None or data.empty:
            continue
        
        entry_date = pd.to_datetime(p.entry_date)
        idx = data.index.get_indexer([entry_date], method="nearest")[0]
        start_idx = max(0, idx - lookback_bars)
        end_idx = min(len(data) - 1, idx + forward_bars)
        window = data.iloc[start_idx : end_idx + 1].copy()
        
        # Add EMAs
        window["EMA9"] = window["Close"].ewm(span=9, adjust=False).mean()
        window["EMA21"] = window["Close"].ewm(span=21, adjust=False).mean()
        
        add_plots = [
            mpf.make_addplot(window["EMA9"], color="blue", width=1),
            mpf.make_addplot(window["EMA21"], color="purple", width=1),
        ]
        
        entry2_date = pd.to_datetime(p.entry2_date) if p.entry2_date else None
        outcome_date = pd.to_datetime(p.outcome_date) if p.outcome_date else None

        
        title = f"{p.symbol} {p.entry_date} {p.outcome} (R:R {p.risk_reward:.1f}x)"
        filename = f"{p.symbol}_{p.entry_date}_{p.outcome}.png"
        filepath = os.path.join(output_dir, filename)
        
        hlines = [p.stop_loss, p.target_1, p.target_2]
        hline_colors = ["red", "green", "green"]
        if p.prior_resistance_price and p.prior_resistance_price > 0:
            hlines.append(p.prior_resistance_price)
            hline_colors.append("blue")
        
        fig, axes = mpf.plot(
            window,
            type="candle",
            volume=True,
            style="yahoo",
            title=title,
            addplot=add_plots,
            hlines=dict(
                hlines=hlines,
                colors=hline_colors,
                linestyle="dashed",
            ),
            returnfig=True,
        )
        
        ax = axes[0]
        # Highlight entry/exit candles with custom colors (no markers)
        def overlay_candle(ax, date, color):
            if date is None or date not in window.index:
                return
            overlay = window[["Open", "High", "Low", "Close"]].copy()
            overlay.loc[:] = np.nan
            overlay.loc[date, ["Open", "High", "Low", "Close"]] = window.loc[
                date, ["Open", "High", "Low", "Close"]
            ]
            mc = mpf.make_marketcolors(up=color, down=color, edge=color, wick=color)
            style = mpf.make_mpf_style(marketcolors=mc)
            mpf.plot(overlay, ax=ax, volume=False, type="candle", style=style, warn_too_much_data=10000)
        
        if entry2_date is not None:
            overlay_candle(ax, entry2_date, "darkorange")
        if outcome_date is not None:
            overlay_candle(ax, outcome_date, "black")
        
        label_idx = max(0, len(window.index) - 5)
        x_label = window.index[label_idx]
        ax.text(x_label, p.stop_loss, f"Stop {p.stop_loss:.2f}", color="red", fontsize=8, va="center")
        ax.text(x_label, p.target_1, f"T1 {p.target_1:.2f}", color="green", fontsize=8, va="center")
        ax.text(x_label, p.target_2, f"T2 {p.target_2:.2f}", color="green", fontsize=8, va="center")
        if p.prior_resistance_price and p.prior_resistance_price > 0:
            ax.text(
                x_label,
                p.prior_resistance_price,
                f"Prior R {p.prior_resistance_price:.2f}",
                color="blue",
                fontsize=8,
                va="center",
            )
        ax.text(x_label, window["EMA9"].iloc[-1], "EMA9", color="blue", fontsize=8, va="center")
        ax.text(x_label, window["EMA21"].iloc[-1], "EMA21", color="purple", fontsize=8, va="center")
        if entry2_date is not None:
            ax.text(entry2_date, p.entry2_price, "Entry", color="green", fontsize=8, va="bottom")
        if outcome_date is not None:
            ax.text(outcome_date, p.exit_price, f"Exit ({p.exit_reason})", color="black", fontsize=8, va="top")
        
        fig.savefig(filepath)


def scan_symbol(
    symbol: str,
    period: str,
    signal_gen: VolumePullbackEntrySignal,
    min_confidence: float,
    cooldown_bars: int,
    max_hold_bars: int,
    include_wait: bool,
    entry1_weight: float,
    fallback_win_gain_pct: float,
    fallback_loss_pct: float,
    use_score_sizing: bool,
    score_full: float,
    score_half: float,
    score_quarter: float,
    score_min: float,
    earnings_window_days: int,
    earnings_stale_days: int,
    exit_on_ema9_break: bool,
    min_gain_before_ema9_exit: float,
    min_avg_volume: float,
) -> Tuple[str, List[PatternInstance]]:
    try:
        data = DATA_MANAGER.get_daily_data(symbol, period=period)
        if data is None or data.empty or len(data) < 80:
            return symbol, []
        avg_volume = float(data["Volume"].tail(60).mean())
        if avg_volume < min_avg_volume:
            return symbol, []
        earnings_dates = None
        if earnings_window_days > 0:
            earnings_dates = get_earnings_dates(symbol, stale_days=earnings_stale_days)
        return symbol, scan_historical_patterns(
            symbol,
            data,
            signal_gen,
            min_confidence=min_confidence,
            cooldown_bars=cooldown_bars,
            max_hold_bars=max_hold_bars,
            include_wait=include_wait,
            entry1_weight=entry1_weight,
            fallback_win_gain_pct=fallback_win_gain_pct,
            fallback_loss_pct=fallback_loss_pct,
            use_score_sizing=use_score_sizing,
            score_full=score_full,
            score_half=score_half,
            score_quarter=score_quarter,
            score_min=score_min,
            earnings_dates=earnings_dates,
            earnings_window_days=earnings_window_days,
            exit_on_ema9_break=exit_on_ema9_break,
            min_gain_before_ema9_exit=min_gain_before_ema9_exit,
        )
    except Exception:
        return symbol, []


def main(argv: Optional[List[str]] = None):
    import argparse

    parser = argparse.ArgumentParser(description="Volume Pullback Pattern Backtest")
    parser.add_argument("--symbols", nargs="*", help="Specific symbols to scan")
    parser.add_argument("--theme", type=str, help="Theme from universe")
    parser.add_argument("--all", action="store_true", help="Scan full universe")
    parser.add_argument(
        "--universe-file",
        type=str,
        default=str(ROOT / "config" / "stock_universe.yaml"),
        help="Universe YAML file (default: config/stock_universe.yaml)",
    )
    parser.add_argument("--period", type=str, default="2y", help="Data period to scan (default: 2y)")
    parser.add_argument("--preload", action="store_true", help="Warm SQLite cache first for all symbols")
    parser.add_argument("--export", type=str, help="Export all found patterns to CSV")
    parser.add_argument("--export-phases", type=str, help="Export current phase per symbol to CSV")
    parser.add_argument(
        "--include-none",
        action="store_true",
        help="Include phase=NONE rows in phase export (default: exclude)",
    )
    parser.add_argument(
        "--include-wait",
        action="store_true",
        help="Include non-tradeable (WAIT) patterns in backtest output",
    )
    parser.add_argument(
        "--min-days-to-outcome",
        type=int,
        default=2,
        help="Filter out patterns with days_to_outcome < N (default: 2)",
    )
    parser.add_argument(
        "--fallback-win-gain",
        type=float,
        default=5.0,
        help="Fallback SUCCESS if max_gain > X%% (default: 5.0)",
    )
    parser.add_argument(
        "--fallback-loss",
        type=float,
        default=8.0,
        help="Fallback FAILURE if max_loss > X%% (default: 8.0)",
    )
    parser.add_argument(
        "--exit-on-ema9-break",
        action="store_true",
        help="Exit early if price closes below EMA9 after sufficient gain",
    )
    parser.add_argument(
        "--min-gain-before-ema9-exit",
        type=float,
        default=8.0,
        help="Min gain %% before EMA9 break exit (default: 8.0)",
    )
    parser.add_argument(
        "--entry1-weight",
        type=float,
        default=0.0,
        help="Weight for Entry1 (0.0-0.3). Entry2 weight = 1 - entry1_weight.",
    )
    parser.add_argument(
        "--use-score-sizing",
        action="store_true",
        help="Use score-based position sizing instead of binary tradeability",
    )
    parser.add_argument("--score-full", type=float, default=85.0, help="Score for full size (default: 85)")
    parser.add_argument("--score-half", type=float, default=70.0, help="Score for half size (default: 70)")
    parser.add_argument("--score-quarter", type=float, default=55.0, help="Score for quarter size (default: 55)")
    parser.add_argument("--score-min", type=float, default=55.0, help="Minimum score to include (default: 55)")
    parser.add_argument(
        "--earnings-window-days",
        type=int,
        default=5,
        help="Block entries within ±N days of earnings (default: 5, set 0 to disable)",
    )
    parser.add_argument(
        "--earnings-stale-days",
        type=int,
        default=30,
        help="Days before refreshing earnings cache (default: 30)",
    )
    parser.add_argument("--min-confidence", type=float, default=0.70, help="Minimum signal confidence (default: 0.70)")
    parser.add_argument("--min-avg-volume", type=float, default=1_000_000, help="Min avg volume (default: 1,000,000)")
    parser.add_argument("--cooldown-bars", type=int, default=5, help="Min bars between signals (default: 5)")
    parser.add_argument("--max-hold-bars", type=int, default=20, help="Max holding period in bars (default: 20)")
    parser.add_argument("--max-workers", type=int, default=10, help="Parallel workers (default: 10)")
    parser.add_argument(
        "--render-examples",
        type=str,
        help="Output directory for example charts (PNG)",
    )
    parser.add_argument("--examples-success", type=int, default=2, help="Number of success examples to render")
    parser.add_argument("--examples-failure", type=int, default=2, help="Number of failure examples to render")
    parser.add_argument(
        "--render-recent",
        type=int,
        default=0,
        help="Render most recent N patterns instead of best/worst examples",
    )
    parser.add_argument(
        "--export-performance",
        type=str,
        help="Export simplified performance log CSV",
    )
    parser.add_argument(
        "--target-method",
        type=str,
        default="resistance",
        choices=["resistance", "measured_move", "fib", "auto"],
        help="Target method: resistance | measured_move | fib | auto",
    )

    args = parser.parse_args(argv)

    universe = load_universe(Path(args.universe_file))
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    elif args.theme:
        if args.theme in universe["themes"]:
            symbols = universe["themes"][args.theme]["symbols"]
        else:
            print(f"❌ Theme '{args.theme}' not found!")
            return
    elif args.all:
        symbols = universe.get("all_symbols", [])
    else:
        symbols = list(
            set(
                universe.get("volatile_momentum", [])
                + universe["themes"].get("ultra_volatile", {}).get("symbols", [])
            )
        )
    
    pre_clean_count = len(symbols)
    symbols = clean_symbols(symbols)
    if len(symbols) < pre_clean_count:
        print(f"🧹 Removed {pre_clean_count - len(symbols)} invalid/delisted tickers")

    period = args.period
    print(f"\n🔍 Scanning {len(symbols)} symbols for volume pullback patterns ({period})...")
    print("=" * 90)

    if args.preload:
        DATA_MANAGER.preload_symbols(symbols, period=period)

    signal_gen = VolumePullbackEntrySignal(
        config=VolumePullbackConfig(
            min_green_candles=3,
            max_pullback_bars=7,
            ema9_tolerance_pct=10.0,
            max_pullback_pct=18.0,
            pullback_vol_ratio=0.85,
            min_breakout_vol_ratio=1.1,
            min_confidence=args.min_confidence,
            require_prior_resistance=True,
            min_breakout_pct=1.0,
            support_hold_tolerance_pct=8.0,
            max_below_ema9_pct=4.0,
            target_method=args.target_method,
        )
    )

    all_patterns: List[PatternInstance] = []
    global EARNINGS_CACHE
    global EARNINGS_MANUAL
    if args.earnings_window_days > 0:
        EARNINGS_CACHE = load_earnings_cache(EARNINGS_CACHE_PATH)
        EARNINGS_MANUAL = load_earnings_manual(EARNINGS_MANUAL_PATH)
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                scan_symbol,
                s,
                period,
                signal_gen,
                args.min_confidence,
                args.cooldown_bars,
                args.max_hold_bars,
                args.include_wait,
                args.entry1_weight,
                args.fallback_win_gain,
                args.fallback_loss,
                args.use_score_sizing,
                args.score_full,
                args.score_half,
                args.score_quarter,
                args.score_min,
                args.earnings_window_days,
                args.earnings_stale_days,
                args.exit_on_ema9_break,
                args.min_gain_before_ema9_exit,
                args.min_avg_volume,
            ): s
            for s in symbols
        }
        completed = 0
        for future in as_completed(futures):
            _, patterns = future.result()
            completed += 1
            if patterns:
                all_patterns.extend(patterns)
            if completed % 20 == 0:
                print(f"   Processed {completed}/{len(symbols)} symbols... ({len(all_patterns)} patterns found)")

    # Filter: keep only resolved outcomes and remove too-fast outcomes (default >= 2 days)
    filtered_patterns = [
        p
        for p in all_patterns
        if p.outcome in ("SUCCESS", "FAILURE") and p.days_to_outcome >= args.min_days_to_outcome
    ]

    filtered_patterns.sort(key=lambda x: x.entry_date, reverse=True)
    successes = [p for p in filtered_patterns if p.outcome == "SUCCESS"]
    failures = [p for p in filtered_patterns if p.outcome == "FAILURE"]
    win_rate = len(successes) / len(filtered_patterns) * 100 if filtered_patterns else 0.0

    print("\n" + "=" * 90)
    print("  VOLUME PULLBACK PATTERN BACKTEST")
    print("=" * 90)
    print(f"\n📊 OVERALL STATISTICS")
    print(f"   Total patterns found (filtered): {len(filtered_patterns)}")
    print(f"   ✅ Successes: {len(successes)} ({win_rate:.1f}%)")
    print(f"   ❌ Failures: {len(failures)}")
    
    if args.use_score_sizing and filtered_patterns:
        total_weight = sum(p.size_factor for p in filtered_patterns)
        if total_weight > 0:
            weighted_wins = sum(p.size_factor for p in filtered_patterns if p.outcome == "SUCCESS")
            weighted_win_rate = weighted_wins / total_weight * 100
            weighted_ev = sum(p.weighted_outcome_pct for p in filtered_patterns) / total_weight
            print(f"\n📊 SCORE-SIZED STATISTICS")
            print(f"   Weighted win rate: {weighted_win_rate:.1f}%")
            print(f"   Weighted EV: {weighted_ev:.2f}%")

    if args.export:
        os.makedirs(os.path.dirname(args.export) or ".", exist_ok=True)
        pd.DataFrame([p.__dict__ for p in filtered_patterns]).to_csv(
            args.export, index=False, float_format="%.2f"
        )
        print(f"✅ Exported {len(filtered_patterns)} patterns to: {args.export}")
    
    if args.export_performance:
        cols = [
            "symbol",
            "entry_date",
            "entry_price",
            "stop_loss",
            "target_1",
            "target_2",
            "exit_price",
            "exit_pct",
            "exit_reason",
            "outcome",
            "outcome_date",
            "score",
            "confidence",
            "r_multiple",
            "risk_reward",
            "max_gain_pct",
            "max_loss_pct",
        ]
        os.makedirs(os.path.dirname(args.export_performance) or ".", exist_ok=True)
        pd.DataFrame([p.__dict__ for p in filtered_patterns])[cols].to_csv(
            args.export_performance, index=False, float_format="%.2f"
        )
        print(f"✅ Exported performance log to: {args.export_performance}")

    if args.export_phases:
        os.makedirs(os.path.dirname(args.export_phases) or ".", exist_ok=True)
        phase_rows = []
        for s in symbols:
            d = DATA_MANAGER.get_daily_data(s, period=period)
            if d is None or d.empty or len(d) < 60:
                continue
            row = classify_current_phase(s, d, signal_gen)
            if args.include_none or row.get("phase") != "NONE":
                phase_rows.append(row)
        pd.DataFrame(phase_rows).to_csv(args.export_phases, index=False, float_format="%.2f")
        print(f"✅ Exported {len(phase_rows)} phase rows to: {args.export_phases}")
    
    if args.earnings_window_days > 0:
        save_earnings_cache(EARNINGS_CACHE_PATH)

    if args.render_examples and filtered_patterns:
        examples = filtered_patterns
        if args.use_score_sizing:
            examples = [p for p in filtered_patterns if p.score >= args.score_full]
        if args.render_recent and args.render_recent > 0:
            examples = sorted(examples, key=lambda p: p.entry_date, reverse=True)[: args.render_recent]
        render_examples(
            patterns=examples,
            period=period,
            output_dir=args.render_examples,
            max_success=args.examples_success,
            max_failure=args.examples_failure,
            examples=examples if args.render_recent else None,
        )
        print(f"✅ Rendered examples to: {args.render_examples}")


if __name__ == "__main__":
    main()

