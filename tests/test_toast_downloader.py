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
        visible = getattr(self.page, "selector_visibility", {})
        return bool(visible.get(self.selector, False))

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
        self.selector_visibility = {}

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
        if "collectFrom" in script:
            return ["Stockton, CA", "Raw Sushi Bistro", "Sales Summary"]
        if "document.body?.innerText" in script:
            return "Reports Stockton, CA Raw Sushi Bistro Sales Summary"
        return None


class _WrongSwitchPage(_RoutePage):
    def __init__(self):
        super().__init__()
        self.keyboard = self

    def evaluate(self, script, arg=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": None})
        if "collectFrom" in script:
            return ["Bandera", "Bakudan - Bandera"]
        if "document.body?.innerText" in script:
            return "Reports Bandera Bakudan - Bandera Sales Summary"
        return None

    def press(self, key):
        self.clicked_selectors.append(f"keyboard:{key}")

    def type(self, text, delay=0):
        self.clicked_selectors.append(f"type:{text}")


class _SearchSubmitPage(_RoutePage):
    def __init__(self):
        super().__init__()
        self.keyboard = self
        self.current_store = "Bandera"

    def evaluate(self, script, arg=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": None})
        if "collectFrom" in script:
            if self.current_store == "Stockton":
                return ["Stockton, CA", "Raw Sushi Bistro"]
            return ["Bandera", "Bakudan - Bandera"]
        if "document.body?.innerText" in script:
            if self.current_store == "Stockton":
                return "Reports Stockton, CA Raw Sushi Bistro Sales Summary"
            return "Reports Bandera Bakudan - Bandera Sales Summary"
        return None

    def press(self, key):
        self.clicked_selectors.append(f"keyboard:{key}")
        if key == "Enter":
            self.current_store = "Stockton"

    def type(self, text, delay=0):
        self.clicked_selectors.append(f"type:{text}")


class _DatePickerPage(_RoutePage):
    def evaluate(self, script, arg=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": None})
        if "const selectors =" in script:
            return "Custom | Mar 22, 2026 - Mar 22, 2026"
        return None


class _NewUIDatePickerPage(_RoutePage):
    """Simulates the new Toast Sales Summary UI where the date picker shows
    'Today\\nApr 7, 2026 - Apr 7, 2026' and the evaluate scoring logic must
    pick the narrowest element that has both a date label and a date pattern."""

    def evaluate(self, script, arg=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": None})
        if "const selectors =" in script:
            return "Today | Apr 7, 2026 - Apr 7, 2026"
        return None


class _LegacyDatePickerPage(_RoutePage):
    def evaluate(self, script, arg=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": None})
        if "const normalize = (value)" in script:
            return "Custom Mar 22, 2026 - Mar 22, 2026"
        return None


class _NoDataPage(_RoutePage):
    def evaluate(self, script, arg=None):
        self.function_calls.append({"script": script, "arg": arg, "timeout": None})
        if "const extractText" in script:
            return "no_data"
        return None


class _RunPage:
    url = "https://www.toasttab.com/restaurants/admin/reports/sales/sales-summary"

    def wait_for_timeout(self, timeout_ms):
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

    assert "home#sales-orders" in page.goto_urls[0][0]


def test_open_report_view_stays_on_new_ui_for_sales_summary():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    page = _RoutePage()
    downloader.page = page
    dismiss_calls = []
    downloader._dismiss_overlays = lambda: dismiss_calls.append(True)

    downloader._open_report_view("sales_summary")

    assert "sales/sales-summary" in page.goto_urls[0][0]
    # Should NOT switch to legacy — new UI has proper download controls
    assert not any("Switching to legacy" in line for line in logs)
    assert downloader.legacy_sales_summary_active is False


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


def test_detect_current_location_returns_best_known_store():
    downloader = toast_downloader.ToastDownloader()
    downloader.page = _CurrentLocationPage()

    current = downloader._detect_current_location()

    assert current == "Stockton"


def test_switch_location_fails_when_enter_flow_lands_on_wrong_store():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    page = _WrongSwitchPage()
    downloader.page = page
    downloader._open_location_dropdown = lambda: True
    downloader._dismiss_overlays = lambda: None

    ok = downloader._switch_location("WA2")

    assert ok is False
    assert "type:WA2" in page.clicked_selectors
    assert "keyboard:Enter" in page.clicked_selectors
    assert any("Switch verification failed" in line for line in logs)


def test_switch_location_uses_search_then_enter_flow():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    page = _SearchSubmitPage()
    downloader.page = page
    downloader._open_location_dropdown = lambda: True
    downloader._dismiss_overlays = lambda: None

    ok = downloader._switch_location("Stockton")

    assert ok is True
    assert "type:Stockton" in page.clicked_selectors
    assert "keyboard:Enter" in page.clicked_selectors
    assert any("Submitted location search: Stockton" in line for line in logs)


def test_build_saved_filename_normalizes_download_name():
    downloader = toast_downloader.ToastDownloader()

    filename = downloader._build_saved_filename(
        "report.xlsx",
        report_type="orders",
        store_name="Stone Oak",
        business_date="2026-04-07",
    )

    assert filename == "2026-04-07_OrderDetails_Stone Oak.xlsx"


def test_open_date_picker_supports_legacy_style_picker_targets():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    downloader.page = _DatePickerPage()

    ok = downloader._open_date_picker()

    assert ok is True
    assert any("Opened date picker" in line for line in logs)


def test_open_date_picker_supports_new_ui_today_dropdown():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    downloader.page = _NewUIDatePickerPage()

    ok = downloader._open_date_picker()

    assert ok is True
    assert any("Opened date picker" in line for line in logs)
    assert any("Today" in line for line in logs)


def test_open_date_picker_supports_legacy_sales_summary_header_control():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    downloader.page = _LegacyDatePickerPage()
    downloader.legacy_sales_summary_active = True

    ok = downloader._open_date_picker()

    assert ok is True
    assert any("Opened date picker" in line for line in logs)


def test_wait_for_report_ready_returns_no_data_state():
    downloader = toast_downloader.ToastDownloader()
    downloader.page = _NoDataPage()

    state = downloader._wait_for_report_ready(timeout_ms=1000)

    assert state == "no_data"


def test_verified_report_state_rejects_no_data_when_store_cannot_be_confirmed():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    downloader.page = _WrongSwitchPage()

    state = downloader._verified_report_state("no_data", "WA2", "Sale Summary", "03/20/2026")

    assert state == "error"
    assert any("Treating this as a failure" in line for line in logs)


def test_dismiss_overlays_clicks_cookie_popup_actions_when_visible():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    page = _RoutePage()
    page.selector_visibility['button:text-is("Opt out of all")'] = True
    downloader.page = page

    downloader._dismiss_overlays()

    assert 'button:text-is("Opt out of all")' in page.clicked_selectors
    assert any("Dismissed consent popup" in line for line in logs)


def test_download_reports_daterange_honors_stop_request_after_current_item():
    stop_state = {"value": False}
    downloader = toast_downloader.ToastDownloader(should_stop=lambda: stop_state["value"])
    downloader.page = _RunPage()
    downloader.context = _FakeContext()
    downloader._start_browser = lambda: None
    downloader._login = lambda: None
    downloader._switch_location_with_retries = lambda _loc: True
    downloader._dismiss_overlays = lambda: None
    downloader._wait_for_report_ready = lambda: None
    downloader._open_report_view = lambda _report: None
    downloader._select_custom_date = lambda _date: True
    downloader.close = lambda: None

    def fake_download_report(*args, **kwargs):
        stop_state["value"] = True
        return {"filepath": "E:/fake.xlsx", "filename": "fake.xlsx"}

    downloader._download_report = fake_download_report

    results = downloader.download_reports_daterange(
        locations=["Stockton"],
        dates=["03/20/2026", "03/21/2026"],
        report_types=["sales_summary"],
    )

    assert results["success"] == 1
    assert results["total"] == 1
    assert results["stopped"] is True


def test_download_reports_daterange_skips_date_entry_for_wa1():
    logs = []
    downloader = toast_downloader.ToastDownloader(on_log=logs.append)
    downloader.page = _RunPage()
    downloader.context = _FakeContext()
    downloader._start_browser = lambda: None
    downloader._login = lambda: None
    downloader._switch_location_with_retries = lambda _loc: True
    downloader._dismiss_overlays = lambda: None
    downloader._wait_for_report_ready = lambda: None
    downloader._open_report_view = lambda _report: None
    downloader.close = lambda: None

    def fail_if_called(_date):
        raise AssertionError("WA1 should skip date entry")

    downloader._select_custom_date = fail_if_called
    downloader._download_report = lambda *args, **kwargs: {"filepath": "E:/fake.xlsx", "filename": "fake.xlsx"}

    results = downloader.download_reports_daterange(
        locations=["WA1"],
        dates=["03/20/2026"],
        report_types=["sales_summary"],
    )

    assert results["success"] == 1
    assert any("Skipping date entry for WA1" in line for line in logs)


def test_should_close_browser_keeps_gui_window_open_on_failure():
    downloader = toast_downloader.ToastDownloader(headless=False)

    assert downloader._should_close_browser({"fail": 1}, had_unhandled_error=False) is False
    assert downloader._should_close_browser({"fail": 0}, had_unhandled_error=True) is False
    assert downloader._should_close_browser({"fail": 0}, had_unhandled_error=False) is True


def test_should_close_browser_always_closes_in_headless_mode():
    downloader = toast_downloader.ToastDownloader(headless=True)

    assert downloader._should_close_browser({"fail": 3}, had_unhandled_error=True) is True
