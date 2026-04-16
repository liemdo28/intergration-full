"""
ui/tabs/download_tab.py — DownloadTab class for Toast POS Manager.
Extracted from app.py for modularity.
"""
import sys
import os
import csv
import json
import time
import threading
import glob as glob_mod
from pathlib import Path
from datetime import datetime, timedelta
from tkinter import filedialog

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from tkcalendar import Calendar

from app_shared import (
    MAPPING_FILE, LOCAL_CONFIG_FILE, REPORTS_DIR, AUDIT_LOG_DIR, TOAST_LOCATIONS,
    REQUIRED_REPORT_RULES, REQUIRED_REPORT_RULES,
    load_mapping, load_local_config, save_local_config,
    get_required_reports, load_required_report_rules,
    make_log_box, append_log, make_section_card, make_subcard, make_action_button,
    make_hero_banner, make_calendar, style_scrollable_frame,
    UI_CARD_FG, UI_CARD_BORDER, UI_MUTED_TEXT, UI_HEADING_TEXT,
    UI_ACCENT_BLUE, UI_ACCENT_TEAL, UI_ACCENT_AMBER,
    publish_agentai_snapshot_if_configured,
)
from app_paths import APP_DIR, RUNTIME_DIR, app_path, runtime_path
from toast_reports import DEFAULT_REPORT_TYPE_KEYS, REPORT_TYPES, build_local_report_dir, get_download_report_types
from report_inventory import refresh_report_inventory, refresh_drive_report_inventory
from date_parser import get_date_range_from_inputs
from integration_status import (
    get_auto_download_plan,
    get_safe_target_date,
)
from services.activity_log_service import log, EventCategory, EventSeverity

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
        self.after(100, self._update_readiness)
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
        make_action_button(gdrive_row, "Open Google Drive", _open_drive, tone="neutral", width=150).pack(side="right", padx=(0, 8))
        make_action_button(gdrive_row, "Check Coverage", self._check_drive_coverage, tone="neutral", width=130).pack(side="right", padx=(0, 8))

        # ── Drive gap summary (suggests missing date ranges before download) ──
        self.drive_gap_suggestion_var = ctk.StringVar(value="Click 'Check Coverage' to scan Google Drive for missing reports.")
        gap_label = ctk.CTkLabel(
            option_chip,
            textvariable=self.drive_gap_suggestion_var,
            text_color="#60a5fa",
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
            wraplength=480,
        )
        gap_label.pack(anchor="w", padx=12, pady=(0, 12))

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

    def _check_drive_coverage(self):
        """Scan Google Drive and report missing (store, type, date) combos
        against the currently selected stores, report types, and date range."""
        locations = [name for name, var in self.loc_vars.items() if var.get()]
        report_types = [key for key, var in self.report_type_vars.items() if var.get()]
        dates = self._get_selected_dates()
        if not locations or not report_types or not dates:
            self.drive_gap_suggestion_var.set("Select stores, report types, and dates first.")
            return

        def _worker():
            try:
                from gdrive_service import GDriveService
                gdrive = GDriveService(on_log=self.log)
                if not gdrive.authenticate():
                    self.after(0, lambda: self.drive_gap_suggestion_var.set(
                        "Google Drive auth failed — connect Drive in Settings."
                    ))
                    return

                self.log("Scanning Drive for coverage...")
                rows = gdrive.scan_report_inventory(
                    store_names=locations,
                    report_types=report_types,
                )
                have = set()
                for row in rows:
                    bd = row.get("business_date")
                    if bd:
                        have.add((row["store"], row["report_key"], bd))

                from collections import defaultdict
                missing = defaultdict(list)
                total_needed = 0
                total_missing = 0
                for loc in locations:
                    for rtype in report_types:
                        for date_str in dates:
                            total_needed += 1
                            if (loc, rtype, date_str) not in have:
                                missing[(loc, rtype)].append(date_str)
                                total_missing += 1

                if total_missing == 0:
                    msg = f"✓ All {total_needed} reports already on Drive. No downloads needed."
                else:
                    # Build summary: show up to 3 (store, type) groups
                    parts = []
                    for (loc, rtype), missing_dates in list(missing.items())[:3]:
                        label = REPORT_TYPES.get(rtype, type("T", (), {"label": rtype})).label
                        parts.append(f"{loc}/{label}: {len(missing_dates)} missing")
                    summary = "; ".join(parts)
                    if len(missing) > 3:
                        summary += f" (+{len(missing) - 3} more)"
                    msg = f"⚠ {total_missing}/{total_needed} missing. {summary}"
                    # Log full details
                    self.log(f"Drive coverage: {total_needed - total_missing}/{total_needed} present, {total_missing} missing.")
                    for (loc, rtype), missing_dates in missing.items():
                        label = REPORT_TYPES.get(rtype, type("T", (), {"label": rtype})).label
                        self.log(f"  Missing {loc}/{label}: {', '.join(missing_dates)}")

                self.after(0, lambda m=msg: self.drive_gap_suggestion_var.set(m))
            except Exception as e:
                self.after(0, lambda err=str(e): self.drive_gap_suggestion_var.set(f"Scan error: {err}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _get_selected_dates(self):
        """Return list of selected dates as YYYY-MM-DD business-date strings."""
        try:
            date_strs = self._get_date_range_list()  # if exists
            return [d if isinstance(d, str) else d.strftime("%Y-%m-%d") for d in date_strs]
        except Exception:
            pass
        # Fallback: parse from calendar range
        try:
            from datetime import datetime, timedelta
            start = datetime.strptime(self.start_date_var.get(), "%Y-%m-%d").date()
            end = datetime.strptime(self.end_date_var.get(), "%Y-%m-%d").date()
            out = []
            d = start
            while d <= end:
                out.append(d.strftime("%Y-%m-%d"))
                d += timedelta(days=1)
            return out
        except Exception:
            return []

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
        start_time = time.time()
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
            log(EventCategory.DOWNLOAD, "Download Reports completed",
                detail=f"{results['success']} report(s) for {', '.join(locations)}",
                store=", ".join(locations), success=True,
                duration=time.time() - start_time)

        except ToastLoginRequiredError as e:
            self.log(f"Error: {e}")
            completion_payload = {"ok": False, "message": str(e), "success": 0, "failed": len(locations) * len(dates), "total": len(locations) * len(dates)}
            log(EventCategory.DOWNLOAD, "Download Reports failed",
                detail=str(e), success=False)
            if not silent_mode:
                self.after(0, lambda msg=str(e): messagebox.showwarning("Toast Login Required", msg))
        except Exception as e:
            self.log(f"Error: {e}")
            import traceback
            self.log(traceback.format_exc())
            completion_payload = {"ok": False, "message": str(e), "success": 0, "failed": len(locations) * len(dates), "total": len(locations) * len(dates)}
            log(EventCategory.DOWNLOAD, "Download Reports failed",
                detail=str(e), success=False)
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

