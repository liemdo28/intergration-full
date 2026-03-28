from pathlib import Path

import qb_automate


def test_resolve_qb_executable_prefers_env_path(tmp_path, monkeypatch):
    exe_path = tmp_path / "QBWEnterprise.exe"
    exe_path.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("QB_EXE_PATH", str(exe_path))

    assert qb_automate.resolve_qb_executable() == exe_path
