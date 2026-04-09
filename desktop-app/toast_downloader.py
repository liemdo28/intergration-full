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
                 on_log=None, on_progress=None, on_report_file=None, max_download_attempts=3,
                 keep_browser_open_on_failure=None, should_stop=None):
        self.download_dir = download_dir or DEFAULT_DOWNLOAD_DIR
        self.headless = headless
        self.session_file = session_file or DEFAULT_SESSION_FILE
        self.on_log = on_log or (lambda msg: None)
        self.on_progress = on_progress or (lambda cur, total, msg: None)
        self.on_report_file = on_report_file or (lambda item: None)
        self.max_download_attempts = max(1, int(max_download_attempts))
        self.keep_browser_open_on_failure = (not self.headless) if keep_browser_open_on_failure is None else bool(keep_browser_open_on_failure)
        self.should_stop = should_stop or (lambda: False)
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.run_audit = []
        self.active_report_key = None
        self.legacy_sales_summary_active = False

    def log(self, msg):
        self.on_log(msg)

    def _emit_report_file(self, payload):
        try:
            self.on_report_file(dict(payload))
        except Exception as exc:
            self.log(f"    Report file callback failed: {exc}")

    def _stop_requested(self):
        try:
            return bool(self.should_stop())
        except Exception:
            return False

    @staticmethod
    def _skip_date_selection_for_location(location_name):
        return str(location_name or "").strip().upper() == "WA1"

    @staticmethod
    def _to_business_date(date_str):
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _normalize_store_text(text):
        normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
        return re.sub(r"\s+", " ", normalized).strip()

    def _store_aliases(self, store_name):
        canonical = self._normalize_store_text(store_name)
        aliases = {
            "stockton": {
                "stockton",
                "stockton ca",
                "stockton raw sushi bistro",
                "stockton ca raw sushi bistro",
                "raw sushi bistro",
            },
            "the rim": {
                "the rim",
                "rim",
                "bakudan the rim",
                "bakudan ramen the rim",
            },
            "stone oak": {
                "stone oak",
                "bakudan stone oak",
                "stone oak bakudan",
            },
            "bandera": {
                "bandera",
                "bakudan bandera",
                "bandera bakudan",
            },
            "wa1": {"wa1", "wa 1"},
            "wa2": {"wa2", "wa 2"},
            "wa3": {"wa3", "wa 3"},
        }
        values = set(aliases.get(canonical, set()))
        if canonical:
            values.add(canonical)
        return tuple(sorted(values, key=len, reverse=True))

    def _text_matches_store(self, text, store_name):
        normalized_text = self._normalize_store_text(text)
        if not normalized_text:
            return False
        padded = f" {normalized_text} "
        for alias in self._store_aliases(store_name):
            if not alias:
                continue
            if normalized_text == alias or normalized_text.startswith(f"{alias} ") or f" {alias} " in padded:
                return True
        return False

    def _resolve_known_store_from_text(self, text):
        best_store = None
        best_score = -1
        normalized_text = self._normalize_store_text(text)
        if not normalized_text:
            return None
        padded = f" {normalized_text} "
        for store in TOAST_LOCATIONS:
            for alias in self._store_aliases(store):
                if not alias:
                    continue
                score = -1
                if normalized_text == alias:
                    score = 100 + len(alias)
                elif normalized_text.startswith(f"{alias} "):
                    score = 80 + len(alias)
                elif f" {alias} " in padded:
                    score = 60 + len(alias)
                if score > best_score:
                    best_store = store
                    best_score = score
        return best_store

    def _location_verified(self, expected_store):
        detected = self._detect_current_location()
        if not detected:
            return False
        return self._normalize_store_text(detected) == self._normalize_store_text(expected_store)

    def _verified_report_state(self, report_state, expected_store, report_label, date_label):
        if report_state != "no_data":
            return report_state
        if self._location_verified(expected_store):
            return report_state
        detected = self._detect_current_location()
        if detected:
            self.log(
                f"    Toast showed a no-data state for {report_label} / {date_label}, "
                f"but the active store appears to be '{detected}', not '{expected_store}'. Treating this as a failure."
            )
        else:
            self.log(
                f"    Toast showed a no-data state for {report_label} / {date_label}, "
                f"but the active store could not be verified as '{expected_store}'. Treating this as a failure."
            )
        return "error"

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
        # Cookie consent banners often have a two-step flow:
        #   1. Click "Opt out of all" (deselect categories)
        #   2. Click "Save" (confirm the choice)
        # Try both in sequence so the banner fully closes.
        clicked_opt_out = self._click_first_visible(
            [
                'button:text-is("Opt out of all")',
                '[role="button"]:text-is("Opt out of all")',
                'button:text-is("Accept all")',
                'button:text-is("Accept All")',
            ],
            timeout=900,
        )
        if clicked_opt_out:
            self.log("  Dismissed consent popup (opt-out)")
            self.page.wait_for_timeout(600)
            # Now confirm by clicking Save
            self._click_first_visible(
                [
                    'button:text-is("Save")',
                    '[role="button"]:text-is("Save")',
                    'button:text-is("Confirm")',
                    'button:text-is("Done")',
                ],
                timeout=1500,
            )
            self.page.wait_for_timeout(500)
        else:
            clicked_label = self._click_first_visible(
                [
                    'button:text-is("Close")',
                    '[aria-label*="close" i]',
                    '[data-testid*="close" i]',
                ],
                timeout=500,
            )
            if clicked_label:
                self.log("  Dismissed consent popup")
                self.page.wait_for_timeout(500)
        try:
            self.page.evaluate("""() => {
                const clickByText = (pattern) => {
                    const nodes = Array.from(document.querySelectorAll('button, [role="button"], a, span, div'));
                    for (const node of nodes) {
                        const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (!text || text.length > 120) continue;
                        if (!pattern.test(text)) continue;
                        const target = node.closest('button, [role="button"], a') || node;
                        try {
                            target.click();
                            return true;
                        } catch (_err) {}
                    }
                    return false;
                };
                clickByText(/^opt out of all$/i);
                clickByText(/^save$/i);
                clickByText(/^accept all$/i);
                clickByText(/^close$/i);
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
        current_location = self._detect_current_location()
        if current_location:
            return self._normalize_store_text(current_location) == self._normalize_store_text(search_term)
        try:
            visible_text = self.page.evaluate(
                """() => {
                    const headerNodes = Array.from(document.querySelectorAll('header *, nav *, [role="banner"] *, [data-testid*="restaurant" i], [aria-label*="location" i], button, [role="button"], [role="combobox"]'));
                    const headerText = headerNodes
                        .map((node) => {
                            const rect = node.getBoundingClientRect?.();
                            if (!rect) return '';
                            if (rect.top < -10 || rect.top > 260) return '';
                            if (rect.left < -10 || rect.left > 1000) return '';
                            return (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        })
                        .filter(Boolean)
                        .join(' ');
                    const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                    return `${headerText} ${bodyText}`.slice(0, 6000);
                }"""
            )
        except Exception:
            return False
        if not visible_text:
            return False
        return self._text_matches_store(visible_text, search_term)

    def _detect_current_location(self):
        try:
            candidate_texts = self.page.evaluate(
                """() => {
                    const texts = [];
                    const seen = new Set();
                    const pushText = (value) => {
                        const text = (value || '').replace(/\\s+/g, ' ').trim();
                        if (!text || text.length > 180) return;
                        if (seen.has(text)) return;
                        seen.add(text);
                        texts.push(text);
                    };

                    const collectFrom = (selector, topMax, leftMax) => {
                        document.querySelectorAll(selector).forEach((node) => {
                            const rect = node.getBoundingClientRect?.();
                            if (!rect) return;
                            if (rect.width < 24 || rect.height < 16) return;
                            if (rect.top < -10 || rect.top > topMax) return;
                            if (rect.left < -10 || rect.left > leftMax) return;
                            pushText(node.innerText || node.textContent || '');
                        });
                    };

                    collectFrom('header *, nav *, [role="banner"] *', 260, 1000);
                    collectFrom('button, [role="button"], [role="combobox"], [aria-haspopup], [data-testid*="restaurant" i], [aria-label*="location" i]', 260, 1000);
                    collectFrom('body *', 240, 900);

                    const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (bodyText) pushText(bodyText.slice(0, 1200));
                    return texts;
                }"""
            )
            best_store = None
            best_score = -1
            for text in candidate_texts or []:
                matched = self._resolve_known_store_from_text(text)
                if not matched:
                    continue
                normalized_text = self._normalize_store_text(text)
                padded = f" {normalized_text} "
                score = 1
                for alias in self._store_aliases(matched):
                    if normalized_text == alias:
                        score = max(score, 100 + len(alias))
                    elif normalized_text.startswith(f"{alias} "):
                        score = max(score, 80 + len(alias))
                    elif f" {alias} " in padded:
                        score = max(score, 60 + len(alias))
                if best_store is None or score > best_score:
                    best_store = matched
                    best_score = score
            return best_store
        except Exception:
            return None

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
            # New Toast header: location picker shows as a clickable div/button
            # with a location pin icon and store name (e.g. "@The Rim")
            'header [data-testid*="location" i]',
            'header [class*="location" i]',
            'nav [data-testid*="location" i]',
            'nav [class*="location" i]',
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
                    const blockedText = /(essential|vendors|functional|analytics|marketing|save|opt out of all|opt in to all|live chat)/i;
                    const clickable = Array.from(
                        document.querySelectorAll(
                            'button, [role="button"], [role="combobox"], [aria-haspopup], a'
                        )
                    );

                    const findClickable = (node) => {
                        let current = node;
                        let bestDiv = null;
                        for (let depth = 0; current && depth < 5; depth += 1, current = current.parentElement) {
                            if (current.matches?.('button, [role="button"], [role="combobox"], [aria-haspopup], a')) {
                                return current;
                            }
                            const nested = current.querySelector?.('button, [role="button"], [role="combobox"], [aria-haspopup], a');
                            if (nested) return nested;
                            // Accept clickable div/span as fallback (new Toast UI uses
                            // div elements with onClick instead of proper buttons).
                            if (!bestDiv && current.matches?.('div, span') && current !== document.body) {
                                bestDiv = current;
                            }
                        }
                        return bestDiv;
                    };

                    // Strategy 1: find location icon and walk up to clickable parent.
                    // In newer Toast UI the picker may be a <div> with click handler
                    // rather than a <button>, so also accept plain div/span ancestors.
                    const iconNodes = Array.from(
                        document.querySelectorAll(
                            '[data-icon*="location" i], [aria-label*="location" i], ' +
                            '[class*="location" i], [class*="restaurant-picker" i], ' +
                            '[data-testid*="restaurant" i], [data-testid*="location" i], ' +
                            'svg[class*="pin" i], svg[class*="map" i]'
                        )
                    );
                    for (const iconNode of iconNodes) {
                        const candidate =
                            iconNode.closest('button, [role="button"], [role="combobox"], [aria-haspopup], a') ||
                            iconNode.closest('[class*="picker" i], [class*="selector" i], [class*="dropdown" i]') ||
                            iconNode.parentElement;
                        if (!candidate) continue;
                        const text = (candidate.innerText || candidate.textContent || '').replace(/\\s+/g, ' ').trim();
                        // Verify this is really the location picker: must contain a
                        // known store name or the "@" prefix pattern Toast uses.
                        if (!text) continue;
                        const lower = text.toLowerCase();
                        const looksLikeLocation =
                            lower.startsWith('@') ||
                            /,\\s*[A-Z]{2}/i.test(text) ||
                            normalizedStores.some((store) => lower.includes(store));
                        if (!looksLikeLocation) continue;
                        candidate.click();
                        return text;
                    }

                    const topStripNodes = Array.from(document.querySelectorAll('body *')).filter((node) => {
                        const rect = node.getBoundingClientRect?.();
                        if (!rect) return false;
                        if (rect.width < 40 || rect.height < 18) return false;
                        if (rect.top < -10 || rect.top > 220) return false;
                        if (rect.left < -10 || rect.left > 900) return false;
                        const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (!text || text.length > 140) return false;
                        if (blockedText.test(text)) return false;
                        return /^@|,\\s*[A-Z]{2}|raw sushi bistro|bakudan/i.test(text) || normalizedStores.some((store) => text.toLowerCase().includes(store));
                    });
                    for (const node of topStripNodes) {
                        const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        const target = findClickable(node);
                        if (!target) continue;
                        try {
                            target.click();
                            return text || '__location_top_strip__';
                        } catch (_err) {}
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
        if self._location_verified(search_term):
            self.log(f"  Already on location: {search_term}")
            return True

        if not self._open_location_dropdown():
            if self._location_verified(search_term):
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
                    try:
                        search_input.press("Control+a")
                    except Exception:
                        pass
                    search_input.fill(search_term)
                    self.log(f"  Searched location: {search_term}")
                    self.page.wait_for_timeout(800)
                    try:
                        search_input.press("Enter")
                    except Exception:
                        self.page.keyboard.press("Enter")
                    self.log(f"  Submitted location search: {search_term}")
                    self.page.wait_for_timeout(1200)
                    break
            except Exception:
                continue
        else:
            self.page.keyboard.type(search_term, delay=50)
            self.page.wait_for_timeout(1000)
            self.page.keyboard.press("Enter")
            self.log(f"  Submitted location search: {search_term}")

        self.page.wait_for_timeout(2000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        self.page.wait_for_timeout(1000)
        if self._location_verified(search_term):
            return True
        detected = self._detect_current_location()
        if detected:
            self.log(f"  Switch verification failed. Current location appears to be: {detected}")
        else:
            self.log("  Switch verification failed. Current location is still unknown.")
        return False

    def _switch_location_with_retries(self, search_term, attempts=3):
        last_detected = None
        for attempt in range(1, max(1, attempts) + 1):
            if self._location_verified(search_term):
                self.log(f"  Already on location: {search_term}")
                return True
            if attempt > 1:
                self.log(f"  Retry switch {attempt}/{attempts} for {search_term}")
                self._dismiss_overlays()
                self.page.wait_for_timeout(1000)
            if self._switch_location(search_term):
                return True
            last_detected = self._detect_current_location()
        if last_detected:
            self.log(f"  Final switch check still shows location: {last_detected}")
        return False

    def _open_date_picker(self):
        """
        Open the date picker dropdown.
        The trigger button shows text like "Yesterday", "Today", "This week",
        "Last week", "Last 7 days", "This month", "Last month", or "Custom"
        with a date range subtitle like "Mar 19, 2026 - Mar 19, 2026".
        It's located in the report header, NOT the "Custom hours" button.
        """
        self.page.wait_for_timeout(1000)

        if self.legacy_sales_summary_active:
            try:
                found = self.page.evaluate("""() => {
                    const normalize = (value) => (value || '').replace(/\\s+/g, ' ').trim();
                    const hasDateText = (text) =>
                        /today|yesterday|custom|last 7 days|this month|last month/i.test(text) ||
                        /\\d{1,2}\\/\\d{1,2}\\/\\d{4}/.test(text) ||
                        /[A-Z][a-z]{2}\\s+\\d{1,2},\\s+\\d{4}/.test(text);
                    const blocked = /(show percentages|leave feedback|sales summary|raw sushi bistro|bakudan|stockton|bandera|the rim|stone oak|wa1|wa2|wa3)/i;
                    const candidates = Array.from(document.querySelectorAll('main button, main [role="button"], main [role="combobox"], main a, main div, main span'));
                    let best = null;
                    for (const node of candidates) {
                        const text = normalize(node.innerText || node.textContent || '');
                        if (!text || text.length > 160) continue;
                        if (!hasDateText(text)) continue;
                        if (blocked.test(text) && !/^custom/i.test(text)) continue;
                        const rect = node.getBoundingClientRect?.();
                        if (!rect) continue;
                        if (rect.width < 60 || rect.height < 24) continue;
                        if (rect.top < 80 || rect.top > 280) continue;
                        if (rect.left < 180 || rect.left > 900) continue;
                        const target = node.closest('button, [role="button"], [role="combobox"], a') || node;
                        const score = (text.toLowerCase().includes('custom') ? 30 : 0) + (/[A-Z][a-z]{2}\\s+\\d{1,2},\\s+\\d{4}|\\d{1,2}\\/\\d{1,2}\\/\\d{4}/.test(text) ? 20 : 0) - Math.abs(rect.left - 420);
                        if (!best || score > best.score) {
                            best = { target, text, score };
                        }
                    }
                    if (!best) return null;
                    try {
                        best.target.click();
                        return best.text;
                    } catch (_err) {
                        return null;
                    }
                }""")
                if found:
                    self.log(f"    Opened date picker: [{found}]")
                    self.page.wait_for_timeout(1000)
                    return True
            except Exception:
                pass

        # The date picker button is in the report controls area.
        # It contains a calendar icon and date range text.
        # We need to find the button that has date-related text but is NOT "Custom hours".
        #
        # New Toast Sales Summary UI structure:
        #   [ < ]  [ 📅 Today  ▼ ]  [ > ]  [ 📍 Location ▼ ]  [ Custom hours ▼ ]
        #              Apr 7, 2026 - Apr 7, 2026
        # The date picker dropdown is a clickable element containing a date label
        # ("Today", "Yesterday", "Custom", etc.) AND a date range subtitle.
        # It sits between left/right arrow navigation buttons.
        # The parent filter bar container also contains "Custom hours" and location
        # text, so we must find the NARROWEST element that contains a date label
        # and date pattern without also containing "Custom hours" or location info.
        date_labels = [
            "Yesterday", "Today", "This week", "Last week",
            "Last 7 days", "This month", "Last month", "Custom",
        ]

        found = self.page.evaluate("""(labels) => {
            const selectors = [
                'button',
                '[role="button"]',
                '[role="combobox"]',
                '[aria-haspopup="listbox"]',
                '[aria-haspopup="menu"]',
                '[data-testid*="date" i]',
                '[class*="date" i]',
                'div',
                'span',
            ];
            const seen = new Set();
            const nodes = [];
            selectors.forEach((selector) => {
                document.querySelectorAll(selector).forEach((node) => {
                    if (seen.has(node)) return;
                    seen.add(node);
                    nodes.push(node);
                });
            });

            const hasDatePattern = (text) =>
                /\\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\b/.test(text) ||
                /\\d{1,2}\\/\\d{1,2}\\/\\d{4}/.test(text);
            const hasDateRangePattern = (text) =>
                /\\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2},\\s+\\d{4}\\s*[-–]\\s*\\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2},\\s+\\d{4}/i.test(text) ||
                /\\d{1,2}\\/\\d{1,2}\\/\\d{4}\\s*[-–]\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{4}/.test(text);

            // Score candidates by specificity: prefer the narrowest element that
            // contains a date label + date pattern.  Skip elements whose own text
            // also includes "Custom hours" (that's the separate hours dropdown).
            let best = null;

            for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').trim();
                if (!text) continue;
                if (text.length > 140) continue;
                const rect = node.getBoundingClientRect?.();
                if (rect) {
                    if (rect.width < 40 || rect.height < 18) continue;
                    if (rect.top < -10 || rect.top > 320) continue;
                    if (rect.left < 120 || rect.left > 1200) continue;
                }

                // Check if this element's own text (NOT children) includes
                // "Custom hours" — if so, it's too broad a container.
                if (text.includes('Custom hours')) continue;

                let matchesLabel = false;
                let matchesDate = hasDatePattern(text) || hasDateRangePattern(text);
                for (const label of labels) {
                    if (text.includes(label)) {
                        matchesLabel = true;
                        break;
                    }
                }

                if (!matchesLabel && !matchesDate) continue;

                // Prefer elements that match BOTH a label and a date pattern.
                // Among those, prefer narrower elements (smaller text length and
                // smaller bounding box area) to avoid clicking a large container.
                const hasBoth = matchesLabel && matchesDate;
                const area = rect ? rect.width * rect.height : 999999;
                const score = (hasBoth ? 10000 : 0) - text.length - area / 1000;

                if (!best || score > best.score) {
                    best = { node, text, score };
                }
            }

            if (best) {
                best.node.click();
                return best.text.replace(/\\n/g, ' | ');
            }

            // Fallback: look for calendar icon elements and walk up to their
            // clickable ancestor.
            const calendarTargets = Array.from(document.querySelectorAll(
                'svg[data-icon*="calendar" i], [aria-label*="calendar" i], ' +
                '[class*="calendar" i], [data-testid*="calendar" i], ' +
                'svg[class*="calendar" i]'
            ))
                .map((node) => node.closest('button, [role="button"], [role="combobox"], [aria-haspopup], div'))
                .filter(Boolean);
            for (const target of calendarTargets) {
                const text = (target.innerText || target.textContent || '').trim();
                if (text.includes('Custom hours')) continue;
                // Accept the calendar icon's parent if it contains any date text
                // or a known label.
                const relevant = hasDatePattern(text) || hasDateRangePattern(text) ||
                    labels.some((label) => text.includes(label));
                if (!relevant && text.length > 0) continue;
                target.click();
                return text ? text.replace(/\\n/g, ' | ') : '[calendar icon]';
            }

            return null;
        }""", date_labels)

        if found:
            self.log(f"    Opened date picker: [{found}]")
            self.page.wait_for_timeout(1000)
            return True

        self.log("    Could not find date picker button")
        return False

    def _locate_date_inputs(self):
        selectors = [
            '[role="dialog"] input',
            '[aria-modal="true"] input',
            'input[placeholder*="Start" i]',
            'input[placeholder*="End" i]',
            'input[aria-label*="Start" i]',
            'input[aria-label*="End" i]',
            'input[name*="start" i]',
            'input[name*="end" i]',
            'input[data-testid*="start" i]',
            'input[data-testid*="end" i]',
            'input',
        ]
        seen = []
        for sel in selectors:
            try:
                for locator in self.page.locator(sel).all():
                    try:
                        if not locator.is_visible(timeout=300):
                            continue
                        text = ""
                        try:
                            text = (locator.get_attribute("type") or "").lower()
                        except Exception:
                            text = ""
                        if text in {"hidden", "checkbox", "radio"}:
                            continue
                        seen.append(locator)
                    except Exception:
                        continue
            except Exception:
                continue
        unique = []
        dedupe = set()
        for locator in seen:
            key = id(locator)
            if key in dedupe:
                continue
            dedupe.add(key)
            unique.append(locator)
        return unique[:4]

    def _fill_custom_date_inputs(self, date_str):
        inputs = self._locate_date_inputs()
        if len(inputs) < 2:
            return False

        # Determine the right format for typing into date inputs:
        #   - New Sales Summary UI: inputs auto-format, type raw digits MMDDYYYY
        #   - Legacy report home (Orders, etc.): inputs need MM/DD/YYYY with slashes
        parts = date_str.strip().split("/")
        if len(parts) != 3:
            return False
        try:
            month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            return False

        is_new_ui = "sales-summary" in (self.page.url or "")
        if is_new_ui:
            # New UI auto-formats: type raw digits MMDDYYYY
            type_value = f"{month:02d}{day:02d}{year:04d}"
        else:
            # Legacy report home: type MM/DD/YYYY with slashes
            type_value = f"{month:02d}/{day:02d}/{year:04d}"

        try:
            inputs[0].click()
            self.page.wait_for_timeout(200)
            self.page.keyboard.press("Control+a")
            self.page.wait_for_timeout(100)
            for ch in type_value:
                self.page.keyboard.press(ch)
                self.page.wait_for_timeout(60)
            self.log(f"    Start date: {date_str}")
            self.page.wait_for_timeout(300)

            inputs[1].click()
            self.page.wait_for_timeout(200)
            self.page.keyboard.press("Control+a")
            self.page.wait_for_timeout(100)
            for ch in type_value:
                self.page.keyboard.press(ch)
                self.page.wait_for_timeout(60)
            self.log(f"    End date: {date_str}")
            self.page.wait_for_timeout(350)
        except Exception:
            return False

        applied = self._click_first_visible(
            [
                'button:text-is("Apply")',
                'button:text-is("Update")',
                'button:text-is("Done")',
                '[role="button"]:text-is("Apply")',
                '[role="button"]:text-is("Update")',
                '[role="button"]:text-is("Done")',
            ],
            timeout=1200,
        )
        if not applied:
            try:
                inputs[1].press("Enter")
                applied = True
            except Exception:
                applied = False
        if applied:
            self.log(f"    Applied date: {date_str}")
            self.page.wait_for_timeout(3000)
            try:
                self.page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            self.page.wait_for_timeout(1500)
            return True
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

        # Step 2: Click "Custom date" / "Custom" in the opened dropdown.
        # The dropdown may be a React listbox, a native <select>, an <a>-list,
        # or plain <div>/<span> items. Try multiple strategies:
        #   A) Playwright selector matching (fast, works for role-based dropdowns)
        #   B) JavaScript DOM scan (catches any element with matching text)
        #   C) Keyboard navigation (arrow-down through items, press Enter on Custom)
        custom_clicked = self._click_first_visible(
            [
                '[role="option"]:text-is("Custom date")',
                '[role="option"]:text-is("Custom dates")',
                '[role="option"]:text-is("Custom range")',
                '[role="option"]:text-is("Custom")',
                '[role="menuitem"]:text-is("Custom date")',
                '[role="menuitem"]:text-is("Custom dates")',
                '[role="menuitem"]:text-is("Custom range")',
                '[role="menuitem"]:text-is("Custom")',
                'li:text-is("Custom date")',
                'li:text-is("Custom dates")',
                'li:text-is("Custom range")',
                'li:text-is("Custom")',
                'button:text-is("Custom date")',
                'button:text-is("Custom dates")',
                'button:text-is("Custom range")',
                'button:text-is("Custom")',
                'text="Custom date"',
                'text="Custom dates"',
                'text="Custom range"',
                'text="Custom"',
            ],
            timeout=800,
            log_msg="    Clicked 'Custom date' (selector)",
        )
        if custom_clicked:
            self.page.wait_for_timeout(1000)

        # Strategy B: JavaScript DOM scan — finds any visible element whose
        # trimmed text starts with "Custom" and is inside a dropdown / overlay
        # that appeared after opening the date picker.
        if not custom_clicked:
            custom_clicked = bool(self.page.evaluate("""() => {
                const customLabels = [
                    'custom date', 'custom dates', 'custom range', 'custom'
                ];
                // Gather candidate elements from overlays, popovers, dropdowns, lists
                const candidates = Array.from(document.querySelectorAll(
                    '[role="listbox"] *, [role="menu"] *, [role="dialog"] *, ' +
                    '[class*="dropdown" i] *, [class*="popover" i] *, ' +
                    '[class*="overlay" i] *, [class*="picker" i] *, ' +
                    '[class*="select" i] *, [class*="menu" i] *, ' +
                    'ul *, ol *, select option, ' +
                    'a, div, span, li, button, option'
                ));
                const seen = new Set();
                for (const node of candidates) {
                    if (seen.has(node)) continue;
                    seen.add(node);
                    const text = (node.innerText || node.textContent || '').trim();
                    if (!text || text.length > 40) continue;
                    const lower = text.toLowerCase();
                    if (!customLabels.some(label => lower === label)) continue;
                    const rect = node.getBoundingClientRect();
                    if (rect.width < 10 || rect.height < 10) continue;
                    if (rect.top < 0 || rect.bottom > window.innerHeight) continue;
                    // Scroll into view if needed, then click
                    node.scrollIntoView?.({block: 'nearest'});
                    node.click();
                    return text;
                }
                // Also try native <select> elements: if the date picker is a
                // <select>, set its value to the Custom option.
                for (const sel of document.querySelectorAll('select')) {
                    for (const opt of sel.options) {
                        const optText = opt.text.trim().toLowerCase();
                        if (customLabels.some(label => optText === label)) {
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', {bubbles: true}));
                            return opt.text.trim();
                        }
                    }
                }
                return null;
            }"""))
            if custom_clicked:
                self.log(f"    Clicked 'Custom date' (JS scan)")
                self.page.wait_for_timeout(1000)

        # Strategy C: Keyboard navigation — arrow-down through the dropdown
        # items until "Custom" is reached, then press Enter.
        if not custom_clicked:
            try:
                for _ in range(10):
                    self.page.keyboard.press("ArrowDown")
                    self.page.wait_for_timeout(150)
                    focused_text = self.page.evaluate("""() => {
                        const el = document.activeElement;
                        if (!el) return '';
                        return (el.innerText || el.textContent || el.value || '').trim().toLowerCase();
                    }""")
                    if focused_text and focused_text.startswith("custom"):
                        self.page.keyboard.press("Enter")
                        custom_clicked = True
                        self.log(f"    Clicked 'Custom date' (keyboard nav)")
                        self.page.wait_for_timeout(1000)
                        break
            except Exception:
                pass

        if not custom_clicked:
            if self._fill_custom_date_inputs(date_str):
                return True
            self.log("    'Custom date' not found in dropdown")
            self.page.keyboard.press("Escape")
            return False

        # After selecting "Custom" in the dropdown, the legacy report home
        # page (<select>-based) requires clicking "Update" to show the date
        # input fields.  Without Update, Tab would land on other toolbar
        # buttons (e.g. "Email Export") instead of date inputs.
        if self.legacy_sales_summary_active:
            update_clicked = self._click_first_visible(
                [
                    'button:text-is("Update")',
                    'input[value="Update"]',
                    '[role="button"]:text-is("Update")',
                ],
                timeout=2000,
                log_msg="    Clicked 'Update' to show date inputs",
            )
            if update_clicked:
                self.page.wait_for_timeout(2000)
                try:
                    self.page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                self.page.wait_for_timeout(1000)

        # After clicking "Custom date", we need to enter the date range.
        # Two strategies:
        #   - For new Toast UI: use _fill_custom_date_inputs (programmatic fill)
        #   - For legacy Toast UI: MUST use keyboard Tab approach because
        #     legacy inputs don't respond properly to programmatic fill().
        if not self.legacy_sales_summary_active:
            if self._fill_custom_date_inputs(date_str):
                return True

        # Try programmatic fill first (works if date inputs are visible).
        if self._fill_custom_date_inputs(date_str):
            return True

        # Keyboard approach: Tab to Start date → type → Tab to End date → type → Tab → Enter.
        # Legacy UI after Update click: date inputs should be right after the
        # select dropdowns. Try a smaller tab count first.
        tab_count = 4 if self.legacy_sales_summary_active else 2
        for _ in range(tab_count):
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

        # Step 7: Tab to Apply/Update button
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

    def _wait_for_report_ready(self, timeout_ms=20000):
        """Wait for report data and classify the page as ready, no_data, error, or unknown."""
        deadline = time.time() + (max(1000, int(timeout_ms)) / 1000)
        while time.time() < deadline:
            try:
                state = self.page.evaluate("""() => {
                    const extractText = () => (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                    const text = extractText();
                    const readySelectors = [
                        '[aria-label="Download report"]',
                        'button[aria-label*="Download" i]',
                        'button[title*="Download" i]',
                        '[data-testid*="download" i]',
                        'button',
                        'a',
                    ];
                    for (const selector of readySelectors) {
                        const nodes = Array.from(document.querySelectorAll(selector));
                        for (const node of nodes) {
                            const label = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (!label) continue;
                            if (!/download|export|excel|csv|xlsx/i.test(label) && !/aria-label="Download report"/i.test(selector)) continue;
                            const btn = node.closest('button') || node.closest('a') || node;
                            if (!btn) continue;
                            const disabled = btn.disabled || btn.hasAttribute('disabled') || btn.classList.contains('disabled');
                            if (!disabled) return 'ready';
                        }
                    }
                    if (/no data|no results|no records|nothing to show|no orders|no payments|no items|there are no .* for this period|there aren't any .* for this period|no sales/i.test(text)) {
                        return 'no_data';
                    }
                    if (/unable to load this table|try changing your filters or refreshing the page|something went wrong|failed to load|we are unable to load/i.test(text)) {
                        return 'error';
                    }
                    return null;
                }""")
                if state in {"ready", "no_data", "error"}:
                    return state
            except Exception:
                pass
            self.page.wait_for_timeout(750)
        return "unknown"

    def _wait_for_report_context(self, report):
        raw_path = report.report_path or ""
        # Strip full URL prefix to get the path portion for matching.
        expected_path = raw_path
        if expected_path.startswith("http"):
            from urllib.parse import urlparse
            parsed = urlparse(expected_path)
            expected_path = parsed.path + (f"#{parsed.fragment}" if parsed.fragment else "")
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

    def _wait_for_legacy_sales_summary_ready(self, timeout_ms=30000):
        try:
            self.page.wait_for_function(
                """() => {
                    const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
                    const hasReadyControls = Array.from(
                        document.querySelectorAll('main button, main [role="button"], main [role="combobox"], main a')
                    ).some((node) => {
                        const value = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (!value || value.length > 160) return false;
                        return /today|yesterday|custom|last 7 days|this month|last month|\\d{1,2}\\/\\d{1,2}\\/\\d{4}|[A-Z][a-z]{2}\\s+\\d{1,2},\\s+\\d{4}/i.test(value);
                    });
                    const hasDownload = Array.from(document.querySelectorAll('button, a')).some((node) => {
                        const value = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        return /download|export|excel|csv|xlsx/i.test(value);
                    });
                    const hasTerminalState = /no data|no results|no records|nothing to show|unable to load this table/i.test(text);
                    return hasReadyControls || hasDownload || hasTerminalState;
                }""",
                timeout=timeout_ms,
            )
            self.page.wait_for_timeout(1000)
            return True
        except Exception:
            return False

    def _maybe_open_legacy_sales_summary(self, report):
        # The new Sales Summary UI has a proper download icon (↓) with
        # "Download CSV file" / "Download Excel file" dropdown — no need
        # to switch to legacy anymore.  Staying on the new UI gives us
        # cleaner date picker and download controls.
        self.legacy_sales_summary_active = False
        return False

        # Legacy switch kept for reference but disabled:
        if report.key != "sales_summary":
            self.legacy_sales_summary_active = False
            return False

        legacy_selectors = [
            'a:text-matches("legacy\\s+Sales\\s+summary", "i")',
            'button:text-matches("legacy\\s+Sales\\s+summary", "i")',
            'text=/legacy\\s+Sales\\s+summary/i',
        ]
        opened = self._click_first_visible(
            legacy_selectors,
            timeout=2000,
            log_msg="    Switching to legacy Sales Summary",
        )
        if not opened:
            self.legacy_sales_summary_active = False
            return False

        self.page.wait_for_timeout(2000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        self.page.wait_for_timeout(1000)
        self.legacy_sales_summary_active = True
        if not self._wait_for_legacy_sales_summary_ready():
            self.log("    Legacy Sales Summary toolbar did not fully load yet")
        self._dismiss_overlays()
        return True

    def _open_report_view(self, report_type):
        report = get_report_type(report_type)
        if not report.download_supported or not report.report_path:
            raise RuntimeError(
                f"Toast downloader does not have a verified navigation flow for '{report.label}' yet. "
                "Use manual export + Google Drive upload for this report type."
            )
        self.active_report_key = report.key
        self.legacy_sales_summary_active = False
        self.log(f"    Opening report: {report.label}")

        # Navigate: use the URL directly if report_path is a full URL,
        # otherwise prefix with REPORTS_BASE.
        url = report.report_path
        if not url.startswith("http"):
            url = f"{REPORTS_BASE}/{url}"
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(3000)
        self._dismiss_overlays()
        self._wait_for_report_context(report)

        # For Sales Summary with direct URL (?utm_content=subnav), the page
        # loads the new UI directly.  Try the legacy link if available, but
        # don't fail if it's not — the new UI date picker also works now.
        self._maybe_open_legacy_sales_summary(report)

    def _click_download_icon(self):
        """Click the download icon using JS click."""
        # Try Playwright selectors first (fast path for known attributes).
        selectors = [
            '[aria-label="Download report"]',
            'button[aria-label*="Download" i]',
            'button[title*="Download" i]',
            'i[aria-label*="Download" i]',
            '[aria-label*="download" i]',
            '[data-testid*="download" i]',
            '[aria-label*="export" i]',
            '[data-testid*="export" i]',
        ]
        for sel in selectors:
            el = self.page.locator(sel).first
            try:
                if el.is_visible(timeout=1500):
                    el.evaluate("""node => {
                        const target = node.closest('button') || node.closest('a') || node;
                        target.click();
                    }""")
                    return True
            except Exception:
                pass

        # Fallback: JS scan for download/export icons by SVG path or class
        # name.  Legacy Toast toolbar uses <svg> icons without aria-labels.
        found = self.page.evaluate("""() => {
            // Look for SVG download icons (arrow-down-to-tray pattern)
            const svgCandidates = Array.from(document.querySelectorAll(
                'svg[class*="download" i], svg[class*="export" i], ' +
                'svg[data-icon*="download" i], svg[data-icon*="export" i], ' +
                '[class*="download" i] svg, [class*="export" i] svg'
            ));
            for (const svg of svgCandidates) {
                const target = svg.closest('button, [role="button"], a') || svg.parentElement;
                if (!target) continue;
                const rect = target.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 10) continue;
                target.click();
                return 'svg-icon';
            }

            // Look for buttons/links with download-related text or title
            const btns = Array.from(document.querySelectorAll(
                'button, [role="button"], a, [role="menuitem"]'
            ));
            for (const btn of btns) {
                const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                const title = (btn.getAttribute('title') || '').toLowerCase();
                const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
                const combined = text + ' ' + title + ' ' + ariaLabel;
                if (/\\bdownload\\b|\\bexport\\b/.test(combined)) {
                    // Exclude "Email Export" button
                    if (/email/i.test(combined)) continue;
                    const rect = btn.getBoundingClientRect();
                    if (rect.width < 10 || rect.height < 10) continue;
                    btn.click();
                    return text || title || ariaLabel;
                }
            }
            return null;
        }""")
        if found:
            self.log(f"    Found download trigger: {found}")
            return True

        return False

    def _download_report(self, save_dir, report_type="sales_summary", store_name=None, business_date=None):
        """Click download icon -> Tab -> Enter. Returns file metadata or None."""
        if not self._click_download_icon():
            self.log("    Download button not found")
            return None

        self.page.wait_for_timeout(1000)

        # Wait for download event.
        # After clicking the download icon, a dropdown appears with
        # "Download CSV file" and "Download Excel file".  Prefer Excel.
        # Flow: Tab once (skip CSV → land on Excel) → Enter.
        try:
            with self.page.expect_download(timeout=30000) as download_info:
                # Prefer Excel over CSV — try Excel-specific selectors first
                explicit_clicked = self._click_first_visible(
                    [
                        '[role="menuitem"]:text-is("Download Excel file")',
                        'button:text-is("Download Excel file")',
                        'text="Download Excel file"',
                        '[role="menuitem"]:text-matches("Excel", "i")',
                        'button:text-matches("Excel", "i")',
                        'text=/Excel/i',
                        '[role="menuitem"]:text-matches("Export|Download", "i")',
                        'button:text-matches("Export|Download", "i")',
                    ],
                    timeout=1200,
                )
                if not explicit_clicked:
                    # Fallback: Tab to Excel option, Enter to download
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

    def _should_close_browser(self, results, had_unhandled_error):
        if self.headless:
            return True
        if not self.keep_browser_open_on_failure:
            return True
        if had_unhandled_error:
            return False
        if results.get("fail", 0) > 0:
            return False
        return True

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

        results = {"success": 0, "fail": 0, "skipped": 0, "total": 0, "files": [], "stopped": False}
        total_tasks = len(locations) * len(dates) * len(reports)
        had_unhandled_error = False

        os.makedirs(self.download_dir, exist_ok=True)

        try:
            self._start_browser()
            self._login()

            self.log(f"Current URL: {self.page.url}")
            self.log(f"Downloading for {len(locations)} locations × {len(dates)} date(s) × {len(reports)} report type(s)")

            task_num = 0

            for i, loc_name in enumerate(locations):
                if self._stop_requested():
                    results["stopped"] = True
                    self.log("Stop requested. Ending download batch after current item.")
                    break
                self.log(f"\n[Location {i+1}/{len(locations)}] {loc_name}")

                # Switch location
                if not self._switch_location_with_retries(loc_name):
                    self.log(f"  Could not switch to {loc_name}, skipping")
                    skipped = len(dates) * len(reports)
                    results["fail"] += skipped
                    task_num += skipped
                    continue
                self._dismiss_overlays()

                # Loop through each report type, then each date.
                # We open the report view ONCE per report type and stay on
                # that page while iterating dates — only changing the date
                # picker each time.  This avoids the slow
                # navigate→legacy→date cycle on every single date.
                for report in reports:
                    report_view_opened = False

                    for j, date_str in enumerate(dates):
                        if self._stop_requested():
                            results["stopped"] = True
                            self.log("Stop requested. Ending download batch after current item.")
                            break
                        date_label = date_str or "Yesterday"
                        task_num += 1
                        results["total"] += 1
                        self.on_progress(task_num, total_tasks, f"{loc_name} - {date_label} - {report.label}")
                        self.log(f"  [{j+1}/{len(dates)}] Date: {date_label}")
                        self.log(f"    Report: {report.label}")

                        # Open the report view only on the first date (or
                        # after a navigation failure).  For subsequent dates
                        # we stay on the same page and just change the date.
                        if not report_view_opened:
                            try:
                                self._open_report_view(report.key)
                                report_view_opened = True
                            except Exception as exc:
                                self.log(f"    Could not open report view: {exc}")
                                results["fail"] += 1
                                continue

                        if date_str:
                            if self._skip_date_selection_for_location(loc_name):
                                self.log(f"    Skipping date entry for {loc_name}; using the page's current date filter.")
                            else:
                                if not self._select_custom_date(date_str):
                                    self.log(f"    Could not set date {date_str}, skipping")
                                    results["fail"] += 1
                                    # The page may have ended up in a bad
                                    # state — force re-open on next date.
                                    report_view_opened = False
                                    continue
                        else:
                            if not self._open_date_picker():
                                results["fail"] += 1
                                report_view_opened = False
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
                                report_view_opened = False
                                continue

                        self._dismiss_overlays()
                        report_state = self._wait_for_report_ready()
                        report_state = self._verified_report_state(report_state, loc_name, report.label, date_label)
                        self._dismiss_overlays()
                        if report_state == "no_data":
                            self.log(f"    No data returned for {loc_name} / {report.label} / {date_label}. Skipping cleanly.")
                            results["success"] += 1
                            results["skipped"] += 1
                            results["files"].append(
                                {
                                    "location": loc_name,
                                    "report_key": report.key,
                                    "report_label": report.label,
                                    "report_folder": report.folder_name,
                                    "business_date": self._to_business_date(date_str),
                                    "status": "no_data",
                                    "reason": "Toast reported no data for this date filter.",
                                }
                            )
                            self.run_audit.append(
                                {
                                    "location": loc_name,
                                    "date": date_label,
                                    "report_type": report.key,
                                    "attempt": 0,
                                    "success": True,
                                    "skipped": True,
                                    "reason": "no_data",
                                    "business_date": self._to_business_date(date_str),
                                }
                            )
                            continue
                        if report_state == "error":
                            self.log("    Report page loaded with an error state. This is not a no-data skip; download will still retry.")

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
                            if self._stop_requested():
                                results["stopped"] = True
                                self.log("    Stop requested before next download attempt.")
                                break
                            if attempt > 1:
                                backoff = min(2 ** (attempt - 1), 8)
                                self.log(f"    Retry {attempt}/{self.max_download_attempts} after {backoff}s backoff")
                                self.page.wait_for_timeout(backoff * 1000)
                                self._dismiss_overlays()
                                report_state = self._wait_for_report_ready()
                                report_state = self._verified_report_state(report_state, loc_name, report.label, date_label)
                                if report_state == "no_data":
                                    last_error = None
                                    self.log(f"    No data returned for {loc_name} / {report.label} / {date_label}. Skipping cleanly.")
                                    break
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
                            if report_state == "no_data":
                                break
                            if download_info:
                                break
                            last_error = "download validation failed or file was not saved"

                        if results.get("stopped") and not download_info:
                            break

                        if report_state == "no_data" and not download_info:
                            results["success"] += 1
                            results["skipped"] += 1
                            results["files"].append(
                                {
                                    "location": loc_name,
                                    "report_key": report.key,
                                    "report_label": report.label,
                                    "report_folder": report.folder_name,
                                    "business_date": business_date,
                                    "status": "no_data",
                                    "reason": "Toast reported no data for this date filter.",
                                }
                            )
                            continue

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
                    if results.get("stopped"):
                        break
                if results.get("stopped"):
                    break

            if results.get("stopped"):
                self.on_progress(task_num, total_tasks, "Stopped")
            else:
                self.on_progress(total_tasks, total_tasks, "Done")

            # Save session after successful run
            try:
                self.context.storage_state(path=self.session_file)
            except Exception:
                pass

        except Exception as e:
            self.log(f"Error: {e}")
            had_unhandled_error = True
            raise
        finally:
            try:
                self._write_audit_manifest(results)
            except Exception as exc:
                self.log(f"Could not write download audit manifest: {exc}")
            if self._should_close_browser(results, had_unhandled_error):
                self.close()
            else:
                self.log("Browser left open for inspection because the download run failed.")

        if results.get("stopped"):
            self.log(
                f"\nStopped! {results['success']}/{results['total']} successful so far, "
                f"{results['fail']} failed"
            )
        else:
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
