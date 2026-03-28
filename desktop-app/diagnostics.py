import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app_paths import APP_DIR, RUNTIME_DIR, runtime_path
from delete_policy import load_delete_policy


@dataclass
class DiagnosticCheck:
    name: str
    status: str
    message: str


@dataclass
class DiagnosticReport:
    checks: list[DiagnosticCheck]

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


def _add(checks: list[DiagnosticCheck], name: str, status: str, message: str) -> None:
    checks.append(DiagnosticCheck(name=name, status=status, message=message))


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


def _check_json_file(path: Path, label: str, checks: list[DiagnosticCheck], *, required_keys: list[str] | None = None) -> None:
    if not path.exists():
        _add(checks, label, "warning", "Not found")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _add(checks, label, "error", f"Invalid JSON: {exc}")
        return

    if required_keys:
        missing = [key for key in required_keys if key not in data]
        if missing:
            _add(checks, label, "warning", f"JSON loaded but missing keys: {', '.join(missing)}")
            return
    _add(checks, label, "ok", str(path))


def run_environment_checks(local_config: dict | None = None) -> DiagnosticReport:
    checks: list[DiagnosticCheck] = []
    local_config = local_config or {}

    if sys.platform == "win32":
        _add(checks, "Platform", "ok", "Windows environment detected")
    else:
        _add(checks, "Platform", "error", f"Unsupported platform: {sys.platform}")

    version_ok = sys.version_info >= (3, 12)
    _add(
        checks,
        "Python",
        "ok" if version_ok else "error",
        f"{sys.version.split()[0]} ({'64-bit' if sys.maxsize > 2**32 else '32-bit'})",
    )
    if sys.maxsize <= 2**32:
        _add(checks, "Python Architecture", "warning", "32-bit Python can fail with QuickBooks COM integration")

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
    for module_name, label in required_modules.items():
        ok, detail = _import_check(module_name)
        _add(checks, label, "ok" if ok else "error", detail if ok else f"Import failed: {detail}")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser_path = Path(playwright.chromium.executable_path)
            if browser_path.exists():
                _add(checks, "Playwright Chromium", "ok", str(browser_path))
            else:
                _add(checks, "Playwright Chromium", "error", "Chromium browser not installed")
    except Exception as exc:
        _add(checks, "Playwright Chromium", "error", f"Browser check failed: {exc}")

    env_path = runtime_path(".env.qb")
    env_values = _load_env_file(env_path)
    if env_path.exists():
        _add(checks, "QB Config", "ok", str(env_path))
    else:
        _add(checks, "QB Config", "warning", f"Missing {env_path.name}; QB features may not work")

    qb_password_slots = [env_values.get(f"QB_PASSWORD{i}", "").strip() for i in range(1, 4)]
    if env_path.exists():
        if any(qb_password_slots):
            _add(checks, "QB Password Slots", "ok", "At least one QB_PASSWORD slot is configured")
        else:
            _add(checks, "QB Password Slots", "warning", "No QB_PASSWORD1..3 values configured")

    qb_exe = None
    try:
        from qb_automate import resolve_qb_executable

        qb_exe = resolve_qb_executable()
    except Exception:
        qb_exe = None
    qb_exe = qb_exe or Path(env_values.get("QB_EXE_PATH") or os.environ.get("QB_EXE_PATH", r"C:\Program Files\Intuit\QuickBooks Enterprise Solutions 24.0\QBWEnterprise.exe"))
    _add(checks, "QuickBooks Executable", "ok" if Path(qb_exe).exists() else "warning", str(qb_exe))

    credentials_path = runtime_path("credentials.json")
    _check_json_file(
        credentials_path,
        "Google Credentials",
        checks,
        required_keys=["installed"],
    )

    token_path = runtime_path("token.json")
    _check_json_file(token_path, "token.json", checks)

    session_path = runtime_path(".toast-session.json")
    _check_json_file(session_path, ".toast-session.json", checks, required_keys=["cookies", "origins"])

    reports_dir = runtime_path("toast-reports")
    if reports_dir.exists():
        _add(checks, "Reports Folder", "ok", str(reports_dir))
    else:
        _add(checks, "Reports Folder", "warning", f"{reports_dir} will be created on first run")

    mapping_path = APP_DIR / "qb-mapping.json"
    map_dir = APP_DIR / "Map"
    _add(checks, "QB Mapping", "ok" if mapping_path.exists() else "error", str(mapping_path))
    _add(checks, "CSV Map Folder", "ok" if map_dir.exists() else "error", str(map_dir))

    qbw_paths = (local_config or {}).get("qbw_paths", {})
    if qbw_paths:
        missing = [f"{store}: {path}" for store, path in qbw_paths.items() if not Path(path).exists()]
        if missing:
            _add(checks, "QB Company Files", "warning", "Missing paths -> " + "; ".join(missing))
        else:
            _add(checks, "QB Company Files", "ok", f"{len(qbw_paths)} configured path(s) look valid")
    else:
        _add(checks, "QB Company Files", "warning", "No .qbw paths saved yet")

    delete_policy = load_delete_policy(local_config, env_values)
    if delete_policy.allow_live_delete:
        _add(checks, "Delete Policy", "warning", f"Live delete enabled via {delete_policy.source}")
    else:
        _add(checks, "Delete Policy", "ok", "Live delete locked; dry-run mode enforced by default")

    _add(checks, "App Runtime Folder", "ok", str(RUNTIME_DIR))
    _add(checks, "Bundled Assets Folder", "ok", str(APP_DIR))

    return DiagnosticReport(checks=checks)


def format_report_lines(report: DiagnosticReport) -> list[str]:
    lines = [f"Environment diagnostics: {report.summary}"]
    for check in report.checks:
        marker = {"ok": "OK", "warning": "WARN", "error": "ERR"}.get(check.status, check.status.upper())
        lines.append(f"[{marker}] {check.name}: {check.message}")
    return lines
