"""
ToastPOSManager — Safe Mode Controller

Safe mode is entered when:
  1. Normal boot had BLOCKERs (from bootstrap report)
  2. User passed --safe flag
  3. App crashed during previous normal run
  4. User chose "Run in Safe Mode" from recovery dialog

In safe mode:
  - Background worker thread is skipped
  - Heavy background actions (periodic Drive scans, auto-sync) are skipped
  - Settings / Recovery tab opens first
  - Diagnostics panel shows full environment info
  - A visible banner notes "Safe Mode" is active
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
    _log_to_file(reason)


def activate_from_bootstrap_report(report) -> None:
    """
    Read a bootstrap_runtime.BootstrapReport and enter safe mode
    if there were blockers or first-run.
    """
    if not report.can_run:
        _state.activate(f"bootstrap blockers: {report.summary()}")
    elif report.is_first_run:
        _state.activate("first-run detected")
    else:
        blockers = [i for i in report.blockers]
        if blockers:
            _state.activate(f"bootstrap blockers: {len(blockers)} issue(s)")


def _log_to_file(reason: str) -> None:
    try:
        log_dir = RUNTIME_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        marker = log_dir / f"safe_mode_{datetime.now().strftime('%Y%m%d_%H%M%S')}.marker"
        marker.write_text(f"safe_mode=True\nreason={reason}\n", encoding="utf-8")
    except Exception:
        pass


def get_last_crash_reason() -> Optional[str]:
    """Check logs for most recent crash marker."""
    try:
        log_dir = RUNTIME_DIR / "logs"
        for marker in sorted((log_dir).glob("crash_*.txt"), reverse=True)[:1]:
            return marker.read_text(encoding="utf-8", errors="replace")[:200]
    except Exception:
        pass
    return None
