#!/bin/bash
# =============================================================================
# Data Health Check-in — Weekly/Monthly Job
# =============================================================================
# Runs the data foundation audit and saves a report. Use for weekly or monthly
# check-ins to monitor price/fundamental coverage and data quality.
#
# Cron (weekly, Sunday 2 AM):
#   0 2 * * 0 /path/to/trading_agent/scripts/data_health_checkin.sh
#
# Cron (monthly, 1st of month 2 AM):
#   0 2 1 * * /path/to/trading_agent/scripts/data_health_checkin.sh
#
# Usage (manual):
#   ./scripts/data_health_checkin.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${PROJECT_DIR}/logs"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOG_DIR}/data_health_${TIMESTAMP}.log"

echo "========================================" | tee "$LOG_FILE"
echo "Data Health Check-in" | tee -a "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

cd "$PROJECT_DIR"

if ! command -v "$PYTHON_BIN" &> /dev/null; then
    echo "ERROR: Python not found at $PYTHON_BIN" | tee -a "$LOG_FILE"
    exit 1
fi

"$PYTHON_BIN" tools/data_health_audit.py 2>&1 | tee -a "$LOG_FILE"

EXIT_CODE=${PIPESTATUS[0]}

echo "" | tee -a "$LOG_FILE"
echo "Finished: $(date)" | tee -a "$LOG_FILE"
echo "Report: results/picker/data_health_report.md" | tee -a "$LOG_FILE"
echo "Log: $LOG_FILE" | tee -a "$LOG_FILE"

exit $EXIT_CODE
