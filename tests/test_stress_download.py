"""
Stress test: 100 concurrent testers downloading Sale Summary + Order Details
for all 7 stores x 5 dates (Apr 1-5, 2026).

Pass criteria:
  - Each tester: 70 tasks, 70 success, 0 fail
  - Per-store: exactly 5 files per report type
  - _open_report_view called 14 times (7 stores x 2 types), NOT 70
  - Missing 1 report for any store = FAIL
"""

from __future__ import annotations

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

import toast_downloader


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_LOCATIONS = list(toast_downloader.TOAST_LOCATIONS)  # 7 stores
REPORT_TYPES = ["sales_summary", "orders"]               # 2 report types
DATES = [f"04/0{d}/2026" for d in range(1, 6)]           # 5 dates
# WA1 skips date entry, so effective date selections = (7 - 1) * 2 * 5 + 1 * 2 * 0 = 60
# But downloads still happen for WA1 (using page's current date).
WA1_SKIP_COUNT = len(REPORT_TYPES) * len(DATES)          # 10
TOTAL_TASKS = len(ALL_LOCATIONS) * len(REPORT_TYPES) * len(DATES)  # 70
NUM_TESTERS = 100


# ---------------------------------------------------------------------------
# Helper: per-tester tracker
# ---------------------------------------------------------------------------

class _TesterTracker:
    def __init__(self, tester_id: int):
        self.tester_id = tester_id
        self.logs: list[str] = []
        self.progress: list[tuple[int, int, str]] = []
        self.downloads: list[dict] = []
        self.open_report_calls: list[str] = []
        self.switch_location_calls: list[str] = []
        self.date_calls: list[str] = []


# ---------------------------------------------------------------------------
# Run one tester
# ---------------------------------------------------------------------------

def _run_single_tester(tester_id: int) -> tuple[int, dict, _TesterTracker]:
    tracker = _TesterTracker(tester_id)

    downloader = toast_downloader.ToastDownloader(
        download_dir=f"E:/fake_downloads/tester_{tester_id}",
        on_log=tracker.logs.append,
        on_progress=lambda cur, total, msg: tracker.progress.append((cur, total, msg)),
    )

    # --- Mock page ---
    class _Page:
        url = "https://www.toasttab.com/restaurants/admin/reports/sales/sales-summary"
        def wait_for_timeout(self, ms): pass
        def wait_for_load_state(self, *a, **kw): pass

    downloader.page = _Page()
    downloader.context = type("Ctx", (), {"storage_state": lambda self, **kw: None})()

    # --- Mock browser lifecycle ---
    downloader._start_browser = lambda: None
    downloader._login = lambda: None
    downloader.close = lambda: None
    downloader._dismiss_overlays = lambda: None

    # --- Mock location switch ---
    def _fake_switch(loc):
        tracker.switch_location_calls.append(loc)
        return True
    downloader._switch_location_with_retries = _fake_switch

    # --- Mock report view ---
    def _fake_open_report(report_type):
        tracker.open_report_calls.append(report_type)
    downloader._open_report_view = _fake_open_report

    # --- Mock date selection ---
    def _fake_select_date(date_str):
        tracker.date_calls.append(date_str)
        return True
    downloader._select_custom_date = _fake_select_date

    # --- Mock report ready ---
    downloader._wait_for_report_ready = lambda **kw: "ready"
    downloader._verified_report_state = lambda state, *a: state

    # --- Mock download ---
    def _fake_download(save_dir, report_type="sales_summary", store_name=None, business_date=None):
        tracker.downloads.append({
            "store": store_name,
            "report_type": report_type,
            "business_date": business_date,
        })
        return {
            "filepath": f"{save_dir}/{business_date}_{report_type}_{store_name}.xlsx",
            "filename": f"{business_date}_{report_type}_{store_name}.xlsx",
            "validation": {"ok": True, "checksum_sha256": "abc123", "errors": [], "warnings": []},
        }
    downloader._download_report = _fake_download

    # --- Mock file system operations ---
    with patch("os.makedirs"), \
         patch("toast_downloader.build_local_report_dir", return_value="E:/fake"), \
         patch("toast_downloader.find_existing_local_report", return_value=None):
        results = downloader.download_reports_daterange(
            locations=ALL_LOCATIONS,
            dates=DATES,
            report_types=REPORT_TYPES,
        )

    return tester_id, results, tracker


# ===========================================================================
# Tests
# ===========================================================================

def test_100_testers_all_succeed():
    """All 100 testers should complete 70/70 downloads with 0 failures."""
    all_results: dict[int, dict] = {}

    with ThreadPoolExecutor(max_workers=NUM_TESTERS) as pool:
        futures = {pool.submit(_run_single_tester, i): i for i in range(NUM_TESTERS)}
        for future in as_completed(futures):
            tester_id, results, _ = future.result()
            all_results[tester_id] = results

    for tid in range(NUM_TESTERS):
        r = all_results[tid]
        assert r["success"] == TOTAL_TASKS, f"Tester {tid}: {r['success']}/{TOTAL_TASKS} success"
        assert r["fail"] == 0, f"Tester {tid}: {r['fail']} failures"
        assert r["stopped"] is False, f"Tester {tid}: unexpectedly stopped"


def test_per_store_report_coverage():
    """Every store must have exactly 5 reports per type. Missing 1 = FAIL."""
    _, results, tracker = _run_single_tester(0)

    coverage: dict[tuple[str, str], list[str]] = defaultdict(list)
    for dl in tracker.downloads:
        coverage[(dl["store"], dl["report_type"])].append(dl["business_date"])

    for loc in ALL_LOCATIONS:
        for rtype in REPORT_TYPES:
            key = (loc, rtype)
            dates = coverage.get(key, [])
            assert len(dates) == len(DATES), (
                f"FAIL: Store '{loc}' / report '{rtype}': "
                f"expected {len(DATES)} downloads, got {len(dates)}. "
                f"Missing: {set(['2026-04-0' + str(d) for d in range(1, 6)]) - set(dates)}"
            )


def test_loop_optimization_open_report_view_count():
    """_open_report_view called 14 times (7 stores x 2 types), NOT 70."""
    _, _, tracker = _run_single_tester(0)

    expected = len(ALL_LOCATIONS) * len(REPORT_TYPES)  # 14
    actual = len(tracker.open_report_calls)
    assert actual == expected, (
        f"_open_report_view called {actual} times, expected {expected}"
    )


def test_location_switch_count():
    """Location switches exactly 7 times (once per store)."""
    _, _, tracker = _run_single_tester(0)

    assert len(tracker.switch_location_calls) == len(ALL_LOCATIONS)
    assert tracker.switch_location_calls == ALL_LOCATIONS


def test_date_selection_count_accounts_for_wa1_skip():
    """Date selected 60 times (WA1 skips date entry for all 10 tasks)."""
    _, _, tracker = _run_single_tester(0)

    expected = TOTAL_TASKS - WA1_SKIP_COUNT  # 70 - 10 = 60
    assert len(tracker.date_calls) == expected, (
        f"Date selection: {len(tracker.date_calls)} calls, expected {expected} "
        f"(WA1 skips {WA1_SKIP_COUNT} date entries)"
    )


def test_no_state_leakage_between_testers():
    """Each tester must have independent state."""
    trackers: list[_TesterTracker] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_run_single_tester, i) for i in range(10)]
        for f in as_completed(futures):
            _, _, tracker = f.result()
            trackers.append(tracker)

    for tracker in trackers:
        assert len(tracker.downloads) == TOTAL_TASKS, (
            f"Tester {tracker.tester_id}: {len(tracker.downloads)}/{TOTAL_TASKS} downloads"
        )

    # No shared references
    for i in range(len(trackers)):
        for j in range(i + 1, len(trackers)):
            assert trackers[i].downloads is not trackers[j].downloads


def test_progress_callback_fires_for_each_task():
    """Progress fires for each of the 70 download tasks."""
    _, _, tracker = _run_single_tester(0)

    # Filter out the final "Done"/"Stopped" progress call (same task_num as last task)
    task_progress = [p for p in tracker.progress if "Done" not in p[2] and "Stopped" not in p[2]]
    assert len(task_progress) == TOTAL_TASKS, (
        f"Progress fired {len(task_progress)} task updates, expected {TOTAL_TASKS}"
    )
    # Sequential task numbers 1..70
    task_nums = [p[0] for p in task_progress]
    assert task_nums == list(range(1, TOTAL_TASKS + 1))
    # All report total = 70
    assert all(p[1] == TOTAL_TASKS for p in task_progress)
