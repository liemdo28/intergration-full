from __future__ import annotations

from datetime import UTC, datetime

from report_inventory import (
    build_report_coverage_summary,
    extract_business_dates_from_name,
    find_existing_local_report,
    list_missing_report_records,
    refresh_drive_report_inventory,
)


def test_extract_business_dates_supports_iso_and_us_patterns():
    dates = extract_business_dates_from_name("2026-02-25_OrderDetails_02-26-2026.csv")

    assert dates == ["2026-02-25", "2026-02-26"]


def test_find_existing_local_report_matches_date_in_folder(tmp_path):
    report_dir = tmp_path / "toast-reports" / "Stockton" / "Order Details"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "2026-02-25_OrderDetails_Stockton.csv"
    report_file.write_text("demo", encoding="utf-8")

    found = find_existing_local_report(
        tmp_path,
        store_name="Stockton",
        report_type="orders",
        business_date="2026-02-25",
    )

    assert found is not None
    assert found["filename"] == report_file.name


def test_missing_records_detect_internal_gap(tmp_path):
    report_dir = tmp_path / "toast-reports" / "Stockton" / "Order Details"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "2026-02-24_OrderDetails_Stockton.csv").write_text("demo", encoding="utf-8")
    (report_dir / "2026-02-26_OrderDetails_Stockton.csv").write_text("demo", encoding="utf-8")

    rows = list_missing_report_records(
        tmp_path,
        now=datetime(2026, 2, 27, 20, 0, tzinfo=UTC),
        max_items=20,
        store_names=["Stockton"],
        report_keys=["orders"],
    )

    assert any(row["business_date"] == "2026-02-25" for row in rows)


def test_build_report_coverage_summary_marks_missing_and_latest_file():
    inventory_rows = [
        {
            "store": "Stockton",
            "report_key": "orders",
            "report_label": "Order Details",
            "business_date": "2026-02-24",
            "filepath": "Toasttab/Stockton/Order Details/2026-02-24_OrderDetails_Stockton.csv",
            "filename": "2026-02-24_OrderDetails_Stockton.csv",
            "modified_at": "2026-02-24T10:00:00+00:00",
            "size_bytes": 100,
            "source": "drive_inventory",
        },
        {
            "store": "Stockton",
            "report_key": "orders",
            "report_label": "Order Details",
            "business_date": "2026-02-26",
            "filepath": "Toasttab/Stockton/Order Details/2026-02-26_OrderDetails_Stockton.csv",
            "filename": "2026-02-26_OrderDetails_Stockton.csv",
            "modified_at": "2026-02-26T10:00:00+00:00",
            "size_bytes": 100,
            "source": "drive_inventory",
        },
    ]
    missing_rows = [
        {
            "store": "Stockton",
            "report_key": "orders",
            "report_label": "Order Details",
            "business_date": "2026-02-25",
            "reason": "gap_inside_window",
            "detected_at": "2026-02-27T00:00:00+00:00",
            "download_supported": True,
        }
    ]

    summary = build_report_coverage_summary(
        inventory_rows,
        missing_rows,
        store_names=["Stockton"],
        report_keys=["orders"],
    )

    assert len(summary) == 1
    row = summary[0]
    assert row["health"] == "missing"
    assert row["missing_count"] == 1
    assert row["next_missing_date"] == "2026-02-25"
    assert row["last_date"] == "2026-02-26"
    assert row["latest_file_name"] == "2026-02-26_OrderDetails_Stockton.csv"


def test_refresh_drive_report_inventory_writes_summary_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "report-inventory.db"
    monkeypatch.setattr("report_inventory.INVENTORY_DB_PATH", db_path)

    inventory_rows = [
        {
            "store": "Stockton",
            "report_key": "sales_summary",
            "report_label": "Sale Summary",
            "business_date": "2026-02-26",
            "filepath": "Toasttab/Stockton/Sale Summary/2026-02-26_SalesSummary_Stockton.xlsx",
            "filename": "2026-02-26_SalesSummary_Stockton.xlsx",
            "modified_at": "2026-02-26T10:00:00+00:00",
            "size_bytes": 100,
            "source": "drive_inventory",
        }
    ]

    snapshot = refresh_drive_report_inventory(
        inventory_rows,
        now=datetime(2026, 2, 27, 20, 0, tzinfo=UTC),
        store_names=["Stockton"],
        report_keys=["sales_summary"],
    )

    assert "summary_rows" in snapshot
    assert snapshot["summary_rows"][0]["store"] == "Stockton"
    assert db_path.exists()
