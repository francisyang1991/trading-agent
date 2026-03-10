#!/bin/bash
# =============================================================================
# Bot Health Monitor (macOS) — launchd version
# =============================================================================
# Usage:
#   ./scripts/bot_health_mac.sh              # One-shot status check
#   ./scripts/bot_health_mac.sh --watch      # Continuous monitoring (every 60s)
#   ./scripts/bot_health_mac.sh --restart    # Restart any down services
#   ./scripts/bot_health_mac.sh --stop       # Stop all bot services
#
# Discord alerts: Set WATCHDOG_DISCORD_WEBHOOK env var in workspace/.env
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$REPO_DIR/logs"
ENV_FILE="$REPO_DIR/workspace/.env"
GUI_DOMAIN="gui/$(id -u)"

SERVICES=("ai.openclaw.gateway" "com.saiyan.interactive-bot" "com.saiyan.claude-agent")
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

# Load webhook from .env if available
WEBHOOK=""
if [ -f "$ENV_FILE" ]; then
    WEBHOOK=$(grep "^WATCHDOG_DISCORD_WEBHOOK=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo "")
fi

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
    local label="$1"
    local short_name="${label#com.saiyan.}"
    case "$label" in
        ai.openclaw.gateway) short_name="openclaw" ;;
    esac
    local plist="$LAUNCH_AGENTS/${label}.plist"

    if [ ! -f "$plist" ]; then
        echo -e "  ${YELLOW}⚠ $short_name${NC}: not installed (no plist at $plist)"
        return 2
    fi

    local info
    info=$(launchctl print "$GUI_DOMAIN/$label" 2>/dev/null) || {
        echo -e "  ${RED}✗ $short_name${NC}: not loaded into launchd"
        return 1
    }

    local pid
    pid=$(echo "$info" | grep "pid =" | awk '{print $3}')
    local state
    state=$(echo "$info" | grep "state =" | awk '{print $3}')

    if [ -n "$pid" ] && [ "$pid" != "0" ]; then
        local mem_rss=""
        mem_rss=$(ps -o rss= -p "$pid" 2>/dev/null || echo "")
        if [ -n "$mem_rss" ]; then
            local mem_mb=$((mem_rss / 1024))
            echo -e "  ${GREEN}✓ $short_name${NC}: running (pid=$pid, ${mem_mb}MB)"
        else
            echo -e "  ${GREEN}✓ $short_name${NC}: running (pid=$pid)"
        fi
        return 0
    else
        echo -e "  ${RED}✗ $short_name${NC}: state=$state (not running)"
        local log_file="$LOG_DIR/${short_name}-stderr.log"
        [ "$short_name" = "openclaw" ] && log_file="$HOME/.openclaw/logs/gateway.err.log"
        if [ -f "$log_file" ]; then
            echo "    Last 3 log lines:"
            tail -3 "$log_file" 2>/dev/null | while read -r line; do
                echo "      $line"
            done
        fi
        return 1
    fi
}

do_status() {
    echo "=== Bot Health Monitor (macOS) — $(timestamp) ==="
    echo "  Repo: $REPO_DIR"
    echo ""

    local all_healthy=true
    local down_list=""

    for svc in "${SERVICES[@]}"; do
        if ! check_service "$svc"; then
            all_healthy=false
            down_list="$down_list ${svc#com.saiyan.}"
        fi
    done

    echo ""
    echo "--- System Resources ---"
    echo "  CPU Load: $(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2, $3, $4}' || echo 'N/A')"
    local mem_info
    mem_info=$(vm_stat 2>/dev/null | awk '/Pages (active|inactive|wired|free):/{gsub(/\./,""); sum+=$NF} END{printf "%.0f MB used", sum*4096/1024/1024}' || echo "N/A")
    echo "  Memory: $mem_info"
    echo "  Disk: $(df -h / 2>/dev/null | awk 'NR==2{printf "%s / %s (%s)", $3, $2, $5}' || echo 'N/A')"
    echo ""

    # Check for zombie Claude processes
    local zombies
    zombies=$(pgrep -fc "claude.*-p" 2>/dev/null) || zombies=0
    if [ "${zombies}" -gt 2 ]; then
        echo -e "  ${RED}⚠ WARNING: $zombies Claude CLI processes running (possible leak)${NC}"
        send_discord_alert "⚠️ **Bot Health Alert**: $zombies Claude CLI processes detected on Mac Mini"
    fi

    if [ "$all_healthy" = false ]; then
        echo -e "${RED}ALERT: Services down:$down_list${NC}"
        send_discord_alert "🔴 **Bot Health Alert (Mac Mini)** — DOWN:$down_list — $(timestamp)"
    else
        echo -e "${GREEN}All bots healthy${NC}"
    fi
}

do_restart() {
    echo "=== Restarting services — $(timestamp) ==="
    for svc in "${SERVICES[@]}"; do
        local short="${svc#com.saiyan.}"
        case "$svc" in ai.openclaw.gateway) short="openclaw" ;; esac
        local plist="$LAUNCH_AGENTS/${svc}.plist"
        if [ ! -f "$plist" ]; then
            echo -e "  ${YELLOW}⚠ $short: no plist found, skipping${NC}"
            continue
        fi

        echo "  Restarting $short..."
        launchctl bootout "$GUI_DOMAIN/$svc" 2>/dev/null || true
        sleep 2
        launchctl bootstrap "$GUI_DOMAIN" "$plist"
        sleep 3

        local pid
        pid=$(launchctl print "$GUI_DOMAIN/$svc" 2>/dev/null | grep "pid =" | awk '{print $3}' || echo "")
        if [ -n "$pid" ] && [ "$pid" != "0" ]; then
            echo -e "  ${GREEN}✓ $short restarted (pid=$pid)${NC}"
            send_discord_alert "🟢 **Bot Recovered (Mac Mini)**: $short restarted (pid=$pid)"
        else
            echo -e "  ${RED}✗ $short failed to start${NC}"
            local errlog="$LOG_DIR/${short}-stderr.log"
            [ "$short" = "openclaw" ] && errlog="$HOME/.openclaw/logs/gateway.err.log"
            echo "    Check: tail -50 $errlog"
            send_discord_alert "🔴 **Bot FAILED to restart (Mac Mini)**: $short"
        fi
    done
}

do_stop() {
    echo "=== Stopping all bot services — $(timestamp) ==="
    for svc in "${SERVICES[@]}"; do
        local short="${svc#com.saiyan.}"
        echo "  Stopping $short..."
        launchctl bootout "$GUI_DOMAIN/$svc" 2>/dev/null && \
            echo -e "  ${GREEN}✓ $short stopped${NC}" || \
            echo -e "  ${YELLOW}⚠ $short was not running${NC}"
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

case "${1:-}" in
    --watch|-w)   do_watch ;;
    --restart|-r) do_restart ;;
    --stop|-s)    do_stop ;;
    --help|-h)
        echo "Usage: $0 [--watch|--restart|--stop|--help]"
        echo "  (no args)   One-shot status check"
        echo "  --watch     Continuous monitoring every 60s"
        echo "  --restart   Restart any down services"
        echo "  --stop      Stop all bot services"
        ;;
    *)            do_status ;;
esac
