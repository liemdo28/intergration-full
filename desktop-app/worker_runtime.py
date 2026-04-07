from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from app_paths import runtime_path

RUNTIME_STATE_FILE = runtime_path("agentai-runtime-state.json")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_background_worker_settings(config: dict | None = None) -> dict:
    config = config or {}
    worker_cfg = dict(config.get("background_worker") or {})

    def _to_int(value, default, minimum):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, parsed)

    return {
        "command_poll_seconds": _to_int(worker_cfg.get("command_poll_seconds"), 30, 10),
        "snapshot_interval_seconds": _to_int(worker_cfg.get("snapshot_interval_seconds"), 120, 30),
        "headless_downloads": bool(worker_cfg.get("headless_downloads", True)),
    }


def load_runtime_state() -> dict:
    if not RUNTIME_STATE_FILE.exists():
        return {}
    try:
        return json.loads(RUNTIME_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update_runtime_state(**fields) -> dict:
    current = load_runtime_state()
    current.update({key: value for key, value in fields.items() if value is not None})
    RUNTIME_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_STATE_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return current


def build_runtime_snapshot(config: dict | None = None) -> dict:
    settings = get_background_worker_settings(config)
    state = load_runtime_state()
    return {
        "mode": state.get("mode") or "gui",
        "headless_window": bool(state.get("headless_window", False)),
        "headless_downloads": bool(state.get("headless_downloads", settings["headless_downloads"])),
        "command_poll_seconds": int(state.get("command_poll_seconds") or settings["command_poll_seconds"]),
        "snapshot_interval_seconds": int(state.get("snapshot_interval_seconds") or settings["snapshot_interval_seconds"]),
        "worker_status": state.get("worker_status") or "idle",
        "active_command_id": state.get("active_command_id") or "",
        "active_command_type": state.get("active_command_type") or "",
        "last_command_started_at": state.get("last_command_started_at"),
        "last_command_finished_at": state.get("last_command_finished_at"),
        "last_command_status": state.get("last_command_status") or "",
        "last_snapshot_published_at": state.get("last_snapshot_published_at"),
        "last_error": state.get("last_error") or "",
        "process_id": state.get("process_id") or os.getpid(),
        "started_at": state.get("started_at"),
        "log_path": str(runtime_path("logs", "app.log")),
    }
