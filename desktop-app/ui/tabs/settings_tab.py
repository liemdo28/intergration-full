"""
ui/tabs/settings_tab.py - SettingsTab class for Toast POS Manager.
Extracted from app.py for modularity.
"""
import sys
import os
import json
import threading
import logging
import webbrowser
from pathlib import Path
from datetime import datetime, timedelta
from tkinter import filedialog

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

from app_shared import (
    LOCAL_CONFIG_FILE, TOAST_LOCATIONS, MAPPING_FILE, REPORTS_DIR,
    load_local_config, save_local_config, load_mapping,
    make_section_card, make_subcard, make_action_button,
    make_hero_banner, style_scrollable_frame,
    UI_CARD_FG, UI_CARD_BORDER, UI_MUTED_TEXT, UI_HEADING_TEXT,
    UI_ACCENT_BLUE, UI_ACCENT_TEAL,
    REQUIRED_REPORT_RULES, get_required_reports, load_required_report_rules,
    get_operator_mode,
)
from app_paths import APP_DIR, RUNTIME_DIR, app_path, runtime_path
from diagnostics import format_report_lines, run_environment_checks
from recovery_center import (
    backup_and_remove, ensure_runtime_file_from_example, export_support_bundle,
    format_playbook, get_playbook_by_title, get_recovery_playbooks,
)
from runtime_manifest import build_manifest, RuntimeManifest
from services.feature_readiness_service import check_all_features
from models.feature_readiness import FeatureKey, ReadinessStatus
from agentai_sync import (
    acknowledge_agentai_command, fetch_next_agentai_command, get_agentai_sync_settings,
    heartbeat_agentai_command, is_agentai_sync_ready, publish_integration_snapshot,
    report_agentai_command_result,
)
from worker_runtime import get_background_worker_settings, update_runtime_state, utc_now_iso
from ui.widgets.status_badge import StatusBadge, Status as SBStatus

class SettingsTab(ctk.CTkFrame):
    def __init__(self, master, run_diagnostics=None, status_var=None, **kwargs):
        super().__init__(master, **kwargs)
        self._app = master  # reference to parent App instance
        self.run_diagnostics = run_diagnostics
        self.status_var = status_var
        self.recovery_playbooks = get_recovery_playbooks()
        self._local_cfg = load_local_config()
        load_required_report_rules(self._local_cfg)
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

        # ── Operator Mode ──
        self._build_operator_mode_section(content)

        # ── Feature Readiness ──
        _read_card, read_frame = make_section_card(
            content,
            "Feature Readiness",
            "Shows whether each feature can run on this machine.",
        )
        from ui.widgets.status_badge import StatusBadge, Status as SBStatus

        self.readiness_vars: dict[str, dict] = {}
        self._readiness_badges: dict[str, StatusBadge] = {}
        self._readiness_reason_labels: dict[str, ctk.CTkLabel] = {}

        _FEATURE_READINESS_MAP = [
            ("download",   "Download Reports (Playwright/Toast)",        "download"),
            ("qb_sync",    "QB Sync (QuickBooks Desktop)",              "qb_sync"),
            ("remove_tx",  "Remove Transactions (QB cleanup)",           "remove_tx"),
            ("drive",      "Drive Upload (Google Drive API)",             "drive"),
        ]

        # 2-column grid layout
        for i, (badge_key, feat_label, svc_key) in enumerate(_FEATURE_READINESS_MAP):
            row = ctk.CTkFrame(read_frame, fg_color="transparent")
            row.pack(fill="x", pady=4)
            # Feature name label
            ctk.CTkLabel(
                row, text=feat_label,
                font=ctk.CTkFont(size=12), anchor="w", width=240,
                text_color="#e2e8f0",
            ).pack(side="left", padx=(0, 8))
            # Status badge
            badge = StatusBadge(row, status=SBStatus.UNKNOWN, text="Checking...")
            badge.pack(side="left", padx=(0, 8))
            self._readiness_badges[badge_key] = badge
            # Reason label
            reason_lbl = ctk.CTkLabel(
                row, text="",
                font=ctk.CTkFont(size=11), anchor="w",
                text_color="#64748b", wraplength=400,
            )
            reason_lbl.pack(side="left", fill="x", expand=True)
            self._readiness_reason_labels[badge_key] = reason_lbl
            self.readiness_vars[badge_key] = {"label": feat_label}

        def _refresh_readiness_ui():
            if not hasattr(self, "_app") or not self._app:
                return
            readiness = self._app.get_readiness()
            for badge_key, _, svc_key in _FEATURE_READINESS_MAP:
                info = readiness.get(svc_key, {})
                reason = info.get("reason", "Unknown")
                ready = info.get("ready", False)
                if info.get("status") == "warning":
                    badge_status = SBStatus.WARNING
                elif info.get("status") == "partial":
                    badge_status = SBStatus.PARTIAL
                elif ready:
                    badge_status = SBStatus.READY
                elif info.get("status") in ("blocked", "unknown"):
                    badge_status = SBStatus.BLOCKED
                else:
                    badge_status = SBStatus.UNKNOWN
                if badge_key in self._readiness_badges:
                    self._readiness_badges[badge_key].set(badge_status, badge_status.label)
                if badge_key in self._readiness_reason_labels:
                    self._readiness_reason_labels[badge_key].configure(text=reason)
            if hasattr(_refresh_readiness_ui, "_after_id"):
                try:
                    self.after_cancel(getattr(_refresh_readiness_ui, "_after_id"))
                except Exception:
                    pass
            _refresh_readiness_ui._after_id = self.after(5000, _refresh_readiness_ui)

        self.after(500, _refresh_readiness_ui)

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
        make_action_button(
            drive_inventory_btns,
            "Download Missing from Drive",
            self._download_missing_from_drive,
            tone="teal",
            width=210,
        ).pack(side="left", padx=(0, 8))
        make_action_button(
            drive_inventory_btns,
            "Export Missing CSV",
            self._export_missing_csv,
            tone="neutral",
            width=140,
        ).pack(side="left", padx=(0, 8))
        make_action_button(
            drive_inventory_btns,
            "Export Missing CSV",
            self._export_missing_csv,
            tone="neutral",
            width=140,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            drive_inventory_btns,
            text="Rows sort by missing coverage first, then by store and report type.",
            text_color=UI_MUTED_TEXT,
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(4, 0))

        # ── Month selector for coverage matrix ──
        month_row = ctk.CTkFrame(drive_inventory_frame, fg_color="transparent")
        month_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(month_row, text="Coverage Month:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 6))
        self.coverage_month_var = ctk.StringVar(value=datetime.now().strftime("%Y-%m"))
        month_options = []
        now = datetime.now()
        for i in range(6, -1, -1):
            d = (now - timedelta(days=i * 30)).replace(day=1)
            month_options.append(d.strftime("%Y-%m"))
        month_menu = ctk.CTkOptionMenu(month_row, variable=self.coverage_month_var, values=month_options, width=110)
        month_menu.pack(side="left", padx=(0, 8))
        make_action_button(month_row, "Refresh for Month", self._refresh_drive_inventory, tone="neutral", width=150).pack(side="left")

        # ── Upload suggestion box ──
        ctk.CTkLabel(
            drive_inventory_frame,
            text="Missing File Suggestions",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", pady=(4, 4))
        upload_hint_frame = ctk.CTkFrame(drive_inventory_frame, fg_color="#1e293b", corner_radius=8)
        upload_hint_frame.pack(fill="x", pady=(0, 8))
        self.upload_suggestion_var = ctk.StringVar(
            value="Click 'Refresh for Month' to see missing reports and upload suggestions."
        )
        self.upload_suggestion_label = ctk.CTkLabel(
            upload_hint_frame,
            textvariable=self.upload_suggestion_var,
            text_color="#60a5fa",
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
            wraplength=560,
        )
        self.upload_suggestion_label.pack(anchor="w", padx=12, pady=10)
        copy_btn_row = ctk.CTkFrame(upload_hint_frame, fg_color="transparent")
        copy_btn_row.pack(fill="x", padx=12, pady=(0, 10))
        self.upload_path_var = ctk.StringVar(value="")
        self.upload_path_entry = ctk.CTkEntry(copy_btn_row, textvariable=self.upload_path_var, font=ctk.CTkFont(size=11), width=400)
        self.upload_path_entry.pack(side="left", padx=(0, 8))
        make_action_button(copy_btn_row, "Copy Path", self._copy_upload_path, tone="neutral", width=100).pack(side="left")

        ctk.CTkLabel(
            drive_inventory_frame,
            text="Coverage Matrix",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", pady=(2, 4))
        tree_outer = ctk.CTkFrame(drive_inventory_frame, fg_color="#1e293b", corner_radius=6)
        tree_outer.pack(fill="x", pady=(0, 6))
        tree_tk = tk.Frame(tree_outer, bg="#1e293b")
        tree_tk.pack(fill="x", padx=4, pady=4)
        vsb = tk.Scrollbar(tree_tk, orient="vertical")
        hsb = tk.Scrollbar(tree_tk, orient="horizontal")
        COLS = ("store","report","health","files","first","last","missing","next_gap","last_file")
        self.drive_matrix_tree = tkinter_ttk.Treeview(
            tree_tk, columns=COLS, show="headings", height=8,
            yscrollcommand=vsb.set, xscrollcommand=hsb.set,
        )
        vsb.config(command=self.drive_matrix_tree.yview)
        hsb.config(command=self.drive_matrix_tree.xview)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.drive_matrix_tree.pack(fill="x")
        self.drive_matrix_tree.tag_configure("ready", background="#1a3d2e", foreground="#4ade80")
        self.drive_matrix_tree.tag_configure("partial", background="#3d2a1a", foreground="#fbbf24")
        self.drive_matrix_tree.tag_configure("missing", background="#3d1a1a", foreground="#f87171")
        self.drive_matrix_tree.tag_configure("empty", background="#1a1a2e", foreground="#94a3b8")
        col_widths = {"store":90,"report":160,"health":72,"files":50,"first":90,"last":90,"missing":60,"next_gap":90,"last_file":200}
        col_labels = {"store":"Store","report":"Report Type","health":"Health","files":"Files","first":"First Date","last":"Last Date","missing":"Missing","next_gap":"Next Gap","last_file":"Latest File"}
        for col in COLS:
            self.drive_matrix_tree.heading(col, text=col_labels[col])
            self.drive_matrix_tree.column(col, width=col_widths[col], anchor="w", minwidth=60)
        self.drive_matrix_tree.bind("<<TreeviewSelect>>", self._on_matrix_row_selected)

        ctk.CTkLabel(
            drive_inventory_frame,
            text="Selection Detail",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))
        detail_frm = ctk.CTkFrame(drive_inventory_frame, fg_color="#1e293b", corner_radius=6)
        detail_frm.pack(fill="x", pady=(0, 6))
        self.drive_detail_var = ctk.StringVar(value="Click a row above to see full details.")
        ctk.CTkLabel(
            detail_frm, textvariable=self.drive_detail_var,
            text_color="#94a3b8", font=ctk.CTkFont(family="Consolas", size=11),
            anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w", padx=10, pady=10)

        ctk.CTkLabel(
            drive_inventory_frame,
            text="Missing Ranges",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))
        miss_frm = ctk.CTkFrame(drive_inventory_frame, fg_color="#1e293b", corner_radius=6)
        miss_frm.pack(fill="x")
        miss_tk = tk.Frame(miss_frm, bg="#1e293b")
        miss_tk.pack(fill="x", padx=4, pady=4)
        m_vsb = tk.Scrollbar(miss_tk, orient="vertical")
        m_hsb = tk.Scrollbar(miss_tk, orient="horizontal")
        m_cols = ("store","report","start","end","count","reason")
        self.drive_missing_tree = tkinter_ttk.Treeview(
            miss_tk, columns=m_cols, show="headings", height=5,
            yscrollcommand=m_vsb.set, xscrollcommand=m_hsb.set,
        )
        m_vsb.config(command=self.drive_missing_tree.yview)
        m_hsb.config(command=self.drive_missing_tree.xview)
        m_vsb.pack(side="right", fill="y")
        m_hsb.pack(side="bottom", fill="x")
        self.drive_missing_tree.pack(fill="x")
        m_widths = {"store":90,"report":160,"start":100,"end":100,"count":50,"reason":160}
        m_labels = {"store":"Store","report":"Report","start":"Start","end":"End","count":"Days","reason":"Reason"}
        for col in m_cols:
            self.drive_missing_tree.heading(col, text=m_labels[col])
            self.drive_missing_tree.column(col, width=m_widths[col], anchor="w", minwidth=60)

        self.drive_inventory_box = None
        self.drive_missing_box = None

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

    def _build_operator_mode_section(self, parent):
        card, body = make_section_card(parent, "Access Level", "Control which features are visible in the sidebar")

        mode_frame = ctk.CTkFrame(body, fg_color="transparent")
        mode_frame.pack(fill="x", pady=4)

        ctk.CTkLabel(mode_frame, text="Access Level:", font=ctk.CTkFont(size=12),
                     text_color="#94a3b8").pack(side="left")

        self._mode_var = ctk.StringVar(value=get_operator_mode())

        ctk.CTkRadioButton(mode_frame, text="Standard Operator", variable=self._mode_var,
                           value="standard", command=self._save_operator_mode).pack(side="left", padx=(12, 8))
        ctk.CTkRadioButton(mode_frame, text="Admin / Support", variable=self._mode_var,
                           value="admin", command=self._save_operator_mode).pack(side="left")

        ctk.CTkLabel(body, text="Standard: shows guided wizards only.  Admin: shows all raw tabs + audit tools.",
                     font=ctk.CTkFont(size=11), text_color="#64748b", anchor="w").pack(anchor="w")

    def _save_operator_mode(self):
        try:
            cfg = load_local_config()
            cfg["operator_mode"] = self._mode_var.get()
            save_local_config(cfg)
            if self.status_var:
                self.status_var.set("Access level saved. Restart app to apply sidebar changes.")
        except Exception as e:
            if self.status_var:
                self.status_var.set(f"Could not save access level: {e}")

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
        missing_count_sum = sum(1 for row in summary_rows if row["health"] == "missing")
        empty_count = sum(1 for row in summary_rows if row["health"] == "empty")
        summary_text = (
            f"Drive snapshot: {len(inventory_rows)} file rows, {len(summary_rows)} store/report lanes, "
            + str(missing_count_sum) + " with gaps, " + str(empty_count) + " empty, " + str(ready_count) + " ready."
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
        tree = getattr(self, "drive_matrix_tree", None)
        if tree:
            for item in tree.get_children():
                tree.delete(item)
            HEALTH_MAP = {"ready":"READY","partial":"PARTIAL","missing":"MISSING","empty":"EMPTY"}
            for row in summary_rows:
                tag = row["health"]
                tree.insert("", "end", values=(
                    row["store"], row["report_label"],
                    HEALTH_MAP.get(tag, tag.upper()),
                    row["available_dates_count"],
                    row.get("first_date") or "-",
                    row.get("last_date") or "-",
                    row["missing_count"],
                    row.get("next_missing_date") or "-",
                    (row.get("latest_file_name") or "-")[:40],
                ), tags=(tag,))
        missing_tree = getattr(self, "drive_missing_tree", None)
        if missing_tree:
            for item in missing_tree.get_children():
                missing_tree.delete(item)
            by_pair = {}
            for row in missing_rows:
                by_pair.setdefault((row["store"], row["report_label"]), []).append(row)
            grouped = []
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
                    grouped.append((store, report_label, start, prev, count, rows[0].get("reason", "")))
                    start = current
                    prev = current
                    count = 1
                grouped.append((store, report_label, start, prev, count, rows[0].get("reason", "")))
            for store, report_label, start, end, count, reason in grouped[:200]:
                reason_short = reason.replace("_", " ").title() if reason else "-"
                tag = reason.split("_")[0] if reason else "gap"
                end_disp = "-" if start == end else end
                missing_tree.insert("", "end", values=(
                    store, report_label, start, end_disp, count, reason_short
                ), tags=(tag,))
        self.drive_inventory_summary.configure(
            text="Drive snapshot: " + str(len(inventory_rows)) + " file rows, " + str(len(summary_rows)) + " store/report lanes, "
                 + str(missing_count_sum) + " with gaps, " + str(empty_count) + " empty, " + str(ready_count) + " ready.",
            text_color="#cbd5e1"
        )

    def _on_matrix_row_selected(self, event):
        tree = getattr(self, "drive_matrix_tree", None)
        if not tree:
            return
        sel = tree.selection()
        if not sel:
            return
        vals = tree.item(sel[0], "values")
        if not vals:
            return
        store, report, health, files, first, last, missing, next_gap, last_file = vals
        parts = [
            ("Store:", str(store)),
            ("Report:", str(report)),
            ("Health:", str(health)),
            ("Files found:", str(files)),
            ("First date:", str(first)),
            ("Last date:", str(last)),
            ("Missing days:", str(missing)),
            ("Next gap:", str(next_gap)),
            ("Latest file:", str(last_file)),
        ]
        self.drive_detail_var.set("\n".join("%-14s %s" % p for p in parts))

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
                self.after(0, lambda snap=snapshot: self._update_upload_suggestions(snap))
                if self.status_var is not None:
                    self.after(0, lambda: self.status_var.set("Drive inventory refreshed"))
            except Exception as exc:
                if self.status_var is not None:
                    self.after(0, lambda: self.status_var.set("Drive inventory failed"))
                self.after(0, lambda err=str(exc): messagebox.showerror("Drive Inventory", err))

        if self.status_var is not None:
            self.status_var.set("Refreshing Drive inventory...")
        threading.Thread(target=_worker, daemon=True).start()

    def _copy_upload_path(self):
        """Copy the currently shown upload path to clipboard."""
        import pyperclip
        path = self.upload_path_var.get()
        if path:
            try:
                pyperclip.copy(path)
                if self.status_var:
                    self.status_var.set("Upload path copied to clipboard!")
            except Exception:
                pass

    def _update_upload_suggestions(self, snapshot):
        """Build upload suggestions from the Drive inventory snapshot."""
        try:
            missing_rows = snapshot.get("missing_rows", [])
            if not missing_rows:
                self.upload_suggestion_var.set(
                    "All reports for this month are present on Google Drive. No uploads needed."
                )
                self.upload_path_var.set("")
                return

            # Count by store/type
            from collections import defaultdict
            by_pair = defaultdict(list)
            for row in missing_rows:
                key = (row["store"], row["report_key"], row["report_label"])
                by_pair[key].append(row["business_date"])

            lines = []
            for (store, rk, label), dates in sorted(by_pair.items())[:6]:
                dates_str = ", ".join(sorted(dates)[:5])
                if len(dates) > 5:
                    dates_str += f" (+{len(dates) - 5} more)"
                lines.append(
                    f"  {store} / {label}: missing {len(dates)} day(s) -> "
                    f"Upload to: Toast/{store}/{label}/"
                )
            if len(by_pair) > 6:
                lines.append(f"  ...and {len(by_pair) - 6} more store/report combinations.")

            msg = (
                f"{len(missing_rows)} total missing file(s) found.\n"
                + "\n".join(lines)
                + "\n\nSelect a missing item above to see the exact upload path."
            )
            self.upload_suggestion_var.set(msg)

            # Default path: first missing item
            first = sorted(by_pair.items())[0]
            store, rk = first[0][0], first[0][1]
            label = first[0][2]
            self.upload_path_var.set(f"Toast/{store}/{label}/")

        except Exception as e:
            self.upload_suggestion_var.set(f"Could not build suggestions: {e}")

    def _export_missing_csv(self):
        """Export missing report records to CSV."""
        try:
            import csv as _csv
            from tkinter import filedialog
            from app_paths import runtime_path
            import sqlite3
            db_path = runtime_path("report-inventory.db")
            if not db_path.exists():
                messagebox.showinfo("Export CSV", "Run Refresh Drive Inventory first.")
                return
            default_name = "missing_reports_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
            out_path = filedialog.asksaveasfilename(
                title="Save Missing Reports CSV",
                defaultextension=".csv",
                initialfile=default_name,
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
            if not out_path:
                return
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT store, report_key, report_label, business_date, reason, detected_at, download_supported "
                "FROM drive_missing_report_records ORDER BY store, report_key, business_date"
            ).fetchall()
            conn.close()
            with open(out_path, "w", newline="", encoding="utf-8-sig") as csvfile:
                writer = _csv.writer(csvfile)
                writer.writerow(["Store","Report Key","Report Label","Business Date","Reason","Detected At","Downloadable"])
                for row in rows:
                    writer.writerow(list(row))
            if self.status_var:
                self.status_var.set("Exported " + str(len(rows)) + " missing records to CSV")
            messagebox.showinfo("Export CSV", "Saved " + str(len(rows)) + " rows to: " + out_path)
        except Exception as exc:
            messagebox.showerror("Export CSV", "Export failed: " + str(exc))

    def _export_missing_csv(self):
        """Export missing report records to CSV."""
        try:
            import csv as _csv
            from tkinter import filedialog
            from app_paths import runtime_path
            import sqlite3
            db_path = runtime_path("report-inventory.db")
            if not db_path.exists():
                messagebox.showinfo("Export CSV", "Run Refresh Drive Inventory first.")
                return
            default_name = "missing_reports_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
            out_path = filedialog.asksaveasfilename(
                title="Save Missing Reports CSV",
                defaultextension=".csv",
                initialfile=default_name,
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
            if not out_path:
                return
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT store, report_key, report_label, business_date, reason, detected_at, download_supported "
                "FROM drive_missing_report_records ORDER BY store, report_key, business_date"
            ).fetchall()
            conn.close()
            with open(out_path, "w", newline="", encoding="utf-8-sig") as csvfile:
                writer = _csv.writer(csvfile)
                writer.writerow(["Store","Report Key","Report Label","Business Date","Reason","Detected At","Downloadable"])
                for row in rows:
                    writer.writerow(list(row))
            if self.status_var:
                self.status_var.set("Exported " + str(len(rows)) + " missing records to CSV")
            messagebox.showinfo("Export CSV", "Saved " + str(len(rows)) + " rows to: " + out_path)
        except Exception as exc:
            messagebox.showerror("Export CSV", "Export failed: " + str(exc))

    def _download_missing_from_drive(self):
        """Pull missing reports from Google Drive back to local toast-reports/ folder."""
        def _worker():
            try:
                from gdrive_service import GDriveService
                from report_inventory import extract_business_dates_from_name
                from toast_reports import build_local_report_dir

                gdrive = GDriveService(config=self._local_cfg)
                if not gdrive.authenticate():
                    self.after(0, lambda: messagebox.showerror("Google Drive", "Auth failed"))
                    return

                import sqlite3
                from app_paths import runtime_path
                db_path = runtime_path("report-inventory.db")
                if not db_path.exists():
                    self.after(0, lambda: messagebox.showinfo("Drive Inventory", "Run Refresh Drive Inventory first."))
                    return

                conn = sqlite3.connect(db_path)
                missing = conn.execute(
                    "SELECT store, report_key, report_label, business_date "
                    "FROM drive_missing_report_records "
                    "WHERE download_supported = 1 "
                    "ORDER BY store, report_key, business_date"
                ).fetchall()
                drive_files = conn.execute(
                    "SELECT store, report_key, filename FROM drive_report_inventory"
                ).fetchall()
                conn.close()

                if not missing:
                    self.after(0, lambda: messagebox.showinfo("Drive Inventory", "No missing reports found."))
                    return

                drive_lookup = {}
                for store, rk, fname in drive_files:
                    dates = extract_business_dates_from_name(fname)
                    for d in dates:
                        drive_lookup.setdefault((store, rk, d), []).append(fname)

                downloaded = 0
                skipped = 0
                errors = 0

                for store, rk, rlabel, bdate in missing:
                    filenames = drive_lookup.get((store, rk, bdate), [])
                    if not filenames:
                        skipped += 1
                        continue
                    fname = filenames[0]
                    try:
                        local_dir = str(build_local_report_dir(
                            self._local_cfg.get("reports_base_dir", ""),
                            store, rk
                        ))
                        result = gdrive.download_report(store, fname, local_dir, report_type=rk)
                        downloaded += 1
                        if self.status_var:
                            self.after(0, lambda p=result: self.status_var.set("Downloaded: " + p))
                    except Exception as e:
                        errors += 1
                        gdrive.log("  Error: " + str(e))

                status_msg = ("Drive download done: " + str(downloaded) + " pulled, "
                              + str(skipped) + " not-on-Drive, " + str(errors) + " errors")
                if self.status_var:
                    self.after(0, lambda: self.status_var.set(status_msg))
                info_msg = ("Downloaded " + str(downloaded) + " report(s) from Drive.\n"
                            + str(skipped) + " not found on Drive (skipped).\n"
                            + str(errors) + " error(s).")
                self.after(0, lambda m=info_msg: messagebox.showinfo("Drive Inventory", m))
            except Exception as exc:
                if self.status_var:
                    self.after(0, lambda: self.status_var.set("Download from Drive failed"))
                self.after(0, lambda err=str(exc): messagebox.showerror("Drive Inventory", err))

        if self.status_var:
            self.status_var.set("Downloading missing from Drive...")
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


