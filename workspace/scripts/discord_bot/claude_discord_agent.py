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
from datetime import datetime, timezone
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

# Session tracking: map Discord thread/channel -> Claude session ID
SESSION_FILE = Path(__file__).parent / "claude_sessions.json"

# System prompt addition for Discord context
DISCORD_SYSTEM_PROMPT = """You are Claude Code, acting as an SDE assistant via Discord for the RichOrDie trading team.
You have full access to the entire home directory (~/*) and can read/write/edit any files and folders.

Key areas you can access:
- ~/trading-agent/ : Trading agent workspace (core logic, bots, analysis, configs)
- ~/* : All other directories in the home folder (projects, scripts, configs, etc.)

When responding:
- Be concise but thorough (Discord has a 2000 char limit per message)
- Use markdown formatting (Discord supports it)
- If asked to make code changes, actually make them using your tools
- You can work across multiple directories in parallel
- For stock analysis questions, reference the trading signals in ~/trading-agent/workspace/data/
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
    Uses MiniMax M2.1 model via Anthropic-compatible API.

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
    # MiniMax M2.1 via Anthropic-compatible API
    env["ANTHROPIC_BASE_URL"] = "https://api.minimax.io/anthropic"
    env["ANTHROPIC_AUTH_TOKEN"] = os.environ.get(
        "MINIMAX_API_KEY",
        "sk-cp-htdF5-oZpUcqnZNM0T1qBmC6ZPp3iD3XsV6Mf7fjmKmdSDZy3AqWvBVTsQz3BSwZb-CSzpg6nnxvtntLReoCvHBOwQb2yNFH-rUVxI0ade1zDrLCcqMgPF0",
    )
    env["ANTHROPIC_MODEL"] = "MiniMax-M2.1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["API_TIMEOUT_MS"] = "3000000"

    log.info(f"Running Claude CLI: msg_len={len(message)}")

    try:
        # Pipe message via stdin (required for headless mode)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKSPACE_DIR,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=message.encode("utf-8")),
                timeout=CLAUDE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return "Request timed out (2 minute limit). Try a simpler question or break it into smaller tasks."

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
    log.info("Using MiniMax M2.1 model via Claude Code CLI")

    run_bot()
