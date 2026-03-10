#!/bin/bash
# =============================================================================
# OpenClaw Watchdog — Auto-heal when gateway is unhealthy
# =============================================================================
# Detects common failure modes and restarts the gateway to recover.
#
# Failure modes (from ~/.openclaw/logs/gateway.err.log):
#   1. Port not responding — process hung or crashed
#   2. API rate limit — "API rate limit reached" (restart clears state)
#   3. LLM timeout — "LLM request timed out" (restart clears stuck request)
#   4. Telegram getUpdates timeout — "Request to 'getUpdates' timed out"
#      (restart reconnects long-poll)
#
# Usage:
#   ./openclaw_watchdog.sh           # One-shot check + auto-fix if needed
#   ./openclaw_watchdog.sh --dry-run # Check only, no restart
#
# Schedule: Run via launchd every 5 min (com.saiyan.openclaw-watchdog.plist)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$REPO_DIR/workspace/.env"
LOG_FILE="$REPO_DIR/logs/openclaw_watchdog.log"
ERR_LOG="$HOME/.openclaw/logs/gateway.err.log"
GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
LAUNCH_LABEL="ai.openclaw.gateway"
GUI_DOMAIN="gui/$(id -u)"

# Load webhook for alerts
WEBHOOK=""
[ -f "$ENV_FILE" ] && WEBHOOK=$(grep "^WATCHDOG_DISCORD_WEBHOOK=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || echo "")

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE" 2>/dev/null || echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

send_alert() {
    local msg="$1"
    [ -z "$WEBHOOK" ] && return
    curl -s -H "Content-Type: application/json" -d "{\"content\": \"$msg\"}" "$WEBHOOK" >/dev/null 2>&1 || true
}

# --- Health checks ---

check_port() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "http://127.0.0.1:$GATEWAY_PORT/" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

check_process() {
    local pid
    pid=$(launchctl print "$GUI_DOMAIN/$LAUNCH_LABEL" 2>/dev/null | grep "pid =" | awk '{print $3}' || echo "")
    [ -n "$pid" ] && [ "$pid" != "0" ]
}

# Check if recent errors indicate stuck state (last 500 lines of err log)
check_recent_errors() {
    [ ! -f "$ERR_LOG" ] && return 1
    local rate_limit count
    rate_limit=$(tail -500 "$ERR_LOG" 2>/dev/null | grep -c "rate limit reached" || echo "0")
    count=$(tail -500 "$ERR_LOG" 2>/dev/null | grep -cE "timed out|getUpdates.*timed out|LLM request timed out" || echo "0")
    # If 3+ rate limit or 5+ timeouts in recent log, consider stuck
    [ "${rate_limit:-0}" -ge 3 ] || [ "${count:-0}" -ge 5 ]
}

do_restart() {
    log "OpenClaw watchdog: RESTARTING gateway (reason: $1)"
    if [ "$DRY_RUN" = true ]; then
        log "  [DRY-RUN] Would run: launchctl bootout + bootstrap"
        return 0
    fi
    local plist="$HOME/Library/LaunchAgents/${LAUNCH_LABEL}.plist"
    [ ! -f "$plist" ] && log "  ERROR: plist not found at $plist" && return 1

    launchctl bootout "$GUI_DOMAIN/$LAUNCH_LABEL" 2>/dev/null || true
    sleep 3
    launchctl bootstrap "$GUI_DOMAIN" "$plist"
    sleep 5

    if check_port; then
        log "  Restart OK — gateway responding"
        send_alert "🟢 **OpenClaw recovered**: Gateway restarted successfully"
        return 0
    else
        log "  Restart FAILED — port still not responding"
        send_alert "🔴 **OpenClaw restart failed**: Gateway still down after restart"
        return 1
    fi
}

# --- Main ---

mkdir -p "$(dirname "$LOG_FILE")"

if check_port; then
    # Port OK — optionally check for error buildup (stuck but responding)
    if check_recent_errors; then
        log "OpenClaw responding but recent errors suggest stuck state — restarting"
        do_restart "error buildup"
    else
        [ "$DRY_RUN" = false ] && log "OpenClaw healthy — no action"
        exit 0
    fi
elif ! check_process; then
    log "OpenClaw process not running — restarting"
    do_restart "process down"
else
    log "OpenClaw process running but port not responding (hung) — restarting"
    do_restart "port unresponsive"
fi
