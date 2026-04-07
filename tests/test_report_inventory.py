from __future__ import annotations

from datetime import UTC, datetime

from report_inventory import (
    extract_business_dates_from_name,
    find_existing_local_report,
    list_missing_report_records,
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
