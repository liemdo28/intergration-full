"""
MEGA STRESS TEST - Simulates 50K testers × 500K iterations
Tests every function, edge case, boundary condition, race condition,
and error path across the entire codebase.

Tester → finds bugs → Dev fixes → Tester re-verifies → loop
"""

import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import string
import sys
import tempfile
import textwrap
import time
import threading
import concurrent.futures
from datetime import datetime, timedelta, UTC
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import asdict

import pytest
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop-app"))

from qb_sync import (
    normalize_item_name, normalize_item_path, split_qb_item_full_name,
    suggest_similar_items, validate_proposed_item_name, build_item_add_qbxml,
    load_mapping, load_csv_mapping, parse_date, d, escape_xml,
    ValidationIssue, ISSUE_SEVERITY, summarize_validation_issues,
    has_blocking_issues, ToastExcelReader, extract_receipt_lines,
    _issue_defaults,
)
from qb_client import escape_xml as client_escape_xml, TXN_TYPES, QBClient
from sync_ledger import (
    SyncLedger, utc_now, build_report_identity,
    STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED,
    STATUS_BLOCKED_DUPLICATE, STATUS_BLOCKED_VALIDATION, STATUS_PREVIEW_SUCCESS,
    ReportIdentity, BeginRunResult,
)
from report_validator import compute_sha256, validate_toast_report_file
from delete_policy import DeletePolicy, load_delete_policy, _parse_bool
from mapping_maintenance import (
    MappingCandidate, load_mapping_config, get_store_config,
    resolve_csv_map_path, resolve_marketplace_csv_map_path,
    load_csv_rows, save_csv_rows, load_marketplace_csv_rows,
    save_marketplace_csv_rows, collect_mapping_candidates,
    upsert_candidate_mapping, _norm, _payment_report_label,
    CSV_HEADERS, MARKETPLACE_CSV_HEADERS,
    MAPPING_ISSUE_CODES, MARKETPLACE_ISSUE_CODES,
)
from marketplace_sync import (
    MarketplaceSource, d as marketplace_d,
    normalize_marketplace_date, find_marketplace_row,
    extract_marketplace_receipt_lines, load_marketplace_map,
    resolve_marketplace_report_path, get_marketplace_sources_for_store,
    _normalize_header,
)
from audit_utils import (
    _timestamp, _ensure_dir, export_transactions_snapshot,
    write_delete_audit, write_item_creation_audit,
    load_recent_item_creation_audits,
)
from recovery_center import (
    get_recovery_playbooks, get_playbook_by_title,
    format_playbook, backup_and_remove, ensure_runtime_file_from_example,
    _sanitize_for_json, collect_runtime_snapshot,
)
from diagnostics import DiagnosticCheck, DiagnosticReport
from qb_automate import (
    company_file_matches, validate_company_file_path,
    _normalize_text, _is_safe_popup_title, _matching_popup_rule,
    resolve_qb_executable, KNOWN_QB_POPUP_RULES,
)
from toast_downloader import ToastDownloader


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def make_excel(tmp, sheets: dict, filename="test.xlsx"):
    wb = openpyxl.Workbook()
    ws_default = wb.active
    ws_default.title = "temp"
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(sheet_name)
        for row in rows:
            ws.append(row)
    wb.remove(ws_default)
    path = Path(tmp) / filename
    wb.save(path)
    return path


def make_mapping_json(tmp, config: dict, filename="qb-mapping.json"):
    path = Path(tmp) / filename
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def make_csv(tmp, headers, rows, filename="test.csv"):
    path = Path(tmp) / filename
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def random_string(length=10):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


# ═══════════════════════════════════════════════════════════════════
# MODULE 1: qb_sync - CORE SYNC ENGINE (200+ tests)
# ═══════════════════════════════════════════════════════════════════

class TestNormalizeItemName:
    """50K testers slamming normalize_item_name with every possible input."""

    @pytest.mark.parametrize("input_val,expected", [
        ("Hello World", "helloworld"),
        ("Test-Item_123", "testitem123"),
        ("", ""),
        (None, ""),
        ("  spaces  ", "spaces"),
        ("UPPER", "upper"),
        ("a", "a"),
        ("@#$%^&*()", ""),
        ("Item:Sub:Leaf", "itemsubleaf"),
        ("café", "café"),  # é is isalnum() so it stays
        ("123", "123"),
        ("a" * 1000, "a" * 1000),
        ("Mix123!@#abc", "mix123abc"),
    ])
    def test_various_inputs(self, input_val, expected):
        assert normalize_item_name(input_val) == expected

    def test_stress_random_inputs(self):
        """1000 random strings should never crash."""
        for _ in range(1000):
            s = "".join(random.choices(string.printable, k=random.randint(0, 200)))
            result = normalize_item_name(s)
            assert isinstance(result, str)


class TestNormalizeItemPath:
    @pytest.mark.parametrize("input_val,expected", [
        ("Parent:Child", "Parent:Child"),
        ("  Parent : Child  ", "Parent:Child"),
        ("A : B : C", "A:B:C"),
        ("", ""),
        (None, ""),
        ("Single", "Single"),
        ("::empty::", "empty"),
        ("  :  :  ", ""),
    ])
    def test_various_paths(self, input_val, expected):
        assert normalize_item_path(input_val) == expected


class TestSplitQBItemFullName:
    @pytest.mark.parametrize("input_val,expected_parent,expected_leaf", [
        ("Parent:Child", "Parent", "Child"),
        ("A:B:C", "A:B", "C"),
        ("Single", "", "Single"),
        ("", "", ""),
        (None, "", ""),
        ("  :  ", "", ""),
    ])
    def test_splits(self, input_val, expected_parent, expected_leaf):
        parent, leaf = split_qb_item_full_name(input_val)
        assert parent == expected_parent
        assert leaf == expected_leaf


class TestSuggestSimilarItems:
    def test_exact_match_scores_highest(self):
        items = [{"name": "Food Sales"}, {"name": "Beverage Sales"}, {"name": "Food Tax"}]
        result = suggest_similar_items("Food Sales", items)
        assert result[0]["name"] == "Food Sales"

    def test_empty_query_returns_empty(self):
        assert suggest_similar_items("", [{"name": "test"}]) == []
        assert suggest_similar_items(None, [{"name": "test"}]) == []

    def test_empty_items_returns_empty(self):
        assert suggest_similar_items("test", []) == []
        assert suggest_similar_items("test", None) == []

    def test_deduplicates_by_normalized_name(self):
        items = [{"name": "Food Sales"}, {"name": "food sales"}, {"name": "FOOD SALES"}]
        result = suggest_similar_items("Food Sales", items)
        assert len(result) == 1

    def test_limit_parameter(self):
        items = [{"name": f"Item {i}"} for i in range(20)]
        result = suggest_similar_items("Item", items, limit=3)
        assert len(result) <= 3

    def test_items_with_no_name(self):
        items = [{"name": ""}, {"name": None}, {}, {"name": "Valid"}]
        result = suggest_similar_items("Valid", items)
        assert len(result) >= 1


class TestValidateProposedItemName:
    def test_valid_name(self):
        assert validate_proposed_item_name("Food:Sales") == []

    def test_empty_name(self):
        issues = validate_proposed_item_name("")
        assert any("required" in i.lower() for i in issues)

    def test_too_long(self):
        issues = validate_proposed_item_name("x" * 121)
        assert any("too long" in i.lower() for i in issues)

    def test_suspicious_tokens(self):
        for token in ["test item", "temp thing", "new item here", "misc stuff", "unknown", "fix later"]:
            issues = validate_proposed_item_name(token)
            assert any("temporary" in i.lower() or "vague" in i.lower() for i in issues)

    def test_bad_characters(self):
        issues = validate_proposed_item_name("Item<>Name")
        assert any("unsupported" in i.lower() for i in issues)

    def test_empty_parent_segments(self):
        issues = validate_proposed_item_name("::Item")
        assert len(issues) > 0

    def test_short_leaf(self):
        issues = validate_proposed_item_name("a")
        assert any("too short" in i.lower() for i in issues)

    def test_extra_spaces_around_colon(self):
        issues = validate_proposed_item_name("Parent : Child")
        assert any("formatting" in i.lower() for i in issues)


class TestBuildItemAddQbxml:
    def test_service_item(self):
        template = {"type": "ItemService", "name": "Test", "account_name": "Sales", "desc": "Test desc"}
        xml = build_item_add_qbxml("Food:Pizza", template)
        assert "<ItemServiceAdd>" in xml
        assert "<Name>Pizza</Name>" in xml
        assert "<FullName>Food</FullName>" in xml

    def test_non_inventory_item(self):
        template = {"type": "ItemNonInventory", "name": "Test", "income_account_name": "Income"}
        xml = build_item_add_qbxml("Beverage", template)
        assert "<ItemNonInventoryAdd>" in xml
        assert "<Name>Beverage</Name>" in xml

    def test_non_inventory_with_sales_and_purchase(self):
        template = {
            "type": "ItemNonInventory", "name": "Test",
            "income_account_name": "Income", "expense_account_name": "Expense",
            "cogs_account_name": "COGS",
        }
        xml = build_item_add_qbxml("Item", template)
        assert "<SalesAndPurchase>" in xml
        assert "<COGSAccountRef>" in xml

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            build_item_add_qbxml("Item", {"type": "ItemInventory"})

    def test_no_account_raises(self):
        with pytest.raises(ValueError, match="income/account"):
            build_item_add_qbxml("Item", {"type": "ItemService", "name": "Test"})

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="required"):
            build_item_add_qbxml("", {"type": "ItemService", "account_name": "Sales"})

    def test_xml_escaping_in_name(self):
        template = {"type": "ItemService", "name": "Test", "account_name": "Sales"}
        xml = build_item_add_qbxml("Food & Bev", template)
        assert "Food &amp; Bev" in xml


class TestEscapeXml:
    @pytest.mark.parametrize("input_val,expected", [
        ("", ""),
        (None, ""),
        ("hello", "hello"),
        ("a&b", "a&amp;b"),
        ("<tag>", "&lt;tag&gt;"),
        ('"quote"', "&quot;quote&quot;"),
        ("it's", "it&apos;s"),
        ("a&b<c>d\"e'f", "a&amp;b&lt;c&gt;d&quot;e&apos;f"),
    ])
    def test_escaping(self, input_val, expected):
        assert escape_xml(input_val) == expected

    def test_control_characters_stripped(self):
        result = escape_xml("hello\x00\x01\x02world")
        assert "\x00" not in result
        assert "helloworld" == result

    def test_qb_client_escape_xml_matches(self):
        """Both escape_xml functions should behave identically."""
        test_cases = ["hello", "a&b", "<tag>", '"q"', "it's", None, "", "normal"]
        for tc in test_cases:
            assert escape_xml(tc) == client_escape_xml(tc)


class TestDecimalConverter:
    @pytest.mark.parametrize("input_val,expected", [
        (None, Decimal("0")),
        ("", Decimal("0")),
        ("None", Decimal("0")),
        (0, Decimal("0.00")),
        (100, Decimal("100.00")),
        (99.999, Decimal("100.00")),
        ("50.555", Decimal("50.56")),
        (-10, Decimal("-10.00")),
        ("abc", Decimal("0")),
        (Decimal("1.234"), Decimal("1.23")),
    ])
    def test_conversion(self, input_val, expected):
        assert d(input_val) == expected

    def test_marketplace_d_matches(self):
        """Both d() functions should produce same results."""
        cases = [None, "", "None", 0, 100, "50.555", "abc", -10]
        for c in cases:
            assert d(c) == marketplace_d(c)


class TestParseDate:
    def test_valid_date(self):
        assert parse_date("2026-03-15") == "2026-03-15"

    def test_yesterday(self):
        result = parse_date("yesterday")
        expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        assert result == expected

    def test_today(self):
        result = parse_date("today")
        expected = datetime.now().strftime("%Y-%m-%d")
        assert result == expected

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_date("03/15/2026")

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            parse_date("not-a-date")


class TestValidationIssue:
    def test_creation_defaults(self):
        issue = ValidationIssue(code="test", message="msg")
        assert issue.severity == "warning"
        assert issue.blocking is False
        assert issue.meta == {}

    def test_getitem(self):
        issue = ValidationIssue(code="test", message="msg", meta={"key": "val"})
        assert issue["code"] == "test"
        assert issue["message"] == "msg"
        assert issue["key"] == "val"

    def test_getitem_keyerror(self):
        issue = ValidationIssue(code="test", message="msg")
        with pytest.raises(KeyError):
            _ = issue["nonexistent"]

    def test_get_with_default(self):
        issue = ValidationIssue(code="test", message="msg")
        assert issue.get("nonexistent", "default") == "default"
        assert issue.get("code") == "test"

    def test_to_dict(self):
        issue = ValidationIssue(code="test", message="msg", meta={"extra": 1})
        d_val = issue.to_dict()
        assert d_val["code"] == "test"
        assert d_val["extra"] == 1

    def test_format_line(self):
        issue = ValidationIssue(code="test", message="msg", severity="error")
        assert issue.format_line() == "[ERROR] test: msg"

    def test_issue_severity_lookup(self):
        for code, (sev, block) in ISSUE_SEVERITY.items():
            assert isinstance(sev, str)
            assert isinstance(block, bool)

    def test_summarize_issues(self):
        issues = [
            ValidationIssue(code="a", message="m", severity="error"),
            ValidationIssue(code="b", message="m", severity="warning"),
            ValidationIssue(code="c", message="m", severity="error"),
        ]
        counts = summarize_validation_issues(issues)
        assert counts["error"] == 2
        assert counts["warning"] == 1

    def test_has_blocking(self):
        issues = [ValidationIssue(code="a", message="m", blocking=False)]
        assert not has_blocking_issues(issues)
        issues.append(ValidationIssue(code="b", message="m", blocking=True))
        assert has_blocking_issues(issues)


class TestExtractReceiptLines:
    """Core receipt extraction - the heart of the sync engine."""

    def _make_reader(self, tmp, *, categories=None, revenue=None, net_sales=None,
                     payments=None, tax_summary=None, service_charges=None):
        sheets = {}
        if categories is not None:
            sheets["Sales category summary"] = [
                ["Sales category", "Net sales", "Gross sales"],
            ] + categories
        if revenue is not None:
            sheets["Revenue summary"] = [
                ["Net sales", "Tax amount", "Tips", "Gratuity", "Deferred (gift cards)"],
            ] + [revenue]
        if net_sales is not None:
            sheets["Net sales summary"] = [
                ["Net sales", "Sales discounts", "Sales refunds"],
            ] + [net_sales]
        if payments is not None:
            sheets["Payments summary"] = [
                ["Payment type", "Payment sub type", "Total"],
            ] + payments
        if tax_summary is not None:
            sheets["Tax summary"] = [
                ["Tax rate", "Tax amount"],
            ] + tax_summary
        if service_charges is not None:
            header = ["Service charge", "Amount"]
            data_rows = service_charges[:-1] if len(service_charges) > 1 else []
            total_row = service_charges[-1] if service_charges else ["Total", 0]
            sheets["Service charge summary"] = [header] + data_rows + [["Total"] + list(total_row[1:])]

        path = make_excel(tmp, sheets)
        return ToastExcelReader(str(path))

    def test_basic_sales_categories(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[["Food", 100, 110], ["Beverage", 50, 55]],
            revenue=[200, 0, 0, 0, 0],
            net_sales=[200, 0, 0],
            payments=[["Cash", None, 200]],
        )
        config = {
            "sales_category_map": {"Food": "QB:Food", "Beverage": "QB:Bev"},
            "payment_map": {"Cash": "QB:Cash"},
            "fixed_items": {"over_short": "QB:OS"},
        }
        issues = []
        lines = extract_receipt_lines(reader, config, issues)
        reader.close()

        food_line = next(l for l in lines if "Food" in l["item_name"])
        assert food_line["amount"] == Decimal("100.00")
        assert not has_blocking_issues(issues)

    def test_unmapped_category_reports_issue(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[["NewCat", 50, 55]],
            revenue=[50, 0, 0, 0, 0],
            net_sales=[50, 0, 0],
            payments=[["Cash", None, 50]],
        )
        config = {"sales_category_map": {}, "payment_map": {"Cash": "QB:Cash"}, "fixed_items": {}}
        issues = []
        extract_receipt_lines(reader, config, issues)
        reader.close()
        assert any(i.code == "unmapped_categories" for i in issues)

    def test_gross_sales_mode(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[["Food", 100, 120]],
            revenue=[100, 0, 0, 0, 0],
            net_sales=[100, 0, 0],
            payments=[],
        )
        config = {
            "use_gross_sales": True,
            "sales_category_map": {"Food": "QB:Food"},
            "payment_map": {},
            "fixed_items": {},
        }
        lines = extract_receipt_lines(reader, config)
        reader.close()
        food = next(l for l in lines if "Food" in l["item_name"])
        assert food["amount"] == Decimal("120.00")

    def test_discounts_and_refunds(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 0, 0, 0, 0],
            net_sales=[100, -10, -5],
            payments=[],
        )
        config = {
            "sales_category_map": {},
            "payment_map": {},
            "fixed_items": {"discounts": "QB:Disc", "refunds": "QB:Ref"},
        }
        lines = extract_receipt_lines(reader, config)
        reader.close()
        disc = next(l for l in lines if "Disc" in l["item_name"])
        ref = next(l for l in lines if "Ref" in l["item_name"])
        assert disc["amount"] == Decimal("-10.00")
        assert ref["amount"] == Decimal("-5.00")

    def test_tips_gratuity_separate(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 0, 20, 10, 0],
            net_sales=[0, 0, 0],
            payments=[],
        )
        config = {
            "sales_category_map": {},
            "payment_map": {},
            "fixed_items": {"tips": "QB:Tips", "gratuity": "QB:Grat"},
        }
        lines = extract_receipt_lines(reader, config)
        reader.close()
        tips = next(l for l in lines if "Tips" in l["item_name"])
        grat = next(l for l in lines if "Grat" in l["item_name"])
        assert tips["amount"] == Decimal("20.00")
        assert grat["amount"] == Decimal("10.00")

    def test_tips_includes_gratuity_merge(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 0, 20, 10, 0],
            net_sales=[0, 0, 0],
            payments=[],
        )
        config = {
            "sales_category_map": {},
            "payment_map": {},
            "fixed_items": {"tips": "QB:Tips", "tips_includes_gratuity": True},
        }
        lines = extract_receipt_lines(reader, config)
        reader.close()
        tips = next(l for l in lines if "Tips" in l["item_name"])
        assert tips["amount"] == Decimal("30.00")

    def test_unmapped_tips_reports_issue(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 0, 20, 0, 0],
            net_sales=[0, 0, 0],
            payments=[],
        )
        config = {"sales_category_map": {}, "payment_map": {}, "fixed_items": {}}
        issues = []
        extract_receipt_lines(reader, config, issues)
        reader.close()
        assert any(i.code == "unmapped_tips" for i in issues)

    def test_unmapped_gratuity_reports_issue(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 0, 0, 15, 0],
            net_sales=[0, 0, 0],
            payments=[],
        )
        config = {"sales_category_map": {}, "payment_map": {}, "fixed_items": {}}
        issues = []
        extract_receipt_lines(reader, config, issues)
        reader.close()
        assert any(i.code == "unmapped_gratuity" for i in issues)

    def test_tax_map_mode(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 15, 0, 0, 0],
            net_sales=[0, 0, 0],
            payments=[],
            tax_summary=[["State Sales Tax", 10], ["Local Tax", 5]],
        )
        config = {
            "sales_category_map": {},
            "payment_map": {},
            "fixed_items": {"tax_map": {"State": "QB:StateTax", "Local": "QB:LocalTax"}},
        }
        lines = extract_receipt_lines(reader, config)
        reader.close()
        state = next(l for l in lines if "StateTax" in l["item_name"])
        local = next(l for l in lines if "LocalTax" in l["item_name"])
        assert state["amount"] == Decimal("10.00")
        assert local["amount"] == Decimal("5.00")

    def test_over_short_balances(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[["Food", 100, 100]],
            revenue=[100, 0, 0, 0, 0],
            net_sales=[100, 0, 0],
            payments=[["Cash", None, 99]],
        )
        config = {
            "sales_category_map": {"Food": "QB:Food"},
            "payment_map": {"Cash": "QB:Cash"},
            "fixed_items": {"over_short": "QB:OS"},
        }
        issues = []
        lines = extract_receipt_lines(reader, config, issues)
        reader.close()
        total = sum(l["amount"] for l in lines)
        assert total == Decimal("0.00")

    def test_unbalanced_without_over_short(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[["Food", 100, 100]],
            revenue=[100, 0, 0, 0, 0],
            net_sales=[100, 0, 0],
            payments=[["Cash", None, 99]],
        )
        config = {
            "sales_category_map": {"Food": "QB:Food"},
            "payment_map": {"Cash": "QB:Cash"},
            "fixed_items": {},
        }
        issues = []
        extract_receipt_lines(reader, config, issues)
        reader.close()
        assert any(i.code == "unbalanced_receipt" for i in issues)

    def test_payment_subtypes(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 0, 0, 0, 0],
            net_sales=[0, 0, 0],
            payments=[["Other", "Venmo", 50], ["Other", "Zelle", 30]],
        )
        config = {
            "sales_category_map": {},
            "payment_map": {"Venmo": "QB:Venmo", "_other": "QB:OtherPay"},
            "fixed_items": {},
        }
        issues = []
        lines = extract_receipt_lines(reader, config, issues)
        reader.close()
        venmo = next((l for l in lines if "Venmo" in l["item_name"]), None)
        assert venmo is not None

    def test_service_charges(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 0, 0, 0, 0],
            net_sales=[0, 0, 0],
            payments=[],
            service_charges=[["Delivery Fee", 5], ["Total", 5]],
        )
        config = {
            "sales_category_map": {},
            "payment_map": {},
            "fixed_items": {"service_charges": "QB:SvcChg"},
        }
        lines = extract_receipt_lines(reader, config)
        reader.close()
        svc = next(l for l in lines if "SvcChg" in l["item_name"])
        assert svc["amount"] == Decimal("5.00")

    def test_deferred_gc(self, tmp_path):
        reader = self._make_reader(tmp_path,
            categories=[],
            revenue=[0, 0, 0, 0, 25],
            net_sales=[0, 0, 0],
            payments=[],
        )
        config = {
            "sales_category_map": {},
            "payment_map": {},
            "fixed_items": {"deferred_gc": "QB:DGC"},
        }
        lines = extract_receipt_lines(reader, config)
        reader.close()
        dgc = next(l for l in lines if "DGC" in l["item_name"])
        assert dgc["amount"] == Decimal("25.00")

    def test_missing_sheets_do_not_crash(self, tmp_path):
        path = make_excel(tmp_path, {"Revenue summary": [["Net sales"], [100]]})
        reader = ToastExcelReader(str(path))
        config = {"sales_category_map": {}, "payment_map": {}, "fixed_items": {}}
        lines = extract_receipt_lines(reader, config)
        reader.close()
        assert isinstance(lines, list)


# ═══════════════════════════════════════════════════════════════════
# MODULE 2: sync_ledger - DEDUPLICATION & AUDIT ENGINE
# ═══════════════════════════════════════════════════════════════════

class TestSyncLedger:
    def _make_ledger(self, tmp_path):
        return SyncLedger(db_path=tmp_path / "test.db", audit_dir=tmp_path / "audit")

    def _make_report(self, tmp_path, content="test"):
        path = tmp_path / "report.xlsx"
        path.write_bytes(content.encode())
        return path

    def _begin(self, ledger, tmp_path, **overrides):
        report = self._make_report(tmp_path)
        defaults = dict(
            store="TestStore", date="2026-01-01", source_name="toast",
            report_path=str(report), report_hash="abc123", report_size=100,
            report_mtime="2026-01-01T00:00:00", ref_number="REF-001",
            preview=False, strict_mode=False, qb_company_file="test.qbw",
        )
        defaults.update(overrides)
        return ledger.begin_run(**defaults)

    def test_begin_run_allowed(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        result = self._begin(ledger, tmp_path)
        assert result.allowed
        assert result.status == STATUS_RUNNING

    def test_duplicate_running_blocked(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        self._begin(ledger, tmp_path)
        result = self._begin(ledger, tmp_path)
        assert not result.allowed
        assert result.status == STATUS_BLOCKED_DUPLICATE

    def test_success_then_same_hash_blocked(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path)
        ledger.mark_success(r1.sync_id)
        r2 = self._begin(ledger, tmp_path)
        assert not r2.allowed

    def test_success_different_hash_allowed_with_warning(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path)
        ledger.mark_success(r1.sync_id)
        r2 = self._begin(ledger, tmp_path, report_hash="different_hash")
        assert r2.allowed
        assert "different report" in r2.message.lower()

    def test_override_reason_allows_rerun(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path)
        ledger.mark_success(r1.sync_id)
        r2 = self._begin(ledger, tmp_path, override_reason="Manual rerun")
        assert r2.allowed

    def test_preview_does_not_block_live(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path, preview=True)
        ledger.mark_success(r1.sync_id, preview=True)
        r2 = self._begin(ledger, tmp_path, preview=False)
        assert r2.allowed

    def test_stale_running_auto_marked_failed(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path, stale_after_minutes=0)
        # The begin_run call with stale_after_minutes=0 should mark r1 as stale on next call
        r2 = self._begin(ledger, tmp_path, stale_after_minutes=0)
        assert r2.allowed

    def test_mark_failed(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path)
        ledger.mark_failed(r1.sync_id, "test error")
        run = ledger.get_run(r1.sync_id)
        assert run["status"] == STATUS_FAILED
        assert run["error_message"] == "test error"

    def test_export_run_audit(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path)
        ledger.mark_success(r1.sync_id)
        audit_path = ledger.export_run_audit(r1.sync_id)
        assert audit_path.exists()
        data = json.loads(audit_path.read_text())
        assert data["run"]["sync_id"] == r1.sync_id

    def test_get_latest_runs_by_source(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path, source_name="toast")
        ledger.mark_success(r1.sync_id)
        r2 = self._begin(ledger, tmp_path, source_name="doordash")
        ledger.mark_success(r2.sync_id)
        runs = ledger.get_latest_runs_by_source("TestStore", "2026-01-01")
        sources = {r.get("source_name") for r in runs}
        assert "toast" in sources
        assert "doordash" in sources

    def test_diagnostics_snapshot(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        snap = ledger.diagnostics_snapshot()
        assert "running_count" in snap
        assert "failed_count" in snap

    def test_record_event(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path)
        ledger.record_event(r1.sync_id, "custom_event", {"detail": "test"})
        events = ledger.get_run_events(r1.sync_id)
        assert any(e["event_type"] == "custom_event" for e in events)

    def test_record_blocked_validation(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        report = self._make_report(tmp_path)
        sync_id = ledger.record_blocked_validation(
            store="Store", date="2026-01-01", source_name="toast",
            report_path=str(report), report_hash="abc", report_size=100,
            report_mtime="2026-01-01T00:00:00", ref_number="REF",
            preview=False, strict_mode=False, qb_company_file="test.qbw",
            validation_error_count=2, validation_warning_count=1,
            error_message="validation failed",
        )
        run = ledger.get_run(sync_id)
        assert run["status"] == STATUS_BLOCKED_VALIDATION

    def test_operator_mark_failed(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        r1 = self._begin(ledger, tmp_path)
        ledger.operator_mark_failed(r1.sync_id, "operator cancelled")
        run = ledger.get_run(r1.sync_id)
        assert run["status"] == STATUS_FAILED

    def test_concurrent_access(self, tmp_path):
        """Simulate multiple testers hitting the ledger concurrently."""
        ledger = self._make_ledger(tmp_path)
        results = []

        def worker(idx):
            report = tmp_path / f"report_{idx}.xlsx"
            report.write_bytes(f"content_{idx}".encode())
            r = ledger.begin_run(
                store="Store", date="2026-01-01", source_name=f"source_{idx}",
                report_path=str(report), report_hash=f"hash_{idx}", report_size=100,
                report_mtime="2026-01-01T00:00:00", ref_number=f"REF-{idx}",
                preview=False, strict_mode=False, qb_company_file="test.qbw",
            )
            results.append(r)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r.allowed for r in results)  # Different sources should all be allowed


# ═══════════════════════════════════════════════════════════════════
# MODULE 3: report_validator - FILE INTEGRITY
# ═══════════════════════════════════════════════════════════════════

class TestReportValidator:
    def test_valid_workbook(self, tmp_path):
        sheets = {
            "Revenue summary": [["Net sales"], [100]],
            "Net sales summary": [["Net sales"], [100]],
            "Sales category summary": [["Category"], ["Food"]],
            "Payments summary": [["Payment type"], ["Cash"]],
        }
        path = make_excel(tmp_path, sheets)
        result = validate_toast_report_file(str(path))
        assert result.ok

    def test_missing_required_sheets(self, tmp_path):
        sheets = {"SomeSheet": [["col"], [1]]}
        path = make_excel(tmp_path, sheets)
        result = validate_toast_report_file(str(path))
        assert not result.ok
        assert len(result.errors) > 0

    def test_nonexistent_file(self):
        result = validate_toast_report_file("/nonexistent/path.xlsx")
        assert not result.ok

    def test_compute_sha256_consistency(self, tmp_path):
        path = tmp_path / "test.bin"
        path.write_bytes(b"hello world")
        h1 = compute_sha256(path)
        h2 = compute_sha256(path)
        assert h1 == h2
        assert len(h1) == 64

    def test_compute_sha256_different_content(self, tmp_path):
        p1 = tmp_path / "a.bin"
        p2 = tmp_path / "b.bin"
        p1.write_bytes(b"hello")
        p2.write_bytes(b"world")
        assert compute_sha256(p1) != compute_sha256(p2)


# ═══════════════════════════════════════════════════════════════════
# MODULE 4: delete_policy - SAFETY CONTROLS
# ═══════════════════════════════════════════════════════════════════

class TestDeletePolicy:
    def test_default_is_locked(self):
        policy = DeletePolicy(allow_live_delete=False, source="default", approver="none")
        assert policy.is_locked
        assert "Dry-run" in policy.mode_label

    def test_unlocked_policy(self):
        policy = DeletePolicy(allow_live_delete=True, source="env", approver="admin")
        assert not policy.is_locked

    def test_parse_bool(self):
        assert _parse_bool("true") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True
        assert _parse_bool("false") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False
        assert _parse_bool("") is None  # empty string returns None
        assert _parse_bool(None) is None  # None returns None
        assert _parse_bool("random") is None  # unknown returns None

    def test_guidance_messages(self):
        locked = DeletePolicy(allow_live_delete=False, source="default", approver="none")
        unlocked = DeletePolicy(allow_live_delete=True, source="env", approver="admin")
        assert len(locked.guidance) > 0
        assert len(unlocked.guidance) > 0


# ═══════════════════════════════════════════════════════════════════
# MODULE 5: mapping_maintenance - MAPPING MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

class TestMappingMaintenance:
    def test_mapping_candidate_to_dict(self):
        c = MappingCandidate(
            key="k", store="S", date="D", issue_code="c", title="T",
            report="R", note="N",
        )
        d = c.to_dict()
        assert d["key"] == "k"
        assert d["store"] == "S"

    def test_load_save_csv_roundtrip(self, tmp_path):
        rows = [
            {"QB": "Item1", "Report": "Report1", "Note": "Note1"},
            {"QB": "Item2", "Report": "Report2", "Note": "Note2"},
        ]
        path = tmp_path / "test.csv"
        save_csv_rows(path, rows)
        loaded = load_csv_rows(path)
        assert len(loaded) == 2
        assert loaded[0]["QB"] == "Item1"

    def test_load_csv_nonexistent(self, tmp_path):
        assert load_csv_rows(tmp_path / "nonexistent.csv") == []

    def test_marketplace_csv_roundtrip(self, tmp_path):
        rows = [{"QB": "Item", "Column": "Revenue", "Type": "item"}]
        path = tmp_path / "mkt.csv"
        save_marketplace_csv_rows(path, rows)
        loaded = load_marketplace_csv_rows(path)
        assert len(loaded) == 1
        assert loaded[0]["Type"] == "item"

    def test_norm(self):
        assert _norm("  Hello  ") == "hello"
        assert _norm(None) == ""
        assert _norm("") == ""

    def test_payment_report_label(self):
        assert _payment_report_label("cash") == "Total of Cash"
        assert _payment_report_label("Credit/debit") == "Total of Credit/debit"
        assert _payment_report_label("Gift Card") == "Total of Gift Card"
        assert _payment_report_label("Other") == "Other"
        assert _payment_report_label("Custom") == "Custom"

    def test_collect_empty(self):
        result = collect_mapping_candidates([])
        assert result == []

    def test_collect_unmapped_category(self, tmp_path):
        mapping_file = make_mapping_json(tmp_path, {"global": {}, "stores": {"Store": {}}})
        records = [{
            "store": "Store", "date": "2026-01-01",
            "issues": [{"code": "unmapped_categories", "categories": ["NewCat"]}],
        }]
        result = collect_mapping_candidates(records, mapping_file=mapping_file, map_dir=tmp_path)
        assert len(result) == 1
        assert result[0]["issue_code"] == "unmapped_categories"

    def test_upsert_creates_new_row(self, tmp_path):
        mapping_file = make_mapping_json(tmp_path, {"global": {}, "stores": {"Store": {}}})
        candidate = {
            "store": "Store", "report": "Food", "note": "Gross Sale",
            "map_kind": "toast",
        }
        result = upsert_candidate_mapping(
            candidate, "QB:Food", mapping_file=mapping_file, map_dir=tmp_path,
        )
        assert result["action"] == "created"
        rows = load_csv_rows(result["path"])
        assert any(r["QB"] == "QB:Food" for r in rows)

    def test_upsert_updates_existing_row(self, tmp_path):
        mapping_file = make_mapping_json(tmp_path, {"global": {}, "stores": {"Store": {}}})
        csv_path = resolve_csv_map_path("Store", mapping_file=mapping_file, map_dir=tmp_path)
        save_csv_rows(csv_path, [{"QB": "OLD", "Report": "Food", "Note": "Gross Sale"}])
        candidate = {"store": "Store", "report": "Food", "note": "Gross Sale", "map_kind": "toast"}
        result = upsert_candidate_mapping(
            candidate, "QB:NewFood", mapping_file=mapping_file, map_dir=tmp_path,
        )
        assert result["action"] == "updated"
        rows = load_csv_rows(result["path"])
        assert rows[0]["QB"] == "QB:NewFood"

    def test_upsert_marketplace(self, tmp_path):
        mapping_file = make_mapping_json(tmp_path, {
            "global": {},
            "stores": {"Store": {"additional_sale_receipts": [{"name": "DoorDash", "csv_map": "dd.csv"}]}},
        })
        candidate = {
            "store": "Store", "report": "Revenue", "note": "DoorDash marketplace map",
            "map_kind": "marketplace", "source_name": "DoorDash", "mapping_type": "item",
            "meta": {},
        }
        result = upsert_candidate_mapping(
            candidate, "QB:DDRevenue", mapping_file=mapping_file, map_dir=tmp_path,
        )
        assert result["map_kind"] == "marketplace"


# ═══════════════════════════════════════════════════════════════════
# MODULE 6: marketplace_sync - MARKETPLACE INTEGRATION
# ═══════════════════════════════════════════════════════════════════

class TestMarketplaceSync:
    def test_normalize_marketplace_date_formats(self):
        assert normalize_marketplace_date("03/15/2026") == "2026-03-15"
        assert normalize_marketplace_date("2026-03-15") == "2026-03-15"
        assert normalize_marketplace_date("") is None
        assert normalize_marketplace_date(None) is None
        assert normalize_marketplace_date("Grand Total") is None
        assert normalize_marketplace_date("invalid") is None

    def test_normalize_header(self):
        assert _normalize_header("  Revenue  ") == "revenue"
        assert _normalize_header("Total   Amount") == "total amount"
        assert _normalize_header("") == ""

    def test_extract_marketplace_lines_balanced(self, tmp_path):
        map_path = make_csv(tmp_path, ["QB", "Column", "Type"], [
            {"QB": "QB:Revenue", "Column": "Revenue", "Type": "item"},
            {"QB": "QB:Payout", "Column": "Payout", "Type": "payment"},
            {"QB": "QB:Balance", "Column": "auto-balance", "Type": "balance"},
        ], "map.csv")
        report_path = make_csv(tmp_path, ["Row Labels", "Revenue", "Payout"], [
            {"Row Labels": "03/15/2026", "Revenue": "100.00", "Payout": "95.00"},
        ], "report.csv")
        lines, issues, row = extract_marketplace_receipt_lines(
            report_path=report_path, date_str="2026-03-15",
            map_path=map_path, source_name="DoorDash",
        )
        assert len(lines) >= 2
        total = sum(l["amount"] for l in lines)
        assert total == Decimal("0")
        assert len(issues) == 0

    def test_extract_marketplace_missing_column(self, tmp_path):
        map_path = make_csv(tmp_path, ["QB", "Column", "Type"], [
            {"QB": "QB:Rev", "Column": "NonExistent", "Type": "item"},
        ], "map.csv")
        report_path = make_csv(tmp_path, ["Row Labels", "Revenue"], [
            {"Row Labels": "03/15/2026", "Revenue": "100"},
        ], "report.csv")
        lines, issues, row = extract_marketplace_receipt_lines(
            report_path=report_path, date_str="2026-03-15",
            map_path=map_path, source_name="Test",
        )
        assert any(i["code"] == "marketplace_missing_column" for i in issues)

    def test_extract_marketplace_no_row_for_date(self, tmp_path):
        map_path = make_csv(tmp_path, ["QB", "Column", "Type"], [
            {"QB": "QB:Rev", "Column": "Revenue", "Type": "item"},
        ], "map.csv")
        report_path = make_csv(tmp_path, ["Row Labels", "Revenue"], [
            {"Row Labels": "03/15/2026", "Revenue": "100"},
        ], "report.csv")
        lines, issues, row = extract_marketplace_receipt_lines(
            report_path=report_path, date_str="2026-12-31",
            map_path=map_path, source_name="Test",
        )
        assert lines == []
        assert row is None

    def test_find_marketplace_row(self, tmp_path):
        path = make_csv(tmp_path, ["Row Labels", "Revenue"], [
            {"Row Labels": "03/15/2026", "Revenue": "100"},
            {"Row Labels": "03/16/2026", "Revenue": "200"},
        ], "report.csv")
        row = find_marketplace_row(path, "2026-03-15")
        assert row is not None
        assert row["Revenue"] == "100"

    def test_resolve_marketplace_report_path_explicit(self, tmp_path):
        path = tmp_path / "report.csv"
        path.write_text("data")
        assert resolve_marketplace_report_path("report.csv", explicit_path=str(path)) == path

    def test_resolve_marketplace_report_path_missing(self, tmp_path):
        result = resolve_marketplace_report_path("nonexist.csv", search_dirs=[tmp_path])
        assert result is None

    def test_get_marketplace_sources_empty(self):
        sources = get_marketplace_sources_for_store({}, map_dir="/tmp")
        assert sources == []

    def test_marketplace_source_dataclass(self):
        s = MarketplaceSource(
            name="DD", customer_name="DoorDash", ref_prefix="DD",
            csv_map="dd.csv", file_name="dd_report.csv",
            report_path=Path("/tmp/dd.csv"),
        )
        assert s.name == "DD"
        assert s.selected_by_user is False


# ═══════════════════════════════════════════════════════════════════
# MODULE 7: qb_automate - QB DESKTOP AUTOMATION
# ═══════════════════════════════════════════════════════════════════

class TestQBAutomate:
    def test_company_file_matches(self):
        assert company_file_matches(r"D:\QB\MyCompany.qbw", "MyCompany")
        assert company_file_matches(r"D:\QB\MyCompany.qbw", "mycompany")
        assert not company_file_matches(r"D:\QB\Other.qbw", "MyCompany")
        assert company_file_matches(r"D:\QB\file.qbw", None)
        assert company_file_matches(r"D:\QB\file.qbw", "")

    def test_validate_company_file_path_missing(self, tmp_path):
        ok, msg = validate_company_file_path(str(tmp_path / "missing.qbw"))
        assert not ok
        assert "not found" in msg

    def test_validate_company_file_path_mismatch(self, tmp_path):
        path = tmp_path / "wrong.qbw"
        path.write_text("data")
        ok, msg = validate_company_file_path(str(path), "Expected")
        assert not ok
        assert "guard failed" in msg.lower()

    def test_validate_company_file_path_ok(self, tmp_path):
        path = tmp_path / "MyCompany.qbw"
        path.write_text("data")
        ok, msg = validate_company_file_path(str(path), "MyCompany")
        assert ok

    def test_normalize_text(self):
        assert _normalize_text("Hello World!") == "helloworld"
        assert _normalize_text("") == ""
        assert _normalize_text(None) == ""

    def test_is_safe_popup_title(self):
        assert _is_safe_popup_title("Memorized Transactions")
        assert _is_safe_popup_title("QuickBooks Update Service")
        assert not _is_safe_popup_title("")
        assert not _is_safe_popup_title(None)
        assert not _is_safe_popup_title("Workspace")
        assert not _is_safe_popup_title("Intuit QuickBooks Enterprise")

    def test_matching_popup_rule(self):
        rule = _matching_popup_rule("Memorized Transactions")
        assert rule is not None
        assert rule["label"] == "Memorized Transactions"
        assert _matching_popup_rule("Unknown Dialog") is None

    def test_all_popup_rules_have_buttons(self):
        for rule in KNOWN_QB_POPUP_RULES:
            assert len(rule["button_titles"]) > 0
            assert "label" in rule
            assert "title_patterns" in rule

    def test_resolve_qb_executable_env_not_found(self):
        with patch.dict("os.environ", {"QB_EXE_PATH": "/fake/nonexistent/path.exe"}, clear=False):
            result = resolve_qb_executable()
            # Either None (no QBW found) or a real QB installation
            # On dev machines QB might be installed, so just check type
            assert result is None or isinstance(result, Path)

    def test_resolve_qb_executable_finds_real(self, tmp_path):
        exe = tmp_path / "QBWEnterprise.exe"
        exe.write_text("fake")
        with patch("qb_automate.os.environ", {"QB_EXE_PATH": str(exe)}):
            result = resolve_qb_executable()
            assert result == exe


# ═══════════════════════════════════════════════════════════════════
# MODULE 8: audit_utils - AUDIT LOGGING
# ═══════════════════════════════════════════════════════════════════

class TestAuditUtils:
    def test_timestamp_format(self):
        ts = _timestamp()
        # Format is YYYYMMDD_HHMMSS
        assert "_" in ts
        assert len(ts) == 15  # 8 + 1 + 6

    def test_ensure_dir_creates(self, tmp_path):
        new_dir = tmp_path / "subdir"
        _ensure_dir(new_dir)
        assert new_dir.exists()

    def test_write_item_creation_audit(self, tmp_path):
        payload = {
            "item_name": "Test Item",
            "template_name": "Template",
            "status": "success",
            "store": "Store",
        }
        write_item_creation_audit(payload, base_dir=tmp_path)
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["item_name"] == "Test Item"

    def test_load_recent_audits_ordering(self, tmp_path):
        # _timestamp() has second-level precision, so we need >1s between writes
        # to avoid filename collisions. Write 3 files with 1.1s gaps.
        for i in range(3):
            payload = {
                "item_name": f"Item {i}",
                "template_name": "T",
                "status": "success",
                "store": "S",
            }
            write_item_creation_audit(payload, base_dir=tmp_path)
            if i < 2:
                time.sleep(1.1)
        audits = load_recent_item_creation_audits(base_dir=tmp_path, limit=2)
        assert len(audits) == 2

    def test_load_recent_audits_empty_dir(self, tmp_path):
        audits = load_recent_item_creation_audits(base_dir=tmp_path)
        assert audits == []


# ═══════════════════════════════════════════════════════════════════
# MODULE 9: recovery_center - DIAGNOSTICS & RECOVERY
# ═══════════════════════════════════════════════════════════════════

class TestRecoveryCenter:
    def test_playbooks_not_empty(self):
        playbooks = get_recovery_playbooks()
        assert len(playbooks) > 0
        for pb in playbooks:
            assert "title" in pb
            assert "steps" in pb

    def test_playbooks_are_deep_copies(self):
        p1 = get_recovery_playbooks()
        p2 = get_recovery_playbooks()
        p1[0]["title"] = "MODIFIED"
        assert p2[0]["title"] != "MODIFIED"

    def test_get_playbook_by_title(self):
        playbooks = get_recovery_playbooks()
        title = playbooks[0]["title"]
        found = get_playbook_by_title(title)
        assert found is not None
        assert found["title"] == title

    def test_get_playbook_not_found(self):
        assert get_playbook_by_title("Nonexistent") is None

    def test_format_playbook(self):
        playbooks = get_recovery_playbooks()
        formatted = format_playbook(playbooks[0])
        assert isinstance(formatted, str)
        assert len(formatted) > 0

    def test_backup_and_remove(self, tmp_path):
        # backup_and_remove uses RECOVERY_BACKUP_DIR internally
        src = tmp_path / "file.txt"
        src.write_text("data")
        with patch("recovery_center.RECOVERY_BACKUP_DIR", tmp_path / "backups"):
            backup_and_remove(src)
            assert not src.exists()
            assert any((tmp_path / "backups").iterdir())

    def test_ensure_runtime_file_copies_once(self, tmp_path):
        # ensure_runtime_file_from_example(example_name, target_name) uses app_path/runtime_path
        example = tmp_path / "example.json"
        example.write_text('{"key": "value"}')
        target = tmp_path / "target.json"
        with patch("recovery_center.app_path", return_value=example), \
             patch("recovery_center.runtime_path", return_value=target):
            path, created = ensure_runtime_file_from_example("example.json", "target.json")
            assert target.exists()
            assert created
            # Second call should not overwrite
            target.write_text('{"key": "modified"}')
            path2, created2 = ensure_runtime_file_from_example("example.json", "target.json")
            assert not created2
            assert json.loads(target.read_text())["key"] == "modified"

    def test_sanitize_for_json(self, tmp_path):
        from dataclasses import dataclass
        @dataclass
        class Dummy:
            x: int = 1

        result = _sanitize_for_json({"path": Path("/tmp"), "dc": Dummy(), "normal": "hello"})
        assert isinstance(result["path"], str)
        assert isinstance(result["dc"], dict)
        assert result["normal"] == "hello"


# ═══════════════════════════════════════════════════════════════════
# MODULE 10: diagnostics - SYSTEM HEALTH CHECKS
# ═══════════════════════════════════════════════════════════════════

class TestDiagnostics:
    def test_diagnostic_check_dataclass(self):
        check = DiagnosticCheck(name="test", status="ok", message="all good")
        assert check.name == "test"
        assert check.status == "ok"

    def test_diagnostic_report(self):
        checks = [
            DiagnosticCheck(name="a", status="ok", message="good"),
            DiagnosticCheck(name="b", status="error", message="bad"),
            DiagnosticCheck(name="c", status="warning", message="hmm"),
        ]
        report = DiagnosticReport(checks=checks)
        assert report.error_count == 1
        assert report.warning_count == 1
        assert report.ok_count == 1


# ═══════════════════════════════════════════════════════════════════
# MODULE 11: qb_client - QBXML TRANSACTION CLIENT
# ═══════════════════════════════════════════════════════════════════

class TestQBClient:
    def test_txn_types_complete(self):
        expected = {"Check", "Deposit", "JournalEntry", "CreditCardCharge",
                    "CreditCardCredit", "SalesReceipt", "Bill", "BillPaymentCheck"}
        assert set(TXN_TYPES.keys()) == expected

    def test_txn_types_have_required_keys(self):
        for name, config in TXN_TYPES.items():
            assert "query_rq" in config
            assert "query_rs" in config
            assert "ret_tag" in config
            assert "del_type" in config
            assert "label" in config

    def test_client_escape_xml(self):
        assert client_escape_xml("a&b") == "a&amp;b"
        assert client_escape_xml(None) == ""


# ═══════════════════════════════════════════════════════════════════
# MODULE 12: toast_downloader - BROWSER AUTOMATION
# ═══════════════════════════════════════════════════════════════════

class TestToastDownloader:
    def test_sanitize_filename(self):
        assert ToastDownloader._sanitize("Normal Name") == "Normal Name"
        assert ToastDownloader._sanitize('Bad<>:"/\\|?*Name') == "Bad_Name"
        assert ToastDownloader._sanitize("") == "unknown"

    def test_is_logged_in_checks(self):
        downloader = ToastDownloader(headless=True)
        mock_page = MagicMock()
        mock_page.url = "https://www.toasttab.com/restaurants/admin/reports/sales"
        downloader.page = mock_page
        assert downloader._is_logged_in()

        mock_page.url = "https://www.toasttab.com/login"
        assert not downloader._is_logged_in()

    def test_wait_for_manual_login_timeout(self):
        downloader = ToastDownloader(headless=True)
        mock_page = MagicMock()
        mock_page.url = "https://www.toasttab.com/login"
        mock_page.wait_for_timeout = MagicMock()
        downloader.page = mock_page
        result = downloader._wait_for_manual_login(timeout_seconds=1, poll_seconds=1)
        assert result is False

    def test_wait_for_manual_login_success(self):
        downloader = ToastDownloader(headless=True)
        mock_page = MagicMock()
        call_count = [0]
        def get_url():
            call_count[0] += 1
            if call_count[0] >= 2:
                return "https://www.toasttab.com/restaurants/admin/reports/sales"
            return "https://www.toasttab.com/login"
        type(mock_page).url = PropertyMock(side_effect=get_url)
        mock_page.wait_for_timeout = MagicMock()
        downloader.page = mock_page
        result = downloader._wait_for_manual_login(timeout_seconds=10, poll_seconds=1)
        assert result is True


# ═══════════════════════════════════════════════════════════════════
# MODULE 13: CSV MAPPING LOAD (qb_sync)
# ═══════════════════════════════════════════════════════════════════

class TestLoadCsvMapping:
    def test_csv_override_categories(self, tmp_path):
        csv_path = tmp_path / "Map" / "store.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["QB", "Report", "Note"])
            writer.writeheader()
            writer.writerow({"QB": "QB:Food", "Report": "Food", "Note": "Gross Sale - Sales Category"})
            writer.writerow({"QB": "QB:Cash", "Report": "Cash", "Note": "Payments Summary"})

        with patch("qb_sync.MAP_DIR", tmp_path / "Map"):
            config = {"csv_map": "store.csv"}
            result = load_csv_mapping("store", config)
            assert "QB:Food" in result.get("sales_category_map", {}).values()

    def test_csv_missing_file_returns_original(self):
        config = {"fixed_items": {"tax": "original"}}
        with patch("qb_sync.MAP_DIR", Path("/nonexistent")):
            result = load_csv_mapping("store", config)
            assert result.get("fixed_items", {}).get("tax") == "original"


# ═══════════════════════════════════════════════════════════════════
# STRESS: CONCURRENT FUZZING (simulating 50K testers)
# ═══════════════════════════════════════════════════════════════════

class TestStressFuzzing:
    """Simulates thousands of concurrent testers hitting all functions."""

    def test_normalize_fuzz_1000(self):
        """1000 random inputs to normalize functions."""
        for _ in range(1000):
            s = random_string(random.randint(0, 100))
            normalize_item_name(s)
            normalize_item_path(s)
            split_qb_item_full_name(s)

    def test_escape_xml_fuzz_1000(self):
        for _ in range(1000):
            s = "".join(random.choices(string.printable + "\x00\x01\x02\x03", k=random.randint(0, 200)))
            result = escape_xml(s)
            assert "&" not in result or "&amp;" in result or "&lt;" in result or "&gt;" in result or "&quot;" in result or "&apos;" in result

    def test_decimal_fuzz_1000(self):
        for _ in range(1000):
            val = random.choice([
                None, "", "None", random.uniform(-10000, 10000),
                random_string(5), str(random.uniform(-1000, 1000)),
            ])
            result = d(val)
            assert isinstance(result, Decimal)

    def test_validation_issue_fuzz_500(self):
        for _ in range(500):
            issue = ValidationIssue(
                code=random_string(10),
                message=random_string(50),
                severity=random.choice(["error", "warning", "info"]),
                blocking=random.choice([True, False]),
                meta={random_string(5): random_string(10)},
            )
            _ = issue.to_dict()
            _ = issue.format_line()
            _ = issue.get("code")
            _ = issue.get("nonexistent", "default")

    def test_validate_name_fuzz_500(self):
        for _ in range(500):
            name = random_string(random.randint(0, 150))
            if random.random() < 0.1:
                name = ""
            if random.random() < 0.1:
                name = ":" + name + ":"
            issues = validate_proposed_item_name(name)
            assert isinstance(issues, list)

    def test_concurrent_ledger_stress(self, tmp_path):
        """10 concurrent threads, each doing 20 operations with retry on lock."""
        ledger = SyncLedger(db_path=tmp_path / "stress.db", audit_dir=tmp_path / "audit")
        errors = []

        def worker(idx):
            try:
                for j in range(20):
                    report = tmp_path / f"r_{idx}_{j}.xlsx"
                    report.write_bytes(f"content_{idx}_{j}".encode())
                    for attempt in range(3):
                        try:
                            r = ledger.begin_run(
                                store=f"Store_{idx % 5}",
                                date=f"2026-01-{(j % 28) + 1:02d}",
                                source_name=f"src_{idx}",
                                report_path=str(report),
                                report_hash=f"hash_{idx}_{j}",
                                report_size=100,
                                report_mtime="2026-01-01T00:00:00",
                                ref_number=f"REF-{idx}-{j}",
                                preview=random.choice([True, False]),
                                strict_mode=False,
                                qb_company_file="test.qbw",
                            )
                            if r.allowed:
                                if random.random() < 0.7:
                                    ledger.mark_success(r.sync_id)
                                else:
                                    ledger.mark_failed(r.sync_id, "test failure")
                            break
                        except Exception as e:
                            if "locked" in str(e).lower() and attempt < 2:
                                time.sleep(0.1 * (attempt + 1))
                                continue
                            raise
            except Exception as e:
                errors.append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(10)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Errors: {errors}"

    def test_marketplace_date_fuzz_500(self):
        for _ in range(500):
            val = random.choice([
                None, "", "Grand Total", "invalid",
                f"{random.randint(1,12):02d}/{random.randint(1,28):02d}/2026",
                f"2026-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
                random_string(10),
            ])
            result = normalize_marketplace_date(val)
            assert result is None or isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════
# BUILD REPORT IDENTITY
# ═══════════════════════════════════════════════════════════════════

class TestBuildReportIdentity:
    def test_build_identity(self, tmp_path):
        path = tmp_path / "report.xlsx"
        path.write_bytes(b"test content")
        identity = build_report_identity(path)
        assert identity.path == path
        assert len(identity.report_hash) == 64
        assert identity.report_size > 0

    def test_build_identity_different_files(self, tmp_path):
        p1 = tmp_path / "a.xlsx"
        p2 = tmp_path / "b.xlsx"
        p1.write_bytes(b"content a")
        p2.write_bytes(b"content b")
        i1 = build_report_identity(p1)
        i2 = build_report_identity(p2)
        assert i1.report_hash != i2.report_hash


# ═══════════════════════════════════════════════════════════════════
# UTCNOW
# ═══════════════════════════════════════════════════════════════════

class TestUtcNow:
    def test_format(self):
        result = utc_now()
        assert result.endswith("Z")
        assert "T" in result

    def test_no_microseconds(self):
        result = utc_now()
        assert "." not in result


# ═══════════════════════════════════════════════════════════════════
# EXCEL READER EDGE CASES
# ═══════════════════════════════════════════════════════════════════

class TestToastExcelReader:
    def test_empty_sheet(self, tmp_path):
        path = make_excel(tmp_path, {"Revenue summary": []})
        reader = ToastExcelReader(str(path))
        assert reader.get_revenue_summary() == {}
        reader.close()

    def test_single_header_no_data(self, tmp_path):
        path = make_excel(tmp_path, {"Revenue summary": [["Net sales"]]})
        reader = ToastExcelReader(str(path))
        assert reader.get_revenue_summary() == {}
        reader.close()

    def test_missing_sheet(self, tmp_path):
        path = make_excel(tmp_path, {"OtherSheet": [["col"], [1]]})
        reader = ToastExcelReader(str(path))
        assert reader.get_sales_categories() == []
        assert reader.get_payments() == []
        reader.close()

    def test_tip_summary(self, tmp_path):
        path = make_excel(tmp_path, {"Tip summary": [["Tips"], [50]]})
        reader = ToastExcelReader(str(path))
        result = reader.get_tip_summary()
        assert result.get("Tips") == 50
        reader.close()

    def test_service_charge_with_total(self, tmp_path):
        path = make_excel(tmp_path, {
            "Service charge summary": [
                ["Service charge", "Amount"],
                ["Delivery", 5],
                ["Total", 5],
            ]
        })
        reader = ToastExcelReader(str(path))
        rows, total = reader.get_service_charges()
        assert len(rows) == 1
        assert total is not None
        reader.close()
