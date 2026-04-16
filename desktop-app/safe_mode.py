"""
ToastPOSManager — Safe Mode Controller

Safe mode is a deterministic, persistent startup mode.

Entry conditions (any one activates safe mode):
  1. Bootstrap report has blockers or is first-run
  2. User passes --safe flag
  3. A crash marker exists from the previous run
  4. User chooses "Run in Safe Mode" from the recovery dialog

In safe mode:
  - Background worker thread is skipped
  - Heavy background actions (periodic Drive scans, auto-sync) are skipped
  - Settings / Recovery tab opens first
  - A visible amber banner shows safe mode is active

Exit safe mode:
  - Run a normal successful session (app closes cleanly without crashes)
  - Delete all safe_mode_*.marker files in the logs/ folder
  - Restart without --safe flag after a clean exit

Persistence:
  - Safe mode state is NOT persisted across runs by default
  - A crash marker triggers safe mode on the NEXT run only
  - After a successful clean run, safe mode is automatically cleared
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent

BUNDLE_DIR = _resolve_bundle_dir()
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE_DIR

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------
@dataclass
class SafeModeConfig:
    active: bool = False
    reason: str = ""
    skipped_workers: list[str] = field(default_factory=list)
    started_at: str = ""

    def activate(self, reason: str) -> None:
        self.active = True
        self.reason = reason
        self.started_at = datetime.now().isoformat()
        logging.info(f"Safe mode activated: {reason}")

    @property
    def should_skip_worker(self) -> bool:
        return self.active

    @property
    def should_skip_periodic_scan(self) -> bool:
        return self.active

    def summary(self) -> str:
        if not self.active:
            return "Safe mode: OFF"
        return f"Safe mode: ON — {self.reason}"

    def deactivate(self) -> None:
        """Exit safe mode and clear the state."""
        self.active = False
        self.reason = ""
        self.skipped_workers.clear()
        self.started_at = ""
        logging.info("Safe mode deactivated")


# ---------------------------------------------------------------------------
# Global singleton (module-level state survives across imports)
# ---------------------------------------------------------------------------
_state = SafeModeConfig()


def is_safe_mode() -> bool:
    return _state.active


def get_safe_mode_config() -> SafeModeConfig:
    return _state


def activate_safe_mode(reason: str) -> None:
    """Programmatically enter safe mode (e.g. after a crash)."""
    _state.activate(reason)
    _write_marker(reason)


def deactivate_safe_mode() -> None:
    """Exit safe mode, clear marker files, clear state."""
    _state.deactivate()
    _clear_markers()


def activate_from_bootstrap_report(report) -> None:
    """
    Read a bootstrap_runtime.BootstrapReport and enter safe mode
    if there were blockers, first-run, or an existing crash marker.
    """
    if not report.can_run:
        _state.activate(f"bootstrap blockers: {report.summary()}")
        return
    if report.is_first_run:
        _state.activate("first-run detected")
        return
    # Check for crash marker from previous run
    prev_crash = get_last_crash_marker()
    if prev_crash:
        _state.activate(f"previous crash: {prev_crash}")


def _write_marker(reason: str) -> None:
    """Write a safe-mode marker file to logs/ so next run can detect it."""
    try:
        log_dir = RUNTIME_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        marker = log_dir / f"safe_mode_{datetime.now().strftime('%Y%m%d_%H%M%S')}.marker"
        marker.write_text(
            f"safe_mode=True\nreason={reason}\nentered_at={datetime.now().isoformat()}\n",
            encoding="utf-8",
        )
        logging.debug(f"Safe mode marker written: {marker}")
    except Exception:
        pass


def _clear_markers() -> None:
    """Remove all safe_mode marker files after a clean run."""
    try:
        log_dir = RUNTIME_DIR / "logs"
        for marker in log_dir.glob("safe_mode_*.marker"):
            marker.unlink(missing_ok=True)
        logging.debug("Safe mode markers cleared")
    except Exception:
        pass


def get_last_crash_marker() -> Optional[str]:
    """Return the reason string from the most recent safe_mode marker, if any."""
    try:
        log_dir = RUNTIME_DIR / "logs"
        markers = sorted(log_dir.glob("safe_mode_*.marker"), key=lambda p: p.stat().st_mtime, reverse=True)
        if markers:
            content = markers[0].read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if line.startswith("reason="):
                    return line[len("reason="):]
            return content[:200]
    except Exception:
        pass
    return None


def get_last_crash_reason() -> Optional[str]:
    """Legacy alias — checks safe_mode markers only."""
    return get_last_crash_marker()
