#!/usr/bin/env python3
"""
Export ignored runtime data into a restorable snapshot tarball.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.data.archive import export_snapshot


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SAIYAN data snapshot")
    parser.add_argument("--output-dir", default="artifacts/data_snapshots", help="Local output directory")
    parser.add_argument("--tag", default="", help="Optional snapshot tag")
    parser.add_argument("--archive-uri", default="", help="Optional GCS prefix (gs://bucket/path)")
    parser.add_argument("--include-outputs", action="store_true", help="Include outputs/ and results/")
    args = parser.parse_args()

    archive = export_snapshot(
        project_root=ROOT,
        output_root=ROOT / args.output_dir,
        tag=args.tag or None,
        archive_uri=args.archive_uri or None,
        include_outputs=bool(args.include_outputs),
    )
    print(f"Snapshot archive: {archive}")


if __name__ == "__main__":
    main()
