"""
Toast Report Downloader - Python port of toast-download.mjs
Uses Playwright to automate downloading Sales Summary reports from Toast website.
"""

import json
import os
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from app_paths import runtime_path
from report_validator import validate_toast_report_file


REPORTS_BASE = "https://www.toasttab.com/restaurants/admin/reports"
DEFAULT_SESSION_FILE = str(runtime_path(".toast-session.json"))
DEFAULT_DOWNLOAD_DIR = str(runtime_path("toast-reports"))
DOWNLOAD_AUDIT_DIR = runtime_path("audit-logs", "download-reports")

TOAST_LOCATIONS = ["Stockton", "The Rim", "Stone Oak", "Bandera", "WA1", "WA2", "WA3"]


class ToastLoginRequiredError(RuntimeError):
    pass


class ToastDownloader:
    def __init__(self, download_dir=None, headless=False, session_file=None,
                 on_log=None, on_progress=None, max_download_attempts=3):
        self.download_dir = download_dir or DEFAULT_DOWNLOAD_DIR
        self.headless = headless
        self.session_file = session_file or DEFAULT_SESSION_FILE
        self.on_log = on_log or (lambda msg: None)
        self.on_progress = on_progress or (lambda cur, total, msg: None)
        self.max_download_attempts = max(1, int(max_download_attempts))
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.run_audit = []

    def log(self, msg):
        self.on_log(msg)

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

    def _login(self):
        """Navigate to Toast and handle login if needed."""
        self.log("Opening Toast...")
        self.page.goto(f"{REPORTS_BASE}/sales/sales-summary",
                       wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(3000)

        url = self.page.url
        had_saved_session = os.path.exists(self.session_file)
        if not self._is_logged_in(url):
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
                self.page.wait_for_url(
                    lambda u: self._is_logged_in(u),
                    timeout=5 * 60 * 1000,
                )
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
            }""")
        except Exception:
            pass

    def _open_location_dropdown(self):
        """Open the restaurant location dropdown."""
        # Wait for page to be fully interactive
        self.page.wait_for_timeout(2000)

        selectors = [
            "#switch-restaurants-menu",
            '[data-toast-track-id="nav-layout--restaurant-picker"]',
            '[aria-label="Toggle restaurant picker"]',
            'button[role="combobox"][aria-haspopup="listbox"]',
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

        # Last resort: try finding any nav button with restaurant name
        self.log("  Dropdown not found with known selectors, trying fallback...")
        try:
            # Look for elements in the navigation that might be the restaurant picker
            nav_buttons = self.page.locator("nav button, header button").all()
            self.log(f"  Found {len(nav_buttons)} nav/header buttons")
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
        if not self._open_location_dropdown():
            return False

        self.page.wait_for_timeout(1000)
        search_inputs = [
            'input[placeholder*="Search" i]',
            'input[type="search"]',
            '[role="searchbox"]',
            '[role="dialog"] input',
            '[role="listbox"] input',
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
            self.page.wait_for_load_state("networkidle", timeout=10000)
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
        date_raw = date_str.replace("/", "")  # "03/15/2026" → "03152026"

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

        dialog_input_selectors = [
            '[role="dialog"] input',
            '[data-testid*="date" i] input',
            'input[placeholder*="MM" i]',
            'input[inputmode="numeric"]',
        ]
        input_handles = []
        for sel in dialog_input_selectors:
            try:
                count = self.page.locator(sel).count()
                if count >= 2:
                    input_handles = [self.page.locator(sel).nth(0), self.page.locator(sel).nth(1)]
                    break
            except Exception:
                continue

        if len(input_handles) >= 2:
            try:
                for index, handle in enumerate(input_handles[:2]):
                    handle.click()
                    handle.press("Control+a")
                    handle.fill(date_str)
                    self.page.wait_for_timeout(250)
                    self.log(f"    {'Start' if index == 0 else 'End'} date: {date_str}")

                if self._click_first_visible(
                    [
                        'button:text-is("Apply")',
                        'button:text-is("Update")',
                        'text="Apply"',
                    ],
                    timeout=1500,
                    log_msg=f"    Applied date: {date_str}",
                ):
                    self.page.wait_for_timeout(3000)
                    try:
                        self.page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    self.page.wait_for_timeout(2000)
                    return True
            except Exception:
                self.log("    Explicit date input path failed, falling back to keyboard flow")

        # Step 3: Tab 4 times to reach Start date input
        for _ in range(4):
            self.page.keyboard.press("Tab")
            self.page.wait_for_timeout(200)

        # Step 4: Paste Start date using clipboard (more reliable than typing)
        # Select all existing text first, then paste
        self.page.keyboard.press("Control+a")
        self.page.wait_for_timeout(100)
        self.page.evaluate(f"navigator.clipboard.writeText('{date_raw}')")
        self.page.keyboard.press("Control+v")
        self.log(f"    Start date: {date_str}")
        self.page.wait_for_timeout(500)

        # Step 5: Tab to End date
        self.page.keyboard.press("Tab")
        self.page.wait_for_timeout(200)

        # Step 6: Paste End date (same as Start)
        self.page.keyboard.press("Control+a")
        self.page.wait_for_timeout(100)
        self.page.evaluate(f"navigator.clipboard.writeText('{date_raw}')")
        self.page.keyboard.press("Control+v")
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
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(2000)

        return True

    def _wait_for_report_ready(self):
        """Wait for report data and download button."""
        try:
            self.page.wait_for_selector('[aria-label="Download report"]', timeout=20000)
            self.page.wait_for_function("""() => {
                const icon = document.querySelector('[aria-label="Download report"]');
                if (!icon) return false;
                const btn = icon.closest('button') || icon.closest('a') || icon.parentElement;
                if (!btn) return true;
                return !btn.disabled && !btn.hasAttribute('disabled') && !btn.classList.contains('disabled');
            }""", timeout=15000)
        except Exception:
            self.page.wait_for_timeout(5000)

    def _click_download_icon(self):
        """Click the download icon using JS click."""
        selectors = [
            '[aria-label="Download report"]',
            'i[aria-label*="Download" i]',
            '[aria-label*="download" i]',
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

    def _download_report(self, save_dir):
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
                        '[role="menuitem"]:text-matches("Excel|Export|Download", "i")',
                        'button:text-matches("Excel|Export|Download", "i")',
                        'text=/Excel|Export|Download/i',
                    ],
                    timeout=1200,
                )
                if not explicit_clicked:
                    self.page.keyboard.press("Tab")
                    self.page.wait_for_timeout(300)
                    self.page.keyboard.press("Enter")

            download = download_info.value
            filename = download.suggested_filename or "report.xlsx"
            filepath = os.path.join(save_dir, filename)
            download.save_as(filepath)
            validation = validate_toast_report_file(filepath)
            if not validation.ok:
                self.log(f"    Downloaded file failed validation: {'; '.join(validation.errors)}")
                return None
            if validation.warnings:
                self.log(f"    Download warnings: {'; '.join(validation.warnings)}")
            self.log(f"    Downloaded: {filename} [{validation.checksum_sha256[:12]}]")
            return {"filepath": filepath, "validation": validation.to_dict()}

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

    def download_reports_daterange(self, locations=None, dates=None):
        """
        Download reports for given locations across a date range.

        Args:
            locations: list of location names. None = all locations.
            dates: list of date strings (MM/DD/YYYY for Toast).
                   None = use Yesterday for all.

        Returns:
            dict with success, fail, total counts and list of downloaded files.
        """
        locations = locations or TOAST_LOCATIONS
        if not dates:
            dates = [None]  # None = Yesterday

        results = {"success": 0, "fail": 0, "total": 0, "files": []}
        total_tasks = len(locations) * len(dates)

        os.makedirs(self.download_dir, exist_ok=True)

        try:
            self._start_browser()
            self._login()

            # Navigate to Sales Summary
            self.log("Opening Sales Summary...")
            self.page.goto(f"{REPORTS_BASE}/sales/sales-summary",
                          wait_until="networkidle", timeout=30000)
            self.page.wait_for_timeout(5000)
            self._dismiss_overlays()
            self.page.wait_for_timeout(2000)

            self.log(f"Current URL: {self.page.url}")
            self.log(f"Downloading for {len(locations)} locations × {len(dates)} date(s)")

            task_num = 0

            for i, loc_name in enumerate(locations):
                loc_dir = os.path.join(self.download_dir, self._sanitize(loc_name))
                os.makedirs(loc_dir, exist_ok=True)

                self.log(f"\n[Location {i+1}/{len(locations)}] {loc_name}")

                # Switch location
                if not self._switch_location(loc_name):
                    self.log(f"  Could not switch to {loc_name}, skipping")
                    results["fail"] += len(dates)
                    task_num += len(dates)
                    continue
                self._dismiss_overlays()

                # Navigate to sales summary for this location
                self.page.goto(f"{REPORTS_BASE}/sales/sales-summary",
                              wait_until="networkidle", timeout=30000)
                self.page.wait_for_timeout(3000)
                self._dismiss_overlays()

                # Loop through each date
                for j, date_str in enumerate(dates):
                    task_num += 1
                    results["total"] += 1

                    date_label = date_str or "Yesterday"
                    self.on_progress(task_num, total_tasks, f"{loc_name} - {date_label}")
                    self.log(f"  [{j+1}/{len(dates)}] Date: {date_label}")

                    # Select date using Custom date picker
                    # For every date: click date picker → Custom date → fill Start/End → Apply
                    if date_str:
                        if not self._select_custom_date(date_str):
                            self.log(f"    Could not set date {date_str}, skipping")
                            results["fail"] += 1
                            continue
                    else:
                        # No date specified = use Yesterday
                        if not self._open_date_picker():
                            results["fail"] += 1
                            continue
                        yesterday_opt = self.page.locator('text="Yesterday"').first
                        try:
                            if yesterday_opt.is_visible(timeout=2000):
                                yesterday_opt.click()
                                self.page.wait_for_timeout(3000)
                                try:
                                    self.page.wait_for_load_state("networkidle", timeout=10000)
                                except Exception:
                                    pass
                        except Exception:
                            results["fail"] += 1
                            continue

                    self._dismiss_overlays()

                    # Wait for report data to load
                    self._wait_for_report_ready()
                    self._dismiss_overlays()

                    # Download with validation + retry
                    download_info = None
                    last_error = None
                    for attempt in range(1, self.max_download_attempts + 1):
                        if attempt > 1:
                            backoff = min(2 ** (attempt - 1), 8)
                            self.log(f"    Retry {attempt}/{self.max_download_attempts} after {backoff}s backoff")
                            self.page.wait_for_timeout(backoff * 1000)
                            self._dismiss_overlays()
                            self._wait_for_report_ready()
                        download_info = self._download_report(loc_dir)
                        self.run_audit.append({
                            "location": loc_name,
                            "date": date_label,
                            "attempt": attempt,
                            "success": bool(download_info),
                        })
                        if download_info:
                            break
                        last_error = "download validation failed or file was not saved"

                    if download_info:
                        results["success"] += 1
                        results["files"].append({"location": loc_name, **download_info})
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
