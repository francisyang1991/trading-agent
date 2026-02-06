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

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
log = logging.getLogger("interactive-bot")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DATA_FILE = os.path.join(os.path.dirname(__file__), '../../data/real_discord_messages_goku_wilson_60d.txt')

GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"]
WILSON_CHANNELS = ["1211549165629476924"]

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
    """Get cached messages mentioning a ticker."""
    if not os.path.exists(DATA_FILE):
        return [], None  # No data file is not an error — scanner will provide analysis
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    patterns = [f"${ticker.upper()}", ticker.upper()]
    messages = []
    for channel_id, msgs in data.items():
        source = "Goku" if channel_id in GOKU_CHANNELS else "Wilson"
        for msg in msgs:
            content = msg.get('content', '')
            ts = msg.get('timestamp', '')
            if any(p in content.upper() for p in patterns):
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
      4. Combine and send results
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

    # Step 4: Combine and send
    if llm_text and scanner_text:
        # Both available: send scanner first, then LLM signal summary
        await _safe_send(channel, scanner_text)
        signal_header = f"\n📡 *Signal Intelligence* ({len(msgs)} Discord mentions):\n"
        # Trim LLM text to fit as a second message
        trimmed = llm_text[:1850]
        await _safe_send(channel, signal_header + trimmed)
    elif llm_text:
        # Only LLM available (GCloud offline)
        await _safe_send(channel, llm_text)
    elif scanner_text:
        # Only scanner available (no cached signals)
        await _safe_send(channel, scanner_text)
    else:
        # Both failed — last resort: basic price from yfinance
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


# ===================================================================
# Bot events
# ===================================================================

@bot.event
async def on_ready():
    log.info(f"Bot online: {bot.user} | Servers: {len(bot.guilds)}")
    log.info(f"Trade API: {trade_executor.TRADE_API_URL}")
    asyncio.create_task(_check_server())


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


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

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
        return await ctx.send("Usage: `!signals TICKER`")
    ticker = ticker.upper().replace("$", "")
    msgs, err = get_ticker_messages(ticker, days=30)
    if err:
        return await ctx.send(f"❌ {err}")
    if not msgs:
        return await ctx.send(f"📭 No signals for *${ticker}*.")
    out = f"*${ticker} Recent Signals* ({len(msgs)} total)\n\n"
    for m in msgs[:10]:
        e = "📊" if m['source'] == 'Goku' else "📈"
        out += f"{e} [{m['date']}] {m['content'][:100]}...\n\n"
    await ctx.send(out[:1900])


@bot.command(name="positions")
async def cmd_positions(ctx):
    r = await trade_executor.call_api("/api/trade/status")
    if not r.get("connected"):
        return await ctx.send("⚠️ Trading Server not connected.")
    positions = r.get("positions", [])
    if not positions:
        return await ctx.send("📭 No open positions.")
    out = f"**Positions** (Net Liq: ${r.get('net_liquidation', 0):,.0f})\n\n"
    for p in positions:
        d = "LONG" if p["quantity"] > 0 else "SHORT"
        out += f"• **{p['symbol']}**: {d} {abs(p['quantity']):.0f} @ ${p['avg_cost']:.2f}\n"
    await ctx.send(out)


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


@bot.command(name="help_trading")
async def cmd_help(ctx):
    await ctx.send(HELP_TEXT)


# ===================================================================
# Help text
# ===================================================================

HELP_TEXT = (
    "**Trading Bot Commands**\n\n"
    "📊 **Analysis** (no order):\n"
    "• `@bot $AAPL` — Analyze Apple\n"
    "• `!analyze NVDA` — Same\n\n"
    "🛒 **Single Entry:**\n"
    "• `@bot buy HOOD` — Buy at EMA21 support\n"
    "• `@bot sell TSLA` — Short at EMA8 resistance\n"
    "• `@bot buy HOOD 5000usd` — $5k worth\n"
    "• `@bot buy HOOD 50 shares` — Exactly 50 shares\n"
    "• `@bot buy HOOD $5k` — Same as 5000usd\n\n"
    "📊 **Scaled Entries:**\n"
    "• `@bot buy HOOD 2x` — 2 entries (40% scout + 60% confirm)\n"
    "• `@bot buy HOOD 3x` — 3 entries (tiered)\n"
    "• `@bot scale into HOOD` — Auto 2 entries\n"
    "• `@bot buy HOOD 10000usd 2x` — $10k split into 2 entries\n\n"
    "📋 **Info:**\n"
    "• `!positions` — Show IB positions\n"
    "• `!orders` — Show open orders\n"
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
