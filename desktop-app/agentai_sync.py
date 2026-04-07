from __future__ import annotations

import json
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from app_paths import runtime_path
from integration_status import build_integration_snapshot
from worker_runtime import build_runtime_snapshot, update_runtime_state, utc_now_iso

LOCAL_CONFIG_FILE = runtime_path("local-config.json")
DEFAULT_PROJECT_ID = "integration-full"
DEFAULT_SOURCE_TYPE = "integration-full"
DEFAULT_APP_VERSION = "v2.2"


def _load_local_config() -> dict:
    if not LOCAL_CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "integration-node"


def _machine_defaults() -> tuple[str, str]:
    machine_name = os.environ.get("COMPUTERNAME") or platform.node() or "Integration Machine"
    return _slugify(machine_name), machine_name


def get_agentai_sync_settings(config: dict | None = None) -> dict:
    config = config or _load_local_config()
    sync_cfg = dict((config.get("agentai_sync") or {}))
    default_machine_id, default_machine_name = _machine_defaults()
    return {
        "enabled": bool(sync_cfg.get("enabled")),
        "api_url": str(sync_cfg.get("api_url") or "").strip().rstrip("/"),
        "token": str(sync_cfg.get("token") or "").strip(),
        "project_id": str(sync_cfg.get("project_id") or DEFAULT_PROJECT_ID).strip() or DEFAULT_PROJECT_ID,
        "source_type": str(sync_cfg.get("source_type") or DEFAULT_SOURCE_TYPE).strip() or DEFAULT_SOURCE_TYPE,
        "app_version": str(sync_cfg.get("app_version") or DEFAULT_APP_VERSION).strip() or DEFAULT_APP_VERSION,
        "machine_id": str(sync_cfg.get("machine_id") or default_machine_id).strip() or default_machine_id,
        "machine_name": str(sync_cfg.get("machine_name") or default_machine_name).strip() or default_machine_name,
    }


def is_agentai_sync_ready(config: dict | None = None) -> tuple[bool, str]:
    settings = get_agentai_sync_settings(config)
    if not settings["enabled"]:
        return False, "AgentAI sync is disabled."
    if not settings["api_url"]:
        return False, "Missing AgentAI API URL."
    if not settings["token"]:
        return False, "Missing AgentAI edge token."
    return True, "AgentAI sync is ready."


def _agentai_request(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    config: dict | None = None,
    timeout_seconds: int = 20,
) -> dict:
    settings = get_agentai_sync_settings(config)
    url = f"{settings['api_url']}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-AgentAI-Token": settings["token"],
        },
        method=method,
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        response_text = response.read().decode("utf-8")
    return json.loads(response_text) if response_text else {}


def publish_integration_snapshot(
    *,
    base_dir: str | Path | None = None,
    config: dict | None = None,
    on_log=None,
    timeout_seconds: int = 20,
) -> dict:
    settings = get_agentai_sync_settings(config)
    ready, reason = is_agentai_sync_ready(config)
    if not ready:
        return {"ok": False, "skipped": True, "message": reason}

    resolved_base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    snapshot = build_integration_snapshot(base_dir=resolved_base, include_today_for_suggestions=False)
    snapshot["runtime"] = build_runtime_snapshot(config)
    payload = {
        "machine_id": settings["machine_id"],
        "machine_name": settings["machine_name"],
        "source_type": settings["source_type"],
        "app_version": settings["app_version"],
        "snapshot": snapshot,
    }
    url = f"{settings['api_url']}/edge/projects/{settings['project_id']}/snapshot"

    try:
        response_payload = _agentai_request(
            f"/edge/projects/{settings['project_id']}/snapshot",
            method="POST",
            payload=payload,
            config=config,
            timeout_seconds=timeout_seconds,
        )
        if callable(on_log):
            on_log(
                "AgentAI snapshot published "
                f"({settings['machine_name']} -> {settings['project_id']} at {datetime.now(timezone.utc).isoformat()})"
            )
        update_runtime_state(last_snapshot_published_at=utc_now_iso(), last_error="")
        return {
            "ok": True,
            "skipped": False,
            "message": "Snapshot published to AgentAI.",
            "url": url,
            "response": response_payload,
        }
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        update_runtime_state(last_error=f"snapshot_http_{exc.code}")
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI sync failed ({exc.code}): {detail or exc.reason}",
            "status_code": exc.code,
        }
    except Exception as exc:
        update_runtime_state(last_error=str(exc))
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI sync failed: {exc}",
        }


def fetch_next_agentai_command(*, config: dict | None = None, timeout_seconds: int = 20) -> dict:
    ready, reason = is_agentai_sync_ready(config)
    if not ready:
        return {"ok": False, "skipped": True, "message": reason, "command": None}
    settings = get_agentai_sync_settings(config)
    try:
        payload = _agentai_request(
            f"/edge/projects/{settings['project_id']}/commands/{settings['machine_id']}",
            config=config,
            timeout_seconds=timeout_seconds,
        )
        return {"ok": True, "skipped": False, "message": "Command poll completed.", "command": payload.get("command")}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI command poll failed ({exc.code}): {detail or exc.reason}",
            "command": None,
        }
    except Exception as exc:
        return {"ok": False, "skipped": False, "message": f"AgentAI command poll failed: {exc}", "command": None}


def acknowledge_agentai_command(
    command_id: str,
    *,
    heartbeat_seconds: int = 120,
    config: dict | None = None,
    timeout_seconds: int = 20,
) -> dict:
    ready, reason = is_agentai_sync_ready(config)
    if not ready:
        return {"ok": False, "skipped": True, "message": reason}
    try:
        response_payload = _agentai_request(
            f"/edge/commands/{command_id}/ack",
            method="POST",
            payload={"heartbeat_seconds": heartbeat_seconds},
            config=config,
            timeout_seconds=timeout_seconds,
        )
        return {"ok": True, "skipped": False, "message": "Command acknowledged.", "response": response_payload}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI command ack failed ({exc.code}): {detail or exc.reason}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI command ack failed: {exc}",
        }


def heartbeat_agentai_command(
    command_id: str,
    *,
    heartbeat_seconds: int = 120,
    config: dict | None = None,
    timeout_seconds: int = 20,
) -> dict:
    ready, reason = is_agentai_sync_ready(config)
    if not ready:
        return {"ok": False, "skipped": True, "message": reason}
    try:
        response_payload = _agentai_request(
            f"/edge/commands/{command_id}/heartbeat",
            method="POST",
            payload={"heartbeat_seconds": heartbeat_seconds},
            config=config,
            timeout_seconds=timeout_seconds,
        )
        return {"ok": True, "skipped": False, "message": "Heartbeat sent.", "response": response_payload}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI heartbeat failed ({exc.code}): {detail or exc.reason}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI heartbeat failed: {exc}",
        }


def report_agentai_command_result(
    command_id: str,
    *,
    status: str,
    result: dict | None = None,
    error_message: str = "",
    config: dict | None = None,
    timeout_seconds: int = 20,
) -> dict:
    ready, reason = is_agentai_sync_ready(config)
    if not ready:
        return {"ok": False, "skipped": True, "message": reason}
    try:
        response_payload = _agentai_request(
            f"/edge/commands/{command_id}/result",
            method="POST",
            payload={
                "status": status,
                "result": result or {},
                "error_message": error_message,
            },
            config=config,
            timeout_seconds=timeout_seconds,
        )
        return {"ok": True, "skipped": False, "message": "Command result sent.", "response": response_payload}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI command result failed ({exc.code}): {detail or exc.reason}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "skipped": False,
            "message": f"AgentAI command result failed: {exc}",
        }
