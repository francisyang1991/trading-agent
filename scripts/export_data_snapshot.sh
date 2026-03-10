#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:/snap/bin:${PATH:-}"

if [ -f "$REPO_DIR/env.live.list" ]; then
  set -a
  source "$REPO_DIR/env.live.list"
  set +a
elif [ -f "$REPO_DIR/env.list" ]; then
  set -a
  source "$REPO_DIR/env.list"
  set +a
fi

if [ -x "$REPO_DIR/venv/bin/python" ]; then
  PYTHON_BIN="$REPO_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "python3 is required"
  exit 1
fi

cd "$REPO_DIR"

ARCHIVE_URI="${SAIYAN_DATA_ARCHIVE_URI:-}"

ARGS=()
if [ -n "$ARCHIVE_URI" ]; then
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "gcloud is required when SAIYAN_DATA_ARCHIVE_URI is set"
    exit 1
  fi
  ARGS+=(--archive-uri "$ARCHIVE_URI")
fi

"$PYTHON_BIN" tools/export_data_snapshot.py "${ARGS[@]}" "$@"
