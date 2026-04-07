"""
Tests for date_parser.py - unified date parsing module.
"""

import pytest
from decimal import Decimal
from datetime import date

from date_parser import (
    parse_iso_date,
    parse_date_flexible,
    parse_toast_filename,
    normalize_marketplace_date,
    parse_date_list,
    parse_ui_date_to_toast,
    validate_toast_date_format,
    get_date_range_from_inputs,
    parse_date,
    ParsedDate,
    DateRange,
    ParseResult,
)


# ── parse_iso_date ─────────────────────────────────────────────────────


class TestParseIsoDate:
    def test_valid_iso(self):
        assert parse_iso_date("2026-03-28") == "2026-03-28"
        assert parse_iso_date("2026-01-01") == "2026-01-01"

    def test_valid_iso_with_whitespace(self):
        assert parse_iso_date("  2026-03-28  ") == "2026-03-28"

    def test_invalid_format(self):
        assert parse_iso_date("03/28/2026") is None
        assert parse_iso_date("03-28-2026") is None

    def test_invalid_date(self):
        assert parse_iso_date("2026-02-30") is None
        assert parse_iso_date("not-a-date") is None

    def test_empty(self):
        assert parse_iso_date("") is None
        assert parse_iso_date(None) is None


# ── parse_date_flexible ─────────────────────────────────────────────────


class TestParseDateFlexible:
    def test_iso_format(self):
        r = parse_date_flexible("2026-03-28")
        assert r.success
        assert r.value.date_str == "2026-03-28"

    def test_us_format(self):
        r = parse_date_flexible("03/28/2026")
        assert r.success
        assert r.value.date_str == "2026-03-28"

    def test_dash_format(self):
        r = parse_date_flexible("03-28-2026")
        assert r.success
        assert r.value.date_str == "2026-03-28"

    def test_month_name_format(self):
        r = parse_date_flexible("Mar 28, 2026")
        assert r.success
        assert r.value.date_str == "2026-03-28"

    def test_full_month_name(self):
        r = parse_date_flexible("March 28, 2026")
        assert r.success
        assert r.value.date_str == "2026-03-28"

    def test_today_special(self):
        r = parse_date_flexible("today")
        assert r.success
        assert r.value.original == "today"

    def test_yesterday_special(self):
        r = parse_date_flexible("yesterday")
        assert r.success
        assert r.value.original == "yesterday"

    def test_case_insensitive_special(self):
        r = parse_date_flexible("TODAY")
        assert r.success

    def test_unrecognized_returns_error(self):
        r = parse_date_flexible("not-a-date")
        assert not r.success
        assert "Unrecognized date format" in r.error

    def test_empty_returns_error(self):
        r = parse_date_flexible("")
        assert not r.success
        assert "Empty date string" in r.error

    def test_source_metadata(self):
        r = parse_date_flexible("03/28/2026", source="test")
        assert r.success
        assert r.value.source == "test"


# ── parse_toast_filename ───────────────────────────────────────────────


class TestParseToastFilename:
    def test_sales_summary_iso_range(self):
        r = parse_toast_filename("SalesSummary_2026-03-15_2026-03-21.xlsx")
        assert r.success
        dr = r.value
        assert isinstance(dr, DateRange)
        assert dr.start.date_str == "2026-03-15"
        assert dr.end.date_str == "2026-03-21"

    def test_sales_summary_dash_range(self):
        r = parse_toast_filename("SalesSummary-2026-03-15-2026-03-15.xlsx")
        assert r.success
        assert r.value.start.date_str == "2026-03-15"

    def test_toast_to_format(self):
        r = parse_toast_filename("Toast_2026-01-15_to_2026-01-21.xlsx")
        assert r.success
        dr = r.value
        assert dr.start.date_str == "2026-01-15"
        assert dr.end.date_str == "2026-01-21"

    def test_compact_single_date(self):
        r = parse_toast_filename("SalesSummary_20260328.xlsx")
        assert r.success
        assert r.value.date_str == "2026-03-28"

    def test_toast_compact_single_date(self):
        r = parse_toast_filename("Toast_20260315.xlsx")
        assert r.success
        assert r.value.date_str == "2026-03-15"

    def test_invalid_filename(self):
        r = parse_toast_filename("UnknownFile.xlsx")
        assert not r.success
        assert "Cannot parse date" in r.error

    def test_full_path(self):
        r = parse_toast_filename("E:/Reports/SalesSummary_2026-03-28_2026-03-28.xlsx")
        assert r.success


# ── normalize_marketplace_date ─────────────────────────────────────────


class TestNormalizeMarketplaceDate:
    def test_mmddyyyy_format(self):
        r = normalize_marketplace_date("1/2/2026")
        assert r.success
        assert r.value.date_str == "2026-01-02"

    def test_iso_format(self):
        r = normalize_marketplace_date("2026-03-28")
        assert r.success
        assert r.value.date_str == "2026-03-28"

    def test_grand_total_returns_error(self):
        r = normalize_marketplace_date("Grand Total")
        assert not r.success
        assert "Grand Total" in r.error

    def test_empty_cell_returns_error(self):
        r = normalize_marketplace_date("")
        assert not r.success
        assert "Empty date cell" in r.error

    def test_none_cell_returns_error(self):
        r = normalize_marketplace_date(None)
        assert not r.success
        assert "Empty date cell" in r.error

    def test_unrecognized_format_returns_error_with_details(self):
        r = normalize_marketplace_date("March 28th, 2026")
        assert not r.success
        assert "Unrecognized date format" in r.error

    def test_row_number_tracking(self):
        r = normalize_marketplace_date("03/28/2026", row_num=5)
        assert r.success
        assert r.value.line_info == "Row 5"


# ── parse_date_list ────────────────────────────────────────────────────


class TestParseDateList:
    def test_single_date(self):
        successes, failures = parse_date_list("2026-03-28")
        assert len(successes) == 1
        assert len(failures) == 0

    def test_multiple_dates(self):
        successes, failures = parse_date_list("2026-03-15, 2026-03-16, 2026-03-17")
        assert len(successes) == 3
        assert len(failures) == 0

    def test_mixed_valid_invalid(self):
        successes, failures = parse_date_list("2026-03-15, invalid, 2026-03-17")
        assert len(successes) == 2
        assert len(failures) == 1

    def test_special_dates(self):
        successes, failures = parse_date_list("today, yesterday")
        assert len(successes) == 2
        assert len(failures) == 0

    def test_empty_parts_skipped(self):
        successes, failures = parse_date_list("2026-03-15,,2026-03-16")
        assert len(successes) == 2


# ── Toast date format ──────────────────────────────────────────────────


class TestValidateToastDateFormat:
    def test_valid_date(self):
        ok, result = validate_toast_date_format(3, 15, 2026)
        assert ok
        assert result == "03152026"

    def test_single_digit_month(self):
        ok, result = validate_toast_date_format(3, 5, 2026)
        assert ok
        assert result == "03052026"  # NOT "352026" — M4 fix!

    def test_single_digit_day(self):
        ok, result = validate_toast_date_format(1, 9, 2026)
        assert ok
        assert result == "01092026"

    def test_invalid_date(self):
        ok, result = validate_toast_date_format(2, 30, 2026)
        assert not ok
        assert "is out of range" in result

    def test_leading_zeros_preserved(self):
        ok, result = validate_toast_date_format(12, 31, 2026)
        assert ok
        assert result == "12312026"


class TestParseUiDateToToast:
    def test_iso_to_toast_format(self):
        ok, result = parse_ui_date_to_toast("2026-03-15")
        assert ok
        assert result == "03152026"

    def test_us_format_converts(self):
        ok, result = parse_ui_date_to_toast("03/05/2026")
        assert ok
        assert result == "03052026"  # Zero-padded, NOT "352026"

    def test_invalid_returns_error(self):
        ok, result = parse_ui_date_to_toast("not-a-date")
        assert not ok
        assert "Unrecognized date format" in result


# ── get_date_range_from_inputs ────────────────────────────────────────


class TestGetDateRangeFromInputs:
    def test_single_day(self):
        ok, dates, err = get_date_range_from_inputs("2026-03-15", "2026-03-15")
        assert ok
        assert dates == ["2026-03-15"]

    def test_range_of_days(self):
        ok, dates, err = get_date_range_from_inputs("2026-03-15", "2026-03-17")
        assert ok
        assert dates == ["2026-03-15", "2026-03-16", "2026-03-17"]

    def test_flexible_format(self):
        ok, dates, err = get_date_range_from_inputs("03/15/2026", "03/17/2026")
        assert ok
        assert len(dates) == 3

    def test_special_dates(self):
        ok, dates, err = get_date_range_from_inputs("yesterday", "today")
        assert ok
        assert len(dates) == 2

    def test_empty_start(self):
        ok, dates, err = get_date_range_from_inputs("", "2026-03-17")
        assert not ok
        assert "enter" in err.lower()

    def test_invalid_start(self):
        ok, dates, err = get_date_range_from_inputs("invalid", "2026-03-17")
        assert not ok
        assert "Invalid Start Date" in err

    def test_invalid_end(self):
        ok, dates, err = get_date_range_from_inputs("2026-03-15", "invalid")
        assert not ok
        assert "Invalid End Date" in err

    def test_start_after_end(self):
        ok, dates, err = get_date_range_from_inputs("2026-03-20", "2026-03-15")
        assert not ok
        assert "before" in err.lower()


# ── backward-compatible parse_date ─────────────────────────────────────


class TestParseDate:
    def test_yesterday(self):
        result = parse_date("yesterday")
        assert result is not None
        assert parse_iso_date(result) is not None

    def test_today(self):
        result = parse_date("today")
        assert result is not None
        assert parse_iso_date(result) is not None

    def test_valid_iso(self):
        assert parse_date("2026-03-28") == "2026-03-28"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unrecognized date format"):
            parse_date("not-a-date")
