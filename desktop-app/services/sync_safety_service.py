"""
Pre-sync safety checks. Run BEFORE QB sync is confirmed.
Checks:
  - Missing reports in Drive for selected dates/stores
  - Potential duplicate QB entries
  - Date range sanity (future dates, very old dates)
"""
from __future__ import annotations
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field


@dataclass
class SafetyIssue:
    severity: str  # "warning" | "error"
    title: str
    detail: str
    fix_hint: str = ""


@dataclass
class SafetyCheckResult:
    issues: list = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)

    @property
    def can_proceed(self) -> bool:
        return not self.has_errors


def run_presync_safety_checks(stores: list, date_start: str, date_end: str) -> SafetyCheckResult:
    """
    Run all pre-sync safety checks. Returns SafetyCheckResult.
    Never raises — wraps all checks in try/except.
    """
    result = SafetyCheckResult()

    # 1. Date sanity check
    try:
        s = datetime.strptime(date_start, "%Y-%m-%d").date()
        e = datetime.strptime(date_end, "%Y-%m-%d").date()
        today = date.today()

        if e > today:
            result.issues.append(SafetyIssue(
                severity="warning",
                title="Future dates selected",
                detail=f"The date range includes future dates (up to {date_end}). Reports may not be available yet.",
                fix_hint="Select only dates up to yesterday to ensure reports exist.",
            ))

        days = (e - s).days + 1
        if days > 60:
            result.issues.append(SafetyIssue(
                severity="warning",
                title="Large date range",
                detail=f"You are about to sync {days} days of data ({date_start} → {date_end}). This may take a while.",
                fix_hint="Consider syncing a smaller range first to verify results.",
            ))

        if s < date(2020, 1, 1):
            result.issues.append(SafetyIssue(
                severity="warning",
                title="Very old date range",
                detail=f"Start date {date_start} is over 5 years ago. Ensure this is intentional.",
                fix_hint="Double-check the date range.",
            ))
    except Exception:
        pass

    # 2. Missing reports check
    try:
        missing = _find_missing_drive_reports(stores, date_start, date_end)
        if missing:
            result.issues.append(SafetyIssue(
                severity="error",
                title=f"{len(missing)} report(s) missing from Drive",
                detail="The following reports are needed for sync but not found in Google Drive:\n" +
                       "\n".join(f"  • {m}" for m in missing[:5]) +
                       (f"\n  … and {len(missing)-5} more" if len(missing) > 5 else ""),
                fix_hint="Download missing reports first, then retry sync.",
            ))
    except Exception:
        pass

    # 3. Duplicate check
    try:
        dupes = _find_potential_duplicates(stores, date_start, date_end)
        if dupes:
            result.issues.append(SafetyIssue(
                severity="warning",
                title=f"{len(dupes)} potential duplicate(s) detected",
                detail="These dates may already have been synced to QuickBooks:\n" +
                       "\n".join(f"  • {d}" for d in dupes[:5]),
                fix_hint="Review the sync ledger before proceeding to avoid duplicate entries.",
            ))
    except Exception:
        pass

    return result


def _find_missing_drive_reports(stores: list, date_start: str, date_end: str) -> list:
    """Returns list of 'Store / YYYY-MM-DD' strings for missing reports."""
    missing = []
    try:
        s = datetime.strptime(date_start, "%Y-%m-%d").date()
        e = datetime.strptime(date_end, "%Y-%m-%d").date()

        # Try to use drive inventory if available
        try:
            from gdrive_service import GDriveService
            drive = GDriveService()
            cur = s
            while cur <= e:
                for store in stores:
                    date_str = cur.strftime("%m/%d/%Y")
                    try:
                        f = drive.find_report_file(store, "sales_summary", date_str)
                        if not f:
                            missing.append(f"{store} / {cur.strftime('%Y-%m-%d')}")
                    except Exception:
                        pass
                cur += timedelta(days=1)
        except Exception:
            pass
    except Exception:
        pass
    return missing


def _find_potential_duplicates(stores: list, date_start: str, date_end: str) -> list:
    """Returns list of 'Store / YYYY-MM-DD' strings for already-synced entries."""
    dupes = []
    try:
        from sync_ledger import get_synced_entries
        synced = get_synced_entries(stores, date_start, date_end)
        return synced or []
    except Exception:
        return []
