#!/bin/bash
# =============================================================================
# Deploy & Install Bot Services on AWS
# =============================================================================
# Run this ON THE AWS MACHINE after syncing code with sync_to_aws.sh.
#
# What it does:
#   1. Installs systemd service files for both Discord bots
#   2. Installs the health monitor as a systemd timer (every 5 min)
#   3. Starts (or restarts) the bots
#   4. Shows status
#
# Usage:
#   ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89
#   cd ~/trading-agent
#   bash scripts/deploy_bots_aws.sh
# =============================================================================

set -euo pipefail

REPO_DIR="$HOME/trading-agent"
SERVICES_DIR="$REPO_DIR/scripts/services"
SCRIPTS_DIR="$REPO_DIR/scripts"

echo "=== Deploying Discord Bots to systemd ==="
echo "  Repo: $REPO_DIR"
echo ""

# --- Validate prerequisites ---
if [ ! -f "$REPO_DIR/workspace/.env" ]; then
    echo "ERROR: $REPO_DIR/workspace/.env not found"
    echo "Create it from workspace/.env.example with your tokens"
    exit 1
fi

if [ ! -f "$REPO_DIR/venv/bin/python" ]; then
    echo "ERROR: Python venv not found at $REPO_DIR/venv"
    echo "Create it: python3 -m venv $REPO_DIR/venv && $REPO_DIR/venv/bin/pip install -r $REPO_DIR/requirements.txt"
    exit 1
fi

# --- Install service files ---
echo "[1/4] Installing systemd service files..."

for svc_file in "$SERVICES_DIR"/*.service; do
    svc_name=$(basename "$svc_file")
    echo "  Installing $svc_name"
    sudo cp "$svc_file" /etc/systemd/system/
done

# --- Install health monitor timer ---
echo "[2/4] Installing health monitor timer..."

# Create timer unit
sudo tee /etc/systemd/system/bot-health-monitor.timer > /dev/null << 'EOF'
[Unit]
Description=Bot Health Monitor — check every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
EOF

# Create one-shot service for the timer
sudo tee /etc/systemd/system/bot-health-monitor.service > /dev/null << EOF
[Unit]
Description=Bot Health Monitor — one-shot check

[Service]
Type=oneshot
User=ubuntu
ExecStart=/bin/bash $SCRIPTS_DIR/bot_health_monitor.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=bot-health-monitor
EOF

# Make health monitor executable
chmod +x "$SCRIPTS_DIR/bot_health_monitor.sh"

# --- Reload and enable ---
echo "[3/4] Enabling and starting services..."
sudo systemctl daemon-reload

# Enable services (start on boot)
sudo systemctl enable interactive-bot.service
sudo systemctl enable claude-agent.service
sudo systemctl enable bot-health-monitor.timer

# Restart bots (or start if not running)
sudo systemctl restart interactive-bot.service
echo "  interactive-bot: restarted"

sudo systemctl restart claude-agent.service
echo "  claude-agent: restarted"

# Start health monitor timer
sudo systemctl start bot-health-monitor.timer
echo "  bot-health-monitor timer: started"

# --- Show status ---
echo ""
echo "[4/4] Current status:"
echo ""
echo "--- interactive-bot ---"
systemctl status interactive-bot.service --no-pager -l | head -15
echo ""
echo "--- claude-agent ---"
systemctl status claude-agent.service --no-pager -l | head -15
echo ""
echo "--- health monitor ---"
systemctl list-timers bot-health-monitor.timer --no-pager
echo ""

echo "=== Deployment complete ==="
echo ""
echo "Useful commands:"
echo "  Status:     sudo systemctl status interactive-bot claude-agent"
echo "  Logs:       journalctl -u interactive-bot -f"
echo "              journalctl -u claude-agent -f"
echo "  Restart:    sudo systemctl restart interactive-bot"
echo "  Health:     bash $SCRIPTS_DIR/bot_health_monitor.sh"
echo "  Watch:      bash $SCRIPTS_DIR/bot_health_monitor.sh --watch"
echo "  Fix down:   bash $SCRIPTS_DIR/bot_health_monitor.sh --restart"
