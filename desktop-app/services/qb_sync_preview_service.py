"""QB Sync preview service — estimates what would be synced."""
from __future__ import annotations
from datetime import datetime, timedelta
from models.qb_sync_preview import QBSyncPreview, QBSyncPreviewEntry


def get_date_list(date_start: str, date_end: str) -> list:
    try:
        s = datetime.strptime(date_start, "%Y-%m-%d").date()
        e = datetime.strptime(date_end, "%Y-%m-%d").date()
        result = []
        cur = s
        while cur <= e:
            result.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return result
    except Exception:
        return []


def build_qb_sync_preview(stores: list, date_start: str, date_end: str) -> QBSyncPreview:
    preview = QBSyncPreview(stores=stores, date_start=date_start, date_end=date_end)

    if not stores:
        preview.can_proceed = False
        preview.block_reason = "No stores selected."
        return preview

    date_list = get_date_list(date_start, date_end)
    if not date_list:
        preview.can_proceed = False
        preview.block_reason = "Invalid date range."
        return preview

    for store in stores:
        for d in date_list:
            entry = QBSyncPreviewEntry(
                store=store,
                date=d,
                file_name=f"Sale Summary {d}.xlsx",
                estimated_receipts=1,
                total_amount=0.0,
            )
            try:
                from gdrive_service import GDriveService
                drive = GDriveService()
                dt = datetime.strptime(d, "%Y-%m-%d")
                date_str = dt.strftime("%m/%d/%Y")
                file_info = drive.find_report_file(store, "sales_summary", date_str)
                if file_info:
                    entry.file_name = file_info.get("name", entry.file_name)
                else:
                    entry.warnings.append("File not found in Drive")
                    preview.warnings.append(f"{store} / {d}: report not found in Drive")
            except Exception as e:
                entry.warnings.append(f"Drive check skipped: {e}")
            preview.entries.append(entry)

    preview.total_estimated_receipts = sum(e.estimated_receipts for e in preview.entries)
    preview.total_estimated_amount = sum(e.total_amount for e in preview.entries)
    return preview


def get_preview_summary_text(preview: QBSyncPreview) -> str:
    n = len(preview.entries)
    w = len(preview.warnings)
    lines = [f"{n} date/store combinations queued for sync"]
    if w:
        lines.append(f"{w} warning(s) — review before confirming")
    return "\n".join(lines)
