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
from audit_utils import (
    export_transactions_snapshot,
    load_recent_item_creation_audits,
    write_delete_audit,
    write_item_creation_audit,
)
from delete_policy import load_delete_policy
from diagnostics import format_report_lines, run_environment_checks
from report_validator import validate_toast_report_file
from report_inventory import refresh_report_inventory
from report_inventory import refresh_drive_report_inventory
from date_parser import get_date_range_from_inputs
from recovery_center import (
    backup_and_remove,
    ensure_runtime_file_from_example,
    export_support_bundle,
    format_playbook,
    get_playbook_by_title,
    get_recovery_playbooks,
)
from toast_reports import DEFAULT_REPORT_TYPE_KEYS, REPORT_TYPES, build_local_report_dir, get_download_report_types
from integration_status import (
    get_auto_download_plan,
    get_auto_qb_sync_plan,
    get_safe_target_date,
    get_world_clocks,
)
from agentai_sync import (
    acknowledge_agentai_command,
    fetch_next_agentai_command,
    get_agentai_sync_settings,
    heartbeat_agentai_command,
    is_agentai_sync_ready,
    publish_integration_snapshot,
    report_agentai_command_result,
)
from worker_runtime import get_background_worker_settings, update_runtime_state, utc_now_iso

import logging
from logging.handlers import RotatingFileHandler

_LOG_DIR = runtime_path("logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_file_handler = RotatingFileHandler(
    _LOG_DIR / "app.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_app_logger = logging.getLogger("toast_pos_manager")
_app_logger.setLevel(logging.INFO)
_app_logger.addHandler(_file_handler)

MAPPING_FILE = app_path("qb-mapping.json")
LOCAL_CONFIG_FILE = runtime_path("local-config.json")
REPORTS_DIR = runtime_path("toast-reports")
AUDIT_LOG_DIR = runtime_path("audit-logs")
DELETE_AUDIT_DIR = AUDIT_LOG_DIR / "delete-transactions"
QBSYNC_ISSUE_DIR = AUDIT_LOG_DIR / "qb-sync-validation"
ITEM_CREATION_AUDIT_DIR = AUDIT_LOG_DIR / "item-creations"
QB_ITEM_CACHE_TTL_SECONDS = 300

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


def get_marketplace_paths(config: dict | None, store_name: str) -> dict[str, str]:
    config = config or {}
    return dict(((config.get("marketplace_paths") or {}).get(store_name) or {}))


def publish_agentai_snapshot_if_configured(*, config: dict | None = None, on_log=None) -> dict:
    result = publish_integration_snapshot(base_dir=APP_DIR, config=config, on_log=on_log)
    if callable(on_log) and not result.get("ok") and not result.get("skipped"):
        on_log(result.get("message", "AgentAI sync failed."))
    return result


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
    _app_logger.info(msg)


def _calendar_colors():
    """Return calendar color scheme matching the current appearance mode."""
    mode = ctk.get_appearance_mode()
    if mode == "Light":
        return dict(
            background="#f0f0f0", foreground="#1a1a1a",
            headersbackground="#1f538d", headersforeground="white",
            selectbackground="#1f538d", selectforeground="white",
            normalbackground="#ffffff", normalforeground="#1a1a1a",
            weekendbackground="#f5f5f5", weekendforeground="#1a1a1a",
            othermonthbackground="#e8e8e8", othermonthforeground="#999999",
            othermonthwebackground="#e8e8e8", othermonthweforeground="#999999",
        )
    return dict(
        background="#2b2b2b", foreground="white",
        headersbackground="#1f538d", headersforeground="white",
        selectbackground="#1f538d", selectforeground="white",
        normalbackground="#333333", normalforeground="white",
        weekendbackground="#3a3a3a", weekendforeground="white",
        othermonthbackground="#252525", othermonthforeground="#666666",
        othermonthwebackground="#252525", othermonthweforeground="#666666",
    )


def make_calendar(parent, initial_date=None):
    """Create a styled calendar widget that respects the current theme."""
    if not initial_date:
        initial_date = datetime.now() - timedelta(days=1)
    colors = _calendar_colors()
    frame = tk.Frame(parent, bg=colors["background"])
    frame.pack(pady=(5, 0))
    cal = Calendar(frame, selectmode="day",
                   year=initial_date.year, month=initial_date.month, day=initial_date.day,
                   date_pattern="yyyy-mm-dd",
                   borderwidth=0, font=("Segoe UI", 10),
                   **colors)
    cal.pack()
    return frame, cal


UI_CARD_FG = "#111827"
UI_CARD_BORDER = "#1f2a3b"
UI_SUBCARD_FG = "#0f172a"
UI_SUBCARD_BORDER = "#1e293b"
UI_MUTED_TEXT = "#94a3b8"
UI_HEADING_TEXT = "#e2e8f0"
UI_ACCENT_BLUE = "#2563eb"
UI_ACCENT_TEAL = "#0f766e"
UI_ACCENT_AMBER = "#b45309"


def style_scrollable_frame(scrollable_frame):
    try:
        scrollable_frame._scrollbar.configure(button_color="#334155", button_hover_color="#475569")
    except Exception:
        pass


def make_section_card(parent, title, subtitle=None):
    card = ctk.CTkFrame(
        parent,
        fg_color=UI_CARD_FG,
        corner_radius=18,
        border_width=1,
        border_color=UI_CARD_BORDER,
    )
    card.pack(fill="x", padx=15, pady=7)
    header = ctk.CTkFrame(card, fg_color="transparent")
    header.pack(fill="x", padx=16, pady=(14, 6))
    ctk.CTkLabel(
        header,
        text=title,
        font=ctk.CTkFont(size=16, weight="bold"),
        text_color="#f8fafc",
    ).pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(
            header,
            text=subtitle,
            font=ctk.CTkFont(size=11),
            text_color=UI_MUTED_TEXT,
            justify="left",
            wraplength=900,
        ).pack(anchor="w", pady=(3, 0))
    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="x", padx=16, pady=(0, 16))
    return card, body


def make_subcard(parent):
    return ctk.CTkFrame(
        parent,
        fg_color=UI_SUBCARD_FG,
        corner_radius=14,
        border_width=1,
        border_color=UI_SUBCARD_BORDER,
    )


def make_action_button(parent, text, command, *, tone="neutral", width=120, height=34):
    palette = {
        "neutral": ("#334155", "#475569"),
        "primary": (UI_ACCENT_BLUE, "#1d4ed8"),
        "teal": (UI_ACCENT_TEAL, "#0f5f59"),
        "amber": (UI_ACCENT_AMBER, "#92400e"),
        "danger": ("#b91c1c", "#991b1b"),
    }
    fg_color, hover_color = palette[tone]
    return ctk.CTkButton(
        parent,
        text=text,
        command=command,
        width=width,
        height=height,
        corner_radius=10,
        fg_color=fg_color,
        hover_color=hover_color,
        font=ctk.CTkFont(size=12, weight="bold"),
    )


def make_hero_banner(parent, title, subtitle, right_label=None, *, accent="#1d4ed8"):
    hero = ctk.CTkFrame(
        parent,
        fg_color="#0f172a",
        corner_radius=20,
        border_width=1,
        border_color=accent,
    )
    hero.pack(fill="x", padx=15, pady=(15, 8))
    hero_top = ctk.CTkFrame(hero, fg_color="transparent")
    hero_top.pack(fill="x", padx=18, pady=(16, 12))
    title_col = ctk.CTkFrame(hero_top, fg_color="transparent")
    title_col.pack(side="left", fill="x", expand=True)
    title_label = ctk.CTkLabel(
        title_col,
        text=title,
        font=ctk.CTkFont(size=22, weight="bold"),
        text_color="#f8fafc",
    )
    title_label.pack(anchor="w")
    subtitle_label = ctk.CTkLabel(
        title_col,
        text=subtitle,
        font=ctk.CTkFont(size=11),
        text_color="#93c5fd",
        wraplength=650,
        justify="left",
    )
    subtitle_label.pack(anchor="w", pady=(4, 0))
    chip = None
    if right_label:
        chip = ctk.CTkFrame(hero_top, fg_color="#172554", corner_radius=14, border_width=1, border_color=accent)
        chip.pack(side="right", padx=(12, 0))
        ctk.CTkLabel(
            chip,
            text=right_label,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#dbeafe",
        ).pack(padx=12, pady=10)
    return {
        "frame": hero,
        "title_label": title_label,
        "subtitle_label": subtitle_label,
        "badge": chip,
    }


# ══════════════════════════════════════════════════════════════════════
#  Tab 1: Download Reports
# ══════════════════════════════════════════════════════════════════════
class DownloadTab(ctk.CTkFrame):
    def __init__(self, master, status_var, **kwargs):
        super().__init__(master, **kwargs)
        self.status_var = status_var
        self._running = False
        self._run_completion_callback = None
        self._stop_event = threading.Event()
        self._stop_mode = None
        self._current_run_downloaded_files = []
        self._current_run_uploaded_files = []
        self._card_fg = "#111827"
        self._card_border = "#1f2a3b"
        self._subcard_fg = "#0f172a"
        self._muted_text = "#94a3b8"
        self._heading_text = "#e2e8f0"
        self._accent_blue = "#2563eb"
        self._accent_teal = "#0f766e"
        self._accent_amber = "#b45309"
        self._build_ui()

    def _make_section_card(self, parent, title, subtitle=None):
        card = ctk.CTkFrame(
            parent,
            fg_color=self._card_fg,
            corner_radius=18,
            border_width=1,
            border_color=self._card_border,
        )
        card.pack(fill="x", padx=15, pady=7)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(
            header,
            text=title,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                header,
                text=subtitle,
                font=ctk.CTkFont(size=11),
                text_color=self._muted_text,
            ).pack(anchor="w", pady=(3, 0))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=16, pady=(0, 16))
        return card, body

    def _make_subcard(self, parent):
        return ctk.CTkFrame(
            parent,
            fg_color=self._subcard_fg,
            corner_radius=14,
            border_width=1,
            border_color="#1e293b",
        )

    def _make_action_button(self, parent, text, command, *, tone="neutral", width=120):
        palette = {
            "neutral": ("#334155", "#475569"),
            "primary": (self._accent_blue, "#1d4ed8"),
            "teal": (self._accent_teal, "#0f5f59"),
            "amber": (self._accent_amber, "#92400e"),
        }
        fg_color, hover_color = palette[tone]
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=34,
            corner_radius=10,
            fg_color=fg_color,
            hover_color=hover_color,
            font=ctk.CTkFont(size=12, weight="bold"),
        )

    def _build_ui(self):
        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)
        try:
            content._scrollbar.configure(button_color="#334155", button_hover_color="#475569")
        except Exception:
            pass

        hero = ctk.CTkFrame(
            content,
            fg_color="#0f172a",
            corner_radius=20,
            border_width=1,
            border_color="#1d4ed8",
        )
        hero.pack(fill="x", padx=15, pady=(15, 8))
        hero_top = ctk.CTkFrame(hero, fg_color="transparent")
        hero_top.pack(fill="x", padx=18, pady=(16, 8))
        title_col = ctk.CTkFrame(hero_top, fg_color="transparent")
        title_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            title_col,
            text="Download Command Center",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_col,
            text="Choose store, report type, and US business date range. Then run one clean batch to Toast and Google Drive.",
            font=ctk.CTkFont(size=11),
            text_color="#93c5fd",
            wraplength=560,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        summary_chip = ctk.CTkFrame(hero_top, fg_color="#172554", corner_radius=14, border_width=1, border_color="#2563eb")
        summary_chip.pack(side="right", padx=(12, 0))
        ctk.CTkLabel(
            summary_chip,
            text="US-aware scheduling",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#dbeafe",
        ).pack(padx=12, pady=10)
        hero_stats = ctk.CTkFrame(hero, fg_color="transparent")
        hero_stats.pack(fill="x", padx=18, pady=(0, 16))
        for text in (
            "1. Pick dates using store time in the USA",
            "2. Select stores and the Toast reports you need",
            "3. Auto-fill gaps or run a full batch now",
        ):
            chip = ctk.CTkFrame(hero_stats, fg_color="#111c31", corner_radius=12, border_width=1, border_color="#1e3a8a")
            chip.pack(side="left", padx=(0, 8))
            ctk.CTkLabel(
                chip,
                text=text,
                font=ctk.CTkFont(size=10),
                text_color="#bfdbfe",
            ).pack(padx=10, pady=8)

        # ── Date Section ──
        _date_card, date_frame = self._make_section_card(
            content,
            "Date Range",
            "Use US business dates so each store downloads the correct reporting window.",
        )

        cal_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        cal_row.pack(fill="x", pady=(0, 8))

        yesterday_str = get_safe_target_date(TOAST_LOCATIONS, include_today=False)
        yesterday = datetime.strptime(yesterday_str, "%Y-%m-%d")

        # Start Date
        start_col = self._make_subcard(cal_row)
        start_col.pack(side="left", padx=(0, 14), fill="y")
        ctk.CTkLabel(start_col, text="Start Date", font=ctk.CTkFont(size=12, weight="bold"), text_color=self._heading_text).pack(anchor="w", padx=12, pady=(12, 0))
        self.start_date_var = ctk.StringVar(value=yesterday_str)
        self.start_cal_frame, self.start_cal = make_calendar(start_col, yesterday)
        start_entry_row = ctk.CTkFrame(start_col, fg_color="transparent")
        start_entry_row.pack(fill="x", padx=12, pady=(8, 12))
        self.start_date_entry = ctk.CTkEntry(start_entry_row, textvariable=self.start_date_var, width=150, fg_color="#111827", border_color="#475569")
        self.start_date_entry.pack(side="left")
        self.start_date_entry.bind("<Return>", lambda e: self._sync_start_cal())
        self.start_cal.bind("<<CalendarSelected>>", lambda e: self._on_start_date_selected())

        # End Date
        end_col = self._make_subcard(cal_row)
        end_col.pack(side="left", padx=(0, 14), fill="y")
        ctk.CTkLabel(end_col, text="End Date", font=ctk.CTkFont(size=12, weight="bold"), text_color=self._heading_text).pack(anchor="w", padx=12, pady=(12, 0))
        self.end_date_var = ctk.StringVar(value=yesterday_str)
        self.end_cal_frame, self.end_cal = make_calendar(end_col, yesterday)
        end_entry_row = ctk.CTkFrame(end_col, fg_color="transparent")
        end_entry_row.pack(fill="x", padx=12, pady=(8, 12))
        self.end_date_entry = ctk.CTkEntry(end_entry_row, textvariable=self.end_date_var, width=150, fg_color="#111827", border_color="#475569")
        self.end_date_entry.pack(side="left")
        self.end_date_entry.bind("<Return>", lambda e: self._sync_end_cal())
        self.end_cal.bind("<<CalendarSelected>>", lambda e: self._on_end_date_selected())

        # Date List Preview (scrollable, same height as calendar)
        list_col = self._make_subcard(cal_row)
        list_col.pack(side="left", padx=(0, 0), fill="y")
        ctk.CTkLabel(list_col, text="Date List", font=ctk.CTkFont(size=12, weight="bold"), text_color=self._heading_text).pack(anchor="w", padx=12, pady=(12, 0))
        self.date_list_box = ctk.CTkTextbox(list_col, width=200, height=210,
                                            font=ctk.CTkFont(family="Consolas", size=11),
                                            state="disabled", fg_color="#111827",
                                            text_color="#e2e8f0", border_width=1,
                                            border_color="#334155")
        self.date_list_box.pack(padx=12, pady=(8, 12), fill="y", expand=True)

        info_bar = ctk.CTkFrame(date_frame, fg_color="#101827", corner_radius=12, border_width=1, border_color="#1e3a8a")
        info_bar.pack(fill="x", pady=(4, 8))
        self.date_info_label = ctk.CTkLabel(info_bar, text="", text_color="#60a5fa", font=ctk.CTkFont(size=12, weight="bold"))
        self.date_info_label.pack(anchor="w", padx=12, pady=10)
        self._update_date_info()

        # Quick buttons
        btn_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        btn_row.pack(fill="x")

        def set_single_date(include_today):
            d_str = self._selection_target_date(include_today=include_today)
            d = datetime.strptime(d_str, "%Y-%m-%d")
            self.start_date_var.set(d_str)
            self.end_date_var.set(d_str)
            self.start_cal.selection_set(d)
            self.end_cal.selection_set(d)
            self._update_date_info()

        def set_last_n_days(n):
            end = datetime.strptime(self._selection_target_date(include_today=False), "%Y-%m-%d")
            start = end - timedelta(days=max(n - 1, 0))
            self.start_date_var.set(start.strftime("%Y-%m-%d"))
            self.end_date_var.set(end.strftime("%Y-%m-%d"))
            self.start_cal.selection_set(start)
            self.end_cal.selection_set(end)
            self._update_date_info()

        self._make_action_button(btn_row, "Yesterday (US)", lambda: set_single_date(False), tone="neutral", width=120).pack(side="left", padx=(0, 8))
        self._make_action_button(btn_row, "Today (US)", lambda: set_single_date(True), tone="primary", width=110).pack(side="left", padx=(0, 8))
        self._make_action_button(btn_row, "Last 7 days", lambda: set_last_n_days(7), tone="neutral", width=110).pack(side="left", padx=(0, 8))
        self._make_action_button(btn_row, "Last 30 days", lambda: set_last_n_days(30), tone="neutral", width=118).pack(side="left")

        # ── Locations Section ──
        _loc_card, loc_frame = self._make_section_card(
            content,
            "Toast Locations",
            "Pick the stores you want to include in the next download batch.",
        )

        checks_frame = ctk.CTkFrame(loc_frame, fg_color="transparent")
        checks_frame.pack(fill="x", pady=(0, 8))
        self.loc_vars = {}
        for i, loc in enumerate(TOAST_LOCATIONS):
            var = ctk.BooleanVar(value=True)
            self.loc_vars[loc] = var
            chip = self._make_subcard(checks_frame)
            chip.grid(row=i // 4, column=i % 4, padx=6, pady=6, sticky="ew")
            ctk.CTkCheckBox(chip, text=loc, variable=var, width=130).pack(anchor="w", padx=12, pady=10)
        for col in range(4):
            checks_frame.grid_columnconfigure(col, weight=1)

        btn_row2 = ctk.CTkFrame(loc_frame, fg_color="transparent")
        btn_row2.pack(fill="x")
        self._make_action_button(btn_row2, "Select All Stores", lambda: [v.set(True) for v in self.loc_vars.values()], tone="neutral", width=130).pack(side="left", padx=(0, 8))
        self._make_action_button(btn_row2, "Clear Selection", lambda: [v.set(False) for v in self.loc_vars.values()], tone="amber", width=122).pack(side="left")

        _report_card, report_frame = self._make_section_card(
            content,
            "Report Types",
            "Choose the exact Toast exports you want to pull for each selected store.",
        )

        report_checks = ctk.CTkFrame(report_frame, fg_color="transparent")
        report_checks.pack(fill="x", pady=(0, 8))
        self.report_type_vars = {}
        for idx, report in enumerate(get_download_report_types()):
            var = ctk.BooleanVar(value=report.key in DEFAULT_REPORT_TYPE_KEYS)
            self.report_type_vars[report.key] = var
            chip = self._make_subcard(report_checks)
            chip.grid(row=idx // 4, column=idx % 4, padx=6, pady=6, sticky="ew")
            ctk.CTkCheckBox(chip, text=report.label, variable=var, width=140).pack(anchor="w", padx=12, pady=10)
        for col in range(4):
            report_checks.grid_columnconfigure(col, weight=1)

        report_btn_row = ctk.CTkFrame(report_frame, fg_color="transparent")
        report_btn_row.pack(fill="x")
        self._make_action_button(report_btn_row, "Select All Reports", lambda: [v.set(True) for v in self.report_type_vars.values()], tone="neutral", width=136).pack(side="left", padx=(0, 8))
        self._make_action_button(report_btn_row, "Sales Summary Only", self._select_sales_summary_only, tone="neutral", width=152).pack(side="left", padx=(0, 8))
        self._make_action_button(
            report_btn_row,
            "Auto Fill to Yesterday",
            lambda: self._apply_auto_download_plan(False),
            tone="teal",
            width=160,
        ).pack(side="left", padx=(8, 8))
        self._make_action_button(
            report_btn_row,
            "Auto Fill to Today",
            lambda: self._apply_auto_download_plan(True),
            tone="primary",
            width=150,
        ).pack(side="left")

        # ── Options ──
        _opt_card, opt_frame = self._make_section_card(
            content,
            "Run Options",
            "Choose what should happen automatically after each download finishes.",
        )
        self.upload_gdrive_var = ctk.BooleanVar(value=True)
        option_chip = self._make_subcard(opt_frame)
        option_chip.pack(fill="x")
        gdrive_row = ctk.CTkFrame(option_chip, fg_color="transparent")
        gdrive_row.pack(fill="x", padx=12, pady=12)
        ctk.CTkCheckBox(gdrive_row, text="Upload to Google Drive after download", variable=self.upload_gdrive_var).pack(side="left")
        def _open_drive():
            import threading, webbrowser
            def _worker():
                try:
                    from gdrive_service import GDriveService
                    gdrive = GDriveService()
                    if not gdrive.authenticate():
                        return
                    root_id = gdrive._get_primary_root_folder()
                    if root_id:
                        webbrowser.open(f"https://drive.google.com/drive/folders/{root_id}")
                except Exception:
                    pass
            threading.Thread(target=_worker, daemon=True).start()
        make_action_button(gdrive_row, "Open Google Drive", _open_drive, tone="neutral", width=150).pack(side="right")

        _run_card, run_frame = self._make_section_card(
            content,
            "Execution",
            "Launch the batch and monitor live progress for the selected stores and report types.",
        )
        action_row = ctk.CTkFrame(run_frame, fg_color="transparent")
        action_row.pack(fill="x", pady=(0, 12))
        self.download_btn = ctk.CTkButton(
            action_row,
            text="Download Reports",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            command=self.start_download,
            fg_color=self._accent_blue,
            hover_color="#1d4ed8",
            corner_radius=14,
        )
        self.download_btn.pack(side="left", fill="x", expand=True)
        self.stop_save_btn = ctk.CTkButton(
            action_row,
            text="Stop + Save",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=130,
            height=50,
            command=self.stop_and_save,
            fg_color="#0f766e",
            hover_color="#0f5f59",
            corner_radius=14,
            state="disabled",
        )
        self.stop_save_btn.pack(side="left", padx=(12, 0))
        self.stop_unsave_btn = ctk.CTkButton(
            action_row,
            text="Stop + Unsave",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=140,
            height=50,
            command=self.stop_and_unsave,
            fg_color="#b45309",
            hover_color="#92400e",
            corner_radius=14,
            state="disabled",
        )
        self.stop_unsave_btn.pack(side="left", padx=(12, 0))
        tip_chip = ctk.CTkFrame(action_row, fg_color="#111827", corner_radius=14, border_width=1, border_color="#334155")
        tip_chip.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(
            tip_chip,
            text="Best practice: run missing dates first, then QB sync.",
            font=ctk.CTkFont(size=10),
            text_color="#cbd5e1",
            wraplength=180,
            justify="left",
        ).pack(padx=12, pady=10)

        # ── Progress ──
        progress_wrap = self._make_subcard(run_frame)
        progress_wrap.pack(fill="x")
        self.progress_bar = ctk.CTkProgressBar(progress_wrap, progress_color=self._accent_blue, fg_color="#1e293b")
        self.progress_bar.pack(fill="x", padx=12, pady=(12, 6))
        self.progress_bar.set(0)
        self.progress_label = ctk.CTkLabel(progress_wrap, text="Ready", text_color="#94a3b8", font=ctk.CTkFont(size=12, weight="bold"))
        self.progress_label.pack(anchor="w", padx=12, pady=(0, 12))

        # ── Log ──
        log_frame = ctk.CTkFrame(
            content,
            fg_color=self._card_fg,
            corner_radius=18,
            border_width=1,
            border_color=self._card_border,
        )
        log_frame.pack(fill="both", expand=True, padx=15, pady=(7, 15))
        self.log_box = make_log_box(log_frame, height=220)

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
        """Update date info label in DownloadTab. Uses flexible date parsing."""
        from date_parser import parse_date_flexible

        start_str = self.start_date_var.get().strip()
        end_str = self.end_date_var.get().strip()

        if not start_str or not end_str:
            self.date_info_label.configure(text="Enter both dates to see range", text_color="#9ca3af")
            return

        s_result = parse_date_flexible(start_str, "qb_sync_ui")
        e_result = parse_date_flexible(end_str, "qb_sync_ui")

        if not s_result.success or not e_result.success:
            err = s_result.error if not s_result.success else e_result.error
            self.date_info_label.configure(text=f"⚠ {err}", text_color="#f87171")
            return

        s_iso = s_result.value.date_str
        e_iso = e_result.value.date_str

        s_dt = datetime.strptime(s_iso, "%Y-%m-%d")
        e_dt = datetime.strptime(e_iso, "%Y-%m-%d")
        days = (e_dt - s_dt).days + 1

        if days < 1:
            self.date_info_label.configure(text="⚠ End date before start date", text_color="#f87171")
            self._set_date_list([])
        elif days == 1:
            self.date_info_label.configure(
                text=f"1 day selected: {s_dt.strftime('%b %d, %Y')}", text_color="#60a5fa"
            )
            self._set_date_list([s_dt])
        else:
            self.date_info_label.configure(
                text=f"{days} days selected: {s_dt.strftime('%b %d')} - {e_dt.strftime('%b %d, %Y')}",
                text_color="#60a5fa",
            )
            date_list = []
            cur = s_dt
            while cur <= e_dt:
                date_list.append(cur)
                cur += timedelta(days=1)
            self._set_date_list(date_list)

    def _set_date_list(self, dates):
        """Populate the date list preview panel."""
        self.date_list_box.configure(state="normal")
        self.date_list_box.delete("1.0", "end")
        for i, dt in enumerate(dates):
            line = f"{i+1:>3}.  {dt.strftime('%m/%d/%Y')}  {dt.strftime('%a')}\n"
            self.date_list_box.insert("end", line)
        if not dates:
            self.date_list_box.insert("end", "No dates")
        self.date_list_box.configure(state="disabled")

    def _get_date_range(self):
        """Get list of date strings from start/end calendar inputs."""
        start_str = self.start_date_var.get().strip()
        end_str = self.end_date_var.get().strip()
        success, dates, error = get_date_range_from_inputs(start_str, end_str)
        if not success:
            if "enter" in error.lower():
                messagebox.showwarning("Warning", error)
            else:
                messagebox.showerror("Error", error)
            return None
        return dates

    def _select_sales_summary_only(self):
        for key, var in self.report_type_vars.items():
            var.set(key == "sales_summary")

    def _selected_locations(self):
        return [loc for loc, var in self.loc_vars.items() if var.get()]

    def _selection_target_date(self, include_today=False):
        locations = self._selected_locations() or TOAST_LOCATIONS
        return get_safe_target_date(locations, include_today=include_today)

    def _remember_downloaded_file(self, filepath):
        file_path = Path(filepath)
        if not file_path.exists():
            return
        if file_path not in self._current_run_downloaded_files:
            self._current_run_downloaded_files.append(file_path)

    def _remember_uploaded_file(self, *, file_id, filepath, store_name, report_key):
        if not file_id:
            return
        record = {
            "file_id": str(file_id),
            "filepath": str(filepath),
            "store_name": store_name,
            "report_key": report_key,
        }
        if record not in self._current_run_uploaded_files:
            self._current_run_uploaded_files.append(record)

    def _set_download_controls(self, *, running):
        if running:
            self.download_btn.configure(state="disabled", text="Downloading...")
            self.stop_save_btn.configure(state="normal")
            self.stop_unsave_btn.configure(state="normal")
        else:
            self.download_btn.configure(state="normal", text="Download Reports")
            self.stop_save_btn.configure(state="disabled")
            self.stop_unsave_btn.configure(state="disabled")

    def stop_and_save(self):
        if not self._running:
            return
        if not self._stop_event.is_set():
            self._stop_mode = "save"
            self._stop_event.set()
            self.log("Stop requested. The app will finish the current item, keep saved files, then stop the batch.")
            self.status_var.set("Stopping after current item...")

    def stop_and_unsave(self):
        if not self._running:
            return
        if not self._stop_event.is_set():
            self._stop_mode = "unsave"
            self._stop_event.set()
            self.log("Stop + Unsave requested. The app will finish the current item, then roll back files saved in this batch.")
            self.status_var.set("Stopping and rolling back current batch...")

    def _rollback_current_run(self, gdrive=None):
        removed_drive = 0
        removed_local = 0
        if gdrive:
            for item in reversed(self._current_run_uploaded_files):
                try:
                    gdrive.delete_file(item["file_id"])
                    removed_drive += 1
                    self.log(
                        f"  Removed Drive file: {item['store_name']} / "
                        f"{REPORT_TYPES.get(item['report_key'], REPORT_TYPES['sales_summary']).label} / "
                        f"{Path(item['filepath']).name}"
                    )
                except Exception as exc:
                    self.log(f"  Could not remove Drive file {item['file_id']}: {exc}")
        for file_path in reversed(self._current_run_downloaded_files):
            try:
                if file_path.exists():
                    file_path.unlink()
                    removed_local += 1
                    self.log(f"  Removed local file: {file_path.name}")
            except Exception as exc:
                self.log(f"  Could not remove local file {file_path.name}: {exc}")
        return {"removed_local": removed_local, "removed_drive": removed_drive}

    def queue_download_run(
        self,
        *,
        locations,
        report_types,
        start_date,
        end_date,
        upload_to_gdrive=True,
        completion_callback=None,
    ):
        if self._running:
            return False, "Download is already running."
        self._run_completion_callback = completion_callback
        for loc, var in self.loc_vars.items():
            var.set(loc in set(locations))
        for key, var in self.report_type_vars.items():
            var.set(key in set(report_types))
        self.upload_gdrive_var.set(bool(upload_to_gdrive))
        self.start_date_var.set(start_date)
        self.end_date_var.set(end_date)
        self.start_cal.selection_set(datetime.strptime(start_date, "%Y-%m-%d"))
        self.end_cal.selection_set(datetime.strptime(end_date, "%Y-%m-%d"))
        self._update_date_info()
        self.start_download()
        return True, "Download command started."

    def _apply_auto_download_plan(self, include_today=False):
        locations = self._selected_locations()
        if not locations:
            messagebox.showwarning("Warning", "Please select at least one location")
            return
        report_types = [key for key, var in self.report_type_vars.items() if var.get()]
        if not report_types:
            messagebox.showwarning("Warning", "Please select at least one report type")
            return

        plan = get_auto_download_plan(locations, report_types, include_today=include_today)
        if not plan["has_gap"]:
            messagebox.showinfo("Downloads Up To Date", plan["message"])
            return

        start_dt = datetime.strptime(plan["start_date"], "%Y-%m-%d")
        end_dt = datetime.strptime(plan["end_date"], "%Y-%m-%d")
        self.start_date_var.set(plan["start_date"])
        self.end_date_var.set(plan["end_date"])
        self.start_cal.selection_set(start_dt)
        self.end_cal.selection_set(end_dt)
        self._update_date_info()
        self.log(plan["message"])

    def start_download(self):
        if self._running:
            return
        locations = [loc for loc, var in self.loc_vars.items() if var.get()]
        if not locations:
            messagebox.showwarning("Warning", "Please select at least one location")
            return
        report_types = [key for key, var in self.report_type_vars.items() if var.get()]
        if not report_types:
            messagebox.showwarning("Warning", "Please select at least one report type")
            return
        dates = self._get_date_range()
        if not dates:
            return
        self._running = True
        self._stop_event.clear()
        self._stop_mode = None
        self._current_run_downloaded_files = []
        self._current_run_uploaded_files = []
        self._set_download_controls(running=True)
        threading.Thread(target=self._download_worker, args=(locations, dates, report_types), daemon=True).start()

    def _download_worker(self, locations, dates, report_types):
        completion_payload = {
            "ok": False,
            "message": "Download did not complete.",
            "success": 0,
            "failed": 0,
            "total": 0,
        }
        silent_mode = False
        gdrive = None
        gdrive_ready = False
        try:
            from toast_downloader import ToastDownloader, ToastLoginRequiredError
            app_root = self.winfo_toplevel()
            headless_downloads = bool(getattr(app_root, "headless_downloads", False))
            runtime_mode = str(getattr(app_root, "runtime_mode", "gui") or "gui")
            silent_mode = bool(getattr(app_root, "silent_mode", False))
            if runtime_mode != "headless_worker":
                headless_downloads = False

            selected_report_names = [REPORT_TYPES[key].label for key in report_types]
            self.log(
                f"Starting download for {len(locations)} locations, {len(dates)} day(s), "
                f"{len(report_types)} report type(s): {', '.join(selected_report_names)}"
            )
            if headless_downloads:
                self.log("Download browser mode: headless worker")
            else:
                self.log("Download browser mode: interactive window")

            drive_skip_keys = set()  # (store, report_key, business_date) already on Drive
            if self.upload_gdrive_var.get():
                try:
                    from gdrive_service import GDriveService
                    gdrive = GDriveService(on_log=self.log)
                    gdrive_ready = gdrive.authenticate()
                    if gdrive_ready:
                        self.log("Google Drive ready. Each report will upload immediately after it is available.")
                        # Pre-scan Drive to skip already-uploaded reports
                        try:
                            self.log("Scanning Google Drive for existing reports...")
                            drive_rows = gdrive.scan_report_inventory(
                                store_names=locations,
                                report_types=report_types,
                            )
                            for row in drive_rows:
                                if row.get("business_date"):
                                    drive_skip_keys.add((
                                        row["store"],
                                        row["report_key"],
                                        row["business_date"],
                                    ))
                            if drive_skip_keys:
                                self.log(f"Found {len(drive_skip_keys)} reports already on Drive — will skip those.")
                            else:
                                self.log("No existing reports found on Drive for selected range.")
                        except Exception as scan_err:
                            self.log(f"Drive scan warning: {scan_err} — will download all.")
                    else:
                        self.log("Google Drive authentication failed - uploads will be skipped.")
                except Exception as e:
                    self.log(f"Google Drive error: {e}")
                    gdrive = None
                    gdrive_ready = False

            def _handle_report_file(file_info):
                if not file_info:
                    return
                filepath = file_info.get("filepath")
                status = file_info.get("status") or file_info.get("source") or ""
                if status == "downloaded" and filepath:
                    self._remember_downloaded_file(filepath)
                if not gdrive_ready or not gdrive:
                    return
                if not filepath or not Path(filepath).exists():
                    return
                store_name = file_info.get("location") or file_info.get("store")
                report_key = file_info.get("report_key", "sales_summary")
                try:
                    if status == "existing_local":
                        existing_drive = gdrive.report_exists(store_name, Path(filepath).name, report_key)
                        if existing_drive:
                            self.log(
                                f"  Drive already has {store_name} / {file_info.get('report_label', report_key)} / "
                                f"{Path(filepath).name}"
                                )
                            return
                    uploaded_id = gdrive.upload_report(filepath, store_name, report_type=report_key)
                    self._remember_uploaded_file(
                        file_id=uploaded_id,
                        filepath=filepath,
                        store_name=store_name,
                        report_key=report_key,
                    )
                except Exception as upload_error:
                    self.log(
                        f"  Upload error for {store_name} / {file_info.get('report_label', report_key)}: {upload_error}"
                    )

            downloader = ToastDownloader(
                download_dir=str(REPORTS_DIR),
                headless=headless_downloads,
                on_log=self.log,
                on_progress=self.update_progress,
                on_report_file=_handle_report_file,
                should_stop=self._stop_event.is_set,
            )

            toast_dates = []
            for d in dates:
                dt = datetime.strptime(d, "%Y-%m-%d")
                toast_dates.append(dt.strftime("%m/%d/%Y"))

            results = downloader.download_reports_daterange(
                locations=locations, dates=toast_dates, report_types=report_types,
                drive_skip_keys=drive_skip_keys,
            )
            completion_payload = {
                "ok": results["success"] == results["total"] and not results.get("stopped"),
                "message": f"Prepared {results['success']} of {results['total']} files.",
                "success": results["success"],
                "failed": max(results["total"] - results["success"], 0),
                "total": results["total"],
                "stopped": bool(results.get("stopped")),
            }
            if results.get("skipped"):
                completion_payload["message"] += f" Skipped {results['skipped']} existing file(s)."
            if results.get("stopped"):
                rollback_result = {"removed_local": 0, "removed_drive": 0}
                if self._stop_mode == "unsave":
                    rollback_result = self._rollback_current_run(gdrive if gdrive_ready else None)
                    completion_payload["message"] = (
                        f"Stopped and rolled back current batch. Removed {rollback_result['removed_local']} local file(s)"
                        f" and {rollback_result['removed_drive']} Drive file(s)."
                    )
                else:
                    completion_payload["message"] = (
                        f"Stopped after current item. Kept {len(self._current_run_downloaded_files)} downloaded file(s)."
                    )

            self.log(
                f"All done! {results['success']}/{results['total']} prepared "
                f"({results.get('skipped', 0)} skipped existing)"
            )

        except ToastLoginRequiredError as e:
            self.log(f"Error: {e}")
            completion_payload = {"ok": False, "message": str(e), "success": 0, "failed": len(locations) * len(dates), "total": len(locations) * len(dates)}
            if not silent_mode:
                self.after(0, lambda msg=str(e): messagebox.showwarning("Toast Login Required", msg))
        except Exception as e:
            self.log(f"Error: {e}")
            import traceback
            self.log(traceback.format_exc())
            completion_payload = {"ok": False, "message": str(e), "success": 0, "failed": len(locations) * len(dates), "total": len(locations) * len(dates)}
        finally:
            refresh_report_inventory(APP_DIR)
            publish_agentai_snapshot_if_configured(on_log=self.log)
            completion_callback = self._run_completion_callback
            self._run_completion_callback = None
            if completion_callback:
                self.after(0, lambda payload=completion_payload, cb=completion_callback: cb(payload))
            self._running = False
            self.after(0, lambda: self._set_download_controls(running=False))
            self.after(0, lambda: self.status_var.set("Ready"))
            self.after(0, lambda: self.progress_bar.set(0))
            self.after(0, lambda: self.progress_label.configure(text="Ready", text_color="gray"))


# ══════════════════════════════════════════════════════════════════════
#  Tab 2: QB Integration (Sync Sales Receipts)
# ══════════════════════════════════════════════════════════════════════
class QBSyncTab(ctk.CTkFrame):
    def __init__(self, master, status_var, **kwargs):
        super().__init__(master, **kwargs)
        self.status_var = status_var
        self._running = False
        self._run_completion_callback = None
        self._global_cfg, self._stores = load_mapping()
        self.validation_records = []
        self.mapping_candidates = []
        self.mapping_candidate_index = {}
        self.mapping_saved_keys = set()
        self.selected_mapping_candidate = None
        self.pending_force_reruns = {}
        self.last_sync_run = None
        self.marketplace_path_vars = {}
        self.marketplace_status_labels = {}
        self.marketplace_source_meta = []
        self._build_ui()

    def _build_ui(self):
        # ── Scrollable container for entire tab ──
        self._scroll_container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll_container.pack(fill="both", expand=True)
        style_scrollable_frame(self._scroll_container)
        _parent = self._scroll_container
        make_hero_banner(
            _parent,
            "QuickBooks Sync Center",
            "Review date range, store files, and source filters before posting sales receipts or running a safe preview.",
            "Posting workflow",
            accent="#059669",
        )

        # ── Date Range Section ──
        _date_card, date_frame = make_section_card(
            _parent,
            "Date Range",
            "Pick the QB sync window using US business dates, then use auto-fill to catch up missing Toast sales.",
        )

        yesterday = get_safe_target_date(include_today=False)
        yesterday_dt = datetime.strptime(yesterday, "%Y-%m-%d")
        self.date_from_var = ctk.StringVar(value=yesterday)
        self.date_to_var = ctk.StringVar(value=yesterday)
        self.date_var = ctk.StringVar(value=yesterday)

        # ── Calendar widgets (matching Download tab) ──────────────────────
        cal_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        cal_row.pack(fill="x", pady=(0, 8))

        # Start Date
        start_col = make_subcard(cal_row)
        start_col.pack(side="left", padx=(0, 14), fill="y")
        ctk.CTkLabel(start_col, text="Start Date", font=ctk.CTkFont(size=12, weight="bold"), text_color=UI_HEADING_TEXT).pack(anchor="w", padx=12, pady=(12, 0))
        self.start_cal_frame, self.start_cal = make_calendar(start_col, yesterday_dt)
        start_entry_row = ctk.CTkFrame(start_col, fg_color="transparent")
        start_entry_row.pack(fill="x", padx=12, pady=(8, 12))
        self.start_date_entry = ctk.CTkEntry(start_entry_row, textvariable=self.date_from_var, width=150, fg_color="#111827", border_color="#475569")
        self.start_date_entry.pack(side="left")
        self.start_date_entry.bind("<Return>", lambda e: self._sync_start_cal())
        self.start_cal.bind("<<CalendarSelected>>", lambda e: self._on_start_date_selected())

        # End Date
        end_col = make_subcard(cal_row)
        end_col.pack(side="left", padx=(0, 14), fill="y")
        ctk.CTkLabel(end_col, text="End Date", font=ctk.CTkFont(size=12, weight="bold"), text_color=UI_HEADING_TEXT).pack(anchor="w", padx=12, pady=(12, 0))
        self.end_cal_frame, self.end_cal = make_calendar(end_col, yesterday_dt)
        end_entry_row = ctk.CTkFrame(end_col, fg_color="transparent")
        end_entry_row.pack(fill="x", padx=12, pady=(8, 12))
        self.end_date_entry = ctk.CTkEntry(end_entry_row, textvariable=self.date_to_var, width=150, fg_color="#111827", border_color="#475569")
        self.end_date_entry.pack(side="left")
        self.end_date_entry.bind("<Return>", lambda e: self._sync_end_cal())
        self.end_cal.bind("<<CalendarSelected>>", lambda e: self._on_end_date_selected())

        info_bar = ctk.CTkFrame(date_frame, fg_color="#101827", corner_radius=12, border_width=1, border_color="#1e3a8a")
        info_bar.pack(fill="x", pady=(4, 8))
        self.date_info_label = ctk.CTkLabel(info_bar, text="", text_color="#60a5fa", font=ctk.CTkFont(size=12, weight="bold"))
        self.date_info_label.pack(anchor="w", padx=12, pady=10)
        self._update_date_range_info()

        # Quick buttons
        btn_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        btn_row.pack(fill="x")

        def set_single_date(include_today):
            d_str = self._selection_target_date(include_today=include_today)
            d = datetime.strptime(d_str, "%Y-%m-%d")
            self.date_from_var.set(d_str)
            self.date_to_var.set(d_str)
            self.start_cal.selection_set(d)
            self.end_cal.selection_set(d)
            self._update_date_range_info()

        def set_last_n_days(n):
            end = datetime.strptime(self._selection_target_date(include_today=False), "%Y-%m-%d")
            start = end - timedelta(days=max(n - 1, 0))
            self.date_from_var.set(start.strftime("%Y-%m-%d"))
            self.date_to_var.set(end.strftime("%Y-%m-%d"))
            self.start_cal.selection_set(start)
            self.end_cal.selection_set(end)
            self._update_date_range_info()

        make_action_button(btn_row, "Yesterday (US)", lambda: set_single_date(False), tone="neutral", width=120).pack(side="left", padx=(0, 8))
        make_action_button(btn_row, "Today (US)", lambda: set_single_date(True), tone="primary", width=110).pack(side="left", padx=(0, 8))
        make_action_button(btn_row, "Last 7 days", lambda: set_last_n_days(7), tone="neutral", width=110).pack(side="left", padx=(0, 8))
        make_action_button(btn_row, "Last 30 days", lambda: set_last_n_days(30), tone="neutral", width=118).pack(side="left", padx=(0, 8))
        make_action_button(btn_row, "Auto Fill QB Missing", lambda: self._apply_auto_qb_plan(False), tone="teal", width=160).pack(side="left", padx=(8, 8))
        make_action_button(btn_row, "Auto Fill QB + Today", lambda: self._apply_auto_qb_plan(True), tone="primary", width=160).pack(side="left")

        # ── Stores Section ──
        _store_card, store_frame = make_section_card(
            _parent,
            "QB Stores",
            "Select stores and point each one to the correct `.qbw` company file before running sync or preview.",
        )

        self._local_cfg = load_local_config()
        self._qb_item_catalog_cache = {}
        qbw_paths = self._local_cfg.get("qbw_paths", {})

        stores_grid = ctk.CTkFrame(store_frame, fg_color="transparent")
        stores_grid.pack(fill="x", pady=(0, 8))

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
            ctk.CTkEntry(stores_grid, textvariable=path_var, width=400, fg_color="#111827", border_color="#475569",
                          placeholder_text="Click Browse to select .qbw file").grid(row=row, column=2, padx=(10, 5), pady=3, sticky="w")
            make_action_button(stores_grid, "Browse", lambda n=name: self._browse_qbw(n), tone="neutral", width=76).grid(row=row, column=3, padx=2, pady=3)

        stores_grid.columnconfigure(2, weight=1)

        btn_row = ctk.CTkFrame(store_frame, fg_color="transparent")
        btn_row.pack(fill="x")
        make_action_button(btn_row, "Select All Stores", lambda: [v.set(True) for v in self.store_vars.values()], tone="neutral", width=130).pack(side="left", padx=(0, 8))
        make_action_button(btn_row, "Clear Selection", lambda: [v.set(False) for v in self.store_vars.values()], tone="amber", width=122).pack(side="left", padx=(0, 8))
        make_action_button(btn_row, "Auto Scan D:\\QB", self._auto_scan_qbw, tone="primary", width=138).pack(side="left")

        # ── Options ──
        _opt_card, opt_frame = make_section_card(
            _parent,
            "Sync Options",
            "Choose Drive or local source, limit which channels sync, and decide whether this run is preview-only.",
        )
        opt_inner = ctk.CTkFrame(opt_frame, fg_color="transparent")
        opt_inner.pack(fill="x")

        ctk.CTkLabel(opt_inner, text="Data Source:").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.source_var = ctk.StringVar(value="gdrive")
        ctk.CTkRadioButton(opt_inner, text="Google Drive", variable=self.source_var, value="gdrive").grid(row=0, column=1, padx=5)
        ctk.CTkRadioButton(opt_inner, text="Local Files", variable=self.source_var, value="local").grid(row=0, column=2, padx=5)

        # ── Source filter buttons (sync specific sources only) ─────────────
        source_filter_frame = ctk.CTkFrame(opt_inner, fg_color="transparent")
        source_filter_frame.grid(row=1, column=0, columnspan=3, pady=(10, 0), sticky="w")
        ctk.CTkLabel(source_filter_frame, text="Sync:").pack(side="left", padx=(0, 5))
        self.source_filter_var = ctk.StringVar(value="all")
        ctk.CTkRadioButton(source_filter_frame, text="All", variable=self.source_filter_var, value="all").pack(side="left", padx=3)
        ctk.CTkRadioButton(source_filter_frame, text="Toasttab", variable=self.source_filter_var, value="toast").pack(side="left", padx=3)
        ctk.CTkRadioButton(source_filter_frame, text="Uber", variable=self.source_filter_var, value="uber").pack(side="left", padx=3)
        ctk.CTkRadioButton(source_filter_frame, text="DoorDash", variable=self.source_filter_var, value="doordash").pack(side="left", padx=3)
        ctk.CTkRadioButton(source_filter_frame, text="Grubhub", variable=self.source_filter_var, value="grubhub").pack(side="left", padx=3)

        self.preview_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opt_inner, text="Preview only (don't create Sales Receipts)",
                         variable=self.preview_var).grid(row=2, column=0, columnspan=3, pady=(10, 0), sticky="w")
        self.strict_sync_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            opt_inner,
            text="Strict accounting mode (block sync on unmapped or unbalanced report data)",
            variable=self.strict_sync_var,
        ).grid(row=3, column=0, columnspan=3, pady=(8, 0), sticky="w")

        # ── Marketplace Uploads ──
        _marketplace_card, marketplace_frame = make_section_card(
            _parent,
            "Marketplace Uploads",
            "Attach the latest Uber, DoorDash, or Grubhub CSVs for stores that need additional sales receipts.",
        )
        marketplace_header = ctk.CTkFrame(marketplace_frame, fg_color="transparent")
        marketplace_header.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            marketplace_header,
            text="Marketplace Uploads",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        self.marketplace_summary = ctk.CTkLabel(
            marketplace_header,
            text="Toasttab still downloads from the website. Marketplace CSVs are user-selected files.",
            text_color="gray",
        )
        self.marketplace_summary.pack(side="left", padx=10)

        marketplace_btn_row = ctk.CTkFrame(marketplace_frame, fg_color="transparent")
        marketplace_btn_row.pack(fill="x", pady=(0, 4))
        make_action_button(marketplace_btn_row, "Refresh File Status", self._refresh_marketplace_source_statuses, tone="neutral", width=140).pack(side="left", padx=(0, 8))
        make_action_button(marketplace_btn_row, "Open Upload Folder", self._open_marketplace_upload_folder, tone="primary", width=140).pack(side="left")

        marketplace_grid = ctk.CTkFrame(marketplace_frame, fg_color="transparent")
        marketplace_grid.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(marketplace_grid, text="Store", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w", padx=(0, 6))
        ctk.CTkLabel(marketplace_grid, text="Source", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=1, sticky="w", padx=(0, 6))
        ctk.CTkLabel(marketplace_grid, text="Uploaded CSV", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=2, sticky="w", padx=(0, 6))
        ctk.CTkLabel(marketplace_grid, text="Status", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=5, sticky="w", padx=(6, 0))

        row_index = 1
        marketplace_paths = self._local_cfg.get("marketplace_paths", {})
        for store_name, store_cfg in self._stores.items():
            for source_cfg in store_cfg.get("additional_sale_receipts", []):
                source_name = source_cfg.get("name", "")
                path_var = ctk.StringVar(value=((marketplace_paths.get(store_name) or {}).get(source_name, "")))
                key = (store_name, source_name)
                self.marketplace_path_vars[key] = path_var
                self.marketplace_source_meta.append(
                    {
                        "store": store_name,
                        "source": source_name,
                        "file_name": source_cfg.get("file_name", ""),
                    }
                )
                ctk.CTkLabel(marketplace_grid, text=store_name, anchor="w", width=95).grid(row=row_index, column=0, sticky="w", padx=(0, 6), pady=3)
                ctk.CTkLabel(marketplace_grid, text=source_name, anchor="w", width=90).grid(row=row_index, column=1, sticky="w", padx=(0, 6), pady=3)
                ctk.CTkEntry(
                    marketplace_grid,
                    textvariable=path_var,
                    width=360,
                    fg_color="#111827",
                    border_color="#475569",
                    placeholder_text=f"Select uploaded {source_cfg.get('file_name', 'CSV file')}",
                ).grid(row=row_index, column=2, sticky="ew", padx=(0, 6), pady=3)
                make_action_button(marketplace_grid, "Browse", lambda s=store_name, src=source_name, fn=source_cfg.get("file_name", ""): self._browse_marketplace_file(s, src, fn), tone="neutral", width=76).grid(row=row_index, column=3, padx=2, pady=3)
                make_action_button(marketplace_grid, "Clear", lambda s=store_name, src=source_name: self._clear_marketplace_file(s, src), tone="amber", width=66).grid(row=row_index, column=4, padx=2, pady=3)
                status_label = ctk.CTkLabel(marketplace_grid, text="No file selected", text_color="gray", anchor="w")
                status_label.grid(row=row_index, column=5, sticky="w", padx=(6, 0), pady=3)
                self.marketplace_status_labels[key] = status_label
                row_index += 1

        marketplace_grid.columnconfigure(2, weight=1)

        # ── Action Button ──
        run_card, run_frame = make_section_card(
            _parent,
            "Execution",
            "Run a preview first when mapping or data quality changed. Use full sync only when the validation panel is clean.",
        )
        self.sync_btn = ctk.CTkButton(run_frame, text="Sync to QuickBooks",
                                       font=ctk.CTkFont(size=16, weight="bold"),
                                       height=48, command=self.start_sync,
                                       fg_color="#059669", hover_color="#047857", corner_radius=14)
        self.sync_btn.pack(fill="x")

        # ── Progress ──
        progress_wrap = make_subcard(run_frame)
        progress_wrap.pack(fill="x", pady=(12, 0))
        self.progress_bar = ctk.CTkProgressBar(progress_wrap, progress_color="#059669", fg_color="#1e293b")
        self.progress_bar.pack(fill="x", padx=12, pady=(12, 6))
        self.progress_bar.set(0)
        self.progress_label = ctk.CTkLabel(progress_wrap, text="Ready", text_color="#94a3b8", font=ctk.CTkFont(size=12, weight="bold"))
        self.progress_label.pack(anchor="w", padx=12, pady=(0, 12))

        # ── Validation Issues ──
        _issue_card, issue_frame = make_section_card(
            _parent,
            "Validation Issues",
            "Review blocking errors, export the issue list, and confirm the report is balanced before posting to QuickBooks.",
        )
        issue_header = ctk.CTkFrame(issue_frame, fg_color="transparent")
        issue_header.pack(fill="x", pady=(0, 4))
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
        self.validation_box.pack(fill="x", pady=(0, 0))
        self.validation_box.configure(state="disabled")

        # ── Mapping Maintenance ──
        _mapping_card, mapping_frame = make_section_card(
            _parent,
            "Mapping Maintenance",
            "Fix unmapped QB items and re-run preview without leaving the app.",
        )
        mapping_header = ctk.CTkFrame(mapping_frame, fg_color="transparent")
        mapping_header.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(mapping_header, text="Mapping Maintenance", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.mapping_summary = ctk.CTkLabel(mapping_header, text="No unmapped issues to fix yet", text_color="gray")
        self.mapping_summary.pack(side="left", padx=10)

        mapping_btn_row = ctk.CTkFrame(mapping_frame, fg_color="transparent")
        mapping_btn_row.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(mapping_btn_row, text="Refresh From Issues", width=135, command=self._refresh_mapping_candidates).pack(side="left", padx=2)
        ctk.CTkButton(mapping_btn_row, text="Open Map Folder", width=120, command=self._open_map_folder).pack(side="left", padx=2)

        self.mapping_candidate_combo = ctk.CTkComboBox(
            mapping_frame,
            values=["No mappable validation issues"],
            command=self._on_mapping_candidate_selected,
            state="readonly",
        )
        self.mapping_candidate_combo.pack(fill="x", padx=10, pady=(0, 6))
        self.mapping_candidate_combo.set("No mappable validation issues")

        mapping_edit_row = ctk.CTkFrame(mapping_frame, fg_color="transparent")
        mapping_edit_row.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(mapping_edit_row, text="QB Item:").pack(side="left", padx=(0, 8))
        self.mapping_qb_item_var = ctk.StringVar(value="")
        self.mapping_qb_item_entry = ctk.CTkEntry(
            mapping_edit_row,
            textvariable=self.mapping_qb_item_var,
            width=320,
            placeholder_text="Enter the QuickBooks item name to save into CSV map",
        )
        self.mapping_qb_item_entry.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(mapping_edit_row, text="Report / Column:").pack(side="left", padx=(4, 8))
        self.mapping_report_var = ctk.StringVar(value="")
        self.mapping_report_entry = ctk.CTkEntry(
            mapping_edit_row,
            textvariable=self.mapping_report_var,
            width=220,
            placeholder_text="CSV report value or marketplace column name",
        )
        self.mapping_report_entry.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(mapping_edit_row, text="Type:").pack(side="left", padx=(4, 8))
        self.mapping_type_var = ctk.StringVar(value="item")
        self.mapping_type_combo = ctk.CTkComboBox(
            mapping_edit_row,
            values=["item", "payment", "balance"],
            variable=self.mapping_type_var,
            width=110,
            state="disabled",
        )
        self.mapping_type_combo.pack(side="left", padx=(0, 8))
        self.save_mapping_btn = ctk.CTkButton(
            mapping_edit_row,
            text="Save Mapping",
            width=110,
            state="disabled",
            command=self._save_selected_mapping,
        )
        self.save_mapping_btn.pack(side="left", padx=2)
        self.save_and_preview_btn = ctk.CTkButton(
            mapping_edit_row,
            text="Save + Re-run Preview",
            width=165,
            state="disabled",
            command=self._save_mapping_and_preview,
        )
        self.save_and_preview_btn.pack(side="left", padx=2)

        mapping_qb_row = ctk.CTkFrame(mapping_frame, fg_color="transparent")
        mapping_qb_row.pack(fill="x", padx=10, pady=(0, 4))
        self.check_mapping_item_btn = ctk.CTkButton(
            mapping_qb_row,
            text="Check Existing Item",
            width=145,
            state="disabled",
            command=self._check_selected_mapping_item,
        )
        self.check_mapping_item_btn.pack(side="left", padx=2)
        self.create_mapping_item_btn = ctk.CTkButton(
            mapping_qb_row,
            text="Create Missing Item",
            width=145,
            state="disabled",
            command=self._create_selected_mapping_item,
        )
        self.create_mapping_item_btn.pack(side="left", padx=2)
        self.refresh_catalog_btn = ctk.CTkButton(
            mapping_qb_row,
            text="Refresh QB Catalog",
            width=140,
            state="disabled",
            command=self._refresh_selected_mapping_catalog,
        )
        self.refresh_catalog_btn.pack(side="left", padx=2)
        self.mapping_item_status = ctk.CTkLabel(
            mapping_qb_row,
            text="QB item validation not run yet",
            text_color="gray",
            anchor="w",
        )
        self.mapping_item_status.pack(side="left", padx=10, fill="x", expand=True)

        self.mapping_detail_box = ctk.CTkTextbox(mapping_frame, height=135, font=ctk.CTkFont(family="Consolas", size=11))
        self.mapping_detail_box.pack(fill="x", padx=10, pady=(0, 10))
        self.mapping_detail_box.configure(state="disabled")

        history_frame = ctk.CTkFrame(mapping_frame, fg_color="transparent")
        history_frame.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(history_frame, text="Recent Item Creation History", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        ctk.CTkButton(history_frame, text="Refresh History", width=120, command=self._refresh_item_creation_history).pack(side="left", padx=10)
        ctk.CTkButton(history_frame, text="Open Audit Folder", width=125, command=self._open_item_creation_audit_folder).pack(side="left", padx=2)

        self.item_creation_history_box = ctk.CTkTextbox(mapping_frame, height=110, font=ctk.CTkFont(family="Consolas", size=11))
        self.item_creation_history_box.pack(fill="x", padx=10, pady=(0, 10))
        self.item_creation_history_box.configure(state="disabled")

        # ── Last Sync Status ──
        _status_card, status_frame = make_section_card(
            _parent,
            "Last Sync Status",
            "Inspect the most recent run, export audit details, or mark stale sync attempts before a controlled re-run.",
        )
        status_header = ctk.CTkFrame(status_frame, fg_color="transparent")
        status_header.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(status_header, text="Last Sync Status", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.last_sync_summary = ctk.CTkLabel(status_header, text="Select one store and one date to inspect sync history", text_color="gray")
        self.last_sync_summary.pack(side="left", padx=10)

        status_btn_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        status_btn_row.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(status_btn_row, text="Refresh Status", width=120, command=self._refresh_last_sync_status).pack(side="left", padx=2)
        self.export_sync_audit_btn = ctk.CTkButton(status_btn_row, text="Export Sync Audit", width=130, state="disabled", command=self._export_last_sync_audit)
        self.export_sync_audit_btn.pack(side="left", padx=2)
        self.mark_stale_btn = ctk.CTkButton(status_btn_row, text="Mark Stale as Failed", width=145, state="disabled", command=self._mark_stale_run_failed)
        self.mark_stale_btn.pack(side="left", padx=2)
        self.force_rerun_btn = ctk.CTkButton(status_btn_row, text="Force Re-run", width=110, state="disabled", command=self._force_rerun_selected)
        self.force_rerun_btn.pack(side="left", padx=2)

        self.last_sync_box = ctk.CTkTextbox(status_frame, height=150, font=ctk.CTkFont(family="Consolas", size=11))
        self.last_sync_box.pack(fill="x", padx=10, pady=(0, 10))
        self.last_sync_box.configure(state="disabled")

        self.source_sync_box = ctk.CTkTextbox(status_frame, height=120, font=ctk.CTkFont(family="Consolas", size=11))
        self.source_sync_box.pack(fill="x", padx=10, pady=(0, 10))
        self.source_sync_box.configure(state="disabled")

        # ── Log ──
        log_frame = ctk.CTkFrame(
            _parent,
            fg_color=UI_CARD_FG,
            corner_radius=18,
            border_width=1,
            border_color=UI_CARD_BORDER,
        )
        log_frame.pack(fill="both", expand=True, padx=15, pady=(7, 15))
        self.log_box = make_log_box(log_frame, height=220)
        self._refresh_mapping_candidates()
        self._refresh_marketplace_source_statuses()
        self._refresh_last_sync_status()
        self._refresh_item_creation_history()

    def _selected_qb_stores(self):
        return [name for name, var in self.store_vars.items() if var.get()]

    def _selection_target_date(self, include_today=False):
        stores = self._selected_qb_stores() or list(self._stores.keys())
        return get_safe_target_date(stores, include_today=include_today)

    def queue_qb_sync_run(
        self,
        *,
        stores,
        start_date,
        end_date,
        source="gdrive",
        source_filter="toast",
        preview=False,
        strict_mode=True,
        completion_callback=None,
    ):
        if self._running:
            return False, "QB sync is already running."
        self._run_completion_callback = completion_callback
        selected = set(stores)
        for name, var in self.store_vars.items():
            var.set(name in selected)
        self.date_from_var.set(start_date)
        self.date_to_var.set(end_date)
        self.start_cal.selection_set(datetime.strptime(start_date, "%Y-%m-%d"))
        self.end_cal.selection_set(datetime.strptime(end_date, "%Y-%m-%d"))
        self.source_var.set(source)
        self.source_filter_var.set(source_filter)
        self.preview_var.set(bool(preview))
        self.strict_sync_var.set(bool(strict_mode))
        self._update_date_range_info()
        self.start_sync()
        return True, "QB sync command started."

    def _apply_auto_qb_plan(self, include_today=False):
        stores = self._selected_qb_stores()
        if not stores:
            messagebox.showwarning("Warning", "Please select at least one store")
            return

        plan = get_auto_qb_sync_plan(stores, include_today=include_today)
        if not plan["has_gap"]:
            messagebox.showinfo("QB Sync Up To Date", plan["message"])
            return

        start_dt = datetime.strptime(plan["start_date"], "%Y-%m-%d")
        end_dt = datetime.strptime(plan["end_date"], "%Y-%m-%d")
        self.date_from_var.set(plan["start_date"])
        self.date_to_var.set(plan["end_date"])
        self.start_cal.selection_set(start_dt)
        self.end_cal.selection_set(end_dt)
        self.source_filter_var.set("toast")
        self._update_date_range_info()
        self.log(plan["message"])

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

    def _get_marketplace_uploaded_paths(self, store_name):
        return get_marketplace_paths(self._local_cfg, store_name)

    def _save_marketplace_paths(self):
        marketplace_paths = {}
        for (store_name, source_name), var in self.marketplace_path_vars.items():
            value = var.get().strip()
            if value:
                marketplace_paths.setdefault(store_name, {})[source_name] = value
        self._local_cfg["marketplace_paths"] = marketplace_paths
        save_local_config(self._local_cfg)

    def _browse_marketplace_file(self, store_name, source_name, suggested_name):
        initial_dir = self._local_cfg.get("last_marketplace_dir") or str(Path.home() / "Downloads")
        filepath = filedialog.askopenfilename(
            title=f"Select {source_name} CSV for {store_name}",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=initial_dir,
            initialfile=suggested_name or "",
        )
        if not filepath:
            return
        filepath = filepath.replace("/", "\\")
        self.marketplace_path_vars[(store_name, source_name)].set(filepath)
        self._local_cfg["last_marketplace_dir"] = str(Path(filepath).parent)
        self._save_marketplace_paths()
        self._refresh_marketplace_source_statuses()

    def _clear_marketplace_file(self, store_name, source_name):
        self.marketplace_path_vars[(store_name, source_name)].set("")
        self._save_marketplace_paths()
        self._refresh_marketplace_source_statuses()

    def _open_marketplace_upload_folder(self):
        folder = runtime_path("marketplace-reports")
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def _refresh_marketplace_source_statuses(self):
        selected = 0
        for meta in self.marketplace_source_meta:
            key = (meta["store"], meta["source"])
            path_text = self.marketplace_path_vars[key].get().strip()
            label = self.marketplace_status_labels[key]
            if path_text:
                path = Path(path_text)
                if path.exists():
                    label.configure(text=f"Ready: {path.name}", text_color="#059669")
                    selected += 1
                else:
                    label.configure(text="Missing file", text_color="#dc2626")
            else:
                label.configure(text="No file selected", text_color="gray")
        total = len(self.marketplace_source_meta)
        if total:
            self.marketplace_summary.configure(
                text=f"Toasttab downloads from Toast website. Marketplace CSVs are user-selected files. {selected}/{total} source file(s) ready.",
                text_color="gray" if selected < total else "#059669",
            )
        else:
            self.marketplace_summary.configure(text="No additional marketplace receipts configured for current stores.", text_color="gray")

    def _get_primary_source_name(self, store_cfg):
        return (store_cfg.get("customer_name") or "Toasttab").strip() or "Toasttab"

    def _expected_source_names_for_store(self, store_name):
        store_cfg = self._stores.get(store_name, {})
        names = [self._get_primary_source_name(store_cfg)]
        names.extend(item.get("name", "") for item in store_cfg.get("additional_sale_receipts", []) if item.get("name"))
        return names

    def _is_marketplace_mapping_candidate(self, candidate):
        return bool(candidate and candidate.get("map_kind") == "marketplace")

    def _record_sync_context(
        self,
        ledger,
        sync_id,
        *,
        source_name,
        report_path,
        report_hash,
        map_path,
        selected_by_user,
        source_mode,
    ):
        ledger.record_event(
            sync_id,
            "run_context",
            {
                "source_name": source_name,
                "report_path": str(report_path),
                "report_hash": report_hash,
                "map_path": str(map_path),
                "selected_by_user": bool(selected_by_user),
                "source_mode": source_mode,
            },
        )

    def _get_run_context(self, ledger, sync_id):
        for event in ledger.get_run_events(sync_id):
            if event.get("event_type") == "run_context":
                return event.get("payload") or {}
        return {}

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
        dates = self._get_date_range()
        if not dates:
            return
        source_filter = self.source_filter_var.get()
        self._set_validation_records([])
        self._running = True
        self.sync_btn.configure(state="disabled", text="Syncing...")
        threading.Thread(target=self._sync_worker,
                          args=(stores, dates, self.source_var.get(), self.preview_var.get(), self.strict_sync_var.get(), source_filter),
                          daemon=True).start()

    # ── Date helper methods for QB Sync tab ────────────────────────────

    def _on_start_date_selected(self):
        """Handle calendar Start Date selection."""
        self.date_from_var.set(self.start_cal.get_date())
        try:
            s = datetime.strptime(self.date_from_var.get(), "%Y-%m-%d")
            e = datetime.strptime(self.date_to_var.get(), "%Y-%m-%d")
            if s > e:
                self.date_to_var.set(self.date_from_var.get())
                self.end_cal.selection_set(s)
        except ValueError:
            pass
        self._update_date_range_info()

    def _on_end_date_selected(self):
        """Handle calendar End Date selection."""
        self.date_to_var.set(self.end_cal.get_date())
        try:
            s = datetime.strptime(self.date_from_var.get(), "%Y-%m-%d")
            e = datetime.strptime(self.date_to_var.get(), "%Y-%m-%d")
            if e < s:
                self.date_from_var.set(self.date_to_var.get())
                self.start_cal.selection_set(e)
        except ValueError:
            pass
        self._update_date_range_info()

    def _sync_start_cal(self):
        """Sync start calendar when user types in entry."""
        try:
            d = datetime.strptime(self.date_from_var.get(), "%Y-%m-%d")
            self.start_cal.selection_set(d)
            self._update_date_range_info()
        except ValueError:
            pass

    def _sync_end_cal(self):
        """Sync end calendar when user types in entry."""
        try:
            d = datetime.strptime(self.date_to_var.get(), "%Y-%m-%d")
            self.end_cal.selection_set(d)
            self._update_date_range_info()
        except ValueError:
            pass

    def _update_date_range_info(self):
        """Update date info label in QBSyncTab."""
        from date_parser import parse_date_flexible

        start_str = self.date_from_var.get().strip()
        end_str = self.date_to_var.get().strip()

        if not start_str or not end_str:
            self.date_info_label.configure(text="Enter both dates to see range", text_color="#9ca3af")
            return

        s_result = parse_date_flexible(start_str, "qb_sync_ui")
        e_result = parse_date_flexible(end_str, "qb_sync_ui")

        if not s_result.success or not e_result.success:
            err = s_result.error if not s_result.success else e_result.error
            self.date_info_label.configure(text=f"⚠ {err}", text_color="#f87171")
            return

        s_iso = s_result.value.date_str
        e_iso = e_result.value.date_str

        s_dt = datetime.strptime(s_iso, "%Y-%m-%d")
        e_dt = datetime.strptime(e_iso, "%Y-%m-%d")
        days = (e_dt - s_dt).days + 1

        if days < 1:
            self.date_info_label.configure(text="⚠ End date before start date", text_color="#f87171")
        elif days == 1:
            self.date_info_label.configure(
                text=f"1 day selected: {s_dt.strftime('%b %d, %Y')}", text_color="#60a5fa"
            )
        else:
            self.date_info_label.configure(
                text=f"{days} days selected: {s_dt.strftime('%b %d')} - {e_dt.strftime('%b %d, %Y')}",
                text_color="#60a5fa",
            )

    def _get_date_range(self):
        """Get list of date strings from From/To inputs using unified date parser."""
        start_str = self.date_from_var.get().strip()
        end_str = self.date_to_var.get().strip()
        success, dates, error = get_date_range_from_inputs(start_str, end_str)
        if not success:
            if "enter" in error.lower():
                messagebox.showwarning("Warning", error)
            else:
                messagebox.showerror("Error", error)
            return None
        return dates

    def _sync_worker(self, stores, dates, source, preview, strict_mode, source_filter="all"):
        completion_payload = {
            "ok": False,
            "message": "QB sync did not complete.",
            "success": 0,
            "failed": 0,
            "total": 0,
        }
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
            from sync_ledger import SyncLedger, build_report_identity, STATUS_BLOCKED_DUPLICATE
            from marketplace_sync import get_marketplace_sources_for_store

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

            total_tasks = 0
            for _, orig_name, cfg in expanded_stores:
                total_tasks += len(dates) * (
                    1
                    + len(
                        get_marketplace_sources_for_store(
                            cfg,
                            map_dir=app_path("Map"),
                            uploaded_paths=self._get_marketplace_uploaded_paths(orig_name),
                            require_uploaded_path=True,
                        )
                    )
                )
            current_task = 0
            success_count = 0
            fail_count = 0
            validation_records = []
            ledger = SyncLedger()

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
                        from qb_automate import open_store, close_qb_completely, validate_company_file_path
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

                        file_ok, file_msg = validate_company_file_path(
                            qbw_path,
                            first_cfg.get("qbw_match"),
                            first_orig_name,
                        )
                        if not file_ok:
                            self.log(f"  {file_msg}")
                            current_task += sum(len(dates) for _ in group_stores)
                            fail_count += sum(len(dates) for _ in group_stores)
                            continue
                        self.log(f"  {file_msg}")

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
                            sync_id = None
                            filepath = None
                            toast_loc = store_cfg.get("toast_location", orig_name)
                            prefix = store_cfg.get("sale_no_prefix", "")
                            ref_number = f"{prefix}{date_str.replace('-', '')}"
                            override_reason = self.pending_force_reruns.get((orig_name, date_str))

                            # Source filter: skip Toast if not selected
                            if source_filter not in ("all", "toast"):
                                self.log(f"  [Filter: skipping Toast (source_filter={source_filter})]")
                            else:
                                filename = f"SalesSummary_{date_str}_{date_str}.xlsx"
                                local_dir = str(build_local_report_dir(REPORTS_DIR, toast_loc, "sales_summary"))
                                if gdrive:
                                    try:
                                        filepath = gdrive.download_report(toast_loc, filename, local_dir, report_type="sales_summary")
                                    except FileNotFoundError:
                                        self.log(f"  File not found on Drive: Toasttab/{toast_loc}/Sale Summary/{filename}")
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

                            primary_source_name = self._get_primary_source_name(store_cfg)
                            validation = validate_toast_report_file(filepath)
                            if not validation.ok:
                                self.log("  Report file validation failed before sync:")
                                for error in validation.errors:
                                    self.log(f"    [ERROR] invalid_report_file: {error}")
                                for warning in validation.warnings:
                                    self.log(f"    [WARN] report_validation_warning: {warning}")
                                validation_records.append(
                                    {
                                        "store": display_name,
                                        "date": date_str,
                                        "source": primary_source_name,
                                        "report_path": filepath,
                                        "summary": {"error": len(validation.errors), "warning": len(validation.warnings), "info": 0},
                                        "issues": [
                                            {
                                                "code": "invalid_report_file",
                                                "message": error,
                                                "severity": "error",
                                                "blocking": True,
                                            }
                                            for error in validation.errors
                                        ] + [
                                            {
                                                "code": "report_validation_warning",
                                                "message": warning,
                                                "severity": "warning",
                                                "blocking": False,
                                            }
                                            for warning in validation.warnings
                                        ],
                                    }
                                )
                                fail_count += 1
                                continue
                            if validation.warnings:
                                self.log("  Report validation warnings:")
                                for warning in validation.warnings:
                                    self.log(f"    [WARN] report_validation_warning: {warning}")

                            report_identity = build_report_identity(filepath)

                            reader = ToastExcelReader(filepath)
                            try:
                                issues = []
                                lines = extract_receipt_lines(reader, store_cfg, issues=issues)
                            finally:
                                try:
                                    reader.close()
                                except Exception:
                                    pass

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
                                        "source": primary_source_name,
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
                                blocked_sync_id = ledger.record_blocked_validation(
                                    store=display_name,
                                    date=date_str,
                                    source_name=primary_source_name,
                                    report_path=filepath,
                                    report_hash=report_identity.report_hash,
                                    report_size=report_identity.report_size,
                                    report_mtime=report_identity.report_mtime,
                                    ref_number=ref_number,
                                    preview=preview,
                                    strict_mode=strict_mode,
                                    qb_company_file=qbw_match if not preview else "",
                                    validation_error_count=sum(1 for issue in issues if issue.severity == "error"),
                                    validation_warning_count=sum(1 for issue in issues if issue.severity == "warning"),
                                    error_message="Strict mode blocked this sync because report validation issues were found",
                                )
                                self._record_sync_context(
                                    ledger,
                                    blocked_sync_id,
                                    source_name=primary_source_name,
                                    report_path=filepath,
                                    report_hash=report_identity.report_hash,
                                    map_path=app_path("Map", store_cfg.get("csv_map", "")),
                                    selected_by_user=False,
                                    source_mode="toast_report",
                                )
                                fail_count += 1
                                continue
                            for l in lines:
                                amt = float(l["amount"])
                                if amt != 0:
                                    self.log(f"    {l['item_name']:<30} {amt:>10.2f}")

                            begin_result = ledger.begin_run(
                                store=display_name,
                                date=date_str,
                                source_name=primary_source_name,
                                report_path=filepath,
                                report_hash=report_identity.report_hash,
                                report_size=report_identity.report_size,
                                report_mtime=report_identity.report_mtime,
                                ref_number=ref_number,
                                preview=preview,
                                strict_mode=strict_mode,
                                qb_company_file=qbw_match if not preview else "",
                                validation_error_count=sum(1 for issue in issues if issue.severity == "error"),
                                validation_warning_count=sum(1 for issue in issues if issue.severity == "warning") + len(validation.warnings),
                                override_reason=override_reason,
                            )
                            sync_id = begin_result.sync_id
                            self._record_sync_context(
                                ledger,
                                sync_id,
                                source_name=primary_source_name,
                                report_path=filepath,
                                report_hash=report_identity.report_hash,
                                map_path=app_path("Map", store_cfg.get("csv_map", "")),
                                selected_by_user=False,
                                source_mode="toast_report",
                            )
                            if begin_result.message and begin_result.message != "Sync run started.":
                                self.log(f"  Ledger: {begin_result.message}")
                            if not begin_result.allowed:
                                self.log(f"  Ledger blocked this sync: {begin_result.message}")
                                validation_records.append(
                                    {
                                        "store": display_name,
                                        "date": date_str,
                                        "source": primary_source_name,
                                        "report_path": filepath,
                                        "summary": {"error": 1, "warning": 0, "info": 0},
                                        "issues": [
                                            {
                                                "code": "blocked_duplicate",
                                                "message": begin_result.message,
                                                "severity": "error",
                                                "blocking": True,
                                            }
                                        ],
                                    }
                                )
                                fail_count += 1
                                continue

                            if preview:
                                self.log(f"  [PREVIEW MODE - not creating Sales Receipt]")
                                ledger.mark_success(sync_id, preview=True)
                                success_count += 1
                                current_task, extra_success, extra_fail, extra_records = self._process_marketplace_receipts_for_date(
                                    display_name=display_name,
                                    orig_name=orig_name,
                                    store_cfg=store_cfg,
                                    date_str=date_str,
                                    preview=preview,
                                    strict_mode=strict_mode,
                                    qb_opened=qb_opened,
                                    qbw_match=qbw_match,
                                    global_cfg=global_cfg,
                                    ledger=ledger,
                                    total_tasks=total_tasks,
                                    current_task=current_task,
                                    override_reason=override_reason,
                                    source_filter=source_filter,
                                )
                                success_count += extra_success
                                fail_count += extra_fail
                                validation_records.extend(extra_records)
                                self.pending_force_reruns.pop((orig_name, date_str), None)
                                continue

                            if not qb_opened:
                                self.log(f"  QB not open - skipping creation")
                                ledger.mark_failed(sync_id, "QB not open - skipping creation")
                                fail_count += 1
                                continue

                            qb = QBSyncClient(
                                app_name=global_cfg.get("app_name", "Toast Report Sync"),
                                qbxml_version=global_cfg.get("qbxml_version", "13.0"),
                            )
                            qb.connect()
                            try:
                                customer = store_cfg.get("customer_name", "Toast")
                                memo = f"Toast {toast_loc} {date_str}"

                                existing_receipts = qb.find_existing_sales_receipts(ref_number)
                                exists = any(item["txn_date"] == date_str for item in existing_receipts)
                                if exists:
                                    self.log(f"  Sales Receipt #{ref_number} already exists, skipping")
                                    ledger.mark_status(
                                        sync_id,
                                        STATUS_BLOCKED_DUPLICATE,
                                        error_message="Sales Receipt already exists in QuickBooks",
                                        payload={"ref_number": ref_number},
                                    )
                                    success_count += 1
                                    continue
                                if existing_receipts:
                                    existing_dates = ", ".join(sorted({item["txn_date"] for item in existing_receipts if item["txn_date"]}))
                                    self.log(f"  Note: found same RefNumber on other date(s): {existing_dates}")

                                # Auto-create customer if not exists
                                if not qb.ensure_customer(customer):
                                    self.log(f"  Error: Could not create customer '{customer}'")
                                    ledger.mark_failed(sync_id, f"Could not create customer '{customer}'")
                                    fail_count += 1
                                    continue

                                result = qb.create_sales_receipt(
                                    txn_date=date_str,
                                    ref_number=ref_number,
                                    customer_name=customer,
                                    memo=memo,
                                    lines=lines,
                                    class_name=store_cfg.get("class_name"),
                                )
                            finally:
                                qb.disconnect()

                            if result.get("success"):
                                self.log(f"  Sales Receipt created! TxnID: {result.get('txn_id')}")
                                ledger.mark_success(sync_id, txn_id=result.get("txn_id"))
                                success_count += 1
                                current_task, extra_success, extra_fail, extra_records = self._process_marketplace_receipts_for_date(
                                    display_name=display_name,
                                    orig_name=orig_name,
                                    store_cfg=store_cfg,
                                    date_str=date_str,
                                    preview=preview,
                                    strict_mode=strict_mode,
                                    qb_opened=qb_opened,
                                    qbw_match=qbw_match,
                                    global_cfg=global_cfg,
                                    ledger=ledger,
                                    total_tasks=total_tasks,
                                    current_task=current_task,
                                    override_reason=override_reason,
                                    source_filter=source_filter,
                                )
                                success_count += extra_success
                                fail_count += extra_fail
                                validation_records.extend(extra_records)
                                self.pending_force_reruns.pop((orig_name, date_str), None)
                            else:
                                self.log(f"  Error: {result.get('error')}")
                                ledger.mark_failed(sync_id, result.get("error") or "QuickBooks create_sales_receipt failed")
                                fail_count += 1

                        except Exception as e:
                            self.log(f"  Error: {e}")
                            import traceback
                            self.log(traceback.format_exc())
                            try:
                                if sync_id:
                                    ledger.mark_failed(sync_id, str(e))
                            except Exception:
                                pass
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
            self.after(0, self._refresh_last_sync_status)
            self.log(f"\nAll done! Success: {success_count}, Failed: {fail_count}")
            completion_payload = {
                "ok": fail_count == 0,
                "message": f"QB sync finished with {success_count} success and {fail_count} failed.",
                "success": success_count,
                "failed": fail_count,
                "total": success_count + fail_count,
            }

        except Exception as e:
            self.log(f"Fatal error: {e}")
            import traceback
            self.log(traceback.format_exc())
            completion_payload = {"ok": False, "message": str(e), "success": 0, "failed": len(stores) * len(dates), "total": len(stores) * len(dates)}
        finally:
            publish_agentai_snapshot_if_configured(on_log=self.log)
            completion_callback = self._run_completion_callback
            self._run_completion_callback = None
            if completion_callback:
                self.after(0, lambda payload=completion_payload, cb=completion_callback: cb(payload))
            self._running = False
            self.after(0, lambda: self.sync_btn.configure(state="normal", text="Sync to QuickBooks"))
            self.after(0, lambda: self.status_var.set("Ready"))
            self.after(0, lambda: self.progress_bar.set(0))
            self.after(0, lambda: self.progress_label.configure(text="Ready", text_color="gray"))

    def _process_marketplace_receipts_for_date(
        self,
        *,
        display_name,
        orig_name,
        store_cfg,
        date_str,
        preview,
        strict_mode,
        qb_opened,
        qbw_match,
        global_cfg,
        ledger,
        total_tasks,
        current_task,
        override_reason,
        source_filter="all",
    ):
        from marketplace_sync import extract_marketplace_receipt_lines, get_marketplace_sources_for_store
        from sync_ledger import STATUS_BLOCKED_DUPLICATE, build_report_identity
        from qb_sync import QBSyncClient

        uploaded_paths = self._get_marketplace_uploaded_paths(orig_name)
        for raw_source in store_cfg.get("additional_sale_receipts", []):
            if not uploaded_paths.get(raw_source.get("name", "")):
                self.log(f"  No uploaded {raw_source.get('name', 'marketplace')} file selected for {orig_name}; skipping")

        sources = get_marketplace_sources_for_store(
            store_cfg,
            map_dir=app_path("Map"),
            uploaded_paths=uploaded_paths,
            require_uploaded_path=True,
        )
        success_count = 0
        fail_count = 0
        validation_records = []

        # Map source names to filter values
        SOURCE_FILTER_MAP = {
            "uber": "uber",
            "doordash": "doordash",
            "grubhub": "grubhub",
        }

        for source in sources:
            # Source filter: skip marketplace if not selected
            source_key = source.name.lower()
            filter_val = SOURCE_FILTER_MAP.get(source_key, "")
            if source_filter not in ("all", "") and filter_val and source_filter != filter_val:
                self.log(f"  [Filter: skipping {source.name} (source_filter={source_filter})]")
                continue

            current_task += 1
            self.update_progress(current_task, total_tasks, f"{display_name} - {date_str} - {source.name}")
            self.log(f"--- {display_name} / {date_str} / {source.name} ---")

            sync_id = None
            try:
                lines, issues, row = extract_marketplace_receipt_lines(
                    report_path=source.report_path,
                    date_str=date_str,
                    map_path=app_path("Map", source.csv_map),
                    source_name=source.name,
                )
                if row is None:
                    self.log(f"  No {source.name} row for {date_str}; skipping")
                    continue
                if not lines:
                    self.log(f"  No {source.name} lines found for {date_str}; skipping")
                    continue

                ref_number = f"{source.ref_prefix}{date_str.replace('-', '')}"
                report_path = str(source.report_path)
                report_identity = build_report_identity(report_path)

                if issues:
                    validation_records.append(
                        {
                            "store": display_name,
                            "date": date_str,
                            "source": source.name,
                            "report_path": report_path,
                            "summary": {
                                "error": sum(1 for issue in issues if issue.get("severity") == "error"),
                                "warning": sum(1 for issue in issues if issue.get("severity") == "warning"),
                                "info": sum(1 for issue in issues if issue.get("severity") == "info"),
                            },
                            "issues": issues,
                        }
                    )
                    self.log(f"  {source.name} validation issues found:")
                    for issue in issues:
                        self.log(f"    [{issue.get('severity', 'warning').upper()}] {issue['code']}: {issue['message']}")

                if strict_mode and any(issue.get("blocking") for issue in issues):
                    self.log(f"  Strict mode blocked {source.name} receipt because validation issues were found")
                    blocked_sync_id = ledger.record_blocked_validation(
                        store=display_name,
                        date=date_str,
                        source_name=source.name,
                        report_path=report_path,
                        report_hash=report_identity.report_hash,
                        report_size=report_identity.report_size,
                        report_mtime=report_identity.report_mtime,
                        ref_number=ref_number,
                        preview=preview,
                        strict_mode=strict_mode,
                        qb_company_file=qbw_match if not preview else "",
                        validation_error_count=sum(1 for issue in issues if issue.get("severity") == "error"),
                        validation_warning_count=sum(1 for issue in issues if issue.get("severity") == "warning"),
                        error_message=f"Strict mode blocked {source.name} receipt because validation issues were found",
                    )
                    self._record_sync_context(
                        ledger,
                        blocked_sync_id,
                        source_name=source.name,
                        report_path=report_path,
                        report_hash=report_identity.report_hash,
                        map_path=app_path("Map", source.csv_map),
                        selected_by_user=source.selected_by_user,
                        source_mode="marketplace_upload",
                    )
                    fail_count += 1
                    continue

                for line in lines:
                    amt = float(line["amount"])
                    if amt != 0:
                        self.log(f"    {line['item_name']:<30} {amt:>10.2f}")

                begin_result = ledger.begin_run(
                    store=display_name,
                    date=date_str,
                    source_name=source.name,
                    report_path=report_path,
                    report_hash=report_identity.report_hash,
                    report_size=report_identity.report_size,
                    report_mtime=report_identity.report_mtime,
                    ref_number=ref_number,
                    preview=preview,
                    strict_mode=strict_mode,
                    qb_company_file=qbw_match if not preview else "",
                    validation_error_count=sum(1 for issue in issues if issue.get("severity") == "error"),
                    validation_warning_count=sum(1 for issue in issues if issue.get("severity") == "warning"),
                    override_reason=override_reason,
                )
                sync_id = begin_result.sync_id
                self._record_sync_context(
                    ledger,
                    sync_id,
                    source_name=source.name,
                    report_path=report_path,
                    report_hash=report_identity.report_hash,
                    map_path=app_path("Map", source.csv_map),
                    selected_by_user=source.selected_by_user,
                    source_mode="marketplace_upload",
                )
                if begin_result.message and begin_result.message != "Sync run started.":
                    self.log(f"  Ledger: {begin_result.message}")
                if not begin_result.allowed:
                    self.log(f"  Ledger blocked this {source.name} sync: {begin_result.message}")
                    validation_records.append(
                        {
                            "store": display_name,
                            "date": date_str,
                            "source": source.name,
                            "report_path": report_path,
                            "summary": {"error": 1, "warning": 0, "info": 0},
                            "issues": [
                                {
                                    "code": "blocked_duplicate",
                                    "message": begin_result.message,
                                    "severity": "error",
                                    "blocking": True,
                                }
                            ],
                        }
                    )
                    fail_count += 1
                    continue

                if preview:
                    self.log(f"  [PREVIEW MODE - not creating {source.name} Sales Receipt]")
                    ledger.mark_success(sync_id, preview=True)
                    success_count += 1
                    continue

                if not qb_opened:
                    self.log(f"  QB not open - skipping {source.name} creation")
                    ledger.mark_failed(sync_id, f"QB not open - skipping {source.name} creation")
                    fail_count += 1
                    continue

                qb = QBSyncClient(
                    app_name=global_cfg.get("app_name", "Toast Report Sync"),
                    qbxml_version=global_cfg.get("qbxml_version", "13.0"),
                )
                qb.connect()
                try:
                    existing_receipts = qb.find_existing_sales_receipts(ref_number)
                    exists = any(item["txn_date"] == date_str for item in existing_receipts)
                    if exists:
                        self.log(f"  {source.name} Sales Receipt #{ref_number} already exists, skipping")
                        ledger.mark_status(
                            sync_id,
                            STATUS_BLOCKED_DUPLICATE,
                            error_message="Sales Receipt already exists in QuickBooks",
                            payload={"ref_number": ref_number, "source": source.name},
                        )
                        success_count += 1
                        continue

                    # Auto-create customer if not exists
                    if not qb.ensure_customer(source.customer_name):
                        self.log(f"  Error: Could not create customer '{source.customer_name}'")
                        ledger.mark_failed(sync_id, f"Could not create customer '{source.customer_name}'")
                        fail_count += 1
                        continue

                    result = qb.create_sales_receipt(
                        txn_date=date_str,
                        ref_number=ref_number,
                        customer_name=source.customer_name,
                        memo=f"{source.name} {display_name} {date_str}",
                        lines=lines,
                        class_name=store_cfg.get("class_name"),
                    )
                finally:
                    qb.disconnect()

                if result.get("success"):
                    self.log(f"  {source.name} Sales Receipt created! TxnID: {result.get('txn_id')}")
                    ledger.mark_success(sync_id, txn_id=result.get("txn_id"))
                    success_count += 1
                else:
                    self.log(f"  Error: {result.get('error')}")
                    ledger.mark_failed(sync_id, result.get("error") or f"{source.name} create_sales_receipt failed")
                    fail_count += 1
            except Exception as exc:
                self.log(f"  {source.name} error: {exc}")
                import traceback
                self.log(traceback.format_exc())
                try:
                    if sync_id:
                        ledger.mark_failed(sync_id, str(exc))
                except Exception:
                    pass
                fail_count += 1

        return current_task, success_count, fail_count, validation_records

    def _status_target(self):
        stores = [name for name, var in self.store_vars.items() if var.get()]
        dates = [d.strip() for d in self.date_var.get().split(",") if d.strip()]
        if len(stores) != 1 or len(dates) != 1:
            return None, None
        return stores[0], dates[0]

    def _set_last_sync_status(self, run, message=None):
        self.last_sync_run = run
        self.last_sync_box.configure(state="normal")
        self.last_sync_box.delete("1.0", "end")

        if not run:
            self.last_sync_summary.configure(text=message or "No sync history for current selection", text_color="gray")
            self.last_sync_box.insert("end", message or "Select one store and one date, then click Refresh Status.")
            self.last_sync_box.configure(state="disabled")
            self._set_source_sync_statuses([], message or "No source-level sync history available.")
            self.export_sync_audit_btn.configure(state="disabled")
            self.mark_stale_btn.configure(state="disabled")
            self.force_rerun_btn.configure(state="disabled")
            return

        status = run.get("status", "unknown")
        color = "#059669"
        if status in {"failed", "blocked_duplicate", "blocked_validation"}:
            color = "#dc2626"
        elif status in {"running", "preview_success"}:
            color = "#d97706"

        hash_short = (run.get("report_hash") or "")[:12]
        context = run.get("context") or {}
        lines = [
            f"Store: {run.get('store')}",
            f"Date: {run.get('date')}",
            f"Source: {run.get('source_name') or '-'}",
            f"Status: {status}",
            f"Started: {run.get('started_at') or '-'}",
            f"Finished: {run.get('finished_at') or '-'}",
            f"Report Hash: {hash_short or '-'}",
            f"Ref Number: {run.get('ref_number') or '-'}",
            f"Preview: {'yes' if run.get('preview') else 'no'}",
            f"Strict Mode: {'yes' if run.get('strict_mode') else 'no'}",
            f"QB File: {run.get('qb_company_file') or '-'}",
            f"Report Path: {context.get('report_path') or run.get('report_path') or '-'}",
            f"Map File: {context.get('map_path') or '-'}",
            f"Error: {run.get('error_message') or '-'}",
            f"Override Reason: {run.get('override_reason') or '-'}",
        ]

        self.last_sync_summary.configure(text=f"{status} | {run.get('store')} | {run.get('date')}", text_color=color)
        self.last_sync_box.insert("end", "\n".join(lines))
        self.last_sync_box.configure(state="disabled")
        self.export_sync_audit_btn.configure(state="normal")
        self.mark_stale_btn.configure(state="normal" if status == "running" else "disabled")
        self.force_rerun_btn.configure(
            state="normal" if status in {"blocked_duplicate", "failed", "blocked_validation", "success"} else "disabled"
        )

    def _set_source_sync_statuses(self, runs, message=None):
        self.source_sync_box.configure(state="normal")
        self.source_sync_box.delete("1.0", "end")
        if not runs:
            self.source_sync_box.insert("end", message or "No source-level sync history yet.")
            self.source_sync_box.configure(state="disabled")
            return

        lines = []
        for run in runs:
            status = run.get("status", "unknown")
            context = run.get("context") or {}
            lines.extend(
                [
                    f"{run.get('source_name') or 'Unknown'} | {status}",
                    f"  Ref: {run.get('ref_number') or '-'}",
                    f"  Report: {Path(run.get('report_path') or '-').name if run.get('report_path') else '-'}",
                    f"  Path: {context.get('report_path') or run.get('report_path') or '-'}",
                    f"  Hash: {(context.get('report_hash') or run.get('report_hash') or '')[:16] or '-'}",
                    f"  Map File: {context.get('map_path') or '-'}",
                    f"  Source Mode: {context.get('source_mode') or '-'}",
                    f"  Selected By User: {'yes' if context.get('selected_by_user') else 'no'}",
                    f"  Finished: {run.get('finished_at') or run.get('started_at') or '-'}",
                    f"  Error: {run.get('error_message') or '-'}",
                    "",
                ]
            )
        self.source_sync_box.insert("end", "\n".join(lines).strip())
        self.source_sync_box.configure(state="disabled")

    def _refresh_source_sync_statuses(self, store, date):
        try:
            from sync_ledger import SyncLedger

            ledger = SyncLedger()
            latest_by_source = {run.get("source_name") or "Unknown": run for run in ledger.get_latest_runs_by_source(store, date)}
            for run in latest_by_source.values():
                if run.get("sync_id"):
                    run["context"] = self._get_run_context(ledger, run["sync_id"])
        except Exception as exc:
            self._set_source_sync_statuses([], f"Could not load source-level status: {exc}")
            return

        ordered_runs = []
        for source_name in self._expected_source_names_for_store(store):
            if source_name in latest_by_source:
                ordered_runs.append(latest_by_source.pop(source_name))
            else:
                ordered_runs.append(
                    {
                        "source_name": source_name,
                        "status": "not_run",
                        "ref_number": "",
                        "report_path": "",
                        "report_hash": "",
                        "finished_at": "",
                        "started_at": "",
                        "error_message": "No run recorded for this source/date yet.",
                        "context": {},
                    }
                )
        ordered_runs.extend(latest_by_source.values())
        self._set_source_sync_statuses(ordered_runs)

    def _refresh_last_sync_status(self):
        store, date = self._status_target()
        if not store or not date:
            self._set_last_sync_status(None, "Select exactly one store and one date to inspect sync history.")
            return
        try:
            from sync_ledger import SyncLedger

            ledger = SyncLedger()
            run = ledger.get_last_run(store, date)
            if run and run.get("sync_id"):
                context = self._get_run_context(ledger, run["sync_id"])
                if context:
                    run = dict(run)
                    run["context"] = context
            self._set_last_sync_status(run)
            self._refresh_source_sync_statuses(store, date)
        except Exception as exc:
            self._set_last_sync_status(None, f"Could not load sync status: {exc}")

    def _export_last_sync_audit(self):
        if not self.last_sync_run:
            return
        try:
            from sync_ledger import SyncLedger

            path = SyncLedger().export_run_audit(self.last_sync_run["sync_id"])
            messagebox.showinfo("Sync Audit Exported", f"Audit exported to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export Error", str(exc))

    def _mark_stale_run_failed(self):
        if not self.last_sync_run or self.last_sync_run.get("status") != "running":
            return
        reason = simpledialog.askstring(
            "Mark Running Sync as Failed",
            "Enter a short reason for marking this running sync as failed:",
            initialvalue="Operator marked stale run as failed",
        )
        if not reason:
            return
        try:
            from sync_ledger import SyncLedger

            SyncLedger().operator_mark_failed(self.last_sync_run["sync_id"], reason)
            self.log(f"Operator marked sync {self.last_sync_run['sync_id']} as failed: {reason}")
            self._refresh_last_sync_status()
        except Exception as exc:
            messagebox.showerror("Ledger Error", str(exc))

    def _force_rerun_selected(self):
        store, date = self._status_target()
        if not store or not date:
            messagebox.showwarning("Select One", "Select exactly one store and one date before forcing a re-run.")
            return
        reason = simpledialog.askstring(
            "Force Re-run",
            "Why are you forcing this re-run?\nThis reason will be stored in the sync ledger.",
        )
        if not reason:
            return
        self.pending_force_reruns[(store, date)] = reason
        self.log(f"Force re-run armed for {store} / {date}: {reason}")
        messagebox.showinfo("Force Re-run Armed", f"The next sync for {store} / {date} will carry this override reason:\n\n{reason}")
        self._refresh_last_sync_status()

    def _set_mapping_candidate(self, candidate, message=None):
        self.selected_mapping_candidate = candidate
        self.mapping_detail_box.configure(state="normal")
        self.mapping_detail_box.delete("1.0", "end")

        if not candidate:
            self.mapping_summary.configure(text=message or "No unmapped issues to fix yet", text_color="gray")
            self.mapping_qb_item_var.set("")
            self.mapping_report_var.set("")
            self.mapping_type_var.set("item")
            self.mapping_item_status.configure(text="QB item validation not run yet", text_color="gray")
            self.mapping_report_entry.configure(state="disabled")
            self.mapping_type_combo.configure(state="disabled")
            self.mapping_candidate_combo.configure(values=["No mappable validation issues"], state="disabled")
            self.mapping_candidate_combo.set("No mappable validation issues")
            self.save_mapping_btn.configure(state="disabled")
            self.save_and_preview_btn.configure(state="disabled")
            self.check_mapping_item_btn.configure(state="disabled")
            self.create_mapping_item_btn.configure(state="disabled")
            self.refresh_catalog_btn.configure(state="disabled")
            self.mapping_detail_box.insert("end", message or "Run Preview/Sync first, then use Validation Issues to drive mapping fixes here.")
            self.mapping_detail_box.configure(state="disabled")
            return

        label = candidate["title"]
        self.mapping_summary.configure(
            text=f"{candidate['issue_code']} | {candidate['store']} | {candidate['date']}",
            text_color="#d97706",
        )
        if self.mapping_candidate_combo.cget("state") == "disabled":
            self.mapping_candidate_combo.configure(state="readonly")
        self.mapping_candidate_combo.set(label)
        self.mapping_qb_item_var.set(candidate.get("current_qb") or "")
        self.mapping_report_var.set(candidate.get("report") or "")
        candidate_type = candidate.get("mapping_type") or "item"
        self.mapping_type_var.set(candidate_type if candidate_type in {"item", "payment", "balance"} else "item")
        self.mapping_item_status.configure(text="QB item validation not run yet", text_color="gray")
        if self._is_marketplace_mapping_candidate(candidate):
            self.mapping_report_entry.configure(state="normal")
            self.mapping_type_combo.configure(state="readonly")
        else:
            self.mapping_report_entry.configure(state="disabled")
            self.mapping_type_combo.configure(state="disabled")
        lines = [
            f"Store: {candidate.get('store')}",
            f"Date: {candidate.get('date')}",
            f"Issue: {candidate.get('issue_code')}",
            f"Report Value: {candidate.get('report')}",
            f"CSV Note: {candidate.get('note')}",
            f"Current QB Item: {candidate.get('current_qb') or '-'}",
            f"Map Kind: {candidate.get('map_kind') or 'toast'}",
            f"Map File: {candidate.get('map_path') or '-'}",
        ]
        if candidate.get("source_name"):
            lines.append(f"Source: {candidate.get('source_name')}")
        if candidate.get("mapping_type"):
            lines.append(f"Mapping Type: {candidate.get('mapping_type')}")
        invalid_type = ((candidate.get("meta") or {}).get("mapping_type") or "").strip().lower()
        if invalid_type and invalid_type not in {"item", "payment", "balance"}:
            lines.append(f"Invalid Type Seen: {invalid_type}")
        if candidate.get("guidance"):
            lines.extend(["", f"Guidance: {candidate.get('guidance')}"])
        lines.extend(["", "This will save or update the selected CSV map row:"])
        if self._is_marketplace_mapping_candidate(candidate):
            lines.extend(
                [
                    f"  QB     = {self.mapping_qb_item_var.get().strip() or '<enter QB item>'}",
                    f"  Column = {self.mapping_report_var.get().strip() or '<enter column>'}",
                    f"  Type   = {self.mapping_type_var.get().strip() or '<select type>'}",
                ]
            )
        else:
            lines.extend(
                [
                    f"  QB     = {self.mapping_qb_item_var.get().strip() or '<enter QB item>'}",
                    f"  Report = {candidate.get('report')}",
                    f"  Note   = {candidate.get('note')}",
                ]
            )
        self.mapping_detail_box.insert("end", "\n".join(lines))
        self.mapping_detail_box.configure(state="disabled")
        self.save_mapping_btn.configure(state="normal")
        self.save_and_preview_btn.configure(state="normal")
        self.check_mapping_item_btn.configure(state="normal")
        self.create_mapping_item_btn.configure(state="normal")
        self.refresh_catalog_btn.configure(state="normal")

    def _refresh_mapping_candidates(self):
        try:
            from mapping_maintenance import collect_mapping_candidates

            candidates = [
                item for item in collect_mapping_candidates(self.validation_records)
                if item["key"] not in self.mapping_saved_keys
            ]
        except Exception as exc:
            self.mapping_candidate_index = {}
            self.mapping_candidates = []
            self._set_mapping_candidate(None, f"Could not load mapping candidates: {exc}")
            return

        self.mapping_candidates = candidates
        self.mapping_candidate_index = {item["title"]: item for item in candidates}
        if not candidates:
            self._set_mapping_candidate(None)
            return

        labels = [item["title"] for item in candidates]
        self.mapping_candidate_combo.configure(values=labels, state="readonly")
        self._set_mapping_candidate(candidates[0])

    def _on_mapping_candidate_selected(self, label):
        candidate = self.mapping_candidate_index.get(label)
        if candidate:
            self._set_mapping_candidate(candidate)

    def _set_mapping_item_status(self, text, color="gray"):
        self.mapping_item_status.configure(text=text, text_color=color)

    def _get_mapping_base_store(self, candidate):
        store_name = self._resolve_store_selection_name(candidate.get("store", ""))
        if store_name:
            return store_name
        store_name = candidate.get("store", "")
        if store_name.startswith("Copper "):
            return "Copper"
        return store_name

    def _get_catalog_cache_key(self, store_name, qbw_path):
        return (store_name, qbw_path.lower())

    def _get_catalog_age_seconds(self, cache_entry):
        loaded_at = cache_entry.get("loaded_at")
        if not loaded_at:
            return None
        return max(0, time.time() - loaded_at)

    def _format_catalog_age(self, age_seconds):
        if age_seconds is None:
            return "unknown age"
        if age_seconds < 60:
            return f"{int(age_seconds)}s old"
        return f"{int(age_seconds // 60)}m {int(age_seconds % 60)}s old"

    def _get_qbw_path_for_store(self, store_name):
        qbw_path = (self.qbw_path_vars.get(store_name).get().strip() if self.qbw_path_vars.get(store_name) else "").strip()
        if not qbw_path:
            saved_paths = (self._local_cfg or {}).get("qbw_paths", {})
            qbw_path = (saved_paths.get(store_name) or "").strip()
        return qbw_path

    def _open_item_creation_audit_folder(self):
        try:
            ITEM_CREATION_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(ITEM_CREATION_AUDIT_DIR))
        except Exception as exc:
            messagebox.showerror("Open Audit Folder Failed", str(exc))

    def _refresh_item_creation_history(self):
        self.item_creation_history_box.configure(state="normal")
        self.item_creation_history_box.delete("1.0", "end")
        records = load_recent_item_creation_audits(ITEM_CREATION_AUDIT_DIR, limit=8)
        if not records:
            self.item_creation_history_box.insert("end", "No item creation audit records yet.")
            self.item_creation_history_box.configure(state="disabled")
            return

        lines = []
        for record in records:
            lines.extend(
                [
                    f"{record.get('generated_at') or record.get('_modified_at')} | {record.get('store') or '-'} | {record.get('status') or '-'}",
                    f"  Item     : {record.get('created_item') or '-'} ({record.get('created_item_type') or '-'})",
                    f"  Template : {record.get('template_name') or '-'} ({record.get('template_type') or '-'})",
                    f"  Operator : {record.get('operator') or '-'}",
                    f"  Audit    : {Path(record.get('_audit_path') or '').name or '-'}",
                    "",
                ]
            )
        self.item_creation_history_box.insert("end", "\n".join(lines).rstrip())
        self.item_creation_history_box.configure(state="disabled")

    def _refresh_selected_mapping_catalog(self):
        if not self.selected_mapping_candidate:
            return
        try:
            store_name = self._get_mapping_base_store(self.selected_mapping_candidate)
            catalog = self._get_qb_item_catalog(store_name, force_refresh=True)
            self._set_mapping_item_status(
                f"QB catalog refreshed for {store_name}: {len(catalog['items'])} items loaded",
                "#2563eb",
            )
        except Exception as exc:
            self._set_mapping_item_status("QB catalog refresh failed", "#dc2626")
            messagebox.showerror(
                "Refresh QB Catalog Failed",
                f"{exc}\n\nRecovery tips:\n- Confirm the correct .qbw file is selected\n- Close extra QB windows/popups\n- Retry after QB finishes loading",
            )

    def _get_qb_item_catalog(self, store_name, *, force_refresh=False):
        qbw_path = self._get_qbw_path_for_store(store_name)
        if not qbw_path:
            raise ValueError(f"Please choose a .qbw file for '{store_name}' before validating or creating QB items.")
        if not os.path.exists(qbw_path):
            raise ValueError(f"QB file not found for '{store_name}':\n{qbw_path}")

        cache_key = self._get_catalog_cache_key(store_name, qbw_path)
        cached = self._qb_item_catalog_cache.get(cache_key)
        age_seconds = None
        if cached and not force_refresh:
            age_seconds = self._get_catalog_age_seconds(cached)
            if age_seconds is not None and age_seconds <= QB_ITEM_CACHE_TTL_SECONDS:
                self._set_mapping_item_status(
                    f"Using cached QB catalog for {store_name} ({self._format_catalog_age(age_seconds)})",
                    "#2563eb",
                )
                return cached
            self._set_mapping_item_status(
                f"QB catalog cache for {store_name} is stale ({self._format_catalog_age(age_seconds)}); refreshing...",
                "#d97706",
            )
        if cached and force_refresh:
            self._set_mapping_item_status(f"Refreshing QB catalog for {store_name}...", "#2563eb")

        from qb_automate import close_qb_completely, open_store, validate_company_file_path
        from qb_sync import QBSyncClient

        store_cfg = self._stores.get(store_name, {})
        file_ok, file_msg = validate_company_file_path(qbw_path, store_cfg.get("qbw_match"), store_name)
        if not file_ok:
            raise ValueError(file_msg)

        self.status_var.set(f"Loading QuickBooks items for {store_name}...")
        self.log(file_msg)
        close_qb_completely()
        time.sleep(1)
        qb_opened = open_store(
            store_name,
            {store_name: qbw_path},
            qbw_match=store_cfg.get("qbw_match"),
            password_key=store_cfg.get("password"),
        )
        if not qb_opened:
            raise ValueError(f"Could not open QuickBooks for '{store_name}' to load items.")

        qb = QBSyncClient(
            app_name="Toast Report Sync",
            qbxml_version="13.0",
        )
        qb.connect()
        try:
            items = qb.query_items()
        finally:
            qb.disconnect()

        catalog = {"qbw_path": qbw_path, "items": items, "loaded_at": time.time()}
        self._qb_item_catalog_cache[cache_key] = catalog
        self.status_var.set(f"Loaded {len(items)} QB items for {store_name}")
        return catalog

    def _find_exact_qb_item(self, qb_item, items):
        target = qb_item.strip().lower()
        for item in items:
            if (item.get("name") or "").strip().lower() == target:
                return item
        return None

    def _infer_item_family(self, candidate, qb_item):
        source_name = (candidate.get("source_name") or "").strip().lower()
        report = (candidate.get("report") or "").strip().lower()
        note = (candidate.get("note") or "").strip().lower()
        item_name = (qb_item or "").strip().lower()
        combined = " | ".join([item_name, report, note, source_name])

        if "clearing" in combined:
            return "clearing"
        if "fee" in combined or "commission" in combined:
            return "fee"
        if "tax" in combined:
            return "tax"
        if "tip" in combined or "gratuity" in combined:
            return "tip"
        if "service charge" in combined or "servicecharge" in combined:
            return "service_charge"
        if "gift" in combined:
            return "gift"
        if "uber" in combined:
            return "uber"
        if "doordash" in combined or "door dash" in combined:
            return "doordash"
        if "grubhub" in combined or "grub hub" in combined:
            return "grubhub"
        return ""

    def _template_matches_family(self, template_item, family):
        if not family:
            return True
        template_name = (template_item.get("name") or "").strip().lower()
        if family == "service_charge":
            return "service charge" in template_name or "servicecharge" in template_name
        if family == "doordash":
            return "doordash" in template_name or "door dash" in template_name
        if family == "grubhub":
            return "grubhub" in template_name or "grub hub" in template_name
        return family in template_name

    def _template_matches_parent(self, template_item, qb_item):
        target_parent, _ = self._split_item_name(qb_item)
        if not target_parent:
            return True
        template_parent, _ = self._split_item_name(template_item.get("name") or "")
        return (template_parent or "").strip().lower() == target_parent.strip().lower()

    def _split_item_name(self, item_name):
        from qb_sync import split_qb_item_full_name

        return split_qb_item_full_name(item_name)

    def _choose_qb_item_template(self, candidate, items, suggestions, qb_item):
        family = self._infer_item_family(candidate, qb_item)
        preferred_names = []
        current_qb = (candidate.get("current_qb") or "").strip()
        if current_qb:
            preferred_names.append(current_qb)
        for suggestion in suggestions:
            preferred_names.append(suggestion.get("name") or "")

        seen = set()
        for name in preferred_names:
            normalized = name.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            exact = self._find_exact_qb_item(name, items)
            if (
                exact
                and exact.get("can_clone")
                and self._template_matches_family(exact, family)
                and self._template_matches_parent(exact, qb_item)
            ):
                return exact

        family_matches = [
            item
            for item in items
            if item.get("can_clone")
            and self._template_matches_family(item, family)
            and self._template_matches_parent(item, qb_item)
        ]
        if family_matches:
            return sorted(family_matches, key=lambda item: item.get("name", "").lower())[0]

        if family:
            return None

        for item in items:
            if item.get("can_clone") and self._template_matches_parent(item, qb_item):
                return item
        return None

    def _format_qb_item_suggestions(self, suggestions):
        if not suggestions:
            return "No close matches found."
        lines = []
        for item in suggestions:
            account_name = item.get("account_name") or item.get("income_account_name") or "-"
            lines.append(f"- {item.get('name')} ({item.get('type')}, account: {account_name})")
        return "\n".join(lines)

    def _write_item_creation_audit(self, payload):
        ITEM_CREATION_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        audit_files = write_item_creation_audit(payload, ITEM_CREATION_AUDIT_DIR)
        self.log(f"Item creation audit -> {audit_files['json_path']}")
        self._refresh_item_creation_history()
        return audit_files

    def _ensure_qb_item_available(self, candidate, qb_item, *, allow_create):
        from qb_sync import QBSyncClient, suggest_similar_items, validate_proposed_item_name

        store_name = self._get_mapping_base_store(candidate)
        normalized_qb_item = qb_item.strip()
        naming_issues = validate_proposed_item_name(normalized_qb_item)
        if naming_issues:
            raise ValueError("QB item name failed policy checks:\n- " + "\n- ".join(naming_issues))

        catalog = self._get_qb_item_catalog(store_name)
        items = catalog["items"]
        exact = self._find_exact_qb_item(normalized_qb_item, items)
        if exact:
            self._set_mapping_item_status(
                f"QB item found in {store_name}: {exact.get('name')} ({exact.get('type')})",
                "#16a34a",
            )
            return True

        suggestions = suggest_similar_items(normalized_qb_item, items)
        suggestion_text = self._format_qb_item_suggestions(suggestions)
        self._set_mapping_item_status(
            f"QB item not found in {store_name}. {len(suggestions)} close match(es) available.",
            "#d97706",
        )
        if not allow_create:
            messagebox.showwarning(
                "QB Item Not Found",
                (
                    f"The QB item '{normalized_qb_item}' was not found in {store_name}.\n\n"
                    f"Close matches:\n{suggestion_text}\n\n"
                    "Use 'Create Missing Item' only if you are sure this item should exist in QuickBooks."
                ),
            )
            return False

        template_item = self._choose_qb_item_template(candidate, items, suggestions, normalized_qb_item)
        if not template_item:
            raise ValueError(
                f"The QB item '{normalized_qb_item}' was not found in {store_name}, and no policy-safe template item was available to clone.\n\n"
                f"Close matches:\n{suggestion_text}"
            )

        answer = messagebox.askyesnocancel(
            "Create QB Item?",
            (
                f"The QB item '{normalized_qb_item}' was not found in {store_name}.\n\n"
                f"Close matches:\n{suggestion_text}\n\n"
                f"Create a new QB item now using template '{template_item.get('name')}' "
                f"({template_item.get('type')})?\n\n"
                "Yes = create item now\n"
                "No = go back and choose/edit item name\n"
                "Cancel = stop"
            ),
        )
        if answer is not True:
            if answer is False:
                self._set_mapping_item_status("Choose an existing QB item or edit the name before saving.", "#d97706")
            return False

        qbw_path = catalog["qbw_path"]
        self.status_var.set(f"Creating QB item '{normalized_qb_item}' for {store_name}...")
        audit_payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "operator": os.environ.get("USERNAME", "") or os.environ.get("USER", ""),
            "store": store_name,
            "qbw_path": qbw_path,
            "candidate_key": candidate.get("key", ""),
            "candidate_issue_code": candidate.get("issue_code", ""),
            "candidate_store": candidate.get("store", ""),
            "candidate_date": candidate.get("date", ""),
            "candidate_report": candidate.get("report", ""),
            "candidate_note": candidate.get("note", ""),
            "source_name": candidate.get("source_name", ""),
            "created_item": normalized_qb_item,
            "created_item_type": "",
            "template_name": template_item.get("name", ""),
            "template_type": template_item.get("type", ""),
            "template_account": template_item.get("account_name") or template_item.get("income_account_name") or "",
            "status": "requested",
            "message": "Operator confirmed item creation from Mapping Maintenance.",
        }
        qb = QBSyncClient(app_name="Toast Report Sync", qbxml_version="13.0")
        qb.connect()
        try:
            result = qb.create_item_from_template(normalized_qb_item, template_item)
        finally:
            qb.disconnect()
        if not result.get("success"):
            audit_payload["status"] = "failed"
            audit_payload["message"] = result.get("error") or "QuickBooks item creation failed."
            self._write_item_creation_audit(audit_payload)
            raise ValueError(result.get("error") or f"Could not create QB item '{normalized_qb_item}'.")

        cache_key = (store_name, qbw_path.lower())
        self._qb_item_catalog_cache.pop(cache_key, None)
        refreshed = self._get_qb_item_catalog(store_name, force_refresh=True)
        exact = self._find_exact_qb_item(normalized_qb_item, refreshed["items"])
        if not exact:
            audit_payload["status"] = "refresh_mismatch"
            audit_payload["message"] = "Item creation reported success but refreshed catalog did not return the item."
            self._write_item_creation_audit(audit_payload)
            raise ValueError(
                f"QB reported item creation success for '{normalized_qb_item}', but the item did not appear in the refreshed catalog."
            )
        self.log(
            f"Created QB item -> {store_name} | {normalized_qb_item} | template={template_item.get('name')} ({template_item.get('type')})"
        )
        audit_payload["status"] = "created"
        audit_payload["message"] = "QuickBooks item created and verified after catalog refresh."
        audit_payload["created_item"] = exact.get("name") or normalized_qb_item
        audit_payload["created_item_type"] = exact.get("type", "")
        audit_files = self._write_item_creation_audit(audit_payload)
        self._set_mapping_item_status(
            f"QB item created in {store_name}: {exact.get('name')} ({exact.get('type')}) | audit: {Path(audit_files['json_path']).name}",
            "#16a34a",
        )
        return True

    def _check_selected_mapping_item(self):
        if not self.selected_mapping_candidate:
            return
        qb_item = self.mapping_qb_item_var.get().strip()
        if not qb_item:
            messagebox.showwarning("QB Item Required", "Enter a QuickBooks item name before checking.")
            return
        try:
            self._ensure_qb_item_available(self.selected_mapping_candidate, qb_item, allow_create=False)
        except Exception as exc:
            self._set_mapping_item_status("QB item check failed", "#dc2626")
            messagebox.showerror(
                "QB Item Check Failed",
                f"{exc}\n\nRecovery tips:\n- Confirm the correct .qbw file is selected\n- Refresh the QB catalog if items were recently added\n- Retry after QuickBooks finishes loading",
            )

    def _create_selected_mapping_item(self):
        if not self.selected_mapping_candidate:
            return
        qb_item = self.mapping_qb_item_var.get().strip()
        if not qb_item:
            messagebox.showwarning("QB Item Required", "Enter a QuickBooks item name before creating it.")
            return
        try:
            created = self._ensure_qb_item_available(self.selected_mapping_candidate, qb_item, allow_create=True)
            if created:
                self._set_mapping_item_status("QB item is ready. You can save the mapping now.", "#16a34a")
        except Exception as exc:
            self._set_mapping_item_status("QB item create failed", "#dc2626")
            messagebox.showerror(
                "QB Item Create Failed",
                f"{exc}\n\nRecovery tips:\n- Confirm the correct .qbw file is selected\n- Close extra QB popups/windows\n- Retry after QuickBooks finishes loading",
            )

    def _apply_mapping_save(self, *, rerun_preview: bool):
        if not self.selected_mapping_candidate:
            return False
        qb_item = self.mapping_qb_item_var.get().strip()
        if not qb_item:
            messagebox.showwarning("QB Item Required", "Enter a QuickBooks item name before saving the mapping.")
            return False
        override_report = None
        override_type = None
        if self._is_marketplace_mapping_candidate(self.selected_mapping_candidate):
            override_report = self.mapping_report_var.get().strip()
            override_type = self.mapping_type_var.get().strip().lower()
            if not override_report:
                messagebox.showwarning("Column Required", "Enter the CSV column name for this marketplace mapping.")
                return False
            if override_type not in {"item", "payment", "balance"}:
                messagebox.showwarning("Type Required", "Select a valid marketplace mapping type: item, payment, or balance.")
                return False
        try:
            if not self._ensure_qb_item_available(self.selected_mapping_candidate, qb_item, allow_create=False):
                return False
        except Exception as exc:
            self._set_mapping_item_status("QB item validation failed", "#dc2626")
            messagebox.showerror(
                "QB Item Validation Failed",
                f"{exc}\n\nSave was blocked. Run 'Check Existing Item' or 'Create Missing Item' first.",
            )
            return False
        try:
            from mapping_maintenance import upsert_candidate_mapping

            result = upsert_candidate_mapping(
                self.selected_mapping_candidate,
                qb_item,
                override_report=override_report,
                override_type=override_type,
            )
            candidate = dict(self.selected_mapping_candidate)
            candidate["current_qb"] = qb_item
            if override_report is not None:
                candidate["report"] = override_report
            if override_type is not None:
                candidate["mapping_type"] = override_type
            self.mapping_saved_keys.add(candidate["key"])
            self.log(
                f"Mapping {result['action']} -> {candidate['store']} | {candidate['report']} | {candidate['note']} => {qb_item}"
            )
            self._refresh_mapping_candidates()
            if rerun_preview:
                if not self._prepare_preview_for_candidate(candidate):
                    return False
                self.log(f"Starting preview rerun for {candidate['store']} / {candidate['date']} after mapping save")
                self.start_sync()
            else:
                messagebox.showinfo(
                    "Mapping Saved",
                    f"Mapping {result['action']} in:\n{result['path']}\n\nRe-run Preview/Sync to confirm the issue is resolved.",
                )
            return True
        except Exception as exc:
            messagebox.showerror("Save Mapping Failed", str(exc))
            return False

    def _save_selected_mapping(self):
        self._apply_mapping_save(rerun_preview=False)

    def _save_mapping_and_preview(self):
        self._apply_mapping_save(rerun_preview=True)

    def _resolve_store_selection_name(self, candidate_store):
        if candidate_store in self.store_vars:
            return candidate_store
        for store_name in self.store_vars:
            if candidate_store.startswith(f"{store_name} "):
                return store_name
        return None

    def _prepare_preview_for_candidate(self, candidate):
        if self._running:
            messagebox.showinfo("Sync In Progress", "Wait for the current sync to finish before starting a preview rerun.")
            return False
        store_name = self._resolve_store_selection_name(candidate.get("store", ""))
        if not store_name:
            messagebox.showerror(
                "Preview Setup Failed",
                f"Could not match candidate store '{candidate.get('store')}' to a selectable store in QB Sync.",
            )
            return False
        for name, var in self.store_vars.items():
            var.set(name == store_name)
        self.date_var.set(candidate.get("date") or "")
        self.preview_var.set(True)
        self.status_var.set(f"Preview armed for {store_name} / {candidate.get('date')}")
        self._refresh_last_sync_status()
        return True

    def _open_map_folder(self):
        try:
            map_dir = app_path("Map")
            map_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(map_dir))
        except Exception as exc:
            messagebox.showerror("Open Folder Failed", str(exc))

    def _set_validation_records(self, records):
        self.mapping_saved_keys = set()
        self.validation_records = records
        counts = {"error": 0, "warning": 0, "info": 0}
        lines = []
        for record in records:
            summary = record.get("summary", {})
            for key, value in summary.items():
                counts[key] = counts.get(key, 0) + value
            source_label = record.get("source") or "Primary"
            lines.append(f"{record['store']} | {record['date']} | {source_label} | {record['report_path']}")
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
        self._refresh_mapping_candidates()

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
            writer = csv.DictWriter(f, fieldnames=["store", "date", "source", "report_path", "severity", "code", "message"])
            writer.writeheader()
            for record in self.validation_records:
                for issue in record.get("issues", []):
                    writer.writerow({
                        "store": record["store"],
                        "date": record["date"],
                        "source": record.get("source", "Primary"),
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
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=5, pady=5)
        make_hero_banner(
            main,
            "Removal Control Center",
            "Connect to the correct QuickBooks company, filter transactions carefully, and prefer dry-run export before any delete action.",
            "High-risk workflow",
            accent="#b45309",
        )

        # ── Top: Store Selection + Connection ──
        top_frame = ctk.CTkFrame(main, fg_color=UI_CARD_FG, corner_radius=18, border_width=1, border_color=UI_CARD_BORDER)
        top_frame.pack(fill="x", padx=10, pady=(0, 5))
        self._build_store_section(top_frame)

        # ── Left + Right panels ──
        panels = ctk.CTkFrame(main, fg_color="transparent")
        panels.pack(fill="both", expand=True, padx=5, pady=5)
        panels.grid_columnconfigure(0, weight=1, minsize=380)
        panels.grid_columnconfigure(1, weight=2, minsize=450)
        panels.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(panels, fg_color=UI_CARD_FG, corner_radius=18, border_width=1, border_color=UI_CARD_BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(5, 3), pady=5)
        self._build_filter_panel(left)

        right = ctk.CTkFrame(panels, fg_color=UI_CARD_FG, corner_radius=18, border_width=1, border_color=UI_CARD_BORDER)
        right.grid(row=0, column=1, sticky="nsew", padx=(3, 5), pady=5)
        self._build_results_panel(right)

        # ── Bottom: Log ──
        log_frame = ctk.CTkFrame(main, fg_color=UI_CARD_FG, corner_radius=18, border_width=1, border_color=UI_CARD_BORDER)
        log_frame.pack(fill="x", padx=10, pady=(5, 10))
        ctk.CTkLabel(log_frame, text="Log", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=10, pady=(5, 0))
        self.log_box = ctk.CTkTextbox(log_frame, height=100, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.pack(fill="x", padx=10, pady=(0, 10))
        self.log_box.configure(state="disabled")

    # ── Store Section ────────────────────────────────────────────────

    def _build_store_section(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(header, text="QB Store", font=ctk.CTkFont(size=16, weight="bold"), text_color="#f8fafc").pack(side="left")
        self.conn_status = ctk.CTkLabel(header, text="Not connected", text_color=UI_MUTED_TEXT, font=ctk.CTkFont(size=12, weight="bold"))
        self.conn_status.pack(side="right", padx=10)
        self.btn_connect = make_action_button(header, "Connect to QB", self._connect_qb, tone="primary", width=140, height=36)
        self.btn_connect.pack(side="right", padx=5)

        store_frame = ctk.CTkScrollableFrame(parent, height=130, fg_color=UI_SUBCARD_FG, corner_radius=14, border_width=1, border_color=UI_SUBCARD_BORDER)
        store_frame.pack(fill="x", padx=10, pady=(5, 0))
        style_scrollable_frame(store_frame)
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
            ctk.CTkLabel(store_frame, text=name, width=100, anchor="w", text_color=UI_HEADING_TEXT).grid(row=row, column=1, padx=(0, 5), pady=3, sticky="w")
            path_var = ctk.StringVar(value=qbw_paths.get(name, ""))
            self.rm_qbw_path_vars[name] = path_var
            ctk.CTkEntry(store_frame, textvariable=path_var, width=400, fg_color="#111827", border_color="#475569",
                          placeholder_text="Click Browse").grid(row=row, column=2, padx=(10, 5), pady=3, sticky="ew")
            make_action_button(store_frame, "Browse", lambda n=name: self._browse_qbw_rm(n), tone="neutral", width=76).grid(row=row, column=3, padx=2, pady=3)

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(5, 10))
        make_action_button(btn_row, "Auto Scan D:\\QB", self._auto_scan_qbw_rm, tone="primary", width=140).pack(side="left", padx=2)

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
        ctk.CTkLabel(parent, text="Filters", font=ctk.CTkFont(size=16, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=10, pady=(12, 6))

        # Account selection
        acct_frame = ctk.CTkFrame(parent, fg_color=UI_SUBCARD_FG, corner_radius=14, border_width=1, border_color=UI_SUBCARD_BORDER)
        acct_frame.pack(fill="x", padx=10, pady=5)
        acct_header = ctk.CTkFrame(acct_frame, fg_color="transparent")
        acct_header.pack(fill="x", padx=5, pady=(5, 0))
        ctk.CTkLabel(acct_header, text="Accounts:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        acct_btn_frame = ctk.CTkFrame(acct_header, fg_color="transparent")
        acct_btn_frame.pack(side="right")
        make_action_button(acct_btn_frame, "All", lambda: self._select_all_accounts(True), tone="neutral", width=44, height=26).pack(side="left", padx=2)
        make_action_button(acct_btn_frame, "None", lambda: self._select_all_accounts(False), tone="amber", width=52, height=26).pack(side="left", padx=2)

        self.acct_scroll = ctk.CTkScrollableFrame(acct_frame, height=100, fg_color="#111827")
        self.acct_scroll.pack(fill="x", padx=5, pady=5)
        style_scrollable_frame(self.acct_scroll)
        self.acct_vars = {}
        self.acct_placeholder = ctk.CTkLabel(self.acct_scroll, text="Connect to QB to load accounts", text_color="gray")
        self.acct_placeholder.pack(pady=10)

        # Transaction type
        txn_frame = ctk.CTkFrame(parent, fg_color=UI_SUBCARD_FG, corner_radius=14, border_width=1, border_color=UI_SUBCARD_BORDER)
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
        date_frame = ctk.CTkFrame(parent, fg_color=UI_SUBCARD_FG, corner_radius=14, border_width=1, border_color=UI_SUBCARD_BORDER)
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
            make_action_button(quick_frame, label, lambda f=d_from, t=d_to: self._set_dates(f, t), tone="neutral", width=90, height=28).pack(side="left", padx=2, pady=2)

        dates_row = ctk.CTkFrame(date_frame, fg_color="transparent")
        dates_row.pack(fill="x", padx=5, pady=5)
        ctk.CTkLabel(dates_row, text="From:").pack(side="left")
        self.date_from_var = ctk.StringVar(value=yesterday.strftime("%Y-%m-%d"))
        ctk.CTkEntry(dates_row, textvariable=self.date_from_var, width=110, fg_color="#111827", border_color="#475569").pack(side="left", padx=(5, 15))
        ctk.CTkLabel(dates_row, text="To:").pack(side="left")
        self.date_to_var = ctk.StringVar(value=yesterday.strftime("%Y-%m-%d"))
        ctk.CTkEntry(dates_row, textvariable=self.date_to_var, width=110, fg_color="#111827", border_color="#475569").pack(side="left", padx=(5, 0))

        # Action buttons
        btn_frame = ctk.CTkFrame(parent, fg_color=UI_SUBCARD_FG, corner_radius=14, border_width=1, border_color=UI_SUBCARD_BORDER)
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
        self.btn_search = ctk.CTkButton(btn_frame, text="Search Transactions", height=38, command=self._search, state="disabled", fg_color=UI_ACCENT_BLUE, hover_color="#1d4ed8", corner_radius=12)
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
            height=34,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            corner_radius=12,
            command=self._export_selected,
            state="disabled",
        )
        self.btn_export.pack(fill="x", padx=5, pady=3)
        self.btn_delete = ctk.CTkButton(btn_frame, text="Delete Selected", height=36,
                                          fg_color="#c0392b", hover_color="#e74c3c",
                                          corner_radius=12,
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
        header.pack(fill="x", padx=10, pady=(12, 0))
        ctk.CTkLabel(header, text="Transactions Found", font=ctk.CTkFont(size=16, weight="bold"), text_color="#f8fafc").pack(side="left")
        self.result_count = ctk.CTkLabel(header, text="", text_color=UI_MUTED_TEXT)
        self.result_count.pack(side="left", padx=10)

        sel_frame = ctk.CTkFrame(header, fg_color="transparent")
        sel_frame.pack(side="right")
        make_action_button(sel_frame, "Select All", lambda: self._select_all_txns(True), tone="neutral", width=80, height=26).pack(side="left", padx=2)
        make_action_button(sel_frame, "None", lambda: self._select_all_txns(False), tone="amber", width=56, height=26).pack(side="left", padx=2)

        self.txn_scroll = ctk.CTkScrollableFrame(parent, fg_color=UI_SUBCARD_FG, corner_radius=14, border_width=1, border_color=UI_SUBCARD_BORDER)
        self.txn_scroll.pack(fill="both", expand=True, padx=10, pady=10)
        style_scrollable_frame(self.txn_scroll)

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
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        _app_logger.info(msg)

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
            store_cfg = self._stores.get(store_name, {})
            self._log_safe("Closing existing QB...")
            close_qb_completely(callback=lambda msg: self._log_safe(msg))

            self.after(0, lambda: self.btn_connect.configure(text="Logging in..."))
            success = open_qb_with_file(
                qbw_path,
                password_key=password_key,
                callback=lambda msg: self._log_safe(msg),
                expected_match=store_cfg.get("qbw_match"),
                store_name=store_name,
            )
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
    def __init__(self, master, run_diagnostics=None, status_var=None, **kwargs):
        super().__init__(master, **kwargs)
        self.run_diagnostics = run_diagnostics
        self.status_var = status_var
        self.recovery_playbooks = get_recovery_playbooks()
        self._local_cfg = load_local_config()
        self._build_ui()

    def _build_ui(self):
        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)
        style_scrollable_frame(content)

        make_hero_banner(
            content,
            "Settings & Recovery Center",
            "Manage Google Drive, Toast session state, QuickBooks configuration, diagnostics, and recovery actions from one place.",
            "Admin controls",
            accent="#475569",
        )

        # ── Google Drive ──
        _gdrive_card, gdrive_frame = make_section_card(
            content,
            "Google Drive",
            "Connect the Drive account used to store Toast exports and create the expected folder structure when needed.",
        )
        self.gdrive_status = ctk.CTkLabel(gdrive_frame, text="Not connected", text_color=UI_MUTED_TEXT, font=ctk.CTkFont(size=12, weight="bold"))
        self.gdrive_status.pack(anchor="w", pady=2)
        gdrive_btns = ctk.CTkFrame(gdrive_frame, fg_color="transparent")
        gdrive_btns.pack(fill="x", pady=(5, 0))
        make_action_button(gdrive_btns, "Connect Google Drive", self._connect_gdrive, tone="primary", width=180).pack(side="left", padx=(0, 8))
        make_action_button(gdrive_btns, "Setup Folders", self._setup_folders, tone="neutral", width=130).pack(side="left", padx=(0, 8))
        make_action_button(gdrive_btns, "Clear Token", self._clear_token, tone="danger", width=110).pack(side="left", padx=(0, 8))
        make_action_button(gdrive_btns, "Open Toast Folder", self._open_gdrive_toast_folder, tone="neutral", width=150).pack(side="left")

        _drive_inventory_card, drive_inventory_frame = make_section_card(
            content,
            "Drive Inventory Center",
            "Scan Google Drive and build a clean coverage matrix showing which report types each store already has, which dates are covered, and where gaps still exist.",
        )
        self.drive_inventory_summary = ctk.CTkLabel(
            drive_inventory_frame,
            text="No Google Drive inventory snapshot yet",
            text_color=UI_MUTED_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.drive_inventory_summary.pack(anchor="w", pady=2)
        drive_inventory_btns = ctk.CTkFrame(drive_inventory_frame, fg_color="transparent")
        drive_inventory_btns.pack(fill="x", pady=(5, 8))
        make_action_button(
            drive_inventory_btns,
            "Refresh Drive Inventory",
            self._refresh_drive_inventory,
            tone="primary",
            width=170,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            drive_inventory_btns,
            text="Rows sort by missing coverage first, then by store and report type.",
            text_color=UI_MUTED_TEXT,
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(4, 0))

        ctk.CTkLabel(
            drive_inventory_frame,
            text="Coverage Matrix",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", pady=(2, 4))
        self.drive_inventory_box = ctk.CTkTextbox(
            drive_inventory_frame,
            height=230,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.drive_inventory_box.pack(fill="x", pady=(0, 8))
        self.drive_inventory_box.configure(state="disabled")

        ctk.CTkLabel(
            drive_inventory_frame,
            text="Missing Ranges",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))
        self.drive_missing_box = ctk.CTkTextbox(
            drive_inventory_frame,
            height=170,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self.drive_missing_box.pack(fill="x")
        self.drive_missing_box.configure(state="disabled")

        # ── Toast Session ──
        _toast_card, toast_frame = make_section_card(
            content,
            "Toast Session",
            "Clear the saved Toast browser session if login expires or the account password changes.",
        )
        self.toast_status = ctk.CTkLabel(toast_frame, text="No saved session", text_color=UI_MUTED_TEXT, font=ctk.CTkFont(size=12, weight="bold"))
        self.toast_status.pack(anchor="w", pady=2)
        make_action_button(toast_frame, "Clear Session", self._clear_session, tone="danger", width=126).pack(anchor="w", pady=(6, 0))

        # ── QB Configuration ──
        _qb_card, qb_frame = make_section_card(
            content,
            "QuickBooks Desktop",
            "Review config file health, current delete policy, and mapped store coverage before production work.",
        )
        env_file = runtime_path(".env.qb")
        delete_policy = load_delete_policy(load_local_config(), self._load_env_values(env_file))
        if env_file.exists():
            ctk.CTkLabel(qb_frame, text=f"Config: {env_file}", text_color="#059669").pack(anchor="w", pady=2)
        else:
            ctk.CTkLabel(qb_frame, text="Config: .env.qb not found", text_color="#dc2626").pack(anchor="w", pady=2)
        delete_policy_color = "#d97706" if delete_policy.is_locked else "#dc2626"
        ctk.CTkLabel(
            qb_frame,
            text=f"Delete policy: {delete_policy.mode_label} ({delete_policy.source})",
            text_color=delete_policy_color,
        ).pack(anchor="w", pady=2)
        ctk.CTkLabel(
            qb_frame,
            text="Set local-config.json -> delete_policy.allow_live_delete=true or ALLOW_LIVE_DELETE=1 in .env.qb only during approved maintenance.",
            text_color=UI_MUTED_TEXT,
            font=ctk.CTkFont(size=11),
            wraplength=700,
        ).pack(anchor="w", pady=(0, 6))
        try:
            _, stores = load_mapping()
            store_text = ", ".join(stores.keys())
            ctk.CTkLabel(qb_frame, text=f"Stores: {store_text}", text_color=UI_MUTED_TEXT,
                          font=ctk.CTkFont(size=11), wraplength=600).pack(anchor="w", pady=(2, 0))
        except Exception:
            pass

        # ── Diagnostics ──
        _diag_card, diag_frame = make_section_card(
            content,
            "Startup Diagnostics",
            "Run an environment check and review missing files, warnings, or runtime blockers before operations begin.",
        )
        self.diag_summary = ctk.CTkLabel(diag_frame, text="Environment check not run yet", text_color=UI_MUTED_TEXT, font=ctk.CTkFont(size=12, weight="bold"))
        self.diag_summary.pack(anchor="w", pady=2)
        diag_btn_row = ctk.CTkFrame(diag_frame, fg_color="transparent")
        diag_btn_row.pack(fill="x", pady=(5, 5))
        make_action_button(
            diag_btn_row,
            "Run Diagnostics",
            (lambda: self.run_diagnostics(True)) if self.run_diagnostics else None,
            tone="primary",
            width=140,
        ).pack(side="left", padx=2)
        self.diag_box = ctk.CTkTextbox(diag_frame, height=180, font=ctk.CTkFont(family="Consolas", size=11))
        self.diag_box.pack(fill="x", pady=(0, 0))
        self.diag_box.configure(state="disabled")

        # ── Recovery Center ──
        _recovery_card, recovery_frame = make_section_card(
            content,
            "Recovery Center",
            "Use guided playbooks and safe reset actions when operational issues happen and a developer is unavailable.",
        )
        ctk.CTkLabel(
            recovery_frame,
            text="Use these playbooks and actions when there is no developer available. Start with a Health Report before changing runtime files.",
            text_color=UI_MUTED_TEXT,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(0, 6))

        playbook_row = ctk.CTkFrame(recovery_frame, fg_color="transparent")
        playbook_row.pack(fill="x", pady=(0, 6))
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
        recovery_btn_row.pack(fill="x", pady=(0, 6))
        make_action_button(recovery_btn_row, "Export Health Report", self._export_health_report, tone="primary", width=150).pack(side="left", padx=(0, 8))
        make_action_button(recovery_btn_row, "Create .env.qb", partial(self._create_runtime_file, ".env.qb.example", ".env.qb"), tone="neutral", width=120).pack(side="left", padx=(0, 8))
        make_action_button(recovery_btn_row, "Create local-config", partial(self._create_runtime_file, "local-config.example.json", "local-config.json"), tone="neutral", width=140).pack(side="left", padx=(0, 8))
        make_action_button(recovery_btn_row, "Open Recovery Backups", lambda: os.startfile(str(runtime_path("recovery-backups"))), tone="neutral", width=176).pack(side="left")

        reset_btn_row = ctk.CTkFrame(recovery_frame, fg_color="transparent")
        reset_btn_row.pack(fill="x", pady=(0, 6))
        make_action_button(reset_btn_row, "Backup + Reset Toast Session", self._backup_clear_session, tone="amber", width=210).pack(side="left", padx=(0, 8))
        make_action_button(reset_btn_row, "Backup + Reset Google Token", self._backup_clear_token, tone="amber", width=210).pack(side="left", padx=(0, 8))
        make_action_button(reset_btn_row, "Open Runtime Folder", lambda: os.startfile(str(RUNTIME_DIR)), tone="neutral", width=150).pack(side="left")

        self.recovery_box = ctk.CTkTextbox(recovery_frame, height=220, font=ctk.CTkFont(family="Consolas", size=11))
        self.recovery_box.pack(fill="x", pady=(0, 0))
        self.recovery_box.configure(state="disabled")
        self._show_playbook(self.playbook_var.get())
        self._refresh_recovery_status()

        # ── AgentAI Sync ──
        _agentai_card, agentai_frame = make_section_card(
            content,
            "AgentAI Sync",
            "Publish this machine's integration snapshot to the central AgentAI brain so remote operations show up even outside the local network.",
        )
        self.agentai_status = ctk.CTkLabel(
            agentai_frame,
            text="AgentAI sync not configured",
            text_color=UI_MUTED_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.agentai_status.pack(anchor="w", pady=2)

        sync_cfg = get_agentai_sync_settings(self._local_cfg)
        self.agentai_enabled_var = ctk.BooleanVar(value=sync_cfg["enabled"])
        self.agentai_api_url_var = ctk.StringVar(value=sync_cfg["api_url"])
        self.agentai_token_var = ctk.StringVar(value=sync_cfg["token"])
        self.agentai_machine_id_var = ctk.StringVar(value=sync_cfg["machine_id"])
        self.agentai_machine_name_var = ctk.StringVar(value=sync_cfg["machine_name"])

        ctk.CTkCheckBox(
            agentai_frame,
            text="Enable remote AgentAI sync for this machine",
            variable=self.agentai_enabled_var,
        ).pack(anchor="w", pady=(4, 8))

        agentai_grid = ctk.CTkFrame(agentai_frame, fg_color="transparent")
        agentai_grid.pack(fill="x")
        agentai_grid.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(agentai_grid, text="AgentAI API URL").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        ctk.CTkEntry(agentai_grid, textvariable=self.agentai_api_url_var, placeholder_text="https://agentai.yourdomain.com").grid(row=0, column=1, sticky="ew", pady=4)

        ctk.CTkLabel(agentai_grid, text="Edge token").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        ctk.CTkEntry(agentai_grid, textvariable=self.agentai_token_var, show="*", placeholder_text="Same value as AGENTAI_EDGE_TOKEN").grid(row=1, column=1, sticky="ew", pady=4)

        ctk.CTkLabel(agentai_grid, text="Machine ID").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=4)
        ctk.CTkEntry(agentai_grid, textvariable=self.agentai_machine_id_var, placeholder_text="stockton-frontdesk-01").grid(row=2, column=1, sticky="ew", pady=4)

        ctk.CTkLabel(agentai_grid, text="Machine name").grid(row=3, column=0, sticky="w", padx=(0, 12), pady=4)
        ctk.CTkEntry(agentai_grid, textvariable=self.agentai_machine_name_var, placeholder_text="Stockton Frontdesk").grid(row=3, column=1, sticky="ew", pady=4)

        agentai_btn_row = ctk.CTkFrame(agentai_frame, fg_color="transparent")
        agentai_btn_row.pack(fill="x", pady=(8, 0))
        make_action_button(agentai_btn_row, "Save AgentAI Sync", self._save_agentai_sync_settings, tone="primary", width=150).pack(side="left", padx=(0, 8))
        make_action_button(agentai_btn_row, "Publish Snapshot Now", self._publish_agentai_snapshot_now, tone="neutral", width=165).pack(side="left")
        self._refresh_agentai_status()

        # ── Appearance ──
        _theme_card, theme_frame = make_section_card(
            content,
            "Appearance",
            "Switch theme modes for the current workstation.",
        )
        theme_row = ctk.CTkFrame(theme_frame, fg_color="transparent")
        theme_row.pack(fill="x")
        ctk.CTkLabel(theme_row, text="Theme:").pack(side="left", padx=(0, 10))
        self.theme_menu = ctk.CTkOptionMenu(theme_row, values=["System", "Dark", "Light"],
                                             command=lambda c: ctk.set_appearance_mode(c), width=120)
        self.theme_menu.set("Dark")
        self.theme_menu.pack(side="left")

        # ── Quick Links ──
        _folder_card, folder_frame = make_section_card(
            content,
            "Quick Links",
            "Jump directly to the folders operators use most often during support and reconciliation work.",
        )
        links_row = ctk.CTkFrame(folder_frame, fg_color="transparent")
        links_row.pack(fill="x")
        make_action_button(links_row, "Open Reports Folder", lambda: os.startfile(str(REPORTS_DIR)), tone="primary", width=150).pack(side="left", padx=(0, 8))
        make_action_button(links_row, "Open Map Folder", lambda: os.startfile(str(app_path("Map"))), tone="neutral", width=130).pack(side="left", padx=(0, 8))
        make_action_button(links_row, "Open Project Folder", lambda: os.startfile(str(RUNTIME_DIR)), tone="neutral", width=150).pack(side="left")

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

    def _open_gdrive_toast_folder(self):
        """Open the Toast root folder in Google Drive in the default browser."""
        def _worker():
            try:
                from gdrive_service import GDriveService
                gdrive = GDriveService()
                if not gdrive.authenticate():
                    self.after(0, lambda: messagebox.showerror("Error", "Google Drive auth failed. Connect first."))
                    return
                root_id = gdrive._get_primary_root_folder()
                if root_id:
                    import webbrowser
                    url = f"https://drive.google.com/drive/folders/{root_id}"
                    webbrowser.open(url)
                else:
                    self.after(0, lambda: messagebox.showinfo("Info", "Toast folder not found on Drive. Run 'Setup Folders' first."))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
        threading.Thread(target=_worker, daemon=True).start()

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

    def _render_drive_inventory_snapshot(self, snapshot):
        summary_rows = list(snapshot.get("summary_rows") or [])
        missing_rows = list(snapshot.get("missing_rows") or [])
        inventory_rows = list(snapshot.get("inventory_rows") or [])

        ready_count = sum(1 for row in summary_rows if row["health"] == "ready")
        missing_count = sum(1 for row in summary_rows if row["health"] == "missing")
        empty_count = sum(1 for row in summary_rows if row["health"] == "empty")
        summary_text = (
            f"Drive snapshot: {len(inventory_rows)} file rows, {len(summary_rows)} store/report lanes, "
            f"{missing_count} with gaps, {empty_count} empty, {ready_count} ready."
        )
        self.drive_inventory_summary.configure(text=summary_text, text_color="#cbd5e1")

        matrix_lines = [
            "STORE        REPORT                    LAST DATE    DAYS  MISS  NEXT GAP    STATUS   LAST FILE",
            "-" * 108,
        ]
        for row in summary_rows[:60]:
            matrix_lines.append(
                f"{row['store'][:12]:12} "
                f"{row['report_label'][:24]:24} "
                f"{(row['last_date'] or '-'):12} "
                f"{row['available_dates_count']:>4}  "
                f"{row['missing_count']:>4}  "
                f"{(row['next_missing_date'] or '-'):12} "
                f"{row['health'][:7]:7} "
                f"{(row['latest_file_name'] or '-')[:36]}"
            )
        if len(summary_rows) > 60:
            matrix_lines.append(f"... {len(summary_rows) - 60} more row(s)")
        self.drive_inventory_box.configure(state="normal")
        self.drive_inventory_box.delete("1.0", "end")
        self.drive_inventory_box.insert("end", "\n".join(matrix_lines))
        self.drive_inventory_box.configure(state="disabled")

        grouped = []
        by_pair = {}
        for row in missing_rows:
            by_pair.setdefault((row["store"], row["report_label"]), []).append(row)
        for (store, report_label), rows in sorted(by_pair.items()):
            rows = sorted(rows, key=lambda item: item["business_date"])
            start = rows[0]["business_date"]
            prev = start
            count = 1
            for item in rows[1:]:
                current = item["business_date"]
                prev_dt = datetime.strptime(prev, "%Y-%m-%d")
                cur_dt = datetime.strptime(current, "%Y-%m-%d")
                if cur_dt == prev_dt + timedelta(days=1):
                    prev = current
                    count += 1
                    continue
                grouped.append((store, report_label, start, prev, count))
                start = current
                prev = current
                count = 1
            grouped.append((store, report_label, start, prev, count))

        missing_lines = [
            "STORE        REPORT                    RANGE                     DAYS",
            "-" * 78,
        ]
        for store, report_label, start, end, count in grouped[:80]:
            range_label = start if start == end else f"{start} -> {end}"
            missing_lines.append(f"{store[:12]:12} {report_label[:24]:24} {range_label:25} {count:>4}")
        if len(grouped) > 80:
            missing_lines.append(f"... {len(grouped) - 80} more missing range(s)")
        self.drive_missing_box.configure(state="normal")
        self.drive_missing_box.delete("1.0", "end")
        self.drive_missing_box.insert("end", "\n".join(missing_lines))
        self.drive_missing_box.configure(state="disabled")

    def _refresh_drive_inventory(self):
        def _worker():
            try:
                from gdrive_service import GDriveService

                gdrive = GDriveService(config=self._local_cfg)
                if not gdrive.authenticate():
                    self.after(0, lambda: messagebox.showerror("Google Drive", "Google Drive auth failed"))
                    return

                inventory_rows = gdrive.scan_report_inventory(store_names=TOAST_LOCATIONS)
                snapshot = refresh_drive_report_inventory(
                    inventory_rows,
                    store_names=TOAST_LOCATIONS,
                )
                self.after(0, lambda snap=snapshot: self._render_drive_inventory_snapshot(snap))
                if self.status_var is not None:
                    self.after(0, lambda: self.status_var.set("Drive inventory refreshed"))
            except Exception as exc:
                if self.status_var is not None:
                    self.after(0, lambda: self.status_var.set("Drive inventory failed"))
                self.after(0, lambda err=str(exc): messagebox.showerror("Drive Inventory", err))

        if self.status_var is not None:
            self.status_var.set("Refreshing Drive inventory...")
        threading.Thread(target=_worker, daemon=True).start()

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

    def _build_agentai_sync_config(self) -> dict:
        cfg = dict(self._local_cfg)
        cfg["agentai_sync"] = {
            "enabled": bool(self.agentai_enabled_var.get()),
            "api_url": self.agentai_api_url_var.get().strip(),
            "token": self.agentai_token_var.get().strip(),
            "project_id": "integration-full",
            "source_type": "integration-full",
            "app_version": "v2.2",
            "machine_id": self.agentai_machine_id_var.get().strip(),
            "machine_name": self.agentai_machine_name_var.get().strip(),
        }
        return cfg

    def _refresh_agentai_status(self):
        ready, message = is_agentai_sync_ready(self._local_cfg)
        sync_cfg = get_agentai_sync_settings(self._local_cfg)
        if ready:
            text = f"Ready: {sync_cfg['machine_name']} -> {sync_cfg['api_url']}"
            color = "#059669"
        elif sync_cfg["enabled"]:
            text = message
            color = "#d97706"
        else:
            text = "AgentAI sync disabled for this machine."
            color = "gray"
        self.agentai_status.configure(text=text, text_color=color)

    def _save_agentai_sync_settings(self):
        self._local_cfg = self._build_agentai_sync_config()
        save_local_config(self._local_cfg)
        self._refresh_agentai_status()
        if self.status_var is not None:
            self.status_var.set("AgentAI sync settings saved")
        messagebox.showinfo("Saved", "AgentAI sync settings saved for this machine.")

    def _publish_agentai_snapshot_now(self):
        self._local_cfg = self._build_agentai_sync_config()
        save_local_config(self._local_cfg)
        self._refresh_agentai_status()

        def _worker():
            result = publish_agentai_snapshot_if_configured(config=self._local_cfg)
            if self.status_var is not None:
                self.after(0, lambda: self.status_var.set("Ready"))
            if result.get("ok"):
                self.after(0, lambda: messagebox.showinfo("AgentAI Sync", result.get("message", "Snapshot published.")))
            else:
                self.after(0, lambda: messagebox.showwarning("AgentAI Sync", result.get("message", "AgentAI sync could not run.")))

        if self.status_var is not None:
            self.status_var.set("Publishing AgentAI snapshot...")
        threading.Thread(target=_worker, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════
#  Main Application
# ══════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self, *, runtime_mode="gui", start_hidden=False, headless_downloads=None):
        super().__init__()
        self.runtime_mode = runtime_mode
        self.start_hidden = bool(start_hidden)
        self.silent_mode = bool(start_hidden)
        worker_settings = get_background_worker_settings(load_local_config())
        default_headless_downloads = bool(worker_settings["headless_downloads"])
        if runtime_mode == "headless_worker" and headless_downloads is None:
            default_headless_downloads = True
        self.headless_downloads = default_headless_downloads if headless_downloads is None else bool(headless_downloads)
        self.command_poll_seconds = int(worker_settings["command_poll_seconds"])
        self.snapshot_interval_seconds = int(worker_settings["snapshot_interval_seconds"])
        self._agentai_snapshot_after_id = None
        if self.start_hidden:
            self.withdraw()

        self.title("Toast POS Manager")

        # Clamp window size to screen dimensions for small screens
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            win_w = min(1150, screen_w - 50)
            win_h = min(900, screen_h - 80)
            self.geometry(f"{win_w}x{win_h}")
            self.minsize(min(1000, screen_w - 50), min(700, screen_h - 80))
        except Exception:
            self.geometry("1150x900")
            self.minsize(1000, 700)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.status_var = ctk.StringVar(value="Ready")
        self.diagnostics_report = None
        self._compact_header_mode = False
        self._compact_nav_mode = False
        self._resize_after_id = None
        self._agentai_poll_after_id = None
        self._active_agentai_command = None
        self._active_agentai_command_config = None
        self._agentai_command_heartbeat_stop = None
        self._build_ui()
        self._sync_runtime_state(started_at=utc_now_iso(), worker_status="idle", last_error="")
        self.run_diagnostics_async(False)
        self.after(5000, lambda: self._schedule_agentai_poll(5000))
        self.after(7000, lambda: self._schedule_agentai_snapshot_publish(5000))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _sync_runtime_state(self, **extra):
        state = {
            "mode": self.runtime_mode,
            "headless_window": self.start_hidden,
            "headless_downloads": self.headless_downloads,
            "command_poll_seconds": self.command_poll_seconds,
            "snapshot_interval_seconds": self.snapshot_interval_seconds,
            "process_id": os.getpid(),
        }
        state.update(extra)
        update_runtime_state(**state)

    def _on_close(self):
        busy = getattr(self, "qb_tab", None) and self.qb_tab._running
        busy = busy or (getattr(self, "download_tab", None) and self.download_tab._running)
        busy = busy or (getattr(self, "rm_tab", None) and self.rm_tab._running)
        if busy and not self.silent_mode:
            if not messagebox.askokcancel(
                "Operation in progress",
                "A sync or download is still running. Are you sure you want to quit?",
            ):
                return
        if self._agentai_poll_after_id:
            try:
                self.after_cancel(self._agentai_poll_after_id)
            except Exception:
                pass
        if self._agentai_snapshot_after_id:
            try:
                self.after_cancel(self._agentai_snapshot_after_id)
            except Exception:
                pass
        if self._agentai_command_heartbeat_stop:
            self._agentai_command_heartbeat_stop.set()
        self._sync_runtime_state(
            worker_status="stopped",
            active_command_id="",
            active_command_type="",
            last_command_finished_at=utc_now_iso(),
        )
        self.destroy()

    def _build_ui(self):
        # ── Header ──
        header = ctk.CTkFrame(self, height=82, corner_radius=0, fg_color="#0b1220", border_width=0)
        header.pack(fill="x")
        header.pack_propagate(False)
        header_inner = ctk.CTkFrame(header, fg_color="transparent")
        header_inner.pack(fill="both", expand=True, padx=20, pady=12)

        header_left = ctk.CTkFrame(header_inner, fg_color="transparent")
        header_left.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(
            header_left,
            text="Toast POS Manager",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w")
        self.header_subtitle_label = ctk.CTkLabel(
            header_left,
            text="Operational control center for Toast downloads, QuickBooks sync, and recovery workflows.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        )
        self.header_subtitle_label.pack(anchor="w", pady=(4, 0))

        header_right = ctk.CTkFrame(header_inner, fg_color="transparent")
        header_right.pack(side="right", anchor="e")
        top_badges = ctk.CTkFrame(header_right, fg_color="transparent")
        top_badges.pack(anchor="e", pady=(0, 8))
        self.env_status_badge = ctk.CTkFrame(
            top_badges,
            fg_color="#3f2f12",
            corner_radius=12,
            border_width=1,
            border_color="#d97706",
        )
        self.env_status_badge.pack(side="left", padx=(0, 8))
        self.env_status_label = ctk.CTkLabel(
            self.env_status_badge,
            text="Environment: checking...",
            text_color="#fbbf24",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self.env_status_label.pack(padx=12, pady=8)
        version_badge = ctk.CTkFrame(
            top_badges,
            fg_color="#111827",
            corner_radius=12,
            border_width=1,
            border_color="#334155",
        )
        version_badge.pack(side="left")
        ctk.CTkLabel(
            version_badge,
            text="v2.2",
            text_color="#cbd5e1",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(padx=10, pady=8)

        self.clock_frame = ctk.CTkFrame(header_right, fg_color="transparent")
        self.clock_frame.pack(anchor="e")
        self.clock_labels = {}
        for clock in get_world_clocks():
            chip = ctk.CTkFrame(
                self.clock_frame,
                fg_color="#111827",
                corner_radius=14,
                border_width=1,
                border_color="#223049",
            )
            chip.pack(side="left", padx=(8, 0))
            label = ctk.CTkLabel(
                chip,
                text="",
                text_color="#cbd5e1",
                font=ctk.CTkFont(size=11, weight="bold"),
            )
            label.pack(padx=12, pady=8)
            self.clock_labels[clock["key"]] = label
        self._refresh_clock_labels()

        # ── Main: Sidebar + Content ──
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=10, pady=(5, 0))

        self._nav_theme = {
            "download": {
                "title": "Download",
                "description": "Pull Toast reports and save them cleanly.",
                "icon": "DL",
                "active_bg": "#2563eb",
                "active_border": "#60a5fa",
            },
            "qb": {
                "title": "QB Sync",
                "description": "Review and post sales into QuickBooks.",
                "icon": "QB",
                "active_bg": "#0f766e",
                "active_border": "#34d399",
            },
            "remove": {
                "title": "Remove",
                "description": "Find and clean up posted transactions.",
                "icon": "RM",
                "active_bg": "#b45309",
                "active_border": "#f59e0b",
            },
            "settings": {
                "title": "Settings",
                "description": "Control Drive, Toast, and app health.",
                "icon": "ST",
                "active_bg": "#475569",
                "active_border": "#94a3b8",
            },
        }
        self._nav_inactive_bg = "#1f2937"
        self._nav_inactive_hover = "#273449"
        self._nav_inactive_border = "#334155"
        self._nav_sidebar_bg = "#161d29"

        # ── Sidebar ──
        sidebar = ctk.CTkFrame(main, width=228, corner_radius=16, fg_color=self._nav_sidebar_bg, border_width=1, border_color="#273244")
        sidebar.pack(side="left", fill="y", padx=(0, 10))
        sidebar.pack_propagate(False)
        self.sidebar = sidebar

        brand_card = ctk.CTkFrame(sidebar, fg_color="#1b2433", corner_radius=14, border_width=1, border_color="#263246")
        brand_card.pack(fill="x", padx=14, pady=(16, 10))
        ctk.CTkLabel(
            brand_card,
            text="Toast POS",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#f8fafc",
        ).pack(anchor="w", padx=14, pady=(14, 2))
        ctk.CTkLabel(
            brand_card,
            text="Manager",
            font=ctk.CTkFont(size=13),
            text_color="#7dd3fc",
        ).pack(anchor="w", padx=14, pady=(0, 2))
        self.brand_description_label = ctk.CTkLabel(
            brand_card,
            text="Download, sync, and monitor store operations in one place.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            justify="left",
            wraplength=170,
        )
        self.brand_description_label.pack(anchor="w", padx=14, pady=(0, 14))

        ctk.CTkLabel(
            sidebar,
            text="WORKSPACE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#64748b",
        ).pack(anchor="w", padx=18, pady=(6, 8))

        nav_stack = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_stack.pack(fill="x", padx=14, pady=(0, 0))

        self._nav_buttons = {}
        self._tab_frames = {}
        nav_order = ["download", "qb", "remove", "settings"]

        for key in nav_order:
            theme = self._nav_theme[key]
            card = ctk.CTkFrame(
                nav_stack,
                fg_color=self._nav_inactive_bg,
                corner_radius=12,
                border_width=1,
                border_color=self._nav_inactive_border,
            )
            card.pack(fill="x", pady=5)

            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=10)

            indicator = ctk.CTkFrame(row, width=5, height=48, fg_color="transparent", corner_radius=6)
            indicator.pack(side="left", padx=(0, 10))
            indicator.pack_propagate(False)

            icon_wrap = ctk.CTkFrame(row, width=34, height=34, corner_radius=10, fg_color="#0f172a")
            icon_wrap.pack(side="left", padx=(0, 10))
            icon_wrap.pack_propagate(False)
            icon_label = ctk.CTkLabel(
                icon_wrap,
                text=theme["icon"],
                font=ctk.CTkFont(size=16, weight="bold"),
                text_color="#e2e8f0",
            )
            icon_label.place(relx=0.5, rely=0.5, anchor="center")

            text_col = ctk.CTkFrame(row, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True)

            title_label = ctk.CTkLabel(
                text_col,
                text=theme["title"],
                anchor="w",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color="#f8fafc",
            )
            title_label.pack(anchor="w")
            desc_label = ctk.CTkLabel(
                text_col,
                text=theme["description"],
                anchor="w",
                justify="left",
                font=ctk.CTkFont(size=11),
                text_color="#94a3b8",
                wraplength=130,
            )
            desc_label.pack(anchor="w", pady=(2, 0))

            self._nav_buttons[key] = {
                "card": card,
                "indicator": indicator,
                "icon_wrap": icon_wrap,
                "icon_label": icon_label,
                "title_label": title_label,
                "desc_label": desc_label,
            }
            self._bind_nav_click(card, key)
            self._bind_nav_click(row, key)
            self._bind_nav_click(indicator, key)
            self._bind_nav_click(icon_wrap, key)
            self._bind_nav_click(icon_label, key)
            self._bind_nav_click(text_col, key)
            self._bind_nav_click(title_label, key)
            self._bind_nav_click(desc_label, key)

        # Version at bottom
        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.pack(side="bottom", fill="x", padx=14, pady=(8, 14))
        ctk.CTkLabel(
            footer,
            text="Toast POS Manager v2.2",
            font=ctk.CTkFont(size=11),
            text_color="#64748b",
        ).pack(anchor="w")
        ctk.CTkLabel(
            footer,
            text="Keep finance and store reporting in sync.",
            font=ctk.CTkFont(size=10),
            text_color="#475569",
        ).pack(anchor="w", pady=(2, 0))

        # ── Tab Content Area ──
        content = ctk.CTkFrame(main, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True)

        for key in ["download", "qb", "remove", "settings"]:
            frame = ctk.CTkFrame(content, fg_color="transparent")
            frame.pack(fill="both", expand=True)
            self._tab_frames[key] = frame

        self.download_tab = DownloadTab(self._tab_frames["download"], self.status_var)
        self.download_tab.pack(fill="both", expand=True)

        self.qb_tab = QBSyncTab(self._tab_frames["qb"], self.status_var)
        self.qb_tab.pack(fill="both", expand=True)

        self.rm_tab = RemoveTab(self._tab_frames["remove"], self.status_var)
        self.rm_tab.pack(fill="both", expand=True)

        self.settings_tab = SettingsTab(
            self._tab_frames["settings"],
            run_diagnostics=self.run_diagnostics_async,
            status_var=self.status_var,
        )
        self.settings_tab.pack(fill="both", expand=True)

        # Default to QB Sync tab
        self._active_tab = "qb"
        for key in ["download", "remove", "settings"]:
            self._tab_frames[key].pack_forget()
        self._apply_nav_styles()
        self.bind("<Configure>", self._queue_responsive_layout)
        self.after(50, self._apply_responsive_layout)

        # ── Status Bar ──
        status_bar = ctk.CTkFrame(self, height=32, corner_radius=0, fg_color="#0b1220")
        status_bar.pack(fill="x", side="bottom")
        status_bar.pack_propagate(False)
        ctk.CTkLabel(status_bar, textvariable=self.status_var,
                      font=ctk.CTkFont(size=11), text_color="#94a3b8").pack(side="left", padx=10)

    def _refresh_clock_labels(self):
        for clock in get_world_clocks():
            label = self.clock_labels.get(clock["key"])
            if label:
                if self._compact_header_mode:
                    label.configure(text=f"{clock['label']} {clock['time']}")
                else:
                    label.configure(text=f"{clock['label']} · {clock['date'][5:].replace('-', '/')} {clock['time']}")
        self.after(1000, self._refresh_clock_labels)

    def _queue_responsive_layout(self, _event=None):
        if self._resize_after_id:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(80, self._apply_responsive_layout)

    def _apply_responsive_layout(self):
        self._resize_after_id = None
        width = max(self.winfo_width(), self.winfo_reqwidth())
        compact_header = width < 1260
        compact_nav = width < 1120
        self._compact_header_mode = compact_header
        self._compact_nav_mode = compact_nav

        self.sidebar.configure(width=208 if compact_nav else 228)
        self.header_subtitle_label.configure(
            wraplength=420 if compact_header else 560,
            text=(
                "Toast downloads, QuickBooks sync, and recovery workflows."
                if compact_header
                else "Operational control center for Toast downloads, QuickBooks sync, and recovery workflows."
            ),
        )
        self.brand_description_label.configure(
            wraplength=142 if compact_nav else 170,
            text=(
                "Download, sync, and monitor operations."
                if compact_nav
                else "Download, sync, and monitor store operations in one place."
            ),
        )

        for key, widgets in self._nav_buttons.items():
            widgets["desc_label"].configure(
                text="" if compact_nav else self._nav_theme[key]["description"],
                wraplength=108 if compact_nav else 130,
            )
            widgets["icon_label"].configure(
                font=ctk.CTkFont(size=12 if compact_nav else 13, weight="bold"),
            )
            widgets["title_label"].configure(
                font=ctk.CTkFont(size=13 if compact_nav else 14, weight="bold"),
            )

        self._refresh_clock_labels()

    def _bind_nav_click(self, widget, key: str):
        widget.bind("<Button-1>", lambda _event, target=key: self._switch_tab(target))

    def _switch_tab(self, key: str):
        """Switch to the given tab, highlighting the active nav button."""
        if key == self._active_tab:
            return

        # Hide current, show new
        self._tab_frames[self._active_tab].pack_forget()
        self._tab_frames[key].pack(fill="both", expand=True)
        self._active_tab = key
        self._apply_nav_styles()

    def _current_agentai_config(self):
        if getattr(self, "settings_tab", None) is not None:
            return dict(self.settings_tab._local_cfg)
        return load_local_config()

    def _start_agentai_command_heartbeat(self, command, config, *, heartbeat_seconds=120):
        self._stop_agentai_command_heartbeat()
        stop_event = threading.Event()
        self._agentai_command_heartbeat_stop = stop_event
        self._active_agentai_command_config = config

        def _worker():
            while not stop_event.wait(max(heartbeat_seconds // 3, 15)):
                result = heartbeat_agentai_command(
                    command.get("id"),
                    heartbeat_seconds=heartbeat_seconds,
                    config=config,
                )
                if not result.get("ok"):
                    break

        threading.Thread(target=_worker, daemon=True).start()

    def _stop_agentai_command_heartbeat(self):
        if self._agentai_command_heartbeat_stop:
            self._agentai_command_heartbeat_stop.set()
        self._agentai_command_heartbeat_stop = None

    def _schedule_agentai_snapshot_publish(self, delay_ms=None):
        if self._agentai_snapshot_after_id:
            try:
                self.after_cancel(self._agentai_snapshot_after_id)
            except Exception:
                pass
        if delay_ms is None:
            delay_ms = max(30000, self.snapshot_interval_seconds * 1000)
        self._agentai_snapshot_after_id = self.after(delay_ms, self._publish_periodic_agentai_snapshot)

    def _publish_periodic_agentai_snapshot(self):
        self._agentai_snapshot_after_id = None
        config = self._current_agentai_config()
        ready, _ = is_agentai_sync_ready(config)
        if not ready:
            self._schedule_agentai_snapshot_publish()
            return

        def _worker():
            result = publish_agentai_snapshot_if_configured(
                config=config,
                on_log=lambda message: _app_logger.info(message),
            )
            if not result.get("ok") and not result.get("skipped"):
                _app_logger.warning(result.get("message", "AgentAI periodic snapshot failed."))
            self.after(0, self._schedule_agentai_snapshot_publish)

        threading.Thread(target=_worker, daemon=True).start()

    def _schedule_agentai_poll(self, delay_ms=None):
        if self._agentai_poll_after_id:
            try:
                self.after_cancel(self._agentai_poll_after_id)
            except Exception:
                pass
        if delay_ms is None:
            delay_ms = max(10000, self.command_poll_seconds * 1000)
        self._agentai_poll_after_id = self.after(delay_ms, self._poll_agentai_commands)

    def _poll_agentai_commands(self):
        self._agentai_poll_after_id = None
        if self._active_agentai_command:
            self._schedule_agentai_poll(15000)
            return
        if (getattr(self, "download_tab", None) and self.download_tab._running) or (getattr(self, "qb_tab", None) and self.qb_tab._running):
            self._schedule_agentai_poll(15000)
            return

        config = self._current_agentai_config()
        ready, _ = is_agentai_sync_ready(config)
        if not ready:
            self._schedule_agentai_poll()
            return

        threading.Thread(target=self._poll_agentai_commands_worker, args=(config,), daemon=True).start()

    def _poll_agentai_commands_worker(self, config):
        result = fetch_next_agentai_command(config=config)
        self.after(0, lambda res=result, cfg=config: self._handle_polled_agentai_command(res, cfg))

    def _handle_polled_agentai_command(self, poll_result, config):
        if not poll_result.get("ok"):
            self.status_var.set("AgentAI command poll failed")
            self._schedule_agentai_poll()
            return

        command = poll_result.get("command")
        if not command:
            self._schedule_agentai_poll()
            return
        self._execute_agentai_command(command, config)

    def _execute_agentai_command(self, command, config):
        self._active_agentai_command = command
        self._active_agentai_command_config = config
        payload = dict(command.get("payload") or {})
        command_type = command.get("command_type")
        self._sync_runtime_state(
            worker_status="running",
            active_command_id=command.get("id") or "",
            active_command_type=command_type or "",
            last_command_started_at=utc_now_iso(),
            last_error="",
        )
        self.status_var.set(f"AgentAI command: {command_type}")
        ack_result = acknowledge_agentai_command(command.get("id"), heartbeat_seconds=120, config=config)
        if not ack_result.get("ok"):
            self._finish_agentai_command(command, {"ok": False, "message": ack_result.get("message", "Could not acknowledge command.")})
            return
        self._start_agentai_command_heartbeat(command, config, heartbeat_seconds=120)

        if command_type == "download_missing_reports":
            stores = payload.get("stores") or [payload.get("store")]
            stores = [item for item in stores if item]
            report_types = payload.get("report_types") or list(DEFAULT_REPORT_TYPE_KEYS)
            start_date = payload.get("start_date")
            end_date = payload.get("end_date") or start_date
            if not stores or not start_date or not end_date:
                self._finish_agentai_command(command, {"ok": False, "message": "Missing store or date range for download command."})
                return
            self._switch_tab("download")
            ok, message = self.download_tab.queue_download_run(
                locations=stores,
                report_types=report_types,
                start_date=start_date,
                end_date=end_date,
                upload_to_gdrive=payload.get("upload_to_gdrive", True),
                completion_callback=lambda result, cmd=command: self._finish_agentai_command(cmd, result),
            )
            if not ok:
                self._finish_agentai_command(command, {"ok": False, "message": message})
            return

        if command_type == "catch_up_qb_sync":
            stores = payload.get("stores") or [payload.get("store")]
            stores = [item for item in stores if item]
            start_date = payload.get("start_date")
            end_date = payload.get("end_date") or start_date
            if not stores or not start_date or not end_date:
                self._finish_agentai_command(command, {"ok": False, "message": "Missing store or date range for QB sync command."})
                return
            self._switch_tab("qb")
            ok, message = self.qb_tab.queue_qb_sync_run(
                stores=stores,
                start_date=start_date,
                end_date=end_date,
                source=payload.get("source", "gdrive"),
                source_filter=payload.get("source_filter", "toast"),
                preview=payload.get("preview", False),
                strict_mode=payload.get("strict_mode", True),
                completion_callback=lambda result, cmd=command: self._finish_agentai_command(cmd, result),
            )
            if not ok:
                self._finish_agentai_command(command, {"ok": False, "message": message})
            return

        if command_type == "publish_snapshot_now":
            threading.Thread(
                target=self._publish_snapshot_command_worker,
                args=(command, config),
                daemon=True,
            ).start()
            return

        if command_type == "run_environment_diagnostics":
            threading.Thread(
                target=self._run_self_check_command_worker,
                args=(command, config),
                daemon=True,
            ).start()
            return

        self._finish_agentai_command(command, {"ok": False, "message": f"Unsupported AgentAI command type: {command_type}"})

    def _finish_agentai_command(self, command, result):
        self._stop_agentai_command_heartbeat()
        self._active_agentai_command = None
        status = "success" if result.get("ok") else "failed"
        self._sync_runtime_state(
            worker_status="idle",
            active_command_id="",
            active_command_type="",
            last_command_finished_at=utc_now_iso(),
            last_command_status=status,
            last_error="" if status == "success" else result.get("message", ""),
        )
        self.status_var.set("Ready")
        threading.Thread(
            target=self._report_agentai_command_result_worker,
            args=(command, result, status, self._active_agentai_command_config or self._current_agentai_config()),
            daemon=True,
        ).start()
        self._active_agentai_command_config = None
        self._schedule_agentai_poll(10000)
        self._schedule_agentai_snapshot_publish(3000)

    def _report_agentai_command_result_worker(self, command, result, status, config):
        report_agentai_command_result(
            command.get("id"),
            status=status,
            result=result,
            error_message="" if status == "success" else result.get("message", ""),
            config=config,
        )

    def _publish_snapshot_command_worker(self, command, config):
        result = publish_agentai_snapshot_if_configured(
            config=config,
            on_log=lambda message: _app_logger.info(message),
        )
        payload = {
            "ok": bool(result.get("ok")),
            "message": result.get("message", "Snapshot command finished."),
            "skipped": bool(result.get("skipped")),
        }
        self.after(0, lambda res=payload, cmd=command: self._finish_agentai_command(cmd, res))

    def _run_self_check_command_worker(self, command, config):
        report = run_environment_checks(load_local_config())
        publish_agentai_snapshot_if_configured(
            config=config,
            on_log=lambda message: _app_logger.info(message),
        )
        result = {
            "ok": report.error_count == 0,
            "message": (
                f"Diagnostics finished with {report.error_count} error(s) and "
                f"{report.warning_count} warning(s)."
            ),
            "error_count": report.error_count,
            "warning_count": report.warning_count,
            "lines": format_report_lines(report)[:12],
        }
        self.after(0, lambda res=result, cmd=command: self._finish_agentai_command(cmd, res))

    def _apply_nav_styles(self):
        for key, widgets in self._nav_buttons.items():
            theme = self._nav_theme[key]
            is_active = key == self._active_tab
            widgets["card"].configure(
                fg_color=theme["active_bg"] if is_active else self._nav_inactive_bg,
                border_color=theme["active_border"] if is_active else self._nav_inactive_border,
            )
            widgets["indicator"].configure(
                fg_color=theme["active_border"] if is_active else "transparent",
            )
            widgets["icon_wrap"].configure(
                fg_color="#eff6ff" if is_active else "#0f172a",
            )
            widgets["icon_label"].configure(
                text_color=theme["active_bg"] if is_active else "#e2e8f0",
            )
            widgets["title_label"].configure(
                text_color="#ffffff" if is_active else "#f8fafc",
            )
            widgets["desc_label"].configure(
                text_color="#dbeafe" if is_active else "#94a3b8",
            )

    def run_diagnostics_async(self, show_popup_on_error=False):
        self.env_status_badge.configure(fg_color="#2f2530", border_color="#a855f7")
        self.env_status_label.configure(
            text="Checking environment..." if self._compact_header_mode else "Environment: checking...",
            text_color="#e9d5ff",
        )
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
            text_color = "#fecaca"
            badge_fg = "#3b1212"
            badge_border = "#dc2626"
            self.status_var.set("Environment issues detected. Open Settings > Startup Diagnostics.")
        elif report.warning_count:
            text = f"Environment: {report.warning_count} warning(s)"
            text_color = "#fde68a"
            badge_fg = "#3f2f12"
            badge_border = "#d97706"
            self.status_var.set("Environment warnings detected. Open Settings > Startup Diagnostics.")
        else:
            text = "Environment: ready"
            text_color = "#bbf7d0"
            badge_fg = "#0f2f24"
            badge_border = "#059669"
            self.status_var.set("Ready")

        if self._compact_header_mode:
            text = text.replace("Environment: ", "")
        self.env_status_badge.configure(fg_color=badge_fg, border_color=badge_border)
        self.env_status_label.configure(text=text, text_color=text_color)
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
    parser.add_argument(
        "--headless-worker",
        action="store_true",
        help="Run the integration app in hidden background worker mode for AgentAI command polling.",
    )
    args = parser.parse_args(argv)

    # Enable DPI awareness on Windows for crisp rendering on HiDPI displays
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    QBSYNC_ISSUE_DIR.mkdir(parents=True, exist_ok=True)
    ITEM_CREATION_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    if args.doctor_cli:
        return run_cli_doctor()

    app = App(
        runtime_mode="headless_worker" if args.headless_worker else "gui",
        start_hidden=args.headless_worker,
        headless_downloads=True if args.headless_worker else None,
    )
    _app_logger.info("Toast POS Manager started in %s mode", "headless_worker" if args.headless_worker else "gui")
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
