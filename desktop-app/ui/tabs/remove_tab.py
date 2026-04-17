"""
ui/tabs/remove_tab.py — RemoveTab class for Toast POS Manager.
Extracted from app.py for modularity.
"""
import sys
import os
import csv
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from tkinter import filedialog

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

from app_shared import (
    LOCAL_CONFIG_FILE, TOAST_LOCATIONS, DELETE_AUDIT_DIR, AUDIT_LOG_DIR,
    MAPPING_FILE,
    load_mapping, load_local_config, save_local_config,
    make_log_box, append_log, make_section_card, make_subcard, make_action_button,
    make_hero_banner, style_scrollable_frame,
    UI_CARD_FG, UI_CARD_BORDER, UI_MUTED_TEXT, UI_HEADING_TEXT,
    UI_ACCENT_BLUE, UI_ACCENT_TEAL, UI_ACCENT_AMBER,
    UI_SUBCARD_FG, UI_SUBCARD_BORDER,
)
from app_paths import APP_DIR, RUNTIME_DIR, app_path, runtime_path
from audit_utils import write_delete_audit, export_transactions_snapshot
from delete_policy import load_delete_policy
from services.activity_log_service import log, EventCategory, EventSeverity

class RemoveTab(ctk.CTkFrame):
    def __init__(self, master, status_var, **kwargs):
        super().__init__(master, **kwargs)
        self.status_var = status_var
        self.qb = None
        self.accounts = []
        self.found_txns = []
        self._running = False
        self._stop_remove_event = threading.Event()
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
        self.btn_stop_remove = ctk.CTkButton(
            btn_frame, text="Stop Remove", height=36,
            fg_color="#b45309", hover_color="#92400e", corner_radius=12,
            command=self._stop_remove, state="disabled",
        )
        self.btn_stop_remove.pack(fill="x", padx=5, pady=3)
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
        self._stop_remove_event.clear()
        self.btn_delete.configure(state="disabled", text="Dry Running..." if dry_run else "Deleting...")
        self.btn_search.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self.btn_stop_remove.configure(state="normal")
        self._log(f"Starting {'dry run for' if dry_run else 'deletion of'} {count} transactions...")
        threading.Thread(target=self._delete_worker, args=(txn_list, snapshot_files, dry_run), daemon=True).start()

    def _delete_worker(self, txn_list, snapshot_files, dry_run):
        start_time = time.time()
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
                    if self._stop_remove_event.is_set():
                        self.after(0, lambda: self._log("Stop requested. Ending delete batch."))
                        break
                    on_progress(index, len(txn_list), txn, True, "Dry run only - no delete sent to QuickBooks")
                result = {
                    "success_count": 0,
                    "fail_count": 0,
                    "errors": [],
                    "dry_run": True,
                }
            else:
                result = self.qb.delete_transactions(txn_list, callback=on_progress, should_stop=self._stop_remove_event.is_set)
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
            _duration = time.time() - start_time
            log(EventCategory.REMOVE_TX, "Remove Transactions completed",
                detail=f"Removed {result['success_count']} transaction(s) ({result['fail_count']} failed)",
                store=None, success=True,
                duration=_duration)
            self.after(0, lambda r=result, d=_duration: self._on_delete_done(r, d))
        except Exception as e:
            err_msg = str(e)
            log(EventCategory.REMOVE_TX, "Remove Transactions failed",
                detail=str(e), success=False)
            self.after(0, lambda m=err_msg: self._on_delete_error(m))

    def _stop_remove(self):
        if not self._stop_remove_event.is_set():
            self._stop_remove_event.set()
            self._log("Stop requested. Delete will finish the current item, then stop.")
            self.btn_stop_remove.configure(state="disabled")

    def _on_delete_done(self, result, _duration=None):
        self._running = False
        self.btn_search.configure(state="normal")
        self.btn_export.configure(state="normal" if self.txn_rows else "disabled")
        self.btn_stop_remove.configure(state="disabled")
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

    def _update_readiness(self) -> None:
        """Update feature-level readiness states and write to runtime_manifest."""
        r = self._readiness

        # Download Reports readiness
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                bp = Path(pw.chromium.executable_path)
                if bp.exists():
                    r["download"]["ready"] = True
                    r["download"]["reason"] = "Chromium bundled and ready"
                else:
                    r["download"]["ready"] = False
                    r["download"]["reason"] = "Chromium not found — Toast download unavailable"
        except Exception as exc:
            r["download"]["ready"] = False
            r["download"]["reason"] = f"Playwright error: {exc}"

        # QB Sync readiness
        qb_ok = False
        try:
            from qb_automate import resolve_qb_executable
            qb_exe = resolve_qb_executable()
            if qb_exe and Path(qb_exe).exists():
                qb_ok = True
        except Exception:
            pass
        env_path = runtime_path(".env.qb")
        if not qb_ok:
            r["qb_sync"]["ready"] = False
            r["qb_sync"]["reason"] = "QuickBooks Desktop not found — QB Sync disabled"
        elif not any(env_path.read_text(encoding="utf-8", errors="ignore").splitlines()):
            r["qb_sync"]["ready"] = False
            r["qb_sync"]["reason"] = "QB not configured — enter passwords in .env.qb"
        else:
            r["qb_sync"]["ready"] = True
            r["qb_sync"]["reason"] = "QB Desktop found and configured"

        # Remove Transactions readiness
        if qb_ok:
            r["remove_tx"]["ready"] = True
            r["remove_tx"]["reason"] = "QB Desktop available"
        else:
            r["remove_tx"]["ready"] = False
            r["remove_tx"]["reason"] = "QB Desktop not found"

        # Drive readiness
        cred_path = runtime_path("credentials.json")
        token_path = runtime_path("token.json")
        if cred_path.exists() and token_path.exists():
            r["drive"]["ready"] = True
            r["drive"]["reason"] = "Google Drive connected"
        elif cred_path.exists():
            r["drive"]["ready"] = False
            r["drive"]["reason"] = "Connected to Google but no auth token yet"
        else:
            r["drive"]["ready"] = False
            r["drive"]["reason"] = "credentials.json not found — see Settings"

        # Build and save manifest
        try:
            self._manifest = build_manifest()
            self._manifest.save()
        except Exception as exc:
            logging.warning(f"Could not save runtime manifest: {exc}")

    def get_readiness(self) -> dict[str, dict]:
        """Return the current readiness state for all features.

        Uses feature_readiness_service for real data, falls back to the
        cached _readiness dict if the service is unavailable.
        """
        try:
            results = check_all_features()
            return {
                "download":   _readiness_for(results.get(FeatureKey.REPORT_DOWNLOAD)),
                "qb_sync":    _readiness_for(results.get(FeatureKey.QB_SYNC)),
                "remove_tx":  _readiness_for(results.get(FeatureKey.REMOVE_TX)),
                "drive":      _readiness_for(results.get(FeatureKey.GOOGLE_DRIVE)),
            }
        except Exception:
            return self._readiness  # fallback to cached state


def _readiness_for(fr) -> dict:
    """Convert a FeatureReadiness object to the legacy dict shape."""
    if fr is None:
        return {"ready": False, "reason": "Unknown state"}
    return {
        "ready": fr.status == ReadinessStatus.READY,
        "reason": fr.reason,
        "next_step": fr.next_step,
        "status": fr.status.value,
    }
#  Tab 4: Settings
# ══════════════════════════════════════════════════════════════════════

