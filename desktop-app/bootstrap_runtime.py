"""
ToastPOSManager — First-Run Bootstrap Layer

Runs before the UI opens. Validates and prepares the runtime environment.
Returns a BootstrapReport dataclass so the caller (launcher.py) can decide
what to do next.

Severity tiers used in report items:
  BLOCKER  — app cannot proceed (e.g. no write permission, bad config JSON)
  WARNING  — feature unavailable but app can still run
  INFO     — environment info, not a problem
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json, logging, shutil, sys, os

# ---------------------------------------------------------------------------
# Paths — use the same logic as app_paths.py so this works in both dev and frozen
# ---------------------------------------------------------------------------
def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent

BUNDLE_DIR = _resolve_bundle_dir()
APP_DIR = BUNDLE_DIR
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class BootstrapItem:
    name: str
    severity: str  # BLOCKER | WARNING | INFO
    status: str    # ok | created | missing | error | skipped
    message: str
    path: Path | None = None

@dataclass
class BootstrapReport:
    can_run: bool        # False if any BLOCKERs
    is_first_run: bool   # True if .env.qb or local-config.json were created now
    portable_mode: bool  # True if running from extracted zip (no install path)
    items: list[BootstrapItem] = field(default_factory=list)
    created_folders: list[Path] = field(default_factory=list)
    created_files: list[Path] = field(default_factory=list)
    bootstrap_time: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def blockers(self) -> list[BootstrapItem]:
        return [i for i in self.items if i.severity == "BLOCKER"]

    @property
    def warnings(self) -> list[BootstrapItem]:
        return [i for i in self.items if i.severity == "WARNING"]

    def summary(self) -> str:
        b = len(self.blockers)
        w = len(self.warnings)
        if b:
            return f"{b} blocker(s), {w} warning(s)"
        if w:
            return f"{w} warning(s)"
        return "All checks passed"

# ---------------------------------------------------------------------------
# Core bootstrap logic
# ---------------------------------------------------------------------------

def run_bootstrap() -> BootstrapReport:
    """
    Main entry point. Performs all checks and repairs.
    Returns a BootstrapReport with severity-tiered items.
    """
    report = BootstrapReport(
        can_run=True,
        is_first_run=False,
        portable_mode=_detect_portable_mode(),
        items=[],
    )

    _check_writable_runtime(report)
    _ensure_folders(report)
    _ensure_config_files(report)
    _check_playwright_browser(report)
    _check_python_presence(report)  # In bundled mode, this should pass; in dev, note it

    # Mark blockers
    blockers = [i for i in report.items if i.severity == "BLOCKER"]
    if blockers:
        report.can_run = False

    _write_bootstrap_log(report)
    return report


def _detect_portable_mode() -> bool:
    """
    True if app is running from a directory that looks like an extracted portable bundle.
    Portable indicators:
    - bundled (frozen) AND
    - no registry install path AND
    - RUNTIME_DIR == APP_DIR (no separate Program Files location)
    """
    if getattr(sys, "frozen", False):
        # Check if this looks like an install vs portable by looking for an install marker
        install_marker = RUNTIME_DIR / "TOAST_POS_INSTALL.txt"
        portable_marker = RUNTIME_DIR / "PORTABLE_MODE.txt"
        # If neither marker exists, use heuristic: if .env.qb is in RUNTIME_DIR and
        # not in AppData, assume portable
        env_in_runtime = (RUNTIME_DIR / ".env.qb").exists()
        return True  # Conservative: assume portable when frozen and env is alongside exe
    return False


def _add(report: BootstrapReport, name: str, severity: str, status: str, message: str, path: Path | None = None) -> None:
    report.items.append(BootstrapItem(name=name, severity=severity, status=status, message=message, path=path))


def _check_writable_runtime(report: BootstrapReport) -> None:
    """BLOCKER if runtime folder is not writable."""
    try:
        test_file = RUNTIME_DIR / f".bootstrap_write_test_{os.getpid()}.tmp"
        test_file.write_text("test")
        test_file.unlink()
        _add(report, "Runtime Folder Write", "INFO", "ok", f"Writable: {RUNTIME_DIR}", RUNTIME_DIR)
    except Exception as exc:
        _add(report, "Runtime Folder Write", "BLOCKER", "error",
             f"Runtime folder not writable: {exc}", RUNTIME_DIR)
        report.can_run = False


def _ensure_folders(report: BootstrapReport) -> None:
    """Create all required runtime subfolders. WARNING if creation fails."""
    folders = ["logs", "audit-logs", "toast-reports", "recovery-backups", "marketplace-reports"]
    for folder_name in folders:
        folder = RUNTIME_DIR / folder_name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if folder.exists():
                _add(report, f"Folder: {folder_name}", "INFO", "ok", str(folder), folder)
                report.created_folders.append(folder)
        except Exception as exc:
            _add(report, f"Folder: {folder_name}", "BLOCKER", "error",
                 f"Cannot create {folder_name}: {exc}", folder)


# ---------------------------------------------------------------------------
# Config schema — every key that the app reads must exist with a safe default.
# Missing keys are auto-healed at startup so partial configs never cause crashes.
# ---------------------------------------------------------------------------
_CONFIG_DEFAULTS: dict = {
    "qbw_paths": {},
    "marketplace_paths": {},
    "last_qbw_dir": "",
    "last_marketplace_dir": "",
    "delete_policy": {"allow_live_delete": False, "approver": ""},
    "google_drive": {
        "root_folder_url": "",
        "root_folder_id": "",
        "brand_folder_name": "",
        "use_date_subfolders": False,
    },
    "agentai_sync": {
        "enabled": False,
        "api_url": "",
        "token": "",
        "project_id": "",
        "source_type": "",
        "app_version": "",
        "machine_id": "",
        "machine_name": "",
    },
    "background_worker": {
        "command_poll_seconds": 30,
        "snapshot_interval_seconds": 120,
        "headless_downloads": True,
    },
    "operator_mode": "standard",
}


def _heal_config(data: dict, path: Path, report: BootstrapReport) -> dict:
    """
    Ensure all required config keys are present.  Any missing key is added
    with its safe default value and the file is re-saved.  This prevents
    KeyError / AttributeError crashes caused by partially initialised configs.
    """
    missing_keys = [k for k in _CONFIG_DEFAULTS if k not in data]
    if not missing_keys:
        return data
    for key in missing_keys:
        data[key] = _CONFIG_DEFAULTS[key]
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _add(report, "local-config.json Heal", "INFO", "ok",
             f"Added {len(missing_keys)} missing key(s) with safe defaults: {', '.join(missing_keys)}", path)
    except Exception as exc:
        _add(report, "local-config.json Heal", "WARNING", "error",
             f"Could not save healed config ({exc}) — app may behave unexpectedly", path)
    return data


def _ensure_config_files(report: BootstrapReport) -> None:
    """Ensure .env.qb and local-config.json exist, creating from example if missing or malformed."""
    # --- .env.qb ---
    env_target = RUNTIME_DIR / ".env.qb"
    env_example = APP_DIR / ".env.qb.example"
    if env_target.exists():
        try:
            content = env_target.read_text(encoding="utf-8")
            # Basic sanity: must have at least QB_PASSWORD1 line
            has_password = any("QB_PASSWORD" in line for line in content.splitlines() if "=" in line)
            if has_password:
                _add(report, ".env.qb Config", "INFO", "ok", "Found and looks configured", env_target)
            else:
                _add(report, ".env.qb Config", "WARNING", "missing",
                     "File exists but no QB_PASSWORD configured; QB features unavailable until configured", env_target)
        except Exception as exc:
            _backup_and_log(report, env_target, f"read error: {exc}")
            _create_from_example(report, env_target, env_example, ".env.qb")
    else:
        _create_from_example(report, env_target, env_example, ".env.qb")
        report.is_first_run = True

    # --- local-config.json ---
    cfg_target = RUNTIME_DIR / "local-config.json"
    cfg_example = APP_DIR / "local-config.example.json"
    if cfg_target.exists():
        try:
            data = json.loads(cfg_target.read_text(encoding="utf-8"))
            _add(report, "local-config.json", "INFO", "ok",
                 f"Found with {len(data)} top-level key(s)", cfg_target)
            # Auto-heal: ensure every required key exists (no KeyError on first use)
            _heal_config(data, cfg_target, report)
        except json.JSONDecodeError as exc:
            _backup_and_log(report, cfg_target, f"malformed JSON: {exc}")
            _create_from_example(report, cfg_target, cfg_example, "local-config.json")
            _add(report, "local-config.json", "WARNING", "created",
                 "Malformed config was backed up and regenerated from example", cfg_target)
    else:
        _create_from_example(report, cfg_target, cfg_example, "local-config.json")
        report.is_first_run = True
        # Heal the freshly-created file too — example may lag the schema
        try:
            data = json.loads(cfg_target.read_text(encoding="utf-8"))
            _heal_config(data, cfg_target, report)
        except Exception:
            pass


def _create_from_example(report: BootstrapReport, target: Path, example: Path, label: str) -> None:
    if example.exists():
        try:
            shutil.copy2(example, target)
            _add(report, label, "INFO", "created", f"Created from example: {target}", target)
            report.created_files.append(target)
        except Exception as exc:
            _add(report, label, "BLOCKER", "error", f"Cannot create from example: {exc}", target)
            report.can_run = False
    else:
        _add(report, label, "BLOCKER", "error",
             f"Example file not found: {example}", example)
        report.can_run = False


def _backup_and_log(report: BootstrapReport, path: Path, reason: str) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.backup_{timestamp}")
    try:
        shutil.copy2(path, backup)
        logging.warning(f"Backed up {path} to {backup} ({reason})")
        _add(report, f"Backup: {path.name}", "INFO", "created",
             f"Backed up malformed file: {backup}", backup)
    except Exception:
        _add(report, f"Backup: {path.name}", "WARNING", "error",
             f"Could not back up {path.name}", path)


def _check_playwright_browser(report: BootstrapReport) -> None:
    """Check the Report Browser (Playwright Chromium) is bundled and reachable."""
    # In frozen builds, probe both distribution locations before Playwright tries to
    # resolve its own executable_path — same dual-probe as _ensure_playwright_env().
    if getattr(sys, "frozen", False) and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        _candidates = [
            (RUNTIME_DIR / "playwright-browsers", str(RUNTIME_DIR / "playwright-browsers")),
            (BUNDLE_DIR / "playwright",            str(BUNDLE_DIR)),
        ]
        for _probe, _val in _candidates:
            if _probe.exists():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _val
                break
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            bp = Path(pw.chromium.executable_path)
            if bp.exists():
                _add(report, "Report Browser", "INFO", "ok", str(bp), bp)
            else:
                _add(report, "Report Browser", "WARNING", "missing",
                     "Report Browser (Chromium) was not found at its expected location. "
                     "The Download Reports feature will not work until this is resolved. "
                     "All other features (QB Sync, Settings, etc.) continue to work normally.", bp)
    except Exception as exc:
        _add(report, "Report Browser", "WARNING", "missing",
             f"Report Browser could not be initialized ({exc}). "
             "The Download Reports feature will not work. "
             "All other features continue to work normally.", None)


def _check_python_presence(report: BootstrapReport) -> None:
    """Note Python version; INFO in bundled, warning in dev mode."""
    import platform
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    arch = platform.architecture()[0]
    bundled = getattr(sys, "frozen", False)
    if bundled:
        _add(report, "Python Runtime", "INFO", "ok",
             f"Bundled Python {py_ver} {arch} (frozen)", None)
    else:
        _add(report, "Python Runtime", "INFO", "ok",
             f"Dev mode Python {py_ver} {arch} (not frozen)", None)


def _write_bootstrap_log(report: BootstrapReport) -> None:
    """Write a machine-readable bootstrap log."""
    try:
        log_path = RUNTIME_DIR / "logs" / f"bootstrap_{datetime.now().strftime('%Y%m%d')}.log"
        lines = [
            f"[BOOTSTRAP] {report.bootstrap_time}",
            f"  can_run={report.can_run}  is_first_run={report.is_first_run}  portable={report.portable_mode}",
            f"  summary={report.summary()}",
        ]
        for item in report.items:
            lines.append(f"  [{item.severity}/{item.status}] {item.name}: {item.message}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass  # Never fail bootstrap just because logging failed


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    report = run_bootstrap()
    print(f"Bootstrap report: {report.summary()}")
    print(f"  can_run={report.can_run}  is_first_run={report.is_first_run}  portable={report.portable_mode}")
    print(f"  blockers={len(report.blockers)}, warnings={len(report.warnings)}")
    for item in report.items:
        print(f"  [{item.severity}/{item.status}] {item.name}: {item.message}")