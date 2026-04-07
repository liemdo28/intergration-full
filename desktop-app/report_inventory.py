from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app_paths import runtime_path
from toast_reports import DEFAULT_REPORT_TYPE_KEYS, get_report_type, infer_report_type, normalize_report_types

INVENTORY_DB_PATH = runtime_path("report-inventory.db")
MAX_LOOKBACK_DAYS = 90
EMPTY_WINDOW_DAYS = 7
KNOWN_STORE_NAMES = (
    "Stockton",
    "The Rim",
    "Stone Oak",
    "Bandera",
    "WA1",
    "WA2",
    "WA3",
)
STORE_TIMEZONES = {
    "Stockton": "America/Los_Angeles",
    "The Rim": "America/Chicago",
    "Stone Oak": "America/Chicago",
    "Bandera": "America/Chicago",
    "WA1": "America/Chicago",
    "WA2": "America/Chicago",
    "WA3": "America/Chicago",
}

ISO_DATE_RE = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")
US_DATE_RE = re.compile(r"(\d{2})[-_](\d{2})[-_](20\d{2})")


def _coerce_base_dir(base_dir: str | Path | None = None) -> Path:
    return Path(base_dir) if base_dir else Path(__file__).resolve().parent


def _report_root(base_dir: str | Path | None = None) -> Path:
    base = _coerce_base_dir(base_dir)
    if base.name.lower() == "toast-reports":
        return base
    return base / "toast-reports"


def _normalize_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _safe_target_date_for_store(store_name: str, *, include_today: bool = False, now: datetime | None = None) -> str:
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    zone_name = STORE_TIMEZONES.get(store_name) or "America/Chicago"
    local_now = reference.astimezone(ZoneInfo(zone_name))
    target = local_now.date() if include_today else (local_now.date() - timedelta(days=1))
    return target.strftime("%Y-%m-%d")


def extract_business_dates_from_name(text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in ISO_DATE_RE.finditer(text or ""):
        date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        normalized = _normalize_date(date_str)
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    for match in US_DATE_RE.finditer(text or ""):
        date_str = f"{match.group(3)}-{match.group(1)}-{match.group(2)}"
        normalized = _normalize_date(date_str)
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _candidate_report_dirs(store_dir: Path, report_key: str) -> list[Path]:
    report = get_report_type(report_key)
    dirs: list[Path] = []
    seen: set[str] = set()
    for folder_name in (report.folder_name, *report.folder_aliases):
        folder = store_dir / folder_name
        normalized = str(folder).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        dirs.append(folder)
    return dirs


def find_existing_local_report(
    base_dir: str | Path | None,
    *,
    store_name: str,
    report_type: str,
    business_date: str,
) -> dict | None:
    root = _report_root(base_dir)
    store_dir = root / store_name
    if not store_dir.exists():
        return None
    report = get_report_type(report_type)
    for report_dir in _candidate_report_dirs(store_dir, report.key):
        if not report_dir.exists():
            continue
        for file_path in sorted(report_dir.glob("*")):
            if not file_path.is_file():
                continue
            dates = extract_business_dates_from_name(file_path.name)
            if business_date in dates:
                return {
                    "store": store_name,
                    "report_key": report.key,
                    "report_label": report.label,
                    "business_date": business_date,
                    "filepath": str(file_path),
                    "filename": file_path.name,
                    "source": "local_inventory",
                    "status": "existing_local",
                }
    return None


def scan_local_report_inventory(base_dir: str | Path | None = None) -> list[dict]:
    root = _report_root(base_dir)
    if not root.exists():
        return []
    rows: list[dict] = []
    for store_dir in sorted(root.iterdir()):
        if not store_dir.is_dir():
            continue
        for report_dir in sorted(store_dir.iterdir()):
            if not report_dir.is_dir():
                continue
            for file_path in sorted(report_dir.glob("*")):
                if not file_path.is_file():
                    continue
                report = infer_report_type((store_dir.name, report_dir.name), file_path.name)
                business_dates = extract_business_dates_from_name(f"{report_dir.name} {file_path.name}")
                if not business_dates:
                    business_dates = [None]
                stat = file_path.stat()
                for business_date in business_dates:
                    rows.append(
                        {
                            "store": store_dir.name,
                            "report_key": report.key,
                            "report_label": report.label,
                            "business_date": business_date,
                            "filepath": str(file_path),
                            "filename": file_path.name,
                            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                            "size_bytes": int(stat.st_size),
                            "source": "local_inventory",
                        }
                    )
    return rows


def _write_inventory_tables(rows: list[dict], missing_rows: list[dict]) -> None:
    INVENTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(INVENTORY_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS report_inventory (
                store TEXT NOT NULL,
                report_key TEXT NOT NULL,
                report_label TEXT NOT NULL,
                business_date TEXT,
                filepath TEXT NOT NULL,
                filename TEXT NOT NULL,
                modified_at TEXT,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'local_inventory'
            )
            """
        )
        conn.execute("DELETE FROM report_inventory")
        conn.executemany(
            """
            INSERT INTO report_inventory (
                store, report_key, report_label, business_date, filepath, filename, modified_at, size_bytes, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["store"],
                    row["report_key"],
                    row["report_label"],
                    row.get("business_date"),
                    row["filepath"],
                    row["filename"],
                    row.get("modified_at"),
                    row.get("size_bytes", 0),
                    row.get("source", "local_inventory"),
                )
                for row in rows
            ],
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS missing_report_records (
                store TEXT NOT NULL,
                report_key TEXT NOT NULL,
                report_label TEXT NOT NULL,
                business_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                download_supported INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute("DELETE FROM missing_report_records")
        conn.executemany(
            """
            INSERT INTO missing_report_records (
                store, report_key, report_label, business_date, reason, detected_at, download_supported
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["store"],
                    row["report_key"],
                    row["report_label"],
                    row["business_date"],
                    row["reason"],
                    row["detected_at"],
                    int(bool(row.get("download_supported", True))),
                )
                for row in missing_rows
            ],
        )
        conn.commit()


def _build_missing_rows(
    inventory_rows: list[dict],
    *,
    include_today: bool = False,
    now: datetime | None = None,
    store_names: list[str] | tuple[str, ...] | None = None,
    report_keys: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    normalized_reports = normalize_report_types(report_keys or list(DEFAULT_REPORT_TYPE_KEYS))
    target_reports = [report for report in normalized_reports if report.download_supported]
    target_stores = list(store_names or KNOWN_STORE_NAMES)
    by_store_report: dict[tuple[str, str], set[str]] = {}
    for row in inventory_rows:
        business_date = row.get("business_date")
        if not business_date:
            continue
        key = (row["store"], row["report_key"])
        by_store_report.setdefault(key, set()).add(business_date)

    detected_at = datetime.now(UTC).isoformat()
    missing_rows: list[dict] = []
    for store in target_stores:
        target_end = _safe_target_date_for_store(store, include_today=include_today, now=now)
        target_end_dt = datetime.strptime(target_end, "%Y-%m-%d")
        for report in target_reports:
            seen_dates = by_store_report.get((store, report.key), set())
            if seen_dates:
                earliest_seen = min(datetime.strptime(value, "%Y-%m-%d") for value in seen_dates)
                window_start_dt = max(earliest_seen, target_end_dt - timedelta(days=MAX_LOOKBACK_DAYS - 1))
            else:
                window_start_dt = target_end_dt - timedelta(days=EMPTY_WINDOW_DAYS - 1)
            last_seen_dt = max((datetime.strptime(value, "%Y-%m-%d") for value in seen_dates), default=None)

            cursor = window_start_dt
            while cursor <= target_end_dt:
                date_str = cursor.strftime("%Y-%m-%d")
                if date_str not in seen_dates:
                    reason = "gap_after_last_known" if last_seen_dt and cursor > last_seen_dt else "gap_inside_window"
                    if not seen_dates:
                        reason = "recent_window_empty"
                    missing_rows.append(
                        {
                            "store": store,
                            "report_key": report.key,
                            "report_label": report.label,
                            "business_date": date_str,
                            "reason": reason,
                            "detected_at": detected_at,
                            "download_supported": report.download_supported,
                        }
                    )
                cursor += timedelta(days=1)
    return missing_rows


def refresh_report_inventory(
    base_dir: str | Path | None = None,
    *,
    include_today: bool = False,
    now: datetime | None = None,
    store_names: list[str] | tuple[str, ...] | None = None,
    report_keys: list[str] | tuple[str, ...] | None = None,
) -> dict:
    inventory_rows = scan_local_report_inventory(base_dir)
    missing_rows = _build_missing_rows(
        inventory_rows,
        include_today=include_today,
        now=now,
        store_names=store_names,
        report_keys=report_keys,
    )
    _write_inventory_tables(inventory_rows, missing_rows)
    return {"inventory_rows": inventory_rows, "missing_rows": missing_rows}


def list_missing_report_records(
    base_dir: str | Path | None = None,
    *,
    include_today: bool = False,
    now: datetime | None = None,
    max_items: int | None = None,
    store_names: list[str] | tuple[str, ...] | None = None,
    report_keys: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    snapshot = refresh_report_inventory(
        base_dir,
        include_today=include_today,
        now=now,
        store_names=store_names,
        report_keys=report_keys,
    )
    rows = sorted(
        snapshot["missing_rows"],
        key=lambda item: (item["business_date"], item["store"], item["report_key"]),
    )
    return rows[:max_items] if max_items else rows


def group_missing_report_records(rows: list[dict]) -> list[dict]:
    grouped: list[dict] = []
    ordered = sorted(rows, key=lambda item: (item["store"], item["report_key"], item["business_date"]))
    current: dict | None = None
    previous_date: datetime | None = None
    for row in ordered:
        row_date = datetime.strptime(row["business_date"], "%Y-%m-%d")
        if (
            current
            and current["store"] == row["store"]
            and current["report_key"] == row["report_key"]
            and previous_date
            and row_date == previous_date + timedelta(days=1)
        ):
            current["end_date"] = row["business_date"]
            current["count"] += 1
        else:
            current = {
                "store": row["store"],
                "report_key": row["report_key"],
                "report_label": row["report_label"],
                "start_date": row["business_date"],
                "end_date": row["business_date"],
                "count": 1,
                "reason": row["reason"],
            }
            grouped.append(current)
        previous_date = row_date
    return grouped
