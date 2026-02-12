#!/bin/bash
# =============================================================================
# Bot Health Monitor — Check status + send Discord alerts
# =============================================================================
# Usage:
#   ./bot_health_monitor.sh              # One-shot status check
#   ./bot_health_monitor.sh --watch      # Continuous monitoring (every 60s)
#   ./bot_health_monitor.sh --restart    # Restart any down services
#
# Discord alerts: Set WATCHDOG_DISCORD_WEBHOOK env var for alerts
# =============================================================================

set -euo pipefail

# Config
SERVICES=("interactive-bot" "claude-agent")
WEBHOOK="${WATCHDOG_DISCORD_WEBHOOK:-}"
LOG_DIR="/home/ubuntu/trading-agent/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

timestamp() { date '+%Y-%m-%d %H:%M:%S %Z'; }

send_discord_alert() {
    local msg="$1"
    if [ -n "$WEBHOOK" ]; then
        curl -s -H "Content-Type: application/json" \
            -d "{\"content\": \"$msg\"}" \
            "$WEBHOOK" > /dev/null 2>&1 || true
    fi
}

check_service() {
    local svc="$1"
    local status
    local active
    local memory
    local uptime

    if ! systemctl is-enabled "$svc" &>/dev/null; then
        echo -e "  ${YELLOW}⚠ $svc${NC}: not installed (no systemd unit)"
        return 2
    fi

    active=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")

    case "$active" in
        active)
            # Get runtime details
            uptime=$(systemctl show "$svc" --property=ActiveEnterTimestamp --value 2>/dev/null || echo "?")
            memory=$(systemctl show "$svc" --property=MemoryCurrent --value 2>/dev/null || echo "?")
            if [ "$memory" != "?" ] && [ "$memory" != "[not set]" ] && [ "$memory" -gt 0 ] 2>/dev/null; then
                memory_mb=$((memory / 1024 / 1024))
                echo -e "  ${GREEN}✓ $svc${NC}: running (${memory_mb}MB, since $uptime)"
            else
                echo -e "  ${GREEN}✓ $svc${NC}: running (since $uptime)"
            fi
            return 0
            ;;
        failed)
            echo -e "  ${RED}✗ $svc${NC}: FAILED"
            # Show last 5 log lines
            journalctl -u "$svc" -n 5 --no-pager 2>/dev/null | while read -r line; do
                echo "    $line"
            done
            return 1
            ;;
        inactive)
            echo -e "  ${YELLOW}⚠ $svc${NC}: inactive (stopped)"
            return 1
            ;;
        *)
            echo -e "  ${RED}? $svc${NC}: $active"
            return 1
            ;;
    esac
}

do_status() {
    echo "=== Bot Health Monitor — $(timestamp) ==="
    echo ""

    local all_healthy=true
    local down_list=""

    for svc in "${SERVICES[@]}"; do
        if ! check_service "$svc"; then
            all_healthy=false
            down_list="$down_list $svc"
        fi
    done

    echo ""

    # System resources
    echo "--- System Resources ---"
    echo "  CPU Load: $(cat /proc/loadavg 2>/dev/null | cut -d' ' -f1-3 || echo 'N/A')"
    echo "  Memory: $(free -m 2>/dev/null | awk '/Mem:/{printf "%dMB / %dMB (%.0f%%)", $3, $2, $3/$2*100}' || echo 'N/A')"
    echo "  Disk: $(df -h / 2>/dev/null | awk 'NR==2{printf "%s / %s (%s)", $3, $2, $5}' || echo 'N/A')"
    echo ""

    # Check for zombie Claude processes
    local zombies
    zombies=$(pgrep -f "claude.*-p" 2>/dev/null | wc -l || echo "0")
    if [ "$zombies" -gt 2 ]; then
        echo -e "  ${RED}⚠ WARNING: $zombies Claude CLI processes running (possible leak)${NC}"
        send_discord_alert "⚠️ **Bot Health Alert**: $zombies Claude CLI processes detected — possible process leak"
    fi

    # Send alert if anything is down
    if [ "$all_healthy" = false ]; then
        echo -e "${RED}ALERT: Services down:$down_list${NC}"
        send_discord_alert "🔴 **Bot Health Alert** — DOWN:$down_list — $(timestamp)"
    else
        echo -e "${GREEN}All bots healthy${NC}"
    fi

    return 0
}

do_restart() {
    echo "=== Restarting down services — $(timestamp) ==="
    for svc in "${SERVICES[@]}"; do
        active=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
        if [ "$active" != "active" ]; then
            echo "  Restarting $svc..."
            sudo systemctl restart "$svc"
            sleep 3
            new_status=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
            if [ "$new_status" = "active" ]; then
                echo -e "  ${GREEN}✓ $svc restarted successfully${NC}"
                send_discord_alert "🟢 **Bot Recovered**: $svc restarted successfully"
            else
                echo -e "  ${RED}✗ $svc failed to restart${NC}"
                send_discord_alert "🔴 **Bot FAILED to restart**: $svc — check logs with: journalctl -u $svc -n 50"
            fi
        else
            echo -e "  ${GREEN}✓ $svc already running${NC}"
        fi
    done
}

do_watch() {
    echo "Starting continuous monitoring (Ctrl+C to stop)..."
    while true; do
        clear
        do_status
        echo ""
        echo "Next check in 60s... (Ctrl+C to stop)"
        sleep 60
    done
}

# Main
case "${1:-}" in
    --watch|-w)
        do_watch
        ;;
    --restart|-r)
        do_restart
        ;;
    --help|-h)
        echo "Usage: $0 [--watch|--restart|--help]"
        echo "  (no args)   One-shot status check"
        echo "  --watch     Continuous monitoring every 60s"
        echo "  --restart   Restart any down services"
        ;;
    *)
        do_status
        ;;
esac
