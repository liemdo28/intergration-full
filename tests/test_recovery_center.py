from pathlib import Path

import recovery_center


def test_ensure_runtime_file_from_example_copies_only_once(tmp_path, monkeypatch):
    example_path = tmp_path / ".env.qb.example"
    target_path = tmp_path / ".env.qb"
    example_path.write_text("QB_PASSWORD1=\n", encoding="utf-8")

    monkeypatch.setattr(recovery_center, "app_path", lambda *parts: example_path)
    monkeypatch.setattr(recovery_center, "runtime_path", lambda *parts: target_path)

    created_path, created = recovery_center.ensure_runtime_file_from_example(".env.qb.example", ".env.qb")
    assert created_path == target_path
    assert created is True
    assert target_path.read_text(encoding="utf-8") == "QB_PASSWORD1=\n"

    created_path, created = recovery_center.ensure_runtime_file_from_example(".env.qb.example", ".env.qb")
    assert created_path == target_path
    assert created is False


def test_backup_and_remove_creates_backup(tmp_path, monkeypatch):
    backup_dir = tmp_path / "recovery-backups"
    target = tmp_path / ".toast-session.json"
    target.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(recovery_center, "RECOVERY_BACKUP_DIR", backup_dir)
    backup_path = recovery_center.backup_and_remove(target)

    assert backup_path is not None
    assert backup_path.exists()
    assert not target.exists()


def test_collect_runtime_snapshot_includes_connectivity_and_qbw_status(monkeypatch):
    class FakeReport:
        summary = "1 warning(s)"

    monkeypatch.setattr(recovery_center, "check_endpoint", lambda host, **kwargs: (host == "www.toasttab.com", f"{host} checked"))
    monkeypatch.setattr(recovery_center, "runtime_path", lambda *parts: Path("C:/runtime").joinpath(*parts))
    monkeypatch.setattr(recovery_center, "APP_DIR", Path("C:/bundle"))
    monkeypatch.setattr(recovery_center, "RUNTIME_DIR", Path("C:/runtime"))
    monkeypatch.setattr(recovery_center, "format_report_lines", lambda report: ["[WARN] test"])

    snapshot = recovery_center.collect_runtime_snapshot(
        {
            "qbw_paths": {
                "Store A": str(Path(__file__).resolve()),
                "Store B": "Z:/missing/file.qbw",
            }
        },
        diagnostics_report=FakeReport(),
    )

    assert snapshot["network"]["toasttab"]["ok"] is True
    assert snapshot["network"]["google_accounts"]["ok"] is False
    assert snapshot["qbw_paths"]["Store A"]["exists"] is True
    assert snapshot["qbw_paths"]["Store B"]["exists"] is False
