"""
Google Drive Toast Report Coverage Validator

Validates report coverage for (store, report_type, date_range) triples.
Detects: missing reports, duplicate files, invalid filenames.
Stores findings in SQLite for UI consumption.

Validation profiles:
  - "strict": fail on any missing/duplicate/invalid (QB Sync guard)
  - "warning": collect all findings but don't block (UI display)
  - "summary": only high-level health, no per-date breakdown

Usage:
    validator = ReportCoverageValidator(drive_service, on_log=log_fn)
    result = validator.validate_store(store_name="Stockton", report_keys=["sales_summary"])

    result = validator.validate_month(store_name="WA3", year=2026, month=3)
    result = validator.validate_range(store_name="WA3", start="2026-03-01", end="2026-03-31")

    findings = validator.get_all_findings()          # unsaved in-memory
    validator.save_findings()                       # write to SQLite
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from app_paths import runtime_path
from date_parser import parse_date_flexible, ParseResult
from report_inventory import INVENTORY_DB_PATH, STORE_TIMEZONES, KNOWN_STORE_NAMES
from toast_reports import (
    DEFAULT_REPORT_TYPE_KEYS,
    get_report_type,
    infer_report_type,
    normalize_report_types,
)

logger = logging.getLogger(__name__)

# ---- Re-export for convenience ----
from report_inventory import (
    extract_business_dates_from_name,
)

VALIDATION_DB_PATH = runtime_path("validation-results.db")

# ---- Filename validation ----

STANDARD_STEM_RE = re.compile(
    r"^(SalesSummary|OrderDetails|OrderItems|PaymentDetails|"
    r"CashActivityAudit|VoidedOrders|Discounts|ModifierSelections|"
    r"ProductMix|MenuItems|TimeEntries|LaborSummary|AccountingExport|"
    r"MenuExport|KitchenDetails|SalesOrders)[_-]?"
    r"(20\d{2})[_-]?(\d{2})[_-]?(\d{2})"
    r"(?:_[A-Za-z0-9]+)?$",
    re.IGNORECASE,
)

VALID_EXTENSIONS = {".xlsx", ".csv", ".xls", ".xlsb", ".xlsm"}
MIN_FILE_SIZE_BYTES = 500  # Toast reports are rarely < 500 bytes


@dataclass
class ValidationFinding:
    """A single validation issue found during coverage validation."""
    severity: str           # "error" | "warning" | "info"
    category: str          # "missing" | "duplicate" | "invalid_filename" | "wrong_report_type"
    store: str
    report_key: str
    report_label: str
    business_date: str      # YYYY-MM-DD
    filename: Optional[str]
    filepath: Optional[str]
    detail: str
    drive_file_id: Optional[str] = None


@dataclass
class DateValidationResult:
    """Result of validating one date's report coverage."""
    store: str
    report_key: str
    report_label: str
    business_date: str
    status: str             # "found" | "missing" | "duplicate" | "invalid"
    files: list[dict] = field(default_factory=list)   # list of drive file dicts
    finding: Optional[ValidationFinding] = None


@dataclass
class StoreValidationResult:
    """Result of validating one store's coverage for one report type over a date range."""
    store: str
    report_key: str
    report_label: str
    start_date: str
    end_date: str
    date_count: int
    found_count: int = 0
    missing_count: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    date_results: list[DateValidationResult] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)
    health: str = "unknown"   # "ready" | "partial" | "missing" | "invalid"

    @property
    def coverage_pct(self) -> float:
        if self.date_count == 0:
            return 0.0
        return round(self.found_count / self.date_count * 100, 1)


class ValidationProfile(str, Enum):
    STRICT = "strict"   # fail fast, collect nothing beyond failures
    WARNING = "warning"  # collect everything, never block
    SUMMARY = "summary"  # only high-level health, skip per-date details


# ---- Core validator ----

class ReportCoverageValidator:
    """
    Validates Google Drive report coverage for Toast stores.

    Parameters
    ----------
    drive_service : GDriveService
        Authenticated GDriveService instance. Its `list_reports()` is used
        to enumerate files for each (store, report_type) pair.
    on_log : callable, optional
        Logging callback (receives a string).
    """

    def __init__(self, drive_service, on_log=None):
        self.drive = drive_service
        self._log = on_log or (lambda msg: None)
        self._findings: list[ValidationFinding] = []
        self._profile = ValidationProfile.WARNING

    def set_profile(self, profile: ValidationProfile | str) -> None:
        self._profile = ValidationProfile(profile)

    # ---- Public API ----

    def validate_month(
        self,
        store_name: str,
        year: int,
        month: int,
        report_keys: list[str] | tuple[str, ...] | None = None,
    ) -> list[StoreValidationResult]:
        """Validate one calendar month for a store."""
        start = f"{year:04d}-{month:02d}-01"
        last_day = date(year, month, 1)
        if month == 12:
            end_year, end_month = year + 1, 1
        else:
            end_year, end_month = year, month + 1
        end = date(end_year, end_month, 1) - timedelta(days=1)
        return self.validate_range(
            store_name=store_name,
            start=start,
            end=end.strftime("%Y-%m-%d"),
            report_keys=report_keys,
        )

    def validate_range(
        self,
        store_name: str,
        start: str,
        end: str,
        report_keys: list[str] | tuple[str, ...] | None = None,
    ) -> list[StoreValidationResult]:
        """Validate a date range for a store across one or more report types."""
        start_result = parse_date_flexible(start, "validation_range")
        end_result = parse_date_flexible(end, "validation_range")
        if not start_result.success or not end_result.success:
            self._log(f"[Validator] Invalid date range: {start} → {end}")
            return []

        start_dt = datetime.strptime(start_result.value.date_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_result.value.date_str, "%Y-%m-%d")
        if start_dt > end_dt:
            self._log(f"[Validator] Start > End: {start} → {end}")
            return []

        reports = normalize_report_types(report_keys or list(DEFAULT_REPORT_TYPE_KEYS))
        results: list[StoreValidationResult] = []
        for report in reports:
            result = self._validate_store_report_range(
                store_name=store_name,
                report=report,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            results.append(result)
        return results

    def validate_store(
        self,
        store_name: str,
        lookback_days: int = 30,
        report_keys: list[str] | tuple[str, ...] | None = None,
    ) -> list[StoreValidationResult]:
        """Validate the last N days for a store."""
        end = date.today()
        start = end - timedelta(days=lookback_days - 1)
        return self.validate_range(
            store_name=store_name,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            report_keys=report_keys,
        )

    # ---- Finding accessors ----

    def get_findings(
        self,
        store: str | None = None,
        report_key: str | None = None,
        severity: str | None = None,
    ) -> list[ValidationFinding]:
        """Return all accumulated findings, optionally filtered."""
        results = self._findings
        if store:
            results = [f for f in results if f.store == store]
        if report_key:
            results = [f for f in results if f.report_key == report_key]
        if severity:
            results = [f for f in results if f.severity == severity]
        return results

    def clear_findings(self) -> None:
        self._findings.clear()

    # ---- SQLite persistence ----

    def save_findings(self, run_id: str | None = None) -> str:
        """
        Persist accumulated findings to SQLite.

        Returns the run_id of the inserted row set.
        """
        run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        INVENTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(INVENTORY_DB_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS validation_finding_records (
                    run_id      TEXT    NOT NULL,
                    severity    TEXT    NOT NULL,
                    category    TEXT    NOT NULL,
                    store       TEXT    NOT NULL,
                    report_key  TEXT    NOT NULL,
                    report_label TEXT   NOT NULL,
                    business_date TEXT  NOT NULL,
                    filename    TEXT,
                    filepath    TEXT,
                    detail      TEXT    NOT NULL,
                    drive_file_id TEXT,
                    saved_at    TEXT    NOT NULL
                )
                """
            )
            conn.execute(
                "DELETE FROM validation_finding_records WHERE run_id = ?",
                (run_id,),
            )
            saved_at = datetime.now().isoformat()
            conn.executemany(
                """
                INSERT INTO validation_finding_records
                (run_id, severity, category, store, report_key, report_label,
                 business_date, filename, filepath, detail, drive_file_id, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        f.severity,
                        f.category,
                        f.store,
                        f.report_key,
                        f.report_label,
                        f.business_date,
                        f.filename,
                        f.filepath,
                        f.detail,
                        f.drive_file_id,
                        saved_at,
                    )
                    for f in self._findings
                ],
            )
            conn.commit()
        self._log(f"[Validator] Saved {len(self._findings)} findings (run_id={run_id})")
        return run_id

    def load_findings(self, run_id: str) -> list[ValidationFinding]:
        """Reload findings from SQLite by run_id."""
        rows = []
        with sqlite3.connect(INVENTORY_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM validation_finding_records WHERE run_id = ? ORDER BY business_date",
                (run_id,),
            ).fetchall()
        findings = [
            ValidationFinding(
                severity=row["severity"],
                category=row["category"],
                store=row["store"],
                report_key=row["report_key"],
                report_label=row["report_label"],
                business_date=row["business_date"],
                filename=row["filename"],
                filepath=row["filepath"],
                detail=row["detail"],
                drive_file_id=row["drive_file_id"],
            )
            for row in rows
        ]
        self._findings = findings
        return findings

    # ---- Private helpers ----

    def _validate_store_report_range(
        self,
        store_name: str,
        report,
        start_dt: datetime,
        end_dt: datetime,
    ) -> StoreValidationResult:
        """Validate one (store, report_type) over a date range."""
        result = StoreValidationResult(
            store=store_name,
            report_key=report.key,
            report_label=report.label,
            start_date=start_dt.strftime("%Y-%m-%d"),
            end_date=end_dt.strftime("%Y-%m-%d"),
            date_count=(end_dt - start_dt).days + 1,
        )

        # Pull all files from Drive for this store/report
        try:
            drive_files = self.drive.list_reports(store_name=store_name, report_type=report.key)
        except Exception as e:
            self._log(f"[Validator] Error listing Drive reports for {store_name}/{report.key}: {e}")
            result.health = "error"
            return result

        # Index files by their business dates (from filename)
        files_by_date: dict[str, list[dict]] = {}
        for f in drive_files:
            fname = f.get("name", "")
            inferred = infer_report_type(filename=fname)
            if inferred.key != report.key:
                continue
            dates = extract_business_dates_from_name(fname)
            for d in dates:
                files_by_date.setdefault(d, []).append(f)

        # Walk each date in range
        cursor = start_dt
        while cursor <= end_dt:
            date_str = cursor.strftime("%Y-%m-%d")
            date_files = files_by_date.get(date_str, [])
            date_result = self._validate_single_date(
                store=store_name,
                report=report,
                business_date=date_str,
                files=date_files,
            )
            result.date_results.append(date_result)
            if date_result.status == "found":
                result.found_count += 1
            elif date_result.status == "missing":
                result.missing_count += 1
            elif date_result.status == "duplicate":
                result.duplicate_count += 1
            else:  # invalid
                result.invalid_count += 1
            cursor += timedelta(days=1)

        result.findings = [dr.finding for dr in result.date_results if dr.finding is not None]
        self._findings.extend(result.findings)

        # Compute health
        if result.missing_count == 0 and result.duplicate_count == 0 and result.invalid_count == 0:
            result.health = "ready"
        elif result.found_count > 0:
            result.health = "partial"
        else:
            result.health = "missing"

        return result

    def _validate_single_date(
        self,
        store: str,
        report,
        business_date: str,
        files: list[dict],
    ) -> DateValidationResult:
        """Validate coverage for one date, one report type."""
        dr = DateValidationResult(
            store=store,
            report_key=report.key,
            report_label=report.label,
            business_date=business_date,
            status="found",
            files=files,
        )

        if not files:
            dr.status = "missing"
            dr.finding = ValidationFinding(
                severity="error",
                category="missing",
                store=store,
                report_key=report.key,
                report_label=report.label,
                business_date=business_date,
                filename=None,
                filepath=None,
                detail=f"Missing {report.label} report for {business_date}",
            )
            return dr

        if len(files) > 1:
            filenames = [f.get("name", "?") for f in files]
            dr.status = "duplicate"
            dr.finding = ValidationFinding(
                severity="warning",
                category="duplicate",
                store=store,
                report_key=report.key,
                report_label=report.label,
                business_date=business_date,
                filename=filenames[0],
                filepath=None,
                detail=f"Duplicate: {len(files)} files found — {filenames!r}",
                drive_file_id=files[0].get("id"),
            )

        # Validate first/primary file
        primary = files[0]
        fname = primary.get("name", "")
        finding = self._check_file_validity(store, report, business_date, fname, primary)
        if finding:
            dr.status = "invalid"
            dr.finding = finding
            return dr

        return dr

    def _check_file_validity(
        self,
        store: str,
        report,
        business_date: str,
        filename: str,
        file_info: dict,
    ) -> ValidationFinding | None:
        """Check one file for invalid filename, wrong extension, or suspiciously small size."""
        stem = Path(filename).stem
        ext = Path(filename).suffix.lower()

        # Extension check
        if ext not in VALID_EXTENSIONS:
            return ValidationFinding(
                severity="warning",
                category="invalid_filename",
                store=store,
                report_key=report.key,
                report_label=report.label,
                business_date=business_date,
                filename=filename,
                filepath=file_info.get("name"),
                detail=f"Unusual file extension: '{ext}' (expected one of {VALID_EXTENSIONS})",
                drive_file_id=file_info.get("id"),
            )

        # Size check
        size = file_info.get("size")
        if size is not None and int(size) < MIN_FILE_SIZE_BYTES:
            return ValidationFinding(
                severity="warning",
                category="invalid_filename",
                store=store,
                report_key=report.key,
                report_label=report.label,
                business_date=business_date,
                filename=filename,
                filepath=file_info.get("name"),
                detail=f"File suspiciously small: {size} bytes (< {MIN_FILE_SIZE_BYTES} minimum)",
                drive_file_id=file_info.get("id"),
            )

        return None

    # ---- Convenience: quick full-store scan ----

    def scan_all_stores(
        self,
        lookback_days: int = 30,
        report_keys: list[str] | tuple[str, ...] | None = None,
        on_progress=None,
    ) -> dict[str, list[StoreValidationResult]]:
        """
        Scan all KNOWN_STORE_NAMES and return results keyed by store name.

        Parameters
        ----------
        on_progress : callable, optional
            Called with (store, current, total) for progress updates.
        """
        all_results: dict[str, list[StoreValidationResult]] = {}
        total = len(KNOWN_STORE_NAMES)
        for idx, store in enumerate(KNOWN_STORE_NAMES, 1):
            if on_progress:
                on_progress(store, idx, total)
            results = self.validate_store(
                store_name=store,
                lookback_days=lookback_days,
                report_keys=report_keys,
            )
            all_results[store] = results
        return all_results
