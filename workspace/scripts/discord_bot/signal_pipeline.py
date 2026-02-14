"""
Signal Pipeline — Auto-Trading Orchestrator
=============================================
Phases:
  1. Collect signals from Discord, scanner, signal_engine
  2. Analyze & rank candidates by trade worthiness
  3. Generate nightly digest for Discord review
  4. Generate orders after approval
  5. Portfolio management suggestions

Architecture:
  This module is imported by interactive_bot.py.
  It uses the GCloud Trading GUI API for scanner analysis
  and IBKR Gateway /api/quotes for real-time prices (yfinance fallback).
"""

import os
import sys
import json
import re
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple

log = logging.getLogger("signal-pipeline")

# Try to import trader grades for adaptive scoring
try:
    from signal_tracker import load_grades
except ImportError:
    def load_grades():
        return {}

# Try to import pattern library for pattern-level scoring
try:
    from pattern_library import (
        extract_patterns, compute_pattern_stats, load_pattern_db,
        get_pattern_conviction_bonus
    )
    HAS_PATTERNS = True
except ImportError:
    HAS_PATTERNS = False

# Paths
DATA_DIR = os.path.join(os.path.dirname(__file__), '../../data')
PIPELINE_FILE = os.path.join(DATA_DIR, 'pipeline_candidates.json')
APPROVED_FILE = os.path.join(DATA_DIR, 'approved_trades.json')

# Ensure data dir exists
os.makedirs(DATA_DIR, exist_ok=True)


# ===================================================================
# Data classes
# ===================================================================

@dataclass
class SignalCandidate:
    """A ticker candidate with aggregated signals and analysis."""
    ticker: str
    # Signal sources
    discord_mentions: int = 0
    discord_bullish: int = 0
    discord_bearish: int = 0
    discord_snippets: List[str] = field(default_factory=list)
    discord_authors: List[str] = field(default_factory=list)
    discord_has_images: bool = False
    discord_patterns: List[str] = field(default_factory=list)  # Detected patterns
    trader_grade_boost: float = 0    # Extra score from trader grades
    pattern_boost: float = 0         # Extra score from pattern win rates
    # Scanner data
    scanner_action: str = ""       # BUY / WAIT / SELL
    scanner_regime: str = ""
    scanner_strategy: str = ""
    scanner_buy_low: float = 0
    scanner_buy_high: float = 0
    scanner_stop: float = 0
    scanner_target_1: float = 0
    scanner_target_2: float = 0
    scanner_ev: float = 0
    scanner_rr: float = 0
    scanner_win_rate: float = 0
    scanner_position_pct: float = 0
    # Price data
    current_price: float = 0
    rsi: float = 0
    ema8: float = 0
    ema21: float = 0
    ema50: float = 0
    # Composite scores
    conviction_score: float = 0    # 0-10 overall score
    action: str = ""               # BUY / SELL / WAIT / NO_TRADE
    reasoning: str = ""
    # Metadata
    timestamp: str = ""

    def to_dict(self):
        return asdict(self)


# ===================================================================
# Phase 1: Signal Collection
# ===================================================================

async def collect_discord_signals(data_file: str, days: int = 3) -> Dict[str, Dict]:
    """
    Collect recent Discord signals grouped by ticker.

    Returns: {ticker: {mentions, bullish, bearish, snippets}}
    """
    if not os.path.exists(data_file):
        return {}

    with open(data_file, 'r') as f:
        data = json.load(f)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ticker_signals = {}

    # Bullish/bearish keywords for simple sentiment detection
    bullish_kw = {'buy', 'long', 'bullish', 'calls', 'breakout', 'bounce',
                  'support', 'accumulate', 'dip buy', 'green', 'moon', 'rip',
                  'gap up', 'higher', 'rally', 'upside', 'target'}
    bearish_kw = {'sell', 'short', 'bearish', 'puts', 'breakdown', 'crash',
                  'resistance', 'dump', 'red', 'tank', 'fade', 'lower',
                  'gap down', 'downside', 'cut', 'stop out'}

    for channel_id, msgs in data.items():
        for msg in msgs:
            content = msg.get('content', '')
            ts = msg.get('timestamp', '')
            author = msg.get('author', {}).get('username', 'unknown') if isinstance(msg.get('author'), dict) else 'unknown'
            has_images = bool(msg.get('attachments') or msg.get('embeds'))
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if dt < cutoff:
                    continue
            except Exception:
                continue

            tickers = re.findall(r'\$([A-Z]{1,5})\b', content)
            if not tickers:
                continue

            content_lower = content.lower()
            is_bullish = any(kw in content_lower for kw in bullish_kw)
            is_bearish = any(kw in content_lower for kw in bearish_kw)

            for ticker in set(tickers[:5]):
                if ticker not in ticker_signals:
                    ticker_signals[ticker] = {
                        'mentions': 0, 'bullish': 0, 'bearish': 0,
                        'snippets': [], 'authors': [], 'has_images': False,
                    }
                sig = ticker_signals[ticker]
                sig['mentions'] += 1
                if is_bullish:
                    sig['bullish'] += 1
                if is_bearish:
                    sig['bearish'] += 1
                if has_images:
                    sig['has_images'] = True
                if author not in sig['authors']:
                    sig['authors'].append(author)
                snippet = content[:120].replace('\n', ' ')
                if len(sig['snippets']) < 3:
                    sig['snippets'].append(snippet)

                # Extract technical patterns (C&H, flag, breakout, etc.)
                if HAS_PATTERNS and 'patterns' not in sig:
                    sig['patterns'] = []
                if HAS_PATTERNS:
                    pats = extract_patterns(content, ticker)
                    for p in pats:
                        pname = p.pattern_name
                        if pname not in sig['patterns']:
                            sig['patterns'].append(pname)

    return ticker_signals


async def collect_scanner_signals(call_api_fn, tickers: List[str]) -> Dict[str, Dict]:
    """
    Run scanner analysis on tickers via GCloud /api/analyze endpoint.

    Args:
        call_api_fn: async function to call the trading API
        tickers: list of ticker symbols to scan

    Returns: {ticker: {scan data}}
    """
    results = {}
    for ticker in tickers:
        try:
            data = await call_api_fn(f"/api/analyze/{ticker}")
            if data and data.get("success"):
                results[ticker] = data
            await asyncio.sleep(1)  # Rate limit between scans
        except Exception as e:
            log.warning(f"Scanner error for {ticker}: {e}")
    return results


TRADE_API_URL = os.environ.get("TRADE_API_URL", "http://34.75.9.166:8080")
TRADE_API_KEY = os.environ.get("TRADE_API_KEY", "saiyan-trade-2026")


def get_ibkr_quotes(tickers: List[str]) -> Dict[str, float]:
    """Batch-fetch current prices from IBKR via GCloud /api/quotes."""
    import requests
    try:
        resp = requests.post(
            f"{TRADE_API_URL}/api/quotes",
            json={"symbols": tickers[:25]},
            headers={"X-API-Key": TRADE_API_KEY, "Content-Type": "application/json"},
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {k: v for k, v in data.items() if isinstance(v, (int, float)) and v > 0}
    except Exception as e:
        log.warning(f"IBKR quotes error: {e}")
    return {}


def get_price_context(ticker: str) -> Dict:
    """Get current price from IBKR first, yfinance fallback."""
    # Try IBKR
    ibkr = get_ibkr_quotes([ticker])
    if ticker in ibkr:
        return {'current_price': ibkr[ticker]}

    # Fallback: yfinance via llm_analyzer
    try:
        sys.path.append(os.path.join(os.path.dirname(__file__), '../core_analysis'))
        from llm_analyzer import get_stock_context
        ctx = get_stock_context(ticker)
        if ctx and 'error' not in ctx:
            return ctx
    except Exception as e:
        log.warning(f"Price context error for {ticker}: {e}")
    return {}


# ===================================================================
# Phase 2: Signal Analysis & Ranking
# ===================================================================

def build_candidates(
    discord_signals: Dict[str, Dict],
    scanner_results: Dict[str, Dict],
) -> List[SignalCandidate]:
    """
    Merge all signal sources into ranked SignalCandidate list.
    Uses trader grades from signal_tracker for adaptive scoring.
    """
    # Load trader performance grades for adaptive scoring
    trader_grades = load_grades()

    # Load pattern stats for pattern-level scoring
    pat_stats = {}
    if HAS_PATTERNS:
        try:
            pat_records = load_pattern_db()
            pat_stats = compute_pattern_stats(pat_records)
        except Exception:
            pat_stats = {}

    # Combine all tickers from both sources
    all_tickers = set(list(discord_signals.keys()) + list(scanner_results.keys()))

    candidates = []
    for ticker in all_tickers:
        c = SignalCandidate(ticker=ticker, timestamp=datetime.now(timezone.utc).isoformat())

        # Discord signals
        dsig = discord_signals.get(ticker, {})
        c.discord_mentions = dsig.get('mentions', 0)
        c.discord_bullish = dsig.get('bullish', 0)
        c.discord_bearish = dsig.get('bearish', 0)
        c.discord_snippets = dsig.get('snippets', [])
        c.discord_authors = dsig.get('authors', [])
        c.discord_has_images = dsig.get('has_images', False)

        # Compute trader grade boost from historical performance
        if trader_grades and c.discord_authors:
            multipliers = [trader_grades.get(a, 1.0) for a in c.discord_authors]
            avg_mult = sum(multipliers) / len(multipliers)
            c.trader_grade_boost = (avg_mult - 1.0) * 2  # -1 to +1 range

        # Extract and score patterns
        c.discord_patterns = dsig.get('patterns', [])
        if pat_stats and c.discord_patterns:
            bonuses = [get_pattern_conviction_bonus(p, pat_stats) for p in c.discord_patterns]
            c.pattern_boost = max(bonuses) if bonuses else 0  # Use best pattern's bonus

        # Scanner data
        sdata = scanner_results.get(ticker, {})
        scan = sdata.get('scan', {})
        tech = sdata.get('technical', {})

        if scan:
            c.scanner_action = scan.get('action', '')
            c.scanner_regime = scan.get('regime', '')
            c.scanner_strategy = scan.get('strategy', '')
            c.scanner_buy_low = scan.get('buy_zone_low') or 0
            c.scanner_buy_high = scan.get('buy_zone_high') or 0
            c.scanner_stop = scan.get('stop_loss') or 0
            c.scanner_target_1 = scan.get('target_1') or 0
            c.scanner_target_2 = scan.get('target_2') or 0
            c.scanner_ev = scan.get('expected_value') or 0
            c.scanner_rr = scan.get('risk_reward') or 0
            c.scanner_win_rate = scan.get('win_rate') or 0
            c.scanner_position_pct = scan.get('position_size_pct') or 0

        if tech:
            c.rsi = tech.get('rsi') or 0
            c.current_price = tech.get('price') or scan.get('price', 0)

        candidates.append(c)

    # Batch-fetch prices from IBKR for tickers missing prices
    need_prices = [c.ticker for c in candidates if c.current_price == 0]
    if need_prices:
        ibkr_prices = get_ibkr_quotes(need_prices)
        for c in candidates:
            if c.current_price == 0 and c.ticker in ibkr_prices:
                c.current_price = ibkr_prices[c.ticker]

    # Final scoring pass
    for c in candidates:
        c.conviction_score = _compute_conviction(c)
        c.action = _determine_action(c)

    # Sort by conviction score descending
    candidates.sort(key=lambda x: x.conviction_score, reverse=True)
    return candidates


def _compute_conviction(c: SignalCandidate) -> float:
    """
    Compute conviction score (0-10) from all signal components.
    """
    score = 0.0

    # Discord signal strength (0-3 points)
    if c.discord_mentions >= 5:
        score += 2.0
    elif c.discord_mentions >= 3:
        score += 1.5
    elif c.discord_mentions >= 1:
        score += 0.5

    if c.discord_bullish > c.discord_bearish:
        score += min((c.discord_bullish - c.discord_bearish) * 0.5, 1.0)
    elif c.discord_bearish > c.discord_bullish:
        score -= 0.5  # Negative for bearish bias

    # Scanner signals (0-4 points)
    action = c.scanner_action.upper()
    if 'BUY' in action:
        score += 2.5
    elif 'WAIT' in action:
        score += 1.0
    # No points for SELL/empty

    if c.scanner_ev > 0:
        score += min(c.scanner_ev / 2, 1.0)  # Up to 1 point for positive EV
    if c.scanner_rr >= 2:
        score += 0.5

    # Technical alignment (0-3 points)
    if 30 < c.rsi < 60:  # Healthy RSI range for entries
        score += 1.0
    elif c.rsi <= 30:  # Oversold — potential bounce
        score += 0.5

    if c.current_price > 0 and c.scanner_buy_low > 0:
        # Price near buy zone = better
        if c.scanner_buy_low <= c.current_price <= c.scanner_buy_high:
            score += 1.5  # In the buy zone!
        elif c.current_price < c.scanner_buy_low * 1.02:
            score += 0.5  # Close to buy zone

    # Trader grade boost (learned from historical performance)
    score += c.trader_grade_boost  # -1 to +1 from signal_tracker grades

    # Pattern win rate boost (learned from pattern_library)
    score += c.pattern_boost       # -1 to +1.5 from pattern performance

    # Image bonus — signals with chart images tend to be higher conviction
    if c.discord_has_images:
        score += 0.3

    return max(0, min(score, 10))


def _determine_action(c: SignalCandidate) -> str:
    """Determine recommended action based on conviction and signals."""
    if c.conviction_score >= 6.0:
        return "BUY"
    elif c.conviction_score >= 4.0:
        return "WAIT"
    elif c.discord_bearish > c.discord_bullish * 2:
        return "AVOID"
    else:
        return "NO_TRADE"


# ===================================================================
# Phase 3: Nightly Digest
# ===================================================================

def format_nightly_digest(candidates: List[SignalCandidate], top_n: int = 5) -> List[str]:
    """
    Format top candidates into Discord messages for nightly review.
    Returns list of message strings (may need multiple messages for Discord limit).
    """
    top = [c for c in candidates if c.conviction_score >= 3.0][:top_n]

    if not top:
        return ["📭 No actionable candidates found today. All signals below threshold."]

    messages = []

    # Header message
    header = (
        f"*Nightly Signal Digest* — {datetime.now().strftime('%b %d, %Y')}\n"
        f"Analyzed {len(candidates)} tickers from Discord + Scanner\n"
        f"Top {len(top)} candidates below:\n"
        f"{'─' * 40}"
    )
    messages.append(header)

    # Each candidate
    for i, c in enumerate(top, 1):
        icon = {"BUY": "🟢", "WAIT": "🟡", "AVOID": "🔴"}.get(c.action, "⚪")

        lines = [f"\n{icon} *#{i} ${c.ticker}* — {c.action} (Score: {c.conviction_score:.1f}/10)"]

        if c.current_price:
            lines.append(f"💰 Price: ${c.current_price:.2f} | RSI: {c.rsi:.0f}")

        if c.scanner_buy_low and c.scanner_buy_high:
            lines.append(f"🎯 Buy Zone: ${c.scanner_buy_low:.2f} – ${c.scanner_buy_high:.2f}")
        if c.scanner_stop:
            lines.append(f"🛑 Stop: ${c.scanner_stop:.2f}")
        if c.scanner_target_1:
            tgt = f"${c.scanner_target_1:.2f}"
            if c.scanner_target_2:
                tgt += f" / ${c.scanner_target_2:.2f}"
            lines.append(f"✅ Targets: {tgt}")

        if c.scanner_ev:
            lines.append(f"⚖️ EV: {c.scanner_ev:.1f}% | R:R {c.scanner_rr:.1f}:1 | WR {c.scanner_win_rate * 100:.0f}%")

        if c.scanner_regime:
            lines.append(f"📊 Regime: {c.scanner_regime} | Strategy: {c.scanner_strategy or 'N/A'}")

        if c.discord_mentions:
            lines.append(f"📡 Discord: {c.discord_mentions} mentions ({c.discord_bullish} bull / {c.discord_bearish} bear)")

        if c.discord_snippets:
            lines.append(f"💬 _{c.discord_snippets[0][:80]}_...")

        if c.scanner_position_pct:
            lines.append(f"📐 Suggested size: {c.scanner_position_pct * 100:.1f}% of portfolio")

        lines.append(f"\n💡 To approve: `!approve {c.ticker}`")

        messages.append("\n".join(lines))

    # Footer
    footer = (
        f"\n{'─' * 40}\n"
        f"📋 *Commands:*\n"
        f"• `!approve TICKER` — Queue limit order at buy zone\n"
        f"• `!approve TICKER market` — Queue market order\n"
        f"• `!portfolio` — Position management suggestions\n"
        f"• `!analyze TICKER` — Deep dive on any ticker"
    )
    messages.append(footer)

    return messages


def format_llm_digest_prompt(candidates: List[SignalCandidate], top_n: int = 8) -> str:
    """Build LLM prompt for enhanced nightly digest analysis."""
    top = candidates[:top_n]
    blocks = []
    for c in top:
        block = (
            f"${c.ticker}: Score={c.conviction_score:.1f}, "
            f"Price=${c.current_price:.2f}, RSI={c.rsi:.0f}, "
            f"Action={c.scanner_action}, Regime={c.scanner_regime}, "
            f"BuyZone=${c.scanner_buy_low:.2f}-${c.scanner_buy_high:.2f}, "
            f"Stop=${c.scanner_stop:.2f}, EV={c.scanner_ev:.1f}%, R:R={c.scanner_rr:.1f}, "
            f"Discord={c.discord_mentions} mentions ({c.discord_bullish}B/{c.discord_bearish}S)"
        )
        if c.discord_snippets:
            block += f"\n  Snippet: {c.discord_snippets[0][:100]}"
        blocks.append(block)

    return f"""You are a senior portfolio manager. Review tonight's signal candidates and provide
a concise trade plan for Discord (<1800 chars).

CANDIDATES:
{chr(10).join(blocks)}

For each actionable ticker:
🟢 $TICKER — BUY: entry zone, stop, target, position size, why
🟡 $TICKER — WAIT: what trigger to watch
🔴 $TICKER — AVOID: why

End with 1-line market outlook. Use single asterisks for bold. Be specific with prices."""


# ===================================================================
# Phase 4: Order Generation
# ===================================================================

def generate_order_params(candidate: SignalCandidate, order_type: str = "limit") -> Dict:
    """
    Generate trade API parameters from a candidate.

    Returns dict ready to POST to /api/trade.
    """
    if order_type == "market":
        return {
            "ticker": candidate.ticker,
            "action": candidate.action if candidate.action in ("BUY", "SELL") else "BUY",
            "limit_price": 0,
            "order_type": "MKT",
            "source": "pipeline-auto",
            "position_size_pct": candidate.scanner_position_pct or 0.02,
        }

    # Limit order at buy zone midpoint
    entry_price = (candidate.scanner_buy_low + candidate.scanner_buy_high) / 2 if candidate.scanner_buy_low else candidate.current_price
    stop_price = candidate.scanner_stop or (entry_price * 0.97)
    target_price = candidate.scanner_target_1 or (entry_price * 1.06)

    return {
        "ticker": candidate.ticker,
        "action": candidate.action if candidate.action in ("BUY", "SELL") else "BUY",
        "order_type": "LMT",
        "limit_price": round(entry_price, 2),
        "stop_loss": round(stop_price, 2),
        "target": round(target_price, 2),
        "source": "pipeline-auto",
        "position_size_pct": candidate.scanner_position_pct or 0.02,
    }


def save_candidates(candidates: List[SignalCandidate]):
    """Save pipeline candidates to file for later retrieval."""
    data = [c.to_dict() for c in candidates]
    with open(PIPELINE_FILE, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    log.info(f"Saved {len(candidates)} candidates to {PIPELINE_FILE}")


def load_candidates() -> List[SignalCandidate]:
    """Load saved pipeline candidates."""
    if not os.path.exists(PIPELINE_FILE):
        return []
    try:
        with open(PIPELINE_FILE, 'r') as f:
            data = json.load(f)
        candidates = []
        for d in data:
            d.pop('timestamp', None)  # Handle missing field gracefully
            c = SignalCandidate(
                ticker=d.get('ticker', ''),
                timestamp=d.get('timestamp', ''),
            )
            for k, v in d.items():
                if hasattr(c, k) and k != 'ticker':
                    setattr(c, k, v)
            candidates.append(c)
        return candidates
    except Exception as e:
        log.warning(f"Failed to load candidates: {e}")
        return []


def save_approved(ticker: str, order_params: Dict):
    """Save an approved trade for execution."""
    approved = []
    if os.path.exists(APPROVED_FILE):
        try:
            with open(APPROVED_FILE, 'r') as f:
                approved = json.load(f)
        except Exception:
            approved = []

    approved.append({
        "ticker": ticker,
        "params": order_params,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    })

    with open(APPROVED_FILE, 'w') as f:
        json.dump(approved, f, indent=2)
    log.info(f"Approved trade for {ticker} saved")


# ===================================================================
# Phase 5: Portfolio Management
# ===================================================================

def format_portfolio_suggestions(positions: List[Dict], candidates: List[SignalCandidate]) -> str:
    """
    Generate portfolio management suggestions based on current positions
    and today's signal candidates.
    """
    if not positions:
        return "📭 No open positions. Use `!daily` to see today's candidates."

    candidate_map = {c.ticker: c for c in candidates}
    lines = [f"*Portfolio Review* — {datetime.now().strftime('%b %d')}\n"]

    total_pnl = 0
    suggestions = []

    for pos in positions:
        symbol = pos.get('symbol', '')
        qty = pos.get('quantity', 0)
        avg = pos.get('avg_cost', 0)
        mkt = pos.get('market_price', 0)
        pnl = pos.get('pnl', 0)
        total_pnl += pnl

        if avg <= 0 or mkt <= 0:
            continue

        pnl_pct = (mkt / avg - 1) * 100
        direction = "LONG" if qty > 0 else "SHORT"

        # Generate suggestion based on P&L and signals
        suggestion = ""
        icon = "🟢" if pnl >= 0 else "🔴"

        # Check if we have fresh signals for this position
        cand = candidate_map.get(symbol)

        if pnl_pct > 15:
            suggestion = "Consider trimming 30-50%. Protect profits."
            icon = "💰"
        elif pnl_pct > 8:
            suggestion = "Raise stop to breakeven. Let it run."
            icon = "📈"
        elif pnl_pct < -8:
            suggestion = "Review thesis. Consider cutting if broken."
            icon = "⚠️"
        elif pnl_pct < -15:
            suggestion = "EXIT — thesis likely broken at -15%."
            icon = "🚨"
        elif cand and cand.action == "BUY" and pnl_pct > -3:
            suggestion = "Signal still bullish. Consider adding."
            icon = "➕"
        else:
            suggestion = "Hold. Monitor for trigger."
            icon = "📊"

        lines.append(
            f"{icon} *{symbol}*: {direction} {abs(qty)} @ ${avg:.2f} → ${mkt:.2f} "
            f"({pnl_pct:+.1f}%)\n"
            f"   💡 {suggestion}"
        )

    # Add total P&L
    total_icon = "🟢" if total_pnl >= 0 else "🔴"
    total_str = f"+${total_pnl:,.0f}" if total_pnl >= 0 else f"-${abs(total_pnl):,.0f}"
    lines.append(f"\n{total_icon} *Total Unrealized P&L:* {total_str}")

    # Suggest new positions from candidates not currently held
    held_symbols = {p.get('symbol') for p in positions}
    new_ideas = [c for c in candidates if c.action == "BUY" and c.ticker not in held_symbols][:3]
    if new_ideas:
        lines.append("\n*New Ideas (not held):*")
        for c in new_ideas:
            lines.append(
                f"🆕 *${c.ticker}* — Score {c.conviction_score:.1f}/10, "
                f"Buy ${c.scanner_buy_low:.2f}-${c.scanner_buy_high:.2f}"
            )

    # Options suggestions for large positions
    for pos in positions:
        mkt_val = pos.get('market_value', 0)
        pnl_pct = 0
        if pos.get('avg_cost', 0) > 0 and pos.get('market_price', 0) > 0:
            pnl_pct = (pos['market_price'] / pos['avg_cost'] - 1) * 100
        if mkt_val > 10000 and pnl_pct > 5 and abs(pos.get('quantity', 0)) >= 100:
            lines.append(
                f"\n🔒 *Options idea for {pos['symbol']}:* "
                f"Sell covered call (30-45 DTE, ~0.30 delta) to generate income "
                f"while holding {abs(pos['quantity'])} shares."
            )

    return "\n".join(lines)


# ===================================================================
# Main Pipeline Orchestrator
# ===================================================================

def _load_universe_tickers(max_tickers: int = 80) -> List[str]:
    """Load prioritized stock universe from YAML config.

    Returns up to max_tickers unique symbols from high_conviction, momentum,
    themed sectors, and the full all_symbols list.
    """
    universe_file = os.path.join(os.path.dirname(__file__), '../../../config/stock_universe.yaml')
    try:
        import yaml
        with open(universe_file, 'r') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        log.warning(f"Could not load stock universe: {e}")
        return []

    seen: set = set()
    ordered: List[str] = []

    def _add(symbols):
        for sym in symbols:
            s = str(sym).upper().strip('"')
            if s not in seen and len(s) >= 1:
                seen.add(s)
                ordered.append(s)

    # Priority lists
    for key in ['high_conviction', 'volatile_momentum', 'quick_test']:
        _add(data.get(key, []))

    # Priority themes
    themes = data.get('themes', {})
    for tkey in ['mag7', 'ai_chips', 'ai_software', 'momentum_leaders',
                 'fintech', 'cybersecurity', 'clean_energy', 'crypto',
                 'wilson', 'saiyan', 'moonvest']:
        _add(themes.get(tkey, {}).get('symbols', []))

    # Fill from all_symbols
    _add(data.get('all_symbols', []))

    return ordered[:max_tickers]


# Candidates history directory for dated snapshots
CANDIDATES_HISTORY_DIR = os.path.join(DATA_DIR, 'candidates_history')
os.makedirs(CANDIDATES_HISTORY_DIR, exist_ok=True)


def _save_candidates_with_history(candidates: List[SignalCandidate]):
    """Save candidates to live file AND a dated history file for replay/audit."""
    save_candidates(candidates)

    date_str = datetime.now().strftime('%Y%m%d_%H%M')
    history_file = os.path.join(CANDIDATES_HISTORY_DIR, f'candidates_{date_str}.json')
    data = [c.to_dict() for c in candidates]
    with open(history_file, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    log.info(f"Saved candidates history to {history_file}")


async def run_full_pipeline(
    data_file: str,
    call_api_fn,
    llm_fn=None,
    discord_days: int = 3,
    universe_size: int = 80,
) -> Tuple[List[SignalCandidate], List[str]]:
    """
    Run the full signal pipeline:
      1. Collect Discord signals
      2. Load universe tickers (50-100) + merge with Discord mentions
      3. Run scanner on merged list in batches
      4. Build & rank candidates
      5. Save with dated timestamp
      6. Generate digest messages

    Args:
        data_file: path to Discord signals cache
        call_api_fn: async function to call GCloud API
        llm_fn: optional LLM function for enhanced analysis
        discord_days: days of Discord history to analyze
        universe_size: max tickers from universe config (default 80)

    Returns:
        (candidates, digest_messages)
    """
    log.info("Pipeline: Phase 1 — Collecting signals")

    # Phase 1a: Discord signals
    discord_signals = await collect_discord_signals(data_file, days=discord_days)
    log.info(f"  Discord: {len(discord_signals)} tickers with signals")

    # Phase 1b: Build scan list — Discord tickers (sorted by mentions) + universe
    top_discord = sorted(
        discord_signals.items(),
        key=lambda x: x[1]['mentions'],
        reverse=True
    )
    discord_tickers = [t for t, _ in top_discord]

    # Load universe tickers (prioritized)
    universe_tickers = _load_universe_tickers(universe_size)
    log.info(f"  Universe: {len(universe_tickers)} tickers loaded")

    # Merge: Discord first (they have human signal), then universe fill (dedup)
    seen = set()
    tickers_to_scan = []
    for t in discord_tickers + universe_tickers:
        if t not in seen:
            seen.add(t)
            tickers_to_scan.append(t)

    log.info(f"Pipeline: Phase 2 — Scanning {len(tickers_to_scan)} tickers via GCloud")

    # Scan in batches of 10 with rate limiting
    scanner_results = {}
    batch_size = 10
    for i in range(0, len(tickers_to_scan), batch_size):
        batch = tickers_to_scan[i:i + batch_size]
        batch_results = await collect_scanner_signals(call_api_fn, batch)
        scanner_results.update(batch_results)
        scanned = min(i + batch_size, len(tickers_to_scan))
        log.info(f"  Scanned {scanned}/{len(tickers_to_scan)} — {len(scanner_results)} results so far")
        if i + batch_size < len(tickers_to_scan):
            await asyncio.sleep(0.5)  # Courtesy delay between batches

    log.info(f"  Scanner: {len(scanner_results)} tickers analyzed total")

    # Phase 3: Build & rank candidates
    log.info("Pipeline: Phase 3 — Ranking candidates")
    candidates = build_candidates(discord_signals, scanner_results)
    _save_candidates_with_history(candidates)
    log.info(f"  Ranked {len(candidates)} candidates")

    # Phase 4: Generate digest
    log.info("Pipeline: Phase 4 — Generating digest")

    if llm_fn:
        # Try LLM-enhanced digest
        try:
            prompt = format_llm_digest_prompt(candidates)
            response = llm_fn(prompt, 2000, {}, "PIPELINE", [])
            content = response.get("raw_response") or response.get("raw_llm") or ""
            if content:
                content = content.replace("```", "").replace("**", "*")
                digest_messages = [content[:1900]]
            else:
                digest_messages = format_nightly_digest(candidates)
        except Exception as e:
            log.warning(f"LLM digest failed: {e}")
            digest_messages = format_nightly_digest(candidates)
    else:
        digest_messages = format_nightly_digest(candidates)

    log.info(f"Pipeline complete: {len(candidates)} candidates, {len(digest_messages)} messages")
    return candidates, digest_messages
