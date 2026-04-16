"""
ToastPOSManager — Recovery Service

Provides all health-check and recovery-action functions used by the
Recovery Center UI and diagnostic flows.

Every function returns (success: bool, message: str) so callers can
display results directly to the operator.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from app_paths import RUNTIME_DIR

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

def get_app_health() -> dict:
    """Return a dict of app-level health indicators."""
    result = {
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "frozen": getattr(sys, "frozen", False),
        "runtime_dir": str(RUNTIME_DIR),
        "runtime_dir_writable": _is_writable(RUNTIME_DIR),
    }
    # App version from version.json
    version_path = RUNTIME_DIR / "version.json"
    if version_path.exists():
        try:
            v = json.loads(version_path.read_text(encoding="utf-8"))
            result["app_version"] = v.get("app_version", "unknown")
        except Exception:
            result["app_version"] = "unknown"
    else:
        result["app_version"] = "unknown"
    # Last bootstrap log
    logs_dir = RUNTIME_DIR / "logs"
    bootstrap_logs = sorted(logs_dir.glob("bootstrap_*.log"), key=lambda p: p.stat().st_mtime, reverse=True) if logs_dir.exists() else []
    if bootstrap_logs:
        text = bootstrap_logs[0].read_text(encoding="utf-8", errors="replace")
        result["last_bootstrap_log"] = text[:300].replace("\n", " | ")
    else:
        result["last_bootstrap_log"] = "none"
    # Safe mode
    try:
        from safe_mode import is_safe_mode
        result["safe_mode_active"] = is_safe_mode()
    except Exception:
        result["safe_mode_active"] = False
    # Crash markers
    crash_markers = list(logs_dir.glob("safe_mode_*.marker")) if logs_dir.exists() else []
    result["crash_markers_present"] = len(crash_markers)
    return result


def get_config_health() -> dict:
    """Return health info for all user-facing config files."""
    files = {
        ".env.qb": "QB credentials (QB_PASSWORD, etc.)",
        "local-config.json": "App settings (stores, QB paths, QBW mappings)",
        "credentials.json": "Google Drive OAuth client secrets",
        "token.json": "Google Drive auth token",
    }
    checks = {}
    for fname, desc in files.items():
        fpath = RUNTIME_DIR / fname
        check = {
            "path": str(fpath),
            "description": desc,
            "exists": fpath.exists(),
            "readable": False,
            "last_modified": None,
            "malformed": False,
        }
        if fpath.exists():
            try:
                text = fpath.read_text(encoding="utf-8")
                check["readable"] = True
                check["last_modified"] = datetime.fromtimestamp(fpath.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                if fname.endswith(".json"):
                    json.loads(text)  # validate
            except UnicodeDecodeError:
                check["readable"] = True  # binary readable at least
                check["malformed"] = True
            except (ValueError, json.JSONDecodeError) as exc:
                check["malformed"] = True
                _log.warning("Malformed config %s: %s", fname, exc)
            except Exception as exc:
                _log.warning("Could not read %s: %s", fname, exc)
        checks[fname] = check
    return {"files": checks, "config_dir": str(RUNTIME_DIR)}


def get_browser_health() -> dict:
    """Check whether Playwright/Chromium is available."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            bp = Path(pw.chromium.executable_path)
            return {"found": bp.exists(), "path": str(bp), "error": None}
    except ImportError:
        return {"found": False, "path": None, "error": "Playwright not installed."}
    except Exception as exc:
        return {"found": False, "path": None, "error": str(exc)}


def get_crash_history() -> list[dict]:
    """Parse safe-mode markers and return recent crash entries."""
    logs_dir = RUNTIME_DIR / "logs"
    if not logs_dir.exists():
        return []
    markers = sorted(logs_dir.glob("safe_mode_*.marker"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    history = []
    for marker in markers:
        entry = {"file": marker.name, "reason": "", "entered_at": ""}
        try:
            text = marker.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.startswith("reason="):
                    entry["reason"] = line[len("reason="):]
                elif line.startswith("entered_at="):
                    entry["entered_at"] = line[len("entered_at="):]
            if not entry["reason"]:
                entry["reason"] = text.strip()[:100]
        except Exception as exc:
            entry["reason"] = f"Could not read marker: {exc}"
        history.append(entry)
    return history


# ---------------------------------------------------------------------------
# Recovery actions
# ---------------------------------------------------------------------------

def reset_config_to_defaults() -> tuple[bool, str]:
    """Reset local-config.json and .env.qb from example templates."""
    messages = []
    errors = []

    # local-config
    cfg_src = RUNTIME_DIR / "local-config.example.json"
    cfg_dst = RUNTIME_DIR / "local-config.json"
    if cfg_src.exists():
        if cfg_dst.exists():
            backup = cfg_dst.with_name(f"local-config.json.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            try:
                shutil.copy2(cfg_dst, backup)
                messages.append(f"Backed up existing local-config.json → {backup.name}")
            except Exception as exc:
                errors.append(f"Could not backup local-config.json: {exc}")
        try:
            shutil.copy2(cfg_src, cfg_dst)
            messages.append("local-config.json reset to defaults.")
        except Exception as exc:
            errors.append(f"Could not reset local-config.json: {exc}")
    else:
        errors.append("local-config.example.json not found — cannot reset.")

    # .env.qb
    env_src = RUNTIME_DIR / ".env.qb.example"
    env_dst = RUNTIME_DIR / ".env.qb"
    if env_src.exists():
        if env_dst.exists():
            backup = env_dst.with_name(f".env.qb.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
            try:
                shutil.copy2(env_dst, backup)
                messages.append(f"Backed up existing .env.qb → {backup.name}")
            except Exception as exc:
                errors.append(f"Could not backup .env.qb: {exc}")
        try:
            shutil.copy2(env_src, env_dst)
            messages.append(".env.qb reset to defaults.")
        except Exception as exc:
            errors.append(f"Could not reset .env.qb: {exc}")
    else:
        errors.append(".env.qb.example not found — cannot reset.")

    if errors:
        return False, " ".join(errors)
    return True, " ".join(messages)


def clear_toast_session() -> tuple[bool, str]:
    """Delete Toast session and cookie files."""
    patterns = ["toast_session*", "toast_cookies*", "toast_auth*"]
    deleted = 0
    for pat in patterns:
        for fpath in RUNTIME_DIR.glob(pat):
            try:
                fpath.unlink()
                deleted += 1
            except Exception as exc:
                _log.warning("Could not delete %s: %s", fpath, exc)
    if deleted:
        return True, f"Cleared {deleted} Toast session file(s)."
    return True, "No Toast session files found."


def open_runtime_folder() -> tuple[bool, str]:
    """Open the runtime folder in Windows Explorer."""
    try:
        if sys.platform == "win32":
            os.startfile(RUNTIME_DIR)
        else:
            subprocess.run(["open", str(RUNTIME_DIR)], check=False)
        return True, f"Opened: {RUNTIME_DIR}"
    except Exception as exc:
        return False, f"Could not open folder: {exc}"


def toggle_safe_mode() -> tuple[bool, str]:
    """Toggle safe mode on/off and return (success, message)."""
    try:
        from safe_mode import is_safe_mode, activate_safe_mode, deactivate_safe_mode
        if is_safe_mode():
            deactivate_safe_mode()
            return True, "Safe mode deactivated. Normal mode restored."
        else:
            activate_safe_mode("manually toggled by user from Recovery Center")
            return True, "Safe mode activated. The app will start in safe mode next time."
    except Exception as exc:
        return False, f"Could not toggle safe mode: {exc}"


def export_support_bundle() -> tuple[bool, str]:
    """Generate a support bundle and return (success, path_or_error)."""
    try:
        from recovery_center import export_support_bundle as _export
        success, path = _export()
        if success:
            return True, f"Support bundle saved to:\n{path}"
        else:
            return False, path or "Export failed with no details."
    except ImportError:
        return False, "Support bundle feature not available in this build."
    except Exception as exc:
        _log.error("Support bundle failed: %s", exc)
        return False, f"Export failed: {exc}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_writable(path: Path) -> bool:
    """Return True if path is writable by creating a temp file."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(path), prefix="health_check_"):
            pass
        return True
    except Exception:
        return False
