#!/bin/bash
# Start Trading Agent on GCloud VM (fixes "Trading Server not connected")
# Run when !prodstatus shows unreachable or after VM reboot.
#
# Usage: ./scripts/start_gcloud_trading.sh

set -euo pipefail

VM="trading-vm"
ZONE="us-east1-b"

echo "=== Starting Trading Agent on GCloud VM ==="
echo "  VM: $VM ($ZONE)"
echo ""

gcloud compute ssh "$VM" --zone="$ZONE" --command="
  sudo docker start saiyan-agent 2>/dev/null || true
  sleep 3
  sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'saiyan|NAMES'
"

echo ""
echo "Testing /api/health..."
curl -s -H "X-API-Key: saiyan-trade-2026" "http://34.75.9.166:8080/api/health" | python3 -m json.tool 2>/dev/null || echo "  (curl failed - check TRADE_API_URL)"

echo ""
echo "If connected: false:"
echo "  1. Approve 2FA on your IBKR mobile app (Gateway is waiting for it)"
echo "  2. Wait 1-2 min, then: sudo docker restart saiyan-agent"
echo "  See: scripts/fix_ib_gateway_2fa.md"
