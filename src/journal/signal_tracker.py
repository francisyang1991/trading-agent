"""
Signal Tracker
==============
Tracks scanner signals and their outcomes to measure and improve signal quality.

This module is used by AI assistants to:
1. Log scanner signals automatically
2. Track outcomes (win/loss/expired)
3. Analyze signal accuracy over time
4. Generate insights for scanner improvement
"""

import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict
import yfinance as yf

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
JOURNAL_FILE = DATA_DIR / "signal_journal.json"


@dataclass
class Signal:
    """A scanner signal record."""
    symbol: str
    date: str
    signal_type: str  # BUY, WAIT, AVOID
    price_at_signal: Optional[float] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    expected_value: Optional[float] = None
    risk_reward: Optional[float] = None
    regime: Optional[str] = None
    rsi: Optional[float] = None
    # Outcome tracking
    outcome: Optional[str] = None  # WIN, LOSS, EXPIRED, MISSED
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    pnl_pct: Optional[float] = None
    notes: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Signal':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class SignalTracker:
    """
    Tracks and analyzes scanner signals.
    
    Usage:
        tracker = SignalTracker()
        tracker.log_signal(signal)
        tracker.update_outcome("XPEV", "WIN", exit_price=25.0)
        stats = tracker.get_performance_stats()
    """
    
    def __init__(self):
        self.journal = self._load_journal()
    
    def _load_journal(self) -> Dict:
        """Load journal from disk."""
        if JOURNAL_FILE.exists():
            try:
                with open(JOURNAL_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning("Journal corrupted, starting fresh")
        return {"signals": [], "last_updated": None}
    
    def _save_journal(self):
        """Save journal to disk."""
        self.journal["last_updated"] = datetime.now().isoformat()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_FILE, 'w') as f:
            json.dump(self.journal, f, indent=2, default=str)
    
    # =========================================================================
    # SIGNAL LOGGING
    # =========================================================================
    
    def log_signal(self, signal: Signal) -> bool:
        """
        Log a signal to the journal.
        Returns True if new signal, False if duplicate.
        """
        # Check for duplicate
        existing = {(s["symbol"], s["date"]) for s in self.journal["signals"]}
        key = (signal.symbol, signal.date)
        
        if key in existing:
            logger.debug(f"Signal already exists: {signal.symbol} on {signal.date}")
            return False
        
        self.journal["signals"].append(signal.to_dict())
        self._save_journal()
        return True
    
    def log_signals_from_scan(self, scan_results: List[Dict], date: str = None) -> int:
        """
        Log multiple signals from scanner results.
        
        Args:
            scan_results: List of StockScan dicts from scanner
            date: Signal date (defaults to today)
            
        Returns:
            Number of new signals logged
        """
        date = date or datetime.now().strftime("%Y-%m-%d")
        count = 0
        
        for r in scan_results:
            signal_type = "BUY" if "BUY" in r.get("action", "") else (
                "WAIT" if "WAIT" in r.get("action", "") else "AVOID"
            )
            
            signal = Signal(
                symbol=r["symbol"],
                date=date,
                signal_type=signal_type,
                price_at_signal=r.get("price"),
                entry_low=r.get("buy_zone_low"),
                entry_high=r.get("buy_zone_high"),
                stop_loss=r.get("stop_loss"),
                target_1=r.get("target_1"),
                target_2=r.get("target_2"),
                expected_value=r.get("expected_value"),
                risk_reward=r.get("risk_reward"),
                regime=r.get("regime"),
                rsi=r.get("rsi")
            )
            
            if self.log_signal(signal):
                count += 1
        
        return count
    
    # =========================================================================
    # OUTCOME TRACKING
    # =========================================================================
    
    def update_outcome(
        self,
        symbol: str,
        outcome: str,
        exit_price: float = None,
        notes: str = ""
    ) -> Optional[Dict]:
        """
        Update outcome for the most recent unresolved signal.
        
        Args:
            symbol: Stock symbol
            outcome: WIN, LOSS, EXPIRED, MISSED
            exit_price: Exit price (optional)
            notes: Additional notes
            
        Returns:
            Updated signal dict or None if not found
        """
        symbol = symbol.upper()
        
        # Find most recent unresolved signal
        for signal in reversed(self.journal["signals"]):
            if signal["symbol"] == symbol and signal["outcome"] is None:
                signal["outcome"] = outcome.upper()
                signal["exit_date"] = datetime.now().strftime("%Y-%m-%d")
                signal["notes"] = notes
                
                if exit_price:
                    signal["exit_price"] = exit_price
                    entry = signal.get("entry_low") or signal.get("price_at_signal")
                    if entry:
                        signal["pnl_pct"] = (exit_price / entry - 1) * 100
                
                self._save_journal()
                return signal
        
        return None
    
    def auto_check_outcomes(self, max_days: int = 30) -> List[Dict]:
        """
        Auto-check outcomes for pending BUY signals.
        
        Checks if price hit target or stop since signal date.
        
        Returns:
            List of updated signals
        """
        updated = []
        cutoff = datetime.now() - timedelta(days=max_days)
        
        pending = [
            s for s in self.journal["signals"]
            if s["outcome"] is None 
            and s["signal_type"] == "BUY"
            and datetime.strptime(s["date"], "%Y-%m-%d") >= cutoff
        ]
        
        for signal in pending:
            try:
                ticker = yf.Ticker(signal["symbol"])
                signal_date = datetime.strptime(signal["date"], "%Y-%m-%d")
                data = ticker.history(start=signal_date)
                
                if data.empty:
                    continue
                
                entry = signal.get("entry_high") or signal.get("price_at_signal")
                stop = signal.get("stop_loss")
                target = signal.get("target_1")
                
                if not entry:
                    continue
                
                high_since = data["High"].max()
                low_since = data["Low"].min()
                current = data["Close"].iloc[-1]
                days_held = (datetime.now() - signal_date).days
                
                outcome = None
                exit_price = None
                
                if target and high_since >= target:
                    outcome = "WIN"
                    exit_price = target
                elif stop and low_since <= stop:
                    outcome = "LOSS"
                    exit_price = stop
                elif days_held > 20:
                    outcome = "EXPIRED"
                    exit_price = current
                
                if outcome:
                    signal["outcome"] = outcome
                    signal["exit_price"] = exit_price
                    signal["exit_date"] = datetime.now().strftime("%Y-%m-%d")
                    signal["pnl_pct"] = (exit_price / entry - 1) * 100
                    updated.append(signal)
                    
            except Exception as e:
                logger.warning(f"Error checking {signal['symbol']}: {e}")
        
        if updated:
            self._save_journal()
        
        return updated
    
    # =========================================================================
    # ANALYSIS
    # =========================================================================
    
    def get_signals(
        self,
        signal_type: str = None,
        outcome: str = None,
        days_back: int = None
    ) -> List[Signal]:
        """Get filtered signals."""
        signals = self.journal["signals"]
        
        if days_back:
            cutoff = datetime.now() - timedelta(days=days_back)
            signals = [s for s in signals 
                      if datetime.strptime(s["date"], "%Y-%m-%d") >= cutoff]
        
        if signal_type:
            signals = [s for s in signals if s["signal_type"] == signal_type]
        
        if outcome:
            signals = [s for s in signals if s["outcome"] == outcome]
        
        return [Signal.from_dict(s) for s in signals]
    
    def get_pending_signals(self) -> List[Signal]:
        """Get unresolved signals."""
        return [
            Signal.from_dict(s) for s in self.journal["signals"]
            if s["outcome"] is None
        ]
    
    def get_performance_stats(self, days_back: int = 90) -> Dict:
        """
        Calculate signal performance statistics.
        
        Returns comprehensive stats for scanner improvement.
        """
        cutoff = datetime.now() - timedelta(days=days_back)
        resolved = [
            s for s in self.journal["signals"]
            if s["outcome"] is not None
            and datetime.strptime(s["date"], "%Y-%m-%d") >= cutoff
        ]
        
        if not resolved:
            return {"message": "No resolved signals in period"}
        
        # Overall stats
        wins = [s for s in resolved if s["outcome"] == "WIN"]
        losses = [s for s in resolved if s["outcome"] == "LOSS"]
        expired = [s for s in resolved if s["outcome"] == "EXPIRED"]
        
        win_rate = len(wins) / len(resolved) * 100 if resolved else 0
        
        pnls = [s["pnl_pct"] for s in resolved if s.get("pnl_pct") is not None]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        
        win_pnls = [s["pnl_pct"] for s in wins if s.get("pnl_pct")]
        loss_pnls = [s["pnl_pct"] for s in losses if s.get("pnl_pct")]
        
        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
        
        # Expected value
        ev = (win_rate/100 * avg_win) + ((100-win_rate)/100 * avg_loss)
        
        # By signal type
        by_type = {}
        for sig_type in ["BUY", "WAIT"]:
            type_signals = [s for s in resolved if s["signal_type"] == sig_type]
            if type_signals:
                type_wins = len([s for s in type_signals if s["outcome"] == "WIN"])
                by_type[sig_type] = {
                    "count": len(type_signals),
                    "win_rate": type_wins / len(type_signals) * 100
                }
        
        # By EV bucket
        by_ev = {}
        for bucket_name, (low, high) in [
            ("EV < 0", (-100, 0)),
            ("0-1%", (0, 1)),
            ("1-2%", (1, 2)),
            ("2%+", (2, 100))
        ]:
            bucket = [s for s in resolved 
                     if s.get("expected_value") is not None 
                     and low <= s["expected_value"] < high]
            if bucket:
                bucket_wins = len([s for s in bucket if s["outcome"] == "WIN"])
                by_ev[bucket_name] = {
                    "count": len(bucket),
                    "win_rate": bucket_wins / len(bucket) * 100
                }
        
        # By regime
        by_regime = {}
        for signal in resolved:
            regime = signal.get("regime")
            if regime:
                if regime not in by_regime:
                    by_regime[regime] = {"count": 0, "wins": 0}
                by_regime[regime]["count"] += 1
                if signal["outcome"] == "WIN":
                    by_regime[regime]["wins"] += 1
        
        for regime in by_regime:
            by_regime[regime]["win_rate"] = (
                by_regime[regime]["wins"] / by_regime[regime]["count"] * 100
            )
        
        return {
            "period_days": days_back,
            "total_signals": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "expired": len(expired),
            "win_rate": win_rate,
            "avg_pnl": avg_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expected_value": ev,
            "by_signal_type": by_type,
            "by_ev_bucket": by_ev,
            "by_regime": by_regime
        }
    
    def generate_improvement_insights(self, stats: Dict) -> List[str]:
        """
        Generate actionable insights for scanner improvement.
        
        Args:
            stats: Output from get_performance_stats()
            
        Returns:
            List of insight strings
        """
        insights = []
        
        # Win rate insights
        wr = stats.get("win_rate", 0)
        if wr < 45:
            insights.append("CRITICAL: Win rate below 45% - review signal criteria urgently")
        elif wr < 52:
            insights.append("Win rate marginal (45-52%) - consider tightening filters")
        elif wr > 60:
            insights.append("Strong win rate (>60%) - current parameters working well")
        
        # EV insights
        ev = stats.get("expected_value", 0)
        if ev < 0:
            insights.append("NEGATIVE EV - scanner is losing money on average")
        elif ev < 0.5:
            insights.append("Low EV (<0.5%) - barely profitable after commissions")
        elif ev > 1.5:
            insights.append("Strong EV (>1.5%) - good edge per trade")
        
        # EV bucket insights
        by_ev = stats.get("by_ev_bucket", {})
        high_ev = by_ev.get("2%+", {})
        low_ev = by_ev.get("EV < 0", {})
        
        if high_ev.get("win_rate", 0) > wr + 10:
            insights.append(
                f"High EV signals (2%+) outperform: {high_ev['win_rate']:.0f}% vs {wr:.0f}% overall. "
                "Consider raising EV threshold."
            )
        
        if low_ev.get("count", 0) > 0 and low_ev.get("win_rate", 0) < 45:
            insights.append(
                f"Negative EV signals underperforming ({low_ev['win_rate']:.0f}% WR). "
                "Confirm these are being filtered out."
            )
        
        # Regime insights
        by_regime = stats.get("by_regime", {})
        best_regime = max(by_regime.items(), key=lambda x: x[1]["win_rate"], default=(None, {}))
        worst_regime = min(by_regime.items(), key=lambda x: x[1]["win_rate"], default=(None, {}))
        
        if best_regime[0] and worst_regime[0]:
            if best_regime[1]["win_rate"] - worst_regime[1]["win_rate"] > 20:
                insights.append(
                    f"Regime disparity: {best_regime[0]} ({best_regime[1]['win_rate']:.0f}% WR) vs "
                    f"{worst_regime[0]} ({worst_regime[1]['win_rate']:.0f}% WR). "
                    f"Consider avoiding signals in {worst_regime[0]} regime."
                )
        
        return insights


# Singleton instance
_tracker_instance = None

def get_signal_tracker() -> SignalTracker:
    """Get singleton signal tracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = SignalTracker()
    return _tracker_instance
