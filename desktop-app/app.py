"""
Toast POS Manager - Unified Desktop Application
Combines 3 tools into 1 app:
  Tab 1: Download Reports (Playwright web scraper)
  Tab 2: QB Integration (Create Sales Receipts from Excel)
  Tab 3: Remove Transactions (Query & delete QB transactions)
  Tab 4: Settings (Google Drive, Toast session, QB config)

No API required - all data comes from Toast website scraping + local Excel files.
"""

import argparse
import sys
import os
import csv
import json
import time
import threading
import glob as glob_mod
from pathlib import Path
from datetime import datetime, timedelta
from tkinter import filedialog, simpledialog
from functools import partial

# Fix encoding for Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from tkcalendar import Calendar
from app_paths import APP_DIR, RUNTIME_DIR, app_path, runtime_path
from audit_utils import export_transactions_snapshot, write_delete_audit
from delete_policy import load_delete_policy
from diagnostics import format_report_lines, run_environment_checks
from recovery_center import (
    backup_and_remove,
    ensure_runtime_file_from_example,
    export_support_bundle,
    format_playbook,
    get_playbook_by_title,
    get_recovery_playbooks,
)

MAPPING_FILE = app_path("qb-mapping.json")
LOCAL_CONFIG_FILE = runtime_path("local-config.json")
REPORTS_DIR = runtime_path("toast-reports")
AUDIT_LOG_DIR = runtime_path("audit-logs")
DELETE_AUDIT_DIR = AUDIT_LOG_DIR / "delete-transactions"
QBSYNC_ISSUE_DIR = AUDIT_LOG_DIR / "qb-sync-validation"

# Toast locations (for download tab)
TOAST_LOCATIONS = ["Stockton", "The Rim", "Stone Oak", "Bandera", "WA1", "WA2", "WA3"]


# ── Config helpers ───────────────────────────────────────────────────
def load_mapping():
    if not MAPPING_FILE.exists():
        return {}, {}
    with open(MAPPING_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("global", {}), data.get("stores", {})


def load_local_config():
    if LOCAL_CONFIG_FILE.exists():
        try:
            with open(LOCAL_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_local_config(config):
    with open(LOCAL_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ── Shared UI helpers ────────────────────────────────────────────────
def make_log_box(parent, height=200):
    """Create a consistent log textbox."""
    ctk.CTkLabel(parent, text="Log", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=15, pady=(10, 3))
    log_box = ctk.CTkTextbox(parent, height=height, font=ctk.CTkFont(family="Consolas", size=12))
    log_box.pack(fill="both", expand=True, padx=15, pady=(0, 15))
    log_box.configure(state="disabled")
    return log_box


def append_log(log_box, msg):
    """Thread-safe log append."""
    ts = time.strftime("%H:%M:%S")
    text = f"[{ts}] {msg}\n"
    log_box.configure(state="normal")
    log_box.insert("end", text)
    log_box.see("end")
    log_box.configure(state="disabled")


def make_calendar(parent, initial_date=None):
    """Create a styled dark calendar widget."""
    if not initial_date:
        initial_date = datetime.now() - timedelta(days=1)
    frame = tk.Frame(parent, bg="#2b2b2b")
    frame.pack(pady=(5, 0))
    cal = Calendar(frame, selectmode="day",
                   year=initial_date.year, month=initial_date.month, day=initial_date.day,
                   date_pattern="yyyy-mm-dd",
                   background="#2b2b2b", foreground="white",
                   headersbackground="#1f538d", headersforeground="white",
                   selectbackground="#1f538d", selectforeground="white",
                   normalbackground="#333333", normalforeground="white",
                   weekendbackground="#3a3a3a", weekendforeground="white",
                   othermonthbackground="#252525", othermonthforeground="#666666",
                   othermonthwebackground="#252525", othermonthweforeground="#666666",
                   borderwidth=0, font=("Segoe UI", 10))
    cal.pack()
    return frame, cal


# ══════════════════════════════════════════════════════════════════════
#  Tab 1: Download Reports
# ══════════════════════════════════════════════════════════════════════
class DownloadTab(ctk.CTkFrame):
    def __init__(self, master, status_var, **kwargs):
        super().__init__(master, **kwargs)
        self.status_var = status_var
        self._running = False
        self._build_ui()

    def _build_ui(self):
        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)

        # ── Date Section ──
        date_frame = ctk.CTkFrame(content)
        date_frame.pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(date_frame, text="Date Range", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))

        cal_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        cal_row.pack(fill="x", padx=10, pady=(0, 5))

        yesterday = datetime.now() - timedelta(days=1)
        yesterday_str = yesterday.strftime("%Y-%m-%d")

        # Start Date
        start_col = ctk.CTkFrame(cal_row, fg_color="transparent")
        start_col.pack(side="left", padx=(0, 20))
        ctk.CTkLabel(start_col, text="Start Date", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        self.start_date_var = ctk.StringVar(value=yesterday_str)
        self.start_cal_frame, self.start_cal = make_calendar(start_col, yesterday)
        start_entry_row = ctk.CTkFrame(start_col, fg_color="transparent")
        start_entry_row.pack(fill="x", pady=(5, 0))
        self.start_date_entry = ctk.CTkEntry(start_entry_row, textvariable=self.start_date_var, width=130)
        self.start_date_entry.pack(side="left")
        self.start_date_entry.bind("<Return>", lambda e: self._sync_start_cal())
        self.start_cal.bind("<<CalendarSelected>>", lambda e: self._on_start_date_selected())

        # End Date
        end_col = ctk.CTkFrame(cal_row, fg_color="transparent")
        end_col.pack(side="left", padx=(0, 20))
        ctk.CTkLabel(end_col, text="End Date", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        self.end_date_var = ctk.StringVar(value=yesterday_str)
        self.end_cal_frame, self.end_cal = make_calendar(end_col, yesterday)
        end_entry_row = ctk.CTkFrame(end_col, fg_color="transparent")
        end_entry_row.pack(fill="x", pady=(5, 0))
        self.end_date_entry = ctk.CTkEntry(end_entry_row, textvariable=self.end_date_var, width=130)
        self.end_date_entry.pack(side="left")
        self.end_date_entry.bind("<Return>", lambda e: self._sync_end_cal())
        self.end_cal.bind("<<CalendarSelected>>", lambda e: self._on_end_date_selected())

        self.date_info_label = ctk.CTkLabel(date_frame, text="", text_color="#60a5fa", font=ctk.CTkFont(size=12))
        self.date_info_label.pack(anchor="w", padx=10, pady=(2, 0))
        self._update_date_info()

        # Quick buttons
        btn_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(5, 10))

        def set_single_date(days_ago):
            d = datetime.now() - timedelta(days=days_ago)
            d_str = d.strftime("%Y-%m-%d")
            self.start_date_var.set(d_str)
            self.end_date_var.set(d_str)
            self.start_cal.selection_set(d)
            self.end_cal.selection_set(d)
            self._update_date_info()

        def set_last_n_days(n):
            end = datetime.now() - timedelta(days=1)
            start = datetime.now() - timedelta(days=n)
            self.start_date_var.set(start.strftime("%Y-%m-%d"))
            self.end_date_var.set(end.strftime("%Y-%m-%d"))
            self.start_cal.selection_set(start)
            self.end_cal.selection_set(end)
            self._update_date_info()

        ctk.CTkButton(btn_row, text="Yesterday", width=90, command=lambda: set_single_date(1)).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="Today", width=70, command=lambda: set_single_date(0)).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="Last 7 days", width=100, command=lambda: set_last_n_days(7)).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="Last 30 days", width=100, command=lambda: set_last_n_days(30)).pack(side="left", padx=2)

        # ── Locations Section ──
        loc_frame = ctk.CTkFrame(content)
        loc_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(loc_frame, text="Toast Locations", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))

        checks_frame = ctk.CTkFrame(loc_frame, fg_color="transparent")
        checks_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.loc_vars = {}
        for i, loc in enumerate(TOAST_LOCATIONS):
            var = ctk.BooleanVar(value=True)
            self.loc_vars[loc] = var
            ctk.CTkCheckBox(checks_frame, text=loc, variable=var, width=130).grid(row=i // 4, column=i % 4, padx=5, pady=3, sticky="w")

        btn_row2 = ctk.CTkFrame(loc_frame, fg_color="transparent")
        btn_row2.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(btn_row2, text="Select All", width=90, command=lambda: [v.set(True) for v in self.loc_vars.values()]).pack(side="left", padx=2)
        ctk.CTkButton(btn_row2, text="Deselect All", width=100, command=lambda: [v.set(False) for v in self.loc_vars.values()]).pack(side="left", padx=2)

        # ── Options ──
        opt_frame = ctk.CTkFrame(content)
        opt_frame.pack(fill="x", padx=15, pady=5)
        self.upload_gdrive_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(opt_frame, text="Upload to Google Drive after download", variable=self.upload_gdrive_var).pack(anchor="w", padx=10, pady=10)

        # ── Action Button ──
        self.download_btn = ctk.CTkButton(content, text="Download Reports",
                                            font=ctk.CTkFont(size=15, weight="bold"),
                                            height=45, command=self.start_download,
                                            fg_color="#2563eb", hover_color="#1d4ed8")
        self.download_btn.pack(fill="x", padx=15, pady=10)

        # ── Progress ──
        self.progress_bar = ctk.CTkProgressBar(content)
        self.progress_bar.pack(fill="x", padx=15, pady=(0, 5))
        self.progress_bar.set(0)
        self.progress_label = ctk.CTkLabel(content, text="Ready", text_color="gray")
        self.progress_label.pack(anchor="w", padx=15)

        # ── Log ──
        self.log_box = make_log_box(content)

    def log(self, msg):
        self.after(0, lambda: append_log(self.log_box, msg))

    def update_progress(self, current, total, msg):
        self.after(0, lambda: self._set_progress(current, total, msg))

    def _set_progress(self, current, total, msg):
        if total > 0:
            self.progress_bar.set(current / total)
        self.progress_label.configure(text=f"{msg} ({current}/{total})")
        self.status_var.set(msg)

    def _on_start_date_selected(self):
        self.start_date_var.set(self.start_cal.get_date())
        try:
            s = datetime.strptime(self.start_date_var.get(), "%Y-%m-%d")
            e = datetime.strptime(self.end_date_var.get(), "%Y-%m-%d")
            if s > e:
                self.end_date_var.set(self.start_date_var.get())
                self.end_cal.selection_set(s)
        except ValueError:
            pass
        self._update_date_info()

    def _on_end_date_selected(self):
        self.end_date_var.set(self.end_cal.get_date())
        try:
            s = datetime.strptime(self.start_date_var.get(), "%Y-%m-%d")
            e = datetime.strptime(self.end_date_var.get(), "%Y-%m-%d")
            if e < s:
                self.start_date_var.set(self.end_date_var.get())
                self.start_cal.selection_set(e)
        except ValueError:
            pass
        self._update_date_info()

    def _sync_start_cal(self):
        try:
            d = datetime.strptime(self.start_date_var.get(), "%Y-%m-%d")
            self.start_cal.selection_set(d)
            self._update_date_info()
        except ValueError:
            pass

    def _sync_end_cal(self):
        try:
            d = datetime.strptime(self.end_date_var.get(), "%Y-%m-%d")
            self.end_cal.selection_set(d)
            self._update_date_info()
        except ValueError:
            pass

    def _update_date_info(self):
        try:
            s = datetime.strptime(self.start_date_var.get(), "%Y-%m-%d")
            e = datetime.strptime(self.end_date_var.get(), "%Y-%m-%d")
            days = (e - s).days + 1
            if days == 1:
                self.date_info_label.configure(text=f"1 day selected: {s.strftime('%b %d, %Y')}")
            else:
                self.date_info_label.configure(text=f"{days} days selected: {s.strftime('%b %d')} - {e.strftime('%b %d, %Y')}")
        except (ValueError, AttributeError):
            pass

    def _get_date_range(self):
        start_str = self.start_date_var.get().strip()
        end_str = self.end_date_var.get().strip()
        if not start_str or not end_str:
            messagebox.showwarning("Warning", "Please enter Start Date and End Date")
            return None
        try:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d")
            end_dt = datetime.strptime(end_str, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Error", "Invalid date format. Use YYYY-MM-DD")
            return None
        if start_dt > end_dt:
            messagebox.showerror("Error", "Start Date must be before or equal to End Date")
            return None
        dates = []
        current = start_dt
        while current <= end_dt:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return dates

    def start_download(self):
        if self._running:
            return
        locations = [loc for loc, var in self.loc_vars.items() if var.get()]
        if not locations:
            messagebox.showwarning("Warning", "Please select at least one location")
            return
        dates = self._get_date_range()
        if not dates:
            return
        self._running = True
        self.download_btn.configure(state="disabled", text="Downloading...")
        threading.Thread(target=self._download_worker, args=(locations, dates), daemon=True).start()

    def _download_worker(self, locations, dates):
        try:
            from toast_downloader import ToastDownloader

            self.log(f"Starting download for {len(locations)} locations, {len(dates)} day(s): {dates[0]} -> {dates[-1]}")

            downloader = ToastDownloader(
                download_dir=str(REPORTS_DIR),
                headless=False,
                on_log=self.log,
                on_progress=self.update_progress,
            )

            toast_dates = []
            for d in dates:
                dt = datetime.strptime(d, "%Y-%m-%d")
                toast_dates.append(dt.strftime("%m/%d/%Y"))

            results = downloader.download_reports_daterange(locations=locations, dates=toast_dates)

            if self.upload_gdrive_var.get() and results["files"]:
                self.log("Uploading to Google Drive...")
                try:
                    from gdrive_service import GDriveService
                    gdrive = GDriveService(on_log=self.log)
                    if gdrive.authenticate():
                        for f in results["files"]:
                            try:
                                gdrive.upload_report(f["filepath"], f["location"])
                            except Exception as e:
                                self.log(f"  Upload error for {f['location']}: {e}")
                        self.log("Google Drive upload complete")
                    else:
                        self.log("Google Drive authentication failed - skipping upload")
                except Exception as e:
                    self.log(f"Google Drive error: {e}")

            self.log(f"All done! {results['success']}/{results['total']} downloaded")

        except Exception as e:
            self.log(f"Error: {e}")
            import traceback
            self.log(traceback.format_exc())
        finally:
            self._running = False
            self.after(0, lambda: self.download_btn.configure(state="normal", text="Download Reports"))
            self.after(0, lambda: self.status_var.set("Ready"))


# ══════════════════════════════════════════════════════════════════════
#  Tab 2: QB Integration (Sync Sales Receipts)
# ══════════════════════════════════════════════════════════════════════
class QBSyncTab(ctk.CTkFrame):
    def __init__(self, master, status_var, **kwargs):
        super().__init__(master, **kwargs)
        self.status_var = status_var
        self._running = False
        self._global_cfg, self._stores = load_mapping()
        self.validation_records = []
        self._build_ui()

    def _build_ui(self):
        # ── Date Section ──
        date_frame = ctk.CTkFrame(self)
        date_frame.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(date_frame, text="Date(s)", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))

        date_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        date_row.pack(fill="x", padx=10, pady=(0, 10))
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.date_var = ctk.StringVar(value=yesterday)
        ctk.CTkLabel(date_row, text="Date(s):").pack(side="left", padx=(0, 10))
        self.date_entry = ctk.CTkEntry(date_row, textvariable=self.date_var, width=250,
                                        placeholder_text="YYYY-MM-DD or YYYY-MM-DD,YYYY-MM-DD")
        self.date_entry.pack(side="left", padx=(0, 10))
        ctk.CTkButton(date_row, text="Yesterday", width=90,
                       command=lambda: self.date_var.set((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))).pack(side="left", padx=2)

        hint = ctk.CTkLabel(date_frame, text="Multiple dates: comma-separated (e.g. 2026-03-18,2026-03-19)",
                            text_color="gray", font=ctk.CTkFont(size=11))
        hint.pack(anchor="w", padx=10, pady=(0, 5))

        # ── Stores Section ──
        store_frame = ctk.CTkFrame(self)
        store_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(store_frame, text="QB Stores", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))

        self._local_cfg = load_local_config()
        qbw_paths = self._local_cfg.get("qbw_paths", {})

        stores_grid = ctk.CTkFrame(store_frame, fg_color="transparent")
        stores_grid.pack(fill="x", padx=10, pady=(0, 5))

        ctk.CTkLabel(stores_grid, text="", width=30).grid(row=0, column=0)
        ctk.CTkLabel(stores_grid, text="Store", font=ctk.CTkFont(size=12, weight="bold"), width=100, anchor="w").grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(stores_grid, text="QB Company File (.qbw)", font=ctk.CTkFont(size=12, weight="bold"), anchor="w").grid(row=0, column=2, sticky="w", padx=(10, 0))

        self.store_vars = {}
        self.qbw_path_vars = {}
        store_names = list(self._stores.keys())

        for i, name in enumerate(store_names):
            row = i + 1
            var = ctk.BooleanVar(value=True)
            self.store_vars[name] = var
            ctk.CTkCheckBox(stores_grid, text="", variable=var, width=30).grid(row=row, column=0, padx=2, pady=3)
            ctk.CTkLabel(stores_grid, text=name, width=100, anchor="w").grid(row=row, column=1, padx=(0, 5), pady=3, sticky="w")
            path_var = ctk.StringVar(value=qbw_paths.get(name, ""))
            self.qbw_path_vars[name] = path_var
            ctk.CTkEntry(stores_grid, textvariable=path_var, width=400,
                          placeholder_text="Click Browse to select .qbw file").grid(row=row, column=2, padx=(10, 5), pady=3, sticky="w")
            ctk.CTkButton(stores_grid, text="Browse", width=70,
                           command=lambda n=name: self._browse_qbw(n)).grid(row=row, column=3, padx=2, pady=3)

        stores_grid.columnconfigure(2, weight=1)

        btn_row = ctk.CTkFrame(store_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(btn_row, text="Select All", width=90, command=lambda: [v.set(True) for v in self.store_vars.values()]).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="Deselect All", width=100, command=lambda: [v.set(False) for v in self.store_vars.values()]).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="Auto Scan D:\\QB", width=120, command=self._auto_scan_qbw,
                       fg_color="#6366f1", hover_color="#4f46e5").pack(side="left", padx=10)

        # ── Options ──
        opt_frame = ctk.CTkFrame(self)
        opt_frame.pack(fill="x", padx=15, pady=5)
        opt_inner = ctk.CTkFrame(opt_frame, fg_color="transparent")
        opt_inner.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(opt_inner, text="Data Source:").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.source_var = ctk.StringVar(value="gdrive")
        ctk.CTkRadioButton(opt_inner, text="Google Drive", variable=self.source_var, value="gdrive").grid(row=0, column=1, padx=5)
        ctk.CTkRadioButton(opt_inner, text="Local Files", variable=self.source_var, value="local").grid(row=0, column=2, padx=5)

        self.preview_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opt_inner, text="Preview only (don't create Sales Receipts)",
                         variable=self.preview_var).grid(row=1, column=0, columnspan=3, pady=(10, 0), sticky="w")
        self.strict_sync_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            opt_inner,
            text="Strict accounting mode (block sync on unmapped or unbalanced report data)",
            variable=self.strict_sync_var,
        ).grid(row=2, column=0, columnspan=3, pady=(8, 0), sticky="w")

        # ── Action Button ──
        self.sync_btn = ctk.CTkButton(self, text="Sync to QuickBooks",
                                       font=ctk.CTkFont(size=15, weight="bold"),
                                       height=45, command=self.start_sync,
                                       fg_color="#059669", hover_color="#047857")
        self.sync_btn.pack(fill="x", padx=15, pady=10)

        # ── Progress ──
        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.pack(fill="x", padx=15, pady=(0, 5))
        self.progress_bar.set(0)
        self.progress_label = ctk.CTkLabel(self, text="Ready", text_color="gray")
        self.progress_label.pack(anchor="w", padx=15)

        # ── Validation Issues ──
        issue_frame = ctk.CTkFrame(self)
        issue_frame.pack(fill="x", padx=15, pady=(5, 5))
        issue_header = ctk.CTkFrame(issue_frame, fg_color="transparent")
        issue_header.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(issue_header, text="Validation Issues", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.validation_summary = ctk.CTkLabel(issue_header, text="No validation issues yet", text_color="gray")
        self.validation_summary.pack(side="left", padx=10)
        self.export_issues_btn = ctk.CTkButton(
            issue_header,
            text="Export Issues",
            width=110,
            height=28,
            command=self._export_validation_issues,
            state="disabled",
        )
        self.export_issues_btn.pack(side="right")
        self.validation_box = ctk.CTkTextbox(issue_frame, height=140, font=ctk.CTkFont(family="Consolas", size=11))
        self.validation_box.pack(fill="x", padx=10, pady=(0, 10))
        self.validation_box.configure(state="disabled")

        # ── Log ──
        self.log_box = make_log_box(self)

    def _browse_qbw(self, store_name):
        filepath = filedialog.askopenfilename(
            title=f"Select QB Company File for {store_name}",
            filetypes=[("QuickBooks Company", "*.qbw"), ("All files", "*.*")],
            initialdir=self._local_cfg.get("last_qbw_dir", "D:\\QB"),
        )
        if filepath:
            filepath = filepath.replace("/", "\\")
            self.qbw_path_vars[store_name].set(filepath)
            self._save_qbw_paths()
            self._local_cfg["last_qbw_dir"] = str(Path(filepath).parent)
            save_local_config(self._local_cfg)

    def _auto_scan_qbw(self):
        scan_dir = filedialog.askdirectory(
            title="Select folder containing QB company files",
            initialdir=self._local_cfg.get("last_qbw_dir", "D:\\QB"),
        )
        if not scan_dir:
            return
        qbw_files = glob_mod.glob(os.path.join(scan_dir, "**", "*.qbw"), recursive=True)
        matched = 0
        for store_name, store_cfg in self._stores.items():
            qbw_match = store_cfg.get("qbw_match", "").lower()
            if not qbw_match:
                continue
            for qbw_path in qbw_files:
                fname = os.path.basename(qbw_path).lower()
                if qbw_match in fname:
                    self.qbw_path_vars[store_name].set(qbw_path.replace("/", "\\"))
                    matched += 1
                    break
        self._save_qbw_paths()
        self._local_cfg["last_qbw_dir"] = scan_dir
        save_local_config(self._local_cfg)
        messagebox.showinfo("Auto Scan", f"Found {len(qbw_files)} .qbw files\nMatched {matched}/{len(self._stores)} stores")

    def _save_qbw_paths(self):
        paths = {}
        for name, var in self.qbw_path_vars.items():
            val = var.get().strip()
            if val:
                paths[name] = val
        self._local_cfg["qbw_paths"] = paths
        save_local_config(self._local_cfg)

    def log(self, msg):
        self.after(0, lambda: append_log(self.log_box, msg))

    def update_progress(self, current, total, msg):
        self.after(0, lambda: self._set_progress(current, total, msg))

    def _set_progress(self, current, total, msg):
        if total > 0:
            self.progress_bar.set(current / total)
        self.progress_label.configure(text=f"{msg} ({current}/{total})")
        self.status_var.set(msg)

    def start_sync(self):
        if self._running:
            return
        stores = [name for name, var in self.store_vars.items() if var.get()]
        if not stores:
            messagebox.showwarning("Warning", "Please select at least one store")
            return
        dates_str = self.date_var.get().strip()
        if not dates_str:
            messagebox.showwarning("Warning", "Please enter a date")
            return
        dates = [d.strip() for d in dates_str.split(",") if d.strip()]
        for d in dates:
            try:
                datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("Error", f"Invalid date format: {d}\nUse YYYY-MM-DD")
                return
        self._set_validation_records([])
        self._running = True
        self.sync_btn.configure(state="disabled", text="Syncing...")
        threading.Thread(target=self._sync_worker,
                          args=(stores, dates, self.source_var.get(), self.preview_var.get(), self.strict_sync_var.get()),
                          daemon=True).start()

    def _sync_worker(self, stores, dates, source, preview, strict_mode):
        try:
            global_cfg, all_stores = load_mapping()
            sys.path.insert(0, str(APP_DIR))
            from qb_sync import (
                QBSyncClient,
                ToastExcelReader,
                extract_receipt_lines,
                has_blocking_issues,
                load_csv_mapping,
                find_report_file,
                summarize_validation_issues,
            )

            gdrive = None
            if source == "gdrive":
                from gdrive_service import GDriveService
                gdrive = GDriveService(on_log=self.log)
                if not gdrive.authenticate():
                    self.log("Google Drive auth failed. Falling back to local files.")
                    gdrive = None

            # Expand sub_stores
            expanded_stores = []
            for store_name in stores:
                cfg = all_stores.get(store_name)
                if not cfg:
                    self.log(f"Store '{store_name}' not found in mapping")
                    continue
                sub_stores = cfg.get("sub_stores")
                if sub_stores:
                    for sub_name, sub_cfg in sub_stores.items():
                        merged = {**cfg, **sub_cfg}
                        merged.pop("sub_stores", None)
                        merged.pop("toast_locations", None)
                        display_name = f"{store_name} {sub_name}"
                        expanded_stores.append((display_name, store_name, merged))
                else:
                    expanded_stores.append((store_name, store_name, cfg))

            total_tasks = len(expanded_stores) * len(dates)
            current_task = 0
            success_count = 0
            fail_count = 0
            validation_records = []

            # Group by qbw_match
            from collections import OrderedDict
            qbw_groups = OrderedDict()
            for display_name, orig_name, cfg in expanded_stores:
                qbw = cfg.get("qbw_match", orig_name)
                if qbw not in qbw_groups:
                    qbw_groups[qbw] = []
                qbw_groups[qbw].append((display_name, orig_name, cfg))

            for qbw_match, group_stores in qbw_groups.items():
                self.log(f"Opening QB company: {qbw_match}")

                qb_opened = False
                if not preview:
                    try:
                        from qb_automate import open_store, close_qb_completely
                        close_qb_completely()
                        time.sleep(2)

                        local_cfg = load_local_config()
                        saved_paths = local_cfg.get("qbw_paths", {})
                        first_orig_name = group_stores[0][1]
                        first_cfg = group_stores[0][2]
                        qbw_path = saved_paths.get(first_orig_name, "")

                        if not qbw_path or not os.path.exists(qbw_path):
                            self.log(f"  QB file not set or not found for '{first_orig_name}'")
                            current_task += sum(len(dates) for _ in group_stores)
                            fail_count += sum(len(dates) for _ in group_stores)
                            continue

                        store_paths = {first_orig_name: qbw_path}
                        qb_opened = open_store(first_orig_name, store_paths,
                                               qbw_match=first_cfg.get("qbw_match"),
                                               password_key=first_cfg.get("password"))
                        if qb_opened:
                            self.log(f"  QB opened for {qbw_match}")
                            time.sleep(3)
                        else:
                            self.log(f"  Failed to open QB for {qbw_match}")
                    except Exception as e:
                        self.log(f"  QB open error: {e}")

                for display_name, orig_name, store_cfg in group_stores:
                    store_cfg = load_csv_mapping(orig_name, store_cfg)

                    for date_str in dates:
                        current_task += 1
                        self.update_progress(current_task, total_tasks, f"{display_name} - {date_str}")
                        self.log(f"--- {display_name} / {date_str} ---")

                        try:
                            filepath = None
                            toast_loc = store_cfg.get("toast_location", orig_name)

                            if gdrive:
                                filename = f"SalesSummary_{date_str}_{date_str}.xlsx"
                                local_dir = str(REPORTS_DIR / toast_loc)
                                try:
                                    filepath = gdrive.download_report(toast_loc, filename, local_dir)
                                except FileNotFoundError:
                                    self.log(f"  File not found on Drive: {toast_loc}/{filename}")
                                    fail_count += 1
                                    continue
                            else:
                                files = find_report_file(orig_name, store_cfg, date_str)
                                if files:
                                    filepath = str(files[0]["filepath"])
                                else:
                                    self.log(f"  Report file not found locally for {date_str}")
                                    fail_count += 1
                                    continue

                            if not filepath or not os.path.exists(filepath):
                                self.log(f"  Report file not available")
                                fail_count += 1
                                continue

                            reader = ToastExcelReader(filepath)
                            issues = []
                            lines = extract_receipt_lines(reader, store_cfg, issues=issues)

                            if not lines:
                                self.log(f"  No data found in report")
                                fail_count += 1
                                continue

                            total_bal = sum(l["amount"] for l in lines)
                            self.log(f"  Lines: {len(lines)}, Balance: {float(total_bal):.2f}")
                            if total_bal != 0:
                                self.log("  Warning: Sales receipt lines are not balanced; verify mapping or over/short setup")
                            if issues:
                                summary = summarize_validation_issues(issues)
                                validation_records.append(
                                    {
                                        "store": display_name,
                                        "date": date_str,
                                        "report_path": filepath,
                                        "summary": summary,
                                        "issues": [issue.to_dict() for issue in issues],
                                    }
                                )
                                self.log("  Validation issues found:")
                                for issue in issues:
                                    self.log(f"    {issue.format_line()}")
                            if strict_mode and has_blocking_issues(issues):
                                self.log("  Strict mode blocked this sync because report validation issues were found")
                                fail_count += 1
                                try:
                                    reader.close()
                                except Exception:
                                    pass
                                continue
                            for l in lines:
                                amt = float(l["amount"])
                                if amt != 0:
                                    self.log(f"    {l['item_name']:<30} {amt:>10.2f}")

                            try:
                                reader.close()
                            except Exception:
                                pass

                            if preview:
                                self.log(f"  [PREVIEW MODE - not creating Sales Receipt]")
                                success_count += 1
                                continue

                            if not qb_opened:
                                self.log(f"  QB not open - skipping creation")
                                fail_count += 1
                                continue

                            qb = QBSyncClient(
                                app_name=global_cfg.get("app_name", "Toast Report Sync"),
                                qbxml_version=global_cfg.get("qbxml_version", "13.0"),
                            )
                            qb.connect()

                            customer = store_cfg.get("customer_name", "Toast")
                            prefix = store_cfg.get("sale_no_prefix", "")
                            ref_number = f"{prefix}{date_str.replace('-', '')}"
                            memo = f"Toast {toast_loc} {date_str}"

                            existing_receipts = qb.find_existing_sales_receipts(ref_number)
                            exists = any(item["txn_date"] == date_str for item in existing_receipts)
                            if exists:
                                self.log(f"  Sales Receipt #{ref_number} already exists, skipping")
                                qb.disconnect()
                                success_count += 1
                                continue
                            if existing_receipts:
                                existing_dates = ", ".join(sorted({item["txn_date"] for item in existing_receipts if item["txn_date"]}))
                                self.log(f"  Note: found same RefNumber on other date(s): {existing_dates}")

                            result = qb.create_sales_receipt(
                                txn_date=date_str,
                                ref_number=ref_number,
                                customer_name=customer,
                                memo=memo,
                                lines=lines,
                                class_name=store_cfg.get("class_name"),
                            )
                            qb.disconnect()

                            if result.get("success"):
                                self.log(f"  Sales Receipt created! TxnID: {result.get('txn_id')}")
                                success_count += 1
                            else:
                                self.log(f"  Error: {result.get('error')}")
                                fail_count += 1

                        except Exception as e:
                            self.log(f"  Error: {e}")
                            import traceback
                            self.log(traceback.format_exc())
                            fail_count += 1

                if not preview and qb_opened:
                    try:
                        from qb_automate import close_qb_completely
                        close_qb_completely()
                        time.sleep(2)
                    except Exception:
                        pass

            self.update_progress(total_tasks, total_tasks, "Done")
            self.after(0, lambda records=validation_records: self._set_validation_records(records))
            self.log(f"\nAll done! Success: {success_count}, Failed: {fail_count}")

        except Exception as e:
            self.log(f"Fatal error: {e}")
            import traceback
            self.log(traceback.format_exc())
        finally:
            self._running = False
            self.after(0, lambda: self.sync_btn.configure(state="normal", text="Sync to QuickBooks"))
            self.after(0, lambda: self.status_var.set("Ready"))

    def _set_validation_records(self, records):
        self.validation_records = records
        counts = {"error": 0, "warning": 0, "info": 0}
        lines = []
        for record in records:
            summary = record.get("summary", {})
            for key, value in summary.items():
                counts[key] = counts.get(key, 0) + value
            lines.append(f"{record['store']} | {record['date']} | {record['report_path']}")
            for issue in record.get("issues", []):
                severity = issue.get("severity", "warning").upper()
                lines.append(f"  [{severity}] {issue['code']}: {issue['message']}")
            lines.append("")

        if records:
            summary_parts = []
            if counts.get("error"):
                summary_parts.append(f"{counts['error']} error")
            if counts.get("warning"):
                summary_parts.append(f"{counts['warning']} warning")
            if counts.get("info"):
                summary_parts.append(f"{counts['info']} info")
            summary_text = ", ".join(summary_parts) if summary_parts else "Issues captured"
            summary_color = "#dc2626" if counts.get("error") else "#d97706"
            self.validation_summary.configure(text=summary_text, text_color=summary_color)
            self.export_issues_btn.configure(state="normal")
        else:
            self.validation_summary.configure(text="No validation issues yet", text_color="gray")
            self.export_issues_btn.configure(state="disabled")

        self.validation_box.configure(state="normal")
        self.validation_box.delete("1.0", "end")
        if lines:
            self.validation_box.insert("end", "\n".join(lines).strip())
        self.validation_box.configure(state="disabled")

    def _export_validation_issues(self):
        if not self.validation_records:
            messagebox.showinfo("No Issues", "There are no validation issues to export.")
            return

        QBSYNC_ISSUE_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        json_path = QBSYNC_ISSUE_DIR / f"qb-sync-validation-{timestamp}.json"
        csv_path = QBSYNC_ISSUE_DIR / f"qb-sync-validation-{timestamp}.csv"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.validation_records, f, indent=2, ensure_ascii=False)

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["store", "date", "report_path", "severity", "code", "message"])
            writer.writeheader()
            for record in self.validation_records:
                for issue in record.get("issues", []):
                    writer.writerow({
                        "store": record["store"],
                        "date": record["date"],
                        "report_path": record["report_path"],
                        "severity": issue.get("severity", "warning"),
                        "code": issue["code"],
                        "message": issue["message"],
                    })

        self.log(f"Validation issues exported -> {csv_path}")
        messagebox.showinfo("Issues Exported", f"CSV: {csv_path}\nJSON: {json_path}")


# ══════════════════════════════════════════════════════════════════════
#  Tab 3: Remove Transactions
# ══════════════════════════════════════════════════════════════════════
class RemoveTab(ctk.CTkFrame):
    def __init__(self, master, status_var, **kwargs):
        super().__init__(master, **kwargs)
        self.status_var = status_var
        self.qb = None
        self.accounts = []
        self.found_txns = []
        self._running = False
        self.delete_dry_run_var = ctk.BooleanVar(value=True)
        self._global_cfg, self._stores = load_mapping()
        self._local_cfg = load_local_config()
        self.delete_policy = load_delete_policy(self._local_cfg, self._load_qb_env())
        self._build_ui()

    def _load_qb_env(self):
        env_path = runtime_path(".env.qb")
        env_values = {}
        if not env_path.exists():
            return env_values
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env_values[key.strip()] = value.strip().strip('"').strip("'")
        except Exception:
            return {}
        return env_values

    def _build_ui(self):
        main = ctk.CTkFrame(self)
        main.pack(fill="both", expand=True, padx=5, pady=5)

        # ── Top: Store Selection + Connection ──
        top_frame = ctk.CTkFrame(main)
        top_frame.pack(fill="x", padx=10, pady=(10, 5))
        self._build_store_section(top_frame)

        # ── Left + Right panels ──
        panels = ctk.CTkFrame(main, fg_color="transparent")
        panels.pack(fill="both", expand=True, padx=5, pady=5)
        panels.grid_columnconfigure(0, weight=1, minsize=380)
        panels.grid_columnconfigure(1, weight=2, minsize=450)
        panels.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(panels)
        left.grid(row=0, column=0, sticky="nsew", padx=(5, 3), pady=5)
        self._build_filter_panel(left)

        right = ctk.CTkFrame(panels)
        right.grid(row=0, column=1, sticky="nsew", padx=(3, 5), pady=5)
        self._build_results_panel(right)

        # ── Bottom: Log ──
        log_frame = ctk.CTkFrame(main)
        log_frame.pack(fill="x", padx=10, pady=(5, 10))
        ctk.CTkLabel(log_frame, text="Log", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=10, pady=(5, 0))
        self.log_box = ctk.CTkTextbox(log_frame, height=100, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.pack(fill="x", padx=10, pady=(0, 10))

    # ── Store Section ────────────────────────────────────────────────

    def _build_store_section(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(header, text="QB Store", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.conn_status = ctk.CTkLabel(header, text="Not connected", text_color="gray")
        self.conn_status.pack(side="right", padx=10)
        self.btn_connect = ctk.CTkButton(header, text="Connect to QB", width=140, command=self._connect_qb)
        self.btn_connect.pack(side="right", padx=5)

        store_frame = ctk.CTkScrollableFrame(parent, height=130)
        store_frame.pack(fill="x", padx=10, pady=(5, 0))
        store_frame.grid_columnconfigure(0, minsize=30)
        store_frame.grid_columnconfigure(1, minsize=100)
        store_frame.grid_columnconfigure(2, weight=1)
        store_frame.grid_columnconfigure(3, minsize=70)

        qbw_paths = self._local_cfg.get("qbw_paths", {})
        self.rm_store_vars = {}
        self.rm_qbw_path_vars = {}

        for i, name in enumerate(self._stores.keys()):
            row = i
            var = ctk.BooleanVar(value=False)
            self.rm_store_vars[name] = var
            ctk.CTkCheckBox(store_frame, text="", variable=var, width=30).grid(row=row, column=0, padx=2, pady=3)
            ctk.CTkLabel(store_frame, text=name, width=100, anchor="w").grid(row=row, column=1, padx=(0, 5), pady=3, sticky="w")
            path_var = ctk.StringVar(value=qbw_paths.get(name, ""))
            self.rm_qbw_path_vars[name] = path_var
            ctk.CTkEntry(store_frame, textvariable=path_var, width=400,
                          placeholder_text="Click Browse").grid(row=row, column=2, padx=(10, 5), pady=3, sticky="ew")
            ctk.CTkButton(store_frame, text="Browse", width=70,
                           command=lambda n=name: self._browse_qbw_rm(n)).grid(row=row, column=3, padx=2, pady=3)

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(5, 10))
        ctk.CTkButton(btn_row, text="Auto Scan D:\\QB", width=130, height=28,
                       command=self._auto_scan_qbw_rm,
                       fg_color="#6366f1", hover_color="#4f46e5").pack(side="left", padx=2)

    def _browse_qbw_rm(self, store_name):
        filepath = filedialog.askopenfilename(
            title=f"Select QB Company File for {store_name}",
            filetypes=[("QuickBooks Company", "*.qbw"), ("All files", "*.*")],
            initialdir=self._local_cfg.get("last_qbw_dir", "D:\\QB"),
        )
        if filepath:
            filepath = filepath.replace("/", "\\")
            self.rm_qbw_path_vars[store_name].set(filepath)
            self._save_rm_qbw_paths()
            self._local_cfg["last_qbw_dir"] = str(Path(filepath).parent)
            save_local_config(self._local_cfg)

    def _auto_scan_qbw_rm(self):
        scan_dir = filedialog.askdirectory(
            title="Select folder containing QB company files",
            initialdir=self._local_cfg.get("last_qbw_dir", "D:\\QB"),
        )
        if not scan_dir:
            return
        qbw_files = glob_mod.glob(os.path.join(scan_dir, "**", "*.qbw"), recursive=True)
        matched = 0
        for store_name, store_cfg in self._stores.items():
            qbw_match = store_cfg.get("qbw_match", "").lower()
            if not qbw_match:
                continue
            for qbw_path in qbw_files:
                fname = os.path.basename(qbw_path).lower()
                if qbw_match in fname:
                    self.rm_qbw_path_vars[store_name].set(qbw_path.replace("/", "\\"))
                    matched += 1
                    break
        self._save_rm_qbw_paths()
        self._local_cfg["last_qbw_dir"] = scan_dir
        save_local_config(self._local_cfg)
        self._log(f"Auto Scan: Found {len(qbw_files)} .qbw files, matched {matched}/{len(self._stores)} stores")

    def _save_rm_qbw_paths(self):
        paths = {}
        for name, var in self.rm_qbw_path_vars.items():
            val = var.get().strip()
            if val:
                paths[name] = val
        self._local_cfg["qbw_paths"] = paths
        save_local_config(self._local_cfg)

    def _get_selected_store(self):
        return [(name, self.rm_qbw_path_vars[name].get().strip())
                for name, var in self.rm_store_vars.items() if var.get()]

    # ── Filter Panel ─────────────────────────────────────────────────

    def _build_filter_panel(self, parent):
        ctk.CTkLabel(parent, text="Filters", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))

        # Account selection
        acct_frame = ctk.CTkFrame(parent)
        acct_frame.pack(fill="x", padx=10, pady=5)
        acct_header = ctk.CTkFrame(acct_frame, fg_color="transparent")
        acct_header.pack(fill="x", padx=5, pady=(5, 0))
        ctk.CTkLabel(acct_header, text="Accounts:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        acct_btn_frame = ctk.CTkFrame(acct_header, fg_color="transparent")
        acct_btn_frame.pack(side="right")
        ctk.CTkButton(acct_btn_frame, text="All", width=40, height=24, command=lambda: self._select_all_accounts(True)).pack(side="left", padx=2)
        ctk.CTkButton(acct_btn_frame, text="None", width=40, height=24, command=lambda: self._select_all_accounts(False)).pack(side="left", padx=2)

        self.acct_scroll = ctk.CTkScrollableFrame(acct_frame, height=100)
        self.acct_scroll.pack(fill="x", padx=5, pady=5)
        self.acct_vars = {}
        self.acct_placeholder = ctk.CTkLabel(self.acct_scroll, text="Connect to QB to load accounts", text_color="gray")
        self.acct_placeholder.pack(pady=10)

        # Transaction type
        txn_frame = ctk.CTkFrame(parent)
        txn_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(txn_frame, text="Transaction Types:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=5, pady=(5, 0))

        self.txn_vars = {}
        txn_options = [
            ("Check", "Check"), ("Deposit", "Deposit"),
            ("JournalEntry", "Journal Entry"), ("CreditCardCharge", "CC Charge"),
            ("CreditCardCredit", "CC Credit"), ("SalesReceipt", "Sales Receipt"),
            ("Bill", "Bill"), ("BillPaymentCheck", "Bill Payment"),
        ]
        txn_grid = ctk.CTkFrame(txn_frame, fg_color="transparent")
        txn_grid.pack(fill="x", padx=5, pady=5)
        for i, (key, label) in enumerate(txn_options):
            var = ctk.BooleanVar(value=False)
            self.txn_vars[key] = var
            ctk.CTkCheckBox(txn_grid, text=label, variable=var, width=150).grid(row=i // 2, column=i % 2, sticky="w", padx=5, pady=2)

        # Date range
        date_frame = ctk.CTkFrame(parent)
        date_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(date_frame, text="Date Range:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=5, pady=(5, 0))

        quick_frame = ctk.CTkFrame(date_frame, fg_color="transparent")
        quick_frame.pack(fill="x", padx=5, pady=(5, 0))
        yesterday = datetime.now() - timedelta(days=1)
        quick_dates = [
            ("Yesterday", yesterday, yesterday),
            ("Today", datetime.now(), datetime.now()),
            ("Last 7 days", datetime.now() - timedelta(days=7), yesterday),
            ("Last 30 days", datetime.now() - timedelta(days=30), yesterday),
        ]
        for label, d_from, d_to in quick_dates:
            ctk.CTkButton(quick_frame, text=label, width=80, height=26,
                           command=lambda f=d_from, t=d_to: self._set_dates(f, t)).pack(side="left", padx=2, pady=2)

        dates_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        dates_row.pack(fill="x", padx=5, pady=5)
        ctk.CTkLabel(dates_row, text="From:").pack(side="left")
        self.date_from_var = ctk.StringVar(value=yesterday.strftime("%Y-%m-%d"))
        ctk.CTkEntry(dates_row, textvariable=self.date_from_var, width=110).pack(side="left", padx=(5, 15))
        ctk.CTkLabel(dates_row, text="To:").pack(side="left")
        self.date_to_var = ctk.StringVar(value=yesterday.strftime("%Y-%m-%d"))
        ctk.CTkEntry(dates_row, textvariable=self.date_to_var, width=110).pack(side="left", padx=(5, 0))

        # Action buttons
        btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=10)
        policy_color = "#f59e0b" if self.delete_policy.is_locked else "#dc2626"
        self.delete_policy_label = ctk.CTkLabel(
            btn_frame,
            text=f"Delete policy: {self.delete_policy.mode_label}. {self.delete_policy.guidance}",
            text_color=policy_color,
            justify="left",
            wraplength=320,
        )
        self.delete_policy_label.pack(anchor="w", padx=5, pady=(0, 6))
        self.btn_search = ctk.CTkButton(btn_frame, text="Search Transactions", height=36, command=self._search, state="disabled")
        self.btn_search.pack(fill="x", padx=5, pady=3)
        self.dry_run_checkbox = ctk.CTkCheckBox(
            btn_frame,
            text="Dry run only (export + simulate, no delete in QuickBooks)",
            variable=self.delete_dry_run_var,
        )
        self.dry_run_checkbox.pack(anchor="w", padx=5, pady=(4, 2))
        self.btn_export = ctk.CTkButton(
            btn_frame,
            text="Export Selected",
            height=32,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._export_selected,
            state="disabled",
        )
        self.btn_export.pack(fill="x", padx=5, pady=3)
        self.btn_delete = ctk.CTkButton(btn_frame, text="Delete Selected", height=36,
                                          fg_color="#c0392b", hover_color="#e74c3c",
                                          command=self._delete_selected, state="disabled")
        self.btn_delete.pack(fill="x", padx=5, pady=3)
        self._apply_delete_policy_ui()

    def _apply_delete_policy_ui(self):
        if self.delete_policy.is_locked:
            self.delete_dry_run_var.set(True)
            self.dry_run_checkbox.configure(state="disabled")
            self.btn_delete.configure(text="Run Dry Delete")
        else:
            self.dry_run_checkbox.configure(state="normal")
            self.btn_delete.configure(text="Delete Selected")

    # ── Results Panel ────────────────────────────────────────────────

    def _build_results_panel(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(header, text="Transactions Found", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.result_count = ctk.CTkLabel(header, text="", text_color="gray")
        self.result_count.pack(side="left", padx=10)

        sel_frame = ctk.CTkFrame(header, fg_color="transparent")
        sel_frame.pack(side="right")
        ctk.CTkButton(sel_frame, text="Select All", width=70, height=24, command=lambda: self._select_all_txns(True)).pack(side="left", padx=2)
        ctk.CTkButton(sel_frame, text="None", width=50, height=24, command=lambda: self._select_all_txns(False)).pack(side="left", padx=2)

        self.txn_scroll = ctk.CTkScrollableFrame(parent)
        self.txn_scroll.pack(fill="both", expand=True, padx=10, pady=10)

        hdr = ctk.CTkFrame(self.txn_scroll, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 5))
        hdr.grid_columnconfigure(0, minsize=30)
        hdr.grid_columnconfigure(1, weight=1, minsize=80)
        hdr.grid_columnconfigure(2, weight=1, minsize=80)
        hdr.grid_columnconfigure(3, weight=2, minsize=120)
        hdr.grid_columnconfigure(4, weight=1, minsize=70)
        hdr.grid_columnconfigure(5, weight=1, minsize=60)
        hdr.grid_columnconfigure(6, weight=2, minsize=120)

        for i, h in enumerate(["", "Date", "Type", "Account", "Ref#", "Amount", "Memo"]):
            ctk.CTkLabel(hdr, text=h, font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=i, sticky="w", padx=3)

        self.txn_rows = []
        self.placeholder_label = ctk.CTkLabel(self.txn_scroll, text="Search to show transactions", text_color="gray")
        self.placeholder_label.pack(pady=30)

    # ── Helpers ──────────────────────────────────────────────────────

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        self.log_box.see("end")

    def _log_safe(self, msg):
        self.after(0, lambda m=msg: self._log(m))

    def _set_dates(self, d_from, d_to):
        self.date_from_var.set(d_from.strftime("%Y-%m-%d"))
        self.date_to_var.set(d_to.strftime("%Y-%m-%d"))

    def _select_all_accounts(self, value):
        for var in self.acct_vars.values():
            var.set(value)

    def _select_all_txns(self, value):
        for var, _, _ in self.txn_rows:
            var.set(value)

    # ── Connect ──────────────────────────────────────────────────────

    def _connect_qb(self):
        if self._running:
            return
        selected_stores = self._get_selected_store()
        if not selected_stores:
            messagebox.showwarning("No Store", "Please select at least one store.")
            return
        store_name, qbw_path = selected_stores[0]
        if not qbw_path:
            messagebox.showwarning("No QB File", f"Please Browse to set .qbw file for '{store_name}'.")
            return
        if not os.path.exists(qbw_path):
            messagebox.showwarning("QB File Not Found", f"File not found:\n{qbw_path}")
            return

        store_cfg = self._stores.get(store_name, {})
        password_key = store_cfg.get("password", "pass1")

        self._running = True
        self.btn_connect.configure(state="disabled", text="Opening QB...")
        self._log(f"Opening QB for '{store_name}': {os.path.basename(qbw_path)}")
        threading.Thread(target=self._connect_worker, args=(store_name, qbw_path, password_key), daemon=True).start()

    def _connect_worker(self, store_name, qbw_path, password_key):
        try:
            from qb_automate import close_qb_completely, open_qb_with_file
            self._log_safe("Closing existing QB...")
            close_qb_completely(callback=lambda msg: self._log_safe(msg))

            self.after(0, lambda: self.btn_connect.configure(text="Logging in..."))
            success = open_qb_with_file(qbw_path, password_key=password_key,
                                         callback=lambda msg: self._log_safe(msg))
            if not success:
                raise Exception(f"Failed to open QB for '{store_name}'.")

            self.after(0, lambda: self.btn_connect.configure(text="Connecting COM..."))
            self._log_safe("Connecting via COM API...")
            time.sleep(3)

            from qb_client import QBClient
            qb = QBClient()
            qb.connect(qbw_path)

            self._log_safe("Querying accounts...")
            accounts = qb.query_all_accounts()
            bank_accounts = [a for a in accounts if a["type"] == "Bank"]
            cc_accounts = [a for a in accounts if a["type"] == "CreditCard"]

            self.qb = qb
            self.accounts = accounts
            self.after(0, lambda b=bank_accounts, c=cc_accounts: self._on_connected(b, c))

        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda m=err_msg: self._on_connect_error(m))

    def _on_connected(self, bank_accounts, cc_accounts):
        self._running = False
        self.conn_status.configure(text="Connected", text_color="#2ecc71")
        self.btn_connect.configure(text="Reconnect", state="normal")
        self.btn_search.configure(state="normal")
        self._log(f"Connected! Found {len(bank_accounts)} Bank, {len(cc_accounts)} CC accounts")

        self.acct_placeholder.pack_forget()
        for widget in self.acct_scroll.winfo_children():
            if widget != self.acct_placeholder:
                widget.destroy()
        self.acct_vars.clear()

        if bank_accounts:
            ctk.CTkLabel(self.acct_scroll, text="-- Bank --", font=ctk.CTkFont(size=11, weight="bold"), text_color="#3498db").pack(anchor="w", padx=5, pady=(5, 0))
            for acct in bank_accounts:
                var = ctk.BooleanVar(value=False)
                self.acct_vars[acct["name"]] = var
                ctk.CTkCheckBox(self.acct_scroll, text=acct["name"], variable=var).pack(anchor="w", padx=10, pady=1)

        if cc_accounts:
            ctk.CTkLabel(self.acct_scroll, text="-- Credit Card --", font=ctk.CTkFont(size=11, weight="bold"), text_color="#e67e22").pack(anchor="w", padx=5, pady=(10, 0))
            for acct in cc_accounts:
                var = ctk.BooleanVar(value=False)
                self.acct_vars[acct["name"]] = var
                ctk.CTkCheckBox(self.acct_scroll, text=acct["name"], variable=var).pack(anchor="w", padx=10, pady=1)

    def _on_connect_error(self, error):
        self._running = False
        self.conn_status.configure(text="Connection failed", text_color="#e74c3c")
        self.btn_connect.configure(text="Connect to QB", state="normal")
        self._log(f"Connection error: {error}")
        messagebox.showerror("QB Connection Error", f"Cannot connect:\n{error}")

    # ── Search ───────────────────────────────────────────────────────

    ACCOUNT_FREE_TYPES = {"SalesReceipt", "Bill", "JournalEntry"}

    def _search(self):
        if self._running or not self.qb:
            return
        selected_types = [key for key, var in self.txn_vars.items() if var.get()]
        if not selected_types:
            messagebox.showwarning("No Type", "Please select at least one transaction type.")
            return
        selected_accounts = [name for name, var in self.acct_vars.items() if var.get()]
        types_needing_account = [t for t in selected_types if t not in self.ACCOUNT_FREE_TYPES]
        if types_needing_account and not selected_accounts:
            messagebox.showwarning("No Account", f"Please select at least one account for: {', '.join(types_needing_account)}")
            return

        date_from = self.date_from_var.get()
        date_to = self.date_to_var.get()
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            messagebox.showwarning("Invalid Date", "Date must be in YYYY-MM-DD format.")
            return

        self._running = True
        self.btn_search.configure(state="disabled", text="Searching...")
        self.btn_delete.configure(state="disabled")
        self._clear_results()
        self._log(f"Searching: {', '.join(selected_types)} from {date_from} to {date_to}")
        threading.Thread(target=self._search_worker, args=(selected_types, selected_accounts, date_from, date_to), daemon=True).start()

    def _search_worker(self, txn_types, accounts, date_from, date_to):
        all_txns = []
        try:
            for txn_type in txn_types:
                if txn_type in self.ACCOUNT_FREE_TYPES:
                    txn_accounts = [""]
                else:
                    txn_accounts = accounts
                txns = self.qb.query_transactions(
                    txn_type, txn_accounts, date_from, date_to,
                    callback=lambda msg: self.after(0, lambda m=msg: self._log(m)),
                )
                all_txns.extend(txns)
            all_txns.sort(key=lambda t: (t["TxnDate"], t["Label"]))
            self.after(0, lambda: self._on_search_done(all_txns))
        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda m=err_msg: self._on_search_error(m))

    def _on_search_done(self, txns):
        self._running = False
        self.found_txns = txns
        self.btn_search.configure(state="normal", text="Search Transactions")
        self.result_count.configure(text=f"({len(txns)} found)")
        self._log(f"Search complete: {len(txns)} transactions found")

        if not txns:
            self.placeholder_label.configure(text="No transactions found")
            self.placeholder_label.pack(pady=30)
            return

        self.placeholder_label.pack_forget()
        for txn in txns:
            var = ctk.BooleanVar(value=True)
            row = ctk.CTkFrame(self.txn_scroll, fg_color="transparent")
            row.pack(fill="x", pady=1)
            row.grid_columnconfigure(0, minsize=30)
            row.grid_columnconfigure(1, weight=1, minsize=80)
            row.grid_columnconfigure(2, weight=1, minsize=80)
            row.grid_columnconfigure(3, weight=2, minsize=120)
            row.grid_columnconfigure(4, weight=1, minsize=70)
            row.grid_columnconfigure(5, weight=1, minsize=60)
            row.grid_columnconfigure(6, weight=2, minsize=120)

            ctk.CTkCheckBox(row, text="", variable=var, width=25).grid(row=0, column=0, padx=3)
            ctk.CTkLabel(row, text=txn["TxnDate"], font=ctk.CTkFont(size=11)).grid(row=0, column=1, sticky="w", padx=3)
            ctk.CTkLabel(row, text=txn["Label"], font=ctk.CTkFont(size=11)).grid(row=0, column=2, sticky="w", padx=3)
            ctk.CTkLabel(row, text=txn["Account"], font=ctk.CTkFont(size=11)).grid(row=0, column=3, sticky="w", padx=3)
            ctk.CTkLabel(row, text=txn.get("RefNumber", ""), font=ctk.CTkFont(size=11)).grid(row=0, column=4, sticky="w", padx=3)
            ctk.CTkLabel(row, text=txn.get("Amount", ""), font=ctk.CTkFont(size=11)).grid(row=0, column=5, sticky="e", padx=3)
            ctk.CTkLabel(row, text=txn.get("Memo", "")[:40], font=ctk.CTkFont(size=11)).grid(row=0, column=6, sticky="w", padx=3)
            self.txn_rows.append((var, row, txn))

        if txns:
            self.btn_delete.configure(state="normal")
            self.btn_export.configure(state="normal")

    def _on_search_error(self, error):
        self._running = False
        self.btn_search.configure(state="normal", text="Search Transactions")
        self._log(f"Search error: {error}")

    def _clear_results(self):
        for _, row, _ in self.txn_rows:
            row.destroy()
        self.txn_rows.clear()
        self.found_txns.clear()
        self.result_count.configure(text="")
        self.placeholder_label.configure(text="Search to show transactions")
        self.placeholder_label.pack(pady=30)
        self.btn_export.configure(state="disabled")

    def _selected_transactions(self):
        return [txn for var, _, txn in self.txn_rows if var.get()]

    def _export_selected(self):
        selected = self._selected_transactions()
        if not selected:
            messagebox.showwarning("Nothing Selected", "Please select transactions to export.")
            return
        audit_files = export_transactions_snapshot(
            selected,
            DELETE_AUDIT_DIR / "exports",
            "selected-transactions",
            metadata={"action": "manual_export"},
        )
        self._log(f"Exported selected transactions -> {audit_files['csv_path']}")
        messagebox.showinfo(
            "Export Complete",
            f"CSV: {audit_files['csv_path']}\nJSON: {audit_files['json_path']}",
        )

    # ── Delete ───────────────────────────────────────────────────────

    def _delete_selected(self):
        if self._running or not self.qb:
            return
        txn_list = self._selected_transactions()
        if not txn_list:
            messagebox.showwarning("Nothing Selected", "Please select transactions to delete.")
            return
        count = len(txn_list)
        snapshot_files = export_transactions_snapshot(
            txn_list,
            DELETE_AUDIT_DIR / "snapshots",
            "delete-request",
            metadata={"count": count, "dry_run": bool(self.delete_dry_run_var.get())},
        )
        self._log(f"Delete snapshot exported -> {snapshot_files['csv_path']}")

        dry_run = bool(self.delete_dry_run_var.get())
        if not dry_run and self.delete_policy.is_locked:
            self.delete_dry_run_var.set(True)
            self._log("Live delete blocked by policy; switched back to dry-run mode")
            messagebox.showerror(
                "Live Delete Locked",
                "Live delete is locked by policy.\n\n"
                "Use dry-run/export mode, or explicitly enable live delete in local-config.json "
                "or ALLOW_LIVE_DELETE=1 in .env.qb for approved maintenance windows.",
            )
            return
        if dry_run:
            if not messagebox.askyesno(
                "Dry Run Delete",
                f"Run dry delete for {count} transaction(s)?\n\nA snapshot was exported first:\n{snapshot_files['csv_path']}",
                icon="warning",
            ):
                return
        else:
            confirm_phrase = "DELETE" if count <= 20 else f"DELETE {count}"
            typed = simpledialog.askstring(
                "Confirm Delete",
                f"A delete snapshot was exported first:\n{snapshot_files['csv_path']}\n\n"
                f"Type '{confirm_phrase}' to permanently delete {count} transaction(s).",
            )
            if typed != confirm_phrase:
                self._log("Delete cancelled: confirmation phrase did not match")
                return

        self._running = True
        self.btn_delete.configure(state="disabled", text="Dry Running..." if dry_run else "Deleting...")
        self.btn_search.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self._log(f"Starting {'dry run for' if dry_run else 'deletion of'} {count} transactions...")
        threading.Thread(target=self._delete_worker, args=(txn_list, snapshot_files, dry_run), daemon=True).start()

    def _delete_worker(self, txn_list, snapshot_files, dry_run):
        audit_rows = []

        def on_progress(current, total, txn, success, msg):
            status = "OK" if success else f"FAIL: {msg}"
            log_msg = f"  [{current}/{total}] {txn['Label']} {txn['TxnDate']} {txn.get('RefNumber', '')} - {status}"
            audit_rows.append({
                **txn,
                "status": "ok" if success else "error",
                "message": msg or ("Deleted" if success else "Delete failed"),
            })
            self.after(0, lambda m=log_msg: self._log(m))

        try:
            if dry_run:
                for index, txn in enumerate(txn_list, start=1):
                    on_progress(index, len(txn_list), txn, True, "Dry run only - no delete sent to QuickBooks")
                result = {
                    "success_count": 0,
                    "fail_count": 0,
                    "errors": [],
                    "dry_run": True,
                }
            else:
                result = self.qb.delete_transactions(txn_list, callback=on_progress)
                result["dry_run"] = False

            audit_files = write_delete_audit(
                audit_rows,
                {
                    "success_count": result["success_count"],
                    "fail_count": result["fail_count"],
                    "dry_run": result["dry_run"],
                    "snapshot_csv": snapshot_files["csv_path"],
                    "snapshot_json": snapshot_files["json_path"],
                },
                DELETE_AUDIT_DIR / "results",
                "delete-run",
            )
            result["audit_files"] = audit_files
            self.after(0, lambda r=result: self._on_delete_done(r))
        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda m=err_msg: self._on_delete_error(m))

    def _on_delete_done(self, result):
        self._running = False
        self.btn_search.configure(state="normal")
        self.btn_export.configure(state="normal" if self.txn_rows else "disabled")
        mode_label = "Dry run complete" if result.get("dry_run") else "Delete complete"
        self._log(f"{mode_label}: {result['success_count']} deleted, {result['fail_count']} failed")
        if result.get("audit_files"):
            self._log(f"Audit saved -> {result['audit_files']['csv_path']}")
        if result.get("dry_run"):
            messagebox.showinfo(
                "Dry Run Complete",
                f"No QuickBooks records were deleted.\n\nAudit CSV: {result['audit_files']['csv_path']}",
            )
        else:
            messagebox.showinfo(
                "Delete Complete",
                f"Deleted: {result['success_count']}\nFailed: {result['fail_count']}\n\nAudit CSV: {result['audit_files']['csv_path']}",
            )
        self._clear_results()
        self.btn_delete.configure(text="Run Dry Delete" if self.delete_policy.is_locked else "Delete Selected", state="disabled")

    def _on_delete_error(self, error):
        self._running = False
        self.btn_search.configure(state="normal")
        self.btn_export.configure(state="normal" if self.txn_rows else "disabled")
        self.btn_delete.configure(text="Run Dry Delete" if self.delete_policy.is_locked else "Delete Selected", state="normal")
        self._log(f"Delete error: {error}")


# ══════════════════════════════════════════════════════════════════════
#  Tab 4: Settings
# ══════════════════════════════════════════════════════════════════════
class SettingsTab(ctk.CTkFrame):
    def __init__(self, master, run_diagnostics=None, **kwargs):
        super().__init__(master, **kwargs)
        self.run_diagnostics = run_diagnostics
        self.recovery_playbooks = get_recovery_playbooks()
        self._build_ui()

    def _build_ui(self):
        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)

        # ── Google Drive ──
        gdrive_frame = ctk.CTkFrame(content)
        gdrive_frame.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(gdrive_frame, text="Google Drive", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))
        self.gdrive_status = ctk.CTkLabel(gdrive_frame, text="Not connected", text_color="gray")
        self.gdrive_status.pack(anchor="w", padx=10, pady=2)
        gdrive_btns = ctk.CTkFrame(gdrive_frame, fg_color="transparent")
        gdrive_btns.pack(fill="x", padx=10, pady=(5, 10))
        ctk.CTkButton(gdrive_btns, text="Connect Google Drive", width=180, command=self._connect_gdrive).pack(side="left", padx=2)
        ctk.CTkButton(gdrive_btns, text="Setup Folders", width=130, command=self._setup_folders).pack(side="left", padx=2)
        ctk.CTkButton(gdrive_btns, text="Clear Token", width=100, fg_color="#dc2626", hover_color="#b91c1c", command=self._clear_token).pack(side="left", padx=2)

        # ── Toast Session ──
        toast_frame = ctk.CTkFrame(content)
        toast_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(toast_frame, text="Toast Session", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))
        self.toast_status = ctk.CTkLabel(toast_frame, text="No saved session", text_color="gray")
        self.toast_status.pack(anchor="w", padx=10, pady=2)
        ctk.CTkButton(toast_frame, text="Clear Session", width=120, fg_color="#dc2626",
                       hover_color="#b91c1c", command=self._clear_session).pack(anchor="w", padx=10, pady=(5, 10))

        # ── QB Configuration ──
        qb_frame = ctk.CTkFrame(content)
        qb_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(qb_frame, text="QuickBooks Desktop", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))
        env_file = runtime_path(".env.qb")
        delete_policy = load_delete_policy(load_local_config(), self._load_env_values(env_file))
        if env_file.exists():
            ctk.CTkLabel(qb_frame, text=f"Config: {env_file}", text_color="#059669").pack(anchor="w", padx=10, pady=2)
        else:
            ctk.CTkLabel(qb_frame, text="Config: .env.qb not found", text_color="#dc2626").pack(anchor="w", padx=10, pady=2)
        delete_policy_color = "#d97706" if delete_policy.is_locked else "#dc2626"
        ctk.CTkLabel(
            qb_frame,
            text=f"Delete policy: {delete_policy.mode_label} ({delete_policy.source})",
            text_color=delete_policy_color,
        ).pack(anchor="w", padx=10, pady=2)
        ctk.CTkLabel(
            qb_frame,
            text="Set local-config.json -> delete_policy.allow_live_delete=true or ALLOW_LIVE_DELETE=1 in .env.qb only during approved maintenance.",
            text_color="gray",
            font=ctk.CTkFont(size=11),
            wraplength=700,
        ).pack(anchor="w", padx=10, pady=(0, 6))
        try:
            _, stores = load_mapping()
            store_text = ", ".join(stores.keys())
            ctk.CTkLabel(qb_frame, text=f"Stores: {store_text}", text_color="gray",
                          font=ctk.CTkFont(size=11), wraplength=600).pack(anchor="w", padx=10, pady=(2, 10))
        except Exception:
            pass

        # ── Diagnostics ──
        diag_frame = ctk.CTkFrame(content)
        diag_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(diag_frame, text="Startup Diagnostics", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))
        self.diag_summary = ctk.CTkLabel(diag_frame, text="Environment check not run yet", text_color="gray")
        self.diag_summary.pack(anchor="w", padx=10, pady=2)
        diag_btn_row = ctk.CTkFrame(diag_frame, fg_color="transparent")
        diag_btn_row.pack(fill="x", padx=10, pady=(5, 5))
        ctk.CTkButton(
            diag_btn_row,
            text="Run Diagnostics",
            width=140,
            command=(lambda: self.run_diagnostics(True)) if self.run_diagnostics else None,
        ).pack(side="left", padx=2)
        self.diag_box = ctk.CTkTextbox(diag_frame, height=180, font=ctk.CTkFont(family="Consolas", size=11))
        self.diag_box.pack(fill="x", padx=10, pady=(0, 10))
        self.diag_box.configure(state="disabled")

        # ── Recovery Center ──
        recovery_frame = ctk.CTkFrame(content)
        recovery_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(recovery_frame, text="Recovery Center", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))
        ctk.CTkLabel(
            recovery_frame,
            text="Use these playbooks and actions when there is no developer available. Start with a Health Report before changing runtime files.",
            text_color="gray",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 6))

        playbook_row = ctk.CTkFrame(recovery_frame, fg_color="transparent")
        playbook_row.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkLabel(playbook_row, text="Scenario:").pack(side="left", padx=(0, 10))
        playbook_titles = [item["title"] for item in self.recovery_playbooks]
        self.playbook_var = ctk.StringVar(value=playbook_titles[0])
        self.playbook_menu = ctk.CTkOptionMenu(
            playbook_row,
            values=playbook_titles,
            variable=self.playbook_var,
            width=340,
            command=self._show_playbook,
        )
        self.playbook_menu.pack(side="left")

        recovery_btn_row = ctk.CTkFrame(recovery_frame, fg_color="transparent")
        recovery_btn_row.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkButton(recovery_btn_row, text="Export Health Report", width=150, command=self._export_health_report).pack(side="left", padx=2)
        ctk.CTkButton(recovery_btn_row, text="Create .env.qb", width=120, command=partial(self._create_runtime_file, ".env.qb.example", ".env.qb")).pack(side="left", padx=2)
        ctk.CTkButton(recovery_btn_row, text="Create local-config", width=140, command=partial(self._create_runtime_file, "local-config.example.json", "local-config.json")).pack(side="left", padx=2)
        ctk.CTkButton(recovery_btn_row, text="Open Recovery Backups", width=170, command=lambda: os.startfile(str(runtime_path("recovery-backups")))).pack(side="left", padx=2)

        reset_btn_row = ctk.CTkFrame(recovery_frame, fg_color="transparent")
        reset_btn_row.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkButton(reset_btn_row, text="Backup + Reset Toast Session", width=210, fg_color="#d97706", hover_color="#b45309", command=self._backup_clear_session).pack(side="left", padx=2)
        ctk.CTkButton(reset_btn_row, text="Backup + Reset Google Token", width=210, fg_color="#d97706", hover_color="#b45309", command=self._backup_clear_token).pack(side="left", padx=2)
        ctk.CTkButton(reset_btn_row, text="Open Runtime Folder", width=150, command=lambda: os.startfile(str(RUNTIME_DIR))).pack(side="left", padx=2)

        self.recovery_box = ctk.CTkTextbox(recovery_frame, height=220, font=ctk.CTkFont(family="Consolas", size=11))
        self.recovery_box.pack(fill="x", padx=10, pady=(0, 10))
        self.recovery_box.configure(state="disabled")
        self._show_playbook(self.playbook_var.get())
        self._refresh_recovery_status()

        # ── Appearance ──
        theme_frame = ctk.CTkFrame(content)
        theme_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(theme_frame, text="Appearance", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))
        theme_row = ctk.CTkFrame(theme_frame, fg_color="transparent")
        theme_row.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(theme_row, text="Theme:").pack(side="left", padx=(0, 10))
        self.theme_menu = ctk.CTkOptionMenu(theme_row, values=["System", "Dark", "Light"],
                                             command=lambda c: ctk.set_appearance_mode(c), width=120)
        self.theme_menu.set("Dark")
        self.theme_menu.pack(side="left")

        # ── Quick Links ──
        folder_frame = ctk.CTkFrame(content)
        folder_frame.pack(fill="x", padx=15, pady=(5, 15))
        ctk.CTkLabel(folder_frame, text="Quick Links", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=10, pady=(10, 5))
        links_row = ctk.CTkFrame(folder_frame, fg_color="transparent")
        links_row.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(links_row, text="Open Reports Folder", width=150,
                       command=lambda: os.startfile(str(REPORTS_DIR))).pack(side="left", padx=2)
        ctk.CTkButton(links_row, text="Open Map Folder", width=130,
                       command=lambda: os.startfile(str(app_path("Map")))).pack(side="left", padx=2)
        ctk.CTkButton(links_row, text="Open Project Folder", width=150,
                       command=lambda: os.startfile(str(RUNTIME_DIR))).pack(side="left", padx=2)

    def update_diagnostics(self, report):
        summary_color = "#059669"
        if report.error_count:
            summary_color = "#dc2626"
        elif report.warning_count:
            summary_color = "#d97706"

        self.diag_summary.configure(text=report.summary, text_color=summary_color)
        self.diag_box.configure(state="normal")
        self.diag_box.delete("1.0", "end")
        self.diag_box.insert("end", "\n".join(format_report_lines(report)))
        self.diag_box.configure(state="disabled")
        self._refresh_recovery_status()

    def _load_env_values(self, path):
        env_values = {}
        if not path.exists():
            return env_values
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env_values[key.strip()] = value.strip().strip('"').strip("'")
        except Exception:
            return {}
        return env_values

    def _connect_gdrive(self):
        def _worker():
            try:
                from gdrive_service import GDriveService
                gdrive = GDriveService()
                if gdrive.authenticate():
                    email = gdrive.get_user_email()
                    self.after(0, lambda: self._set_gdrive_status(f"Connected: {email}", "#059669"))
                else:
                    self.after(0, lambda: self._set_gdrive_status("Authentication failed", "#dc2626"))
            except Exception as e:
                self.after(0, lambda: self._set_gdrive_status(f"Error: {e}", "#dc2626"))
        threading.Thread(target=_worker, daemon=True).start()

    def _setup_folders(self):
        def _worker():
            try:
                from gdrive_service import GDriveService
                gdrive = GDriveService()
                if gdrive.authenticate():
                    gdrive.setup_folders(TOAST_LOCATIONS)
                    self.after(0, lambda: messagebox.showinfo("Done", "Google Drive folders created!"))
                else:
                    self.after(0, lambda: messagebox.showerror("Error", "Google Drive auth failed"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
        threading.Thread(target=_worker, daemon=True).start()

    def _clear_token(self):
        token_file = runtime_path("token.json")
        if token_file.exists():
            token_file.unlink()
            self._set_gdrive_status("Token cleared. Reconnect to authenticate.", "gray")
        self._refresh_recovery_status()

    def _clear_session(self):
        session_file = runtime_path(".toast-session.json")
        if session_file.exists():
            session_file.unlink()
            self._set_toast_status("Session cleared. Next download will require login.", "gray")
        self._refresh_recovery_status()

    def _show_playbook(self, title):
        playbook = get_playbook_by_title(title)
        text = format_playbook(playbook) if playbook else "No playbook available."
        self.recovery_box.configure(state="normal")
        self.recovery_box.delete("1.0", "end")
        self.recovery_box.insert("end", text)
        self.recovery_box.configure(state="disabled")

    def _export_health_report(self):
        app = self.winfo_toplevel()
        report = getattr(app, "diagnostics_report", None)
        bundle = export_support_bundle(load_local_config(), report)
        messagebox.showinfo("Health Report Exported", f"TXT: {bundle['txt_path']}\nJSON: {bundle['json_path']}")
        self._refresh_recovery_status()

    def _create_runtime_file(self, example_name, target_name):
        try:
            path, created = ensure_runtime_file_from_example(example_name, target_name)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        if created:
            messagebox.showinfo("Created", f"Created {path}. Review and fill any required values before production use.")
        else:
            messagebox.showinfo("Already Exists", f"{path} already exists.")
        self._refresh_recovery_status()

    def _backup_clear_session(self):
        session_file = runtime_path(".toast-session.json")
        if not session_file.exists():
            messagebox.showinfo("Toast Session", "No saved Toast session was found.")
            return
        backup_path = backup_and_remove(session_file)
        self._set_toast_status("Session reset. Next download will require login.", "gray")
        messagebox.showinfo("Toast Session Reset", f"Backup created at:\n{backup_path}\n\nNext Toast download will prompt for login again.")
        self._refresh_recovery_status()

    def _backup_clear_token(self):
        token_file = runtime_path("token.json")
        if not token_file.exists():
            messagebox.showinfo("Google Token", "No token.json file was found.")
            return
        backup_path = backup_and_remove(token_file)
        self._set_gdrive_status("Token reset. Reconnect Google Drive to authenticate again.", "gray")
        messagebox.showinfo("Google Token Reset", f"Backup created at:\n{backup_path}\n\nReconnect Google Drive before the next upload.")
        self._refresh_recovery_status()

    def _set_gdrive_status(self, text, color):
        self.gdrive_status.configure(text=text, text_color=color)

    def _set_toast_status(self, text, color):
        self.toast_status.configure(text=text, text_color=color)

    def _refresh_recovery_status(self):
        recovery_dir = runtime_path("recovery-backups")
        if not recovery_dir.exists():
            recovery_dir.mkdir(parents=True, exist_ok=True)
        token_file = runtime_path("token.json")
        if not token_file.exists() and self.gdrive_status.cget("text") == "Not connected":
            self._set_gdrive_status("No saved token. Connect Google Drive when needed.", "gray")

        session_file = runtime_path(".toast-session.json")
        if session_file.exists():
            size_kb = session_file.stat().st_size / 1024
            mtime = datetime.fromtimestamp(session_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            self._set_toast_status(f"Session saved ({size_kb:.0f} KB, updated {mtime})", "#059669")
        elif self.toast_status.cget("text") in {"No saved session", "Session cleared. Next download will require login.", "Session reset. Next download will require login."}:
            self._set_toast_status("No saved session. Next download will require login.", "gray")


# ══════════════════════════════════════════════════════════════════════
#  Main Application
# ══════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Toast POS Manager")
        self.geometry("1150x900")
        self.minsize(1000, 800)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.status_var = ctk.StringVar(value="Ready")
        self.diagnostics_report = None
        self._build_ui()
        self.run_diagnostics_async(False)

    def _build_ui(self):
        # ── Header ──
        header = ctk.CTkFrame(self, height=50, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="Toast POS Manager",
                      font=ctk.CTkFont(size=20, weight="bold")).pack(side="left", padx=20, pady=10)
        ctk.CTkLabel(header, text="v2.1 - Hardened", text_color="gray",
                      font=ctk.CTkFont(size=12)).pack(side="right", padx=20)
        self.env_status_label = ctk.CTkLabel(header, text="Environment: checking...", text_color="#d97706",
                                             font=ctk.CTkFont(size=12))
        self.env_status_label.pack(side="right", padx=(0, 12))

        # ── Tab View ──
        self.tabview = ctk.CTkTabview(self, corner_radius=8)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=(5, 0))

        dl_tab = self.tabview.add("Download Reports")
        qb_tab = self.tabview.add("QB Sync")
        rm_tab = self.tabview.add("Remove Transactions")
        settings_tab = self.tabview.add("Settings")

        self.download_tab = DownloadTab(dl_tab, self.status_var)
        self.download_tab.pack(fill="both", expand=True)

        self.qb_tab = QBSyncTab(qb_tab, self.status_var)
        self.qb_tab.pack(fill="both", expand=True)

        self.rm_tab = RemoveTab(rm_tab, self.status_var)
        self.rm_tab.pack(fill="both", expand=True)

        self.settings_tab = SettingsTab(settings_tab, run_diagnostics=self.run_diagnostics_async)
        self.settings_tab.pack(fill="both", expand=True)

        # ── Status Bar ──
        status_bar = ctk.CTkFrame(self, height=30, corner_radius=0)
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        ctk.CTkLabel(status_bar, textvariable=self.status_var,
                      font=ctk.CTkFont(size=11), text_color="gray").pack(side="left", padx=10)

    def run_diagnostics_async(self, show_popup_on_error=False):
        self.env_status_label.configure(text="Environment: checking...", text_color="#d97706")
        threading.Thread(
            target=self._run_diagnostics_worker,
            args=(show_popup_on_error,),
            daemon=True,
        ).start()

    def _run_diagnostics_worker(self, show_popup_on_error):
        report = run_environment_checks(load_local_config())
        self.after(0, lambda r=report, show=show_popup_on_error: self._on_diagnostics_ready(r, show))

    def _on_diagnostics_ready(self, report, show_popup_on_error):
        self.diagnostics_report = report
        if report.error_count:
            text = f"Environment: {report.error_count} error(s)"
            color = "#dc2626"
            self.status_var.set("Environment issues detected. Open Settings > Startup Diagnostics.")
        elif report.warning_count:
            text = f"Environment: {report.warning_count} warning(s)"
            color = "#d97706"
            self.status_var.set("Environment warnings detected. Open Settings > Startup Diagnostics.")
        else:
            text = "Environment: ready"
            color = "#059669"
            self.status_var.set("Ready")

        self.env_status_label.configure(text=text, text_color=color)
        if hasattr(self, "settings_tab"):
            self.settings_tab.update_diagnostics(report)

        if show_popup_on_error and (report.error_count or report.warning_count):
            preview_lines = format_report_lines(report)[:8]
            messagebox.showwarning("Startup Diagnostics", "\n".join(preview_lines))


def run_cli_doctor():
    report = run_environment_checks(load_local_config())
    print("\n".join(format_report_lines(report)))
    return 0 if report.error_count == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Toast POS Manager")
    parser.add_argument("--doctor-cli", action="store_true", help="Run environment diagnostics and exit")
    args = parser.parse_args(argv)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    QBSYNC_ISSUE_DIR.mkdir(parents=True, exist_ok=True)
    if args.doctor_cli:
        return run_cli_doctor()

    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
