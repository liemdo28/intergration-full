from pathlib import Path

import openpyxl

from report_validator import validate_toast_report_file


def _build_workbook(path: Path, include_required=True):
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    sheets = [
        "Revenue summary",
        "Net sales summary",
        "Sales category summary",
        "Payments summary",
    ] if include_required else ["Revenue summary"]
    for sheet_name in sheets:
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(["Name", "Amount"])
        sheet.append(["Sample", 1])
    workbook.save(path)


def _build_tabular_workbook(path: Path, sheet_name="Sheet1"):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(["Order #", "Amount"])
    sheet.append([1001, 42.5])
    workbook.save(path)


def test_validate_toast_report_file_accepts_valid_workbook(tmp_path):
    path = tmp_path / "SalesSummary_2026-03-28_2026-03-28.xlsx"
    _build_workbook(path)

    result = validate_toast_report_file(path)

    assert result.ok is True
    assert result.errors == []
    assert result.checksum_sha256


def test_validate_toast_report_file_rejects_missing_required_sheets(tmp_path):
    path = tmp_path / "SalesSummary_2026-03-28_2026-03-28.xlsx"
    _build_workbook(path, include_required=False)

    result = validate_toast_report_file(path)

    assert result.ok is False
    assert any("Missing required sheets" in error for error in result.errors)


def test_validate_toast_report_file_accepts_item_detail_workbook(tmp_path):
    path = tmp_path / "ItemDetails_2026-03-28.xlsx"
    _build_tabular_workbook(path, sheet_name="Item Details")

    result = validate_toast_report_file(path, report_type="item_detail")

    assert result.ok is True
    assert result.errors == []


def test_validate_toast_report_file_accepts_payment_csv(tmp_path):
    path = tmp_path / "Payments_2026-03-28.csv"
    path.write_text("Payment Type,Amount\nCard,125.50\n", encoding="utf-8")

    result = validate_toast_report_file(path, report_type="payment")

    assert result.ok is True
    assert result.available_sheets == ["CSV"]
