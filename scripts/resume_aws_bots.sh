#!/bin/bash
# =============================================================================
# Resume AWS Discord Bots (run ON AWS)
# =============================================================================
# Starts interactive-bot and claude-agent after pause_aws_bots.sh.
#
# Usage (on AWS):
#   ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89
#   cd ~/trading-agent && bash scripts/resume_aws_bots.sh
# =============================================================================

set -euo pipefail

echo "=== Resuming AWS Discord bots ==="

sudo systemctl start interactive-bot.service
echo "  interactive-bot: started"

sudo systemctl start claude-agent.service
echo "  claude-agent: started"

echo ""
echo "Bots resumed. Status:"
systemctl status interactive-bot.service --no-pager -l | head -8
echo ""
systemctl status claude-agent.service --no-pager -l | head -8
