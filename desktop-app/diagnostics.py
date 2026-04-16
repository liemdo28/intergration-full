import importlib
import json
import os
import socket
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app_paths import APP_DIR, RUNTIME_DIR, runtime_path
from delete_policy import load_delete_policy


class Severity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


@dataclass
class DiagnosticCheck:
    name: str
    severity: str  # "blocker" | "warning" | "info"
    status: str    # "ok" | "error" | "warning" | "skipped"
    message: str


@dataclass
class DiagnosticReport:
    checks: list[DiagnosticCheck]

    @property
    def items(self) -> list[DiagnosticCheck]:
        return self.checks

    @property
    def blocked_features(self) -> list[str]:
        return [check.name for check in self.checks if check.severity == "blocker"]

    @property
    def error_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "warning")

    @property
    def ok_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "ok")

    @property
    def summary(self) -> str:
        if self.error_count:
            return f"{self.error_count} error(s), {self.warning_count} warning(s)"
        if self.warning_count:
            return f"{self.warning_count} warning(s)"
        return "All checks passed"


def _add(checks: list[DiagnosticCheck], severity: str, name: str, status: str, message: str) -> None:
    checks.append(DiagnosticCheck(name=name, severity=severity, status=status, message=message))


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _import_check(module_name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(module_name)
        return True, "installed"
    except Exception as exc:
        return False, str(exc)


def _check_json_file(path: Path, label: str, checks: list[DiagnosticCheck], severity: str, *, required_keys: list[str] | None = None) -> None:
    if not path.exists():
        _add(checks, severity, label, "warning", "Not found")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _add(checks, severity, label, "error", f"Invalid JSON: {exc}")
        return

    if required_keys:
        missing = [key for key in required_keys if key not in data]
        if missing:
            _add(checks, severity, label, "warning", f"JSON loaded but missing keys: {', '.join(missing)}")
            return
    _add(checks, severity, label, "ok", str(path))


def _check_endpoint(host: str, *, port: int = 443, timeout: float = 2.5) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"Connected to {host}:{port}"
    except Exception as exc:
        return False, str(exc)


def run_environment_checks(local_config: dict | None = None) -> DiagnosticReport:
    checks: list[DiagnosticCheck] = []
    local_config = local_config or {}

    # BLOCKER: Platform check
    if sys.platform == "win32":
        _add(checks, Severity.INFO, "Platform", "ok", "Windows environment detected")
    else:
        _add(checks, Severity.BLOCKER, "Platform", "error", f"Unsupported platform: {sys.platform}")

    version_ok = sys.version_info >= (3, 12)
    is_64bit = sys.maxsize > 2**32
    if version_ok and is_64bit:
        _add(checks, Severity.INFO, "Python", "ok", f"{sys.version.split()[0]} (64-bit)")
    elif version_ok and not is_64bit:
        _add(checks, Severity.BLOCKER, "Python", "error", f"{sys.version.split()[0]} (32-bit); 64-bit Python required")
    else:
        _add(checks, Severity.BLOCKER, "Python", "error", f"{sys.version.split()[0]} ({'64-bit' if is_64bit else '32-bit'}); Python 3.12+ required")

    if not is_64bit:
        _add(checks, Severity.WARNING, "Python Architecture", "warning", "32-bit Python can fail with QuickBooks COM integration")

    required_modules = {
        "customtkinter": "UI",
        "tkcalendar": "Calendar widget",
        "openpyxl": "Excel parser",
        "psutil": "Process control",
        "pywinauto": "QuickBooks UI automation",
        "win32com.client": "QuickBooks COM bridge",
        "playwright.sync_api": "Toast browser automation",
        "googleapiclient.discovery": "Google Drive integration",
    }
    critical_modules = {"customtkinter", "win32com.client"}
    for module_name, label in required_modules.items():
        ok, detail = _import_check(module_name)
        severity = Severity.BLOCKER if label in critical_modules else Severity.WARNING
        _add(checks, severity, label, "ok" if ok else "error", detail if ok else f"Import failed: {detail}")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser_path = Path(playwright.chromium.executable_path)
            if browser_path.exists():
                _add(checks, Severity.INFO, "Playwright Chromium", "ok", str(browser_path))
            else:
                _add(checks, Severity.BLOCKER, "Playwright Chromium", "error", "Chromium browser not installed")
    except Exception as exc:
        _add(checks, Severity.BLOCKER, "Playwright Chromium", "error", f"Browser check failed: {exc}")

    toast_ok, toast_msg = _check_endpoint("www.toasttab.com")
    _add(checks, Severity.INFO, "Toast Reachability", "ok" if toast_ok else "warning", toast_msg if toast_ok else f"Toast may be unavailable: {toast_msg}")

    google_ok, google_msg = _check_endpoint("accounts.google.com")
    _add(checks, Severity.INFO, "Google Reachability", "ok" if google_ok else "warning", google_msg if google_ok else f"Google auth/upload may be unavailable: {google_msg}")

    env_path = runtime_path(".env.qb")
    env_values = _load_env_file(env_path)
    if env_path.exists():
        _add(checks, Severity.INFO, "QB Config", "ok", str(env_path))
    else:
        _add(checks, Severity.WARNING, "QB Config", "warning", f"Missing {env_path.name}; QB features may not work")

    qb_password_slots = [env_values.get(f"QB_PASSWORD{i}", "").strip() for i in range(1, 4)]
    if env_path.exists():
        if any(qb_password_slots):
            _add(checks, Severity.INFO, "QB Password Slots", "ok", "At least one QB_PASSWORD slot is configured")
        else:
            _add(checks, Severity.WARNING, "QB Password Slots", "warning", "No QB_PASSWORD1..3 values configured")

    qb_exe = None
    try:
        from qb_automate import resolve_qb_executable

        qb_exe = resolve_qb_executable()
    except Exception:
        qb_exe = None
    qb_exe = qb_exe or Path(env_values.get("QB_EXE_PATH") or os.environ.get("QB_EXE_PATH", r"C:\Program Files\Intuit\QuickBooks Enterprise Solutions 24.0\QBWEnterprise.exe"))
    qb_exe_exists = Path(qb_exe).exists()
    if qb_exe_exists:
        _add(checks, Severity.INFO, "QuickBooks Executable", "ok", str(qb_exe))
    else:
        _add(checks, Severity.BLOCKER, "QuickBooks Executable", "error", f"QB executable not found: {qb_exe}")

    credentials_path = runtime_path("credentials.json")
    _check_json_file(
        credentials_path,
        "Google Credentials",
        checks,
        Severity.WARNING,
        required_keys=["installed"],
    )

    token_path = runtime_path("token.json")
    _check_json_file(token_path, "token.json", checks, Severity.WARNING)

    session_path = runtime_path(".toast-session.json")
    _check_json_file(session_path, ".toast-session.json", checks, Severity.WARNING, required_keys=["cookies", "origins"])

    reports_dir = runtime_path("toast-reports")
    if reports_dir.exists():
        _add(checks, Severity.INFO, "Reports Folder", "ok", str(reports_dir))
    else:
        _add(checks, Severity.INFO, "Reports Folder", "warning", f"{reports_dir} will be created on first run")

    mapping_path = APP_DIR / "qb-mapping.json"
    map_dir = APP_DIR / "Map"
    if mapping_path.exists():
        _add(checks, Severity.INFO, "QB Mapping", "ok", str(mapping_path))
    else:
        _add(checks, Severity.BLOCKER, "QB Mapping", "error", f"qb-mapping.json not found: {mapping_path}")
    if map_dir.exists():
        _add(checks, Severity.INFO, "CSV Map Folder", "ok", str(map_dir))
    else:
        _add(checks, Severity.BLOCKER, "CSV Map Folder", "error", f"Map folder not found: {map_dir}")

    mapping_data = {}
    if mapping_path.exists():
        try:
            mapping_data = json.loads(mapping_path.read_text(encoding="utf-8"))
        except Exception:
            mapping_data = {}

    qbw_paths = (local_config or {}).get("qbw_paths", {})
    if qbw_paths:
        missing = [f"{store}: {path}" for store, path in qbw_paths.items() if not Path(path).exists()]
        if missing:
            _add(checks, Severity.WARNING, "QB Company Files", "warning", "Missing paths -> " + "; ".join(missing))
        else:
            _add(checks, Severity.INFO, "QB Company Files", "ok", f"{len(qbw_paths)} configured path(s) look valid")
        stores = mapping_data.get("stores", {}) if isinstance(mapping_data, dict) else {}
        mismatches = []
        try:
            from qb_automate import company_file_matches
        except Exception:
            company_file_matches = None
        if company_file_matches:
            for store, path in qbw_paths.items():
                expected = (stores.get(store) or {}).get("qbw_match")
                if expected and Path(path).exists() and not company_file_matches(path, expected):
                    mismatches.append(f"{store}: expected '{expected}' in {Path(path).name}")
        if mismatches:
            _add(checks, Severity.WARNING, "QB Company Guards", "warning", "; ".join(mismatches))
        elif qbw_paths:
            _add(checks, Severity.INFO, "QB Company Guards", "ok", "Configured QB file names match expected store guards")
    else:
        _add(checks, Severity.WARNING, "QB Company Files", "warning", "No .qbw paths saved yet")

    marketplace_paths = (local_config or {}).get("marketplace_paths", {})
    if marketplace_paths:
        missing_marketplace = []
        ready_count = 0
        for store_name, sources in marketplace_paths.items():
            for source_name, path in (sources or {}).items():
                if Path(path).exists():
                    ready_count += 1
                else:
                    missing_marketplace.append(f"{store_name}/{source_name}: {path}")
        if missing_marketplace:
            _add(checks, Severity.WARNING, "Marketplace Uploads", "warning", "Missing uploaded file(s) -> " + "; ".join(missing_marketplace))
        elif ready_count:
            _add(checks, Severity.INFO, "Marketplace Uploads", "ok", f"{ready_count} uploaded marketplace file(s) look valid")
    else:
        _add(checks, Severity.WARNING, "Marketplace Uploads", "warning", "No uploaded marketplace CSV paths saved yet")

    delete_policy = load_delete_policy(local_config, env_values)
    if delete_policy.allow_live_delete:
        _add(checks, Severity.WARNING, "Delete Policy", "warning", f"Live delete enabled via {delete_policy.source}")
    else:
        _add(checks, Severity.INFO, "Delete Policy", "ok", "Live delete locked; dry-run mode enforced by default")

    try:
        from sync_ledger import SyncLedger

        ledger = SyncLedger()
        snapshot = ledger.diagnostics_snapshot()
        _add(checks, Severity.INFO, "Sync Ledger", "ok", snapshot["db_path"])
        if snapshot["stale_running_count"]:
            _add(checks, Severity.WARNING, "Sync Ledger Stale Runs", "warning", f"{snapshot['stale_running_count']} stale running sync(s)")
        else:
            _add(checks, Severity.INFO, "Sync Ledger Stale Runs", "ok", "No stale running syncs detected")
        if snapshot["failed_count"]:
            _add(checks, Severity.WARNING, "Recent Failed Syncs", "warning", f"{snapshot['failed_count']} failed sync record(s) in ledger")
        else:
            _add(checks, Severity.INFO, "Recent Failed Syncs", "ok", "No failed syncs recorded")
    except Exception as exc:
        _add(checks, Severity.BLOCKER, "Sync Ledger", "error", f"Ledger check failed: {exc}")

    # BLOCKER: Runtime dir must be writable for the app to function
    try:
        test_file = RUNTIME_DIR / ".diagnostics_write_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        _add(checks, Severity.INFO, "App Runtime Folder", "ok", str(RUNTIME_DIR))
    except Exception as exc:
        _add(checks, Severity.BLOCKER, "App Runtime Folder", "error", f"Runtime directory not writable: {exc}")

    _add(checks, Severity.INFO, "Bundled Assets Folder", "ok", str(APP_DIR))

    return DiagnosticReport(checks=checks)


def format_report_lines(report: DiagnosticReport) -> list[str]:
    lines = [f"Environment diagnostics: {report.summary}"]

    # BLOCKERs first
    for check in report.checks:
        if check.severity == Severity.BLOCKER:
            marker = {"ok": "OK", "warning": "WARN", "error": "ERR"}.get(check.status, check.status.upper())
            lines.append(f"[BLOCKER][{marker}] {check.name}: {check.message}")

    # Then everything else (WARNING + INFO), grouped by status
    status_order = ["warning", "ok", "skipped", "error"]
    for status in status_order:
        for check in report.checks:
            if check.severity != Severity.BLOCKER and check.status == status:
                marker = {"ok": "OK", "warning": "WARN", "error": "ERR"}.get(check.status, check.status.upper())
                sev_marker = check.severity.upper()
                lines.append(f"[{sev_marker}][{marker}] {check.name}: {check.message}")

    return lines


def format_feature_readiness(report: DiagnosticReport) -> list[str]:
    """Returns a readiness summary per feature tab."""
    lines = []
    blockers = {i.name: i.message for i in report.items if i.severity == Severity.BLOCKER}
    warnings = {i.name: i.message for i in report.items if i.severity == Severity.WARNING}

    features = {
        "Download Reports": ["Playwright Chromium", "customtkinter", "Python"],
        "QB Sync": ["QuickBooks Executable", "QB Config", "QB Mapping", "CSV Map Folder"],
        "Remove Transactions": ["QuickBooks Executable"],
        "Drive Upload": ["Google Credentials", "token.json"],
        "Marketplace Uploads": ["Marketplace Uploads"],
    }
    for feat, required_checks in features.items():
        missing = [c for c in required_checks if c in blockers]
        if missing:
            reason = blockers[missing[0]] if missing else "Not ready"
            lines.append(f"{feat}: {reason}")
        elif any(c in warnings for c in required_checks):
            lines.append(f"{feat}: Partially configured")
        else:
            lines.append(f"{feat}: Ready")
    return lines
