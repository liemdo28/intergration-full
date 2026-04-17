"""
Source Completeness Gate

Checks that ALL required source files exist in Google Drive before
QB sync is allowed to write to QuickBooks Desktop.

This is a hard gate — sync is blocked if any required file is missing.
Partial sync is never allowed because it creates accounting gaps.

Used by:
  - QBSyncWizard (step 4: Preview) — gates the "Confirm & Sync" action
  - preflight_validation_service — included in readiness checks
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta


@dataclass
class SourceFile:
    store: str
    date: str           # YYYY-MM-DD
    report_type: str    # e.g. "sales_summary"
    found: bool
    file_name: str = ""
    drive_path: str = ""
    error: str = ""

    @property
    def label(self) -> str:
        return f"{self.store} / {self.date}"


@dataclass
class CompletenessReport:
    stores: list = field(default_factory=list)
    date_start: str = ""
    date_end: str = ""
    report_types: list = field(default_factory=list)
    files: list = field(default_factory=list)
    drive_available: bool = True
    drive_error: str = ""

    @property
    def found_count(self) -> int:
        return sum(1 for f in self.files if f.found)

    @property
    def missing_count(self) -> int:
        return sum(1 for f in self.files if not f.found)

    @property
    def total_count(self) -> int:
        return len(self.files)

    @property
    def is_complete(self) -> bool:
        """True only if ALL required files are found. False if drive unavailable."""
        if not self.drive_available:
            return False
        return self.missing_count == 0 and self.total_count > 0

    @property
    def missing_files(self) -> list:
        return [f for f in self.files if not f.found]

    def summary_text(self) -> str:
        if not self.drive_available:
            return f"Google Drive is not connected — cannot verify source files. {self.drive_error}"
        if self.is_complete:
            return f"All {self.total_count} required report(s) found in Drive."
        return (
            f"{self.missing_count} of {self.total_count} required report(s) are missing from Drive. "
            f"Download them before syncing to QuickBooks."
        )


def _get_date_list(date_start: str, date_end: str) -> list:
    try:
        s = datetime.strptime(date_start, "%Y-%m-%d").date()
        e = datetime.strptime(date_end, "%Y-%m-%d").date()
        result = []
        cur = s
        while cur <= e:
            result.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return result
    except Exception:
        return []


def check_source_completeness(
    stores: list,
    date_start: str,
    date_end: str,
    report_types: list = None,
) -> CompletenessReport:
    """
    Check that all required source files exist in Google Drive.

    Returns CompletenessReport. Never raises.

    If Drive is unavailable, report.drive_available = False and is_complete = False.
    This means sync will be blocked if Drive is not connected — by design.
    """
    report_types = report_types or ["sales_summary"]
    report = CompletenessReport(
        stores=stores,
        date_start=date_start,
        date_end=date_end,
        report_types=report_types,
    )

    if not stores or not date_start or not date_end:
        report.drive_available = False
        report.drive_error = "Invalid parameters."
        return report

    date_list = _get_date_list(date_start, date_end)
    if not date_list:
        report.drive_available = False
        report.drive_error = "Invalid date range."
        return report

    # Try to connect to Drive
    try:
        from gdrive_service import GDriveService
        drive = GDriveService()
    except Exception as exc:
        report.drive_available = False
        report.drive_error = f"Google Drive is not connected: {exc}"
        # Still build the file list as "unknown" — mark all as not found
        for store in stores:
            for d in date_list:
                for rtype in report_types:
                    report.files.append(SourceFile(
                        store=store, date=d, report_type=rtype,
                        found=False, error="Drive not connected"
                    ))
        return report

    # Check each file
    for store in stores:
        for d in date_list:
            for rtype in report_types:
                sf = SourceFile(store=store, date=d, report_type=rtype, found=False)
                try:
                    dt = datetime.strptime(d, "%Y-%m-%d")
                    date_str = dt.strftime("%m/%d/%Y")
                    file_info = drive.find_report_file(store, rtype, date_str)
                    if file_info:
                        sf.found = True
                        sf.file_name = file_info.get("name", "")
                        sf.drive_path = file_info.get("id", "")
                    # if None returned, found=False (missing)
                except Exception as exc:
                    sf.found = False
                    sf.error = str(exc)
                report.files.append(sf)

    return report


def gate_sync_or_raise(stores: list, date_start: str, date_end: str) -> None:
    """
    Hard gate: raises ValueError with client-safe message if source files are incomplete.
    Call this before any QB write operation.
    """
    result = check_source_completeness(stores, date_start, date_end)
    if not result.is_complete:
        raise ValueError(result.summary_text())
