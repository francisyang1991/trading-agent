#!/bin/bash
# =============================================================================
# Daily Stock Scanner
# =============================================================================
# Runs the full universe scan and generates a report.
# Designed to be called by launchd or cron at 8 PM daily.
#
# Usage (manual):
#   ./scripts/daily_scan.sh
#
# Output:
#   Report saved to results/scan_YYYYMMDD_HHMMSS.txt
# =============================================================================

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${PROJECT_DIR}/logs"
RESULTS_DIR="${PROJECT_DIR}/results"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Ensure directories exist
mkdir -p "$LOG_DIR"
mkdir -p "$RESULTS_DIR"

# Log file with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOG_DIR}/scan_${TIMESTAMP}.log"

echo "========================================" | tee -a "$LOG_FILE"
echo "SAIYAN Daily Scanner" | tee -a "$LOG_FILE"
echo "Started: $(date)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

cd "$PROJECT_DIR"

# Check Python availability
if ! command -v "$PYTHON_BIN" &> /dev/null; then
    echo "ERROR: Python not found at $PYTHON_BIN" | tee -a "$LOG_FILE"
    exit 1
fi

# Run the scanner on full universe
echo "Running scanner on full universe..." | tee -a "$LOG_FILE"

"$PYTHON_BIN" tools/scanner.py --all 2>&1 | tee -a "$LOG_FILE"

SCAN_EXIT_CODE=${PIPESTATUS[0]}

if [ $SCAN_EXIT_CODE -eq 0 ]; then
    echo "" | tee -a "$LOG_FILE"
    echo "✅ Scan completed successfully!" | tee -a "$LOG_FILE"
    
    # Find the latest scan result
    LATEST_RESULT=$(ls -t "$RESULTS_DIR"/scan_*.txt 2>/dev/null | head -1)
    if [ -n "$LATEST_RESULT" ]; then
        echo "Report: $LATEST_RESULT" | tee -a "$LOG_FILE"
    fi
else
    echo "" | tee -a "$LOG_FILE"
    echo "❌ Scan failed with exit code: $SCAN_EXIT_CODE" | tee -a "$LOG_FILE"
fi

echo "" | tee -a "$LOG_FILE"
echo "Finished: $(date)" | tee -a "$LOG_FILE"
echo "Log saved to: $LOG_FILE"

exit $SCAN_EXIT_CODE
