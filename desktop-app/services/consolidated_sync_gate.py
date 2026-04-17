"""
Consolidated Sync Gate

Single entry point that runs ALL pre-sync safety checks in one pass:
1. QB readiness (executable + credentials)
2. Company file guard (QBW file accessible)
3. Source completeness (all Drive files exist)
4. Duplicate detection (sync ledger)
5. File validity (local files not corrupted)
6. Date sanity (no future dates, no extreme ranges)

Returns a GateResult with:
  - can_proceed: bool
  - blockers: list of blocking issues (must fix)
  - warnings: list of warnings (proceed with caution)
  - summary_for_ui(): formatted dict for UI display
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class GateIssue:
    severity: str   # "BLOCK" | "WARN"
    code: str       # machine-readable code
    title: str      # client-safe title
    detail: str     # client-safe detail
    fix_hint: str = ""
    nav_target: str = ""  # "navigate:wizard_download", etc.


@dataclass
class GateResult:
    stores: list = field(default_factory=list)
    date_start: str = ""
    date_end: str = ""
    issues: list = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def blockers(self) -> list:
        return [i for i in self.issues if i.severity == "BLOCK"]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == "WARN"]

    @property
    def can_proceed(self) -> bool:
        return len(self.blockers) == 0

    def summary_for_ui(self) -> dict:
        return {
            "can_proceed": self.can_proceed,
            "blocker_count": len(self.blockers),
            "warning_count": len(self.warnings),
            "blockers": [{"title": i.title, "detail": i.detail, "fix": i.fix_hint, "nav": i.nav_target} for i in self.blockers],
            "warnings": [{"title": i.title, "detail": i.detail, "fix": i.fix_hint} for i in self.warnings],
        }


def run_consolidated_gate(
    stores: list,
    date_start: str,
    date_end: str,
    report_types: list = None,
    check_drive: bool = True,
    check_duplicates: bool = True,
) -> GateResult:
    """
    Run all sync safety checks. Returns GateResult. Never raises.
    """
    result = GateResult(stores=stores, date_start=date_start, date_end=date_end)
    report_types = report_types or ["sales_summary"]

    # 1. Input validation
    if not stores:
        result.issues.append(GateIssue("BLOCK", "no_stores", "No stores selected",
            "At least one store must be selected before syncing.", "Select stores in step 1."))
        return result

    # 2. Date sanity
    _check_date_sanity(result, date_start, date_end)

    # 3. QB readiness
    _check_qb_readiness(result)

    # 4. Source completeness (Drive)
    if check_drive:
        _check_source_completeness(result, stores, date_start, date_end, report_types)

    # 5. Duplicate detection
    if check_duplicates:
        _check_duplicates(result, stores, date_start, date_end)

    # 6. Local file validity
    _check_local_file_validity(result, stores, date_start, date_end, report_types)

    return result


# ── Individual checks (each wraps in try/except) ──────────────────────────

def _check_date_sanity(result: GateResult, date_start: str, date_end: str):
    try:
        from datetime import date, timedelta
        s = datetime.strptime(date_start, "%Y-%m-%d").date()
        e = datetime.strptime(date_end, "%Y-%m-%d").date()
        today = date.today()
        if e > today:
            result.issues.append(GateIssue("WARN", "future_dates",
                "Selected dates include the future",
                f"End date {date_end} is in the future. Reports may not be available yet.",
                "Select dates up to yesterday."))
        days = (e - s).days + 1
        if days > 90:
            result.issues.append(GateIssue("WARN", "large_range",
                f"Large date range ({days} days)",
                f"Syncing {days} days at once may take a long time.",
                "Consider syncing a smaller range first."))
    except Exception:
        pass


def _check_qb_readiness(result: GateResult):
    try:
        import os
        from diagnostics import _resolve_qb_executable
        exe = _resolve_qb_executable()
        if not exe or not os.path.exists(str(exe)):
            result.issues.append(GateIssue("BLOCK", "qb_missing",
                "QuickBooks Desktop is not found",
                "QuickBooks Desktop must be installed on this machine for sync to work.",
                "Install QuickBooks Desktop and try again."))
    except Exception:
        pass

    try:
        from app_paths import RUNTIME_DIR
        env_qb = RUNTIME_DIR / ".env.qb"
        if not env_qb.exists():
            result.issues.append(GateIssue("BLOCK", "qb_creds_missing",
                "QuickBooks credentials are not configured",
                "The QB credentials file is missing. Sync cannot authenticate with QuickBooks.",
                "Open Settings > QuickBooks to configure your QB password."))
    except Exception:
        pass


def _check_source_completeness(result: GateResult, stores, date_start, date_end, report_types):
    try:
        from services.source_completeness_service import check_source_completeness
        report = check_source_completeness(stores, date_start, date_end, report_types)
        if not report.drive_available:
            result.issues.append(GateIssue("BLOCK", "drive_unavailable",
                "Google Drive is not connected",
                "Source reports cannot be verified because Google Drive is not connected. "
                "Sync is blocked until Drive is connected.",
                "Open Settings > Google Drive to connect your account.",
                nav_target="navigate:settings"))
        elif report.missing_count > 0:
            missing_sample = [f"{f.store} / {f.date}" for f in report.missing_files[:3]]
            sample_text = ", ".join(missing_sample)
            if report.missing_count > 3:
                sample_text += f" … and {report.missing_count - 3} more"
            result.issues.append(GateIssue("BLOCK", "missing_source_files",
                f"{report.missing_count} required report(s) are missing from Drive",
                f"The following reports must be downloaded before sync can run: {sample_text}.",
                "Use the Download Wizard to download missing reports, then return here.",
                nav_target="navigate:wizard_download"))
    except Exception:
        pass


def _check_duplicates(result: GateResult, stores, date_start, date_end):
    try:
        from sync_ledger import get_synced_entries
        dupes = get_synced_entries(stores, date_start, date_end)
        if dupes:
            dupe_sample = dupes[:3]
            sample_text = ", ".join(str(d) for d in dupe_sample)
            if len(dupes) > 3:
                sample_text += f" … and {len(dupes) - 3} more"
            result.issues.append(GateIssue("WARN", "potential_duplicates",
                f"{len(dupes)} date(s) may already be synced",
                f"These dates may already have QuickBooks entries: {sample_text}. "
                "Syncing again may create duplicates.",
                "Review the sync ledger before proceeding."))
    except Exception:
        pass


# Pre-sync warning categories that are accounting-integrity issues and must
# be treated as hard blockers rather than advisories.
# A date_gap or incomplete_report means data is missing for accounting — sync
# must not proceed or it will write incomplete journal entries to QuickBooks.
_PRESYNC_ESCALATE_TO_BLOCK = {"date_gap", "missing_file", "incomplete_report"}


def _check_local_file_validity(result: GateResult, stores, date_start, date_end,
                                report_types=None):
    """
    Validate local report files before sync.

    - Pre-sync blockers (missing_file, corrupt file) → BLOCK
    - Pre-sync warnings: date_gap / incomplete_report are escalated to BLOCK
      because missing days = accounting gap = QuickBooks entries would be wrong.
    - Other warnings (e.g. large range) → WARN (proceed with caution)
    """
    try:
        from pre_sync_validator import validate_sync_readiness
        report = validate_sync_readiness(
            stores=stores,
            date_range_start=date_start,
            date_range_end=date_end,
            report_types=report_types or ["sales_summary"],
        )
        # Pre-sync blockers → BLOCK
        for b in report.blockers[:5]:  # cap to avoid UI overflow
            result.issues.append(GateIssue("BLOCK", f"presync_{b.category}",
                _presync_title(b.category),
                b.detail,
                b.suggested_fix or ""))
        # Pre-sync warnings — escalate accounting-integrity issues to BLOCK
        for w in report.warnings[:5]:
            severity = "BLOCK" if w.category in _PRESYNC_ESCALATE_TO_BLOCK else "WARN"
            result.issues.append(GateIssue(severity, f"presync_{w.category}",
                _presync_title(w.category),
                w.detail,
                w.suggested_fix or "" if hasattr(w, "suggested_fix") else ""))
    except Exception:
        pass


def _presync_title(category: str) -> str:
    TITLES = {
        "missing_file": "Required report file is missing",
        "duplicate": "Duplicate report file detected",
        "invalid_file": "Report file appears to be corrupted",
        "date_gap": "Gap detected in report date coverage",
        "incomplete_report": "Report file is incomplete",
    }
    return TITLES.get(category, category.replace("_", " ").title())
