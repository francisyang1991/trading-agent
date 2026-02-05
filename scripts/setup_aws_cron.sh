#!/bin/bash
# Setup daily cron job for trading analysis bot on OpenClaw AWS
# Runs at 11:50 AM PST (18:50 UTC in winter, 19:50 UTC in summer)

SCRIPT_DIR="/home/ubuntu/trading-agent/workspace/scripts"
VENV_PYTHON="/home/ubuntu/trading-agent/venv/bin/python"
LOG_DIR="/home/ubuntu/trading-agent/logs"

echo "=== Setting up Daily Trading Bot Cron ==="

# Create log directory
mkdir -p $LOG_DIR

# Create wrapper script
cat > /home/ubuntu/trading-agent/run_daily_discord_bot.sh << 'EOF'
#!/bin/bash
export DISCORD_BOT_TOKEN="${DISCORD_BOT_TOKEN}"  # Set in ~/.bashrc or pass as env var
export MINIMAX_API_KEY="sk-cp-G8bxUw5-mlh3IrH9KR3HoYv1Y7FErDgFPjT33eOkeJIxWQDTbuw08m0zkIIV4KWnP6Q9NbHycVDKa9bZN2G5wJcQ2-Lh1uy_d78J3mkfeZyBNjkC8gODvII"

cd /home/ubuntu/trading-agent/workspace/scripts/report_generation
/home/ubuntu/trading-agent/venv/bin/python discord_daily_bot.py >> /home/ubuntu/trading-agent/logs/discord_bot.log 2>&1
EOF

chmod +x /home/ubuntu/trading-agent/run_daily_discord_bot.sh

# Add to crontab
# 11:50 AM PST = 19:50 UTC (during PST, winter time)
# 11:50 AM PDT = 18:50 UTC (during PDT, summer time)
# Using 18:50 UTC for PDT (March-November)
# Using 19:50 UTC for PST (November-March)

# For simplicity, we'll use a single time and note to adjust seasonally
# Current: PST (winter) = 19:50 UTC

(crontab -l 2>/dev/null | grep -v "discord_daily_bot\|run_daily_discord_bot"; echo "50 19 * * 1-5 /home/ubuntu/trading-agent/run_daily_discord_bot.sh") | crontab -

echo "Cron job added:"
crontab -l | grep discord

echo ""
echo "=== Setup Complete ==="
echo ""
echo "The bot will run at:"
echo "  - 11:50 AM PST / 19:50 UTC (Monday-Friday)"
echo "  - Sends to: Rich or Die (#1345123472019423284)"
echo ""
echo "IMPORTANT: Update DISCORD_BOT_TOKEN in:"
echo "  /home/ubuntu/trading-agent/run_daily_discord_bot.sh"
echo ""
echo "To test manually:"
echo "  /home/ubuntu/trading-agent/run_daily_discord_bot.sh"
echo ""
echo "To view logs:"
echo "  tail -f /home/ubuntu/trading-agent/logs/discord_bot.log"
