"""
Ranking Engine - Rank and filter stock picks.

Provides additional ranking and filtering beyond the base score:
- Composite ranking across multiple factors
- Percentile-based ranking
- Filtering by various criteria
"""

from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
import numpy as np

from ..core.types import PickScore, Regime, Strategy


@dataclass
class RankingCriteria:
    """Criteria for ranking stocks."""
    
    # Score weights for composite ranking
    score_weight: float = 0.40
    momentum_weight: float = 0.20
    rs_weight: float = 0.20
    rr_weight: float = 0.20
    
    # Filters
    min_score: float = 60.0
    min_rr_ratio: float = 1.5
    min_momentum_6m: float = 0.0
    max_volatility: float = 100.0
    
    # Regime filters
    allowed_regimes: Optional[List[Regime]] = None
    excluded_strategies: Optional[List[Strategy]] = None


class RankingEngine:
    """
    Engine for ranking and filtering stock picks.
    
    Example:
        engine = RankingEngine()
        criteria = RankingCriteria(min_score=70, min_rr_ratio=2.0)
        ranked = engine.rank(picks, criteria)
    """
    
    def __init__(self):
        pass
    
    def rank(
        self,
        picks: List[PickScore],
        criteria: Optional[RankingCriteria] = None
    ) -> List[PickScore]:
        """
        Rank and filter stock picks.
        
        Args:
            picks: List of stock picks
            criteria: Ranking criteria
            
        Returns:
            Filtered and ranked list
        """
        if criteria is None:
            criteria = RankingCriteria()
        
        # Filter
        filtered = self._filter(picks, criteria)
        
        if not filtered:
            return []
        
        # Calculate composite scores
        scores = []
        for pick in filtered:
            composite = self._calculate_composite_score(pick, criteria)
            scores.append((pick, composite))
        
        # Sort by composite score
        scores.sort(key=lambda x: x[1], reverse=True)
        
        # Update ranks
        result = []
        for i, (pick, _) in enumerate(scores):
            pick.rank = i + 1
            result.append(pick)
        
        return result
    
    def _filter(
        self,
        picks: List[PickScore],
        criteria: RankingCriteria
    ) -> List[PickScore]:
        """Apply filters to picks."""
        filtered = []
        
        for pick in picks:
            # Score filter
            if pick.total_score < criteria.min_score:
                continue
            
            # R:R filter
            if pick.risk_reward < criteria.min_rr_ratio:
                continue
            
            # Momentum filter
            if pick.momentum_6m < criteria.min_momentum_6m:
                continue
            
            # Volatility filter
            if pick.volatility > criteria.max_volatility:
                continue
            
            # Regime filter
            if criteria.allowed_regimes:
                if pick.regime not in criteria.allowed_regimes:
                    continue
            
            # Strategy filter
            if criteria.excluded_strategies:
                if pick.strategy in criteria.excluded_strategies:
                    continue
            
            filtered.append(pick)
        
        return filtered
    
    def _calculate_composite_score(
        self,
        pick: PickScore,
        criteria: RankingCriteria
    ) -> float:
        """Calculate composite ranking score."""
        
        # Normalize momentum to 0-100 scale
        mom_normalized = min(100, max(0, pick.momentum_6m + 50))
        
        # Normalize RS to 0-100 scale
        rs_normalized = min(100, max(0, pick.rs_vs_spy + 50))
        
        # Normalize R:R to 0-100 scale (4+ is 100)
        rr_normalized = min(100, pick.risk_reward * 25)
        
        composite = (
            pick.total_score * criteria.score_weight +
            mom_normalized * criteria.momentum_weight +
            rs_normalized * criteria.rs_weight +
            rr_normalized * criteria.rr_weight
        )
        
        return composite
    
    def top_n(
        self,
        picks: List[PickScore],
        n: int = 10,
        criteria: Optional[RankingCriteria] = None
    ) -> List[PickScore]:
        """Get top N picks."""
        ranked = self.rank(picks, criteria)
        return ranked[:n]
    
    def by_grade(
        self,
        picks: List[PickScore],
        grades: List[str] = ['A', 'B']
    ) -> List[PickScore]:
        """Filter picks by grade."""
        return [p for p in picks if p.grade in grades]
    
    def by_regime(
        self,
        picks: List[PickScore],
        regimes: List[Regime]
    ) -> List[PickScore]:
        """Filter picks by regime."""
        return [p for p in picks if p.regime in regimes]
    
    def by_strategy(
        self,
        picks: List[PickScore],
        strategies: List[Strategy]
    ) -> List[PickScore]:
        """Filter picks by strategy."""
        return [p for p in picks if p.strategy in strategies]
    
    def actionable_picks(
        self,
        picks: List[PickScore],
        current_prices: Optional[Dict[str, float]] = None
    ) -> List[PickScore]:
        """
        Get picks that are currently actionable (in buy zone).
        
        Args:
            picks: List of picks
            current_prices: Current prices by symbol
            
        Returns:
            Picks that are in their buy zone
        """
        actionable = []
        
        for pick in picks:
            # Skip stay cash
            if pick.strategy == Strategy.STAY_CASH:
                continue
            
            # Check if in buy zone
            price = current_prices.get(pick.symbol) if current_prices else None
            
            if price is None:
                # Use entry zone midpoint as proxy
                price = (pick.entry_zone_low + pick.entry_zone_high) / 2
            
            if pick.entry_zone_low <= price <= pick.entry_zone_high:
                actionable.append(pick)
        
        return actionable


def rank_picks(
    picks: List[PickScore],
    min_score: float = 60.0,
    min_rr_ratio: float = 1.5,
    top_n: Optional[int] = None
) -> List[PickScore]:
    """
    Convenience function to rank picks.
    
    Args:
        picks: List of stock picks
        min_score: Minimum score threshold
        min_rr_ratio: Minimum risk/reward ratio
        top_n: Return only top N picks
        
    Returns:
        Ranked and filtered list
    """
    engine = RankingEngine()
    criteria = RankingCriteria(
        min_score=min_score,
        min_rr_ratio=min_rr_ratio
    )
    
    ranked = engine.rank(picks, criteria)
    
    if top_n:
        return ranked[:top_n]
    
    return ranked


def rank_tiered_candidates(candidates: List) -> List:
    """
    Rank tiered candidates by tier priority then composite score.
    Compatible with TieredCandidate-like objects.
    """
    tier_priority = {"A": 3, "B": 2, "C": 1}
    ordered = sorted(
        candidates,
        key=lambda c: (
            tier_priority.get(getattr(c, "tier", "C"), 0),
            getattr(c, "composite_score", 0),
            getattr(c, "rs_rank", 0),
        ),
        reverse=True,
    )
    return ordered
