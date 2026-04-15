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


def test_close_qb_completely_does_not_force_kill_by_default(monkeypatch):
    messages = []

    class FakeProc:
        def __init__(self, name):
            self.info = {"name": name}
            self._name = name
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def name(self):
            return self._name

        def is_running(self):
            return True

    proc = FakeProc("QBWEnterprise.exe")

    class FakePsutil:
        NoSuchProcess = RuntimeError
        AccessDenied = PermissionError

        @staticmethod
        def process_iter(_attrs):
            return [proc]

        @staticmethod
        def wait_procs(_touched, timeout):
            return ([], [proc])

    monkeypatch.setitem(__import__("sys").modules, "psutil", FakePsutil)

    result = qb_automate.close_qb_completely(callback=messages.append)

    assert result is False
    assert proc.terminated is True
    assert proc.killed is False
    assert any("Force-kill is disabled by default" in message for message in messages)


def test_is_safe_popup_title_only_allows_known_dialogs():
    assert qb_automate._is_safe_popup_title("Memorized Transactions") is True
    assert qb_automate._is_safe_popup_title("QuickBooks Update Service") is True
    assert qb_automate._is_safe_popup_title("Random Important Warning") is False
