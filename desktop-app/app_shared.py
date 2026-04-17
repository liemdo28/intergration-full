"""
app_shared.py — Shared constants, helpers, and UI utilities for Toast POS Manager tabs.
Extracted from app.py to avoid circular imports.
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
# ---------------------------------------------------------------------------
# Task 8: Config-driven required report rules per store
# REQUIRED_REPORT_RULES: store name -> frozenset of required report_key strings
# Unknown stores fall back to frozenset(DEFAULT_REPORT_TYPE_KEYS).
# ---------------------------------------------------------------------------
REQUIRED_REPORT_RULES: dict[str, frozenset[str]] = {}


def get_required_reports(store_name: str) -> frozenset[str]:
    """Return required report keys for a store (custom rules or defaults)."""
    return REQUIRED_REPORT_RULES.get(store_name, frozenset(DEFAULT_REPORT_TYPE_KEYS))


def load_required_report_rules(cfg: dict) -> None:
    """Load store-level required report rules from a config dict.

    Expected format::

        {
          "coverage_rules": {
            "Stockton":   ["sales_summary", "order_details"],
            "WA3":        ["sales_summary"],
          }
        }

    Clears and repopulates REQUIRED_REPORT_RULES.
    """
    global REQUIRED_REPORT_RULES
    REQUIRED_REPORT_RULES = {}
    rules = cfg.get("coverage_rules", {})
    for store, keys in rules.items():
        if isinstance(keys, (list, tuple, frozenset)):
            REQUIRED_REPORT_RULES[str(store)] = frozenset(k for k in keys if k)
        elif isinstance(keys, str):
            REQUIRED_REPORT_RULES[str(store)] = frozenset(
                k.strip() for k in keys.split(",") if k.strip()
            )
    import logging
    _lg = logging.getLogger(__name__)
    _lg.debug(f"Loaded coverage rules for {len(REQUIRED_REPORT_RULES)} store(s): {list(REQUIRED_REPORT_RULES.keys())}")
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


def get_operator_mode() -> str:
    """Returns 'standard' or 'admin'. Defaults to 'standard' if not configured."""
    try:
        cfg = load_local_config()
        return cfg.get("operator_mode", "standard")
    except Exception:
        return "standard"


def is_admin_mode() -> bool:
    return get_operator_mode() == "admin"


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

