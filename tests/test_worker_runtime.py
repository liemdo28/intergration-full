from __future__ import annotations

from worker_runtime import build_runtime_snapshot, get_background_worker_settings, update_runtime_state


def test_background_worker_settings_apply_defaults():
    settings = get_background_worker_settings({})

    assert settings["command_poll_seconds"] == 30
    assert settings["snapshot_interval_seconds"] == 120
    assert settings["headless_downloads"] is False


def test_build_runtime_snapshot_reflects_saved_state(monkeypatch, tmp_path):
    runtime_file = tmp_path / "agentai-runtime-state.json"
    monkeypatch.setattr("worker_runtime.RUNTIME_STATE_FILE", runtime_file)

    update_runtime_state(
        mode="headless_worker",
        worker_status="running",
        active_command_id="cmd-123",
        active_command_type="download_missing_reports",
        headless_window=True,
        headless_downloads=True,
    )

    snapshot = build_runtime_snapshot({"background_worker": {"command_poll_seconds": 45}})

    assert snapshot["mode"] == "headless_worker"
    assert snapshot["worker_status"] == "running"
    assert snapshot["active_command_id"] == "cmd-123"
    assert snapshot["headless_window"] is True
    assert snapshot["headless_downloads"] is True
