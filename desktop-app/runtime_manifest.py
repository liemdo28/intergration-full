"""
ToastPOSManager — Runtime Manifest Generator

Produces a structured manifest of the current runtime environment.
Used by diagnostics, crash reporter, and Settings UI.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


BUNDLE_DIR = _resolve_bundle_dir()
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE_DIR


@dataclass
class RuntimeManifest:
    app_version: str = ""
    build: str = ""
    commit_hash: str = ""
    build_time: str = ""
    python_version: str = ""
    python_executable: str = ""
    frozen: bool = False
    portable_mode: bool = False
    machine: str = ""
    windows_release: str = ""
    windows_version: str = ""
    runtime_dir: str = ""
    bundle_dir: str = ""
    runtime_dirs_exist: dict[str, bool] = None
    config_files: dict[str, dict] = None
    playwright_browser: Optional[str] = None
    qb_exe_path: Optional[str] = None
    env_vars: dict[str, str] = None
    generated_at: str = ""

    def __post_init__(self):
        if self.runtime_dirs_exist is None:
            self.runtime_dirs_exist = {}
        if self.config_files is None:
            self.config_files = {}
        if self.env_vars is None:
            self.env_vars = {}

    def to_dict(self) -> dict:
        return {k: str(v) if isinstance(v, Path) else v for k, v in asdict(self).items()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def save(self, path: Path | None = None) -> Path:
        path = path or (RUNTIME_DIR / "runtime_manifest.json")
        path.write_text(self.to_json(), encoding="utf-8")
        return path


def build_manifest() -> RuntimeManifest:
    """Build a complete RuntimeManifest from the current environment."""
    manifest = RuntimeManifest()

    # Load version.json
    version_path = BUNDLE_DIR / "version.json"
    if version_path.exists():
        try:
            version_data = json.loads(version_path.read_text(encoding="utf-8"))
            manifest.app_version = version_data.get("app_version", "")
            manifest.build = version_data.get("build", "")
            manifest.commit_hash = version_data.get("commit_hash", "")
            manifest.build_time = version_data.get("build_time", "")
        except Exception:
            pass

    # Python info
    manifest.python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    manifest.python_executable = sys.executable
    manifest.frozen = getattr(sys, "frozen", False)

    # Windows info
    manifest.machine = platform.machine()
    manifest.windows_release, _ = platform.win32_ver()
    manifest.windows_version = platform.version()

    # Paths
    manifest.runtime_dir = str(RUNTIME_DIR)
    manifest.bundle_dir = str(BUNDLE_DIR)

    # Detect portable mode
    env_in_runtime = (RUNTIME_DIR / ".env.qb").exists()
    manifest.portable_mode = manifest.frozen and env_in_runtime

    # Check runtime subdirs
    for subdir in ["logs", "audit-logs", "toast-reports", "recovery-backups", "marketplace-reports"]:
        path = RUNTIME_DIR / subdir
        manifest.runtime_dirs_exist[subdir] = path.exists()

    # Config files
    for cfg in [".env.qb", "local-config.json", "credentials.json", "token.json", ".toast-session.json"]:
        p = RUNTIME_DIR / cfg
        if p.exists():
            try:
                size = p.stat().st_size
                manifest.config_files[cfg] = {"exists": True, "size_bytes": size}
            except Exception:
                manifest.config_files[cfg] = {"exists": True, "error": "stat failed"}
        else:
            manifest.config_files[cfg] = {"exists": False}

    # Playwright browser
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            bp = str(Path(pw.chromium.executable_path))
            manifest.playwright_browser = bp
    except Exception:
        manifest.playwright_browser = None

    # QB exe path
    try:
        from qb_automate import resolve_qb_executable
        qb_exe = resolve_qb_executable()
        if qb_exe:
            manifest.qb_exe_path = str(Path(qb_exe).resolve())
    except Exception:
        pass

    # Relevant env vars
    for key in ["PATH", "COMPUTERNAME", "USERNAME", "USERDOMAIN"]:
        val = os.environ.get(key, "")
        if val:
            manifest.env_vars[key] = val[:80] + ("..." if len(val) > 80 else "")

    manifest.generated_at = datetime.now().isoformat()
    return manifest


if __name__ == "__main__":
    m = build_manifest()
    print(m.to_json())
    m.save()
    print("Manifest saved.")
