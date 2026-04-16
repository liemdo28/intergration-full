"""
ui/tabs/qb_sync_tab.py — QBSyncTab class for Toast POS Manager.
Extracted from app.py for modularity.
"""
import sys
import os
import csv
import json
import time
import threading
import glob as glob_mod
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, date
from tkinter import filedialog, simpledialog
from functools import partial

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, ttk as tkinter_ttk
from tkcalendar import Calendar

from app_shared import (
    MAPPING_FILE, LOCAL_CONFIG_FILE, REPORTS_DIR, AUDIT_LOG_DIR, QBSYNC_ISSUE_DIR,
    ITEM_CREATION_AUDIT_DIR, QB_ITEM_CACHE_TTL_SECONDS, TOAST_LOCATIONS,
    REQUIRED_REPORT_RULES,
    load_mapping, load_local_config, save_local_config, get_marketplace_paths,
    get_required_reports, load_required_report_rules,
    make_log_box, append_log, make_section_card, make_subcard, make_action_button,
    make_hero_banner, make_calendar, style_scrollable_frame,
    UI_CARD_FG, UI_CARD_BORDER, UI_SUBCARD_FG, UI_MUTED_TEXT,
    UI_HEADING_TEXT, UI_ACCENT_BLUE, UI_ACCENT_TEAL, UI_ACCENT_AMBER,
)
from app_paths import APP_DIR, RUNTIME_DIR, app_path, runtime_path
from toast_reports import DEFAULT_REPORT_TYPE_KEYS, REPORT_TYPES, build_local_report_dir
from audit_utils import load_recent_item_creation_audits, write_item_creation_audit, export_transactions_snapshot
from report_inventory import refresh_drive_report_inventory
from date_parser import get_date_range_from_inputs
from integration_status import (
    get_auto_qb_sync_plan,
    get_safe_target_date,
)
from services.activity_log_service import log, EventCategory, EventSeverity

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
        self._stop_sync_event = threading.Event()
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
        self.require_coverage_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            opt_inner,
            text="Require report coverage (block sync if expected reports are missing from Drive)",
            variable=self.require_coverage_var,
        ).grid(row=4, column=0, columnspan=3, pady=(8, 0), sticky="w")
        rules_row = ctk.CTkFrame(opt_inner, fg_color="transparent")
        rules_row.grid(row=5, column=0, columnspan=3, pady=(4, 0), sticky="w")
        ctk.CTkLabel(rules_row, text="Coverage Rules:").pack(side="left", padx=(0, 8))
        self.coverage_rules_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            rules_row, textvariable=self.coverage_rules_var,
            width=300, font=ctk.CTkFont(size=11),
            placeholder_text="Leave blank = default  |  store:report1,report2  |  *prefix = override default",
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            rules_row,
            text="e.g.  Stockton:sales_summary,order_details  |  Prefix * to override defaults",
            text_color=UI_MUTED_TEXT, font=ctk.CTkFont(size=10),
            anchor="w",
        ).pack(side="left")

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
        sync_row = ctk.CTkFrame(run_frame, fg_color="transparent")
        sync_row.pack(fill="x")
        self.sync_btn = ctk.CTkButton(sync_row, text="Sync to QuickBooks",
                                       font=ctk.CTkFont(size=16, weight="bold"),
                                       height=48, command=self.start_sync,
                                       fg_color="#059669", hover_color="#047857", corner_radius=14)
        self.sync_btn.pack(side="left", fill="x", expand=True)
        self.stop_sync_btn = ctk.CTkButton(
            sync_row, text="Stop Sync", font=ctk.CTkFont(size=13, weight="bold"),
            width=120, height=48, command=self._stop_sync,
            fg_color="#dc2626", hover_color="#991b1b", corner_radius=14, state="disabled",
        )
        self.stop_sync_btn.pack(side="left", padx=(12, 0))

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

    def _check_coverage_guard(self, stores, dates):
        """Check Drive for missing reports before sync. Returns (ok, message)."""
        import sqlite3
        from app_paths import runtime_path
        db_path = runtime_path("report-inventory.db")
        if not db_path.exists():
            return True, "Drive inventory not yet scanned."
        conn = sqlite3.connect(db_path)
        missing_by_store = {}
        for store in stores:
            for date_str in dates:
                rows = conn.execute(
                    "SELECT report_key, COUNT(*) FROM drive_missing_report_records "
                    "WHERE store=? AND business_date=? AND download_supported=1",
                    (store, date_str)
                ).fetchall()
                for rk, cnt in rows:
                    if cnt > 0:
                        missing_by_store.setdefault(store, []).append((date_str, rk))
        conn.close()
        if not missing_by_store:
            return True, ""
        total_missing = sum(len(v) for v in missing_by_store.values())
        lines_msg = [str(total_missing) + " missing expected report(s) found on Drive:"]
        for store, items in sorted(missing_by_store.items()):
            lines_msg.append("  " + store + ": " + str(len(items)) + " missing")
        lines_msg.append("")
        lines_msg.append("Run Refresh Drive Inventory in Settings to update the snapshot.")
        lines_msg.append("Sync will continue without blocking.")
        msg = "\n".join(lines_msg)
        return False, msg

    def _check_coverage_guard(self, stores, dates, rules_text=None):
        """Check Drive for missing reports before sync. Returns (ok, message).

        rules_text: optional per-store overrides in the form  store:report1,report2
            Entries separated by newlines or semicolons.
            Prefix a report key with * to override the store's entire default set.
            e.g. "Stockton:sales_summary,order_details;WA3:*sales_summary"
        """
        import sqlite3
        from app_paths import runtime_path
        db_path = runtime_path("report-inventory.db")
        if not db_path.exists():
            return True, "Drive inventory not yet scanned."

        # Parse rules_text into custom_rules: store -> frozenset(report_keys)
        custom_rules: dict[str, frozenset[str]] = {}
        if rules_text:
            for entry in rules_text.replace(";", "\n").split("\n"):
                entry = entry.strip()
                if ":" not in entry:
                    continue
                store_name, reports_part = entry.split(":", 1)
                store_name = store_name.strip()
                report_keys = [k.strip() for k in reports_part.split(",") if k.strip()]
                is_override = any(k.startswith("*") for k in report_keys)
                if is_override:
                    custom_rules[store_name] = frozenset(k.lstrip("*") for k in report_keys)
                else:
                    default = get_required_reports(store_name)
                    custom_rules[store_name] = default | frozenset(report_keys)

        conn = sqlite3.connect(db_path)
        missing_by_store: dict[str, list[tuple[str, str]]] = {}
        for store in stores:
            req_keys = custom_rules.get(store, get_required_reports(store))
            if not req_keys:
                continue
            for date_str in dates:
                for rk in req_keys:
                    cnt = conn.execute(
                        "SELECT COUNT(*) FROM drive_missing_report_records "
                        "WHERE store=? AND business_date=? AND report_key=? AND download_supported=1",
                        (store, date_str, rk)
                    ).fetchone()[0]
                    if cnt > 0:
                        missing_by_store.setdefault(store, []).append((date_str, rk))
        conn.close()
        if not missing_by_store:
            return True, ""
        total_missing = sum(len(v) for v in missing_by_store.values())
        lines_msg = [str(total_missing) + " missing required report(s) found on Drive:"]
        for store, items in sorted(missing_by_store.items()):
            by_report: dict[str, int] = {}
            for date_str, rk in items:
                by_report[rk] = by_report.get(rk, 0) + 1
            report_summary = ", ".join(f"{rk}({n})" for rk, n in sorted(by_report.items()))
            lines_msg.append(f"  {store}: {report_summary}")
        lines_msg.append("")
        lines_msg.append("Update coverage rules in QB Sync options if needed.")
        lines_msg.append("Run Refresh Drive Inventory in Settings to update the snapshot.")
        lines_msg.append("Sync will continue without blocking.")
        msg = "\n".join(lines_msg)
        return False, msg

    def _stop_sync(self):
        if not self._stop_sync_event.is_set():
            self._stop_sync_event.set()
            self.log("Stop requested. Sync will finish the current item, then stop.")
            self.stop_sync_btn.configure(state="disabled")

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
        if self.require_coverage_var.get():
            guard_ok, guard_msg = self._check_coverage_guard(stores, dates)
            if not guard_ok:
                messagebox.showwarning("Coverage Guard", guard_msg)
                return
        source_filter = self.source_filter_var.get()
        self._set_validation_records([])
        self._running = True
        self._stop_sync_event.clear()
        self.sync_btn.configure(state="disabled", text="Syncing...")
        self.stop_sync_btn.configure(state="normal")
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
        start_time = time.time()
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
                        if self._stop_sync_event.is_set():
                            self.log("Stop requested. Ending sync batch.")
                            break
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
                                        self.log(f"  File not found on Drive: Toast/{toast_loc}/Sale Summary/{filename}")
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
            log(EventCategory.QB_SYNC, "QB Sync completed",
                detail=f"Sync completed — {success_count} success, {fail_count} failed",
                store=", ".join(stores) if stores else None, success=True,
                duration=time.time() - start_time)
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
            log(EventCategory.QB_SYNC, "QB Sync failed",
                detail=str(e), success=False)
        finally:
            publish_agentai_snapshot_if_configured(on_log=self.log)
            completion_callback = self._run_completion_callback
            self._run_completion_callback = None
            if completion_callback:
                self.after(0, lambda payload=completion_payload, cb=completion_callback: cb(payload))
            self._running = False
            self.after(0, lambda: self.sync_btn.configure(state="normal", text="Sync to QuickBooks"))
            self.after(0, lambda: self.stop_sync_btn.configure(state="disabled"))
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

