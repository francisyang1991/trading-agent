#!/bin/bash
# =============================================================================
# Pause AWS Discord Bots (run ON AWS)
# =============================================================================
# Stops interactive-bot and claude-agent. Use resume_aws_bots.sh to start again.
#
# Usage (on AWS):
#   ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89
#   cd ~/trading-agent && bash scripts/pause_aws_bots.sh
# =============================================================================

set -euo pipefail

echo "=== Pausing AWS Discord bots ==="

sudo systemctl stop interactive-bot.service 2>/dev/null || true
echo "  interactive-bot: stopped"

sudo systemctl stop claude-agent.service 2>/dev/null || true
echo "  claude-agent: stopped"

# Disable from auto-start on boot (optional — comment out if you want them to auto-start after reboot)
# sudo systemctl disable interactive-bot.service
# sudo systemctl disable claude-agent.service

echo ""
echo "Bots paused. To resume: bash scripts/resume_aws_bots.sh"
echo "Status: systemctl status interactive-bot claude-agent"
