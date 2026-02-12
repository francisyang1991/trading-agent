"""
Pattern Library — Technical Pattern Recognition & Win Rate Tracking
====================================================================
Extracts specific chart patterns from Discord trading messages and
tracks each pattern's real-world win rate over time.

The bot learns which patterns actually make money and which don't,
then uses that to weight future signals.

Example messages:
  "$AMD Setting up a cup and handle ahead of earnings"
    → pattern: CUP_AND_HANDLE, direction: BULL

  "$RGTI textbook bearflag breakdown"
    → pattern: BEAR_FLAG, direction: BEAR

  "$INTC Still holding EMA8 despite market weakness"
    → pattern: EMA_HOLD, sub: EMA8, direction: BULL

  "$LEU low risk entry around 285 with sl below 275"
    → pattern: ENTRY_WITH_STOP, entry: 285, stop: 275, direction: BULL
"""

import re
import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

log = logging.getLogger("pattern-library")

DATA_DIR = os.path.join(os.path.dirname(__file__), '../../data')
PATTERN_DB_FILE = os.path.join(DATA_DIR, 'pattern_performance.json')
os.makedirs(DATA_DIR, exist_ok=True)


# ===================================================================
# Pattern Definitions — all known technical patterns from Discord
# ===================================================================

@dataclass
class PatternDef:
    """Definition of a technical pattern to detect."""
    name: str            # Machine name: CUP_AND_HANDLE
    display: str         # Human name: Cup & Handle
    direction: str       # BULL / BEAR / EITHER
    regex: list          # List of regex patterns to match
    description: str = ""


# Master pattern catalog — order matters (first match wins for overlapping)
PATTERN_CATALOG: List[PatternDef] = [
    # ── Chart Patterns (bullish) ──
    PatternDef("CUP_AND_HANDLE", "Cup & Handle", "BULL",
               [r'cup\s*(?:and|&|n)\s*handle', r'c\s*&\s*h\s+breakout', r'C&H'],
               "Bullish continuation: rounded base + handle consolidation before breakout"),
    PatternDef("BULL_FLAG", "Bull Flag", "BULL",
               [r'bull\s*flag', r'flagging\s+(?:after|on|above)', r'first\s+flag',
                r'flag\s+out\s+of', r'building\s+a\s+(?:little\s+)?flag',
                r'massive\s+weekly\s+base.*flag'],
               "Consolidation after sharp up move, typically continues higher"),
    PatternDef("BREAKOUT", "Breakout", "BULL",
               [r'break(?:ing)?\s*out', r'breakout', r'broke\s+(?:out|above|through)',
                r'breaking\s+(?:out|above|through)', r'ATH\s+break'],
               "Price breaking above resistance/consolidation"),
    PatternDef("ATH_BREAKOUT", "All-Time High Breakout", "BULL",
               [r'ATH', r'all.?time\s+high', r'new\s+high'],
               "Price at or breaking to new all-time highs"),
    PatternDef("BASE_BREAKOUT", "Base Breakout", "BULL",
               [r'base\s+breakout', r'breaking\s+out\s+of.*consolidation',
                r'out\s+of\s+(?:the\s+)?base', r'weekly\s+base'],
               "Breaking out of extended consolidation/base"),
    PatternDef("DOUBLE_BOTTOM", "Double Bottom", "BULL",
               [r'double\s+bottom', r'W\s+bottom', r'W\s+pattern'],
               "Two lows at same level, bullish reversal"),
    PatternDef("INVERSE_HEAD_SHOULDERS", "Inverse H&S", "BULL",
               [r'inverse\s+(?:head|h)\s*(?:and|&)\s*(?:shoulders|s)',
                r'inv\s+h\s*&\s*s', r'iHS'],
               "Bullish reversal pattern"),
    PatternDef("ASCENDING_TRIANGLE", "Ascending Triangle", "BULL",
               [r'ascending\s+triangle', r'asc\s+triangle'],
               "Higher lows against flat resistance, bullish"),

    # ── Chart Patterns (bearish) ──
    PatternDef("BEAR_FLAG", "Bear Flag", "BEAR",
               [r'bear\s*flag', r'bearflag'],
               "Consolidation after sharp down move, continues lower"),
    PatternDef("BREAKDOWN", "Breakdown", "BEAR",
               [r'break(?:ing)?\s*down', r'breakdown', r'broke\s+(?:down|below)',
                r'losing\s+(?:support|key)', r'lost\s+support'],
               "Price breaking below support"),
    PatternDef("HEAD_SHOULDERS", "Head & Shoulders", "BEAR",
               [r'(?<!inverse\s)head\s*(?:and|&)\s*shoulders',
                r'(?<!inv\s)h\s*&\s*s\s+(?:top|pattern)', r'h\s+pattern\b'],
               "Bearish reversal: three peaks with middle highest"),
    PatternDef("DOUBLE_TOP", "Double Top", "BEAR",
               [r'double\s+top', r'M\s+(?:top|pattern)'],
               "Two highs at same level, bearish reversal"),
    PatternDef("DTSS", "Down-Trend Short Sell", "BEAR",
               [r'DTSS', r'down\s*trend\s+short'],
               "Bearish trend continuation pattern"),

    # ── EMA / Moving Average Patterns ──
    PatternDef("EMA8_HOLD", "EMA8 Hold/Bounce", "BULL",
               [r'hold(?:ing)?\s+(?:the\s+)?(?:daily\s+)?EMA\s*8',
                r'EMA\s*8\s+(?:bounce|hold|support)',
                r'above\s+EMA\s*8', r'at\s+(?:the\s+)?EMA\s*8',
                r'pullback\s+to\s+(?:the\s+)?EMA\s*8',
                r'(?:reclaim|test).*EMA\s*8'],
               "Price holding or bouncing off 8-day EMA"),
    PatternDef("EMA21_HOLD", "EMA21 Hold/Bounce", "BULL",
               [r'hold(?:ing)?\s+(?:the\s+)?(?:daily\s+)?EMA\s*21',
                r'EMA\s*21\s+(?:bounce|hold|support)',
                r'above\s+EMA\s*21', r'at\s+(?:the\s+)?(?:weekly\s+)?EMA\s*21',
                r'pullback\s+to\s+(?:the\s+)?EMA\s*21',
                r'losing\s+(?:the\s+)?(?:daily\s+)?EMA\s*21'],
               "Price interacting with 21-day EMA"),
    PatternDef("EMA50_HOLD", "EMA50 Hold/Bounce", "BULL",
               [r'(?:above|hold(?:ing)?|bounce|at)\s+(?:the\s+)?(?:daily\s+)?(?:50\s*)?EMA\s*50',
                r'50\s*EMA\s+(?:bounce|hold|support)',
                r'harami\s+above\s+50\s*EMA', r'above\s+50\s*EMA'],
               "Price holding 50-day EMA — significant support"),
    PatternDef("EMA_CROSS", "EMA Crossover", "EITHER",
               [r'EMA\s*(?:cross|golden|death)', r'golden\s+cross', r'death\s+cross'],
               "Moving average crossover signal"),

    # ── Volume Patterns ──
    PatternDef("VOLUME_ACCUMULATION", "Accumulation Volume", "BULL",
               [r'accumul(?:ation|ating)\s+volume', r'volume\s+(?:is\s+)?good',
                r'notable\s+volume', r'volume\s+(?:spike|surge|pickup)',
                r'picking\s+up\s+(?:momentum|volume)'],
               "Increasing volume on up moves — institutional buying"),
    PatternDef("LOW_VOL_PULLBACK", "Low Volume Pullback", "BULL",
               [r'low\s+vol(?:ume)?\s+pullback', r'pullback\s+(?:with\s+)?declin(?:ing|e)\s+vol',
                r'declining\s+volume', r'volume\s+dry(?:ing)?\s+up'],
               "Pullback on declining volume — healthy consolidation"),
    PatternDef("VOLUME_BREAKOUT", "Volume Breakout", "BULL",
               [r'volume\s+break(?:out)?', r'break(?:out)?\s+on\s+(?:big|heavy|huge)\s+vol'],
               "Breakout confirmed by heavy volume"),

    # ── Momentum / Trend ──
    PatternDef("RELATIVE_STRENGTH", "Relative Strength", "BULL",
               [r'relative\s+strength', r'notable\s+RS', r'\bRS\b\s+today',
                r'RS\s+(?:leader|strong)', r'despite\s+(?:market|broader)\s+weakness'],
               "Stock outperforming the broader market"),
    PatternDef("BEACHBALL", "Beachball Under Water", "BULL",
               [r'beachball', r'beach\s+ball', r'coiled\s+spring'],
               "Compressed stock ready to pop — strong buying pressure held down"),
    PatternDef("TIGHTENING", "Tightening / Squeeze", "BULL",
               [r'tighten(?:ing|ed)\s+up', r'squeez(?:ing|e)', r'compress(?:ing|ed)',
                r'narrow(?:ing)?\s+range', r'coiling'],
               "Price range narrowing — breakout imminent"),
    PatternDef("GAP_UP", "Gap Up", "BULL",
               [r'gap\s*(?:up|higher)', r'gapped\s+up', r'EP\s+gap'],
               "Price gaps higher at open"),
    PatternDef("GAP_DOWN", "Gap Down", "BEAR",
               [r'gap\s*(?:down|lower)', r'gapped\s+down'],
               "Price gaps lower at open"),

    # ── Pullback / Dip Buy ──
    PatternDef("PULLBACK_BUY", "Pullback Buy", "BULL",
               [r'pullback\s+(?:buy|entry|play)', r'dip\s*(?:buy|buying)',
                r'buy\s+(?:the\s+)?dip', r'bounce\s+play',
                r'potential\s+bounce', r'low\s+risk\s+entry'],
               "Buying on a temporary pullback in uptrend"),
    PatternDef("UNDERCUT_RECLAIM", "Undercut & Reclaim", "BULL",
               [r'undercut\s+(?:and|&)\s+reclaim', r'FBO.*reclaim',
                r'shakeout.*reclaim', r'reclaim\s+of'],
               "False breakdown followed by reclaim — bullish trap"),

    # ── Candlestick Patterns ──
    PatternDef("BULLISH_HARAMI", "Bullish Harami", "BULL",
               [r'bullish\s+harami', r'bull\s+harami'],
               "Small body inside prior large bearish candle — reversal"),
    PatternDef("BEARISH_ENGULFING", "Bearish Engulfing", "BEAR",
               [r'bearish\s+engulfing', r'bear\s+engulf'],
               "Large bearish candle engulfs prior bullish — reversal"),
    PatternDef("HAMMER", "Hammer / Doji", "BULL",
               [r'hammer', r'doji', r'spinning\s+top', r'long\s+wick'],
               "Reversal candlestick pattern"),
    PatternDef("MACD_CROSS", "MACD Crossover", "EITHER",
               [r'MACD\s+cross(?:ing|ed)?(?:\s+(?:up|down))?',
                r'MACD\s+(?:bullish|bearish)'],
               "MACD signal line crossover"),

    # ── Explicit Trade Signals ──
    PatternDef("ENTRY_WITH_STOP", "Entry with Stop Loss", "EITHER",
               [r'entry\s+(?:at|around|near)\s+\d', r'stop\s+(?:at|below|above|loss)\s+\d',
                r'sl\s+(?:at|below|above)\s+\d', r'\bstop\b.*\d+.*\btarget\b',
                r'with\s+(?:stop|sl)\s+(?:at|below)'],
               "Explicit entry price with stop loss level"),
    PatternDef("TOOK_POSITION", "Took Position", "EITHER",
               [r'I\s+(?:took|bought|sold|shorted|entered|added)',
                r'(?:took|taking)\s+(?:a\s+)?(?:long|short|position)',
                r'I\'m\s+(?:in|long|short)', r'bought\s+(?:some|here|at)',
                r'took\s+(?:some|this|profits?)'],
               "Trader explicitly entered/exited a position"),
]


# ===================================================================
# Pattern Record — tracks one pattern instance with outcome
# ===================================================================

@dataclass
class PatternRecord:
    """A detected pattern instance with outcome tracking."""
    # Identity
    record_id: str = ""
    ticker: str = ""
    pattern_name: str = ""       # e.g. CUP_AND_HANDLE
    pattern_display: str = ""    # e.g. Cup & Handle
    direction: str = ""          # BULL / BEAR
    # Source
    author: str = ""
    channel_id: str = ""
    signal_date: str = ""
    content_snippet: str = ""
    # Price tracking
    price_at_signal: float = 0
    price_1d: float = 0
    price_3d: float = 0
    price_5d: float = 0
    price_10d: float = 0
    price_20d: float = 0
    return_1d: float = 0
    return_3d: float = 0
    return_5d: float = 0
    return_10d: float = 0
    return_20d: float = 0
    # Parsed entry/stop/target
    entry_price: float = 0
    stop_price: float = 0
    target_price: float = 0
    # Verdict
    is_winner: bool = False
    is_loser: bool = False
    peak_return: float = 0

    def to_dict(self):
        return asdict(self)


# ===================================================================
# Pattern Extraction — parse messages for patterns
# ===================================================================

def extract_patterns(content: str, ticker: str, author: str = "",
                     channel_id: str = "", msg_id: str = "",
                     timestamp: str = "") -> List[PatternRecord]:
    """
    Extract all technical patterns from a Discord message for a given ticker.

    Returns list of PatternRecords (one per unique pattern found).
    """
    results = []
    seen_patterns = set()
    content_lower = content.lower()

    for pdef in PATTERN_CATALOG:
        if pdef.name in seen_patterns:
            continue

        matched = False
        for rgx in pdef.regex:
            if re.search(rgx, content, re.IGNORECASE):
                matched = True
                break

        if not matched:
            continue

        seen_patterns.add(pdef.name)

        # Determine direction override from context
        direction = pdef.direction
        if direction == "EITHER":
            # Try to infer from content
            bull_score = sum(1 for kw in ['buy', 'long', 'bull', 'bounce', 'support', 'higher']
                            if kw in content_lower)
            bear_score = sum(1 for kw in ['sell', 'short', 'bear', 'breakdown', 'lower']
                             if kw in content_lower)
            direction = "BULL" if bull_score >= bear_score else "BEAR"

        # Try to extract price levels
        entry_price, stop_price, target_price = _extract_price_levels(content)

        rec = PatternRecord(
            record_id=f"{channel_id}_{msg_id}_{ticker}_{pdef.name}",
            ticker=ticker,
            pattern_name=pdef.name,
            pattern_display=pdef.display,
            direction=direction,
            author=author,
            channel_id=channel_id,
            signal_date=timestamp,
            content_snippet=content[:250],
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
        )
        results.append(rec)

    return results


def _extract_price_levels(content: str) -> Tuple[float, float, float]:
    """
    Try to extract entry, stop loss, and target prices from message.

    Examples:
      "entry around 285 with sl below 275" → (285, 275, 0)
      "buy zone 24.50-25.50, stop 23" → (25, 23, 0)
      "target 350" → (0, 0, 350)
    """
    entry = 0.0
    stop = 0.0
    target = 0.0

    # Entry patterns
    m = re.search(r'entry\s+(?:at|around|near)\s+\$?(\d+\.?\d*)', content, re.I)
    if m:
        entry = float(m.group(1))

    # Stop loss patterns
    m = re.search(r'(?:stop|sl|stop\s*loss)\s+(?:at|below|above)?\s*\$?(\d+\.?\d*)', content, re.I)
    if m:
        stop = float(m.group(1))

    # Target patterns
    m = re.search(r'target\s+(?:at|of|is)?\s*\$?(\d+\.?\d*)', content, re.I)
    if m:
        target = float(m.group(1))

    return entry, stop, target


# ===================================================================
# Raw Signal Parser — for Goku direct buy/sell channel
# ===================================================================

def parse_raw_signal(content: str, author: str = "", channel_id: str = "",
                     msg_id: str = "", timestamp: str = "") -> List[PatternRecord]:
    """
    Parse raw buy/sell signals from Goku's direct signal channel.
    These messages tend to be more structured:
      "BUY $HOOD 24.50"
      "SELL $TSLA 250"
      "$AAPL LONG 175-180, SL 170, TP 195"
    """
    results = []
    content_upper = content.upper()

    # Find tickers
    tickers = re.findall(r'\$([A-Z]{1,5})\b', content)
    if not tickers:
        return results

    # Detect action
    is_buy = bool(re.search(r'\b(BUY|LONG|BOUGHT|CALL|ADDING|ENTERED)\b', content_upper))
    is_sell = bool(re.search(r'\b(SELL|SHORT|SOLD|PUT|EXIT|TRIM|CUT)\b', content_upper))
    direction = "BULL" if is_buy else "BEAR" if is_sell else "BULL"

    entry, stop, target = _extract_price_levels(content)

    # Also look for simple price after action word
    if entry == 0:
        m = re.search(r'(?:BUY|SELL|LONG|SHORT)\s+\$\w+\s+(?:at\s+)?\$?(\d+\.?\d*)', content, re.I)
        if m:
            entry = float(m.group(1))

    for ticker in set(tickers[:3]):
        pattern_name = "RAW_BUY" if direction == "BULL" else "RAW_SELL"
        rec = PatternRecord(
            record_id=f"{channel_id}_{msg_id}_{ticker}_RAW",
            ticker=ticker,
            pattern_name=pattern_name,
            pattern_display=f"Raw {'Buy' if direction == 'BULL' else 'Sell'} Signal",
            direction=direction,
            author=author,
            channel_id=channel_id,
            signal_date=timestamp,
            content_snippet=content[:250],
            entry_price=entry,
            stop_price=stop,
            target_price=target,
        )
        results.append(rec)

    return results


# ===================================================================
# Pattern Stats — aggregate win rates per pattern
# ===================================================================

@dataclass
class PatternStats:
    """Aggregate statistics for one pattern type — risk-adjusted."""
    pattern_name: str = ""
    display_name: str = ""
    total_signals: int = 0
    tracked: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0
    avg_return_5d: float = 0
    avg_return_10d: float = 0
    avg_return_20d: float = 0
    # Risk-adjusted metrics
    avg_winner: float = 0       # Average return of winning trades
    avg_loser: float = 0        # Average return of losing trades (negative)
    expected_value: float = 0   # EV = WR * avg_win + (1-WR) * avg_loss
    profit_factor: float = 0    # Total gains / Total losses (>1 is profitable)
    max_drawdown: float = 0     # Worst single trade return
    best_return: float = 0      # Best single trade return
    # Composite quality grade
    grade: str = ""             # A / B / C / D / F based on EV + risk
    best_hold: str = ""         # Best holding period (5d / 10d / 20d)
    best_trade: str = ""
    worst_trade: str = ""
    confidence: str = ""        # HIGH / MEDIUM / LOW based on sample size

    def to_dict(self):
        return asdict(self)


def compute_pattern_stats(records: List[dict]) -> Dict[str, PatternStats]:
    """
    Compute risk-adjusted stats for each pattern type.

    Key metrics:
    - Expected Value (EV): (win_rate * avg_winner) + (loss_rate * avg_loser)
    - Profit Factor: sum(gains) / abs(sum(losses))
    - Max Drawdown: worst single trade
    - Grade: composite of EV, profit factor, sample size

    Returns: {pattern_name: PatternStats}
    """
    by_pattern: Dict[str, List[dict]] = {}
    for rec in records:
        name = rec.get('pattern_name', 'UNKNOWN')
        if name not in by_pattern:
            by_pattern[name] = []
        by_pattern[name].append(rec)

    stats = {}
    for name, recs in by_pattern.items():
        s = PatternStats(pattern_name=name)
        s.display_name = recs[0].get('pattern_display', name) if recs else name
        s.total_signals = len(recs)

        tracked = [r for r in recs if r.get('price_at_signal', 0) > 0
                   and any(r.get(f'return_{d}d', 0) != 0 for d in [1, 3, 5, 10, 20])]
        s.tracked = len(tracked)

        if not tracked:
            s.confidence = "LOW"
            s.grade = "?"
            stats[name] = s
            continue

        # Use the best available return for each record as the "trade return"
        trade_returns = []
        for r in tracked:
            rets = [r.get(f'return_{d}d', 0) for d in [5, 10, 20]
                    if r.get(f'return_{d}d', 0) != 0]
            if rets:
                # Use the 10d return as primary (realistic holding period)
                # Fallback to 5d, then 20d
                ret = r.get('return_10d', 0) or r.get('return_5d', 0) or r.get('return_20d', 0)
                trade_returns.append(ret)

        if not trade_returns:
            s.confidence = "LOW"
            s.grade = "?"
            stats[name] = s
            continue

        # Win = any period with >=2% gain, Loss = any period with <=-5% loss
        s.winners = sum(1 for r in tracked if r.get('is_winner'))
        s.losers = sum(1 for r in tracked if r.get('is_loser'))
        s.win_rate = s.winners / s.tracked if s.tracked > 0 else 0

        # Period averages
        ret5 = [r.get('return_5d', 0) for r in tracked if r.get('return_5d', 0) != 0]
        ret10 = [r.get('return_10d', 0) for r in tracked if r.get('return_10d', 0) != 0]
        ret20 = [r.get('return_20d', 0) for r in tracked if r.get('return_20d', 0) != 0]
        s.avg_return_5d = round(sum(ret5) / len(ret5), 2) if ret5 else 0
        s.avg_return_10d = round(sum(ret10) / len(ret10), 2) if ret10 else 0
        s.avg_return_20d = round(sum(ret20) / len(ret20), 2) if ret20 else 0

        # Best hold period
        period_returns = {'5d': s.avg_return_5d, '10d': s.avg_return_10d, '20d': s.avg_return_20d}
        s.best_hold = max(period_returns, key=period_returns.get) if any(v > 0 for v in period_returns.values()) else "N/A"

        # Winner / loser averages
        winners = [r for r in trade_returns if r > 0]
        losers = [r for r in trade_returns if r < 0]
        s.avg_winner = round(sum(winners) / len(winners), 2) if winners else 0
        s.avg_loser = round(sum(losers) / len(losers), 2) if losers else 0

        # Expected Value: WR * avg_win + (1-WR) * avg_loss
        wr = len(winners) / len(trade_returns) if trade_returns else 0
        lr = len(losers) / len(trade_returns) if trade_returns else 0
        s.expected_value = round(wr * s.avg_winner + lr * s.avg_loser, 2)

        # Profit Factor: total gains / abs(total losses)
        total_gains = sum(r for r in trade_returns if r > 0)
        total_losses = abs(sum(r for r in trade_returns if r < 0))
        s.profit_factor = round(total_gains / total_losses, 2) if total_losses > 0 else (
            99.0 if total_gains > 0 else 0)

        # Max drawdown / best return
        s.max_drawdown = round(min(trade_returns), 2) if trade_returns else 0
        s.best_return = round(max(trade_returns), 2) if trade_returns else 0

        # Best / worst trades
        if tracked:
            best = max(tracked, key=lambda r: r.get('peak_return', 0))
            worst = min(tracked, key=lambda r: r.get('peak_return', 0))
            s.best_trade = f"${best['ticker']} +{best.get('peak_return', 0):.1f}%"
            s.worst_trade = f"${worst['ticker']} {worst.get('peak_return', 0):.1f}%"

        # Confidence by sample size
        if s.tracked >= 20:
            s.confidence = "HIGH"
        elif s.tracked >= 10:
            s.confidence = "MEDIUM"
        else:
            s.confidence = "LOW"

        # Composite Grade: balances EV, profit factor, win rate, drawdown
        # A: strong positive EV + good profit factor + manageable drawdown
        # F: negative EV or terrible profit factor
        s.grade = _compute_grade(s)
        stats[name] = s

    return stats


def _compute_grade(s: PatternStats) -> str:
    """
    Compute a letter grade (A-F) that balances EV, profit factor, and risk.

    Criteria:
    - A: EV >+2%, PF >2.0, WR >55%, max DD >-8%
    - B: EV >+1%, PF >1.5
    - C: EV >0%, PF >1.0 (marginally profitable)
    - D: EV ~0 or slightly negative, PF 0.7-1.0
    - F: Negative EV, PF <0.7
    """
    ev = s.expected_value
    pf = s.profit_factor
    wr = s.win_rate
    dd = s.max_drawdown

    # Not enough data — inconclusive
    if s.tracked < 5:
        return "?"

    # Grade A: strong edge
    if ev >= 2.0 and pf >= 1.8 and wr >= 0.50 and dd > -15:
        return "A"
    # Grade B: solid edge
    if ev >= 1.0 and pf >= 1.3 and wr >= 0.45:
        return "B"
    # Grade C: marginal edge (covers costs but tight)
    if ev >= 0.0 and pf >= 1.0:
        return "C"
    # Grade D: breakeven or slightly negative
    if ev >= -1.0 and pf >= 0.7:
        return "D"
    # Grade F: losing pattern
    return "F"


def get_pattern_conviction_bonus(pattern_name: str, stats: Dict[str, PatternStats]) -> float:
    """
    Get a conviction score bonus/penalty based on risk-adjusted grade.

    Uses the composite grade (EV + profit factor + drawdown), not just win rate.
    Returns: -1.5 to +2.0 bonus to add to pipeline conviction score.
    """
    s = stats.get(pattern_name)
    if not s or s.tracked < 5:
        return 0  # Not enough data

    grade = s.grade
    if grade == "A":
        return 2.0   # Strong edge — high conviction boost
    elif grade == "B":
        return 1.0   # Solid — moderate boost
    elif grade == "C":
        return 0.3   # Marginal — small boost
    elif grade == "D":
        return -0.5  # Breakeven — slight discount
    elif grade == "F":
        return -1.5  # Losing pattern — strong discount
    return 0


# ===================================================================
# Persistence
# ===================================================================

def load_pattern_db() -> List[dict]:
    """Load pattern performance database."""
    if not os.path.exists(PATTERN_DB_FILE):
        return []
    try:
        with open(PATTERN_DB_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def save_pattern_db(records: List[dict]):
    """Save pattern performance database."""
    with open(PATTERN_DB_FILE, 'w') as f:
        json.dump(records, f, indent=2, default=str)
    log.info(f"Saved {len(records)} pattern records to {PATTERN_DB_FILE}")


def ingest_patterns_from_discord(data_file: str, existing: List[dict]) -> List[dict]:
    """
    Scan Discord message cache and extract all pattern records.
    Deduplicates against existing records.
    """
    if not os.path.exists(data_file):
        return existing

    try:
        with open(data_file, 'r') as f:
            data = json.load(f)
    except Exception:
        return existing

    GOKU_SIGNAL_CHANNEL = "1393715240474247249"
    existing_ids = {r['record_id'] for r in existing}
    new_count = 0

    for channel_id, msgs in data.items():
        for msg in msgs:
            content = msg.get('content', '')
            if not content:
                continue

            tickers = re.findall(r'\$([A-Z]{1,5})\b', content)
            author = msg.get('author', {}).get('username', 'unknown') if isinstance(msg.get('author'), dict) else 'unknown'
            msg_id = msg.get('id', '')
            timestamp = msg.get('timestamp', '')

            for ticker in set(tickers[:5]):
                # Use raw signal parser for the direct buy/sell channel
                if channel_id == GOKU_SIGNAL_CHANNEL:
                    records = parse_raw_signal(content, author, channel_id, msg_id, timestamp)
                else:
                    records = extract_patterns(content, ticker, author, channel_id, msg_id, timestamp)

                for rec in records:
                    if rec.record_id not in existing_ids:
                        existing.append(rec.to_dict())
                        existing_ids.add(rec.record_id)
                        new_count += 1

    if new_count > 0:
        log.info(f"Ingested {new_count} new pattern records")
    return existing


# ===================================================================
# Discord Formatting
# ===================================================================

def format_pattern_stats_discord(stats: Dict[str, PatternStats]) -> str:
    """Format pattern stats for Discord — risk-adjusted view."""
    if not stats:
        return "No pattern data yet. Run `!learn` to start tracking."

    tracked_stats = [s for s in stats.values() if s.tracked >= 2]
    if not tracked_stats:
        return "Not enough tracked data yet. Patterns need 5+ days to show outcomes."

    # Sort by expected value (the real measure of profitability), then by grade
    grade_order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4, "?": 5}
    tracked_stats.sort(key=lambda s: (grade_order.get(s.grade, 5), -s.expected_value))

    lines = ["📊 **Pattern Scorecard** (" + str(len(tracked_stats)) + " patterns)\n"]
    lines.append("```")
    lines.append(f"  {'Pattern':<20} {'Grd':>3} {'EV':>6} {'PF':>5} {'WR':>5} {'#':>4} {'Hold':>5} {'MaxDD':>6}")
    lines.append("  " + "─" * 58)

    for s in tracked_stats:
        name = s.display_name[:19]
        ev_str = f"{s.expected_value:+.1f}%"
        pf_str = f"{s.profit_factor:.1f}" if s.profit_factor < 50 else ">50"
        dd_str = f"{s.max_drawdown:.0f}%" if s.max_drawdown != 0 else "0%"
        hold = s.best_hold if s.best_hold != "N/A" else "-"

        lines.append(
            f"  {name:<20} [{s.grade}] {ev_str:>5} {pf_str:>5}"
            f" {s.win_rate:>4.0%} {s.tracked:>4} {hold:>5} {dd_str:>6}"
        )

    lines.append("  " + "─" * 58)

    # Overall
    all_ev = [s.expected_value for s in tracked_stats if s.tracked >= 5]
    avg_ev = sum(all_ev) / len(all_ev) if all_ev else 0
    total_t = sum(s.tracked for s in tracked_stats)
    lines.append(f"  {'Avg (5+ trades)':<20}     {avg_ev:+.1f}%       {total_t:>4}")
    lines.append("```")

    # Legend
    lines.append(
        "Grade: [A] EV>2% PF>1.8 | [B] EV>1% PF>1.3 | [C] EV>0 | [D] breakeven | [F] losing\n"
        "EV = expected gain per trade | PF = profit factor | MaxDD = worst trade"
    )

    return "\n".join(lines)


def format_pattern_match_for_ticker(ticker: str, stats: Dict[str, PatternStats],
                                     messages: list = None) -> str:
    """
    For a given ticker, find which patterns appeared in its recent messages
    and show their historical win rates. Used by !analyze and @technical.

    Args:
        ticker: Stock ticker (e.g. "AAPL")
        stats: Pre-computed pattern stats dict
        messages: List of raw message dicts (from Discord cache)

    Returns: Formatted string for Discord, or empty string if no patterns found.
    """
    if not stats or not messages:
        return ""

    # Extract patterns from the messages for this ticker
    found_patterns = {}  # pattern_name -> {count, direction, snippets}
    for msg in messages:
        content = msg.get('content', '')
        if not content:
            continue
        author = msg.get('author', {}).get('username', '') if isinstance(msg.get('author'), dict) else str(msg.get('author', ''))

        records = extract_patterns(content, ticker)
        for rec in records:
            name = rec.pattern_name
            if name not in found_patterns:
                found_patterns[name] = {
                    'display': rec.pattern_display,
                    'direction': rec.direction,
                    'count': 0,
                    'authors': set(),
                }
            found_patterns[name]['count'] += 1
            if author:
                found_patterns[name]['authors'].add(author)

    if not found_patterns:
        return ""

    lines = [f"\n🧬 *Pattern Library Match* (${ticker})\n```"]
    lines.append(f"  {'Pattern':<20} {'Grd':>3} {'EV':>6} {'WR':>5} {'Hold':>5} {'#Msgs':>5}")
    lines.append("  " + "─" * 48)

    for name, info in sorted(found_patterns.items(), key=lambda x: -x[1]['count']):
        s = stats.get(name)
        if s and s.tracked >= 3:
            ev_str = f"{s.expected_value:+.1f}%"
            wr_str = f"{s.win_rate:.0%}"
            hold = s.best_hold if s.best_hold != "N/A" else "-"
            grade = s.grade
        else:
            ev_str = "N/A"
            wr_str = "N/A"
            hold = "?"
            grade = "?"

        display = info['display'][:19]
        lines.append(f"  {display:<20} [{grade}] {ev_str:>5} {wr_str:>5} {hold:>5} {info['count']:>5}")

    lines.append("```")

    # Add a one-line recommendation based on grade + EV
    best = None
    best_ev = -999
    for name, info in found_patterns.items():
        s = stats.get(name)
        if s and s.tracked >= 5 and s.expected_value > best_ev:
            best = s
            best_ev = s.expected_value

    if best:
        hold = best.best_hold if best.best_hold != "N/A" else "5-10d"
        if best.grade in ("A", "B"):
            lines.append(
                f"💡 *{best.display_name}* [{best.grade}] "
                f"EV {best.expected_value:+.1f}% | PF {best.profit_factor:.1f} → hold ~{hold}"
            )
        elif best.grade == "C":
            lines.append(
                f"📌 *{best.display_name}* [C] marginal edge "
                f"(EV {best.expected_value:+.1f}%) → tight stop needed"
            )
        else:
            lines.append(
                f"⚠️ *{best.display_name}* [{best.grade}] "
                f"weak/negative EV ({best.expected_value:+.1f}%) → caution"
            )

    return "\n".join(lines)
