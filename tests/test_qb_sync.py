from decimal import Decimal
from pathlib import Path

import openpyxl

import qb_sync


def _write_sheet(workbook, title, rows, total_row=None):
    sheet = workbook.create_sheet(title)
    if rows:
        headers = list(rows[0].keys())
    elif total_row:
        headers = list(total_row.keys())
    else:
        headers = ["Name", "Amount"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    if total_row:
        sheet.append([total_row.get(header) for header in headers])


def _build_report(path: Path, *, revenue, net_sales, categories, payments, tax_rows=None, service_total=None):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _write_sheet(wb, "Revenue summary", [revenue])
    _write_sheet(wb, "Net sales summary", [net_sales])
    _write_sheet(wb, "Sales category summary", categories)
    _write_sheet(wb, "Payments summary", payments)
    _write_sheet(wb, "Tax summary", tax_rows or [])
    _write_sheet(wb, "Tip summary", [])
    _write_sheet(wb, "Service charge summary", [], total_row=service_total or {"Name": "Total", "Amount": 0})
    wb.save(path)


def _extract_amounts(lines):
    return {line["item_name"]: Decimal(str(line["amount"])) for line in lines}


def test_gratuity_is_not_double_counted_when_mapped_separately(tmp_path):
    report_path = tmp_path / "SalesSummary_2026-03-28_2026-03-28.xlsx"
    _build_report(
        report_path,
        revenue={"Tax amount": 0, "Tips": 10, "Gratuity": 5, "Deferred (gift cards)": 0},
        net_sales={"Sales discounts": 0, "Sales refunds": 0},
        categories=[{"Sales category": "Food", "Net sales": 100, "Gross sales": 100}],
        payments=[{"Payment type": "Cash", "Payment sub type": "", "Total": 115}],
    )

    store_config = {
        "sales_category_map": {"Food": "Food Sales"},
        "payment_map": {"Cash": "Cash Drawer"},
        "fixed_items": {
            "tips": "Tips Payable",
            "gratuity": "Gratuity Payable",
            "tips_includes_gratuity": True,
            "over_short": "Over/Short",
        },
    }

    reader = qb_sync.ToastExcelReader(report_path)
    lines = qb_sync.extract_receipt_lines(reader, store_config)
    amounts = _extract_amounts(lines)

    assert amounts["Food Sales"] == Decimal("100.00")
    assert amounts["Tips Payable"] == Decimal("10.00")
    assert amounts["Gratuity Payable"] == Decimal("5.00")
    assert amounts["Cash Drawer"] == Decimal("-115.00")
    assert "Over/Short" not in amounts


def test_gratuity_merges_into_tips_when_not_mapped_separately(tmp_path):
    report_path = tmp_path / "SalesSummary_2026-03-28_2026-03-28.xlsx"
    _build_report(
        report_path,
        revenue={"Tax amount": 0, "Tips": 10, "Gratuity": 5, "Deferred (gift cards)": 0},
        net_sales={"Sales discounts": 0, "Sales refunds": 0},
        categories=[{"Sales category": "Food", "Net sales": 100, "Gross sales": 100}],
        payments=[{"Payment type": "Cash", "Payment sub type": "", "Total": 115}],
    )

    store_config = {
        "sales_category_map": {"Food": "Food Sales"},
        "payment_map": {"Cash": "Cash Drawer"},
        "fixed_items": {
            "tips": "Tips Payable",
            "tips_includes_gratuity": True,
            "over_short": "Over/Short",
        },
    }

    reader = qb_sync.ToastExcelReader(report_path)
    lines = qb_sync.extract_receipt_lines(reader, store_config)
    amounts = _extract_amounts(lines)

    assert amounts["Tips Payable"] == Decimal("15.00")
    assert "Gratuity Payable" not in amounts


def test_over_short_balances_unmatched_payments(tmp_path):
    report_path = tmp_path / "SalesSummary_2026-03-28_2026-03-28.xlsx"
    _build_report(
        report_path,
        revenue={"Tax amount": 0, "Tips": 0, "Gratuity": 0, "Deferred (gift cards)": 0},
        net_sales={"Sales discounts": 0, "Sales refunds": 0},
        categories=[{"Sales category": "Food", "Net sales": 100, "Gross sales": 100}],
        payments=[{"Payment type": "Cash", "Payment sub type": "", "Total": 90}],
    )

    store_config = {
        "sales_category_map": {"Food": "Food Sales"},
        "payment_map": {"Cash": "Cash Drawer"},
        "fixed_items": {"over_short": "Over/Short"},
    }

    reader = qb_sync.ToastExcelReader(report_path)
    lines = qb_sync.extract_receipt_lines(reader, store_config)
    amounts = _extract_amounts(lines)

    assert amounts["Food Sales"] == Decimal("100.00")
    assert amounts["Cash Drawer"] == Decimal("-90.00")
    assert amounts["Over/Short"] == Decimal("-10.00")


def test_check_exists_matches_exact_date():
    client = qb_sync.QBSyncClient()

    client._send = lambda _qbxml: """<?xml version="1.0" encoding="utf-8"?>
<?qbxml version="13.0"?>
<QBXML>
  <QBXMLMsgsRs>
    <SalesReceiptQueryRs statusCode="0" statusSeverity="Info" statusMessage="Status OK">
      <SalesReceiptRet>
        <TxnID>ABC123</TxnID>
        <TxnDate>2026-03-27</TxnDate>
        <RefNumber>20260328</RefNumber>
      </SalesReceiptRet>
    </SalesReceiptQueryRs>
  </QBXMLMsgsRs>
</QBXML>"""

    assert client.check_exists("2026-03-28", "20260328") is False
    assert client.check_exists("2026-03-27", "20260328") is True


def test_load_csv_mapping_overrides_store_config(tmp_path, monkeypatch):
    map_dir = tmp_path / "Map"
    map_dir.mkdir()
    csv_path = map_dir / "demo.csv"
    csv_path.write_text(
        "QB,Report,Note\n"
        "Food Sales,Ramen,Sales Category\n"
        "Discounts,Sales discounts,Net Sales Summary\n"
        "Tips Payable,Tips,Revenue Summary\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(qb_sync, "MAP_DIR", map_dir)
    store_config = {"csv_map": "demo.csv", "fixed_items": {}}

    updated = qb_sync.load_csv_mapping("Demo", store_config)

    assert updated["sales_category_map"]["Ramen"] == "Food Sales"
    assert updated["fixed_items"]["discounts"] == "Discounts"
    assert updated["fixed_items"]["tips"] == "Tips Payable"


def test_find_report_file_supports_nested_sale_summary_folder(tmp_path, monkeypatch):
    nested_dir = tmp_path / "Stockton" / "Sale Summary"
    nested_dir.mkdir(parents=True)
    report_path = nested_dir / "SalesSummary_2026-03-28_2026-03-28.xlsx"
    report_path.write_bytes(b"placeholder")

    monkeypatch.setattr(qb_sync, "REPORTS_DIR", tmp_path)

    files = qb_sync.find_report_file(
        "Stockton",
        {"toast_location": "Stockton"},
        "2026-03-28",
    )

    assert len(files) == 1
    assert files[0]["filepath"] == report_path
