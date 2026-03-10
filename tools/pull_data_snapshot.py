#!/usr/bin/env python3
"""
Download and restore a snapshot tarball from GCS for local development.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.data.archive import download_archive, restore_snapshot_archive


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull SAIYAN data snapshot from GCS")
    parser.add_argument("--archive-uri", required=True, help="GCS prefix, e.g. gs://bucket/saiyan-data")
    parser.add_argument("--tag", default="", help="Optional explicit snapshot tag")
    parser.add_argument("--download-dir", default="artifacts/data_snapshots/downloads", help="Local download cache")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak files for overwritten targets")
    args = parser.parse_args()

    archive = download_archive(
        archive_uri=args.archive_uri,
        download_dir=ROOT / args.download_dir,
        tag=args.tag or None,
    )
    snapshot_root = restore_snapshot_archive(
        archive_path=archive,
        project_root=ROOT,
        backup_existing=not args.no_backup,
    )
    print(f"Downloaded archive: {archive}")
    print(f"Restored snapshot: {snapshot_root}")


if __name__ == "__main__":
    main()
