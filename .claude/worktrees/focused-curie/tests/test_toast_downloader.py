import toast_downloader


class _FakePage:
    def __init__(self, urls):
        self._urls = list(urls)
        self.wait_calls = []

    @property
    def url(self):
        if self._urls:
            return self._urls.pop(0)
        return "https://www.toasttab.com/login"

    def wait_for_timeout(self, timeout_ms):
        self.wait_calls.append(timeout_ms)


def test_wait_for_manual_login_logs_progress_and_succeeds():
    logs = []
    progress = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append, on_progress=lambda cur, total, msg: progress.append((cur, total, msg)))
    downloader.page = _FakePage(
        [
            "https://www.toasttab.com/login",
            "https://www.toasttab.com/login",
            "https://www.toasttab.com/restaurants/admin/reports/sales/sales-summary",
        ]
    )

    ok = downloader._wait_for_manual_login(timeout_seconds=15, poll_seconds=1)

    assert ok is True
    assert any("Waiting for Toast login..." in line for line in logs)
    assert progress


def test_wait_for_manual_login_times_out_cleanly():
    downloader = toast_downloader.ToastDownloader()
    downloader.page = _FakePage(["https://www.toasttab.com/login"] * 10)

    ok = downloader._wait_for_manual_login(timeout_seconds=2, poll_seconds=1)

    assert ok is False
