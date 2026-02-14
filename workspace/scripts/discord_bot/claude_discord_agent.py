#!/usr/bin/env python3
"""
Claude Code Discord Agent
==========================
A Discord bot that runs on AWS and connects @mentions to the Claude Code CLI.
When a user @mentions @claudecode, the bot:
  1. Receives the message
  2. Pipes it to `claude -p` (non-interactive print mode)
  3. Sends Claude's response back to Discord

Claude Code CLI has full access to the trading-agent workspace, so it can:
  - Read/write/edit files
  - Run shell commands
  - Analyze code and data
  - Act as a full SDE assistant

Architecture:
  Discord User -> @claudecode -> bot on AWS -> claude CLI -> response -> Discord

Requirements:
  - Claude Code CLI installed: ~/.local/bin/claude
  - ANTHROPIC_API_KEY set in environment
  - DISCORD_BOT_TOKEN_CLAUDE set in environment
  - discord.py installed in venv: pip install discord.py

Usage:
  python claude_discord_agent.py
"""

import os
import sys
import json
import asyncio
import subprocess
import uuid
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ============================================================================
# LOGGING
# ============================================================================
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG to see all message events
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("claude-agent")

# ============================================================================
# CONFIGURATION
# ============================================================================

# Bot token (app ID: 1469202811207290911 - @claudecode)
# IMPORTANT: never hardcode secrets in source files.
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN_CLAUDE", "")
if not DISCORD_BOT_TOKEN:
    log.warning("DISCORD_BOT_TOKEN_CLAUDE is not set; bot will not connect.")

# Claude Code CLI path
CLAUDE_CLI = os.path.expanduser("~/.local/bin/claude")

# Workspace directory (Claude Code operates from home directory for full access)
WORKSPACE_DIR = os.path.expanduser("~")

# Additional directories Claude Code can access (parallel to trading-agent)
# These are added via --add-dir flags
ADDITIONAL_DIRS = [
    os.path.expanduser("~/trading-agent"),  # Keep trading-agent explicitly accessible
    # Add other common directories as needed
    # os.path.expanduser("~/projects"),
    # os.path.expanduser("~/scripts"),
]

# Safety limits
# MAX_BUDGET_PER_REQUEST removed - no budget limit
# MAX_TURNS_PER_REQUEST removed - no turn limit (Claude can work until task completes)
CLAUDE_TIMEOUT_SECONDS = 1200   # 20 minute timeout per request
DISCORD_MSG_LIMIT = 2000        # Discord character limit

# Proactive agent — channel to send unprompted messages to (Rich or Die)
PROACTIVE_CHANNEL_ID = int(os.environ.get("PROACTIVE_CHANNEL_ID", "1345123472019423284"))

# Proactive schedule (PST hours)
CODEBASE_REVIEW_HOUR_PST = 6   # 6:00 AM PST — daily codebase retrospective
MARKET_NEWS_HOURS_PST = [6, 10, 14]  # 6 AM, 10 AM, 2 PM PST

# Session tracking: map Discord thread/channel -> Claude session ID
SESSION_FILE = Path(__file__).parent / "claude_sessions.json"

# System prompt addition for Discord context
DISCORD_SYSTEM_PROMPT = """You are Claude Code, acting as an SDE assistant via Discord for the RichOrDie trading team.
You have full access to the entire home directory (~/*) and can read/write/edit any files and folders.

Key areas you can access:
- ~/trading-agent/ : Trading agent workspace (core logic, bots, analysis, configs)
- ~/* : All other directories in the home folder (projects, scripts, configs, etc.)

## TRADING SKILL — Stock Scanning & Analysis

When asked about stocks, what to trade, market opportunities, or portfolio management,
use the Pipeline Skill CLI tool. Read the full instructions first:

    cat ~/trading-agent/workspace/scripts/discord_bot/SKILL.md

Quick reference:
    cd ~/trading-agent/workspace/scripts/discord_bot
    python pipeline_skill.py scan 80          # Scan 80 stocks from universe + Discord
    python pipeline_skill.py candidates       # Show ranked candidates
    python pipeline_skill.py analyze TICKER   # Deep analyze one ticker
    python pipeline_skill.py approve T1 T2    # Approve tickers for trading
    python pipeline_skill.py positions        # Show open positions
    python pipeline_skill.py portfolio        # Portfolio health + suggestions

WORKFLOW for "what stocks to enter?":
1. Run: python pipeline_skill.py scan 80
2. Review output for BUY candidates with score >= 5.0
3. For top picks, run: python pipeline_skill.py analyze TICKER
4. Present ranked recommendations with entry price, stop, target, and conviction

## INTERNET SEARCH SKILL — Exa.ai

You can search the internet for real-time market news, research, and information.
Read the full instructions first:

    cat ~/trading-agent/workspace/scripts/discord_bot/SEARCH_SKILL.md

Quick reference:
    cd ~/trading-agent/workspace/scripts/discord_bot
    python exa_search.py search "NVDA earnings 2026"   # General search
    python exa_search.py news "AAPL NVDA TSLA"         # Stock news (7 days)
    python exa_search.py research "AI chip demand"      # Deep research
    python exa_search.py pulse                          # Today's market pulse

WORKFLOW for market-aware recommendations:
1. Run: python exa_search.py pulse                      # Get market context
2. Run: python exa_search.py news "TOP_TICKERS"         # Check news for top picks
3. Run: python pipeline_skill.py scan 80                # Scan with pipeline
4. Cross-reference news sentiment with pipeline scores for final recommendations

When responding:
- Be concise but thorough (Discord has a 2000 char limit per message)
- Use markdown formatting (Discord supports it)
- If asked to make code changes, actually make them using your tools
- You can work across multiple directories in parallel
- For stock analysis, USE pipeline_skill.py and exa_search.py — don't just reference raw data files
- Always confirm what you did and any files you changed
- You have full read/write permissions to the entire home directory
"""


# ============================================================================
# SESSION MANAGEMENT
# ============================================================================

def load_sessions():
    """Load session mapping from file."""
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_sessions(sessions):
    """Save session mapping to file."""
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f, indent=2)


def get_session_id(channel_id: str, thread_id: str = None) -> str:
    """Get or create a Claude session ID for a Discord channel/thread."""
    sessions = load_sessions()
    key = thread_id or channel_id

    if key not in sessions:
        sessions[key] = {
            "session_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "turn_count": 0,
        }
        save_sessions(sessions)

    return sessions[key]["session_id"]


def increment_turn(channel_id: str, thread_id: str = None):
    """Increment turn count for a session."""
    sessions = load_sessions()
    key = thread_id or channel_id
    if key in sessions:
        sessions[key]["turn_count"] = sessions[key].get("turn_count", 0) + 1
        sessions[key]["last_used"] = datetime.now(timezone.utc).isoformat()
        save_sessions(sessions)


# ============================================================================
# CLAUDE CODE CLI INTERFACE
# ============================================================================

async def run_claude(
    message: str,
    session_id: str = None,
    continue_session: bool = False,
) -> str:
    """
    Run Claude Code CLI in print mode and return the response.
    Uses MiniMax M2.5 model via Anthropic-compatible API.

    Args:
        message: The user's message/query
        session_id: UUID for session continuity
        continue_session: Whether to continue a previous session

    Returns:
        Claude's response as text
    """
    cmd = [
        CLAUDE_CLI,
        "-p",  # Print mode (non-interactive)
        "--output-format", "text",
        "--dangerously-skip-permissions",  # Headless mode, no permission prompts
        "--no-session-persistence",  # Don't persist sessions to disk
        # No --max-turns limit - Claude can work until task completes
        "--append-system-prompt", DISCORD_SYSTEM_PROMPT,
    ]

    # Add additional directories Claude can access (parallel to home directory)
    # Running from ~ gives access to everything, but --add-dir makes it explicit
    for add_dir in ADDITIONAL_DIRS:
        if os.path.exists(add_dir) and os.path.isdir(add_dir):
            cmd.extend(["--add-dir", add_dir])
            log.debug(f"Added directory access: {add_dir}")

    # No budget limit - removed MAX_BUDGET_PER_REQUEST

    # Build environment with MiniMax API settings
    env = os.environ.copy()
    env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
    # MiniMax M2.5 via Anthropic-compatible API
    env["ANTHROPIC_BASE_URL"] = "https://api.minimax.io/anthropic"
    env["ANTHROPIC_AUTH_TOKEN"] = os.environ.get(
        "MINIMAX_API_KEY",
        "sk-cp-htdF5-oZpUcqnZNM0T1qBmC6ZPp3iD3XsV6Mf7fjmKmdSDZy3AqWvBVTsQz3BSwZb-CSzpg6nnxvtntLReoCvHBOwQb2yNFH-rUVxI0ade1zDrLCcqMgPF0",
    )
    env["ANTHROPIC_MODEL"] = "MiniMax-M2.5"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["API_TIMEOUT_MS"] = "3000000"

    log.info(f"Running Claude CLI: msg_len={len(message)}")

    try:
        # Pipe message via stdin (required for headless mode)
        # Start in new process group so we can kill the entire tree on timeout
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKSPACE_DIR,
            env=env,
            start_new_session=True,  # Creates new process group for clean kill
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=message.encode("utf-8")),
                timeout=CLAUDE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.warning(f"Claude CLI timed out after {CLAUDE_TIMEOUT_SECONDS}s — killing process tree")
            # Kill the entire process group to handle child processes
            try:
                import signal as sig
                import os as _os
                # Try SIGTERM first (graceful)
                _os.killpg(_os.getpgid(process.pid), sig.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    # SIGKILL if still alive
                    _os.killpg(_os.getpgid(process.pid), sig.SIGKILL)
                    await asyncio.wait_for(process.wait(), timeout=5)
            except (ProcessLookupError, OSError):
                # Process already dead — that's fine
                pass
            except asyncio.TimeoutError:
                log.error("Process still alive after SIGKILL — possible zombie")
            return (
                f"Request timed out after {CLAUDE_TIMEOUT_SECONDS // 60} minutes. "
                "Try a simpler question or break it into smaller tasks."
            )

        response = stdout.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            log.error(f"Claude CLI error (exit {process.returncode}): {error_msg[:500]}")

            if "API key" in error_msg or "AUTH_TOKEN" in error_msg or "authentication" in error_msg.lower():
                return "MiniMax API key issue. Ask Franci to check the configuration."
            if "rate limit" in error_msg.lower():
                return "Rate limited by MiniMax API. Please wait a moment and try again."

            return f"Claude encountered an error. Details: {error_msg[:300]}"

        if not response:
            return "Claude returned an empty response. Try rephrasing your question."

        log.info(f"Claude response: {len(response)} chars")
        return response

    except FileNotFoundError:
        return f"Claude CLI not found at {CLAUDE_CLI}. It needs to be installed on the server."
    except Exception as e:
        log.error(f"Unexpected error running Claude: {e}")
        return f"Error running Claude: {str(e)[:200]}"


# ============================================================================
# PROACTIVE AGENT — Background scheduled tasks
# ============================================================================

def _utc_to_pst_hour(utc_dt: datetime) -> int:
    """Convert UTC datetime to approximate PST hour (no DST)."""
    return (utc_dt.hour - 8) % 24


async def _safe_send_chunked(channel, text: str, prefix: str = ""):
    """Send a long message to Discord, splitting into chunks if needed."""
    if prefix:
        text = f"{prefix}\n{text}"
    if not text or not text.strip():
        return
    chunks = split_message(text, DISCORD_MSG_LIMIT)
    for chunk in chunks:
        try:
            await channel.send(chunk)
            await asyncio.sleep(0.5)
        except Exception as e:
            log.error(f"Failed to send proactive message chunk: {e}")


def _get_portfolio_tickers() -> list:
    """
    Get current portfolio tickers from local data files.
    Checks pipeline candidates and approved trades for active tickers.
    """
    tickers = set()
    data_dir = os.path.expanduser("~/trading-agent/workspace/data")

    # From pipeline candidates
    candidates_file = os.path.join(data_dir, "pipeline_candidates.json")
    if os.path.exists(candidates_file):
        try:
            with open(candidates_file) as f:
                candidates = json.load(f)
            for c in candidates:
                t = c.get("ticker", "")
                if t:
                    tickers.add(t.upper())
        except Exception:
            pass

    # From approved trades
    approved_file = os.path.join(data_dir, "approved_trades.json")
    if os.path.exists(approved_file):
        try:
            with open(approved_file) as f:
                approved = json.load(f)
            for t in approved:
                ticker = t.get("ticker", "")
                if ticker:
                    tickers.add(ticker.upper())
        except Exception:
            pass

    # Fallback: common watchlist if nothing found
    if not tickers:
        tickers = {"AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOG", "SPY", "QQQ"}

    return sorted(tickers)


async def _fetch_market_news(tickers: list) -> str:
    """
    Fetch recent news for portfolio tickers using yfinance.
    Returns formatted news text for LLM analysis.
    """
    try:
        import yfinance as yf
    except ImportError:
        return "yfinance not available for news fetching."

    all_news = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=8)

    for ticker in tickers[:12]:  # Cap at 12 tickers to avoid slowness
        try:
            stock = yf.Ticker(ticker)
            news = stock.news
            if not news:
                continue
            for item in news[:3]:  # Top 3 per ticker
                title = item.get("title", "")
                publisher = item.get("publisher", "")
                link = item.get("link", "")
                pub_ts = item.get("providerPublishTime", 0)
                pub_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc) if pub_ts else None

                if pub_dt and pub_dt < cutoff:
                    continue  # Skip old news

                all_news.append(
                    f"[{ticker}] {title} — {publisher}"
                    + (f" ({pub_dt.strftime('%m/%d %H:%M')} UTC)" if pub_dt else "")
                )
        except Exception as e:
            log.debug(f"News fetch error for {ticker}: {e}")

    if not all_news:
        return ""

    return "\n".join(all_news[:30])


async def _proactive_codebase_review(client):
    """
    Daily codebase retrospective at CODEBASE_REVIEW_HOUR_PST.
    Uses Claude Code CLI to scan the trading-agent codebase and suggest improvements.
    Sends findings to the proactive Discord channel.
    """
    await asyncio.sleep(300)  # Wait 5 min after startup

    while True:
        try:
            now = datetime.now(timezone.utc)
            pst_hour = _utc_to_pst_hour(now)
            is_weekday = now.weekday() < 5

            if is_weekday and pst_hour == CODEBASE_REVIEW_HOUR_PST and now.minute <= 3:
                log.info("Proactive: Starting daily codebase retrospective")
                channel = client.get_channel(PROACTIVE_CHANNEL_ID)
                if not channel:
                    log.warning(f"Proactive channel {PROACTIVE_CHANNEL_ID} not found")
                    await asyncio.sleep(3600)
                    continue

                prompt = (
                    "You are doing a daily self-retrospective of the trading-agent codebase.\n"
                    "Scan ~/trading-agent/ and provide a brief improvement report.\n\n"
                    "Check these areas (be concise — Discord format, under 1800 chars total):\n"
                    "1. **Bugs/Errors**: Any Python syntax errors, import failures, or broken references?\n"
                    "2. **TODOs/FIXMEs**: List any unresolved TODO or FIXME comments.\n"
                    "3. **Config Issues**: Missing env vars, hardcoded secrets, stale paths?\n"
                    "4. **Code Quality**: Duplicated logic, dead code, or inconsistencies between files?\n"
                    "5. **Test Gaps**: Critical modules without tests?\n\n"
                    "Format your response as:\n"
                    "🔍 **Daily Codebase Review** — {date}\n\n"
                    "Then list findings grouped by severity (🔴 Critical, 🟡 Warning, 🟢 Suggestion).\n"
                    "End with a 1-line summary: 'Overall health: X/10'.\n"
                    "If everything looks good, say so — don't invent problems."
                )

                try:
                    response = await run_claude(message=prompt)
                    if response and not response.startswith("Error"):
                        await _safe_send_chunked(
                            channel, response,
                            prefix="🤖 **Proactive Codebase Review** (automated daily scan)"
                        )
                        log.info(f"Proactive codebase review sent ({len(response)} chars)")
                    else:
                        log.warning(f"Codebase review returned error: {response[:200]}")
                except Exception as e:
                    log.error(f"Codebase review Claude call failed: {e}")

                await asyncio.sleep(3600)  # Don't trigger again for 1 hour
            else:
                await asyncio.sleep(60)
        except Exception as e:
            log.error(f"Proactive codebase review error: {e}")
            await asyncio.sleep(600)


async def _proactive_market_news(client):
    """
    Market news monitor — runs every 4 hours during market hours.
    Fetches news for portfolio tickers, analyzes impact with MiniMax,
    and sends actionable alerts to Discord.
    """
    await asyncio.sleep(600)  # Wait 10 min after startup

    while True:
        try:
            now = datetime.now(timezone.utc)
            pst_hour = _utc_to_pst_hour(now)
            is_weekday = now.weekday() < 5

            if is_weekday and pst_hour in MARKET_NEWS_HOURS_PST and now.minute <= 3:
                log.info(f"Proactive: Market news scan ({pst_hour}:00 PST)")
                channel = client.get_channel(PROACTIVE_CHANNEL_ID)
                if not channel:
                    log.warning(f"Proactive channel {PROACTIVE_CHANNEL_ID} not found")
                    await asyncio.sleep(3600)
                    continue

                # Gather portfolio tickers and their news
                tickers = _get_portfolio_tickers()
                news_text = await _fetch_market_news(tickers)

                if not news_text:
                    log.info("No recent news found for portfolio tickers")
                    await asyncio.sleep(3600)
                    continue

                prompt = (
                    f"You are a trading desk analyst monitoring news for a portfolio.\n\n"
                    f"PORTFOLIO TICKERS: {', '.join(tickers)}\n\n"
                    f"RECENT NEWS:\n{news_text}\n\n"
                    f"Analyze which news items could materially affect the portfolio.\n"
                    f"For each significant item:\n"
                    f"1. Which ticker(s) are impacted\n"
                    f"2. Expected impact (bullish/bearish/neutral)\n"
                    f"3. Suggested action (hold/add/trim/watch)\n"
                    f"4. Urgency (act now vs monitor)\n\n"
                    f"Format for Discord (<1800 chars):\n"
                    f"📰 **Market News Scan** — {{time}} PST\n\n"
                    f"Use these icons:\n"
                    f"🔴 = bearish/risk   🟢 = bullish/opportunity   🟡 = monitor\n\n"
                    f"If nothing is material, send a 1-line 'all clear' message.\n"
                    f"Be specific with price implications. No filler."
                )

                try:
                    # Use Claude CLI for richer analysis (can check current prices too)
                    response = await run_claude(message=prompt)
                    if response and not response.startswith("Error"):
                        await _safe_send_chunked(
                            channel, response,
                            prefix="🤖 **Proactive Market Alert** (automated news scan)"
                        )
                        log.info(f"Proactive market news sent ({len(response)} chars)")
                    else:
                        log.warning(f"Market news analysis returned error: {response[:200]}")
                except Exception as e:
                    log.error(f"Market news Claude call failed: {e}")

                await asyncio.sleep(3600)  # Don't trigger again for 1 hour
            else:
                await asyncio.sleep(60)
        except Exception as e:
            log.error(f"Proactive market news error: {e}")
            await asyncio.sleep(600)


# ============================================================================
# DISCORD BOT
# ============================================================================

def run_bot():
    """Run the Discord bot that connects @mentions to Claude Code CLI."""
    try:
        import discord
    except ImportError:
        log.error("discord.py not installed. Run: pip install discord.py")
        sys.exit(1)

    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN_CLAUDE not set")
        sys.exit(1)

    intents = discord.Intents.default()
    intents.guilds = True
    intents.message_content = True  # Required to read message content (even for @mentions)
    # Note: Must enable "Message Content Intent" in Discord Developer Portal:
    # https://discord.com/developers/applications/1469202811207290911/bot

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info(f"Logged in as {client.user} (ID: {client.user.id})")
        log.info(f"Connected to {len(client.guilds)} servers")
        for guild in client.guilds:
            log.info(f"  - {guild.name} (ID: {guild.id})")
        log.info(f"Claude CLI: {CLAUDE_CLI}")
        log.info(f"Workspace: {WORKSPACE_DIR} (full home directory access)")
        log.info(f"Additional dirs: {', '.join(ADDITIONAL_DIRS) if ADDITIONAL_DIRS else 'none'}")
        log.info("Ready - listening for @mentions...")

        # Start proactive background tasks
        log.info("Starting proactive agent tasks...")
        asyncio.create_task(_proactive_codebase_review(client))
        asyncio.create_task(_proactive_market_news(client))
        log.info(f"  Codebase review: daily at {CODEBASE_REVIEW_HOUR_PST}:00 AM PST (weekdays)")
        log.info(f"  Market news: {MARKET_NEWS_HOURS_PST} PST (weekdays)")

    @client.event
    async def on_message(message):
        # Debug: log all messages (for troubleshooting)
        log.debug(f"Received message: author={message.author}, channel={message.channel}, content_preview={str(message.content)[:50] if message.content else 'None'}")
        
        # Ignore own messages
        if message.author == client.user:
            log.debug("Ignoring own message")
            return

        # Only respond to @mentions or DMs
        is_mention = client.user.mentioned_in(message) if client.user else False
        is_dm = isinstance(message.channel, discord.DMChannel)
        
        log.debug(f"is_mention={is_mention}, is_dm={is_dm}, bot_user_id={client.user.id if client.user else None}")

        if not is_mention and not is_dm:
            log.debug("Message is not a mention or DM, ignoring")
            return
        
        log.info(f"Processing @mention from {message.author} in {message.channel}")

        # Clean the mention tag from the message
        try:
            content = message.content or ""
            if not content:
                log.warning(f"Message from {message.author} has no content (message_content intent may be disabled)")
                await message.channel.send("⚠️ I received your @mention but couldn't read the message content. Please enable 'Message Content Intent' in the Discord Developer Portal: https://discord.com/developers/applications/1469202811207290911/bot")
                return
            
            if client.user:
                content = content.replace(f"<@{client.user.id}>", "").strip()
                content = content.replace(f"<@!{client.user.id}>", "").strip()

            if not content:
                content = "hello"

            author = str(message.author)
            channel_name = getattr(message.channel, "name", "DM")
            log.info(f"Message from {author} in #{channel_name}: {content[:100]}")
        except Exception as e:
            log.error(f"Error processing message: {e}", exc_info=True)
            return

        # Get or create session for this channel/thread
        thread_id = str(message.channel.id) if hasattr(message.channel, "parent_id") and message.channel.parent_id else None
        channel_id = str(message.channel.id)
        session_id = get_session_id(channel_id, thread_id)

        # Show typing indicator while Claude processes
        async with message.channel.typing():
            # Add context about who's asking
            full_message = f"[Discord user: {author} in #{channel_name}]\n{content}"

            # Check if this is a continuation
            sessions = load_sessions()
            key = thread_id or channel_id
            is_continuation = sessions.get(key, {}).get("turn_count", 0) > 0

            # Run Claude Code CLI
            response = await run_claude(
                message=full_message,
                session_id=session_id,
                continue_session=is_continuation,
            )

            # Track the turn
            increment_turn(channel_id, thread_id)

        # Send response (handle Discord's 2000 char limit)
        if len(response) <= DISCORD_MSG_LIMIT:
            try:
                await message.reply(response, mention_author=False)
            except discord.errors.HTTPException as e:
                log.error(f"Failed to send reply: {e}")
                # Try sending as a new message instead
                try:
                    await message.channel.send(response[:DISCORD_MSG_LIMIT])
                except Exception:
                    pass
        else:
            # Split into multiple messages
            chunks = split_message(response, DISCORD_MSG_LIMIT)
            for i, chunk in enumerate(chunks):
                try:
                    if i == 0:
                        await message.reply(chunk, mention_author=False)
                    else:
                        await message.channel.send(chunk)
                    # Small delay between chunks to avoid rate limits
                    if i < len(chunks) - 1:
                        await asyncio.sleep(0.5)
                except discord.errors.HTTPException as e:
                    log.error(f"Failed to send chunk {i+1}/{len(chunks)}: {e}")
                    break

    log.info("Starting Claude Code Discord Agent...")
    client.run(DISCORD_BOT_TOKEN, log_handler=None)


def split_message(text: str, limit: int = 2000) -> list:
    """
    Split a long message into chunks that fit Discord's character limit.
    Tries to split on newlines or code block boundaries for cleaner output.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        # Try to split at a code block boundary
        split_pos = remaining[:limit].rfind("\n```\n")
        if split_pos > limit // 2:
            split_pos += 1  # Include the newline
        else:
            # Try to split at a double newline
            split_pos = remaining[:limit].rfind("\n\n")
            if split_pos > limit // 2:
                split_pos += 1
            else:
                # Try single newline
                split_pos = remaining[:limit].rfind("\n")
                if split_pos > limit // 2:
                    split_pos += 1
                else:
                    # Hard split
                    split_pos = limit

        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:]

    return chunks


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    # Quick sanity checks
    if not Path(CLAUDE_CLI).exists():
        log.error(f"Claude CLI not found at {CLAUDE_CLI}")
        log.error("Install it: curl -fsSL https://claude.ai/install.sh | bash")
        sys.exit(1)

    # MiniMax API key is configured in ~/.claude/settings.json and in run_claude()
    log.info("Using MiniMax M2.5 model via Claude Code CLI")

    run_bot()
