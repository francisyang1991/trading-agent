#!/bin/bash
# =============================================================================
# Earnings Refresh Cron Job
# =============================================================================
# Runs incremental earnings refresh on weekdays to keep fundamental/earnings
# data in the database up to date. Uses technical universe CSV if present
# (from picker output), otherwise falls back to full listed universe.
#
# Usage (manual):
#   ./scripts/earnings_refresh_cron.sh
#
# Cron (weekdays 6 AM local):
#   0 6 * * 1-5 /path/to/trading_agent/scripts/earnings_refresh_cron.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${PROJECT_DIR}/logs"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%HMM%S)
LOG_FILE="${LOG_DIR}/earnings_refresh_${TIMESTAMP}.log"

echo "========================================" | tee "$LOG_FILE"
echo "Earnings Refresh Cron" | tee -a "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

cd "$PROJECT_DIR"

if ! command -v "$PYTHON_BIN" &> /dev/null; then
    echo "ERROR: Python not found at $PYTHON_BIN" | tee -a "$LOG_FILE"
    exit 1
fi

"$PYTHON_BIN" tools/incremental_earnings_refresh.py \
    --config config/picker_config.yaml \
    --skip-validation \
    --skip-scanner \
    --calendar-recent-days 2 \
    --months-back "3" \
    2>&1 | tee -a "$LOG_FILE"

EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "$LOG_FILE"
echo "Finished: $(date)" | tee -a "$LOG_FILE"
echo "Log: $LOG_FILE" | tee -a "$LOG_FILE"

exit $EXIT_CODE
