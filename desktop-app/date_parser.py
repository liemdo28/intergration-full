"""
Unified date parsing cho tất cả nền tảng.
Hỗ trợ: Toast (filename), Uber/Doordash/Grubhub (pivot CSV), UI input.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# ===== Supported input formats =====
DATE_FORMATS = [
    "%m/%d/%Y",   # 03/28/2026
    "%Y-%m-%d",   # 2026-03-28
    "%m-%d-%Y",   # 03-28-2026
    "%d/%m/%Y",   # 28/03/2026
    "%b %d, %Y",  # Mar 28, 2026
    "%B %d, %Y",  # March 28, 2026
]

SPECIAL_DATES = {"today", "yesterday"}

MARKETPLACE_DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y"]

# ===== Dataclasses =====


@dataclass
class ParsedDate:
    """Parsed date result with full metadata."""
    date_str: str          # ISO format YYYY-MM-DD
    original: str          # Raw input
    source: str            # "filename" | "pivot_csv" | "ui_input" | "marketplace"
    line_info: Optional[str] = None  # "Row 5, Col A" for CSV errors


@dataclass
class DateRange:
    """Date range parsed from Toast filename."""
    start: ParsedDate
    end: ParsedDate


@dataclass
class ParseResult:
    """Unified result from any date parsing operation."""
    success: bool
    value: Optional[Union[ParsedDate, DateRange]] = None
    error: Optional[str] = None
    source: str = ""
    line_info: Optional[str] = None  # For CSV row/col error context


# ===== CORE: parse ISO YYYY-MM-DD with validation =====


def parse_iso_date(date_str: str) -> Optional[str]:
    """Parse and validate ISO date string. Returns None if invalid."""
    if not date_str:
        return None
    try:
        datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return date_str.strip()
    except ValueError:
        return None


# ===== CORE: parse flexible date string =====


def parse_date_flexible(date_str: str, source: str = "unknown") -> ParseResult:
    """
    Parse date from any format. Returns ParseResult with detailed info.

    Handles:
    - ISO format: 2026-03-28
    - US format: 03/28/2026
    - Dash format: 03-28-2026
    - EU format: 28/03/2026
    - Month name: Mar 28, 2026 / March 28, 2026
    - Special: today, yesterday
    """
    if not date_str:
        return ParseResult(success=False, error="Empty date string", source=source)

    original = date_str.strip()

    # Special dates
    if original.lower() in SPECIAL_DATES:
        if original.lower() == "yesterday":
            return ParseResult(
                success=True,
                value=ParsedDate(
                    date_str=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                    original=original,
                    source=source,
                ),
                source=source,
            )
        elif original.lower() == "today":
            return ParseResult(
                success=True,
                value=ParsedDate(
                    date_str=datetime.now().strftime("%Y-%m-%d"),
                    original=original,
                    source=source,
                ),
                source=source,
            )

    # Try ISO first (most common in system)
    iso = parse_iso_date(original)
    if iso:
        return ParseResult(
            success=True,
            value=ParsedDate(date_str=iso, original=original, source=source),
            source=source,
        )

    # Try other formats
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(original, fmt).strftime("%Y-%m-%d")
            return ParseResult(
                success=True,
                value=ParsedDate(date_str=parsed, original=original, source=source),
                source=source,
            )
        except ValueError:
            continue

    return ParseResult(
        success=False,
        error=f"Unrecognized date format: '{original}'",
        source=source,
    )


# ===== TOAST: Parse date from filename =====

# Patterns tried in order of specificity
TOAST_FILENAME_PATTERNS = [
    # SalesSummary_2026-03-28_2026-03-28.xlsx (current Toast format)
    re.compile(r"SalesSummary[_-](\d{4}-\d{2}-\d{2})[_-](\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    # Toast_2024-01-15_to_2024-01-21.xlsx (alternative format)
    re.compile(r"Toast[_-](\d{4}-\d{2}-\d{2})[_-]to[_-](\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    # Toast_20260328.xlsx (compact, single date)
    re.compile(r"Toast[_-](\d{8})", re.IGNORECASE),
    # SalesSummary_20260328.xlsx (compact, single date)
    re.compile(r"SalesSummary[_-](\d{8})", re.IGNORECASE),
]


def parse_toast_filename(filepath: str | Path) -> ParseResult:
    """
    Parse date range from Toast report filename.

    Supports multiple filename patterns:
    - SalesSummary_2026-03-28_2026-03-28.xlsx
    - Toast_2024-01-15_to_2024-01-21.xlsx
    - SalesSummary_20260328.xlsx (compact)

    Returns ParseResult with DateRange (for two dates) or ParsedDate (for single).
    """
    filename = Path(filepath).name

    for pattern in TOAST_FILENAME_PATTERNS:
        match = pattern.search(filename)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                # Date range: start and end
                start_result = parse_date_flexible(groups[0], "filename")
                end_result = parse_date_flexible(groups[1], "filename")
                if start_result.success and end_result.success:
                    return ParseResult(
                        success=True,
                        value=DateRange(
                            start=start_result.value,
                            end=end_result.value,
                        ),
                        source="filename",
                    )
            elif len(groups) == 1:
                # Single date (compact format: YYYYMMDD)
                compact_date = groups[0]
                if len(compact_date) == 8 and compact_date.isdigit():
                    formatted = f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:8]}"
                    iso = parse_iso_date(formatted)
                    if iso:
                        return ParseResult(
                            success=True,
                            value=ParsedDate(
                                date_str=formatted,
                                original=filename,
                                source="filename",
                            ),
                            source="filename",
                        )

    return ParseResult(
        success=False,
        error=f"Cannot parse date from filename: '{filename}'",
        source="filename",
    )


def validate_toast_date_format(month: int, day: int, year: int) -> tuple[bool, str]:
    """Validate and format date components for Toast date picker."""
    try:
        date(year, month, day)
        return True, f"{month:02d}{day:02d}{year}"
    except ValueError as e:
        return False, str(e)


def parse_ui_date_to_toast(date_str: str) -> tuple[bool, str]:
    """
    Parse YYYY-MM-DD UI input and return Toast-compatible MMDDYYYY string.

    This replaces the buggy: date_str.replace("/", "")
    which would produce "352026" (6 digits!) for "3/5/2026".

    Returns: (success, result_or_error_message)
    """
    result = parse_date_flexible(date_str, "ui_to_toast")
    if not result.success:
        return False, result.error

    d = result.value.date_str  # ISO format YYYY-MM-DD
    year, month, day = d.split("-")
    return validate_toast_date_format(int(month), int(day), int(year))


# ===== MARKETPLACE CSV: Parse date from Row Labels column =====


def normalize_marketplace_date(value: str, row_num: int = None) -> ParseResult:
    """
    Parse date from marketplace pivot CSV Row Labels column.

    FIX: Previously returned None silently on unparseable dates (Bug C1).
    Now returns ParseResult with detailed error information.

    Args:
        value: Cell value from "Row Labels" column
        row_num: Optional row number for error reporting

    Returns:
        ParseResult with ParsedDate on success, or error details on failure.
    """
    text = (value or "").strip()

    line_info = f"Row {row_num}" if row_num else None

    if not text:
        return ParseResult(
            success=False,
            error="Empty date cell",
            source="marketplace",
            line_info=line_info,
        )

    if text.lower() == "grand total":
        return ParseResult(
            success=False,
            error="Grand Total row (not a date, skipping)",
            source="marketplace",
            line_info=line_info,
        )

    for fmt in MARKETPLACE_DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            return ParseResult(
                success=True,
                value=ParsedDate(
                    date_str=parsed,
                    original=text,
                    source="marketplace",
                    line_info=line_info,
                ),
                source="marketplace",
            )
        except ValueError:
            continue

    logger.warning(f"Unrecognized date format in marketplace CSV: '{text}' (row {row_num})")
    return ParseResult(
        success=False,
        error=f"Unrecognized date format in Row Labels: '{text}'",
        source="marketplace",
        line_info=line_info,
    )


# ===== UTILITY: Parse comma-separated date list =====


def parse_date_list(
    date_str: str, source: str = "ui_input"
) -> tuple[list[ParseResult], list[ParseResult]]:
    """
    Parse comma-separated date list (e.g., "2026-03-15, 2026-03-16, yesterday").

    Returns:
        (successes, failures) - lists of ParseResult objects
    """
    successes = []
    failures = []

    for part in date_str.split(","):
        part = part.strip()
        if not part:
            continue
        result = parse_date_flexible(part, source)
        if result.success:
            successes.append(result)
        else:
            failures.append(result)

    return successes, failures


# ===== UTILITY: Date range for QB Sync tab =====


def get_date_range_from_inputs(
    start_str: str, end_str: str
) -> tuple[bool, list[str], str]:
    """
    Parse start/end date strings and return list of all dates in range.

    Returns:
        (success, date_list, error_message)
        If success=False, date_list is empty and error_message explains why.
    """
    if not start_str or not end_str:
        return False, [], "Please enter Start Date and End Date"

    start_result = parse_date_flexible(start_str, "ui_range")
    end_result = parse_date_flexible(end_str, "ui_range")

    if not start_result.success:
        return False, [], f"Invalid Start Date: {start_result.error}"
    if not end_result.success:
        return False, [], f"Invalid End Date: {end_result.error}"

    start_iso = start_result.value.date_str
    end_iso = end_result.value.date_str

    start_dt = datetime.strptime(start_iso, "%Y-%m-%d")
    end_dt = datetime.strptime(end_iso, "%Y-%m-%d")

    if start_dt > end_dt:
        return False, [], "Start Date must be before or equal to End Date"

    dates = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    return True, dates, ""


# ===== Backward-compatible helpers =====

def parse_date(date_str: str) -> str:
    """
    Backward-compatible wrapper for existing parse_date() in qb_sync.py.
    Accepts "yesterday", "today", or "YYYY-MM-DD".
    Raises ValueError if invalid (same as original behavior).
    """
    result = parse_date_flexible(date_str, "legacy")
    if not result.success:
        raise ValueError(result.error)
    return result.value.date_str
