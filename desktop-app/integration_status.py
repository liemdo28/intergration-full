from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from toast_reports import REPORT_TYPES


KNOWN_TOAST_STORES = (
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

WORLD_CLOCKS = (
    ("vn", "VN", "Asia/Ho_Chi_Minh", "Ho Chi Minh City"),
    ("san_antonio", "San Antonio", "America/Chicago", "San Antonio, TX"),
    ("stockton", "Stockton", "America/Los_Angeles", "Stockton, CA"),
)

SUCCESS_SYNC_STATUSES = {"success", "preview_success"}
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _coerce_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _format_iso_local(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def get_store_timezone_name(store_name: str) -> str:
    return STORE_TIMEZONES.get(store_name, "America/Chicago")


def get_world_clocks(now: datetime | None = None) -> list[dict]:
    base_now = _coerce_now(now)
    clocks = []
    for key, label, timezone_name, location in WORLD_CLOCKS:
        local_dt = base_now.astimezone(ZoneInfo(timezone_name))
        clocks.append(
            {
                "key": key,
                "label": label,
                "location": location,
                "timezone": timezone_name,
                "iso": _format_iso_local(local_dt),
                "date": local_dt.strftime("%Y-%m-%d"),
                "time": local_dt.strftime("%H:%M:%S"),
                "display": local_dt.strftime("%a %m/%d %H:%M:%S"),
            }
        )
    return clocks


def get_safe_target_date(
    stores: list[str] | tuple[str, ...] | None = None,
    *,
    include_today: bool = False,
    now: datetime | None = None,
) -> str:
    base_now = _coerce_now(now)
    store_list = list(stores or KNOWN_TOAST_STORES)
    target_dates = []
    for store in store_list:
        local_date = base_now.astimezone(ZoneInfo(get_store_timezone_name(store))).date()
        if not include_today:
            local_date -= timedelta(days=1)
        target_dates.append(local_date)
    if not target_dates:
        fallback = base_now.date()
        if not include_today:
            fallback -= timedelta(days=1)
        return fallback.isoformat()
    return min(target_dates).isoformat()


def _parse_business_date(value: str | None) -> str | None:
    if not value:
        return None
    matches = DATE_RE.findall(value)
    if not matches:
        return None
    return max(matches)


def _parse_iso_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        if candidate.endswith("Z"):
            return datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _report_key_from_parts(parts: tuple[str, ...], filename: str) -> str:
    lowered_parts = {part.strip().lower() for part in parts}
    for key, report in REPORT_TYPES.items():
        if report.folder_name.lower() in lowered_parts:
            return key

    stem = Path(filename).stem.lower()
    if stem.startswith("salessummary"):
        return "sales_summary"
    if "item" in stem and "detail" in stem:
        return "item_detail"
    if "payment" in stem:
        return "payment"
    if "order" in stem and "detail" not in stem:
        return "order"
    return "sales_summary"


def _record_sort_key(record: dict) -> tuple[str, str, str]:
    return (
        record.get("business_date") or "",
        record.get("saved_at") or "",
        record.get("filepath") or "",
    )


def _choose_newer(existing: dict | None, candidate: dict) -> dict:
    if existing is None or _record_sort_key(candidate) >= _record_sort_key(existing):
        return candidate
    return existing


def _collect_local_download_records(base_dir: Path) -> dict[tuple[str, str], dict]:
    latest: dict[tuple[str, str], dict] = {}
    reports_root = base_dir / "toast-reports"
    if not reports_root.exists():
        return latest

    for path in reports_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(reports_root)
        if len(rel.parts) < 2:
            continue

        store = rel.parts[0]
        report_key = _report_key_from_parts(rel.parts[1:-1], path.name)
        business_date = _parse_business_date(path.name)
        try:
            saved_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            saved_at = saved_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except OSError:
            saved_at = None

        report = REPORT_TYPES.get(report_key, REPORT_TYPES["sales_summary"])
        candidate = {
            "store": store,
            "report_key": report.key,
            "report_label": report.label,
            "report_folder": report.folder_name,
            "business_date": business_date,
            "saved_at": saved_at,
            "filepath": str(path),
            "source": "local_scan",
        }
        latest[(store, report.key)] = _choose_newer(latest.get((store, report.key)), candidate)
    return latest


def _collect_manifest_download_records(base_dir: Path) -> tuple[dict[tuple[str, str], dict], dict[tuple[str, str], dict]]:
    success_latest: dict[tuple[str, str], dict] = {}
    attempt_latest: dict[tuple[str, str], dict] = {}
    audit_dir = base_dir / "audit-logs" / "download-reports"
    if not audit_dir.exists():
        return success_latest, attempt_latest

    for path in sorted(audit_dir.glob("download-run-*.json"), reverse=True):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        generated_at_raw = manifest.get("generated_at") or path.stem.replace("download-run-", "")
        generated_dt = None
        try:
            generated_dt = datetime.strptime(generated_at_raw, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            generated_dt = None
        generated_at = generated_dt.isoformat().replace("+00:00", "Z") if generated_dt else None

        for attempt in manifest.get("attempts", []):
            store = attempt.get("location")
            if not store:
                continue
            report_key = attempt.get("report_type") or "sales_summary"
            report = REPORT_TYPES.get(report_key, REPORT_TYPES["sales_summary"])
            date_value = attempt.get("date")
            if isinstance(date_value, str) and "/" in date_value:
                try:
                    business_date = datetime.strptime(date_value, "%m/%d/%Y").strftime("%Y-%m-%d")
                except ValueError:
                    business_date = _parse_business_date(date_value)
            else:
                business_date = _parse_business_date(str(date_value or ""))

            candidate = {
                "store": store,
                "report_key": report.key,
                "report_label": report.label,
                "report_folder": report.folder_name,
                "business_date": business_date,
                "saved_at": generated_at,
                "filepath": None,
                "source": "download_manifest",
                "last_attempt_success": bool(attempt.get("success")),
            }
            attempt_latest[(store, report.key)] = _choose_newer(attempt_latest.get((store, report.key)), candidate)

        for item in ((manifest.get("results") or {}).get("files") or []):
            store = item.get("location")
            filepath = item.get("filepath")
            if not store or not filepath:
                continue
            report_key = item.get("report_key") or _report_key_from_parts(tuple(Path(filepath).parts), Path(filepath).name)
            report = REPORT_TYPES.get(report_key, REPORT_TYPES["sales_summary"])
            candidate = {
                "store": store,
                "report_key": report.key,
                "report_label": report.label,
                "report_folder": report.folder_name,
                "business_date": _parse_business_date(filepath),
                "saved_at": generated_at,
                "filepath": filepath,
                "source": "download_manifest",
            }
            success_latest[(store, report.key)] = _choose_newer(success_latest.get((store, report.key)), candidate)

    return success_latest, attempt_latest


def collect_download_state(base_dir: str | Path | None = None) -> dict:
    resolved_base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    local_latest = _collect_local_download_records(resolved_base)
    manifest_success, manifest_attempts = _collect_manifest_download_records(resolved_base)

    combined = dict(manifest_success)
    for key, record in local_latest.items():
        combined[key] = _choose_newer(combined.get(key), record)

    latest_downloads = sorted(
        combined.values(),
        key=lambda item: (
            item.get("business_date") or "",
            item.get("saved_at") or "",
            item.get("store") or "",
            item.get("report_key") or "",
        ),
        reverse=True,
    )

    return {
        "latest_by_store_report": combined,
        "latest_downloads": latest_downloads,
        "latest_attempts_by_store_report": manifest_attempts,
    }


def collect_qb_sync_state(base_dir: str | Path | None = None) -> dict:
    resolved_base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    db_path = resolved_base / "sync-ledger.db"
    latest_attempts: dict[tuple[str, str], dict] = {}
    latest_success: dict[tuple[str, str], dict] = {}
    if not db_path.exists():
        return {
            "latest_attempts_by_store_source": latest_attempts,
            "latest_success_by_store_source": latest_success,
            "latest_attempts": [],
            "latest_success": [],
        }

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT store, date, source_name, status, started_at, finished_at, error_message
            FROM sync_runs
            ORDER BY COALESCE(finished_at, started_at) DESC, rowid DESC
            """
        ).fetchall()

    for row in rows:
        item = dict(row)
        key = (item.get("store") or "", item.get("source_name") or "Unknown")
        item["completed_at"] = item.get("finished_at") or item.get("started_at")
        if key not in latest_attempts:
            latest_attempts[key] = item
        if item.get("status") in SUCCESS_SYNC_STATUSES and key not in latest_success:
            latest_success[key] = item

    return {
        "latest_attempts_by_store_source": latest_attempts,
        "latest_success_by_store_source": latest_success,
        "latest_attempts": sorted(
            latest_attempts.values(),
            key=lambda item: (
                item.get("completed_at") or "",
                item.get("store") or "",
                item.get("source_name") or "",
            ),
            reverse=True,
        ),
        "latest_success": sorted(
            latest_success.values(),
            key=lambda item: (
                item.get("completed_at") or "",
                item.get("store") or "",
                item.get("source_name") or "",
            ),
            reverse=True,
        ),
    }


def _next_day(date_str: str | None) -> str | None:
    if not date_str:
        return None
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def get_auto_download_plan(
    selected_locations: list[str] | tuple[str, ...],
    report_types: list[str] | tuple[str, ...],
    *,
    include_today: bool = False,
    base_dir: str | Path | None = None,
    now: datetime | None = None,
) -> dict:
    resolved_base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    state = collect_download_state(resolved_base)
    latest_by_store_report = state["latest_by_store_report"]
    plan_items = []

    for store in selected_locations:
        target_end = get_safe_target_date([store], include_today=include_today, now=now)
        for report_key in report_types:
            last_record = latest_by_store_report.get((store, report_key))
            if last_record and last_record.get("business_date"):
                start_date = _next_day(last_record["business_date"])
            else:
                start_date = target_end
            if start_date and start_date <= target_end:
                plan_items.append(
                    {
                        "store": store,
                        "report_key": report_key,
                        "report_label": REPORT_TYPES[report_key].label,
                        "start_date": start_date,
                        "end_date": target_end,
                        "last_download_date": (last_record or {}).get("business_date"),
                    }
                )

    if not plan_items:
        target = get_safe_target_date(selected_locations, include_today=include_today, now=now)
        return {
            "has_gap": False,
            "message": f"All selected downloads are already covered through {target}.",
            "items": [],
        }

    start_date = min(item["start_date"] for item in plan_items)
    end_date = max(item["end_date"] for item in plan_items)
    return {
        "has_gap": True,
        "start_date": start_date,
        "end_date": end_date,
        "message": f"Auto-filled missing download range from {start_date} to {end_date}.",
        "items": plan_items,
    }


def get_auto_qb_sync_plan(
    selected_stores: list[str] | tuple[str, ...],
    *,
    include_today: bool = False,
    base_dir: str | Path | None = None,
    now: datetime | None = None,
) -> dict:
    resolved_base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    download_state = collect_download_state(resolved_base)
    qb_state = collect_qb_sync_state(resolved_base)
    latest_downloads = download_state["latest_by_store_report"]
    latest_qb_success = qb_state["latest_success_by_store_source"]
    plan_items = []

    for store in selected_stores:
        latest_download = latest_downloads.get((store, "sales_summary"))
        if not latest_download or not latest_download.get("business_date"):
            continue

        target_end = min(
            latest_download["business_date"],
            get_safe_target_date([store], include_today=include_today, now=now),
        )
        last_qb = latest_qb_success.get((store, "Toasttab"))
        if last_qb and last_qb.get("date"):
            start_date = _next_day(last_qb["date"])
        else:
            start_date = latest_download["business_date"]

        if start_date and start_date <= target_end:
            plan_items.append(
                {
                    "store": store,
                    "source_name": "Toasttab",
                    "start_date": start_date,
                    "end_date": target_end,
                    "last_download_date": latest_download["business_date"],
                    "last_qb_sync_date": (last_qb or {}).get("date"),
                }
            )

    if not plan_items:
        return {
            "has_gap": False,
            "message": "QB Toasttab sync is already caught up for the selected stores.",
            "items": [],
        }

    start_date = min(item["start_date"] for item in plan_items)
    end_date = max(item["end_date"] for item in plan_items)
    return {
        "has_gap": True,
        "start_date": start_date,
        "end_date": end_date,
        "message": f"Auto-filled missing QB sync range from {start_date} to {end_date}.",
        "items": plan_items,
    }


def _build_ai_suggestions(
    download_state: dict,
    qb_state: dict,
    *,
    include_today: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    suggestions: list[dict] = []
    latest_downloads = download_state["latest_by_store_report"]
    latest_attempts = download_state["latest_attempts_by_store_report"]
    latest_qb_success = qb_state["latest_success_by_store_source"]
    latest_qb_attempts = qb_state["latest_attempts_by_store_source"]

    for store in KNOWN_TOAST_STORES:
        target_end = get_safe_target_date([store], include_today=include_today, now=now)
        missing_reports = []
        earliest_start = None
        for report_key, report in REPORT_TYPES.items():
            last_download = latest_downloads.get((store, report_key))
            if last_download and last_download.get("business_date"):
                start_date = _next_day(last_download["business_date"])
            else:
                start_date = target_end
            if start_date and start_date <= target_end:
                missing_reports.append(report.label)
                earliest_start = start_date if earliest_start is None else min(earliest_start, start_date)

        if missing_reports and earliest_start:
            suggestions.append(
                {
                    "id": f"download-gap-{store.lower().replace(' ', '-')}",
                    "kind": "download_gap",
                    "priority": 3,
                    "store": store,
                    "action_label": "Download missing reports",
                    "title": f"{store}: fill missing Toast reports",
                    "description": f"Download {', '.join(missing_reports)} from {earliest_start} to {target_end}.",
                    "start_date": earliest_start,
                    "end_date": target_end,
                    "prompt": (
                        f"Review Toast POS integration for {store} and download missing "
                        f"{', '.join(missing_reports)} reports from {earliest_start} to {target_end} "
                        f"using the store's US business date."
                    ),
                }
            )

        sales_download = latest_downloads.get((store, "sales_summary"))
        qb_success = latest_qb_success.get((store, "Toasttab"))
        if sales_download and sales_download.get("business_date"):
            qb_start = _next_day(qb_success.get("date")) if qb_success and qb_success.get("date") else sales_download["business_date"]
            qb_end = sales_download["business_date"]
            if qb_start and qb_start <= qb_end:
                suggestions.append(
                    {
                        "id": f"qb-gap-{store.lower().replace(' ', '-')}",
                        "kind": "qb_gap",
                        "priority": 2,
                        "store": store,
                        "action_label": "Catch up QB sync",
                        "title": f"{store}: QB Toasttab sync behind downloads",
                        "description": f"Sync Toasttab sales from {qb_start} to {qb_end}.",
                        "start_date": qb_start,
                        "end_date": qb_end,
                        "prompt": (
                            f"Prepare a catch-up QuickBooks sync plan for {store} using Toasttab sales summaries "
                            f"from {qb_start} to {qb_end}."
                        ),
                    }
                )

        qb_attempt = latest_qb_attempts.get((store, "Toasttab"))
        if qb_attempt and qb_attempt.get("status") == "failed":
            suggestions.append(
                {
                    "id": f"qb-failed-{store.lower().replace(' ', '-')}",
                    "kind": "qb_failed",
                    "priority": 4,
                    "store": store,
                    "action_label": "Review failed QB sync",
                    "title": f"{store}: QB sync failed",
                    "description": qb_attempt.get("error_message") or "The latest Toasttab QB sync failed.",
                    "start_date": qb_attempt.get("date"),
                    "end_date": qb_attempt.get("date"),
                    "prompt": (
                        f"Investigate the failed Toasttab to QuickBooks sync for {store} on {qb_attempt.get('date')} "
                        f"and propose the next recovery step."
                    ),
                }
            )

        for report_key, report in REPORT_TYPES.items():
            attempt = latest_attempts.get((store, report_key))
            if attempt and not attempt.get("last_attempt_success"):
                suggestions.append(
                    {
                        "id": f"download-retry-{store.lower().replace(' ', '-')}-{report_key}",
                        "kind": "download_retry",
                        "priority": 1,
                        "store": store,
                        "action_label": "Retry latest download",
                        "title": f"{store}: retry {report.label}",
                        "description": f"The latest {report.label} download attempt did not complete successfully.",
                        "start_date": attempt.get("business_date"),
                        "end_date": attempt.get("business_date"),
                        "prompt": (
                            f"Retry the latest {report.label} Toast download for {store} and verify why the previous "
                            f"attempt around {attempt.get('business_date')} failed."
                        ),
                    }
                )

    suggestions.sort(
        key=lambda item: (
            item.get("priority", 0),
            item.get("end_date") or "",
            item.get("store") or "",
        ),
        reverse=True,
    )
    return suggestions


def build_integration_snapshot(
    *,
    base_dir: str | Path | None = None,
    include_today_for_suggestions: bool = False,
    max_items: int = 6,
    now: datetime | None = None,
) -> dict:
    resolved_base = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    download_state = collect_download_state(resolved_base)
    qb_state = collect_qb_sync_state(resolved_base)
    clocks = get_world_clocks(now)
    suggestions = _build_ai_suggestions(
        download_state,
        qb_state,
        include_today=include_today_for_suggestions,
        now=now,
    )

    latest_downloads = download_state["latest_downloads"][:max_items]
    latest_qb_sync = qb_state["latest_success"][:max_items]
    summary = {
        "stores_tracked": len({item["store"] for item in download_state["latest_downloads"]} | {item["store"] for item in qb_state["latest_success"]}),
        "download_rows": len(download_state["latest_downloads"]),
        "qb_sync_rows": len(qb_state["latest_success"]),
        "last_download_at": next((item.get("saved_at") for item in latest_downloads if item.get("saved_at")), None),
        "last_qb_sync_at": next((item.get("completed_at") for item in latest_qb_sync if item.get("completed_at")), None),
        "download_gap_count": sum(1 for item in suggestions if item["kind"] == "download_gap"),
        "qb_gap_count": sum(1 for item in suggestions if item["kind"] == "qb_gap"),
        "failed_qb_count": sum(1 for item in suggestions if item["kind"] == "qb_failed"),
    }

    return {
        "generated_at": _format_iso_local(_coerce_now(now)),
        "base_dir": str(resolved_base),
        "world_clocks": clocks,
        "summary": summary,
        "latest_downloads": latest_downloads,
        "latest_qb_sync": latest_qb_sync,
        "latest_qb_attempts": qb_state["latest_attempts"][:max_items],
        "ai_suggestions": suggestions[:max_items],
    }
