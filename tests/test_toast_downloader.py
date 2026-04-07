import toast_downloader


class _FakePage:
    def __init__(self, urls):
        self._urls = list(urls)
        self.wait_calls = []
        self.goto_calls = []
        self.function_calls = []
        self.evaluate_calls = []

    @property
    def url(self):
        if self._urls:
            return self._urls.pop(0)
        return "https://www.toasttab.com/login"

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def wait_for_function(self, script, arg=None, timeout=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": timeout})

    def evaluate(self, script, *args):
        self.evaluate_calls.append({"script": script, "args": args})
        return None

    def wait_for_timeout(self, timeout_ms):
        self.wait_calls.append(timeout_ms)


class _FakeContext:
    def __init__(self):
        self.saved_paths = []

    def storage_state(self, path=None):
        self.saved_paths.append(path)


class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def is_visible(self, timeout=None):
        self.page.visible_selectors.append((self.selector, timeout))
        return False

    def click(self):
        self.page.clicked_selectors.append(self.selector)

    def evaluate(self, script):
        self.page.evaluated_selectors.append(self.selector)


class _RoutePage:
    def __init__(self):
        self.goto_urls = []
        self.wait_calls = []
        self.function_calls = []
        self.visible_selectors = []
        self.clicked_selectors = []
        self.evaluated_selectors = []

    def goto(self, url, **kwargs):
        self.goto_urls.append((url, kwargs))

    def wait_for_timeout(self, timeout_ms):
        self.wait_calls.append(timeout_ms)

    def wait_for_function(self, script, arg=None, timeout=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": timeout})

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def locator(self, selector):
        return _FakeLocator(self, selector)


class _FallbackLocationPage(_RoutePage):
    def evaluate(self, script, arg=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": None})
        if "document.body?.innerText" in script:
            return ""
        if "normalizedStores" in script:
            return "Stockton, CA Raw Sushi Bistro"
        return None


class _CurrentLocationPage(_RoutePage):
    def evaluate(self, script, arg=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": None})
        if "document.body?.innerText" in script:
            return "Reports Stockton, CA Raw Sushi Bistro Sales Summary"
        return None


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


def test_login_in_headless_mode_fails_without_opening_interactive_browser(monkeypatch):
    downloader = toast_downloader.ToastDownloader(headless=True)
    downloader.page = _FakePage(["https://www.toasttab.com/login"])
    downloader.context = _FakeContext()
    monkeypatch.setattr(toast_downloader.os.path, "exists", lambda path: False)

    try:
        downloader._login()
    except toast_downloader.ToastLoginRequiredError as exc:
        assert "did not open a browser window" in str(exc)
    else:
        raise AssertionError("Expected headless login to require a saved session")


def test_open_report_view_uses_direct_route_without_tab_click():
    downloader = toast_downloader.ToastDownloader()
    page = _RoutePage()
    downloader.page = page
    dismiss_calls = []
    downloader._dismiss_overlays = lambda: dismiss_calls.append(True)

    downloader._open_report_view("cash_activity_audit")

    assert page.goto_urls
    assert page.goto_urls[0][0] == "https://www.toasttab.com/restaurants/admin/reports/home#cash-mgmt"
    assert dismiss_calls
    assert any(call["arg"]["fragment"] == "cash-mgmt" for call in page.function_calls if call["arg"])
    assert page.clicked_selectors == []


def test_open_report_view_uses_direct_route_for_sales_orders():
    downloader = toast_downloader.ToastDownloader()
    page = _RoutePage()
    downloader.page = page
    downloader._dismiss_overlays = lambda: None

    downloader._open_report_view("sales_orders")

    assert page.goto_urls[0][0] == "https://www.toasttab.com/restaurants/admin/reports/home#sales-orders"


def test_open_location_dropdown_uses_store_name_fallback_match():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    page = _FallbackLocationPage()
    downloader.page = page
    downloader._dismiss_overlays = lambda: None

    ok = downloader._open_location_dropdown()

    assert ok is True
    assert any("fallback match" in line for line in logs)


def test_switch_location_short_circuits_when_store_already_visible():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    downloader.page = _CurrentLocationPage()

    ok = downloader._switch_location("Stockton")

    assert ok is True
    assert any("Already on location: Stockton" in line for line in logs)


def test_build_saved_filename_normalizes_download_name():
    downloader = toast_downloader.ToastDownloader()

    filename = downloader._build_saved_filename(
        "report.xlsx",
        report_type="orders",
        store_name="Stone Oak",
        business_date="2026-04-07",
    )

    assert filename == "2026-04-07_OrderDetails_Stone Oak.xlsx"
