#!/bin/bash
# =============================================================================
# Export Runtime Data Snapshot for GCP Production
# =============================================================================
# Creates a timestamped data snapshot tarball and optionally uploads it to GCS.
#
# Expected env:
#   SAIYAN_DATA_ARCHIVE_URI=gs://your-bucket/saiyan-data
#
# Optional env files loaded automatically:
#   env.live.list
#   env.list
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/logs"
STAMP="$(date +%Y%m%dT%H%M%S)"
LOG_FILE="$LOG_DIR/data_snapshot_export_${STAMP}.log"

mkdir -p "$LOG_DIR"
export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:/snap/bin:${PATH:-}"

if [ -z "${SAIYAN_DATA_ARCHIVE_URI:-}" ]; then
  if [ -f "$PROJECT_DIR/env.live.list" ]; then
    set -a
    source "$PROJECT_DIR/env.live.list"
    set +a
  elif [ -f "$PROJECT_DIR/env.list" ]; then
    set -a
    source "$PROJECT_DIR/env.list"
    set +a
  fi
fi

if [ -z "${SAIYAN_DATA_ARCHIVE_URI:-}" ]; then
  echo "SAIYAN_DATA_ARCHIVE_URI is required" | tee -a "$LOG_FILE"
  exit 1
fi

echo "=== Exporting data snapshot ===" | tee -a "$LOG_FILE"
echo "Project: $PROJECT_DIR" | tee -a "$LOG_FILE"
echo "Archive: $SAIYAN_DATA_ARCHIVE_URI" | tee -a "$LOG_FILE"
echo "Time:    $(date -u +"%Y-%m-%dT%H:%M:%SZ")" | tee -a "$LOG_FILE"

cd "$PROJECT_DIR"
"$PROJECT_DIR/scripts/export_data_snapshot.sh" 2>&1 | tee -a "$LOG_FILE"

echo "=== Snapshot export complete ===" | tee -a "$LOG_FILE"
