from pathlib import Path

import qb_automate


def test_resolve_qb_executable_prefers_env_path(tmp_path, monkeypatch):
    exe_path = tmp_path / "QBWEnterprise.exe"
    exe_path.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("QB_EXE_PATH", str(exe_path))

    assert qb_automate.resolve_qb_executable() == exe_path


def test_company_file_matches_uses_normalized_filename():
    assert qb_automate.company_file_matches(r"D:\QB\JHT Ventures.qbw", "jht ventures") is True
    assert qb_automate.company_file_matches(r"D:\QB\RawStockton.qbw", "jht ventures") is False


def test_validate_company_file_path_rejects_mismatched_company_guard(tmp_path):
    qbw_path = tmp_path / "OtherCompany.qbw"
    qbw_path.write_text("stub", encoding="utf-8")

    ok, message = qb_automate.validate_company_file_path(qbw_path, "jht ventures", "The Rim")

    assert ok is False
    assert "QB company guard failed" in message
