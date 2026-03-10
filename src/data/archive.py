"""
Snapshot export and restore utilities for non-git data artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class ExportItem:
    relative_path: str
    kind: str = "copy"  # copy | sqlite_backup
    optional: bool = True


DEFAULT_EXPORT_ITEMS = (
    ExportItem("data/stock_cache.db", kind="sqlite_backup"),
    ExportItem("ib_data_server/ib_historical.db", kind="sqlite_backup"),
    ExportItem("data/daily"),
    ExportItem("data/fundamental"),
    ExportItem("data/processed"),
    ExportItem("data/email"),
    ExportItem("data/picker/fundamental_snapshots"),
)

OPTIONAL_OUTPUT_ITEMS = (
    ExportItem("outputs"),
    ExportItem("results"),
)


def default_snapshot_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def _sqlite_backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src) as source_conn:
        with sqlite3.connect(dest) as dest_conn:
            source_conn.backup(dest_conn)


def _copy_item(src: Path, dest: Path, kind: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if kind == "sqlite_backup":
        _sqlite_backup(src, dest)
        return
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest)


def export_snapshot(
    project_root: Path,
    output_root: Path,
    tag: Optional[str] = None,
    archive_uri: Optional[str] = None,
    include_outputs: bool = False,
) -> Path:
    """
    Export ignored runtime data into a restorable tar.gz archive.
    """
    tag = tag or default_snapshot_tag()
    snapshot_root = output_root / tag
    repo_root = snapshot_root / "repo"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    repo_root.mkdir(parents=True, exist_ok=True)

    items = list(DEFAULT_EXPORT_ITEMS)
    if include_outputs:
        items.extend(OPTIONAL_OUTPUT_ITEMS)

    exported = []
    for item in items:
        src = project_root / item.relative_path
        if not src.exists():
            if item.optional:
                continue
            raise FileNotFoundError(f"Missing required export path: {src}")
        dest = repo_root / item.relative_path
        _copy_item(src, dest, item.kind)

    for exported_path in sorted(repo_root.rglob("*")):
        if not exported_path.is_file():
            continue
        rel = exported_path.relative_to(repo_root).as_posix()
        exported.append(
            {
                "path": rel,
                "size": exported_path.stat().st_size,
                "sha256": _sha256_file(exported_path),
            }
        )

    manifest = {
        "tag": tag,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "mode": os.environ.get("SAIYAN_DATA_MODE", "dev"),
        "file_count": len(exported),
        "files": exported,
    }
    manifest_path = snapshot_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    archive_path = output_root / f"{tag}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(snapshot_root, arcname=tag)

    if archive_uri:
        remote = archive_uri.rstrip("/") + f"/{archive_path.name}"
        subprocess.run(
            ["gcloud", "storage", "cp", str(archive_path), remote],
            check=True,
        )

    return archive_path


def resolve_latest_archive(archive_uri: str) -> str:
    """
    Resolve the lexicographically latest tarball under a GCS prefix.
    """
    proc = subprocess.run(
        ["gcloud", "storage", "ls", archive_uri.rstrip("/") + "/*.tar.gz"],
        check=True,
        capture_output=True,
        text=True,
    )
    candidates = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not candidates:
        raise FileNotFoundError(f"No snapshot archives found under {archive_uri}")
    return sorted(candidates)[-1]


def download_archive(
    archive_uri: str,
    download_dir: Path,
    tag: Optional[str] = None,
) -> Path:
    """
    Download a snapshot tarball from GCS into a local directory.
    """
    download_dir.mkdir(parents=True, exist_ok=True)
    remote = (
        archive_uri.rstrip("/") + f"/{tag}.tar.gz"
        if tag
        else resolve_latest_archive(archive_uri)
    )
    local_path = download_dir / Path(remote).name
    subprocess.run(
        ["gcloud", "storage", "cp", remote, str(local_path)],
        check=True,
    )
    return local_path


def restore_snapshot_archive(
    archive_path: Path,
    project_root: Path,
    backup_existing: bool = True,
) -> Path:
    """
    Extract a tarball and overlay its repo data paths back into the project.
    """
    staging_root = Path(tempfile.mkdtemp(prefix="saiyan_data_restore_"))
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(staging_root)

    extracted_dirs = [p for p in staging_root.iterdir() if p.is_dir()]
    if not extracted_dirs:
        raise FileNotFoundError(f"No snapshot payload found in {archive_path}")
    snapshot_root = extracted_dirs[0]
    repo_root = snapshot_root / "repo"
    manifest_path = snapshot_root / "manifest.json"
    if not manifest_path.exists() or not repo_root.exists():
        raise FileNotFoundError(f"Invalid snapshot archive structure: {archive_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest.get("files", []):
        rel = Path(item["path"])
        src = repo_root / rel
        dest = project_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if backup_existing and dest.exists():
            backup = dest.with_suffix(dest.suffix + ".bak")
            if backup.exists():
                backup.unlink()
            shutil.copy2(dest, backup)
        shutil.copy2(src, dest)
    return snapshot_root
