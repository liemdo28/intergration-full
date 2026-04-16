"""Service layer for download reports — thin orchestration wrapper."""
from __future__ import annotations
import threading
from datetime import datetime, timedelta
from models.download_result import DownloadResult, DownloadFileResult


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


def estimate_download_count(stores: list, date_start: str, date_end: str, report_types: list) -> int:
    return len(stores) * len(get_date_list(date_start, date_end)) * len(report_types)


def run_download(
    stores: list,
    date_start: str,
    date_end: str,
    report_types: list,
    on_progress=None,
    stop_event=None,
) -> DownloadResult:
    """Run the download workflow and return a DownloadResult."""
    result = DownloadResult(
        stores=stores,
        date_start=date_start,
        date_end=date_end,
        report_types=report_types,
        started_at=datetime.utcnow().isoformat(),
    )

    def _log(msg):
        if callable(on_progress):
            on_progress(msg)

    date_list = get_date_list(date_start, date_end)
    if not date_list:
        result.warnings.append("No valid dates in range.")
        result.finished_at = datetime.utcnow().isoformat()
        return result

    date_strs = []
    for d in date_list:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            date_strs.append(dt.strftime("%m/%d/%Y"))
        except Exception:
            pass

    try:
        from toast_downloader import ToastDownloader

        for store in stores:
            if stop_event and stop_event.is_set():
                _log(f"Stopped before {store}")
                break

            _log(f"Starting download for {store}...")
            try:
                downloader = ToastDownloader(
                    location=store,
                    report_keys=report_types,
                    dates=date_strs,
                    on_log=_log,
                    stop_event=stop_event,
                )
                run_res = downloader.download_reports_daterange()
                success = run_res.get("success", 0)
                fail = run_res.get("fail", 0)
                _log(f"{store}: {success} downloaded, {fail} failed")
                for d in date_list:
                    for rt in report_types:
                        result.files.append(DownloadFileResult(
                            store=store, date=d, report_type=rt,
                            success=(fail == 0),
                        ))
            except Exception as e:
                _log(f"{store}: Error — {e}")
                result.warnings.append(f"{store}: {e}")
                for d in date_list:
                    for rt in report_types:
                        result.files.append(DownloadFileResult(
                            store=store, date=d, report_type=rt,
                            success=False, error=str(e)
                        ))
    except Exception as e:
        result.warnings.append(f"Download engine error: {e}")

    result.finished_at = datetime.utcnow().isoformat()
    return result
