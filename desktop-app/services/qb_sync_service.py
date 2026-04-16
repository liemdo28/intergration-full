"""Service layer for QB sync workflow."""
from __future__ import annotations
from datetime import datetime, timedelta


def run_qb_sync(
    stores: list,
    date_start: str,
    date_end: str,
    on_progress=None,
    stop_event=None,
) -> dict:
    """Run QB sync. Returns result dict."""
    result = {
        "ok": False,
        "success_count": 0,
        "fail_count": 0,
        "warnings": [],
        "entry_count": 0,
        "total_amount": 0.0,
        "started_at": datetime.utcnow().isoformat(),
        "finished_at": "",
    }

    def _log(msg):
        if callable(on_progress):
            on_progress(msg)

    try:
        import json
        from pathlib import Path
        from app_paths import RUNTIME_DIR

        config = {}
        cfg_path = RUNTIME_DIR / "local-config.json"
        if cfg_path.exists():
            try:
                config = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        qbw_paths = config.get("qbw_paths", {})

        s = datetime.strptime(date_start, "%Y-%m-%d").date()
        e = datetime.strptime(date_end, "%Y-%m-%d").date()

        for store in stores:
            if stop_event and stop_event.is_set():
                _log(f"Stopped before {store}")
                break

            qbw_path = qbw_paths.get(store, "")
            if not qbw_path:
                result["warnings"].append(f"{store}: No QB company file configured")
                result["fail_count"] += 1
                _log(f"{store}: Skipped — no QB company file configured")
                continue

            _log(f"Syncing {store}...")
            cur = s
            while cur <= e:
                if stop_event and stop_event.is_set():
                    break
                d_str = cur.strftime("%m/%d/%Y")
                _log(f"  {store} / {cur}...")
                try:
                    from gdrive_service import GDriveService
                    from qb_sync import QBSyncClient
                    drive = GDriveService()
                    local_path = drive.download_report(store, "sales_summary", d_str)
                    if local_path:
                        client = QBSyncClient(qbw_path=qbw_path, store_name=store)
                        sync_res = client.sync_date(local_path, d_str, on_log=_log)
                        if sync_res.get("ok"):
                            result["success_count"] += 1
                            result["entry_count"] += sync_res.get("receipt_count", 0)
                            result["total_amount"] += sync_res.get("total_amount", 0.0)
                        else:
                            result["fail_count"] += 1
                            result["warnings"].append(
                                f"{store}/{cur}: {sync_res.get('error', 'sync failed')}"
                            )
                    else:
                        result["fail_count"] += 1
                        result["warnings"].append(f"{store}/{cur}: File not found in Drive")
                except Exception as exc:
                    result["fail_count"] += 1
                    result["warnings"].append(f"{store}/{cur}: {exc}")
                cur += timedelta(days=1)

        result["ok"] = result["fail_count"] == 0

    except Exception as exc:
        result["warnings"].append(f"QB sync engine error: {exc}")

    result["finished_at"] = datetime.utcnow().isoformat()
    return result
