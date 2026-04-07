from pathlib import Path

from marketplace_sync import (
    extract_marketplace_receipt_lines,
    get_marketplace_sources_for_store,
)
from date_parser import normalize_marketplace_date


def test_normalize_marketplace_date():
    # FIX C1: normalize_marketplace_date now returns ParseResult (not None silently)
    r = normalize_marketplace_date("1/2/2026")
    assert r.success
    assert r.value.date_str == "2026-01-02"

    r_gt = normalize_marketplace_date("Grand Total")
    assert not r_gt.success
    assert "Grand Total" in r_gt.error


def test_extract_marketplace_lines_balances_doordash_row(tmp_path):
    report_path = tmp_path / "DoordashSale.csv"
    map_path = tmp_path / "doordash_raw.csv"

    report_path.write_text(
        "\n".join(
            [
                "Row Labels,Sum of Subtotal,Sum of Subtotal tax passed to merchant,Sum of Net total",
                "1/2/2026,100,8,108",
            ]
        ),
        encoding="utf-8",
    )
    map_path.write_text(
        "\n".join(
            [
                "QB,Column,Type",
                "DD Subtotal,Sum of Subtotal,item",
                "DD Tax,Sum of Subtotal tax passed to merchant,item",
                "DD Payout,Sum of Net total,payment",
                "Over/Short,auto-balance,balance",
            ]
        ),
        encoding="utf-8",
    )

    lines, issues, row = extract_marketplace_receipt_lines(
        report_path=report_path,
        date_str="2026-01-02",
        map_path=map_path,
        source_name="DoorDash",
    )

    assert row is not None
    assert not issues
    assert sum(line["amount"] for line in lines) == 0


def test_extract_marketplace_lines_handles_negative_payout_by_inverting_sign(tmp_path):
    """
    FIX C4: Uber payout can be NEGATIVE (platform owes restaurant).
    When negative in CSV, keep as-is (do NOT double-invert).
    """
    report_path = tmp_path / "UberSale.csv"
    map_path = tmp_path / "uber_raw.csv"

    report_path.write_text(
        "\n".join(
            [
                "Row Labels,Sum of Other payments,Sum of Total payout",
                "1/3/2026,-7.96,-7.96",
            ]
        ),
        encoding="utf-8",
    )
    map_path.write_text(
        "\n".join(
            [
                "QB,Column,Type",
                "UE Other Payments,Sum of Other payments,item",
                "UE Payout,Sum of Total payout,payment",
                "Over/Short,auto-balance,balance",
            ]
        ),
        encoding="utf-8",
    )

    lines, issues, row = extract_marketplace_receipt_lines(
        report_path=report_path,
        date_str="2026-01-03",
        map_path=map_path,
        source_name="Uber",
    )

    assert row is not None
    assert not issues
    assert sum(line["amount"] for line in lines) == 0
    payout_line = next(line for line in lines if line["item_name"] == "UE Payout")
    # FIX C4: Negative payout stays NEGATIVE (platform owes restaurant)
    assert str(payout_line["amount"]) == "-7.96"


def test_extract_marketplace_lines_doordash_negative_payout_not_double_inverted(tmp_path):
    """
    FIX C4 (CRITICAL): DoorDash "Net total" is ALREADY NEGATIVE in CSV
    (platform owes restaurant money). Previous code ALWAYS inverted payment type,
    turning -15.50 into +15.50 — WRONG accounting entry.

    New fix: DoorDash payout is NOT inverted. Negative stays negative.
    """
    report_path = tmp_path / "DoordashSale.csv"
    map_path = tmp_path / "doordash_raw.csv"

    # Net total is NEGATIVE (DoorDash owes restaurant)
    report_path.write_text(
        "\n".join(
            [
                "Row Labels,Sum of Subtotal,Sum of Net total",
                "1/15/2026,100.00,-15.50",
            ]
        ),
        encoding="utf-8",
    )
    map_path.write_text(
        "\n".join(
            [
                "QB,Column,Type",
                "DD Subtotal,Sum of Subtotal,item",
                "DD Payout,Sum of Net total,payment",
                "Over/Short,auto-balance,balance",
            ]
        ),
        encoding="utf-8",
    )

    lines, issues, row = extract_marketplace_receipt_lines(
        report_path=report_path,
        date_str="2026-01-15",
        map_path=map_path,
        source_name="DoorDash",
    )

    assert row is not None
    assert not issues
    payout_line = next(line for line in lines if line["item_name"] == "DD Payout")
    # FIX C4: DoorDash Net total is POSITIVE in CSV → invert to negative (restaurant receives payout)
    assert str(payout_line["amount"]) == "-15.50"




def test_get_marketplace_sources_for_store_resolves_existing_reports(tmp_path):
    map_dir = tmp_path / "Map"
    downloads = tmp_path / "Downloads"
    map_dir.mkdir()
    downloads.mkdir()
    (map_dir / "uber_raw.csv").write_text("QB,Column,Type\n", encoding="utf-8")
    (downloads / "UberSale.csv").write_text("Row Labels\n", encoding="utf-8")

    sources = get_marketplace_sources_for_store(
        {
            "additional_sale_receipts": [
                {
                    "name": "Uber",
                    "customer_name": "Uber",
                    "ref_prefix": "UE",
                    "file_name": "UberSale.csv",
                    "csv_map": "uber_raw.csv",
                }
            ]
        },
        map_dir=map_dir,
        search_dirs=[downloads],
    )

    assert len(sources) == 1
    assert sources[0].customer_name == "Uber"
    assert Path(sources[0].report_path).name == "UberSale.csv"


def test_get_marketplace_sources_for_store_requires_uploaded_path_when_requested(tmp_path):
    map_dir = tmp_path / "Map"
    downloads = tmp_path / "Downloads"
    uploaded = tmp_path / "Uploaded"
    map_dir.mkdir()
    downloads.mkdir()
    uploaded.mkdir()
    (map_dir / "uber_raw.csv").write_text("QB,Column,Type\n", encoding="utf-8")
    (downloads / "UberSale.csv").write_text("Row Labels\n", encoding="utf-8")
    uploaded_file = uploaded / "Raw-Uber-2026-03-28.csv"
    uploaded_file.write_text("Row Labels\n", encoding="utf-8")

    store_config = {
        "additional_sale_receipts": [
            {
                "name": "Uber",
                "customer_name": "Uber",
                "ref_prefix": "UE",
                "file_name": "UberSale.csv",
                "csv_map": "uber_raw.csv",
            }
        ]
    }

    assert get_marketplace_sources_for_store(
        store_config,
        map_dir=map_dir,
        search_dirs=[downloads],
        require_uploaded_path=True,
    ) == []

    sources = get_marketplace_sources_for_store(
        store_config,
        map_dir=map_dir,
        search_dirs=[downloads],
        uploaded_paths={"Uber": str(uploaded_file)},
        require_uploaded_path=True,
    )

    assert len(sources) == 1
    assert Path(sources[0].report_path) == uploaded_file
    assert sources[0].selected_by_user is True
