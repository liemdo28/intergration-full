"""
Toast Report Downloader - Python port of toast-download.mjs
Uses Playwright to automate downloading Toast reports from Toast website.
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from app_paths import runtime_path
from report_inventory import find_existing_local_report
from report_validator import validate_toast_report_file
from date_parser import validate_toast_date_format
from toast_reports import build_local_report_dir, get_report_type, normalize_report_types


REPORTS_BASE = "https://www.toasttab.com/restaurants/admin/reports"
DEFAULT_SESSION_FILE = str(runtime_path(".toast-session.json"))
DEFAULT_DOWNLOAD_DIR = str(runtime_path("toast-reports"))
DOWNLOAD_AUDIT_DIR = runtime_path("audit-logs", "download-reports")

TOAST_LOCATIONS = ["Stockton", "The Rim", "Stone Oak", "Bandera", "WA1", "WA2", "WA3"]
LOGIN_WAIT_TIMEOUT_SECONDS = 5 * 60
LOGIN_WAIT_POLL_SECONDS = 5


class ToastLoginRequiredError(RuntimeError):
    pass


class ToastDownloader:
    def __init__(self, download_dir=None, headless=False, session_file=None,
                 on_log=None, on_progress=None, on_report_file=None, max_download_attempts=3):
        self.download_dir = download_dir or DEFAULT_DOWNLOAD_DIR
        self.headless = headless
        self.session_file = session_file or DEFAULT_SESSION_FILE
        self.on_log = on_log or (lambda msg: None)
        self.on_progress = on_progress or (lambda cur, total, msg: None)
        self.on_report_file = on_report_file or (lambda item: None)
        self.max_download_attempts = max(1, int(max_download_attempts))
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.run_audit = []

    def log(self, msg):
        self.on_log(msg)

    def _emit_report_file(self, payload):
        try:
            self.on_report_file(dict(payload))
        except Exception as exc:
            self.log(f"    Report file callback failed: {exc}")

    @staticmethod
    def _to_business_date(date_str):
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _canonical_report_stem(report_type):
        stems = {
            "sales_summary": "SalesSummary",
            "orders": "OrderDetails",
            "order_items": "ItemSelectionDetails",
            "payments": "PaymentDetails",
            "discounts": "Discounts",
            "cash_activity_audit": "CashActivityAudit",
            "voided_orders": "VoidedOrder",
            "sales_orders": "Order",
        }
        return stems.get(report_type, "ToastReport")

    def _build_saved_filename(self, suggested_filename, *, report_type, store_name=None, business_date=None):
        suffix = Path(suggested_filename or "report.xlsx").suffix or ".xlsx"
        if not business_date:
            return suggested_filename or f"{self._canonical_report_stem(report_type)}{suffix}"
        store_part = self._sanitize(store_name or "Store")
        stem = self._canonical_report_stem(report_type)
        return f"{business_date}_{stem}_{store_part}{suffix}"

    def _is_logged_in(self, url=None):
        target = url or self.page.url
        return "/reports/" in target or "/admin/" in target

    def _click_first_visible(self, selectors, *, timeout=2000, log_msg=None):
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=timeout):
                    el.click()
                    if log_msg:
                        self.log(log_msg)
                    return True
            except Exception:
                continue
        return False

    def _start_browser(self):
        """Launch browser and restore session."""
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                args=[] if self.headless else ["--start-maximized"],
            )

            ctx_opts = {
                "accept_downloads": True,
                "viewport": {"width": 1920, "height": 1080} if self.headless else None,
            }
            if os.path.exists(self.session_file):
                ctx_opts["storage_state"] = self.session_file
                self.log("Restoring session...")

            self.context = self.browser.new_context(**ctx_opts)
            self.page = self.context.new_page()
        except Exception:
            self.close()
            raise

    def _wait_for_manual_login(self, timeout_seconds=LOGIN_WAIT_TIMEOUT_SECONDS, poll_seconds=LOGIN_WAIT_POLL_SECONDS):
        deadline = time.time() + max(1, int(timeout_seconds))
        poll_seconds = max(1, int(poll_seconds))
        last_logged_remaining = None
        while time.time() < deadline:
            current_url = ""
            try:
                current_url = self.page.url
            except Exception:
                current_url = ""
            if self._is_logged_in(current_url):
                return True

            remaining = max(0, int(deadline - time.time()))
            remaining_bucket = ((remaining + 29) // 30) * 30
            if last_logged_remaining != remaining_bucket:
                minutes = remaining_bucket // 60
                seconds = remaining_bucket % 60
                if minutes:
                    self.log(f"Waiting for Toast login... {minutes}m {seconds:02d}s remaining")
                else:
                    self.log(f"Waiting for Toast login... {seconds}s remaining")
                self.on_progress(0, 1, f"Toast login pending ({remaining}s left)")
                last_logged_remaining = remaining_bucket

            self.page.wait_for_timeout(poll_seconds * 1000)

        return False

    def _login(self):
        """Navigate to Toast and handle login if needed."""
        self.log("Opening Toast...")
        self.page.goto(f"{REPORTS_BASE}/sales/sales-summary",
                       wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(3000)

        url = self.page.url
        had_saved_session = os.path.exists(self.session_file)
        if not self._is_logged_in(url):
            if self.headless:
                raise ToastLoginRequiredError(
                    "Toast session is not ready for headless downloads. The app stayed background-safe and did not open "
                    "a browser window. Refresh the Toast session once in an interactive run, then switch back to "
                    "headless downloads."
                )
            # Not logged in
            if had_saved_session:
                self.log("Saved Toast session appears expired or invalid. A fresh login is required.")
            login_btn = self.page.locator("a, button").filter(has_text=re.compile(r"^Login$|^Sign In$", re.I)).first
            try:
                if login_btn.is_visible(timeout=3000):
                    login_btn.click()
                    self.page.wait_for_timeout(3000)
            except Exception:
                pass

            self.log("Please login in the browser window... (5 min timeout)")
            try:
                if not self._wait_for_manual_login():
                    raise PWTimeout("Toast login timed out")
            except PWTimeout as exc:
                raise ToastLoginRequiredError(
                    "Toast login did not complete in time. If the password changed or Toast asked for a new auth step, "
                    "open Settings > Recovery Center, use 'Backup + Reset Toast Session', then try one small download again."
                ) from exc
            self.log("Login successful!")
        else:
            self.log("Session valid.")

        # Save session
        self.context.storage_state(path=self.session_file)

    def _dismiss_overlays(self):
        """Dismiss onboarding checklist and other overlays."""
        clicked_label = self._click_first_visible(
            [
                'button:text-is("Opt out of all")',
                'button:text-is("Save")',
                'button:text-is("Accept all")',
                'button:text-is("Accept All")',
                'button:text-is("Close")',
                '[role="button"]:text-is("Opt out of all")',
                '[role="button"]:text-is("Save")',
                '[aria-label*="close" i]',
                '[data-testid*="close" i]',
            ],
            timeout=900,
        )
        if clicked_label:
            self.log("  Dismissed consent popup")
            self.page.wait_for_timeout(500)
        try:
            self.page.evaluate("""() => {
                const checklist = document.querySelector('#single-spa-application\\\\:toast-onboarding-checklist-spa');
                if (checklist) {
                    checklist.style.display = 'none';
                    checklist.style.pointerEvents = 'none';
                }
                document.querySelectorAll('[data-obd-chkl]').forEach(el => {
                    el.style.display = 'none';
                    el.style.pointerEvents = 'none';
                });
                const toastIq = document.querySelector('[aria-label="Ask Toast IQ"]');
                if (toastIq) toastIq.style.pointerEvents = 'none';
                const overlaySelectors = [
                    '#onetrust-consent-sdk',
                    '#onetrust-banner-sdk',
                    '.onetrust-pc-dark-filter',
                    '.ot-sdk-container',
                    '[aria-label*="cookie" i]',
                    '[id*="cookie" i]',
                    '[class*="cookie" i]',
                    '[id*="consent" i]',
                    '[class*="consent" i]',
                ];
                overlaySelectors.forEach((selector) => {
                    document.querySelectorAll(selector).forEach((el) => {
                        el.style.display = 'none';
                        el.style.pointerEvents = 'none';
                        el.setAttribute('aria-hidden', 'true');
                    });
                });
                document.querySelectorAll('body *').forEach((el) => {
                    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!text) return;
                    if (/opt out of all|opt in to all|functional|vendors|analytics|essential/i.test(text) && text.length < 200) {
                        el.style.pointerEvents = 'none';
                    }
                });
            }""")
        except Exception:
            pass

    def _wait_for_shell_ready(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        try:
            self.page.wait_for_function(
                """() => {
                    const clickable = document.querySelectorAll(
                        'button, [role="button"], [role="combobox"], [aria-haspopup], a'
                    );
                    return clickable.length > 3;
                }""",
                timeout=15000,
            )
        except Exception:
            pass
        self.page.wait_for_timeout(1000)

    def _current_location_matches(self, search_term):
        try:
            visible_text = self.page.evaluate(
                """() => {
                    const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                    return text.slice(0, 4000);
                }"""
            )
        except Exception:
            return False
        if not visible_text:
            return False
        lowered = visible_text.lower()
        return search_term.lower() in lowered

    def _open_location_dropdown(self):
        """Open the restaurant location dropdown."""
        # Wait for page shell/navigation to finish hydrating.
        self._wait_for_shell_ready()
        self._dismiss_overlays()

        selectors = [
            "#switch-restaurants-menu",
            '[data-toast-track-id="nav-layout--restaurant-picker"]',
            '[aria-label="Toggle restaurant picker"]',
            '[aria-label*="restaurant" i]',
            '[aria-label*="location" i]',
            '[data-testid*="restaurant" i]',
            '[data-testid*="location" i]',
            'button[role="combobox"][aria-haspopup="listbox"]',
            '[role="combobox"][aria-haspopup="listbox"]',
            '[aria-haspopup="listbox"]',
            '[aria-haspopup="menu"]',
        ]

        for sel in selectors:
            el = self.page.locator(sel).first
            try:
                if el.is_visible(timeout=5000):
                    el.evaluate("el => el.click()")
                    self.log("  Opened location dropdown")
                    return True
            except Exception:
                continue

        try:
            matched = self.page.evaluate(
                """(knownStores) => {
                    const normalizedStores = knownStores.map((item) => item.toLowerCase());
                    const clickable = Array.from(
                        document.querySelectorAll(
                            'button, [role="button"], [role="combobox"], [aria-haspopup], a'
                        )
                    );

                    for (const el of clickable) {
                        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (!text || text.length > 120) continue;
                        if (normalizedStores.some((store) => text.toLowerCase().includes(store))) {
                            el.click();
                            return text;
                        }
                    }

                    const iconCandidate = Array.from(
                        document.querySelectorAll(
                            '[data-icon*="location" i], [aria-label*="location" i], [class*="location" i]'
                        )
                    )
                        .map((node) => node.closest('button, [role="button"], [role="combobox"], [aria-haspopup], a'))
                        .find(Boolean);
                    if (iconCandidate) {
                        const text = (iconCandidate.innerText || iconCandidate.textContent || '').replace(/\\s+/g, ' ').trim();
                        iconCandidate.click();
                        return text || '__location_icon__';
                    }

                    const anyElements = Array.from(document.querySelectorAll('body *'));
                    for (const el of anyElements) {
                        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (!text || text.length > 120) continue;
                        if (!normalizedStores.some((store) => text.toLowerCase().includes(store))) continue;
                        const target = el.closest('button, [role="button"], [role="combobox"], [aria-haspopup], a, div, span');
                        if (!target) continue;
                        target.click();
                        return text;
                    }
                    return null;
                }""",
                TOAST_LOCATIONS,
            )
            if matched:
                self.log(f"  Opened location dropdown via fallback match: {matched}")
                self.page.wait_for_timeout(800)
                return True
        except Exception:
            pass

        # Last resort: try finding any nav button with restaurant name
        self.log("  Dropdown not found with known selectors, trying fallback...")
        try:
            # Look for elements in the navigation that might be the restaurant picker
            nav_buttons = self.page.locator("nav button, header button, button, [role='button'], [role='combobox']").all()
            self.log(f"  Found {len(nav_buttons)} clickable nav candidates")
            for btn in nav_buttons:
                try:
                    text = btn.inner_text(timeout=1000)
                    if text and len(text) > 2 and len(text) < 50:
                        self.log(f"    Button: '{text}'")
                except Exception:
                    pass
        except Exception:
            pass

        return False

    def _switch_location(self, search_term):
        """Switch to a specific restaurant location."""
        if self._current_location_matches(search_term):
            self.log(f"  Already on location: {search_term}")
            return True

        if not self._open_location_dropdown():
            if self._current_location_matches(search_term):
                self.log(f"  Already on location: {search_term}")
                return True
            return False

        self.page.wait_for_timeout(1000)
        self._dismiss_overlays()
        search_inputs = [
            'input[placeholder*="Search" i]',
            'input[aria-label*="Search" i]',
            'input[type="search"]',
            '[role="searchbox"]',
            '[role="dialog"] input',
            '[role="listbox"] input',
            '[aria-modal="true"] input',
        ]
        for sel in search_inputs:
            try:
                search_input = self.page.locator(sel).first
                if search_input.is_visible(timeout=1000):
                    search_input.click()
                    search_input.fill(search_term)
                    self.log(f"  Searched location: {search_term}")
                    self.page.wait_for_timeout(800)
                    break
            except Exception:
                continue
        else:
            self.page.keyboard.type(search_term, delay=50)
            self.page.wait_for_timeout(1000)

        option_selectors = [
            f'[role="option"]:text-is("{search_term}")',
            f'[role="menuitem"]:text-is("{search_term}")',
            f'button:text-is("{search_term}")',
            f'text="{search_term}"',
        ]
        if self._click_first_visible(option_selectors, timeout=1500):
            self.log(f"  Switching to: {search_term}")
        else:
            self.page.keyboard.press("Tab")
            self.page.wait_for_timeout(300)
            self.page.keyboard.press("Enter")
            self.log(f"  Switching to: {search_term} (keyboard fallback)")

        self.page.wait_for_timeout(2000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        self.page.wait_for_timeout(1000)
        return True

    def _open_date_picker(self):
        """
        Open the date picker dropdown.
        The trigger button shows text like "Yesterday", "Today", "This week",
        "Last week", "Last 7 days", "This month", "Last month", or "Custom"
        with a date range subtitle like "Mar 19, 2026 - Mar 19, 2026".
        It's located in the report header, NOT the "Custom hours" button.
        """
        self.page.wait_for_timeout(1000)

        # The date picker button is in the report controls area.
        # It contains a calendar icon and date range text.
        # We need to find the button that has date-related text but is NOT "Custom hours".
        date_labels = [
            "Yesterday", "Today", "This week", "Last week",
            "Last 7 days", "This month", "Last month", "Custom",
        ]

        # Strategy: Use JS to find the correct button by checking innerText
        # The date picker button typically contains one of the date labels
        # and a date range like "Mar 19, 2026 - Mar 19, 2026"
        found = self.page.evaluate("""(labels) => {
            // Find all buttons on the page
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const text = btn.innerText || '';
                // Skip "Custom hours" button
                if (text.includes('Custom hours')) continue;
                // Skip very long text (not a date picker)
                if (text.length > 100) continue;
                // Check if button text contains any date label
                for (const label of labels) {
                    if (text.includes(label)) {
                        // Verify it also has a date range pattern (Mon DD, YYYY)
                        if (/\\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\b/.test(text) || label === 'Custom') {
                            btn.click();
                            return text.trim().replace(/\\n/g, ' | ');
                        }
                    }
                }
            }
            return null;
        }""", date_labels)

        if found:
            self.log(f"    Opened date picker: [{found}]")
            self.page.wait_for_timeout(1000)
            return True

        self.log("    Could not find date picker button")
        return False

    def _select_custom_date(self, date_str):
        """
        Select a single date using Custom date picker.
        date_str in MM/DD/YYYY format (e.g. "03/15/2026").

        Flow:
        1. Click date picker button (shows Yesterday/Today/Custom/etc)
        2. Scroll down and click "Custom date"
        3. Tab 4 times to reach Start date input
        4. Type MMDDYYYY (auto-formats to MM/DD/YYYY)
        5. Tab 1 time to End date input
        6. Type MMDDYYYY
        7. Tab 1 time to Apply button
        8. Enter to apply
        """
        # FIX M4: Validate date components before stripping separators.
        # Buggy: "3/5/2026".replace("/", "") → "352026" (6 digits!)
        # Fix: parse M/D/YYYY components and zero-pad each.
        parts = date_str.strip().split("/")
        if len(parts) != 3:
            raise ValueError(f"Invalid date format (expected MM/DD/YYYY): '{date_str}'")
        month_s, day_s, year_s = parts
        try:
            month, day, year = int(month_s), int(day_s), int(year_s)
        except ValueError:
            raise ValueError(f"Date components must be integers: '{date_str}'")
        ok, result_or_err = validate_toast_date_format(month, day, year)
        if not ok:
            raise ValueError(f"Invalid date '{date_str}': {result_or_err}")
        date_raw = result_or_err  # Already formatted MMDDYYYY

        # Step 1: Open date picker dropdown
        if not self._open_date_picker():
            return False

        self.page.wait_for_timeout(500)

        # Step 2: Click "Custom date" in dropdown
        # (works whether current mode is Yesterday, Today, Custom, etc.)
        custom_clicked = False
        custom_clicked = self._click_first_visible(
            [
                '[role="option"]:text-is("Custom date")',
                '[role="menuitem"]:text-is("Custom date")',
                'button:text-is("Custom date")',
                'text="Custom date"',
            ],
            timeout=3000,
            log_msg="    Clicked 'Custom date'",
        )
        if custom_clicked:
            self.page.wait_for_timeout(1000)

        if not custom_clicked:
            self.log("    'Custom date' not found in dropdown")
            self.page.keyboard.press("Escape")
            return False

        # Step 3: Tab 4 times to reach Start date input
        for _ in range(4):
            self.page.keyboard.press("Tab")
            self.page.wait_for_timeout(200)

        # Step 4: Clear and type Start date digit by digit
        self.page.keyboard.press("Control+a")
        self.page.wait_for_timeout(100)
        for ch in date_raw:
            self.page.keyboard.press(ch)
            self.page.wait_for_timeout(80)
        self.log(f"    Start date: {date_str}")
        self.page.wait_for_timeout(500)

        # Step 5: Tab to End date
        self.page.keyboard.press("Tab")
        self.page.wait_for_timeout(200)

        # Step 6: Clear and type End date digit by digit
        self.page.keyboard.press("Control+a")
        self.page.wait_for_timeout(100)
        for ch in date_raw:
            self.page.keyboard.press(ch)
            self.page.wait_for_timeout(80)
        self.log(f"    End date: {date_str}")
        self.page.wait_for_timeout(500)

        # Step 7: Tab to Apply button
        self.page.keyboard.press("Tab")
        self.page.wait_for_timeout(200)

        # Step 8: Enter to apply
        self.page.keyboard.press("Enter")
        self.log(f"    Applied date: {date_str}")

        # Wait for report to reload
        self.page.wait_for_timeout(3000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        self.page.wait_for_timeout(2000)

        return True

    def _wait_for_report_ready(self):
        """Wait for report data and download button."""
        try:
            self.page.wait_for_function("""() => {
                const candidates = [
                    '[aria-label="Download report"]',
                    'button[aria-label*="Download" i]',
                    'button[title*="Download" i]',
                    '[data-testid*="download" i]',
                    'button:has-text("Download")',
                    'button:has-text("Export")',
                ];
                for (const selector of candidates) {
                    const nodes = Array.from(document.querySelectorAll(selector));
                    for (const node of nodes) {
                        const btn = node.closest('button') || node.closest('a') || node;
                        if (!btn) continue;
                        const disabled = btn.disabled || btn.hasAttribute('disabled') || btn.classList.contains('disabled');
                        if (!disabled) return true;
                    }
                }
                return false;
            }""", timeout=20000)
        except Exception:
            self.page.wait_for_timeout(5000)

    def _wait_for_report_context(self, report):
        expected_path = report.report_path or ""
        expected_fragment = expected_path.split("#", 1)[1] if "#" in expected_path else ""
        markers = tuple(marker for marker in report.ready_markers if marker)

        try:
            self.page.wait_for_function(
                """({ pathPart, fragment }) => {
                    const href = window.location.href || "";
                    if (fragment && href.includes(`#${fragment}`)) return true;
                    if (pathPart && href.includes(pathPart)) return true;
                    return false;
                }""",
                {"pathPart": expected_path, "fragment": expected_fragment},
                timeout=15000,
            )
        except Exception:
            pass

        for marker in markers:
            try:
                if self.page.locator(f'text="{marker}"').first.is_visible(timeout=2500):
                    return
            except Exception:
                continue

        self.page.wait_for_timeout(1500)

    def _open_report_view(self, report_type):
        report = get_report_type(report_type)
        if not report.download_supported or not report.report_path:
            raise RuntimeError(
                f"Toast downloader does not have a verified navigation flow for '{report.label}' yet. "
                "Use manual export + Google Drive upload for this report type."
            )
        self.log(f"    Opening report: {report.label}")
        self.page.goto(f"{REPORTS_BASE}/{report.report_path}", wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(3000)
        self._dismiss_overlays()
        self._wait_for_report_context(report)

        if report.tab_label:
            opened = self._click_first_visible(
                [
                    f'[role="tab"]:text-is("{report.tab_label}")',
                    f'[role="menuitem"]:text-is("{report.tab_label}")',
                    f'button:text-is("{report.tab_label}")',
                    f'a:text-is("{report.tab_label}")',
                    f'text="{report.tab_label}"',
                ],
                timeout=2500,
                log_msg=f"    Opened tab: {report.tab_label}",
            )
            if not opened:
                raise RuntimeError(f"Could not open Toast report tab '{report.tab_label}'")
            self.page.wait_for_timeout(2000)
            try:
                self.page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            self.page.wait_for_timeout(1000)
            self._dismiss_overlays()

    def _click_download_icon(self):
        """Click the download icon using JS click."""
        selectors = [
            '[aria-label="Download report"]',
            'button[aria-label*="Download" i]',
            'button[title*="Download" i]',
            'i[aria-label*="Download" i]',
            '[aria-label*="download" i]',
            '[data-testid*="download" i]',
        ]
        for sel in selectors:
            el = self.page.locator(sel).first
            try:
                if el.is_visible(timeout=2000):
                    el.evaluate("""node => {
                        const target = node.closest('button') || node.closest('a') || node;
                        target.click();
                    }""")
                    return True
            except Exception:
                pass
        return False

    def _download_report(self, save_dir, report_type="sales_summary", store_name=None, business_date=None):
        """Click download icon -> Tab -> Enter. Returns file metadata or None."""
        if not self._click_download_icon():
            self.log("    Download button not found")
            return None

        self.page.wait_for_timeout(1000)

        # Wait for download event
        try:
            with self.page.expect_download(timeout=30000) as download_info:
                explicit_clicked = self._click_first_visible(
                    [
                        '[role="menuitem"]:text-matches("Excel|CSV|XLSX|Export|Download", "i")',
                        'button:text-matches("Excel|CSV|XLSX|Export|Download", "i")',
                        '[role="option"]:text-matches("Excel|CSV|XLSX|Export|Download", "i")',
                        'text=/Excel|CSV|XLSX|Export|Download/i',
                    ],
                    timeout=1200,
                )
                if not explicit_clicked:
                    self.page.keyboard.press("Tab")
                    self.page.wait_for_timeout(300)
                    self.page.keyboard.press("Enter")

            download = download_info.value
            filename = download.suggested_filename or "report.xlsx"
            save_name = self._build_saved_filename(
                filename,
                report_type=report_type,
                store_name=store_name,
                business_date=business_date,
            )
            filepath = os.path.join(save_dir, save_name)
            download.save_as(filepath)
            validation = validate_toast_report_file(filepath, report_type=report_type)
            if not validation.ok:
                self.log(f"    Downloaded file failed validation: {'; '.join(validation.errors)}")
                return None
            if validation.warnings:
                self.log(f"    Download warnings: {'; '.join(validation.warnings)}")
            self.log(f"    Downloaded: {save_name} [{validation.checksum_sha256[:12]}]")
            return {"filepath": filepath, "filename": save_name, "original_filename": filename, "validation": validation.to_dict()}

        except Exception as e:
            self.log(f"    Download failed: {e}")
            return None

    def _write_audit_manifest(self, results):
        DOWNLOAD_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        manifest = {
            "generated_at": timestamp,
            "download_dir": self.download_dir,
            "results": results,
            "attempts": self.run_audit,
        }
        manifest_path = DOWNLOAD_AUDIT_DIR / f"download-run-{timestamp}.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        self.log(f"Download audit saved -> {manifest_path}")
        return manifest_path

    def download_reports(self, locations=None, target_date=None):
        """
        Download reports for given locations for a single date.
        Wrapper around download_reports_daterange for backwards compatibility.
        """
        dates = [target_date] if target_date else None
        return self.download_reports_daterange(locations=locations, dates=dates)

    def download_reports_daterange(self, locations=None, dates=None, report_types=None):
        """
        Download reports for given locations across a date range.

        Args:
            locations: list of location names. None = all locations.
            dates: list of date strings (MM/DD/YYYY for Toast).
                   None = use Yesterday for all.
            report_types: list of report type keys. None = sales_summary only.

        Returns:
            dict with success, fail, total counts and list of downloaded files.
        """
        locations = locations or TOAST_LOCATIONS
        reports = normalize_report_types(report_types)
        if not dates:
            dates = [None]  # None = Yesterday

        results = {"success": 0, "fail": 0, "skipped": 0, "total": 0, "files": []}
        total_tasks = len(locations) * len(dates) * len(reports)

        os.makedirs(self.download_dir, exist_ok=True)

        try:
            self._start_browser()
            self._login()

            self.log(f"Current URL: {self.page.url}")
            self.log(f"Downloading for {len(locations)} locations × {len(dates)} date(s) × {len(reports)} report type(s)")

            task_num = 0

            for i, loc_name in enumerate(locations):
                self.log(f"\n[Location {i+1}/{len(locations)}] {loc_name}")

                # Switch location
                if not self._switch_location(loc_name):
                    self.log(f"  Could not switch to {loc_name}, skipping")
                    skipped = len(dates) * len(reports)
                    results["fail"] += skipped
                    task_num += skipped
                    continue
                self._dismiss_overlays()

                # Loop through each date
                for j, date_str in enumerate(dates):
                    date_label = date_str or "Yesterday"
                    self.log(f"  [{j+1}/{len(dates)}] Date: {date_label}")
                    for report in reports:
                        task_num += 1
                        results["total"] += 1
                        self.on_progress(task_num, total_tasks, f"{loc_name} - {date_label} - {report.label}")
                        self.log(f"    Report: {report.label}")

                        try:
                            self._open_report_view(report.key)
                        except Exception as exc:
                            self.log(f"    Could not open report view: {exc}")
                            results["fail"] += 1
                            continue

                        if date_str:
                            if not self._select_custom_date(date_str):
                                self.log(f"    Could not set date {date_str}, skipping")
                                results["fail"] += 1
                                continue
                        else:
                            if not self._open_date_picker():
                                results["fail"] += 1
                                continue
                            yesterday_opt = self.page.locator('text="Yesterday"').first
                            try:
                                if yesterday_opt.is_visible(timeout=2000):
                                    yesterday_opt.click()
                                    self.page.wait_for_timeout(3000)
                                    try:
                                        self.page.wait_for_load_state("networkidle", timeout=30000)
                                    except Exception:
                                        pass
                            except Exception:
                                results["fail"] += 1
                                continue

                        self._dismiss_overlays()
                        self._wait_for_report_ready()
                        self._dismiss_overlays()

                        report_dir = build_local_report_dir(self.download_dir, self._sanitize(loc_name), report.key)
                        os.makedirs(report_dir, exist_ok=True)
                        business_date = self._to_business_date(date_str)

                        if business_date:
                            existing_file = find_existing_local_report(
                                self.download_dir,
                                store_name=self._sanitize(loc_name),
                                report_type=report.key,
                                business_date=business_date,
                            )
                            if existing_file:
                                self.log(f"    Already exists locally for {business_date}: {existing_file['filename']}")
                                results["success"] += 1
                                results["skipped"] += 1
                                results["files"].append(
                                    {
                                        "location": loc_name,
                                        "report_key": report.key,
                                        "report_label": report.label,
                                        "report_folder": report.folder_name,
                                        "business_date": business_date,
                                        **existing_file,
                                    }
                                )
                                self._emit_report_file(results["files"][-1])
                                self.run_audit.append(
                                    {
                                        "location": loc_name,
                                        "date": date_label,
                                        "report_type": report.key,
                                        "attempt": 0,
                                        "success": True,
                                        "skipped": True,
                                        "business_date": business_date,
                                    }
                                )
                                continue

                        download_info = None
                        last_error = None
                        for attempt in range(1, self.max_download_attempts + 1):
                            if attempt > 1:
                                backoff = min(2 ** (attempt - 1), 8)
                                self.log(f"    Retry {attempt}/{self.max_download_attempts} after {backoff}s backoff")
                                self.page.wait_for_timeout(backoff * 1000)
                                self._dismiss_overlays()
                                self._wait_for_report_ready()
                            download_info = self._download_report(
                                str(report_dir),
                                report_type=report.key,
                                store_name=loc_name,
                                business_date=business_date,
                            )
                            self.run_audit.append({
                                "location": loc_name,
                                "date": date_label,
                                "report_type": report.key,
                                "attempt": attempt,
                                "success": bool(download_info),
                                "business_date": business_date,
                            })
                            if download_info:
                                break
                            last_error = "download validation failed or file was not saved"

                        if download_info:
                            results["success"] += 1
                            results["files"].append(
                                {
                                    "location": loc_name,
                                    "report_key": report.key,
                                    "report_label": report.label,
                                    "report_folder": report.folder_name,
                                    "business_date": business_date,
                                    "status": "downloaded",
                                    **download_info,
                                }
                            )
                            self._emit_report_file(results["files"][-1])
                        else:
                            results["fail"] += 1
                            if last_error:
                                self.log(f"    Final failure: {last_error}")

            self.on_progress(total_tasks, total_tasks, "Done")

            # Save session after successful run
            try:
                self.context.storage_state(path=self.session_file)
            except Exception:
                pass

        except Exception as e:
            self.log(f"Error: {e}")
            raise
        finally:
            try:
                self._write_audit_manifest(results)
            except Exception as exc:
                self.log(f"Could not write download audit manifest: {exc}")
            self.close()

        self.log(f"\nDone! {results['success']}/{results['total']} successful, {results['fail']} failed")
        return results

    def close(self):
        """Close browser."""
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        self.browser = None
        self.playwright = None

    @staticmethod
    def _sanitize(name):
        return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "unknown"
