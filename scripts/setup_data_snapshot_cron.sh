#!/bin/bash
# =============================================================================
# Setup Data Snapshot Export Cron Job
# =============================================================================
# Adds a daily cron entry to export a runtime data snapshot on the GCP VM.
#
# Usage:
#   ./scripts/setup_data_snapshot_cron.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CRON_SCRIPT="${PROJECT_DIR}/scripts/data_snapshot_export_cron.sh"
EXPORT_SCRIPT="${PROJECT_DIR}/scripts/export_data_snapshot.sh"
CRON_SCHEDULE="${SAIYAN_DATA_SNAPSHOT_CRON:-30 1 * * *}"
CRON_LINE="${CRON_SCHEDULE} ${CRON_SCRIPT}"

if [ -f "$PROJECT_DIR/env.live.list" ]; then
  set -a
  source "$PROJECT_DIR/env.live.list"
  set +a
elif [ -f "$PROJECT_DIR/env.list" ]; then
  set -a
  source "$PROJECT_DIR/env.list"
  set +a
fi

if ! command -v crontab >/dev/null 2>&1; then
  echo "crontab is required"
  exit 1
fi

if [ -z "${SAIYAN_DATA_ARCHIVE_URI:-}" ]; then
  echo "SAIYAN_DATA_ARCHIVE_URI is required in env.live.list, env.list, or the current shell"
  exit 1
fi

echo "=== Setting up Data Snapshot Export Cron ==="
echo "Project:  $PROJECT_DIR"
echo "Script:   $CRON_SCRIPT"
echo "Schedule: $CRON_SCHEDULE"
echo "Archive:  $SAIYAN_DATA_ARCHIVE_URI"
echo ""

chmod +x "$CRON_SCRIPT"
chmod +x "$EXPORT_SCRIPT"

(crontab -l 2>/dev/null | grep -v "data_snapshot_export_cron"; echo "$CRON_LINE") | crontab -

echo "Cron job added:"
crontab -l | grep data_snapshot_export_cron

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To test manually:"
echo "  $CRON_SCRIPT"
echo ""
echo "Logs: $PROJECT_DIR/logs/data_snapshot_export_*.log"
