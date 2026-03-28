from __future__ import annotations

import json
import shutil
import socket
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app_paths import APP_DIR, RUNTIME_DIR, app_path, runtime_path
from diagnostics import format_report_lines, run_environment_checks


RECOVERY_BACKUP_DIR = runtime_path("recovery-backups")
SUPPORT_BUNDLE_DIR = runtime_path("audit-logs", "support-bundles")


PLAYBOOKS = [
    {
        "id": "toast-password-changed",
        "title": "Toast password changed or login failed",
        "symptoms": [
            "Download Reports opens Toast but never reaches the reports screen",
            "The saved session no longer works",
            "Toast asks for a fresh login or new password",
        ],
        "steps": [
            "Open Settings > Recovery Center and use Backup + Reset Toast Session.",
            "Run Download Reports again and sign in with the new Toast credentials.",
            "Test one store for one day before running any batch download.",
            "If Toast now shows MFA or a new login screen, stop automation and switch to local Excel upload until the workflow is revalidated.",
        ],
    },
    {
        "id": "internet-down",
        "title": "Internet unavailable or unstable",
        "symptoms": [
            "Toast download fails before the report page loads",
            "Google Drive auth or upload fails",
            "Diagnostics warn that external endpoints are unreachable",
        ],
        "steps": [
            "Check the Recovery Center health report for network reachability warnings.",
            "If Toast download is blocked, retry only after the network is stable.",
            "If reports are already downloaded locally, continue with QB Sync from local files instead of Google Drive.",
            "Do not clear sessions or tokens just because the network is down.",
        ],
    },
    {
        "id": "qb-not-opening",
        "title": "QuickBooks will not open or connect",
        "symptoms": [
            "QB Sync or Remove Transactions fails before QuickBooks is ready",
            "The app cannot find the QB executable",
            "QuickBooks opens but stays on a popup or wrong company file",
        ],
        "steps": [
            "Run Startup Diagnostics and review QuickBooks Executable and QB Company Files.",
            "Confirm the correct .qbw path is still valid in local-config.json.",
            "Close QuickBooks manually, then reopen the app and retry one preview sync.",
            "If QuickBooks itself is unstable, open the company file manually in QB first, then rerun the app.",
        ],
    },
    {
        "id": "strict-mode-blocked",
        "title": "Strict mode blocked QB Sync",
        "symptoms": [
            "Sync stops with validation issues",
            "Unmapped category, tax, or payment messages appear",
            "Receipt is reported as unbalanced",
        ],
        "steps": [
            "Open the Validation Issues panel and export the CSV or JSON report.",
            "Update the store mapping files before rerunning production sync.",
            "Use Preview mode first after every mapping change.",
            "Do not disable strict mode for normal production runs.",
        ],
    },
    {
        "id": "google-token-expired",
        "title": "Google Drive token expired",
        "symptoms": [
            "Google Drive connect or upload fails",
            "token.json is missing or invalid",
        ],
        "steps": [
            "Use Backup + Reset Google Token in Recovery Center.",
            "Reconnect Google Drive from Settings and complete the auth flow again.",
            "Test folder setup or one upload before the next production run.",
        ],
    },
    {
        "id": "move-to-new-machine",
        "title": "Move app to a new computer",
        "symptoms": [
            "The app starts but cannot find QB, mappings, or auth files",
            "Local config and sessions are missing after reinstall",
        ],
        "steps": [
            "Run the Health Report first to see what is missing on the new machine.",
            "Create .env.qb and local-config.json from the examples if they are missing.",
            "Reconnect Google Drive and sign in to Toast again if needed.",
            "Reselect each .qbw company file and run one preview sync before live use.",
        ],
    },
]


def get_recovery_playbooks() -> list[dict[str, Any]]:
    return deepcopy(PLAYBOOKS)


def get_playbook_by_title(title: str) -> dict[str, Any] | None:
    for playbook in PLAYBOOKS:
        if playbook["title"] == title:
            return deepcopy(playbook)
    return None


def format_playbook(playbook: dict[str, Any]) -> str:
    lines = [playbook["title"], "", "Symptoms:"]
    for symptom in playbook.get("symptoms", []):
        lines.append(f"- {symptom}")
    lines.append("")
    lines.append("Recovery steps:")
    for idx, step in enumerate(playbook.get("steps", []), start=1):
        lines.append(f"{idx}. {step}")
    return "\n".join(lines)


def _sanitize_for_json(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _sanitize_for_json(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(item) for item in value]
    return value


def check_endpoint(host: str, *, port: int = 443, timeout: float = 2.5) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"Connected to {host}:{port}"
    except Exception as exc:
        return False, str(exc)


def backup_and_remove(path: Path) -> Path | None:
    if not path.exists():
        return None
    RECOVERY_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = RECOVERY_BACKUP_DIR / f"{path.stem}-{timestamp}{path.suffix}"
    shutil.copy2(path, backup_path)
    path.unlink()
    return backup_path


def ensure_runtime_file_from_example(example_name: str, target_name: str) -> tuple[Path, bool]:
    target_path = runtime_path(target_name)
    if target_path.exists():
        return target_path, False

    example_path = app_path(example_name)
    if not example_path.exists():
        raise FileNotFoundError(f"Example file not found: {example_path}")

    shutil.copy2(example_path, target_path)
    return target_path, True


def collect_runtime_snapshot(local_config: dict | None = None, diagnostics_report=None) -> dict[str, Any]:
    local_config = local_config or {}
    diagnostics_report = diagnostics_report or run_environment_checks(local_config)

    env_path = runtime_path(".env.qb")
    config_path = runtime_path("local-config.json")
    session_path = runtime_path(".toast-session.json")
    token_path = runtime_path("token.json")
    credentials_path = runtime_path("credentials.json")

    toast_ok, toast_msg = check_endpoint("www.toasttab.com")
    google_ok, google_msg = check_endpoint("accounts.google.com")

    qbw_paths = local_config.get("qbw_paths", {})
    qbw_status = {
        store: {
            "path": path,
            "exists": Path(path).exists(),
        }
        for store, path in qbw_paths.items()
    }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "runtime_dir": str(RUNTIME_DIR),
        "bundle_dir": str(APP_DIR),
        "reports_dir": str(runtime_path("toast-reports")),
        "audit_dir": str(runtime_path("audit-logs")),
        "files": {
            ".env.qb": env_path.exists(),
            "local-config.json": config_path.exists(),
            ".toast-session.json": session_path.exists(),
            "token.json": token_path.exists(),
            "credentials.json": credentials_path.exists(),
        },
        "qbw_paths": qbw_status,
        "network": {
            "toasttab": {"ok": toast_ok, "message": toast_msg},
            "google_accounts": {"ok": google_ok, "message": google_msg},
        },
        "diagnostics_summary": diagnostics_report.summary,
        "diagnostics_lines": format_report_lines(diagnostics_report),
    }


def export_support_bundle(local_config: dict | None = None, diagnostics_report=None) -> dict[str, Path]:
    snapshot = collect_runtime_snapshot(local_config, diagnostics_report)
    SUPPORT_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = SUPPORT_BUNDLE_DIR / f"support-bundle-{timestamp}.json"
    txt_path = SUPPORT_BUNDLE_DIR / f"support-bundle-{timestamp}.txt"

    json_path.write_text(json.dumps(_sanitize_for_json(snapshot), indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "Toast POS Manager - Support Bundle",
        f"Generated: {snapshot['generated_at']}",
        "",
        "Runtime paths:",
        f"- Runtime: {snapshot['runtime_dir']}",
        f"- Bundle: {snapshot['bundle_dir']}",
        f"- Reports: {snapshot['reports_dir']}",
        f"- Audit: {snapshot['audit_dir']}",
        "",
        "Connectivity:",
        f"- Toast: {'OK' if snapshot['network']['toasttab']['ok'] else 'WARN'} - {snapshot['network']['toasttab']['message']}",
        f"- Google: {'OK' if snapshot['network']['google_accounts']['ok'] else 'WARN'} - {snapshot['network']['google_accounts']['message']}",
        "",
        "Runtime files:",
    ]
    for name, exists in snapshot["files"].items():
        lines.append(f"- {name}: {'present' if exists else 'missing'}")

    lines.append("")
    lines.append("QB company files:")
    if snapshot["qbw_paths"]:
        for store, item in snapshot["qbw_paths"].items():
            lines.append(f"- {store}: {'OK' if item['exists'] else 'MISSING'} - {item['path']}")
    else:
        lines.append("- No QB company files configured")

    lines.append("")
    lines.extend(snapshot["diagnostics_lines"])
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    return {"json_path": json_path, "txt_path": txt_path}
