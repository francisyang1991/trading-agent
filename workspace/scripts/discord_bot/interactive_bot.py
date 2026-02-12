#!/usr/bin/env python3
"""
Interactive Discord Trading Bot
================================
Thin Discord event handler. All heavy logic lives in:
  - trade_parser.py   → command parsing
  - trade_executor.py → entry computation, API calls, reporting

Architecture:
  AWS (this bot)  ─── HTTP POST ───►  GCP (Trading GUI :8080/api/trade)
                  ─── HTTP GET  ───►  GCP (/api/analyze/<ticker>)
                                           │
                                           ▼
                                      IB Gateway (ib_async)
                                      Scanner + Technical Analyzer

Commands:
  @bot buy HOOD           → single entry at EMA21 support
  @bot sell TSLA          → short at EMA8 resistance
  @bot buy HOOD 2x        → 2 scaled entries
  @bot buy HOOD 5000usd   → $5k worth of HOOD
  @bot buy HOOD 50 shares → exactly 50 shares
  @bot scale into NVDA    → auto 2 scaled entries
  @bot $AAPL              → analysis only (no order)
  !analyze TICKER         → analysis only
  !positions              → show IB positions
  !orders                 → show open orders
"""

import os
import sys
import re
import json
import asyncio
import logging
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone

# Core analysis (LLM + yfinance)
sys.path.append(os.path.join(os.path.dirname(__file__), '../core_analysis'))
from llm_analyzer import _call_minimax_anthropic, get_stock_context

# Trading modules (same directory)
import trade_parser
import trade_executor
import signal_pipeline
import signal_tracker
import pattern_library
import auto_executor
import portfolio_manager

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
log = logging.getLogger("interactive-bot")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_USER_TOKEN = os.environ.get("DISCORD_USER_TOKEN", "")
DATA_FILE = os.path.join(os.path.dirname(__file__), '../../data/real_discord_messages_goku_wilson_60d.txt')

GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461", "1393715240474247249"]
WILSON_CHANNELS = ["1211549165629476924"]
ALL_SIGNAL_CHANNELS = set(GOKU_CHANNELS + WILSON_CHANNELS)

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ===================================================================
# Analysis helpers
# ===================================================================

def extract_ticker(text):
    """Extract stock ticker from message text."""
    exclude = {
        'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN',
        'HAS', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE',
        'WAY', 'WHO', 'BOT', 'GET', 'LET', 'PUT', 'SAY', 'USE', 'YES',
        'BUY', 'SELL', 'HOLD', 'LONG', 'SHORT', 'WHAT', 'WHEN', 'THIS',
        'THAT', 'WITH', 'FROM', 'HAVE', 'WILL', 'YOUR', 'ABOUT', 'THINK',
        'INTO', 'SCALE', 'DCA', 'ADD', 'USD', 'SHARES',
    }
    for pattern in [r'\$([A-Z]{1,5})\b', r'\b([A-Z]{1,5})\b']:
        for match in re.findall(pattern, text.upper()):
            if match not in exclude and len(match) >= 2:
                return match
    return None


def get_ticker_messages(ticker, days=60):
    """Get cached messages mentioning a ticker.

    Uses regex word-boundary matching to avoid partial ticker matches.
    e.g., searching for $AA will NOT match $AAPL, $AAL, or random text
    containing the letters 'AA' inside other words.
    """
    if not os.path.exists(DATA_FILE):
        return [], None  # No data file is not an error — scanner will provide analysis
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Build regex patterns with word boundaries for exact ticker matching
    ticker_upper = ticker.upper()
    ticker_patterns = [
        # $AA followed by non-letter (or end of string) — matches "$AA " but NOT "$AAPL"
        re.compile(r'\$' + re.escape(ticker_upper) + r'(?![A-Za-z])'),
        # Standalone AA: not preceded by letter/$ and not followed by letter
        re.compile(r'(?<![A-Za-z$])' + re.escape(ticker_upper) + r'(?![A-Za-z])'),
    ]

    messages = []
    for channel_id, msgs in data.items():
        source = "Goku" if channel_id in GOKU_CHANNELS else "Wilson"
        for msg in msgs:
            content = msg.get('content', '')
            ts = msg.get('timestamp', '')
            if any(p.search(content) for p in ticker_patterns):
                try:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    if dt > cutoff:
                        messages.append({
                            "source": source, "date": ts[:10],
                            "content": content,
                            "author": msg.get('author', {}).get('username', 'Unknown'),
                        })
                except Exception:
                    continue
    messages.sort(key=lambda x: x['date'], reverse=True)
    return messages, None


# ===================================================================
# GCloud Scanner API integration
# ===================================================================

async def fetch_gcloud_analysis(ticker):
    """
    Call the GCloud Trading GUI /api/analyze/<ticker> endpoint.

    Returns dict with 'scan', 'technical', 'info', 'earnings' keys,
    or None if the API is unreachable.
    """
    try:
        result = await trade_executor.call_api(f"/api/analyze/{ticker}")
        if result.get("success"):
            return result
        log.warning(f"GCloud analysis for {ticker}: {result.get('error', 'no data')}")
        return None
    except Exception as e:
        log.warning(f"GCloud analysis API error for {ticker}: {e}")
        return None


def format_scanner_analysis(ticker, data):
    """
    Format GCloud scanner JSON into a Discord-friendly message.

    Produces a complete analysis even for tickers with zero cached signals.
    """
    lines = []
    scan = data.get("scan", {})
    tech = data.get("technical", {})
    info = data.get("info", {})
    earnings = data.get("earnings", {})

    # --- Header ---
    name = info.get("name", ticker)
    sector = info.get("sector", "")
    lines.append(f"*{ticker} — {name}*")
    if sector:
        lines.append(f"_{sector}_")

    # --- Price ---
    price = tech.get("price") or scan.get("price")
    if price:
        chg_1d = tech.get("change_1d", 0)
        chg_1w = tech.get("change_1w", 0)
        arrow = "🟢" if chg_1d >= 0 else "🔴"
        lines.append(f"\n💰 *Price:* ${price:.2f}  {arrow} {chg_1d:+.2f}% today | {chg_1w:+.2f}% week")

    # --- Regime & Strategy ---
    regime = scan.get("regime", "")
    strategy = scan.get("strategy", "")
    vol_cat = scan.get("vol_category", "")
    if regime:
        lines.append(f"\n📊 *Regime:* {regime} | Vol: {vol_cat}")
    if strategy:
        lines.append(f"🎯 *Strategy:* {strategy}")

    # --- Technical ---
    emas = tech.get("emas", {})
    rsi = tech.get("rsi")
    trend = tech.get("trend", "")
    if rsi is not None:
        rsi_label = "OVERBOUGHT" if rsi > 70 else ("OVERSOLD" if rsi < 30 else "NEUTRAL")
        lines.append(f"\n📈 *Technical:* {trend}")
        lines.append(f"   RSI: {rsi:.1f} ({rsi_label})")
    ema_parts = []
    for period in ["9", "21", "50", "200"]:
        e = emas.get(period)
        if e:
            ema_parts.append(f"EMA{period}: ${e['value']:.2f} ({e['distance_pct']:+.1f}%)")
    if ema_parts:
        lines.append(f"   {' | '.join(ema_parts[:3])}")

    # --- Entry / Targets ---
    buy_low = scan.get("buy_zone_low")
    buy_high = scan.get("buy_zone_high")
    stop = scan.get("stop_loss")
    t1 = scan.get("target_1")
    t2 = scan.get("target_2")
    if buy_low and buy_high:
        lines.append(f"\n🎯 *Entry Zone:* ${buy_low:.2f} – ${buy_high:.2f}")
    if stop:
        lines.append(f"🛑 *Stop Loss:* ${stop:.2f}")
    if t1:
        tgt = f"${t1:.2f}"
        if t2:
            tgt += f" / ${t2:.2f}"
        lines.append(f"✅ *Targets:* {tgt}")

    # --- EV & Sizing ---
    ev = scan.get("expected_value")
    rr = scan.get("risk_reward")
    wr = scan.get("win_rate")
    pos_pct = scan.get("position_size_pct")
    if ev is not None and rr is not None:
        ev_icon = "✅" if (ev or 0) > 0 else "⚠️"
        lines.append(f"\n⚖️ *EV:* {ev_icon} {ev:.2f}% | R:R {rr:.1f}:1 | WR {(wr or 0) * 100:.0f}%")
    if pos_pct:
        lines.append(f"📐 *Position Size:* {pos_pct * 100:.1f}% of portfolio")

    # --- Volume Pullback ---
    if scan.get("volume_pullback_signal"):
        vpd = scan.get("volume_pullback_data") or {}
        grade = vpd.get("setup_grade", "?")
        lines.append(f"\n⚡ *Volume Pullback Detected* — Grade {grade}")

    # --- Support / Resistance ---
    support = tech.get("support")
    resistance = tech.get("resistance")
    if support and resistance:
        lines.append(f"\n🔑 Support: ${support:.2f} | Resistance: ${resistance:.2f}")

    # --- Earnings ---
    upcoming = earnings.get("upcoming")
    if upcoming and upcoming.get("date"):
        lines.append(f"\n📅 Next Earnings: {upcoming['date']}")

    # --- Action ---
    action = scan.get("action", "")
    reasoning = scan.get("reasoning", "")
    if action:
        lines.append(f"\n🚦 *Action:* {action}")
    if reasoning:
        lines.append(f"   {reasoning[:200]}")

    # --- Trade prompt ---
    lines.append(f"\n💡 To trade: `@bot buy {ticker}` or `@bot sell {ticker}`")

    text = "\n".join(lines)
    return text[:1900] + "..." if len(text) > 1900 else text


def generate_stock_analysis(ticker, messages):
    """
    Generate LLM analysis from cached Discord signals.

    FIX: Handles all response types from _call_minimax_anthropic:
    - raw_response (unparsed LLM text)
    - raw_llm (LLM text stored by local analysis fallback)
    - Parsed JSON (sentiment, thesis, etc.) → format into readable text
    """
    if not messages:
        return ""  # Caller will fall through to scanner

    stock_ctx = get_stock_context(ticker)
    price_info = ""
    if stock_ctx and 'error' not in stock_ctx:
        price_info = (
            f"CURRENT MARKET DATA:\n"
            f"- Price: ${stock_ctx.get('current_price', 'N/A')}\n"
            f"- EMA8: ${stock_ctx.get('ema8', 'N/A')} | EMA21: ${stock_ctx.get('ema21', 'N/A')}\n"
            f"- EMA50: ${stock_ctx.get('ema50', 'N/A')}\n"
            f"- 52W: ${stock_ctx.get('52w_low', 'N/A')} - ${stock_ctx.get('52w_high', 'N/A')}\n"
        )
    goku = [m for m in messages if m['source'] == 'Goku']
    wilson = [m for m in messages if m['source'] == 'Wilson']
    goku_text = "\n".join(f"[{m['date']}] {m['author']}: {m['content']}" for m in goku[:20])
    wilson_text = "\n".join(f"[{m['date']}] {m['author']}: {m['content']}" for m in wilson[:20])

    prompt = f"""You are an elite trading analyst. Analyze ${ticker}.

{price_info}

GOKU (Technical) - {len(goku)} mentions:
{goku_text or "None"}

WILSON (Fundamental) - {len(wilson)} mentions:
{wilson_text or "None"}

Provide Discord-formatted analysis (<1800 chars):
*${ticker} DEEP DIVE*
📊 *FUNDAMENTAL* - Business, catalysts, risks
📈 *TECHNICAL* - Pattern, support/resistance
🎯 *ENTRY* - Zone, trigger, size
🛑 *EXIT* - Stop, targets, horizon
⚖️ *RISK/REWARD* - R:R, confidence, key risk

Use single asterisks for bold."""

    try:
        response = _call_minimax_anthropic(prompt, 2000, stock_ctx, ticker, messages)
    except Exception as e:
        log.warning(f"LLM analysis failed for {ticker}: {e}")
        return ""

    # Extract content from response — handle ALL possible formats
    content = ""

    # 1. Raw LLM text (unparsed or from local fallback)
    if response.get("raw_response"):
        content = response["raw_response"]
    elif response.get("raw_llm"):
        content = response["raw_llm"]

    # 2. Parsed JSON → format into readable text
    if not content and response.get("sentiment"):
        parts = [f"*${ticker} Analysis*"]
        parts.append(f"📊 Sentiment: *{response.get('sentiment', 'N/A')}* ({response.get('confidence', 'N/A')} confidence)")
        if response.get("thesis"):
            parts.append(f"\n📝 {response['thesis']}")
        kp = response.get("key_points", [])
        if kp:
            parts.append("\n🔑 *Key Points:*")
            for p in kp[:5]:
                parts.append(f"  • {str(p)[:150]}")
        levels = response.get("price_levels", {})
        targets = levels.get("targets", [])
        if targets:
            parts.append(f"\n🎯 Targets: {', '.join(f'${t}' for t in targets[:3])}")
        if response.get("trading_strategy"):
            parts.append(f"\n💡 Strategy: {response['trading_strategy'][:200]}")
        if response.get("technical_setup"):
            parts.append(f"📈 Setup: {response['technical_setup'][:200]}")
        risks = response.get("risk_factors", [])
        if risks:
            parts.append(f"⚠️ Risks: {', '.join(str(r)[:60] for r in risks[:3])}")
        content = "\n".join(parts)

    # 3. Final cleanup
    if content:
        content = content.replace("```", "").replace("**", "*")
        return content[:1900] + "..." if len(content) > 1900 else content

    return ""  # Caller will fall through to scanner


def generate_standalone_analysis(ticker):
    """
    Generate LLM-powered technical analysis using only yfinance data.

    Used as a fallback when no cached Discord signals exist for a ticker.
    This ensures every ticker query gets meaningful analysis.
    """
    stock_ctx = get_stock_context(ticker)
    if not stock_ctx or 'error' in stock_ctx:
        return ""

    price_info = (
        f"CURRENT MARKET DATA for ${ticker}:\n"
        f"- Current Price: ${stock_ctx.get('current_price', 'N/A')}\n"
        f"- EMA8: ${stock_ctx.get('ema8', 'N/A')} | EMA21: ${stock_ctx.get('ema21', 'N/A')}\n"
        f"- EMA50: ${stock_ctx.get('ema50', 'N/A')}\n"
        f"- RSI: {stock_ctx.get('rsi', 'N/A')}\n"
        f"- 52-Week Range: ${stock_ctx.get('52w_low', 'N/A')} - ${stock_ctx.get('52w_high', 'N/A')}\n"
        f"- Average Volume: {stock_ctx.get('avg_volume', 'N/A')}\n"
    )

    prompt = f"""You are an elite trading analyst. Provide technical analysis for ${ticker}.

{price_info}

NOTE: No cached trader signals are available for this ticker. Provide your own
independent technical analysis based purely on the price data above.

Provide Discord-formatted analysis (<1800 chars):
*${ticker} Technical Analysis*
📊 *TREND* - Current trend direction, EMA alignment, momentum
📈 *KEY LEVELS* - Support/resistance based on EMAs and 52-week range
🎯 *ENTRY* - Suggested entry zone, trigger conditions
🛑 *RISK* - Stop loss level, key risks
⚖️ *OUTLOOK* - Short-term vs medium-term view

Be specific with price levels. Use single asterisks for bold."""

    try:
        response = _call_minimax_anthropic(prompt, 2000, stock_ctx, ticker, [])
    except Exception as e:
        log.warning(f"Standalone LLM analysis failed for {ticker}: {e}")
        return ""

    content = ""
    if response.get("raw_response"):
        content = response["raw_response"]
    elif response.get("raw_llm"):
        content = response["raw_llm"]

    if not content and response.get("sentiment"):
        parts = [f"*${ticker} Technical Analysis*"]
        parts.append(f"📊 Sentiment: *{response.get('sentiment', 'N/A')}* ({response.get('confidence', 'N/A')} confidence)")
        if response.get("thesis"):
            parts.append(f"\n📝 {response['thesis']}")
        if response.get("technical_setup"):
            parts.append(f"\n📈 Setup: {response['technical_setup'][:300]}")
        if response.get("trading_strategy"):
            parts.append(f"\n💡 Strategy: {response['trading_strategy'][:300]}")
        levels = response.get("price_levels", {})
        support = levels.get("support", [])
        resistance = levels.get("resistance", [])
        targets = levels.get("targets", [])
        if support:
            parts.append(f"\n🔑 Support: {', '.join(f'${s}' for s in support[:3])}")
        if resistance:
            parts.append(f"🔑 Resistance: {', '.join(f'${r}' for r in resistance[:3])}")
        if targets:
            parts.append(f"🎯 Targets: {', '.join(f'${t}' for t in targets[:3])}")
        risks = response.get("risk_factors", [])
        if risks:
            parts.append(f"\n⚠️ Risks: {', '.join(str(r)[:60] for r in risks[:3])}")
        content = "\n".join(parts)

    if content:
        content = content.replace("```", "").replace("**", "*")
        return content[:1900] + "..." if len(content) > 1900 else content

    return ""


async def _safe_send(channel, text):
    """Send a message to Discord, guarding against empty content."""
    if not text or not text.strip():
        log.warning("Attempted to send empty message — skipped")
        return
    # Discord limit is 2000 chars
    if len(text) > 2000:
        text = text[:1997] + "..."
    await channel.send(text)


async def run_full_analysis(channel, ticker):
    """
    Run the full analysis pipeline for a ticker:
      1. Check cached Discord signals
      2. If signals exist, generate LLM analysis
      3. Always call GCloud scanner for real technical data
      4. Pattern library match (historical win rates)
      5. Combine and send results
    """
    # Step 1: Cached signals
    msgs, err = get_ticker_messages(ticker)

    # Step 2: LLM analysis from cached signals (if any)
    llm_text = ""
    if msgs:
        llm_text = generate_stock_analysis(ticker, msgs)

    # Step 3: GCloud scanner analysis (always)
    scanner_text = ""
    gcloud_data = await fetch_gcloud_analysis(ticker)
    if gcloud_data:
        scanner_text = format_scanner_analysis(ticker, gcloud_data)

    # Step 4: Pattern library match
    pattern_text = ""
    try:
        pat_records = pattern_library.load_pattern_db()
        if pat_records:
            pat_stats = pattern_library.compute_pattern_stats(pat_records)
            # Build raw message list for pattern extraction
            raw_msgs = []
            if msgs:
                raw_msgs = msgs  # Already have cached messages
            else:
                # Try loading from data file for this ticker
                try:
                    with open(DATA_FILE, 'r') as f:
                        data = json.load(f)
                    for ch_msgs in data.values():
                        for m in ch_msgs:
                            content = m.get('content', '')
                            if f'${ticker}' in content.upper() or f'${ticker.upper()}' in content:
                                raw_msgs.append(m)
                except Exception:
                    pass
            if raw_msgs:
                pattern_text = pattern_library.format_pattern_match_for_ticker(
                    ticker, pat_stats, raw_msgs
                )
    except Exception as e:
        log.debug(f"Pattern match error for {ticker}: {e}")

    # Step 5: Combine and send
    if llm_text and scanner_text:
        await _safe_send(channel, scanner_text)
        signal_header = f"\n📡 *Signal Intelligence* ({len(msgs)} Discord mentions):\n"
        trimmed = llm_text[:1850]
        await _safe_send(channel, signal_header + trimmed)
    elif llm_text:
        await _safe_send(channel, llm_text)
    elif scanner_text:
        await _safe_send(channel, scanner_text)
    else:
        standalone = generate_standalone_analysis(ticker)
        if standalone:
            header = f"📭 No cached trader signals for *${ticker}*. Here is an independent analysis:\n\n"
            await _safe_send(channel, header + standalone[:1850])
        else:
            ctx = get_stock_context(ticker)
            if ctx and "error" not in ctx:
                fallback = (
                    f"*${ticker}* Quick Snapshot\n"
                    f"   Price: ${ctx.get('current_price', 'N/A')}\n"
                    f"   EMA8: ${ctx.get('ema8', 'N/A')} | EMA21: ${ctx.get('ema21', 'N/A')}\n"
                    f"   52W: ${ctx.get('52w_low', 'N/A')} – ${ctx.get('52w_high', 'N/A')}\n"
                    f"\n💡 To trade: `@bot buy {ticker}` or `@bot sell {ticker}`"
                )
                await _safe_send(channel, fallback)
            else:
                await _safe_send(channel, f"❌ Could not fetch any data for *${ticker}*.")

    # Send pattern match as a separate message (always, if available)
    if pattern_text:
        await _safe_send(channel, pattern_text)


# ===================================================================
# Bot events
# ===================================================================

@bot.event
async def on_ready():
    log.info(f"Bot online: {bot.user} | Servers: {len(bot.guilds)}")
    log.info(f"Trade API: {trade_executor.TRADE_API_URL}")
    asyncio.create_task(_check_server())
    # Backfill recent signals from Goku/Wilson channels on startup
    asyncio.create_task(_scrape_recent_signals())
    # Start periodic signal refresh (every 30 min during market hours)
    asyncio.create_task(_periodic_signal_refresh())
    # Start nightly pipeline scheduler
    asyncio.create_task(_nightly_pipeline_scheduler())
    # Start daily learning cycle (after market close, before pipeline)
    asyncio.create_task(_daily_learning_scheduler())
    # Start auto-execution scheduler (9:35 AM ET)
    asyncio.create_task(_auto_execute_scheduler())
    # Start mid-day portfolio check (12:00 PM ET)
    asyncio.create_task(_midday_check_scheduler())


async def _check_server():
    await asyncio.sleep(2)
    try:
        r = await trade_executor.call_api("/api/health")
        if r.get("connected"):
            log.info(f"Trading Server connected — mode: {r.get('trading_mode', '?')}")
        else:
            log.warning("Trading Server reachable but IB Gateway disconnected")
    except Exception as e:
        log.warning(f"Cannot reach Trading Server: {e}")


# ===================================================================
# Live signal collection from Goku/Wilson channels
# ===================================================================

def _save_signal_to_cache(message):
    """
    Save a new message from Goku/Wilson channels to the local cache file.
    This keeps the signal data fresh without needing the separate scraper.
    """
    channel_id = str(message.channel.id)
    msg_data = {
        "id": str(message.id),
        "content": message.content,
        "timestamp": message.created_at.isoformat(),
        "author": {
            "id": str(message.author.id),
            "username": str(message.author),
        },
    }

    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {}

        if channel_id not in data:
            data[channel_id] = []

        # Avoid duplicates
        existing_ids = {m.get('id') for m in data[channel_id]}
        if msg_data['id'] not in existing_ids:
            data[channel_id].append(msg_data)
            with open(DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            log.info(f"Saved signal from {message.author} in #{message.channel.name}: {message.content[:60]}...")
    except Exception as e:
        log.warning(f"Failed to save signal: {e}")


async def _scrape_recent_signals():
    """
    On startup, scrape recent messages from Goku/Wilson channels
    using the Discord user token (HTTP API) to backfill any gaps.
    Runs once on bot start, then the live collector keeps data fresh.
    """
    if not DISCORD_USER_TOKEN:
        log.warning("DISCORD_USER_TOKEN not set — skipping signal backfill")
        return

    import aiohttp
    headers = {"authorization": DISCORD_USER_TOKEN}

    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {}
    except Exception:
        data = {}

    total_new = 0
    async with aiohttp.ClientSession() as session:
        for channel_id in ALL_SIGNAL_CHANNELS:
            existing = data.get(channel_id, [])
            existing_ids = {m.get('id') for m in existing}

            # Fetch last 100 messages (covers ~2-3 days typically)
            url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=100"
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        messages = await resp.json()
                        for msg in messages:
                            if msg['id'] not in existing_ids:
                                existing.append(msg)
                                total_new += 1
                        data[channel_id] = existing
                        log.info(f"Backfill channel {channel_id}: {len(messages)} fetched, {total_new} new so far")
                    elif resp.status == 401:
                        log.warning(f"DISCORD_USER_TOKEN invalid (401) — cannot backfill signals")
                        return
                    else:
                        log.warning(f"Backfill channel {channel_id}: HTTP {resp.status}")
                await asyncio.sleep(1)  # Rate limit respect
            except Exception as e:
                log.warning(f"Backfill error for {channel_id}: {e}")

    if total_new > 0:
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)
            log.info(f"Signal backfill complete: {total_new} new messages saved")
        except Exception as e:
            log.warning(f"Failed to save backfill data: {e}")
    else:
        log.info("Signal backfill: data already up to date")


async def _periodic_signal_refresh():
    """
    Periodically refresh signal data from Goku/Wilson channels.
    Runs every 30 minutes during US market hours (9:30 AM - 4:30 PM ET),
    and once after market close (5:00 PM ET).
    """
    await asyncio.sleep(60)  # Wait 1 min after startup (backfill runs first)

    while True:
        try:
            now = datetime.now(timezone.utc)
            et_hour = (now.hour - 5) % 24  # Rough UTC to ET conversion

            # Market hours: ~14:30-21:30 UTC (9:30 AM - 4:30 PM ET)
            # After-close: ~22:00 UTC (5:00 PM ET)
            is_market_hours = 14 <= et_hour <= 21
            is_after_close = et_hour == 22
            is_weekday = now.weekday() < 5

            if is_weekday and (is_market_hours or is_after_close):
                log.info("Periodic signal refresh triggered")
                await _scrape_recent_signals()
                await asyncio.sleep(1800)  # 30 minutes
            else:
                await asyncio.sleep(900)  # Check again in 15 min
        except Exception as e:
            log.warning(f"Periodic refresh error: {e}")
            await asyncio.sleep(300)  # Retry in 5 min on error


# Target channel for nightly digest (Rich or Die)
DIGEST_CHANNEL_ID = 1345123472019423284


async def _nightly_pipeline_scheduler():
    """
    Run full pipeline nightly after market close (~5:15 PM ET / 22:15 UTC).
    Sends digest to the Rich or Die channel automatically.
    """
    await asyncio.sleep(120)  # Wait 2 min after startup

    while True:
        try:
            now = datetime.now(timezone.utc)
            et_hour = (now.hour - 5) % 24
            et_minute = now.minute
            is_weekday = now.weekday() < 5

            # Trigger at ~5:15 PM ET (22:15 UTC) on weekdays
            if is_weekday and et_hour == 17 and 14 <= et_minute <= 16:
                log.info("Nightly pipeline triggered")
                await _run_and_send_pipeline()
                await asyncio.sleep(3600)  # Don't trigger again for 1 hour
            else:
                await asyncio.sleep(60)  # Check every minute
        except Exception as e:
            log.warning(f"Nightly pipeline error: {e}")
            await asyncio.sleep(600)


async def _auto_execute_scheduler():
    """
    Run auto-execution of approved trades at market open (~9:35 AM ET / 14:35 UTC).
    """
    await asyncio.sleep(130)

    while True:
        try:
            now = datetime.now(timezone.utc)
            et_hour = (now.hour - 5) % 24
            et_minute = now.minute
            is_weekday = now.weekday() < 5

            # Trigger at ~9:35 AM ET (14:35 UTC)
            if is_weekday and et_hour == 9 and 34 <= et_minute <= 36:
                log.info("Auto-execution triggered")
                channel = bot.get_channel(DIGEST_CHANNEL_ID)
                if channel:
                    await auto_executor.execute_approved_trades(channel.send)
                else:
                    log.error(f"Cannot find digest channel {DIGEST_CHANNEL_ID}")
                await asyncio.sleep(3600)
            else:
                await asyncio.sleep(60)
        except Exception as e:
            log.exception(f"Auto-execute scheduler error: {e}")
            await asyncio.sleep(60)


async def _midday_check_scheduler():
    """
    Run portfolio check at noon (~12:00 PM ET / 17:00 UTC).
    """
    await asyncio.sleep(140)

    while True:
        try:
            now = datetime.now(timezone.utc)
            et_hour = (now.hour - 5) % 24
            et_minute = now.minute
            is_weekday = now.weekday() < 5

            # Trigger at ~12:00 PM ET (17:00 UTC)
            if is_weekday and et_hour == 12 and 0 <= et_minute <= 2:
                log.info("Mid-day portfolio check triggered")
                channel = bot.get_channel(DIGEST_CHANNEL_ID)
                if channel:
                    await portfolio_manager.run_midday_check(channel.send)
                else:
                    log.error(f"Cannot find digest channel {DIGEST_CHANNEL_ID}")
                await asyncio.sleep(3600)
            else:
                await asyncio.sleep(60)
        except Exception as e:
            log.exception(f"Mid-day check scheduler error: {e}")
            await asyncio.sleep(60)


async def _daily_learning_scheduler():
    """
    Run learning cycle daily at ~4:45 PM ET (before nightly pipeline at 5:15 PM).
    Tracks signal outcomes, grades traders, processes chart images.
    """
    await asyncio.sleep(180)  # Wait 3 min after startup

    while True:
        try:
            now = datetime.now(timezone.utc)
            et_hour = (now.hour - 5) % 24
            et_minute = now.minute
            is_weekday = now.weekday() < 5

            # Trigger at ~4:45 PM ET on weekdays (before pipeline at 5:15 PM)
            if is_weekday and et_hour == 16 and 44 <= et_minute <= 46:
                log.info("Daily learning cycle triggered")
                minimax_key = os.environ.get("MINIMAX_API_KEY", "")
                summary = await signal_tracker.run_daily_learning_cycle(
                    DATA_FILE, api_key=minimax_key
                )
                log.info(f"Learning cycle complete:\n{summary}")
                await asyncio.sleep(3600)  # Don't run again for 1 hour
            else:
                await asyncio.sleep(60)
        except Exception as e:
            log.warning(f"Learning cycle error: {e}")
            await asyncio.sleep(600)


async def _run_and_send_pipeline(channel=None):
    """Run the full signal pipeline and send results to Discord."""
    try:
        # Refresh signals first
        await _scrape_recent_signals()
        await asyncio.sleep(2)

        # Run pipeline
        candidates, digest_messages = await signal_pipeline.run_full_pipeline(
            data_file=DATA_FILE,
            call_api_fn=trade_executor.call_api,
            llm_fn=_call_minimax_anthropic,
            discord_days=3,
        )

        # Find the channel to send to
        if channel is None:
            channel = bot.get_channel(DIGEST_CHANNEL_ID)

        if channel is None:
            log.warning("Could not find digest channel")
            return candidates

        # Send each digest message
        for msg in digest_messages:
            await _safe_send(channel, msg)
            await asyncio.sleep(1)

        # Auto-trade in paper mode (nightly pipeline)
        try:
            health = await trade_executor.call_api("/api/health")
            if health.get("trading_mode") == "paper":
                buy_candidates = [c for c in candidates if c.action == "BUY"][:3]
                for c in buy_candidates:
                    try:
                        params = signal_pipeline.generate_order_params(c, "limit")
                        result = await trade_executor.call_api("/api/trade", method="POST", payload=params)
                        status = "✅" if result.get("success") else "⚠️"
                        await _safe_send(channel, f"{status} Auto-trade: ${c.ticker} {params['order_type']} @ ${params.get('price', 'MKT')}")
                        signal_pipeline.save_approved(c.ticker, params)
                        await asyncio.sleep(1)
                    except Exception as e:
                        log.warning(f"Auto-trade {c.ticker} failed: {e}")
        except Exception as e:
            log.warning(f"Auto-trade check failed: {e}")

        log.info(f"Nightly digest sent: {len(candidates)} candidates, {len(digest_messages)} messages")
        return candidates

    except Exception as e:
        log.exception(f"Pipeline error: {e}")
        if channel:
            await _safe_send(channel, f"❌ Pipeline error: {str(e)[:200]}")
        return []


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # ---- Live signal collection from Goku/Wilson channels ----
    # (Only works if bot is in those servers; otherwise backfill handles it)
    if str(message.channel.id) in ALL_SIGNAL_CHANNELS:
        _save_signal_to_cache(message)

    if bot.user.mentioned_in(message):
        clean = re.sub(r'<@!?\d+>', '', message.content).strip()

        # ---- Trade command? ----
        cmd = trade_parser.parse(clean)
        if cmd:
            log.info(f"Trade: {trade_parser.format_command(cmd)} (from {message.author})")
            await _safe_send(
                message.channel,
                f"📊 Analyzing **${cmd.ticker}** for "
                f"{'BUY' if cmd.direction == 'long' else 'SELL'}..."
            )
            try:
                stock_ctx = get_stock_context(cmd.ticker)
                if not stock_ctx or "error" in stock_ctx:
                    await _safe_send(message.channel, f"❌ Could not fetch data for ${cmd.ticker}.")
                else:
                    await trade_executor.execute(message.channel, cmd, stock_ctx)
            except Exception as e:
                log.exception(f"Trade error: {e}")
                await _safe_send(message.channel, f"❌ Error: {str(e)[:200]}")
            await bot.process_commands(message)
            return

        # ---- Analysis-only? ----
        ticker = extract_ticker(clean)
        if ticker:
            await _safe_send(message.channel, f"🔍 Analyzing *${ticker}*... (checking scanner + signals)")
            try:
                await run_full_analysis(message.channel, ticker)
            except Exception as e:
                log.exception(f"Analysis error for {ticker}: {e}")
                await _safe_send(message.channel, f"❌ Error analyzing {ticker}: {str(e)[:150]}")
        else:
            await _safe_send(message.channel, HELP_TEXT)

    await bot.process_commands(message)


# ===================================================================
# Slash / bang commands
# ===================================================================

@bot.command(name="analyze")
async def cmd_analyze(ctx, ticker: str = None):
    if not ticker:
        return await _safe_send(ctx, "Usage: `!analyze TICKER`")
    ticker = ticker.upper().replace("$", "")
    await _safe_send(ctx, f"🔍 Analyzing *${ticker}*... (checking scanner + signals)")
    try:
        await run_full_analysis(ctx, ticker)
    except Exception as e:
        log.exception(f"Analysis error for {ticker}: {e}")
        await _safe_send(ctx, f"❌ Error analyzing {ticker}: {str(e)[:150]}")


@bot.command(name="signals")
async def cmd_signals(ctx, ticker: str = None):
    if not ticker:
        return await ctx.send("Usage: `!signals TICKER` or `!signals recent`")

    # Handle "!signals recent" — show recent signals across all tickers
    if ticker.lower() == "recent":
        return await _show_recent_signals(ctx)

    ticker = ticker.upper().replace("$", "")
    msgs, err = get_ticker_messages(ticker, days=30)
    if err:
        return await ctx.send(f"❌ {err}")
    if not msgs:
        return await ctx.send(
            f"📭 No cached signals for *${ticker}* in the last 30 days.\n"
            f"💡 Try `!analyze {ticker}` for a full technical analysis instead."
        )
    out = f"*${ticker} Recent Signals* ({len(msgs)} in last 30 days)\n\n"
    for m in msgs[:10]:
        e = "📊" if m['source'] == 'Goku' else "📈"
        snippet = m['content'][:120].replace('\n', ' ')
        out += f"{e} [{m['date']}] _{m['author']}_: {snippet}...\n\n"
    await _safe_send(ctx, out)


@bot.command(name="recent")
async def cmd_recent(ctx):
    """Show the most recent signals across all tickers (last 7 days)."""
    await _show_recent_signals(ctx)


@bot.command(name="daily")
async def cmd_daily(ctx, days: int = 3):
    """Analyze recent signals. Usage: !daily [days] (default 3, max 30)."""
    days = max(1, min(days, 30))  # Clamp to 1-30
    await _analyze_daily_signals(ctx, days=days)


def _collect_recent_messages(days=7):
    """Collect recent messages from cached data, grouped by ticker.

    Returns:
        tuple: (ticker_map, all_recent) where ticker_map is {ticker: [msgs]}
               and all_recent is the flat list sorted by date.
    """
    if not os.path.exists(DATA_FILE):
        return {}, []

    with open(DATA_FILE, 'r') as f:
        data = json.load(f)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    all_recent = []
    ticker_map = {}  # ticker -> [messages]

    for channel_id, msgs in data.items():
        source = "Goku" if channel_id in GOKU_CHANNELS else "Wilson"
        for msg in msgs:
            content = msg.get('content', '')
            ts = msg.get('timestamp', '')
            # Extract tickers: $TICKER format
            tickers_found = re.findall(r'\$([A-Z]{1,5})\b', content)
            # Also extract from "Long: FCEL TER HWM" / "Short: AAPL TSLA" patterns
            direction_match = re.findall(
                r'(?:Long|Short|Buy|Sell|Bought|Sold|Adding|Added|Watching)[:\s]+([A-Z]{2,5}(?:\s+[A-Z]{2,5})*)',
                content,
            )
            if direction_match:
                for group in direction_match:
                    for t in group.split():
                        t = t.strip()
                        if 2 <= len(t) <= 5 and t.isalpha() and t.isupper():
                            if t not in tickers_found:
                                tickers_found.append(t)
            # Also catch standalone uppercase tickers near trading keywords
            if not tickers_found:
                # Fallback: look for uppercase words near trading context
                words = re.findall(r'\b([A-Z]{2,5})\b', content)
                exclude = {
                    'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN',
                    'HAS', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE',
                    'WAY', 'WHO', 'BOT', 'GET', 'LET', 'PUT', 'SAY', 'USE', 'YES',
                    'BUY', 'SELL', 'HOLD', 'LONG', 'SHORT', 'DCA', 'USD', 'IMO',
                    'FWIW', 'ATH', 'ATL', 'EMA', 'RSI', 'MACD', 'SMA', 'GDP',
                    'CPI', 'IPO', 'CEO', 'CFO', 'ETF', 'OTM', 'ITM', 'ATM',
                }
                tickers_found = [w for w in words if w not in exclude][:5]
            if not tickers_found:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if dt > cutoff:
                    entry = {
                        "source": source,
                        "date": ts[:10],
                        "tickers": tickers_found[:5],
                        "content": content,
                        "author": msg.get('author', {}).get('username', 'Unknown'),
                    }
                    all_recent.append(entry)
                    for t in tickers_found[:5]:
                        ticker_map.setdefault(t, []).append(entry)
            except Exception:
                continue

    all_recent.sort(key=lambda x: x['date'], reverse=True)
    return ticker_map, all_recent


async def _show_recent_signals(ctx):
    """Show the most recent signals across all tickers from cached data."""
    ticker_map, all_recent = _collect_recent_messages(days=7)

    if not all_recent:
        return await ctx.send("📭 No signals in the last 7 days.")

    # Build a summary grouped by ticker with mention counts
    out = f"*Recent Signals* (last 7 days, {len(all_recent)} total messages)\n\n"

    # Sort tickers by mention count (most mentioned first)
    sorted_tickers = sorted(ticker_map.items(), key=lambda x: len(x[1]), reverse=True)

    for ticker, msgs in sorted_tickers[:15]:
        goku_count = sum(1 for m in msgs if m['source'] == 'Goku')
        wilson_count = sum(1 for m in msgs if m['source'] == 'Wilson')
        latest_date = msgs[0]['date'] if msgs else '?'
        source_str = []
        if goku_count:
            source_str.append(f"📊Goku:{goku_count}")
        if wilson_count:
            source_str.append(f"📈Wilson:{wilson_count}")
        out += f"• *${ticker}* — {' '.join(source_str)} (latest: {latest_date})\n"

    out += f"\n💡 Use `!signals TICKER` for details, `!daily` for trade analysis"
    await _safe_send(ctx, out)


async def _analyze_daily_signals(ctx, days: int = 3):
    """
    Analyze recent signals from Goku/Wilson channels and evaluate
    which tickers are worth trading.

    This is the core "signal intelligence" feature:
    1. Collects all recent signals (configurable lookback)
    2. Groups by ticker, counts bullish/bearish sentiment
    3. Fetches current price data for top tickers
    4. Calls LLM to evaluate trade worthiness
    5. Returns actionable summary
    """
    await _safe_send(ctx, f"🔍 Analyzing signals from Goku & Wilson (last {days} days)... (this may take a moment)")

    ticker_map, all_recent = _collect_recent_messages(days=days)

    if not all_recent:
        return await ctx.send(f"📭 No signals in the last {days} days to analyze.")

    # Sort by mention count — focus on most-discussed tickers
    sorted_tickers = sorted(ticker_map.items(), key=lambda x: len(x[1]), reverse=True)
    top_tickers = sorted_tickers[:8]  # Analyze top 8 tickers

    # Build signal summary for LLM
    signal_summaries = []
    for ticker, msgs in top_tickers:
        goku_msgs = [m for m in msgs if m['source'] == 'Goku']
        wilson_msgs = [m for m in msgs if m['source'] == 'Wilson']

        # Fetch current price context
        ctx_data = get_stock_context(ticker)
        price_str = ""
        if ctx_data and 'error' not in ctx_data:
            price_str = (
                f"Price: ${ctx_data.get('current_price', '?')} | "
                f"EMA8: ${ctx_data.get('ema8', '?')} | "
                f"EMA21: ${ctx_data.get('ema21', '?')} | "
                f"RSI: {ctx_data.get('rsi', '?')}"
            )

        goku_text = "\n".join(
            f"  [{m['date']}] {m['author']}: {m['content'][:150]}"
            for m in goku_msgs[:5]
        )
        wilson_text = "\n".join(
            f"  [{m['date']}] {m['author']}: {m['content'][:150]}"
            for m in wilson_msgs[:5]
        )

        signal_summaries.append(
            f"${ticker} ({len(msgs)} mentions, Goku:{len(goku_msgs)} Wilson:{len(wilson_msgs)})\n"
            f"  {price_str}\n"
            f"  Goku signals:\n{goku_text or '    None'}\n"
            f"  Wilson signals:\n{wilson_text or '    None'}"
        )

    signals_block = "\n\n".join(signal_summaries)

    # Add pattern library context for informed decisions
    pattern_context = ""
    try:
        pat_records = pattern_library.load_pattern_db()
        if pat_records:
            pat_stats = pattern_library.compute_pattern_stats(pat_records)
            pat_lines = []
            for ticker_name, msgs_list in top_tickers:
                raw_msgs = msgs_list
                found = {}
                for m in raw_msgs:
                    content = m.get('content', '')
                    recs = pattern_library.extract_patterns(content, ticker_name)
                    for rec in recs:
                        s = pat_stats.get(rec.pattern_name)
                        if s and s.tracked >= 3:
                            found[rec.pattern_name] = (rec.pattern_display, s.win_rate,
                                                        s.avg_return_5d, s.avg_return_10d, s.avg_return_20d)
                if found:
                    parts = []
                    for name, (display, wr, r5, r10, r20) in found.items():
                        best_hold = '5d' if r5 >= r10 and r5 >= r20 else ('10d' if r10 >= r20 else '20d')
                        parts.append(f"{display}: {wr:.0%} WR, best hold={best_hold}")
                    pat_lines.append(f"${ticker_name}: {'; '.join(parts)}")
            if pat_lines:
                pattern_context = "\n\nPATTERN LIBRARY (historical win rates from 1-year backtest):\n" + "\n".join(pat_lines)
    except Exception as e:
        log.debug(f"Pattern context error: {e}")

    prompt = f"""You are an elite trading desk analyst. Analyze these recent Discord signals
from two trader channels (Goku = technical, Wilson = fundamental) and determine
which tickers are worth trading TODAY.

RECENT SIGNALS (last {days} days):
{signals_block}
{pattern_context}

For each ticker, evaluate:
1. Signal strength (how many mentions, consistency of bias)
2. Technical setup (price vs EMAs, RSI)
3. Pattern history (use the PATTERN LIBRARY data — patterns with >70% WR are strong setups)
4. Risk level and recommended hold period (based on pattern best hold period)
5. Whether it's ACTIONABLE now or should WAIT

Format your response for Discord (<1800 chars):
*Daily Signal Analysis*

For each ticker use this format:
🟢 $TICKER — BUY (pattern name, WR%, entry zone, hold X days)
🔴 $TICKER — SELL/SHORT (reason, stop level)
🟡 $TICKER — WAIT (trigger to watch, expected setup)
⚪ $TICKER — NO TRADE (why skip)

End with a 1-line overall market read.
Use single asterisks for bold. Be specific with price levels and hold periods."""

    try:
        response = _call_minimax_anthropic(prompt, 2000, {}, "DAILY", [])
        content = ""
        if response.get("raw_response"):
            content = response["raw_response"]
        elif response.get("raw_llm"):
            content = response["raw_llm"]

        if content:
            content = content.replace("```", "").replace("**", "*")
            await _safe_send(ctx, content[:1900])
        else:
            # LLM failed — provide a basic summary without LLM
            await _send_basic_daily_summary(ctx, top_tickers)

    except Exception as e:
        log.warning(f"Daily analysis LLM failed: {e}")
        await _send_basic_daily_summary(ctx, top_tickers)


async def _send_basic_daily_summary(ctx, top_tickers):
    """Fallback: basic signal summary without LLM analysis."""
    out = "*Daily Signal Summary* (LLM unavailable — raw counts)\n\n"
    for ticker, msgs in top_tickers:
        goku = sum(1 for m in msgs if m['source'] == 'Goku')
        wilson = sum(1 for m in msgs if m['source'] == 'Wilson')
        ctx_data = get_stock_context(ticker)
        price_str = ""
        if ctx_data and 'error' not in ctx_data:
            price = ctx_data.get('current_price', '?')
            rsi = ctx_data.get('rsi', '?')
            price_str = f" — ${price} (RSI: {rsi})"

        out += f"• *${ticker}*{price_str} — {len(msgs)} mentions "
        out += f"(📊Goku:{goku} 📈Wilson:{wilson})\n"
        # Show latest signal snippet
        latest = msgs[0]['content'][:80].replace('\n', ' ')
        out += f"  ↳ _{latest}_...\n\n"

    out += "💡 Use `!analyze TICKER` for deep analysis on any ticker."
    await _safe_send(ctx, out)


@bot.command(name="positions")
async def cmd_positions(ctx):
    # Use /api/positions for market_price + P&L, fall back to /api/trade/status
    positions = await trade_executor.call_api("/api/positions")

    # /api/positions returns a list directly (not a dict)
    if isinstance(positions, dict) and positions.get("error"):
        return await ctx.send("⚠️ Trading Server not connected.")
    if not positions or (isinstance(positions, dict) and not positions.get("connected", True)):
        # Fallback to /api/trade/status
        r = await trade_executor.call_api("/api/trade/status")
        if not r.get("connected"):
            return await ctx.send("⚠️ Trading Server not connected.")
        positions = r.get("positions", [])

    if not positions:
        return await ctx.send("📭 No open positions.")

    # Get account info for net liquidation
    acct = await trade_executor.call_api("/api/account")
    net_liq = acct.get("net_liquidation", 0) if isinstance(acct, dict) else 0

    total_pnl = 0
    out = f"**Positions**"
    if net_liq:
        out += f" (Net Liq: ${net_liq:,.0f})"
    out += "\n\n"

    for p in positions:
        d = "LONG" if p.get("quantity", 0) >= 0 else "SHORT"
        qty = abs(p.get("quantity", 0))
        avg = p.get("avg_cost", 0)
        mkt = p.get("market_price", 0)
        pnl = p.get("pnl", 0)
        mkt_val = p.get("market_value", 0)
        total_pnl += pnl

        # P&L formatting
        pnl_str = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
        pnl_pct = ((mkt / avg - 1) * 100) if avg > 0 and mkt > 0 else 0
        pnl_icon = "🟢" if pnl >= 0 else "🔴"

        out += (
            f"{pnl_icon} **{p['symbol']}**: {d} {qty:.0f} "
            f"@ ${avg:.2f} → ${mkt:.2f} "
            f"({pnl_str}, {pnl_pct:+.1f}%)\n"
        )

    # Summary line
    total_icon = "🟢" if total_pnl >= 0 else "🔴"
    total_str = f"+${total_pnl:,.0f}" if total_pnl >= 0 else f"-${abs(total_pnl):,.0f}"
    out += f"\n{total_icon} **Total P&L**: {total_str}"

    await _safe_send(ctx, out)


@bot.command(name="orders")
async def cmd_orders(ctx):
    r = await trade_executor.call_api("/api/trade/status")
    if not r.get("connected"):
        return await ctx.send("⚠️ Trading Server not connected.")
    orders = r.get("open_orders", [])
    if not orders:
        return await ctx.send("📭 No open orders.")
    out = "**Open Orders**\n\n"
    for o in orders:
        p = f"${o['price']:.2f}" if o.get("price") else "MKT"
        out += f"• **{o['symbol']}**: {o['action']} {o['quantity']} @ {p} [{o['status']}]\n"
    await ctx.send(out)


@bot.command(name="pipeline")
async def cmd_pipeline(ctx):
    """Manually trigger the full signal pipeline. Auto-trades in paper mode."""
    await _safe_send(ctx, "🔄 Running full signal pipeline... (signals + scanner + ranking)")
    try:
        candidates = await _run_and_send_pipeline(channel=ctx)
        if not candidates:
            await _safe_send(ctx, "⚠️ Pipeline completed but no candidates generated.")
            return

        # Auto-trade in paper mode — skip approval for BUY candidates
        try:
            health = await trade_executor.call_api("/api/health")
            is_paper = health.get("trading_mode") == "paper"
        except Exception:
            is_paper = False

        if is_paper:
            buy_candidates = [c for c in candidates if c.action == "BUY"][:3]
            if buy_candidates:
                await _safe_send(ctx, f"\n📝 *Paper mode detected* — auto-executing top {len(buy_candidates)} BUY signals...")
                for c in buy_candidates:
                    try:
                        params = signal_pipeline.generate_order_params(c, "limit")
                        result = await trade_executor.call_api("/api/trade", method="POST", payload=params)
                        status = "✅" if result.get("success") else "⚠️"
                        msg = result.get("message", result.get("error", "sent"))
                        await _safe_send(
                            ctx,
                            f"{status} ${c.ticker}: {params['order_type']} @ ${params.get('price', 'MKT')} | "
                            f"Stop ${params.get('stop_loss')} | Target ${params.get('take_profit')} — {msg}"
                        )
                        signal_pipeline.save_approved(c.ticker, params)
                        await asyncio.sleep(1)
                    except Exception as e:
                        await _safe_send(ctx, f"❌ ${c.ticker} order failed: {str(e)[:100]}")
            else:
                await _safe_send(ctx, "📝 Paper mode — no candidates scored high enough for auto-trade (need score >= 6).")
        else:
            await _safe_send(ctx, "🔒 *Live mode* — use `!approve TICKER` to place orders after review.")

    except Exception as e:
        log.exception(f"Pipeline command error: {e}")
        await _safe_send(ctx, f"❌ Pipeline error: {str(e)[:200]}")


@bot.command(name="midday")
async def cmd_midday(ctx):
    """Run mid-day portfolio review manually."""
    await _safe_send(ctx, "☀️ Running mid-day portfolio review...")
    try:
        await portfolio_manager.run_midday_check(ctx.send)
    except Exception as e:
        log.exception(f"Mid-day command error: {e}")
        await _safe_send(ctx, f"❌ Check failed: {str(e)}")


@bot.command(name="approve")
async def cmd_approve(ctx, *args):
    """Approve pipeline candidate(s) and queue trade orders.

    Supports single or multiple tickers:
      !approve PBR
      !approve PBR HOOD NVDA
      !approve PBR market       (last arg = order type if 'market' or 'limit')
    """
    if not args:
        return await _safe_send(ctx, "Usage: `!approve TICKER [TICKER2 ...]` or `!approve TICKER market`")

    # Parse args: last arg might be order_type
    args_list = list(args)
    order_type = "limit"
    if args_list[-1].lower() in ("market", "limit", "mkt", "lmt"):
        order_type = "market" if args_list[-1].lower() in ("market", "mkt") else "limit"
        args_list = args_list[:-1]

    if not args_list:
        return await _safe_send(ctx, "Usage: `!approve TICKER [TICKER2 ...]`")

    tickers = [t.upper().replace("$", "") for t in args_list]

    # Load saved candidates
    candidates = signal_pipeline.load_candidates()
    if not candidates:
        return await _safe_send(ctx, "\u274c No pipeline candidates found. Run `!pipeline` first.")

    results = []
    for ticker in tickers:
        match = next((c for c in candidates if c.ticker == ticker), None)
        if not match:
            available = ", ".join(c.ticker for c in candidates[:10])
            results.append(f"\u274c {ticker} not in candidates. Available: {available}")
            continue

        params = signal_pipeline.generate_order_params(match, order_type)
        signal_pipeline.save_approved(ticker, params)

        try:
            result = await trade_executor.call_api("/api/trade", method="POST", payload=params)
            if result.get("success"):
                fill_info = result.get("message", "Order placed")
                lp = params.get("limit_price", 0)
                sl = params.get("stop_loss", 0)
                tgt = params.get("target", 0)
                results.append(
                    f"\u2705 *${ticker}* \u2014 {params.get('order_type', 'LMT')} "
                    f"@ ${lp:.2f} | Stop ${sl:.2f} | Target ${tgt:.2f} \u2014 {fill_info}"
                )
            else:
                error = result.get("error", "Unknown error")
                results.append(f"\u26a0\ufe0f ${ticker}: API returned: {error}")
        except Exception as e:
            results.append(f"\u26a0\ufe0f ${ticker}: saved locally, API failed: {str(e)[:80]}")

        await asyncio.sleep(1)  # Rate limit between orders

    count = len(tickers)
    header = f"\U0001f4cb *Approve Results* ({count} ticker{'s' if count > 1 else ''}):\n\n"
    await _safe_send(ctx, header + "\n".join(results))


@bot.command(name="portfolio")
async def cmd_portfolio(ctx):
    """Get portfolio management suggestions for existing positions."""
    # Get current positions
    positions = await trade_executor.call_api("/api/positions")
    if isinstance(positions, dict) and positions.get("error"):
        return await _safe_send(ctx, "⚠️ Trading Server not connected.")
    if not positions or not isinstance(positions, list):
        return await _safe_send(ctx, "📭 No open positions to manage.")

    # Load latest pipeline candidates
    candidates = signal_pipeline.load_candidates()

    # Generate suggestions
    suggestions = signal_pipeline.format_portfolio_suggestions(positions, candidates)
    await _safe_send(ctx, suggestions)


@bot.command(name="patterns")
async def cmd_patterns(ctx):
    """Show pattern win rates from historical signal tracking."""
    try:
        records = pattern_library.load_pattern_db()
        if not records:
            # Try ingesting first
            records = pattern_library.ingest_patterns_from_discord(DATA_FILE, [])
            if records:
                # Track outcomes for what we can
                records = signal_tracker.track_outcomes(records)
                pattern_library.save_pattern_db(records)

        if not records:
            return await _safe_send(ctx, "📭 No pattern data. Run `!learn` first.")

        stats = pattern_library.compute_pattern_stats(records)
        output = pattern_library.format_pattern_stats_discord(stats)
        await _safe_send(ctx, output)
    except Exception as e:
        await _safe_send(ctx, f"❌ Pattern error: {str(e)[:200]}")


@bot.command(name="learn")
async def cmd_learn(ctx):
    """Manually trigger the learning cycle — track outcomes + grade traders."""
    await _safe_send(ctx, "🧠 Running learning cycle... (batch-fetching prices, ~30s)")
    try:
        minimax_key = os.environ.get("MINIMAX_API_KEY", "")
        # Run with a timeout to prevent infinite hangs
        summary = await asyncio.wait_for(
            signal_tracker.run_daily_learning_cycle(DATA_FILE, api_key=minimax_key),
            timeout=120,  # 2 minute hard cap
        )
        for chunk in [summary[i:i+1900] for i in range(0, len(summary), 1900)]:
            await _safe_send(ctx, f"```\n{chunk}\n```")
    except asyncio.TimeoutError:
        await _safe_send(ctx, "⚠️ Learning cycle timed out (>2 min). Partial data saved. Try `!grades` or `!patterns` to see what was collected.")
    except Exception as e:
        log.exception(f"Learn command error: {e}")
        await _safe_send(ctx, f"❌ Learning error: {str(e)[:200]}")


@bot.command(name="grades")
async def cmd_grades(ctx):
    """Show trader performance grades based on signal outcomes."""
    try:
        records = signal_tracker.load_performance()
        if not records:
            return await _safe_send(ctx, "📭 No signal data yet. Run `!learn` first to start tracking.")

        grades = signal_tracker.compute_grades(records)
        output = signal_tracker.format_grades_for_discord(grades)
        await _safe_send(ctx, output)
    except Exception as e:
        await _safe_send(ctx, f"❌ Error loading grades: {str(e)[:200]}")


@bot.command(name="help_trading")
async def cmd_help(ctx):
    await ctx.send(HELP_TEXT)


# ===================================================================
# Help text
# ===================================================================

HELP_TEXT = (
    "**Trading Bot Commands**\n\n"
    "📊 **Analysis:**\n"
    "• `@bot $AAPL` — Analyze ticker\n"
    "• `!analyze NVDA` — Deep analysis\n"
    "• `!daily [N]` — Analyze last N days signals (default 3)\n\n"
    "📡 **Pipeline & Auto-Trading:**\n"
    "• `!pipeline` — Full pipeline (signals + scanner + auto-trade in paper)\n"
    "• `!approve TICKER` — Approve & place limit order\n"
    "• `!midday` — Run mid-day portfolio review\n"
    "• `!portfolio` — Position management suggestions\n\n"
    "🧠 **Self-Learning:**\n"
    "• `!learn` — Track outcomes + grade traders + analyze patterns\n"
    "• `!grades` — Trader performance report\n"
    "• `!patterns` — Pattern win rates (C&H, flag, breakout, etc.)\n\n"
    "🛒 **Manual Trading:**\n"
    "• `@bot buy HOOD` — Buy at EMA21\n"
    "• `@bot buy HOOD 2x` — 2 scaled entries\n"
    "• `!signals AAPL` / `!recent` — Browse signals\n\n"
    "📋 **Info:**\n"
    "• `!positions` — Positions with P&L\n"
    "• `!orders` — Open orders\n"
    "\n*Auto: Learning 4:45 PM | Pipeline 5:15 PM ET*"
)


# ===================================================================
# Main
# ===================================================================

def main():
    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set")
        sys.exit(1)
    log.info("Starting bot...")
    log.info(f"Trade API: {trade_executor.TRADE_API_URL}")
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.PrivilegedIntentsRequired:
        log.error("Enable MESSAGE CONTENT INTENT at https://discord.com/developers/applications")
        sys.exit(1)


if __name__ == "__main__":
    main()
