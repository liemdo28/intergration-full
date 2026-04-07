from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from integration_status import (
    build_integration_snapshot,
    get_auto_download_plan,
    get_auto_qb_sync_plan,
    get_safe_target_date,
)


def _write_download_manifest(base_dir, *, store, report_key, business_date):
    audit_dir = base_dir / "audit-logs" / "download-reports"
    audit_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = base_dir / "toast-reports" / store / "Sale Summary"
    reports_dir.mkdir(parents=True, exist_ok=True)
    filepath = reports_dir / f"SalesSummary_{business_date}_{business_date}.xlsx"
    filepath.write_bytes(b"demo")
    manifest = {
        "generated_at": "20260407-120000",
        "results": {
            "files": [
                {
                    "location": store,
                    "report_key": report_key,
                    "filepath": str(filepath),
                }
            ]
        },
        "attempts": [
            {
                "location": store,
                "report_type": report_key,
                "date": datetime.strptime(business_date, "%Y-%m-%d").strftime("%m/%d/%Y"),
                "success": True,
            }
        ],
    }
    (audit_dir / "download-run-20260407-120000.json").write_text(json.dumps(manifest), encoding="utf-8")


def _write_sync_ledger(base_dir, *, store, business_date, status="success", source_name="Toasttab"):
    db_path = base_dir / "sync-ledger.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE sync_runs (
                sync_id TEXT PRIMARY KEY,
                store TEXT NOT NULL,
                date TEXT NOT NULL,
                source_name TEXT NOT NULL DEFAULT '',
                report_path TEXT NOT NULL,
                report_hash TEXT NOT NULL,
                report_size INTEGER NOT NULL,
                report_mtime TEXT NOT NULL,
                ref_number TEXT,
                preview INTEGER NOT NULL,
                strict_mode INTEGER NOT NULL,
                qb_company_file TEXT,
                status TEXT NOT NULL,
                validation_error_count INTEGER NOT NULL DEFAULT 0,
                validation_warning_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error_message TEXT,
                override_reason TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sync_runs (
                sync_id, store, date, source_name, report_path, report_hash, report_size, report_mtime,
                ref_number, preview, strict_mode, qb_company_file, status, validation_error_count,
                validation_warning_count, started_at, finished_at, error_message, override_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"sync-{store}-{business_date}",
                store,
                business_date,
                source_name,
                "demo.xlsx",
                "hash",
                1,
                "2026-04-06T00:00:00Z",
                "1",
                0,
                1,
                "",
                status,
                0,
                0,
                "2026-04-07T01:00:00Z",
                "2026-04-07T01:01:00Z",
                "" if status == "success" else "failed",
                "",
            ),
        )
        conn.commit()


def test_get_safe_target_date_uses_store_timezone():
    now = datetime(2026, 4, 7, 6, 30, tzinfo=UTC)
    assert get_safe_target_date(["Stockton"], include_today=False, now=now) == "2026-04-05"
    assert get_safe_target_date(["Stockton"], include_today=True, now=now) == "2026-04-06"


def test_auto_download_and_qb_plans_follow_latest_history(tmp_path):
    _write_download_manifest(tmp_path, store="Stockton", report_key="sales_summary", business_date="2026-04-04")
    _write_sync_ledger(tmp_path, store="Stockton", business_date="2026-04-03")

    now = datetime(2026, 4, 7, 18, 0, tzinfo=UTC)

    download_plan = get_auto_download_plan(
        ["Stockton"],
        ["sales_summary"],
        include_today=False,
        base_dir=tmp_path,
        now=now,
    )
    assert download_plan["has_gap"] is True
    assert download_plan["start_date"] == "2026-04-05"
    assert download_plan["end_date"] == "2026-04-06"

    qb_plan = get_auto_qb_sync_plan(
        ["Stockton"],
        include_today=False,
        base_dir=tmp_path,
        now=now,
    )
    assert qb_plan["has_gap"] is True
    assert qb_plan["start_date"] == "2026-04-04"
    assert qb_plan["end_date"] == "2026-04-04"


def test_build_integration_snapshot_includes_suggestions(tmp_path):
    _write_download_manifest(tmp_path, store="Stockton", report_key="sales_summary", business_date="2026-04-05")
    _write_sync_ledger(tmp_path, store="Stockton", business_date="2026-04-04", status="failed")

    snapshot = build_integration_snapshot(base_dir=tmp_path, now=datetime(2026, 4, 7, 20, 0, tzinfo=UTC))

    assert snapshot["summary"]["download_rows"] >= 1
    assert snapshot["latest_downloads"][0]["store"] == "Stockton"
    assert any(item["kind"] == "download_gap" for item in snapshot["ai_suggestions"])


def test_build_integration_snapshot_canonicalizes_legacy_report_keys(tmp_path):
    _write_download_manifest(tmp_path, store="Stockton", report_key="payment", business_date="2026-04-05")

    snapshot = build_integration_snapshot(base_dir=tmp_path, now=datetime(2026, 4, 7, 20, 0, tzinfo=UTC))

    assert snapshot["latest_downloads"][0]["report_key"] == "payments"
