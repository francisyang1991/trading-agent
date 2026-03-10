"""
Intraday signal layer for core-position T+0 MVP.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

import numpy as np
import pandas as pd


class IntradayAction(str, Enum):
    NONE = "NONE"
    BUY_SATELLITE = "BUY_SATELLITE"
    SELL_SATELLITE = "SELL_SATELLITE"
    REBALANCE_TO_CORE = "REBALANCE_TO_CORE"


@dataclass
class SignalDecision:
    symbol: str
    action: IntradayAction
    score: float
    reason: str
    trigger_price: float
    session_vwap: float
    zscore: float
    atr: float
    metadata: Dict[str, float]


@dataclass
class IntradaySignalConfig:
    min_bars_for_signal: int = 30
    zscore_entry_buy: float = -2.5
    zscore_entry_sell: float = 2.5
    zscore_exit_to_mean: float = 0.5
    rolling_std_window: int = 20
    atr_window: int = 14
    atr_stop_multiplier: float = 1.2


class IntradaySignalEngine:
    def __init__(self, config: IntradaySignalConfig):
        self.config = config

    @staticmethod
    def _calc_session_vwap(df: pd.DataFrame) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        pv = typical * df["volume"]
        c_pv = pv.cumsum()
        c_vol = df["volume"].cumsum().replace(0, np.nan)
        return c_pv / c_vol

    def _calc_zscore(self, close: pd.Series, session_vwap: pd.Series) -> pd.Series:
        spread = close - session_vwap
        rolling_std = spread.rolling(
            window=self.config.rolling_std_window,
            min_periods=self.config.rolling_std_window,
        ).std()
        return spread / rolling_std.replace(0, np.nan)

    def _calc_atr(self, df: pd.DataFrame) -> pd.Series:
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(self.config.atr_window, min_periods=self.config.atr_window).mean()

    def build_features(self, bars_1m: pd.DataFrame) -> pd.DataFrame:
        if bars_1m.empty:
            return bars_1m
        df = bars_1m.copy()
        df["session_vwap"] = self._calc_session_vwap(df)
        df["zscore"] = self._calc_zscore(df["close"], df["session_vwap"])
        df["atr"] = self._calc_atr(df)
        return df

    def generate(
        self,
        symbol: str,
        bars_1m: pd.DataFrame,
        satellite_qty: int,
        max_satellite_qty: int,
        can_add: bool,
        can_reduce: bool,
    ) -> Optional[SignalDecision]:
        if bars_1m.empty or len(bars_1m) < self.config.min_bars_for_signal:
            return None

        feat = self.build_features(bars_1m)
        row = feat.iloc[-1]
        z = row.get("zscore")
        atr = row.get("atr")
        session_vwap = row.get("session_vwap")
        px = float(row["close"])

        if pd.isna(z) or pd.isna(atr) or pd.isna(session_vwap):
            return None

        # Mean-reversion close condition for existing satellite exposure.
        if satellite_qty > 0 and z >= self.config.zscore_exit_to_mean and can_reduce:
            return SignalDecision(
                symbol=symbol,
                action=IntradayAction.SELL_SATELLITE,
                score=min(1.0, abs(z) / 3.0),
                reason="Long satellite mean reversion exit",
                trigger_price=px,
                session_vwap=float(session_vwap),
                zscore=float(z),
                atr=float(atr),
                metadata={"satellite_qty": float(satellite_qty)},
            )

        if satellite_qty < 0 and z <= -self.config.zscore_exit_to_mean and can_add:
            return SignalDecision(
                symbol=symbol,
                action=IntradayAction.BUY_SATELLITE,
                score=min(1.0, abs(z) / 3.0),
                reason="Sold-from-core buyback mean reversion",
                trigger_price=px,
                session_vwap=float(session_vwap),
                zscore=float(z),
                atr=float(atr),
                metadata={"satellite_qty": float(satellite_qty)},
            )

        # Add satellite long on downside extreme.
        if z <= self.config.zscore_entry_buy and can_add:
            return SignalDecision(
                symbol=symbol,
                action=IntradayAction.BUY_SATELLITE,
                score=min(1.0, abs(z) / 3.0),
                reason="VWAP downside deviation buy",
                trigger_price=px,
                session_vwap=float(session_vwap),
                zscore=float(z),
                atr=float(atr),
                metadata={"satellite_qty": float(satellite_qty)},
            )

        # Reduce via high throw on upside extreme.
        if z >= self.config.zscore_entry_sell and can_reduce and satellite_qty > -max_satellite_qty:
            return SignalDecision(
                symbol=symbol,
                action=IntradayAction.SELL_SATELLITE,
                score=min(1.0, abs(z) / 3.0),
                reason="VWAP upside deviation sell",
                trigger_price=px,
                session_vwap=float(session_vwap),
                zscore=float(z),
                atr=float(atr),
                metadata={"satellite_qty": float(satellite_qty)},
            )

        return SignalDecision(
            symbol=symbol,
            action=IntradayAction.NONE,
            score=0.0,
            reason="No actionable signal",
            trigger_price=px,
            session_vwap=float(session_vwap),
            zscore=float(z),
            atr=float(atr),
            metadata={"satellite_qty": float(satellite_qty)},
        )
