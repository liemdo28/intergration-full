import json
from pathlib import Path

from mapping_maintenance import collect_mapping_candidates, upsert_candidate_mapping


def _write_mapping_config(path: Path):
    path.write_text(
        json.dumps(
            {
                "global": {},
                "stores": {
                    "Stockton": {
                        "csv_map": "raw.csv",
                        "additional_sale_receipts": [
                            {
                                "name": "Uber",
                                "csv_map": "uber_raw.csv",
                            }
                        ],
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_collect_mapping_candidates_splits_unmapped_category_issue(tmp_path):
    mapping_file = tmp_path / "qb-mapping.json"
    map_dir = tmp_path / "Map"
    map_dir.mkdir()
    _write_mapping_config(mapping_file)
    (map_dir / "raw.csv").write_text("QB,Report,Note\nExisting Item,Total of Cash,Payments Summary\n", encoding="utf-8")

    records = [
        {
            "store": "Stockton",
            "date": "2026-03-28",
            "issues": [
                {
                    "code": "unmapped_categories",
                    "message": "Unmapped sales categories: Sushi, Kitchen",
                    "categories": ["Sushi", "Kitchen"],
                },
                {
                    "code": "unmapped_payment_type",
                    "message": "Unmapped payment type: Cash",
                    "payment_type": "Cash",
                },
            ],
        }
    ]

    candidates = collect_mapping_candidates(records, mapping_file=mapping_file, map_dir=map_dir)

    assert len(candidates) == 3
    reports = {(item["report"], item["note"]) for item in candidates}
    assert ("Sushi", "Gross Sale") in reports
    assert ("Kitchen", "Gross Sale") in reports
    assert ("Total of Cash", "Payments Summary") in reports
    cash_candidate = next(item for item in candidates if item["report"] == "Total of Cash")
    assert cash_candidate["current_qb"] == "Existing Item"


def test_upsert_candidate_mapping_creates_new_csv_row(tmp_path):
    mapping_file = tmp_path / "qb-mapping.json"
    map_dir = tmp_path / "Map"
    map_dir.mkdir()
    _write_mapping_config(mapping_file)

    result = upsert_candidate_mapping(
        {
            "store": "Stockton",
            "report": "Sushi",
            "note": "Gross Sale",
        },
        "Toast:Sushi Sales",
        mapping_file=mapping_file,
        map_dir=map_dir,
    )

    csv_path = Path(result["path"])
    text = csv_path.read_text(encoding="utf-8")
    assert result["action"] == "created"
    assert "Toast:Sushi Sales,Sushi,Gross Sale" in text


def test_upsert_candidate_mapping_updates_existing_row(tmp_path):
    mapping_file = tmp_path / "qb-mapping.json"
    map_dir = tmp_path / "Map"
    map_dir.mkdir()
    _write_mapping_config(mapping_file)
    csv_path = map_dir / "raw.csv"
    csv_path.write_text(
        "QB,Report,Note\nOld Item,Sushi,Gross Sale\n",
        encoding="utf-8",
    )

    result = upsert_candidate_mapping(
        {
            "store": "Stockton",
            "report": "Sushi",
            "note": "Gross Sale",
        },
        "Toast:Sushi Sales",
        mapping_file=mapping_file,
        map_dir=map_dir,
    )

    text = csv_path.read_text(encoding="utf-8")
    assert result["action"] == "updated"
    assert "Toast:Sushi Sales,Sushi,Gross Sale" in text
    assert "Old Item,Sushi,Gross Sale" not in text


def test_collect_mapping_candidates_includes_marketplace_issue(tmp_path):
    mapping_file = tmp_path / "qb-mapping.json"
    map_dir = tmp_path / "Map"
    map_dir.mkdir()
    _write_mapping_config(mapping_file)
    (map_dir / "raw.csv").write_text("QB,Report,Note\n", encoding="utf-8")
    (map_dir / "uber_raw.csv").write_text(
        "QB,Column,Type\nUE Payout,Sum of Total payout,payment\n",
        encoding="utf-8",
    )

    records = [
        {
            "store": "Stockton",
            "date": "2026-03-28",
            "source": "Uber",
            "issues": [
                {
                    "code": "marketplace_invalid_mapping_type",
                    "message": "Uber mapping has invalid Type: payout",
                    "column": "Sum of Total payout",
                    "qb_item": "UE Payout",
                    "mapping_type": "payout",
                    "source_name": "Uber",
                }
            ],
        }
    ]

    candidates = collect_mapping_candidates(records, mapping_file=mapping_file, map_dir=map_dir)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["map_kind"] == "marketplace"
    assert candidate["source_name"] == "Uber"
    assert candidate["current_qb"] == "UE Payout"
    assert candidate["map_path"].endswith("uber_raw.csv")


def test_upsert_candidate_mapping_updates_marketplace_row(tmp_path):
    mapping_file = tmp_path / "qb-mapping.json"
    map_dir = tmp_path / "Map"
    map_dir.mkdir()
    _write_mapping_config(mapping_file)
    csv_path = map_dir / "uber_raw.csv"
    csv_path.write_text(
        "QB,Column,Type\nUE Payout,Wrong Column,payout\n",
        encoding="utf-8",
    )

    result = upsert_candidate_mapping(
        {
            "store": "Stockton",
            "source_name": "Uber",
            "map_kind": "marketplace",
            "report": "Sum of Total payout",
            "mapping_type": "payment",
            "meta": {"qb_item": "UE Payout"},
        },
        "UE Payout",
        override_report="Sum of Total payout",
        override_type="payment",
        mapping_file=mapping_file,
        map_dir=map_dir,
    )

    text = csv_path.read_text(encoding="utf-8")
    assert result["action"] == "updated"
    assert "UE Payout,Sum of Total payout,payment" in text
    assert "Wrong Column" not in text
