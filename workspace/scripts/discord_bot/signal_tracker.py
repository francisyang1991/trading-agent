"""
Signal Tracker — Self-Learning Trading Intelligence
=====================================================
Tracks signal outcomes, grades traders/channels, and adapts
conviction scoring based on real performance data.

Flow:
  1. CAPTURE: When a signal is detected, record ticker + price at signal time
  2. TRACK:   After N days (1, 3, 5, 10, 20), record actual price outcomes
  3. GRADE:   Score each trader/channel by hit rate, avg return, consistency
  4. ADAPT:   Feed grades into pipeline scoring (boost high-accuracy sources)
  5. IMAGE:   Download chart images from Discord, send to LLM for analysis

Data stored in: workspace/data/signal_performance.json
"""

import os
import sys
import json
import re
import base64
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

log = logging.getLogger("signal-tracker")

# Import pattern library for deep pattern analysis
try:
    from pattern_library import (
        ingest_patterns_from_discord, load_pattern_db, save_pattern_db,
        compute_pattern_stats, format_pattern_stats_discord, TRACK_PERIODS as PAT_PERIODS
    )
    HAS_PATTERN_LIB = True
except ImportError:
    HAS_PATTERN_LIB = False

# Paths
DATA_DIR = os.path.join(os.path.dirname(__file__), '../../data')
PERF_FILE = os.path.join(DATA_DIR, 'signal_performance.json')
GRADES_FILE = os.path.join(DATA_DIR, 'trader_grades.json')
IMAGE_DIR = os.path.join(DATA_DIR, 'signal_images')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)

# Tracking periods (days)
TRACK_PERIODS = [1, 3, 5, 10, 20]


# ===================================================================
# Signal Record — one per signal detected
# ===================================================================

@dataclass
class SignalRecord:
    """A single signal with its outcome tracking."""
    # Identity
    signal_id: str = ""           # channel_id + message_id
    ticker: str = ""
    direction: str = ""           # BULL / BEAR / NEUTRAL
    signal_date: str = ""         # ISO timestamp
    # Source
    channel_id: str = ""
    author: str = ""
    author_id: str = ""
    source_name: str = ""         # Goku / Wilson
    content_snippet: str = ""
    # Price at signal time
    price_at_signal: float = 0
    rsi_at_signal: float = 0
    # Outcomes (filled in over time)
    price_1d: float = 0
    price_3d: float = 0
    price_5d: float = 0
    price_10d: float = 0
    price_20d: float = 0
    return_1d: float = 0         # % return
    return_3d: float = 0
    return_5d: float = 0
    return_10d: float = 0
    return_20d: float = 0
    # Computed
    is_winner: bool = False       # True if best return > 2%
    is_loser: bool = False        # True if worst return < -5%
    peak_return: float = 0        # Best return across all periods
    worst_return: float = 0       # Worst return
    last_tracked: str = ""        # Last time we checked prices
    # Images
    image_urls: List[str] = field(default_factory=list)
    image_analysis: str = ""      # LLM analysis of chart images

    def to_dict(self):
        return asdict(self)


# ===================================================================
# 1. Signal Capture — called when scraping Discord messages
# ===================================================================

BULLISH_KW = {'buy', 'long', 'bullish', 'calls', 'breakout', 'bounce',
              'support', 'accumulate', 'dip buy', 'rip', 'gap up', 'rally',
              'upside', 'target', 'moon', 'higher', 'adding'}
BEARISH_KW = {'sell', 'short', 'bearish', 'puts', 'breakdown', 'crash',
              'resistance', 'dump', 'fade', 'lower', 'gap down', 'downside',
              'cut', 'stop out', 'exit'}

GOKU_CHANNELS = {"1277321989874385029", "1411717415565393970", "1227315745352847461"}
WILSON_CHANNELS = {"1211549165629476924"}


def detect_direction(content: str) -> str:
    """Detect signal direction from message content."""
    lower = content.lower()
    bull = sum(1 for kw in BULLISH_KW if kw in lower)
    bear = sum(1 for kw in BEARISH_KW if kw in lower)
    if bull > bear:
        return "BULL"
    elif bear > bull:
        return "BEAR"
    return "NEUTRAL"


def extract_signals_from_message(msg: dict, channel_id: str) -> List[SignalRecord]:
    """
    Extract SignalRecords from a raw Discord API message object.
    One record per ticker mentioned in the message.
    """
    content = msg.get('content', '')
    if not content:
        return []

    tickers = re.findall(r'\$([A-Z]{1,5})\b', content)
    if not tickers:
        return []

    direction = detect_direction(content)
    author = msg.get('author', {}).get('username', 'unknown')
    author_id = msg.get('author', {}).get('id', '')
    timestamp = msg.get('timestamp', '')
    msg_id = msg.get('id', '')

    # Determine source name
    source = "Goku" if channel_id in GOKU_CHANNELS else "Wilson" if channel_id in WILSON_CHANNELS else "Other"

    # Extract image URLs from attachments and embeds
    image_urls = []
    for att in msg.get('attachments', []):
        url = att.get('url', '')
        ct = att.get('content_type', '')
        if 'image' in ct or url.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            image_urls.append(url)
    for embed in msg.get('embeds', []):
        if embed.get('image', {}).get('url'):
            image_urls.append(embed['image']['url'])
        if embed.get('thumbnail', {}).get('url'):
            thumb = embed['thumbnail']['url']
            if any(ext in thumb.lower() for ext in ['.png', '.jpg', '.jpeg']):
                image_urls.append(thumb)

    records = []
    seen = set()
    for ticker in tickers[:5]:  # Max 5 tickers per message
        if ticker in seen:
            continue
        seen.add(ticker)

        records.append(SignalRecord(
            signal_id=f"{channel_id}_{msg_id}_{ticker}",
            ticker=ticker,
            direction=direction,
            signal_date=timestamp,
            channel_id=channel_id,
            author=author,
            author_id=author_id,
            source_name=source,
            content_snippet=content[:200],
            image_urls=image_urls,
        ))

    return records


# ===================================================================
# 2. Outcome Tracking — check prices after N days
# ===================================================================

_price_cache: Dict[str, float] = {}  # Ticker → current price (refreshed per cycle)
_bad_tickers: set = set()  # Tickers that failed to fetch (skip them)

# GCloud Trading API for IBKR real data
TRADE_API_URL = os.environ.get("TRADE_API_URL", "http://34.75.9.166:8080")
TRADE_API_KEY = os.environ.get("TRADE_API_KEY", "saiyan-trade-2026")


def _batch_fetch_prices_ibkr(tickers: List[str]) -> Dict[str, float]:
    """
    Batch-fetch prices from IBKR Gateway via GCloud /api/quotes endpoint.
    This is the preferred source — real broker data, no rate limits.
    """
    import requests
    result = {}
    headers = {"X-API-Key": TRADE_API_KEY, "Content-Type": "application/json"}

    # Process in chunks of 25 (API limit is 30)
    for i in range(0, len(tickers), 25):
        chunk = tickers[i:i + 25]
        try:
            resp = requests.post(
                f"{TRADE_API_URL}/api/quotes",
                json={"symbols": chunk},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("error"):
                    for sym, price in data.items():
                        if isinstance(price, (int, float)) and price > 0:
                            result[sym] = float(price)
        except Exception as e:
            log.warning(f"IBKR batch quote error: {e}")

    return result


def _batch_fetch_prices_yfinance(tickers: List[str]) -> Dict[str, float]:
    """Fallback: batch-fetch from yfinance."""
    result = {}
    try:
        import yfinance as yf
        for i in range(0, len(tickers), 50):
            chunk = tickers[i:i + 50]
            ticker_str = " ".join(chunk)
            try:
                data = yf.download(ticker_str, period="5d", progress=False, threads=True)
                if data is not None and not data.empty:
                    close = data['Close']
                    if hasattr(close, 'columns'):
                        for t in close.columns:
                            vals = close[t].dropna()
                            if len(vals) > 0:
                                result[t] = float(vals.iloc[-1])
                    else:
                        if len(chunk) == 1 and len(close.dropna()) > 0:
                            result[chunk[0]] = float(close.dropna().iloc[-1])
            except Exception as e:
                log.warning(f"yfinance batch error: {e}")
    except ImportError:
        log.warning("yfinance not available")
    return result


def _batch_fetch_prices(tickers: List[str]) -> Dict[str, float]:
    """
    Batch-fetch prices: IBKR Gateway first, yfinance fallback for remainder.
    """
    global _price_cache, _bad_tickers
    need = [t for t in set(tickers) if t not in _price_cache and t not in _bad_tickers]

    if not need:
        return _price_cache

    log.info(f"Batch-fetching prices for {len(need)} tickers (IBKR first)...")

    # Try IBKR Gateway first (preferred: real broker data)
    ibkr_prices = _batch_fetch_prices_ibkr(need)
    _price_cache.update(ibkr_prices)
    log.info(f"  IBKR returned {len(ibkr_prices)} prices")

    # Fallback to yfinance for remaining
    still_need = [t for t in need if t not in _price_cache]
    if still_need:
        log.info(f"  Falling back to yfinance for {len(still_need)} remaining...")
        yf_prices = _batch_fetch_prices_yfinance(still_need)
        _price_cache.update(yf_prices)
        log.info(f"  yfinance returned {len(yf_prices)} prices")

    # Mark still-missing as bad
    for t in need:
        if t not in _price_cache:
            _bad_tickers.add(t)

    log.info(f"Price cache: {len(_price_cache)} total, {len(_bad_tickers)} failed")
    return _price_cache


def track_outcomes(records: List[dict]) -> List[dict]:
    """
    Update price outcomes for signals that need tracking.
    Uses batch price fetching for speed.

    Returns updated records.
    """
    global _price_cache
    _price_cache = {}  # Reset cache each cycle

    now = datetime.now(timezone.utc)
    updated = 0

    # Step 1: Collect all unique tickers that need prices
    tickers_needed = set()
    for rec in records:
        ticker = rec.get('ticker', '')
        if not ticker:
            continue
        signal_date_str = rec.get('signal_date', '')
        if not signal_date_str:
            continue
        price_at_signal = rec.get('price_at_signal', 0)
        if price_at_signal <= 0:
            tickers_needed.add(ticker)
            continue
        # Check if any period needs updating
        try:
            sig_dt = datetime.fromisoformat(signal_date_str.replace('Z', '+00:00'))
            days_elapsed = (now - sig_dt).days
        except Exception:
            continue
        for days in TRACK_PERIODS:
            if days_elapsed >= days and rec.get(f'price_{days}d', 0) == 0:
                tickers_needed.add(ticker)
                break

    if not tickers_needed:
        log.info("No tickers need price updates")
        return records

    # Step 2: Batch fetch all prices at once
    prices = _batch_fetch_prices(list(tickers_needed))

    # Step 3: Apply prices to records
    for rec in records:
        ticker = rec.get('ticker', '')
        signal_date_str = rec.get('signal_date', '')
        if not ticker or not signal_date_str:
            continue

        try:
            sig_dt = datetime.fromisoformat(signal_date_str.replace('Z', '+00:00'))
        except Exception:
            continue

        days_elapsed = (now - sig_dt).days
        price_at_signal = rec.get('price_at_signal', 0)

        # Set price_at_signal if missing
        if price_at_signal <= 0:
            current = prices.get(ticker, 0)
            if current > 0:
                rec['price_at_signal'] = current
                price_at_signal = current
                updated += 1

        if price_at_signal <= 0:
            continue

        # Update period outcomes
        current_price = prices.get(ticker, 0)
        if current_price <= 0:
            continue

        for days in TRACK_PERIODS:
            key_price = f'price_{days}d'
            key_return = f'return_{days}d'
            if days_elapsed >= days and rec.get(key_price, 0) == 0:
                rec[key_price] = current_price
                ret = ((current_price / price_at_signal) - 1) * 100
                if rec.get('direction') == 'BEAR':
                    ret = -ret
                rec[key_return] = round(ret, 2)
                updated += 1

        # Compute win/loss
        returns = [rec.get(f'return_{d}d', 0) for d in TRACK_PERIODS if rec.get(f'return_{d}d', 0) != 0]
        if returns:
            rec['peak_return'] = max(returns)
            rec['worst_return'] = min(returns)
            rec['is_winner'] = max(returns) >= 2.0
            rec['is_loser'] = min(returns) <= -5.0

        rec['last_tracked'] = now.isoformat()

    log.info(f"Tracked outcomes: {updated} updates across {len(records)} records")
    return records


# ===================================================================
# 3. Trader Grading — score traders/channels by performance
# ===================================================================

@dataclass
class TraderGrade:
    """Performance grade for a signal source (trader or channel)."""
    name: str = ""
    source: str = ""              # Goku / Wilson
    author_id: str = ""
    total_signals: int = 0
    tracked_signals: int = 0
    winners: int = 0
    losers: int = 0
    neutral: int = 0
    win_rate: float = 0           # winners / tracked
    avg_return_5d: float = 0
    avg_return_10d: float = 0
    avg_return_20d: float = 0
    best_signal: str = ""         # ticker + return
    worst_signal: str = ""
    consistency_score: float = 0  # 0-1 how consistent
    overall_grade: str = ""       # A/B/C/D/F
    conviction_multiplier: float = 1.0  # Feed into pipeline scoring

    def to_dict(self):
        return asdict(self)


def compute_grades(records: List[dict]) -> Dict[str, TraderGrade]:
    """
    Compute trader grades from tracked signal records.

    Returns: {author_name: TraderGrade}
    """
    # Group by author
    by_author: Dict[str, List[dict]] = {}
    for rec in records:
        author = rec.get('author', 'unknown')
        if author not in by_author:
            by_author[author] = []
        by_author[author].append(rec)

    grades = {}
    for author, signals in by_author.items():
        g = TraderGrade(name=author)
        g.total_signals = len(signals)
        g.source = signals[0].get('source_name', '') if signals else ''
        g.author_id = signals[0].get('author_id', '') if signals else ''

        tracked = [s for s in signals if s.get('price_at_signal', 0) > 0 and any(s.get(f'return_{d}d', 0) != 0 for d in TRACK_PERIODS)]
        g.tracked_signals = len(tracked)

        if not tracked:
            g.overall_grade = "N/A"
            g.conviction_multiplier = 1.0
            grades[author] = g
            continue

        g.winners = sum(1 for s in tracked if s.get('is_winner'))
        g.losers = sum(1 for s in tracked if s.get('is_loser'))
        g.neutral = g.tracked_signals - g.winners - g.losers
        g.win_rate = g.winners / g.tracked_signals if g.tracked_signals > 0 else 0

        # Average returns
        returns_5d = [s.get('return_5d', 0) for s in tracked if s.get('return_5d', 0) != 0]
        returns_10d = [s.get('return_10d', 0) for s in tracked if s.get('return_10d', 0) != 0]
        returns_20d = [s.get('return_20d', 0) for s in tracked if s.get('return_20d', 0) != 0]

        g.avg_return_5d = round(sum(returns_5d) / len(returns_5d), 2) if returns_5d else 0
        g.avg_return_10d = round(sum(returns_10d) / len(returns_10d), 2) if returns_10d else 0
        g.avg_return_20d = round(sum(returns_20d) / len(returns_20d), 2) if returns_20d else 0

        # Best/worst
        best = max(tracked, key=lambda s: s.get('peak_return', 0))
        worst = min(tracked, key=lambda s: s.get('worst_return', 0))
        g.best_signal = f"${best['ticker']} +{best.get('peak_return', 0):.1f}%"
        g.worst_signal = f"${worst['ticker']} {worst.get('worst_return', 0):.1f}%"

        # Consistency = how often returns are positive (across all periods)
        all_returns = returns_5d + returns_10d + returns_20d
        if all_returns:
            g.consistency_score = round(sum(1 for r in all_returns if r > 0) / len(all_returns), 2)

        # Grade assignment
        score = (g.win_rate * 40) + (g.consistency_score * 30) + (min(g.avg_return_5d, 10) * 3)
        if score >= 70:
            g.overall_grade = "A"
            g.conviction_multiplier = 1.5
        elif score >= 55:
            g.overall_grade = "B"
            g.conviction_multiplier = 1.2
        elif score >= 40:
            g.overall_grade = "C"
            g.conviction_multiplier = 1.0
        elif score >= 25:
            g.overall_grade = "D"
            g.conviction_multiplier = 0.7
        else:
            g.overall_grade = "F"
            g.conviction_multiplier = 0.5

        grades[author] = g

    return grades


def save_grades(grades: Dict[str, TraderGrade]):
    """Save grades to disk."""
    data = {k: v.to_dict() for k, v in grades.items()}
    with open(GRADES_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    log.info(f"Saved grades for {len(grades)} traders to {GRADES_FILE}")


def load_grades() -> Dict[str, float]:
    """
    Load trader grades and return conviction multipliers.
    Returns: {author_name: multiplier}
    """
    if not os.path.exists(GRADES_FILE):
        return {}
    try:
        with open(GRADES_FILE, 'r') as f:
            data = json.load(f)
        return {name: g.get('conviction_multiplier', 1.0) for name, g in data.items()}
    except Exception:
        return {}


# ===================================================================
# 4. Image Analysis — download chart images, send to LLM
# ===================================================================

async def download_image(url: str, session=None) -> Optional[bytes]:
    """Download an image from Discord CDN."""
    try:
        import aiohttp
        if session is None:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        return await resp.read()
        else:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.read()
    except Exception as e:
        log.warning(f"Image download failed: {e}")
    return None


def analyze_image_with_llm(image_bytes: bytes, ticker: str, context: str,
                           api_key: str, content_type: str = "image/png") -> str:
    """
    Send a chart image to MiniMax (Anthropic-compatible) for visual analysis.

    MiniMax M2.1 supports vision via the Anthropic messages API format.
    """
    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=api_key,
            base_url="https://api.minimax.io/anthropic"
        )

        b64_image = base64.b64encode(image_bytes).decode('utf-8')

        message = client.messages.create(
            model="MiniMax-M2.1",
            max_tokens=1200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": content_type,
                            "data": b64_image,
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""Analyze this stock chart for ${ticker}.

Context from Discord signal:
{context[:500]}

Identify:
1. Chart pattern (head&shoulders, cup&handle, breakout, pullback, etc.)
2. Key support/resistance levels visible
3. Volume pattern (increasing, decreasing, divergence)
4. Trend direction and strength
5. Entry/exit zones if visible
6. Risk assessment (1-5 scale)

Be specific with price levels. Keep response under 400 chars."""
                    }
                ]
            }]
        )

        return message.content[0].text if message.content else ""

    except Exception as e:
        log.warning(f"Image LLM analysis failed: {e}")
        return f"[Image analysis unavailable: {str(e)[:80]}]"


async def process_signal_images(record: dict, api_key: str) -> str:
    """
    Download and analyze all images attached to a signal.
    Returns combined analysis text.
    """
    image_urls = record.get('image_urls', [])
    if not image_urls:
        return ""

    analyses = []
    for url in image_urls[:3]:  # Max 3 images per signal
        img_data = await download_image(url)
        if not img_data:
            continue

        # Determine content type
        ct = "image/png"
        if '.jpg' in url.lower() or '.jpeg' in url.lower():
            ct = "image/jpeg"
        elif '.gif' in url.lower():
            ct = "image/gif"
        elif '.webp' in url.lower():
            ct = "image/webp"

        analysis = analyze_image_with_llm(
            img_data,
            record.get('ticker', '?'),
            record.get('content_snippet', ''),
            api_key,
            ct,
        )
        if analysis:
            analyses.append(analysis)

    combined = "\n---\n".join(analyses) if analyses else ""
    return combined


# ===================================================================
# 5. Persistence — load/save performance data
# ===================================================================

def load_performance() -> List[dict]:
    """Load tracked signal performance data."""
    if not os.path.exists(PERF_FILE):
        return []
    try:
        with open(PERF_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def save_performance(records: List[dict]):
    """Save tracked signal performance data."""
    with open(PERF_FILE, 'w') as f:
        json.dump(records, f, indent=2, default=str)
    log.info(f"Saved {len(records)} signal records to {PERF_FILE}")


def ingest_new_signals(data_file: str, existing_records: List[dict]) -> List[dict]:
    """
    Scan the Discord data cache for new signals not yet tracked.
    Returns updated records list (existing + new).
    """
    if not os.path.exists(data_file):
        return existing_records

    try:
        with open(data_file, 'r') as f:
            data = json.load(f)
    except Exception:
        return existing_records

    existing_ids = {r['signal_id'] for r in existing_records}
    new_count = 0

    for channel_id, msgs in data.items():
        for msg in msgs:
            records = extract_signals_from_message(msg, channel_id)
            for rec in records:
                if rec.signal_id not in existing_ids:
                    existing_records.append(rec.to_dict())
                    existing_ids.add(rec.signal_id)
                    new_count += 1

    if new_count > 0:
        log.info(f"Ingested {new_count} new signals for tracking")
    return existing_records


# ===================================================================
# 6. Full Update Cycle — called daily after market close
# ===================================================================

async def run_daily_learning_cycle(data_file: str, api_key: str = ""):
    """
    Full self-learning cycle:
      1. Ingest any new signals from Discord cache
      2. Track price outcomes for existing signals
      3. Analyze chart images for recent signals
      4. Compute/update trader grades
      5. Save everything

    Should be called daily after market close.
    """
    log.info("=== Starting Daily Learning Cycle ===")

    # Load existing data
    records = load_performance()
    log.info(f"Loaded {len(records)} existing signal records")

    # 1. Ingest new signals
    records = ingest_new_signals(data_file, records)

    # 2. Track outcomes
    records = track_outcomes(records)

    # 3. Process images for recent signals (last 3 days, no analysis yet)
    if api_key:
        recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        for rec in records:
            if (rec.get('signal_date', '') >= recent_cutoff
                    and rec.get('image_urls')
                    and not rec.get('image_analysis')):
                try:
                    analysis = await process_signal_images(rec, api_key)
                    if analysis:
                        rec['image_analysis'] = analysis
                        log.info(f"Image analysis for ${rec['ticker']}: {analysis[:80]}...")
                except Exception as e:
                    log.warning(f"Image processing error: {e}")
                await asyncio.sleep(1)  # Rate limit

    # 4. Compute grades
    grades = compute_grades(records)
    save_grades(grades)

    # 5. Save records
    save_performance(records)

    # 6. Pattern library — ingest and track pattern-level performance
    pat_count = 0
    pat_stats_count = 0
    if HAS_PATTERN_LIB:
        try:
            pat_records = load_pattern_db()
            pat_records = ingest_patterns_from_discord(data_file, pat_records)
            pat_count = len(pat_records)

            # Track outcomes for pattern records (reuse same yfinance logic)
            pat_records = track_outcomes(pat_records)

            pat_stats = compute_pattern_stats(pat_records)
            pat_stats_count = len(pat_stats)

            save_pattern_db(pat_records)
            log.info(f"Pattern library: {pat_count} records, {pat_stats_count} patterns tracked")
        except Exception as e:
            log.warning(f"Pattern library error: {e}")

    # Summary
    total = len(records)
    tracked = sum(1 for r in records if r.get('price_at_signal', 0) > 0)
    winners = sum(1 for r in records if r.get('is_winner'))
    losers = sum(1 for r in records if r.get('is_loser'))
    with_images = sum(1 for r in records if r.get('image_urls'))
    analyzed_images = sum(1 for r in records if r.get('image_analysis'))

    summary = (
        f"=== Learning Cycle Complete ===\n"
        f"Total signals: {total}\n"
        f"Tracked (has price): {tracked}\n"
        f"Winners (>2%): {winners} | Losers (<-5%): {losers}\n"
        f"Signals with images: {with_images} | Analyzed: {analyzed_images}\n"
        f"Trader grades: {len(grades)}\n"
        f"Patterns tracked: {pat_count} records, {pat_stats_count} pattern types\n"
    )

    for name, g in sorted(grades.items(), key=lambda x: x[1].win_rate, reverse=True)[:5]:
        summary += f"  {g.overall_grade} {name} ({g.source}): {g.win_rate:.0%} WR, avg 5d: {g.avg_return_5d:+.1f}%\n"

    log.info(summary)
    return summary


def format_grades_for_discord(grades: Dict[str, TraderGrade]) -> str:
    """Format trader grades for Discord display."""
    if not grades:
        return "No trader grades computed yet. Run `!learn` first."

    sorted_g = sorted(grades.values(), key=lambda g: g.win_rate, reverse=True)

    lines = [f"*Trader Performance Report* ({len(sorted_g)} traders)\n"]
    for g in sorted_g:
        if g.tracked_signals < 2:
            continue  # Skip traders with too few signals

        grade_icon = {"A": "🏆", "B": "🟢", "C": "🟡", "D": "🟠", "F": "🔴"}.get(g.overall_grade, "⚪")

        lines.append(
            f"{grade_icon} *{g.name}* ({g.source}) — Grade: *{g.overall_grade}*\n"
            f"   {g.tracked_signals} signals | WR: {g.win_rate:.0%} | "
            f"Avg 5d: {g.avg_return_5d:+.1f}% | Avg 10d: {g.avg_return_10d:+.1f}%\n"
            f"   Best: {g.best_signal} | Worst: {g.worst_signal}\n"
            f"   Conviction multiplier: {g.conviction_multiplier:.1f}x"
        )

    return "\n".join(lines)
