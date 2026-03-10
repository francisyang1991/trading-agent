#!/usr/bin/env python3
"""
Interactive Discord Trading Bot
================================
Thin Discord event handler. All heavy logic lives in:
  - trade_parser.py   → command parsing
  - trade_executor.py → entry computation, API calls, reporting
  - message_routing.py → mention-intent routing
  - signal_message_utils.py → cached-signal parsing/collection
  - daily_signal_analysis.py → daily LLM prompt/report assembly
  - llm_shared.py → shared LLM adapter + stock context
  - scheduler_runtime.py → reusable scheduler loops
  - bot_shared.py → shared send/error helpers

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
import json
import socket
from pathlib import Path
import asyncio
import logging
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Trading agent root (for email analysis)
_script_dir = os.path.dirname(os.path.abspath(__file__))
_TRADING_AGENT_ROOT = os.path.abspath(os.path.join(_script_dir, '..', '..', '..'))
if _TRADING_AGENT_ROOT not in sys.path:
    sys.path.insert(0, _TRADING_AGENT_ROOT)

# Trading modules (same directory)
import trade_parser
import trade_executor
import signal_pipeline
import signal_tracker
import pattern_library
import auto_executor
import portfolio_manager
import message_routing
import daily_signal_analysis
from bot_shared import safe_send as _safe_send, notify_job_exception
from llm_shared import call_llm_raw, get_stock_context, extract_llm_text
from scheduler_runtime import DailySchedulerJob, run_daily_scheduler, run_interval_scheduler
from signal_message_utils import extract_ticker, get_ticker_messages, collect_recent_messages

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
log = logging.getLogger("interactive-bot")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_BOT_INSTANCE_ID = os.environ.get("BOT_INSTANCE_ID") or socket.gethostname()

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_USER_TOKEN = os.environ.get("DISCORD_USER_TOKEN", "")
DATA_FILE = os.path.join(os.path.dirname(__file__), '../../data/real_discord_messages_goku_wilson_60d.txt')

GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461", "1393715240474247249"]
WILSON_CHANNELS = ["1211549165629476924"]
ALL_SIGNAL_CHANNELS = set(GOKU_CHANNELS + WILSON_CHANNELS)

SCHEDULER_TZ_NAME = os.environ.get("SCHEDULER_TZ", "America/Los_Angeles")
SCHEDULER_TZ = ZoneInfo(SCHEDULER_TZ_NAME)

# Daily automation windows in Pacific time (PST/PDT via zoneinfo)
MORNING_PIPELINE_TIME = (7, 0)
MIDDAY_REVIEW_TIME = (12, 0)
LEARNING_CYCLE_TIME = (14, 45)
NIGHTLY_REVIEW_TIME = (15, 15)
AUTO_EXECUTION_TIME = (6, 35)  # 9:35 AM ET

# Timeouts (seconds)
PIPELINE_TIMEOUT_SECONDS = int(os.environ.get("PIPELINE_TIMEOUT_SECONDS", "1200"))

# Citrini email → Discord (optional)
CITRINI_EMAIL_ENABLED = os.environ.get("CITRINI_EMAIL_ENABLED", "false").lower() in ("1", "true", "yes")
CITRINI_EMAIL_INTERVAL_MIN = int(os.environ.get("CITRINI_EMAIL_INTERVAL_MIN", "60"))

# Signal cache: refresh every 6h, append-only; keep last Y days
SIGNAL_REFRESH_INTERVAL_SEC = int(os.environ.get("SIGNAL_REFRESH_INTERVAL_SEC", "21600"))  # 6 hours
SIGNAL_CACHE_DAYS = int(os.environ.get("SIGNAL_CACHE_DAYS", "60"))

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)
_tasks_started = False

# Guard: prevent duplicate !daily processing (e.g. double-send or two bots)
_daily_in_progress: set = set()


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

    FIX: Handles all response types from shared LLM adapter:
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
        response = call_llm_raw(prompt, 2000, stock_ctx, ticker, messages)
    except Exception as e:
        log.warning(f"LLM analysis failed for {ticker}: {e}")
        return ""

    # Extract content from response — handle raw text first
    content = extract_llm_text(response, max_chars=None)

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
        content = content.replace("**", "*")
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
        response = call_llm_raw(prompt, 2000, stock_ctx, ticker, [])
    except Exception as e:
        log.warning(f"Standalone LLM analysis failed for {ticker}: {e}")
        return ""

    content = extract_llm_text(response, max_chars=None)

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
        content = content.replace("**", "*")
        return content[:1900] + "..." if len(content) > 1900 else content

    return ""


def _now_local():
    """Current scheduler time in configured timezone."""
    return datetime.now(timezone.utc).astimezone(SCHEDULER_TZ)


def _is_due_today(last_run_date: str, hour: int, minute: int):
    """
    Return (should_run, today_key, local_now). A job is due once per weekday
    after its scheduled local time.
    """
    local_now = _now_local()
    if local_now.weekday() >= 5:
        return False, last_run_date, local_now

    today_key = local_now.date().isoformat()
    if last_run_date == today_key:
        return False, last_run_date, local_now

    if (local_now.hour, local_now.minute) < (hour, minute):
        return False, last_run_date, local_now

    return True, today_key, local_now


async def _get_digest_channel():
    """
    Resolve digest channel reliably. fetch_channel() is used as fallback when
    get_channel() cache is not populated.
    """
    channel = bot.get_channel(DIGEST_CHANNEL_ID)
    if channel is not None:
        return channel

    try:
        channel = await bot.fetch_channel(DIGEST_CHANNEL_ID)
        return channel
    except Exception as e:
        log.error(f"Cannot resolve digest channel {DIGEST_CHANNEL_ID}: {e}")
        return None


async def _notify_scheduler_error(job_label: str, error: Exception):
    """Unified scheduler error reporting to logs + Discord."""
    await notify_job_exception(
        job_label=job_label,
        error=error,
        resolve_channel_fn=_get_digest_channel,
        send_fn=lambda channel, text: _safe_send(channel, text, logger=log),
        logger=log,
    )


async def _send_daily_portfolio_status(channel, label: str = "Daily Portfolio Status"):
    """Send a compact portfolio status snapshot to Discord."""
    now_local = _now_local().strftime("%Y-%m-%d %H:%M %Z")
    positions_resp = await trade_executor.call_api("/api/positions")

    if isinstance(positions_resp, dict) and positions_resp.get("error"):
        await _safe_send(channel, f"⚠️ *{label}* ({now_local}) — could not fetch positions: {positions_resp['error']}")
        return

    positions = positions_resp if isinstance(positions_resp, list) else []
    if not positions:
        await _safe_send(channel, f"📭 *{label}* ({now_local}) — no open positions.")
        return

    acct = await trade_executor.call_api("/api/account")
    net_liq = acct.get("net_liquidation", 0) if isinstance(acct, dict) else 0

    lines = [f"*{label}* ({now_local})"]
    if net_liq:
        lines.append(f"💼 Net Liq: ${net_liq:,.0f}")

    total_pnl = 0.0
    for p in positions[:12]:
        symbol = p.get("symbol", "?")
        qty = p.get("quantity", 0)
        avg = p.get("avg_cost", 0)
        mkt = p.get("market_price", 0)
        pnl = p.get("pnl", 0)
        total_pnl += pnl

        side = "LONG" if qty >= 0 else "SHORT"
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        pnl_pct = ((mkt / avg - 1) * 100) if avg > 0 and mkt > 0 else 0
        lines.append(
            f"{pnl_icon} *{symbol}* {side} {abs(qty):.0f} @ ${avg:.2f} → ${mkt:.2f} ({pnl_pct:+.1f}%)"
        )

    total_icon = "🟢" if total_pnl >= 0 else "🔴"
    total_str = f"+${total_pnl:,.0f}" if total_pnl >= 0 else f"-${abs(total_pnl):,.0f}"
    lines.append(f"{total_icon} *Total Unrealized P&L:* {total_str}")

    await _safe_send(channel, "\n".join(lines))


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
    msgs, err = get_ticker_messages(DATA_FILE, ticker, GOKU_CHANNELS)

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
    global _tasks_started
    log.info(f"Bot online: {bot.user} | Servers: {len(bot.guilds)}")
    log.info(f"Trade API: {trade_executor.TRADE_API_URL}")

    if _tasks_started:
        log.info("Scheduler tasks already active; skipping duplicate startup on reconnect")
        return

    _tasks_started = True
    asyncio.create_task(_check_server())
    # Initial signal refresh (append-only; bootstraps empty channels)
    asyncio.create_task(_append_new_signals_only())
    # Periodic signal refresh (every 6h, append-only)
    asyncio.create_task(_periodic_signal_refresh())
    # Start morning pipeline scheduler (7:00 AM PT)
    asyncio.create_task(_morning_pipeline_scheduler())
    # Start nightly pipeline scheduler
    asyncio.create_task(_nightly_pipeline_scheduler())
    # Start daily learning cycle (2:45 PM PT, before nightly review)
    asyncio.create_task(_daily_learning_scheduler())
    # Start auto-execution scheduler (6:35 AM PT / 9:35 AM ET)
    asyncio.create_task(_auto_execute_scheduler())
    # Start mid-day portfolio check (12:00 PM PT)
    asyncio.create_task(_midday_check_scheduler())
    # Start Citrini email checker (new emails → LLM → Discord)
    if CITRINI_EMAIL_ENABLED:
        asyncio.create_task(_citrini_email_scheduler())
    # Send startup health report to Discord
    asyncio.create_task(_send_startup_report())


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


async def _send_startup_report():
    """Send a startup health report to Discord so user knows the bot is alive."""
    await asyncio.sleep(10)  # Wait for bot cache to populate
    try:
        channel = await _get_digest_channel()
        if not channel:
            log.error(f"STARTUP: Cannot resolve digest channel {DIGEST_CHANNEL_ID} — "
                       "ALL scheduled reports will fail! Check DIGEST_CHANNEL_ID and bot guild membership.")
            return

        local_now = _now_local()
        # Check trade API health
        try:
            health = await trade_executor.call_api("/api/health")
            api_status = "✅ Connected" if health.get("connected") else "⚠️ Reachable but IB disconnected"
            trading_mode = health.get("trading_mode", "unknown")
        except Exception as e:
            api_status = f"❌ Unreachable ({str(e)[:60]})"
            trading_mode = "N/A"

        guilds = ", ".join(g.name for g in bot.guilds[:5])
        report = (
            f"🚀 *Bot Online* — {local_now.strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 User: {bot.user}\n"
            f"🌐 Servers: {guilds}\n"
            f"📡 Trade API: {api_status} (mode: {trading_mode})\n"
            f"🕐 Timezone: {SCHEDULER_TZ_NAME}\n"
            f"\n*Scheduled Jobs (PST):*\n"
            f"  ⏰ 6:35 AM — Auto-execute approved trades\n"
            f"  ⏰ 7:00 AM — Morning signal pipeline\n"
            f"  ⏰ 12:00 PM — Mid-day portfolio review\n"
            f"  ⏰ 2:45 PM — Daily learning cycle\n"
            f"  ⏰ 3:15 PM — Nightly market review\n"
            f"\nAll jobs will report to this channel."
        )
        await _safe_send(channel, report)
        log.info("Startup report sent to Discord")
    except Exception as e:
        log.exception(f"Failed to send startup report: {e}")


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


def _normalize_msg(msg):
    """Ensure message has id, content, timestamp, author for cache compatibility."""
    return {
        "id": msg.get("id"),
        "content": msg.get("content", ""),
        "timestamp": msg.get("timestamp", ""),
        "author": msg.get("author", {}),
    }


async def _append_new_signals_only():
    """
    Append-only refresh: fetch only messages after the last known ID per channel.
    Trims cache to last SIGNAL_CACHE_DAYS. Runs every 6h to keep data fresh.
    """
    if not DISCORD_USER_TOKEN:
        log.warning("DISCORD_USER_TOKEN not set — skipping signal refresh")
        return

    import aiohttp
    headers = {"authorization": DISCORD_USER_TOKEN}
    cutoff = datetime.now(timezone.utc) - timedelta(days=SIGNAL_CACHE_DAYS)

    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
        else:
            data = {}
    except Exception:
        data = {}

    total_new = 0
    async with aiohttp.ClientSession() as session:
        for channel_id in ALL_SIGNAL_CHANNELS:
            existing = data.get(channel_id, [])
            existing_ids = {m.get("id") for m in existing}

            valid_ids = [m.get("id") for m in existing if m.get("id")]
            if existing and valid_ids:
                # Append-only: fetch messages after max ID
                max_id = max(valid_ids, key=lambda x: int(x))
                current_after = max_id
                while True:
                    url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=50&after={current_after}"
                    try:
                        async with session.get(url, headers=headers) as resp:
                            if resp.status == 200:
                                messages = await resp.json()
                            elif resp.status == 401:
                                log.warning("DISCORD_USER_TOKEN invalid (401) — cannot refresh signals")
                                break
                            else:
                                log.warning(f"Signal refresh channel {channel_id}: HTTP {resp.status}")
                                break
                    except Exception as e:
                        log.warning(f"Signal refresh error for {channel_id}: {e}")
                        break

                    if not messages:
                        break
                    for msg in messages:
                        mid = msg.get("id")
                        if mid and mid not in existing_ids:
                            existing.append(_normalize_msg(msg))
                            existing_ids.add(mid)
                            total_new += 1
                    current_after = messages[-1]["id"]
                    if len(messages) < 50:
                        break
                    await asyncio.sleep(1)
            else:
                # Bootstrap: fetch last 100 for empty channel
                url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=100"
                try:
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            messages = await resp.json()
                            for msg in messages:
                                mid = msg.get("id")
                                if mid and mid not in existing_ids:
                                    existing.append(_normalize_msg(msg))
                                    existing_ids.add(mid)
                                    total_new += 1
                        elif resp.status == 401:
                            log.warning("DISCORD_USER_TOKEN invalid (401) — cannot bootstrap signals")
                            break
                except Exception as e:
                    log.warning(f"Signal bootstrap error for {channel_id}: {e}")
                await asyncio.sleep(1)

            # Trim to last Y days
            trimmed = []
            for m in existing:
                ts = m.get("timestamp", "")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt > cutoff:
                        trimmed.append(m)
                except Exception:
                    trimmed.append(m)
            data[channel_id] = trimmed

    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
        if total_new > 0:
            log.info(f"Signal refresh: {total_new} new messages appended, cache trimmed to {SIGNAL_CACHE_DAYS}d")
        else:
            log.info("Signal refresh: no new messages, cache trimmed")
    except Exception as e:
        log.warning(f"Failed to save signal cache: {e}")


async def _periodic_signal_refresh():
    """
    Append-only signal refresh every 6 hours. Fetches only new messages
    after last known ID per channel; trims cache to last Y days.
    """
    async def _tick():
        log.info("Periodic signal refresh (append-only)")
        await _append_new_signals_only()
        return SIGNAL_REFRESH_INTERVAL_SEC

    await run_interval_scheduler(
        name="Periodic Signal Refresh",
        startup_delay_sec=60,
        tick_fn=_tick,
        default_interval_sec=SIGNAL_REFRESH_INTERVAL_SEC,
        error_interval_sec=300,
        on_error_fn=lambda e: _notify_scheduler_error("Periodic signal refresh", e),
        logger=log,
    )


# Target channel for nightly digest (Rich or Die)
DIGEST_CHANNEL_ID = int(os.environ.get("DIGEST_CHANNEL_ID", "1345123472019423284"))


async def _morning_pipeline_scheduler():
    """
    Run morning signal pipeline at 7:00 AM Pacific to queue pre-market ideas.
    """
    async def _run(_local_now):
        channel = await _get_digest_channel()
        if channel is None:
            log.error(f"Cannot find digest channel {DIGEST_CHANNEL_ID}")
            return
        try:
            await asyncio.wait_for(
                _run_and_send_pipeline(channel=channel, run_label="Morning Pipeline"),
                timeout=PIPELINE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            mins = max(1, PIPELINE_TIMEOUT_SECONDS // 60)
            await _safe_send(channel, f"⚠️ Morning Pipeline timed out after {mins} minutes.")
            log.error("Morning pipeline timed out")
            await _send_discord_only_digest(channel, "Morning Pipeline", days=3, reason="scanner timeout")

    await run_daily_scheduler(
        config=DailySchedulerJob(
            name="Morning Pipeline",
            hour=MORNING_PIPELINE_TIME[0],
            minute=MORNING_PIPELINE_TIME[1],
            startup_delay_sec=90,
        ),
        is_due_today_fn=_is_due_today,
        run_job_fn=_run,
        on_error_fn=lambda e: _notify_scheduler_error("Morning Pipeline", e),
        logger=log,
    )


async def _nightly_pipeline_scheduler():
    """
    Run full pipeline daily at 3:15 PM Pacific and send portfolio status after review.
    """
    async def _run(_local_now):
        channel = await _get_digest_channel()
        if channel is None:
            log.error(f"Cannot find digest channel {DIGEST_CHANNEL_ID}")
            return
        try:
            await asyncio.wait_for(
                _run_and_send_pipeline(
                    channel=channel,
                    run_label="Nightly Market Review",
                    send_portfolio_status=True,
                ),
                timeout=PIPELINE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            mins = max(1, PIPELINE_TIMEOUT_SECONDS // 60)
            await _safe_send(channel, f"⚠️ Nightly Market Review timed out after {mins} minutes.")
            log.error("Nightly pipeline timed out")
            await _send_discord_only_digest(channel, "Nightly Market Review", days=3, reason="scanner timeout")

    await run_daily_scheduler(
        config=DailySchedulerJob(
            name="Nightly Market Review",
            hour=NIGHTLY_REVIEW_TIME[0],
            minute=NIGHTLY_REVIEW_TIME[1],
            startup_delay_sec=120,
        ),
        is_due_today_fn=_is_due_today,
        run_job_fn=_run,
        on_error_fn=lambda e: _notify_scheduler_error("Nightly Market Review", e),
        logger=log,
    )


async def _auto_execute_scheduler():
    """
    Run auto-execution of approved trades at market open (6:35 AM Pacific / 9:35 AM ET).
    """
    async def _run(local_now):
        channel = await _get_digest_channel()
        send_fn = channel.send if channel else None
        if channel is None:
            log.error(f"Cannot find digest channel {DIGEST_CHANNEL_ID}; executing without Discord updates")
        else:
            await _safe_send(channel, f"⏰ *Auto-Execution Check* — {local_now.strftime('%H:%M %Z')}")
        await auto_executor.execute_approved_trades(send_fn)

    await run_daily_scheduler(
        config=DailySchedulerJob(
            name="Auto-Execution",
            hour=AUTO_EXECUTION_TIME[0],
            minute=AUTO_EXECUTION_TIME[1],
            startup_delay_sec=130,
        ),
        is_due_today_fn=_is_due_today,
        run_job_fn=_run,
        on_error_fn=lambda e: _notify_scheduler_error("Auto-execute", e),
        logger=log,
    )


async def _midday_check_scheduler():
    """
    Run portfolio check at noon Pacific.
    """
    async def _run(_local_now):
        channel = await _get_digest_channel()
        if channel is None:
            log.error(f"Cannot find digest channel {DIGEST_CHANNEL_ID}")
            return
        try:
            await asyncio.wait_for(
                portfolio_manager.run_midday_check(channel.send),
                timeout=120,
            )
        except asyncio.TimeoutError:
            await _safe_send(channel, "⚠️ Mid-day portfolio check timed out after 2 minutes.")
            log.error("Midday check timed out")

    await run_daily_scheduler(
        config=DailySchedulerJob(
            name="Mid-day Portfolio Check",
            hour=MIDDAY_REVIEW_TIME[0],
            minute=MIDDAY_REVIEW_TIME[1],
            startup_delay_sec=140,
        ),
        is_due_today_fn=_is_due_today,
        run_job_fn=_run,
        on_error_fn=lambda e: _notify_scheduler_error("Mid-day check", e),
        logger=log,
    )


async def _citrini_email_scheduler():
    """
    Periodically check for new Citrini emails, run LLM extraction, send trade ideas to Discord.
    """
    interval_sec = max(300, CITRINI_EMAIL_INTERVAL_MIN * 60)

    async def _tick():
        channel = await _get_digest_channel()
        if not channel:
            return interval_sec

        try:
            from src.email_analysis.citrini_discord import run_citrini_email_check
        except ImportError as e:
            log.warning(f"Citrini email check skipped (import error): {e}")
            return interval_sec

        root_dir = Path(_TRADING_AGENT_ROOT)
        client_secret = str(root_dir / "secret" / "client_secret_75860045039-mgs0h9aai4488pokfqgsqlb1doo41eh4.apps.googleusercontent.com.json")
        token_path = str(root_dir / "token.json")

        llm_provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "zai"
        messages, err = run_citrini_email_check(
            client_secret_path=client_secret,
            token_path=token_path,
            processed_path="data/email/citrini_processed.json",
            sender="citrini@substack.com",
            limit=10,
            unseen_only=True,
            llm_provider=llm_provider,
            root_dir=root_dir,
        )

        if err:
            log.warning(f"Citrini email check failed: {err}")
        elif messages:
            await _safe_send(channel, f"📬 *New Citrini Newsletter* — trade ideas extracted:\n")
            for msg in messages:
                await _safe_send(channel, msg)
                await asyncio.sleep(1)
            log.info(f"Citrini: sent {len(messages)} trade idea message(s) to Discord")
        return interval_sec

    await run_interval_scheduler(
        name="Citrini Email",
        startup_delay_sec=300,
        tick_fn=_tick,
        default_interval_sec=interval_sec,
        error_interval_sec=interval_sec,
        on_error_fn=lambda e: _notify_scheduler_error("Citrini email", e),
        logger=log,
    )


async def _daily_learning_scheduler():
    """
    Run learning cycle daily at 2:45 PM Pacific (before nightly review).
    Tracks signal outcomes, grades traders, processes chart images.
    Sends summary report to Discord digest channel.
    """
    async def _run(local_now):
        channel = await _get_digest_channel()
        if channel:
            await _safe_send(channel, f"🧠 *Daily Learning Cycle* starting at {local_now.strftime('%H:%M %Z')}...")

        minimax_key = os.environ.get("MINIMAX_API_KEY", "")
        try:
            summary = await asyncio.wait_for(
                signal_tracker.run_daily_learning_cycle(DATA_FILE, api_key=minimax_key),
                timeout=120,
            )
        except asyncio.TimeoutError:
            summary = "⚠️ Learning cycle timed out after 2 minutes."

        log.info(f"Learning cycle complete:\n{summary}")
        if channel and summary:
            report = f"🧠 *Daily Learning Cycle Complete*\n```\n{summary[:1700]}\n```"
            await _safe_send(channel, report)
        elif not channel:
            log.error(f"Cannot find digest channel {DIGEST_CHANNEL_ID} for learning report")

    await run_daily_scheduler(
        config=DailySchedulerJob(
            name="Daily Learning Cycle",
            hour=LEARNING_CYCLE_TIME[0],
            minute=LEARNING_CYCLE_TIME[1],
            startup_delay_sec=180,
        ),
        is_due_today_fn=_is_due_today,
        run_job_fn=_run,
        on_error_fn=lambda e: _notify_scheduler_error("Learning cycle", e),
        logger=log,
    )


async def _run_and_send_pipeline(channel=None, run_label="Signal Pipeline", send_portfolio_status=False):
    """Run the full signal pipeline and send results to Discord."""
    try:
        # Use cached signals (refreshed every 6h via _append_new_signals_only)
        # Run pipeline
        candidates, digest_messages = await signal_pipeline.run_full_pipeline(
            data_file=DATA_FILE,
            call_api_fn=trade_executor.call_api,
            llm_fn=call_llm_raw,
            discord_days=3,
        )

        # Find the channel to send to
        if channel is None:
            channel = await _get_digest_channel()

        if channel is None:
            log.warning("Could not find digest channel; pipeline will run without Discord output")
            return candidates

        run_time = _now_local().strftime("%Y-%m-%d %H:%M %Z")
        await _safe_send(channel, f"🤖 *{run_label}* triggered at {run_time}")

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
                        price = params.get("limit_price", "MKT")
                        await _safe_send(channel, f"{status} Auto-trade: ${c.ticker} {params['order_type']} @ ${price}")
                        signal_pipeline.save_approved(c.ticker, params)
                        await asyncio.sleep(1)
                    except Exception as e:
                        log.warning(f"Auto-trade {c.ticker} failed: {e}")
        except Exception as e:
            log.warning(f"Auto-trade check failed: {e}")

        if send_portfolio_status:
            await _send_daily_portfolio_status(channel, label="Post-Review Portfolio Status")

        log.info(f"{run_label} sent: {len(candidates)} candidates, {len(digest_messages)} messages")
        return candidates

    except Exception as e:
        log.exception(f"Pipeline error: {e}")
        if channel:
            await _safe_send(channel, f"❌ Pipeline error: {str(e)[:200]}")
        return []


async def _send_discord_only_digest(channel, run_label: str, days: int = 3, reason: str = ""):
    """Send a Discord-only digest when scanner times out or is unavailable."""
    if channel is None:
        return
    try:
        header = f"⚠️ *{run_label}* scanner timed out; sending Discord-only digest."
        if reason:
            header += f" ({reason})"
        await _safe_send(channel, header)

        discord_signals = await signal_pipeline.collect_discord_signals(DATA_FILE, days=days)
        candidates = signal_pipeline.build_candidates(discord_signals, {})
        digest_messages = signal_pipeline.format_nightly_digest(candidates)

        for msg in digest_messages:
            await _safe_send(channel, msg)
            await asyncio.sleep(1)

        await _safe_send(
            channel,
            f"✅ *{run_label}* Discord-only digest complete — {len(candidates)} candidates analyzed.",
        )
    except Exception as e:
        log.warning(f"Discord-only digest failed: {e}")
        try:
            await _safe_send(channel, f"❌ *{run_label}* fallback digest failed: {str(e)[:200]}")
        except Exception:
            pass


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # ---- Live signal collection from Goku/Wilson channels ----
    # (Only works if bot is in those servers; otherwise backfill handles it)
    if str(message.channel.id) in ALL_SIGNAL_CHANNELS:
        _save_signal_to_cache(message)

    if bot.user.mentioned_in(message):
        intent = message_routing.parse_mention_intent(
            message.content,
            trade_parse_fn=trade_parser.parse,
            ticker_extract_fn=extract_ticker,
        )

        if intent.kind == "trade" and intent.trade_command:
            cmd = intent.trade_command
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

        if intent.kind == "analyze" and intent.ticker:
            ticker = intent.ticker
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
    msgs, err = get_ticker_messages(DATA_FILE, ticker, GOKU_CHANNELS, days=30)
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


@bot.command(name="daily")
async def cmd_daily(ctx, days: int = 3):
    """Analyze recent signals. Usage: !daily [days] (default 3, max 30)."""
    days = max(1, min(days, 30))  # Clamp to 1-30
    await _analyze_daily_signals(ctx, days=days)


async def _show_recent_signals(ctx):
    """Show the most recent signals across all tickers from cached data."""
    ticker_map, all_recent = collect_recent_messages(DATA_FILE, GOKU_CHANNELS, days=7)

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
    channel_key = getattr(ctx.channel, "id", id(ctx.channel))
    if channel_key in _daily_in_progress:
        await _safe_send(ctx, "⏳ Already running a !daily analysis. Please wait for it to finish.")
        return
    _daily_in_progress.add(channel_key)
    try:
        await _safe_send(ctx, f"🔍 Analyzing signals from Goku & Wilson (last {days} days)... (this may take a moment)")

        # Use cached signals (refreshed every 6h via _append_new_signals_only)
        ticker_map, all_recent = collect_recent_messages(DATA_FILE, GOKU_CHANNELS, days=days)

        if not all_recent:
            return await ctx.send(f"📭 No signals in the last {days} days to analyze.")

        # Sort by mention count — focus on most-discussed tickers
        sorted_tickers = sorted(ticker_map.items(), key=lambda x: len(x[1]), reverse=True)
        top_tickers = sorted_tickers[:8]  # Analyze top 8 tickers

        content = daily_signal_analysis.run_daily_llm_analysis(
            top_tickers=top_tickers,
            days=days,
            llm_fn=call_llm_raw,
            get_stock_context_fn=get_stock_context,
            pattern_library_module=pattern_library,
            logger=log,
        )
        # Retry once before giving up; avoid sending noisy fallback output.
        if not content:
            await asyncio.sleep(1)
            content = daily_signal_analysis.run_daily_llm_analysis(
                top_tickers=top_tickers,
                days=days,
                llm_fn=call_llm_raw,
                get_stock_context_fn=get_stock_context,
                pattern_library_module=pattern_library,
                logger=log,
            )
        if content:
            await _safe_send(ctx, content[:1900])
        else:
            log.warning(
                f"!daily LLM returned no content (instance={_BOT_INSTANCE_ID}). "
                "If you see duplicate responses, another bot instance may have succeeded."
            )
            await _safe_send(
                ctx,
                "⚠️ `!daily` analysis is temporarily unavailable (LLM did not return usable output). "
                "Please retry in ~1 minute."
            )
    except Exception as e:
        log.warning(
            f"!daily LLM failed (instance={_BOT_INSTANCE_ID}): {e}. "
            "If you see duplicate responses, another bot instance may have succeeded."
        )
        await _safe_send(
            ctx,
            "⚠️ `!daily` analysis failed due to an LLM/API error. Please retry in ~1 minute."
        )
    finally:
        _daily_in_progress.discard(channel_key)


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
    """Manually trigger the full signal pipeline."""
    await _safe_send(ctx, "🔄 Running full signal pipeline... (signals + scanner + ranking)")
    try:
        candidates = await _run_and_send_pipeline(channel=ctx)
        if not candidates:
            await _safe_send(ctx, "⚠️ Pipeline completed but no candidates generated.")
            return

        await _safe_send(
            ctx,
            "✅ Pipeline run complete. "
            "Auto-trading (paper mode) is handled once inside the pipeline engine.",
        )

    except Exception as e:
        log.exception(f"Pipeline command error: {e}")
        await _safe_send(ctx, f"❌ Pipeline error: {str(e)[:200]}")


@bot.command(name="citrini")
async def cmd_citrini(ctx):
    """Manually check for new Citrini emails, run LLM, post trade ideas to Discord."""
    await _safe_send(ctx, "📬 Checking Citrini inbox for new emails...")
    try:
        from src.email_analysis.citrini_discord import run_citrini_email_check
    except ImportError as e:
        return await _safe_send(ctx, f"❌ Citrini module not available: {e}")

    root_dir = Path(_TRADING_AGENT_ROOT)
    client_secret = str(root_dir / "secret" / "client_secret_75860045039-mgs0h9aai4488pokfqgsqlb1doo41eh4.apps.googleusercontent.com.json")
    token_path = str(root_dir / "token.json")
    llm_provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "zai"

    messages, err = run_citrini_email_check(
        client_secret_path=client_secret,
        token_path=token_path,
        processed_path="data/email/citrini_processed.json",
        sender="citrini@substack.com",
        limit=10,
        unseen_only=True,
        llm_provider=llm_provider,
        root_dir=root_dir,
    )

    if err:
        return await _safe_send(ctx, f"❌ Citrini check failed: {err}")
    if not messages:
        return await _safe_send(ctx, "📭 No new Citrini emails to process.")
    await _safe_send(ctx, f"📬 *New Citrini Newsletter* — {len(messages)} email(s) processed:\n")
    for msg in messages:
        await _safe_send(ctx, msg)
        await asyncio.sleep(1)


@bot.command(name="portfolio")
async def cmd_portfolio(ctx, mode: str = ""):
    """
    Portfolio command:
    - !portfolio            -> position management suggestions
    - !portfolio midday     -> run midday portfolio review check
    """
    mode = (mode or "").lower().strip()
    if mode in {"midday", "review", "check"}:
        await _safe_send(ctx, "☀️ Running mid-day portfolio review...")
        try:
            await portfolio_manager.run_midday_check(ctx.send)
        except Exception as e:
            log.exception(f"Mid-day portfolio mode error: {e}")
            await _safe_send(ctx, f"❌ Check failed: {str(e)}")
        return

    if mode:
        return await _safe_send(ctx, "Usage: `!portfolio` or `!portfolio midday`")

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


@bot.command(name="prodstatus")
async def cmd_prodstatus(ctx):
    """
    Production account portfolio snapshot.
    Shows account mode, net liq, cash, buying power, and current positions.
    """
    health = await trade_executor.call_api("/api/health")
    if not isinstance(health, dict):
        return await _safe_send(ctx, "⚠️ Trading Server not reachable. Check TRADE_API_URL and that the GCloud VM is running.")
    if not health.get("connected"):
        return await _safe_send(
            ctx,
            "⚠️ Trading Server reachable but IB Gateway disconnected. "
            "Log in to IB Gateway on the GCloud VM (VNC or restart saiyan-ibgateway)."
        )

    mode = str(health.get("trading_mode", "unknown")).lower()
    mode_icon = "🟢" if mode == "live" else ("🟡" if mode == "paper" else "⚪")

    acct = await trade_executor.call_api("/api/account")
    positions = await trade_executor.call_api("/api/positions")
    if not isinstance(positions, list):
        positions = []

    lines = [f"{mode_icon} *Production Account Status*"]
    lines.append(f"Mode: *{mode.upper()}*")

    if isinstance(acct, dict) and not acct.get("error"):
        net_liq = acct.get("net_liquidation")
        cash = acct.get("cash")
        buying_power = acct.get("buying_power")
        excess_liq = acct.get("excess_liquidity")
        if net_liq is not None:
            lines.append(f"Net Liq: ${net_liq:,.0f}")
        if cash is not None:
            lines.append(f"Cash: ${cash:,.0f}")
        if buying_power is not None:
            lines.append(f"Buying Power: ${buying_power:,.0f}")
        if excess_liq is not None:
            lines.append(f"Excess Liquidity: ${excess_liq:,.0f}")

    if not positions:
        lines.append("\n📭 No open positions.")
        return await _safe_send(ctx, "\n".join(lines))

    total_pnl = 0.0
    lines.append(f"\n*Open Positions ({len(positions)}):*")
    for p in positions[:15]:
        symbol = p.get("symbol", "?")
        qty = float(p.get("quantity", 0))
        avg = float(p.get("avg_cost", 0) or 0)
        mkt = float(p.get("market_price", 0) or 0)
        pnl = float(p.get("pnl", 0) or 0)
        total_pnl += pnl
        side = "LONG" if qty >= 0 else "SHORT"
        pnl_icon = "🟢" if pnl >= 0 else "🔴"
        pnl_pct = ((mkt / avg - 1) * 100) if avg > 0 and mkt > 0 else 0
        lines.append(
            f"{pnl_icon} *{symbol}* {side} {abs(qty):.0f} @ ${avg:.2f} → ${mkt:.2f} ({pnl_pct:+.1f}%)"
        )

    total_icon = "🟢" if total_pnl >= 0 else "🔴"
    total_str = f"+${total_pnl:,.0f}" if total_pnl >= 0 else f"-${abs(total_pnl):,.0f}"
    lines.append(f"\n{total_icon} *Total Unrealized P&L:* {total_str}")
    if mode != "live":
        lines.append("\n⚠️ Note: account is not in LIVE mode.")

    await _safe_send(ctx, "\n".join(lines))


async def _run_research_patterns(ctx):
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
            return await _safe_send(ctx, "📭 No pattern data. Run `!research learn` first.")

        stats = pattern_library.compute_pattern_stats(records)
        output = pattern_library.format_pattern_stats_discord(stats)
        await _safe_send(ctx, output)
    except Exception as e:
        await _safe_send(ctx, f"❌ Pattern error: {str(e)[:200]}")


async def _run_research_learn(ctx):
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
        await _safe_send(
            ctx,
            "⚠️ Learning cycle timed out (>2 min). Partial data saved. "
            "Try `!research grades` or `!research patterns`.",
        )
    except Exception as e:
        log.exception(f"Learn command error: {e}")
        await _safe_send(ctx, f"❌ Learning error: {str(e)[:200]}")


async def _run_research_grades(ctx):
    """Show trader performance grades based on signal outcomes."""
    try:
        records = signal_tracker.load_performance()
        if not records:
            return await _safe_send(ctx, "📭 No signal data yet. Run `!research learn` first.")

        grades = signal_tracker.compute_grades(records)
        output = signal_tracker.format_grades_for_discord(grades)
        await _safe_send(ctx, output)
    except Exception as e:
        await _safe_send(ctx, f"❌ Error loading grades: {str(e)[:200]}")


@bot.command(name="research")
async def cmd_research(ctx, mode: str = "help"):
    """
    Unified research command namespace.

    Usage:
      !research learn
      !research grades
      !research patterns
    """
    mode = (mode or "help").lower().strip()
    if mode in {"help", "-h", "--help"}:
        return await _safe_send(
            ctx,
            "Usage: `!research learn|grades|patterns`\n"
            "• `learn` -> run learning cycle\n"
            "• `grades` -> show trader grades\n"
            "• `patterns` -> show pattern win rates",
        )

    if mode in {"learn", "cycle"}:
        return await _run_research_learn(ctx)
    if mode in {"grades", "grade"}:
        return await _run_research_grades(ctx)
    if mode in {"patterns", "pattern"}:
        return await _run_research_patterns(ctx)

    return await _safe_send(
        ctx,
        f"Unknown research mode: `{mode}`. Use `!research learn|grades|patterns`.",
    )


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
    "• `!signals AAPL` / `!signals recent` — Browse signals\n"
    "• `!daily [N]` — Analyze last N days signals (default 3)\n\n"
    "📡 **Pipeline & Auto-Trading:**\n"
    "• `!pipeline` — Full pipeline (signals + scanner + auto-trade in paper)\n"
    "• `!citrini` — Check Citrini emails, extract trade ideas, post to Discord\n"
    "• `!portfolio` — Position management suggestions\n"
    "• `!portfolio midday` — Run mid-day portfolio review\n\n"
    "• `!prodstatus` — Production account status snapshot\n\n"
    "🧠 **Self-Learning:**\n"
    "• `!research learn|grades|patterns` — Learning/grades/pattern stats\n\n"
    "🛒 **Manual Trading:**\n"
    "• `@bot buy HOOD` — Buy at EMA21\n"
    "• `@bot buy HOOD 2x` — 2 scaled entries\n"
    "• `@bot buy HOOD 5000usd` — Dollar-sized entry\n\n"
    "📋 **Info:**\n"
    "• `!positions` — Positions with P&L\n"
    "• `!orders` — Open orders\n"
    "\n*Auto (PT): Morning 7:00 | Midday 12:00 | Learning 2:45 | Review 3:15 | Open Exec 6:35*"
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
