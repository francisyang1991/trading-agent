"""
Config-based strategy routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class StrategyProfile:
    name: str
    entry_strategies: List[str]
    exit_strategies: List[str]
    position_policy: Dict


class ConfigStrategyRouter:
    """
    Route candidate tiers/tickers/sectors to strategy profiles via YAML config.
    """

    def __init__(self, config_path: str = "config/strategy_profiles.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        if not self.config_path.exists():
            return {"profiles": {}, "routing": {}}
        with self.config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"profiles": {}, "routing": {}}

    def _profile(self, name: str) -> Optional[StrategyProfile]:
        p = self.config.get("profiles", {}).get(name)
        if not p:
            return None
        return StrategyProfile(
            name=name,
            entry_strategies=p.get("entry_strategies", []),
            exit_strategies=p.get("exit_strategies", []),
            position_policy=p.get("position_policy", {}),
        )

    def route(
        self,
        ticker: str,
        tier: Optional[str] = None,
        sector: Optional[str] = None,
    ) -> Optional[StrategyProfile]:
        """
        Route by precedence: ticker -> sector -> tier -> default.
        """
        routing = self.config.get("routing", {})

        ticker_profile = routing.get("ticker_map", {}).get(ticker.upper())
        if ticker_profile:
            return self._profile(ticker_profile)

        if sector:
            sec_profile = routing.get("sector_map", {}).get(sector.lower())
            if sec_profile:
                return self._profile(sec_profile)

        if tier:
            tier_profile = routing.get("tier_map", {}).get(tier.upper())
            if tier_profile:
                return self._profile(tier_profile)

        default_profile = routing.get("default_profile")
        if default_profile:
            return self._profile(default_profile)
        return None
