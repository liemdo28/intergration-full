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
from tkinter import ttk as tkinter_ttk
from tkinter import ttk as tkinter_ttk
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



from safe_mode import is_safe_mode, get_safe_mode_config
from runtime_manifest import build_manifest, RuntimeManifest
from services.feature_readiness_service import check_all_features
from models.feature_readiness import FeatureKey, ReadinessStatus
from content.ui_copy import operator_msg, CopyKey
from services.activity_log_service import (
    log,
    EventCategory,
    EventSeverity,
)
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

# ── Tab modules — extracted for modularity ──────────────────────────
from app_shared import *  # shared constants and helpers (includes all constants, helpers)
from app_shared import get_operator_mode
from ui.tabs.download_tab import DownloadTab
from ui.tabs.qb_sync_tab import QBSyncTab
from ui.tabs.remove_tab import RemoveTab
from ui.tabs.settings_tab import SettingsTab


def _get_nav_order(operator_mode: str = "standard") -> list:
    """Returns nav items based on operator mode."""
    # Standard operator: guided wizard-first experience
    standard = ["home", "wizard_download", "wizard_qb", "settings", "recovery"]
    # Admin/support: full access including raw tabs and audit tools
    admin = ["home", "wizard_download", "wizard_qb", "download", "qb", "remove", "settings", "recovery", "audit"]
    return admin if operator_mode == "admin" else standard


from services.feature_readiness_service import readiness_to_ui_dict as _readiness_for

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
        # Safe mode banner
        if is_safe_mode():
            cfg = get_safe_mode_config()
            banner = ctk.CTkFrame(self, fg_color="#92400e", height=32)
            banner.pack(fill="x", padx=0, pady=0)
            banner.pack_propagate(False)
            ctk.CTkLabel(
                banner,
                text=f"SAFE MODE — {cfg.reason}  |  Background workers disabled",
                text_color="#fef3c7", font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(pady=6)


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
        # Listen for navigate:XXX commands from child tabs/widgets
        self._nav_trace_id: str | None = None
        def _on_nav_trace(*args):
            try:
                val = self.status_var.get()
                if val.startswith("navigate:"):
                    tab_key = val.split(":", 1)[1]
                    if hasattr(self, "_tab_frames") and tab_key in self._tab_frames:
                        self._switch_tab(tab_key)
            except Exception:
                pass
        self._nav_trace_id = self.status_var.trace_add("write", _on_nav_trace)
        # Readiness dashboard state
        self._readiness: dict[str, dict] = {
            "download": {"ready": False, "reason": "Checking..."},
            "qb_sync":  {"ready": False, "reason": "Checking..."},
            "remove_tx":{"ready": False, "reason": "Checking..."},
            "drive":    {"ready": False, "reason": "Checking..."},
        }
        self._manifest: RuntimeManifest | None = None

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
        log(EventCategory.APP_LIFECYCLE, "App started",
            detail=f"Runtime mode: {self.runtime_mode}", success=True)
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
        log(EventCategory.APP_LIFECYCLE, "App closed cleanly",
            detail=f"Runtime mode: {self.runtime_mode}", success=True)
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

        from services.ui_state_service import get_nav_theme
        self._nav_theme = get_nav_theme()
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
        _mode = get_operator_mode()
        nav_order = _get_nav_order(_mode)

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

        # Always create all frames even if not in nav
        all_frames = ["home", "wizard_download", "wizard_qb", "download", "qb", "remove", "settings", "recovery", "audit"]
        for key in all_frames:
            frame = ctk.CTkFrame(content, fg_color="transparent")
            frame.pack(fill="both", expand=True)
            self._tab_frames[key] = frame

        # Home dashboard tab
        from ui.home_dashboard import HomeDashboard
        self.home_tab = HomeDashboard(self._tab_frames["home"], status_var=self.status_var)
        self.home_tab.pack(fill="both", expand=True)

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

        # Recovery Center tab
        from ui.recovery_center import RecoveryCenter
        self.recovery_tab = RecoveryCenter(self._tab_frames["recovery"], status_var=self.status_var)
        self.recovery_tab.pack(fill="both", expand=True)

        # Audit / Activity Center tab
        from ui.activity_audit_center import ActivityAuditCenter
        self.audit_tab = ActivityAuditCenter(self._tab_frames["audit"], status_var=self.status_var)
        self.audit_tab.pack(fill="both", expand=True)

        # Wizard tabs
        from ui.wizards.download_reports_wizard import DownloadReportsWizard
        self.wizard_download_tab = DownloadReportsWizard(
            self._tab_frames["wizard_download"], status_var=self.status_var
        )
        self.wizard_download_tab.pack(fill="both", expand=True)

        from ui.wizards.qb_sync_wizard import QBSyncWizard
        self.wizard_qb_tab = QBSyncWizard(
            self._tab_frames["wizard_qb"], status_var=self.status_var
        )
        self.wizard_qb_tab.pack(fill="both", expand=True)

        # Default to Home tab
        self._active_tab = "home"
        for key in all_frames:
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
        from services.ui_state_service import get_diagnostics_status_display
        self.diagnostics_report = report
        display = get_diagnostics_status_display(report)
        text = display["text"]
        text_color = display["text_color"]
        badge_fg = display["badge_fg"]
        badge_border = display["badge_border"]
        self.status_var.set(display["status_bar_text"])

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
