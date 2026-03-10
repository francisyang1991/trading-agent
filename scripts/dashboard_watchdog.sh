#!/bin/bash
# =============================================================================
# Dashboard Watchdog — Auto-heal when saiyan-dashboard is unhealthy
# =============================================================================
# Three-layer self-healing:
#   L1: Docker restart: unless-stopped (handles crashes, OOM)
#   L2: Docker HEALTHCHECK (handles hung process)
#   L3: This script via cron (handles Docker daemon issues, disk full, network)
#
# Failure modes:
#   1. Container not running — crashed, OOM killed
#   2. Port 3000 not responding — hung Node.js event loop
#   3. Health endpoint degraded — upstream API or SQLite issues
#   4. Disk space low — SQLite WAL growth, Docker images
#
# Usage:
#   ./dashboard_watchdog.sh              # One-shot check + auto-fix
#   ./dashboard_watchdog.sh --dry-run    # Check only, no action
#
# Cron (every 5 min):
#   */5 * * * * /home/ubuntu/trading-agent/scripts/dashboard_watchdog.sh
# =============================================================================

set -euo pipefail

CONTAINER_NAME="saiyan-dashboard"
COMPOSE_DIR="/home/ubuntu/trading-agent"
HEALTH_URL="http://localhost:3000/api/health"
LOG_DIR="/var/log/saiyan-watchdog"
LOG_FILE="$LOG_DIR/dashboard_watchdog.log"
MAX_RESTARTS_PER_HOUR=3
RESTART_COUNT_FILE="$LOG_DIR/.dashboard_restart_count"

# Optional: Discord webhook for alerts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
WEBHOOK=""
[ -f "$REPO_DIR/.env" ] && WEBHOOK=$(grep "^WATCHDOG_DISCORD_WEBHOOK=" "$REPO_DIR/.env" 2>/dev/null | cut -d= -f2- || echo "")

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE" 2>/dev/null || echo "$*"
}

send_alert() {
    local msg="$1"
    [ -z "$WEBHOOK" ] && return
    curl -s -H "Content-Type: application/json" \
        -d "{\"content\": \"$msg\"}" "$WEBHOOK" >/dev/null 2>&1 || true
}

# Rate limit restarts (max N per hour)
check_restart_limit() {
    local now count last_reset
    now=$(date +%s)

    if [ ! -f "$RESTART_COUNT_FILE" ]; then
        echo "$now 0" > "$RESTART_COUNT_FILE"
        return 0
    fi

    read -r last_reset count < "$RESTART_COUNT_FILE"
    local elapsed=$((now - last_reset))

    if [ "$elapsed" -ge 3600 ]; then
        echo "$now 0" > "$RESTART_COUNT_FILE"
        return 0
    fi

    if [ "$count" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
        log "RATE LIMIT: $count restarts in the last hour. Manual intervention needed."
        send_alert "⚠️ **Dashboard watchdog rate limit**: $count restarts/hour. Manual fix needed."
        return 1
    fi
    return 0
}

increment_restart_count() {
    local now count last_reset
    now=$(date +%s)
    if [ -f "$RESTART_COUNT_FILE" ]; then
        read -r last_reset count < "$RESTART_COUNT_FILE"
        echo "$last_reset $((count + 1))" > "$RESTART_COUNT_FILE"
    else
        echo "$now 1" > "$RESTART_COUNT_FILE"
    fi
}

do_restart() {
    local reason="$1"
    log "ACTION: Restarting dashboard (reason: $reason)"

    if [ "$DRY_RUN" = true ]; then
        log "  [DRY-RUN] Would restart $CONTAINER_NAME"
        return 0
    fi

    if ! check_restart_limit; then
        return 1
    fi

    docker restart "$CONTAINER_NAME" 2>/dev/null || \
        (cd "$COMPOSE_DIR" && docker compose --profile dashboard up -d "$CONTAINER_NAME")

    increment_restart_count
    sleep 15

    if check_port; then
        log "  Restart OK — dashboard responding"
        send_alert "🟢 **Dashboard recovered**: restarted ($reason)"
        return 0
    else
        log "  Restart FAILED — still not responding"
        send_alert "🔴 **Dashboard restart failed**: ($reason). Manual fix needed."
        return 1
    fi
}

# --- Health Checks ---

check_container_running() {
    docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -q "true"
}

check_port() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$HEALTH_URL" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

check_disk() {
    local usage
    usage=$(df -h /home 2>/dev/null | awk 'NR==2{gsub(/%/, "", $5); print $5}' || echo "0")
    [ "${usage:-0}" -lt 90 ]
}

# --- Main ---

log "--- Dashboard watchdog check ---"

# Check 1: Is the container running?
if ! check_container_running; then
    log "FAIL: Container not running"
    do_restart "container not running"
    exit $?
fi

# Check 2: Is the port responding?
if ! check_port; then
    log "FAIL: Port 3000 not responding (container running but hung)"
    do_restart "port unresponsive"
    exit $?
fi

# Check 3: Is the health endpoint returning degraded?
health_json=$(curl -s --connect-timeout 5 "$HEALTH_URL" 2>/dev/null || echo "{}")
if echo "$health_json" | grep -q '"status":"degraded"'; then
    log "WARN: Health degraded — $health_json"
    send_alert "⚠️ **Dashboard degraded**: $(echo "$health_json" | head -c 300)"
    # Don't restart — cached data still works, upstream may recover
fi

# Check 4: Disk space
if ! check_disk; then
    log "WARN: Disk usage above 90%"
    send_alert "⚠️ **Dashboard disk warning**: above 90% full"
    if [ "$DRY_RUN" = false ]; then
        docker image prune -f >/dev/null 2>&1 || true
        log "  Pruned unused Docker images"
    fi
fi

log "OK: Dashboard healthy"
