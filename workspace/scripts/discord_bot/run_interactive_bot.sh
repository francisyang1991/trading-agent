#!/bin/bash
# Run Interactive Discord Bot
# This bot responds to @mentions with stock analysis

# Load environment
source ~/trading-agent/workspace/.env
export DISCORD_BOT_TOKEN
export MINIMAX_API_KEY

# Run the bot
cd ~/trading-agent/workspace/scripts/discord_bot
~/trading-agent/venv/bin/python interactive_bot.py
