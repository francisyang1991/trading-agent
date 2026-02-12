#!/bin/bash
# Run Interactive Discord Bot
# This bot responds to @mentions with stock analysis

set -euo pipefail

# Load environment — auto-export ALL variables from .env
set -a
source ~/trading-agent/workspace/.env
set +a

# Ensure critical env vars are present
if [ -z "${DISCORD_BOT_TOKEN:-}" ]; then
    echo "ERROR: DISCORD_BOT_TOKEN not set in .env" >&2
    exit 1
fi
if [ -z "${MINIMAX_API_KEY:-}" ]; then
    echo "WARNING: MINIMAX_API_KEY not set — LLM analysis will fall back to local" >&2
fi

# Log startup info
echo "[$(date)] Starting interactive bot..."
echo "  DISCORD_BOT_TOKEN: set (${#DISCORD_BOT_TOKEN} chars)"
echo "  DISCORD_USER_TOKEN: ${DISCORD_USER_TOKEN:+set}${DISCORD_USER_TOKEN:-NOT SET}"
echo "  MINIMAX_API_KEY: ${MINIMAX_API_KEY:+set}${MINIMAX_API_KEY:-NOT SET}"
echo "  TRADE_API_URL: ${TRADE_API_URL:-http://localhost:8080 (default)}"

# Run the bot
cd ~/trading-agent/workspace/scripts/discord_bot
exec ~/trading-agent/venv/bin/python -u interactive_bot.py
