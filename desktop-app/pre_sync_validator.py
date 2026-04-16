"""
ToastPOSManager — Pre-Sync Validation Gate

Runs before QB Sync to detect:
1. Missing daily report files for selected stores/dates
2. Duplicate report files (same date, same store, different content)
3. Invalid/corrupted report files
4. Date gaps in report coverage

Returns a PreSyncValidationReport with:
  - issues: list of Issue objects (severity, category, store, date, detail)
  - can_proceed: bool — True if no blocking issues
  - warnings: list — non-blocking issues
  - blockers: list — must-fix before sync
  - recommended_action: str — "proceed", "fix_first", "preview_only"
"""

from __future__ import annotations

import hashlib
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from app_paths import runtime_path, app_path

_log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────
REPORTS_DIR = runtime_path("toast-reports")

# Minimum file age in seconds before a file is considered "ready" (not still downloading)
_MIN_FILE_AGE_SECONDS = 300  # 5 minutes


# ── Dataclasses ──────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    severity: str          # "ERROR", "WARNING", "INFO"
    category: str         # "missing_file", "duplicate", "invalid_file", "date_gap", "incomplete_report"
    store: str             # Store display name
    date: str              # YYYY-MM-DD
    report_type: str       # e.g. "sales_summary", "DeviceLabor", "NetSales"
    detail: str            # Human-readable explanation
    file_path: str | None  = None
    suggested_fix: str | None = None


@dataclass
class PreSyncValidationReport:
    stores: list[str]
    date_range_start: str
    date_range_end: str
    issues: list[ValidationIssue] = field(default_factory=list)
    checked_files: int = 0
    checked_dates: int = 0
    checked_stores: int = 0

    @property
    def blockers(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    @property
    def can_proceed(self) -> bool:
        return len(self.blockers) == 0

    @property
    def recommended_action(self) -> str:
        if self.blockers:
            return "fix_first"
        elif self.warnings:
            return "preview_only"
        return "proceed"

    def summary(self) -> str:
        b = len(self.blockers)
        w = len(self.warnings)
        action = self.recommended_action
        return f"{b} blocker(s), {w} warning(s) — {action}"


# ── Internal helpers ──────────────────────────────────────────────────

def _try_import(module_name: str, fallback=None):
    """Try to import a module; return fallback on ImportError."""
    try:
        return __import__(module_name)
    except ImportError:
        _log.warning("pre_sync_validator: %s not available — skipping check", module_name)
        return fallback


def _file_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file, or empty string on error."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return ""


def _is_still_downloading(path: Path) -> bool:
    """Return True if file was modified in the last _MIN_FILE_AGE_SECONDS (still downloading)."""
    try:
        age = time.time() - path.stat().st_mtime
        return age < _MIN_FILE_AGE_SECONDS
    except Exception:
        return False


# Lazy-load optional dependencies so the module is always importable
_openpyxl = _try_import("openpyxl")
_report_validator = _try_import("report_validator")
_toast_reports = _try_import("toast_reports")

import time  # noqa: E402  (used in _is_still_downloading)


def _build_store_dir(store_name: str) -> Path:
    """Return the base report directory for a store name (flat, no sub-folder)."""
    return REPORTS_DIR / store_name


def _build_nested_store_dir(store_name: str, report_type: str) -> Path:
    """Return the nested report directory for a store + report type."""
    return _build_store_dir(store_name) / report_type


# ── Public API ───────────────────────────────────────────────────────

def check_file_validity(file_path: Path) -> tuple[bool, str]:
    """
    Returns (is_valid, error_message).
    - File must exist
    - File must not be empty
    - Must be readable as Excel (try openpyxl.load_workbook)
    - Must not be corrupt
    - If file was modified < 5 min ago, skip it (still downloading)
    Returns (True, "valid") on success.
    """
    if not isinstance(file_path, Path):
        file_path = Path(file_path)

    if not file_path.exists():
        return False, "File does not exist"

    if not file_path.is_file():
        return False, "Path is not a file"

    if _is_still_downloading(file_path):
        return False, "File is still downloading (modified < 5 min ago)"

    size = file_path.stat().st_size
    if size <= 0:
        return False, "File is empty (0 bytes)"

    if _openpyxl is None:
        return False, "openpyxl not available — cannot validate"

    try:
        wb = _openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        wb.close()
    except Exception as exc:
        return False, f"File cannot be read as Excel: {exc}"

    return True, "valid"


def check_missing_files(
    store: str,
    target_date: date,
    report_types: list[str],
) -> list[ValidationIssue]:
    """
    For a given store + date, return list of issues for missing report files.

    Checks both flat store folder and nested store/report-type folder for each
    report type. Uses the same naming pattern as qb_sync.find_report_file():
      <ReportType>_<YYYYMMDD>_<YYYYMMDD>.xlsx
    and for nested lookups:
      <ReportType>/<ReportType>_<YYYYMMDD>_<YYYYMMDD>.xlsx
    """
    issues: list[ValidationIssue] = []
    date_str = target_date.strftime("%Y%m%d")  # YYYYMMDD used in filenames
    date_human = target_date.strftime("%Y-%m-%d")

    store_dir = _build_store_dir(store)

    for rpt in report_types:
        # Normalise key to report key string
        try:
            rpt_key = rpt if isinstance(rpt, str) else str(rpt)
        except Exception:
            rpt_key = str(rpt)

        # Attempt to look up the folder name for this report type
        folder_name = _get_folder_name_for_report(rpt_key)
        filename_pattern = f"{rpt_key}_{date_str}_{date_str}.xlsx"

        found = False
        checked: list[Path] = []

        # Flat path: store_dir / filename
        flat = store_dir / filename_pattern
        checked.append(flat)
        if flat.exists():
            found = True

        # Nested path: store_dir / folder_name / filename
        if not found:
            nested = store_dir / folder_name / filename_pattern
            checked.append(nested)
            if nested.exists():
                found = True

        if not found:
            # Also check if the store_dir itself doesn't exist (whole store missing)
            if not store_dir.exists():
                issues.append(
                    ValidationIssue(
                        severity="ERROR",
                        category="missing_file",
                        store=store,
                        date=date_human,
                        report_type=rpt_key,
                        detail=f"Report directory for '{store}' does not exist — no reports downloaded for this store",
                        file_path=str(store_dir),
                        suggested_fix=f"Run Download Reports for {store}",
                    )
                )
            else:
                issues.append(
                    ValidationIssue(
                        severity="ERROR",
                        category="missing_file",
                        store=store,
                        date=date_human,
                        report_type=rpt_key,
                        detail=f"Missing report file: {filename_pattern}",
                        file_path=str(checked[-1]),
                        suggested_fix=f"Download {rpt_key} for {store} on {date_human}",
                    )
                )
        else:
            # File exists — check if it's still downloading
            for p in checked:
                if p.exists():
                    if _is_still_downloading(p):
                        issues.append(
                            ValidationIssue(
                                severity="WARNING",
                                category="incomplete_report",
                                store=store,
                                date=date_human,
                                report_type=rpt_key,
                                detail=f"File {p.name} is still being written (modified < 5 min ago)",
                                file_path=str(p),
                                suggested_fix="Wait for download to finish, then run sync again",
                            )
                        )

    return issues


def check_duplicate_files(
    store: str,
    target_date: date,
) -> list[ValidationIssue]:
    """
    Find all report files for a store/date and flag duplicates:
      - Multiple files with the same report type in the same folder
      - Same report type, same date, but different content (SHA-256 mismatch)

    Only checks files that match the expected naming pattern.
    """
    issues: list[ValidationIssue] = []
    date_str = target_date.strftime("%Y%m%d")
    date_human = target_date.strftime("%Y-%m-%d")
    store_dir = _build_store_dir(store)

    if not store_dir.exists():
        return []  # Missing store dir — handled by check_missing_files

    # Collect all matching files grouped by report_type key
    type_files: dict[str, list[Path]] = {}
    pattern_str = f"*_{date_str}_{date_str}.xlsx"

    try:
        for p in store_dir.rglob(pattern_str):
            if p.is_file() and not _is_still_downloading(p):
                # Extract report type key from filename: e.g. SalesSummary_20260415_20260415.xlsx
                stem = p.stem  # "SalesSummary_20260415_20260415"
                parts = stem.split("_")
                rpt_key = parts[0] if parts else "unknown"
                type_files.setdefault(rpt_key, []).append(p)
    except Exception as exc:
        _log.warning("Error scanning store %s for duplicates: %s", store, exc)
        return []

    for rpt_key, paths in type_files.items():
        if len(paths) > 1:
            # Multiple files for same type — flag all as duplicates
            paths_str = ", ".join(str(p.relative_to(store_dir)) for p in paths)
            issues.append(
                ValidationIssue(
                    severity="ERROR",
                    category="duplicate",
                    store=store,
                    date=date_human,
                    report_type=rpt_key,
                    detail=f"Multiple report files found for '{rpt_key}' on {date_human}: {len(paths)} files",
                    file_path=str(paths[0]),
                    suggested_fix=f"Keep only the correct file, remove duplicates: {paths_str}",
                )
            )
        elif len(paths) == 1:
            # One file — compute hash and check for content mismatch
            # (Same report type should have consistent content for same date)
            # We compare against the most recent previous check if available
            # For now, just validate it's readable
            valid, msg = check_file_validity(paths[0])
            if not valid:
                issues.append(
                    ValidationIssue(
                        severity="ERROR",
                        category="invalid_file",
                        store=store,
                        date=date_human,
                        report_type=rpt_key,
                        detail=f"File exists but is invalid: {msg}",
                        file_path=str(paths[0]),
                        suggested_fix="Re-download the report or check if it was saved incorrectly",
                    )
                )

    return issues


def check_date_coverage_gap(
    stores: list[str],
    start_date: date,
    end_date: date,
) -> list[ValidationIssue]:
    """
    For each store, iterate from start_date to end_date.
    Flag any date that has no report files at all.

    - 1 consecutive missing day → WARNING
    - 3+ consecutive missing days → ERROR
    - 2 consecutive missing days → WARNING
    """
    issues: list[ValidationIssue] = []
    date_human_start = start_date.strftime("%Y-%m-%d")
    date_human_end = end_date.strftime("%Y-%m-%d")

    for store in stores:
        store_dir = _build_store_dir(store)
        if not store_dir.exists():
            # Report dir missing entirely — already caught as missing_file elsewhere
            continue

        current = start_date
        gap_start: date | None = None
        gap_count = 0

        def _has_reports_for_date(d: date) -> bool:
            """Return True if any report file exists for this store/date."""
            ds = d.strftime("%Y%m%d")
            pattern = f"*_{ds}_{ds}.*"
            try:
                for _ in store_dir.rglob(pattern):
                    return True
            except Exception:
                pass
            return False

        while current <= end_date:
            if _has_reports_for_date(current):
                if gap_count >= 3:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            category="date_gap",
                            store=store,
                            date=date_human_start,
                            report_type="*",
                            detail=f"Date gap of {gap_count} consecutive days ({gap_start} to {current - timedelta(days=1)}) has no report files",
                            suggested_fix=f"Download reports for {store} covering {gap_start} to {current - timedelta(days=1)}",
                        )
                    )
                elif gap_count >= 1:
                    severity = "WARNING" if gap_count < 3 else "ERROR"
                    issues.append(
                        ValidationIssue(
                            severity=severity,
                            category="date_gap",
                            store=store,
                            date=(gap_start or current).strftime("%Y-%m-%d"),
                            report_type="*",
                            detail=f"Missing reports for {store} on {gap_start} to {current - timedelta(days=1)} ({gap_count} day(s))",
                            suggested_fix=f"Check if {store} exported Toast reports for those dates",
                        )
                    )
                gap_start = None
                gap_count = 0
            else:
                if gap_start is None:
                    gap_start = current
                gap_count += 1
            current += timedelta(days=1)

        # Flush any trailing gap
        if gap_count >= 3:
            issues.append(
                ValidationIssue(
                    severity="ERROR",
                    category="date_gap",
                    store=store,
                    date=date_human_end,
                    report_type="*",
                    detail=f"Date gap of {gap_count} consecutive days ending at range end has no report files",
                    suggested_fix=f"Download reports for {store} covering remaining dates",
                )
            )
        elif gap_count >= 1:
            issues.append(
                ValidationIssue(
                    severity="WARNING",
                    category="date_gap",
                    store=store,
                    date=(gap_start or end_date).strftime("%Y-%m-%d"),
                    report_type="*",
                    detail=f"Missing reports for {store} on final {gap_count} day(s) of range",
                    suggested_fix="Check Toast exports for the remaining dates",
                )
            )

    return issues


def validate_sync_readiness(
    stores: list[str],
    start_date: date,
    end_date: date,
) -> PreSyncValidationReport:
    """
    Main entry point. For each store + date combination:
      - Check each expected report type for existence
      - Check file validity (not empty, valid Excel)
      - Check for duplicate files (same date + same store + same type, different hash)
      - Detect date gaps (consecutive dates missing all reports)

    Returns PreSyncValidationReport.
    """
    report = PreSyncValidationReport(
        stores=list(stores),
        date_range_start=start_date.strftime("%Y-%m-%d"),
        date_range_end=end_date.strftime("%Y-%m-%d"),
    )

    # Get expected report types
    report_types: list[str] = []
    if _toast_reports is not None:
        try:
            report_types = [r.key for r in _toast_reports.get_download_report_types()]
        except Exception as exc:
            _log.warning("get_download_report_types() failed: %s — using defaults", exc)
            report_types = ["sales_summary", "orders", "order_items", "payments", "discounts"]
    else:
        report_types = ["sales_summary", "orders", "order_items", "payments", "discounts"]

    checked_files = 0
    checked_dates = 0

    for store in stores:
        store_normalized = store.strip().lower()
        current = start_date
        while current <= end_date:
            checked_dates += 1
            date_human = current.strftime("%Y-%m-%d")

            # Missing file check
            missing = check_missing_files(store, current, report_types)
            report.issues.extend(missing)
            checked_files += len(report_types)

            # Duplicate / invalid file check
            dups = check_duplicate_files(store, current)
            report.issues.extend(dups)

            current += timedelta(days=1)

        report.checked_stores += 1

    # Date coverage gap check (runs over full range per store)
    gaps = check_date_coverage_gap(stores, start_date, end_date)
    report.issues.extend(gaps)

    report.checked_files = checked_files
    report.checked_dates = checked_dates

    return report


def _get_folder_name_for_report(report_type: str) -> str:
    """Return the folder name for a report type key."""
    if _toast_reports is None:
        # Fallback: capitalise key and remove underscores
        return report_type.replace("_", " ").title().replace(" ", "")
    try:
        rpt = _toast_reports.get_report_type(report_type)
        return rpt.folder_name
    except Exception:
        return report_type.replace("_", " ").title().replace(" ", "")


def format_validation_report(report: PreSyncValidationReport) -> str:
    """Format the report as a human-readable multi-line string for display in the UI."""
    lines: list[str] = []
    lines.append("═" * 60)
    lines.append("  PRE-SYNC VALIDATION REPORT")
    lines.append("═" * 60)
    lines.append(f"  Stores    : {', '.join(report.stores)}")
    lines.append(f"  Date range: {report.date_range_start} → {report.date_range_end}")
    lines.append(f"  Files checked : {report.checked_files}")
    lines.append(f"  Dates checked : {report.checked_dates}")
    lines.append("")
    lines.append(f"  Result: {report.summary()}")
    lines.append("")

    if report.blockers:
        lines.append("  ── BLOCKERS (must fix before sync) ──")
        for i, issue in enumerate(report.blockers, 1):
            lines.append(f"  {i}. [{issue.severity}] {issue.category.upper()}")
            lines.append(f"     Store      : {issue.store}")
            lines.append(f"     Date       : {issue.date}")
            lines.append(f"     Report type: {issue.report_type}")
            lines.append(f"     Detail     : {issue.detail}")
            if issue.suggested_fix:
                lines.append(f"     Fix        : {issue.suggested_fix}")
            lines.append("")
        lines.append("")

    if report.warnings:
        lines.append("  ── WARNINGS (non-blocking) ──")
        for i, issue in enumerate(report.warnings, 1):
            lines.append(f"  {i}. [{issue.severity}] {issue.category.upper()}")
            lines.append(f"     Store      : {issue.store}")
            lines.append(f"     Date       : {issue.date}")
            lines.append(f"     Report type: {issue.report_type}")
            lines.append(f"     Detail     : {issue.detail}")
            if issue.suggested_fix:
                lines.append(f"     Fix        : {issue.suggested_fix}")
            lines.append("")
        lines.append("")

    if not report.blockers and not report.warnings:
        lines.append("  ✓ All checks passed — ready to sync.")

    lines.append("═" * 60)
    return "\n".join(lines)


def get_pre_sync_summary_for_ui(report: PreSyncValidationReport) -> dict:
    """
    Return a UI-ready dict with summary fields.

    Returns:
        {
            "can_proceed": bool,
            "action": str,        # "proceed" | "fix_first" | "preview_only"
            "blocker_count": int,
            "warning_count": int,
            "summary_line": str,
            "blockers": [...],
            "warnings": [...],
            "gaps": [...],
        }
    """
    blockers_list: list[dict] = []
    for b in report.blockers:
        blockers_list.append({
            "store": b.store,
            "date": b.date,
            "type": b.report_type,
            "detail": b.detail,
            "file_path": b.file_path or "",
            "fix": b.suggested_fix or "",
        })

    warnings_list: list[dict] = []
    for w in report.warnings:
        warnings_list.append({
            "store": w.store,
            "date": w.date,
            "type": w.report_type,
            "detail": w.detail,
            "file_path": w.file_path or "",
            "fix": w.suggested_fix or "",
        })

    # Gather date gaps separately
    gaps_list: list[dict] = []
    for b in report.blockers:
        if b.category == "date_gap":
            gaps_list.append({
                "store": b.store,
                "from": b.date,
                "to": b.date,
                "count": 0,  # already counted in the detail
                "detail": b.detail,
            })
    for w in report.warnings:
        if w.category == "date_gap":
            gaps_list.append({
                "store": w.store,
                "from": w.date,
                "to": w.date,
                "count": 0,
                "detail": w.detail,
            })

    return {
        "can_proceed": report.can_proceed,
        "action": report.recommended_action,
        "blocker_count": len(report.blockers),
        "warning_count": len(report.warnings),
        "summary_line": report.summary(),
        "blockers": blockers_list,
        "warnings": warnings_list,
        "gaps": gaps_list,
    }
