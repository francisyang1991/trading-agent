#!/bin/bash
# Switch to LIVE (production) mode using separate env.live.list credentials
# Run ON THE GCP VM after: gcloud compute ssh trading-vm --zone=us-east1-b
#
# Prerequisites:
#   1. Create env.live.list from env.live.example with your LIVE IBKR credentials
#   2. Ensure docker-compose.live.yaml exists (deploy from repo)
#
# WARNING: Live mode trades real money.

set -e

DIR="${TRADING_AGENT_DIR:-/home/francisyang/trading-agent}"
cd "$DIR" || { echo "Error: $DIR not found"; exit 1; }

echo "=== Switching to LIVE mode (env.live.list) ==="

if [ ! -f env.live.list ]; then
    echo "ERROR: env.live.list not found."
    echo "  1. Copy env.live.example to env.live.list"
    echo "  2. Edit env.live.list with your LIVE IBKR username and password"
    exit 1
fi

if grep -q "your_live_ibkr_username\|your_live_ibkr_password" env.live.list 2>/dev/null; then
    echo "ERROR: env.live.list still has placeholder credentials."
    echo "  Edit env.live.list and set IB_USERNAME and IB_PASSWORD to your LIVE account."
    exit 1
fi

# Use docker compose (v2) or docker-compose (v1)
if docker compose version &>/dev/null; then
    DC="docker compose"
else
    DC="docker-compose"
fi

# Stop current stack
echo "Stopping current services..."
sudo $DC -f docker-compose.yaml down 2>/dev/null || true

# Start with live profile (uses env.live.list)
if [ -f docker-compose.live.yaml ]; then
    echo "Starting with live profile (docker-compose.live.yaml)..."
    sudo $DC -f docker-compose.yaml -f docker-compose.live.yaml \
        --env-file env.live.list --profile full up -d
else
    echo "docker-compose.live.yaml not found. Using inline live config..."
    # Fallback: update env and restart
    sudo cp env.live.list .env.live
    export $(grep -v '^#' .env.live | xargs)
    sudo sed -i.bak "s/IB_TRADING_MODE=paper/IB_TRADING_MODE=live/" .env 2>/dev/null || true
    sudo sed -i.bak "s/IB_PORT=4004/IB_PORT=4001/" .env 2>/dev/null || true
    sudo cp env.live.list env.list
    sudo $DC -f docker-compose.yaml --env-file env.live.list --profile full up -d
fi

echo ""
echo "Waiting for IB Gateway (60-120s for login)..."
sleep 30
sudo docker logs saiyan-ibgateway 2>&1 | tail -10

echo ""
echo "Verify: curl -s http://localhost:8080/api/health"
echo "Then: !prodstatus in Discord"
