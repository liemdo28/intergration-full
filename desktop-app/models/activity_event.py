"""
ToastPOSManager — Activity Event Model

Dataclass for all auditable events in the app.
Every user action and significant system event gets written here.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import sys


def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


BUNDLE_DIR = _resolve_bundle_dir()
RUNTIME_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else BUNDLE_DIR
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EventCategory(str, Enum):
    DOWNLOAD = "download"
    QB_SYNC = "qb_sync"
    REMOVE_TX = "remove_tx"
    DRIVE_UPLOAD = "drive_upload"
    SETTINGS_CHANGE = "settings_change"
    RECOVERY = "recovery"
    APP_LIFECYCLE = "app_lifecycle"
    CRASH = "crash"
    SAFE_MODE = "safe_mode"


class EventSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# ActivityEvent
# ---------------------------------------------------------------------------


@dataclass
class ActivityEvent:
    """
    Represents a single auditable event in the application.

    Parameters
    ----------
    event_id : str
        UUID4 hex string uniquely identifying this event.
    timestamp : str
        ISO 8601 UTC timestamp string.
    category : EventCategory
        Broad functional area the event belongs to.
    severity : EventSeverity
        How serious the event is.
    title : str
        Short one-line summary, e.g. "QB Sync completed".
    detail : str
        Extended human-readable description. Empty string if N/A.
    store : str | None
        Store name this event is associated with, if any.
    user_initiated : bool
        True if triggered by an operator click; False if background.
    success : bool
        True if the operation succeeded; False if it failed.
    duration_seconds : float | None
        Elapsed time in seconds, if applicable.
    extra : dict
        Arbitrary key-value metadata.
    """

    event_id: str
    timestamp: str
    category: EventCategory
    severity: EventSeverity
    title: str
    detail: str
    store: str | None
    user_initiated: bool
    success: bool
    duration_seconds: float | None = None
    extra: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    # Serialisation helpers
    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict:
        """
        Return a plain dict representation of this event.

        Enum members are serialised as their string values so the result is
        JSON-compatible.
        """
        raw = asdict(self)
        raw["category"] = self.category.value
        raw["severity"] = self.severity.value
        return raw

    @classmethod
    def from_dict(cls, data: dict) -> ActivityEvent:
        """
        Reconstruct an ``ActivityEvent`` from a dict.

        Handles datetime / string ambiguity in the ``timestamp`` field.
        """
        # Normalise enum strings back to members
        category = (
            EventCategory(data["category"])
            if isinstance(data["category"], str)
            else data["category"]
        )
        severity = (
            EventSeverity(data["severity"])
            if isinstance(data["severity"], str)
            else data["severity"]
        )

        return cls(
            event_id=data["event_id"],
            timestamp=data["timestamp"],
            category=category,
            severity=severity,
            title=data["title"],
            detail=data.get("detail", ""),
            store=data.get("store"),
            user_initiated=data.get("user_initiated", False),
            success=data.get("success", True),
            duration_seconds=data.get("duration_seconds"),
            extra=data.get("extra", {}),
        )

    def to_json(self) -> str:
        """Return a JSON string representation of this event."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> ActivityEvent:
        """Reconstruct an ``ActivityEvent`` from a JSON string."""
        return cls.from_dict(json.loads(raw))


# ---------------------------------------------------------------------------
# ActivitySummary
# ---------------------------------------------------------------------------


@dataclass
class ActivitySummary:
    """
    Aggregated statistics for a set of activity events.
    """

    total_events: int
    success_count: int
    failure_count: int
    by_category: dict[str, int]
    by_severity: dict[str, int]
