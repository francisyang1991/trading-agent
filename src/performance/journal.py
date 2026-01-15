"""
Trade Journal
=============

Record and analyze trades with notes and lessons.
"""

import json
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from pathlib import Path

from ..core.types import Trade, Signal


@dataclass
class JournalEntry:
    """Trade journal entry."""
    trade_id: str
    symbol: str
    
    # Trade details
    entry_date: datetime
    exit_date: Optional[datetime]
    entry_price: float
    exit_price: float
    quantity: int
    direction: int  # 1=long, -1=short
    
    # Results
    pnl: float
    pnl_pct: float
    holding_days: int
    
    # Context
    strategy: str
    regime: str
    entry_reason: str
    exit_reason: str
    
    # Analysis
    what_went_well: str = ""
    what_went_wrong: str = ""
    lessons_learned: str = ""
    
    # Rating (1-5)
    execution_rating: int = 3
    setup_rating: int = 3
    
    # Tags
    tags: List[str] = field(default_factory=list)
    
    # Screenshots/charts
    chart_path: str = ""
    notes: str = ""


class TradeJournal:
    """
    Trade Journal - Record and learn from trades.
    
    Features:
    - Trade logging with context
    - Post-trade analysis
    - Pattern identification
    - Learning extraction
    
    Example:
        journal = TradeJournal()
        
        # Log trade
        entry = journal.log_trade(trade, signal)
        
        # Add analysis
        journal.add_analysis(
            entry.trade_id,
            what_went_well="Good entry timing",
            lessons="Need to trail stop tighter"
        )
        
        # Get insights
        insights = journal.get_insights()
    """
    
    def __init__(self, journal_path: str = "data/journal"):
        self.journal_path = Path(journal_path)
        self.journal_path.mkdir(parents=True, exist_ok=True)
        
        self._entries: Dict[str, JournalEntry] = {}
        
        # Load existing entries
        self._load_journal()
    
    def log_trade(
        self,
        trade: Trade,
        entry_signal: Optional[Signal] = None,
        exit_signal: Optional[Signal] = None
    ) -> JournalEntry:
        """
        Log completed trade to journal.
        
        Args:
            trade: Completed trade
            entry_signal: Entry signal (for context)
            exit_signal: Exit signal (for context)
            
        Returns:
            JournalEntry
        """
        holding_days = (
            (trade.exit_time - trade.entry_time).days
            if trade.exit_time and trade.entry_time else 0
        )
        
        # Extract context from signals
        entry_reason = ""
        exit_reason = ""
        strategy = ""
        regime = ""
        
        if entry_signal:
            entry_reason = entry_signal.metadata.get('reasons', []) if entry_signal.metadata else []
            entry_reason = ', '.join(entry_reason) if isinstance(entry_reason, list) else str(entry_reason)
            strategy = entry_signal.strategy.value if entry_signal.strategy else ""
            regime = entry_signal.regime.value if entry_signal.regime else ""
        
        if exit_signal:
            exit_reason = exit_signal.metadata.get('exit_reason', '') if exit_signal.metadata else ''
        
        entry = JournalEntry(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            entry_date=trade.entry_time,
            exit_date=trade.exit_time,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            direction=trade.direction,
            pnl=trade.pnl,
            pnl_pct=trade.pnl_pct,
            holding_days=holding_days,
            strategy=strategy,
            regime=regime,
            entry_reason=entry_reason,
            exit_reason=exit_reason
        )
        
        self._entries[trade.trade_id] = entry
        self._save_entry(entry)
        
        return entry
    
    def add_analysis(
        self,
        trade_id: str,
        what_went_well: str = None,
        what_went_wrong: str = None,
        lessons_learned: str = None,
        execution_rating: int = None,
        setup_rating: int = None,
        tags: List[str] = None,
        notes: str = None
    ):
        """Add post-trade analysis."""
        if trade_id not in self._entries:
            return
        
        entry = self._entries[trade_id]
        
        if what_went_well is not None:
            entry.what_went_well = what_went_well
        if what_went_wrong is not None:
            entry.what_went_wrong = what_went_wrong
        if lessons_learned is not None:
            entry.lessons_learned = lessons_learned
        if execution_rating is not None:
            entry.execution_rating = execution_rating
        if setup_rating is not None:
            entry.setup_rating = setup_rating
        if tags is not None:
            entry.tags.extend(tags)
        if notes is not None:
            entry.notes = notes
        
        self._save_entry(entry)
    
    def get_entry(self, trade_id: str) -> Optional[JournalEntry]:
        """Get journal entry by trade ID."""
        return self._entries.get(trade_id)
    
    def get_entries(
        self,
        symbol: str = None,
        strategy: str = None,
        start_date: datetime = None,
        end_date: datetime = None,
        winners_only: bool = False,
        losers_only: bool = False
    ) -> List[JournalEntry]:
        """Get filtered journal entries."""
        entries = list(self._entries.values())
        
        if symbol:
            entries = [e for e in entries if e.symbol == symbol]
        
        if strategy:
            entries = [e for e in entries if e.strategy == strategy]
        
        if start_date:
            entries = [e for e in entries if e.entry_date and e.entry_date >= start_date]
        
        if end_date:
            entries = [e for e in entries if e.entry_date and e.entry_date <= end_date]
        
        if winners_only:
            entries = [e for e in entries if e.pnl > 0]
        
        if losers_only:
            entries = [e for e in entries if e.pnl <= 0]
        
        return sorted(entries, key=lambda x: x.entry_date or datetime.min, reverse=True)
    
    def get_insights(self) -> Dict:
        """Extract insights from journal."""
        entries = list(self._entries.values())
        
        if not entries:
            return {}
        
        winners = [e for e in entries if e.pnl > 0]
        losers = [e for e in entries if e.pnl <= 0]
        
        # By strategy
        by_strategy = {}
        for entry in entries:
            if entry.strategy not in by_strategy:
                by_strategy[entry.strategy] = {'wins': 0, 'losses': 0, 'pnl': 0}
            
            if entry.pnl > 0:
                by_strategy[entry.strategy]['wins'] += 1
            else:
                by_strategy[entry.strategy]['losses'] += 1
            by_strategy[entry.strategy]['pnl'] += entry.pnl
        
        # Common lessons
        all_lessons = [e.lessons_learned for e in entries if e.lessons_learned]
        
        # Best and worst trades
        best_trade = max(entries, key=lambda x: x.pnl) if entries else None
        worst_trade = min(entries, key=lambda x: x.pnl) if entries else None
        
        return {
            'total_entries': len(entries),
            'winners': len(winners),
            'losers': len(losers),
            'win_rate': len(winners) / len(entries) * 100 if entries else 0,
            'by_strategy': by_strategy,
            'best_trade': best_trade.trade_id if best_trade else None,
            'worst_trade': worst_trade.trade_id if worst_trade else None,
            'avg_execution_rating': sum(e.execution_rating for e in entries) / len(entries) if entries else 0,
            'avg_setup_rating': sum(e.setup_rating for e in entries) / len(entries) if entries else 0,
            'lessons_count': len(all_lessons),
        }
    
    def export_to_csv(self, filepath: str = None):
        """Export journal to CSV."""
        if not self._entries:
            return
        
        if filepath is None:
            filepath = str(self.journal_path / "journal_export.csv")
        
        data = []
        for entry in self._entries.values():
            data.append({
                'trade_id': entry.trade_id,
                'symbol': entry.symbol,
                'entry_date': entry.entry_date,
                'exit_date': entry.exit_date,
                'entry_price': entry.entry_price,
                'exit_price': entry.exit_price,
                'quantity': entry.quantity,
                'pnl': entry.pnl,
                'pnl_pct': entry.pnl_pct,
                'strategy': entry.strategy,
                'entry_reason': entry.entry_reason,
                'exit_reason': entry.exit_reason,
                'what_went_well': entry.what_went_well,
                'what_went_wrong': entry.what_went_wrong,
                'lessons_learned': entry.lessons_learned,
                'execution_rating': entry.execution_rating,
                'setup_rating': entry.setup_rating,
                'tags': ','.join(entry.tags),
            })
        
        df = pd.DataFrame(data)
        df.to_csv(filepath, index=False)
    
    def _save_entry(self, entry: JournalEntry):
        """Save entry to file."""
        filepath = self.journal_path / f"{entry.trade_id}.json"
        
        data = {
            'trade_id': entry.trade_id,
            'symbol': entry.symbol,
            'entry_date': entry.entry_date.isoformat() if entry.entry_date else None,
            'exit_date': entry.exit_date.isoformat() if entry.exit_date else None,
            'entry_price': entry.entry_price,
            'exit_price': entry.exit_price,
            'quantity': entry.quantity,
            'direction': entry.direction,
            'pnl': entry.pnl,
            'pnl_pct': entry.pnl_pct,
            'holding_days': entry.holding_days,
            'strategy': entry.strategy,
            'regime': entry.regime,
            'entry_reason': entry.entry_reason,
            'exit_reason': entry.exit_reason,
            'what_went_well': entry.what_went_well,
            'what_went_wrong': entry.what_went_wrong,
            'lessons_learned': entry.lessons_learned,
            'execution_rating': entry.execution_rating,
            'setup_rating': entry.setup_rating,
            'tags': entry.tags,
            'notes': entry.notes,
        }
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _load_journal(self):
        """Load existing journal entries."""
        for filepath in self.journal_path.glob("*.json"):
            try:
                with open(filepath) as f:
                    data = json.load(f)
                
                entry = JournalEntry(
                    trade_id=data['trade_id'],
                    symbol=data['symbol'],
                    entry_date=datetime.fromisoformat(data['entry_date']) if data.get('entry_date') else None,
                    exit_date=datetime.fromisoformat(data['exit_date']) if data.get('exit_date') else None,
                    entry_price=data['entry_price'],
                    exit_price=data['exit_price'],
                    quantity=data['quantity'],
                    direction=data.get('direction', 1),
                    pnl=data['pnl'],
                    pnl_pct=data['pnl_pct'],
                    holding_days=data.get('holding_days', 0),
                    strategy=data.get('strategy', ''),
                    regime=data.get('regime', ''),
                    entry_reason=data.get('entry_reason', ''),
                    exit_reason=data.get('exit_reason', ''),
                    what_went_well=data.get('what_went_well', ''),
                    what_went_wrong=data.get('what_went_wrong', ''),
                    lessons_learned=data.get('lessons_learned', ''),
                    execution_rating=data.get('execution_rating', 3),
                    setup_rating=data.get('setup_rating', 3),
                    tags=data.get('tags', []),
                    notes=data.get('notes', ''),
                )
                
                self._entries[entry.trade_id] = entry
            except Exception as e:
                print(f"Error loading journal entry {filepath}: {e}")
