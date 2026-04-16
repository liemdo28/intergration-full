"""
ToastPOSManager — Activity Log Service

Persists ActivityEvent records to monthly JSONL log files and provides
query/aggregation helpers.
"""

from __future__ import annotations

import csv
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import sys

from models.activity_event import (
    ActivityEvent,
    ActivitySummary,
    EventCategory,
    EventSeverity,
)


# ---------------------------------------------------------------------------
# Bundle / runtime path resolution
# ---------------------------------------------------------------------------

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
LOG_DIR = RUNTIME_DIR / "activity-logs"

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_log_dir() -> None:
    """Create the log directory if it does not already exist."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.warning("Could not create activity log directory %s: %s", LOG_DIR, exc)


def _event_log_path(ref_date: datetime | None = None) -> Path:
    """
    Return the path to the monthly log file.

    Parameters
    ----------
    ref_date : datetime | None
        Reference date for determining the file month.
        Defaults to the current UTC date.

    Returns
    -------
    Path
        Path to the activity_YYYYMM.jsonl file for the given month.
    """
    if ref_date is None:
        ref_date = datetime.utcnow()
    return LOG_DIR / f"activity_{ref_date.strftime('%Y%m')}.jsonl"


def _all_log_files(since: datetime | None = None) -> list[Path]:
    """
    Return all monthly log files that should be consulted for a given
    ``since`` window.

    When ``since`` is None all ``activity_*.jsonl`` files under LOG_DIR are
    returned.  When ``since`` is provided only the files whose month overlaps
    the window are included.
    """
    if not LOG_DIR.is_dir():
        return []
    if since is None:
        return sorted(LOG_DIR.glob("activity_*.jsonl"), reverse=True)
    # Determine the range of months we need
    end = datetime.utcnow()
    files = []
    cur = datetime(since.year, since.month, 1)
    while cur <= end:
        candidate = LOG_DIR / f"activity_{cur.strftime('%Y%m')}.jsonl"
        if candidate.exists():
            files.append(candidate)
        # Advance to next month
        if cur.month == 12:
            cur = datetime(cur.year + 1, 1, 1)
        else:
            cur = datetime(cur.year, cur.month + 1, 1)
    return files


# ---------------------------------------------------------------------------
# Public API — write
# ---------------------------------------------------------------------------

def log_event(event: ActivityEvent) -> None:
    """
    Append a single event to the current month's log file.

    Parameters
    ----------
    event : ActivityEvent
        The event to persist.
    """
    _ensure_log_dir()
    path = _event_log_path()
    try:
        with open(path, "a", newline="", encoding="utf-8") as fh:
            fh.write(event.to_json())
            fh.write("\n")
    except OSError as exc:
        _logger.error("Failed to write activity event to %s: %s", path, exc)


def log(
    category: EventCategory,
    title: str,
    detail: str = "",
    store: str | None = None,
    severity: EventSeverity = EventSeverity.INFO,
    success: bool = True,
    duration: float | None = None,
    **extra,
) -> ActivityEvent:
    """
    Convenience factory that creates and persists an ``ActivityEvent``.

    Parameters
    ----------
    category : EventCategory
        Functional area of the event.
    title : str
        Short one-line summary.
    detail : str, optional
        Extended description. Defaults to empty string.
    store : str | None, optional
        Associated store name. Defaults to None.
    severity : EventSeverity, optional
        Event severity. Defaults to INFO.
    success : bool, optional
        Whether the operation succeeded. Defaults to True.
    duration : float | None, optional
        Elapsed time in seconds. Defaults to None.
    **extra : dict
        Additional key-value metadata added to ``event.extra``.

    Returns
    -------
    ActivityEvent
        The newly created and persisted event.
    """
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    event = ActivityEvent(
        event_id=uuid.uuid4().hex,
        timestamp=timestamp,
        category=category,
        severity=severity,
        title=title,
        detail=detail,
        store=store,
        user_initiated=True,
        success=success,
        duration_seconds=duration,
        extra=extra,
    )
    log_event(event)
    return event


# ---------------------------------------------------------------------------
# Public API — read / aggregate
# ---------------------------------------------------------------------------

def get_events(
    since: datetime | None = None,
    category: EventCategory | None = None,
    store: str | None = None,
    limit: int = 200,
) -> list[ActivityEvent]:
    """
    Retrieve activity events matching the given filters.

    Parameters
    ----------
    since : datetime | None
        Only return events after this timestamp.
        If None all matching events are returned (subject to limit).
    category : EventCategory | None
        Restrict to this category.  None means all categories.
    store : str | None
        Restrict to this store name.  None means all stores.
    limit : int, optional
        Maximum number of events to return. Defaults to 200.

    Returns
    -------
    list[ActivityEvent]
        Events sorted by timestamp descending, newest first.
    """
    events: list[ActivityEvent] = []
    for log_path in _all_log_files(since):
        try:
            with open(log_path, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = ActivityEvent.from_json(line)
                    except Exception:
                        # Skip malformed lines without crashing the whole query
                        continue

                    # Apply filters
                    if since is not None:
                        try:
                            ev_time = datetime.fromisoformat(
                                event.timestamp.rstrip("Z")
                            )
                            if ev_time < since:
                                continue
                        except ValueError:
                            pass
                    if category is not None and event.category != category:
                        continue
                    if store is not None and event.store != store:
                        continue

                    events.append(event)
        except OSError as exc:
            _logger.warning("Could not read log file %s: %s", log_path, exc)

    # Sort descending by timestamp
    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events[:limit]


def get_activity_summary(since: datetime | None = None) -> ActivitySummary:
    """
    Compute aggregate statistics over the filtered event set.

    Parameters
    ----------
    since : datetime | None
        Window start.  None means "all time".

    Returns
    -------
    ActivitySummary
        Aggregated counts by category and severity.
    """
    events = get_events(since=since, limit=2000)
    total = len(events)
    successes = sum(1 for e in events if e.success)
    failures = total - successes

    by_category: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    for e in events:
        by_category[e.category.value] += 1
        by_severity[e.severity.value] += 1

    return ActivitySummary(
        total_events=total,
        success_count=successes,
        failure_count=failures,
        by_category=dict(by_category),
        by_severity=dict(by_severity),
    )


def get_recent_activity(count: int = 10) -> list[ActivityEvent]:
    """
    Shorthand to fetch the most recent ``count`` events.

    Parameters
    ----------
    count : int, optional
        Number of events to return. Defaults to 10.

    Returns
    -------
    list[ActivityEvent]
    """
    return get_events(limit=count)


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def clear_old_events(older_than_days: int = 90) -> int:
    """
    Delete monthly log files older than the specified threshold.

    Parameters
    ----------
    older_than_days : int, optional
        Age threshold in days.  Defaults to 90.

    Returns
    -------
    int
        Number of files deleted.
    """
    if not LOG_DIR.is_dir():
        return 0
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    cutoff_str = cutoff.strftime("%Y%m")
    deleted = 0
    for log_path in LOG_DIR.glob("activity_*.jsonl"):
        # Extract YYYYMM from filename
        month_part = log_path.stem.removeprefix("activity_")
        if month_part < cutoff_str:
            try:
                log_path.unlink()
                deleted += 1
                _logger.info("Deleted old activity log: %s", log_path.name)
            except OSError as exc:
                _logger.warning("Could not delete %s: %s", log_path, exc)
    return deleted


def export_events_csv(events: list[ActivityEvent], dest_path: Path) -> None:
    """
    Export a list of events to a CSV file.

    Parameters
    ----------
    events : list[ActivityEvent]
        Events to export.
    dest_path : Path
        Destination CSV file path.

    Raises
    ------
    OSError
        If the file cannot be written.
    """
    fieldnames = [
        "timestamp",
        "category",
        "severity",
        "title",
        "detail",
        "store",
        "success",
        "duration_seconds",
    ]
    try:
        with open(dest_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for event in events:
                writer.writerow({
                    "timestamp": event.timestamp,
                    "category": event.category.value,
                    "severity": event.severity.value,
                    "title": event.title,
                    "detail": event.detail,
                    "store": event.store or "",
                    "success": event.success,
                    "duration_seconds": (
                        f"{event.duration_seconds:.2f}"
                        if event.duration_seconds is not None
                        else ""
                    ),
                })
    except OSError as exc:
        _logger.error("Failed to export events to CSV %s: %s", dest_path, exc)
        raise