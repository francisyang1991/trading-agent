import sqlite3
import tarfile
from pathlib import Path

from src.data.archive import export_snapshot, restore_snapshot_archive


def test_export_and_restore_snapshot(tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    data_dir = project / "data"
    data_dir.mkdir()
    daily_dir = data_dir / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "NVDA.csv").write_text("Date,Open\n2026-01-01,1\n", encoding="utf-8")

    db_path = data_dir / "stock_cache.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO test (value) VALUES ('ok')")
        conn.commit()

    output_root = tmp_path / "artifacts"
    archive_path = export_snapshot(project_root=project, output_root=output_root, tag="unit-test")
    assert archive_path.exists()
    assert tarfile.is_tarfile(archive_path)

    restore_project = tmp_path / "restore"
    restore_project.mkdir()
    restore_snapshot_archive(archive_path=archive_path, project_root=restore_project, backup_existing=False)

    restored_csv = restore_project / "data" / "daily" / "NVDA.csv"
    assert restored_csv.exists()
    assert "2026-01-01" in restored_csv.read_text(encoding="utf-8")

    restored_db = restore_project / "data" / "stock_cache.db"
    with sqlite3.connect(restored_db) as conn:
        row = conn.execute("SELECT value FROM test").fetchone()
    assert row[0] == "ok"
