"""
ToastPOSManager — Recovery Center UI Tab

Shows app health, config health, browser status, and crash history.
Provides one-click recovery actions for common issues.
"""

from __future__ import annotations

import threading

try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False

from pathlib import Path
import sys

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


BUNDLE_DIR = _resolve_bundle_dir()
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE_DIR

# ---------------------------------------------------------------------------
# Internal imports
# ---------------------------------------------------------------------------
from services.recovery_service import (
    get_app_health,
    get_config_health,
    get_browser_health,
    get_crash_history,
    reset_config_to_defaults,
    clear_toast_session,
    open_runtime_folder,
    toggle_safe_mode,
    export_support_bundle,
)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------
def _make_section_card(parent, title: str, description: str = ""):
    """Section card builder — always uses local implementation for reliability."""
    outer = ctk.CTkFrame(parent, fg_color="transparent")
    outer.pack(fill="x", pady=(0, 14))
    header = ctk.CTkFrame(outer, fg_color="#1e293b", corner_radius=8)
    header.pack(fill="x", pady=(0, 4))
    ctk.CTkLabel(header, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                text_color="#f8fafc", anchor="w").pack(anchor="w", padx=12, pady=(10, 2))
    if description:
        ctk.CTkLabel(header, text=description, font=ctk.CTkFont(size=11),
                    text_color="#64748b", anchor="w").pack(anchor="w", padx=12, pady=(0, 8))
    inner = ctk.CTkFrame(outer, fg_color="transparent")
    inner.pack(fill="x")
    return outer, inner


def _make_action_button(parent, text: str, command, tone: str = "neutral", width: int = 140, **kwargs):
    colors = {
        "primary": ("#3b82f6", "#2563eb"),
        "danger":   ("#ef4444", "#dc2626"),
        "warning":  ("#f59e0b", "#d97706"),
        "neutral":  ("#334155", "#1e293b"),
    }
    fg, hover = colors.get(tone, colors["neutral"])
    btn = ctk.CTkButton(parent, text=text, width=width, fg_color=fg, hover_color=hover, **kwargs)
    btn.configure(command=command)
    return btn


def _pill(parent, text: str, fg_color: str, text_color: str = "#f8fafc"):
    """Small inline label with colored background."""
    lbl = ctk.CTkLabel(parent, text=text, fg_color=fg_color, text_color=text_color,
                      font=ctk.CTkFont(size=11, weight="bold"), corner_radius=6)
    lbl.pack(side="left", padx=(0, 6))
    return lbl


# ---------------------------------------------------------------------------
# Recovery Center
# ---------------------------------------------------------------------------

class RecoveryCenter(ctk.CTkFrame if CTK else object):
    """Recovery Center tab content frame."""

    def __init__(self, master, status_var=None, **kwargs):
        # Strip CTk-only kwargs for non-CTk fallback
        if not CTK:
            super().__init__(**kwargs)
            return

        super().__init__(master, fg_color="transparent", **kwargs)
        self._status_var = status_var
        self._health_data: dict = {}
        self._build_ui()
        self.after(300, self._refresh_all)

    def _build_ui(self):
        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)

        # ── Hero banner ──────────────────────────────────────────────────
        self._hero_card(content)

        # ── Sections ──────────────────────────────────────────────────────
        self._section_app_health(content)
        self._section_config_health(content)
        self._section_browser_health(content)
        self._section_crash_history(content)
        self._section_recovery_actions(content)
        self._section_support_bundle(content)

    def _hero_card(self, parent):
        outer = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=12)
        outer.pack(fill="x", pady=(0, 20))
        inner = ctk.CTkFrame(outer, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(left, text="Recovery Center",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="#f8fafc", anchor="w").pack(anchor="w")
        ctk.CTkLabel(left, text="Diagnose app health and repair common issues without losing your configuration.",
                     font=ctk.CTkFont(size=12), text_color="#94a3b8", anchor="w",
                     wraplength=560).pack(anchor="w", pady=(4, 0))

        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="right")
        self._refresh_all_btn = _make_action_button(right, "Refresh All",
                                                     self._refresh_all, tone="neutral", width=120)

    # ── App Health ─────────────────────────────────────────────────────────
    def _section_app_health(self, parent):
        _outer, frame = _make_section_card(parent, "App Health", "Runtime environment and version information.")
        self._health_labels: dict[str, ctk.CTkLabel] = {}
        self._health_badges: dict[str, ctk.CTkLabel] = {}

        rows = [
            ("python_version",  "Python"),
            ("platform",        "Platform"),
            ("runtime_dir",     "Runtime folder"),
            ("app_version",     "App version"),
            ("safe_mode_active","Safe Mode"),
            ("crash_markers",   "Crash markers"),
        ]
        for key, label in rows:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=11), anchor="w",
                         text_color="#94a3b8", width=140).pack(side="left")
            val_lbl = ctk.CTkLabel(row, text="...", font=ctk.CTkFont(size=11),
                                   anchor="w", text_color="#f8fafc")
            val_lbl.pack(side="left", fill="x", expand=True)
            self._health_labels[key] = val_lbl

    def _section_config_health(self, parent):
        _outer, frame = _make_section_card(parent, "Configuration Files", "Status of key user settings files.")
        self._config_rows: list[ctk.CTkFrame] = []
        self._config_labels: dict[str, dict] = {}
        # Placeholder rows — populated by _load_config_health
        for fname in [".env.qb", "local-config.json", "credentials.json", "token.json"]:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=3)
            name_lbl = ctk.CTkLabel(row, text=fname, font=ctk.CTkFont(size=11, weight="bold"),
                                   anchor="w", text_color="#e2e8f0", width=180)
            name_lbl.pack(side="left")
            status_lbl = ctk.CTkLabel(row, text="Loading...", font=ctk.CTkFont(size=11),
                                      anchor="w", text_color="#64748b")
            status_lbl.pack(side="left", padx=(8, 0))
            self._config_labels[fname] = {"name": name_lbl, "status": status_lbl}

    def _section_browser_health(self, parent):
        _outer, frame = _make_section_card(parent, "Browser (Chromium) Status",
                                           "Whether the automated report browser is available.")
        self._browser_status_lbl = ctk.CTkLabel(frame, text="Checking...", font=ctk.CTkFont(size=11),
                                                 anchor="w", text_color="#64748b")
        self._browser_status_lbl.pack(anchor="w")
        self._browser_path_lbl = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=10),
                                              anchor="w", text_color="#475569")
        self._browser_path_lbl.pack(anchor="w", pady=(2, 0))

    def _section_crash_history(self, parent):
        _outer, frame = _make_section_card(parent, "Recent Crashes / Safe Mode Activations",
                                           "Safe mode is reset automatically after a clean app exit.")
        self._crash_list_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self._crash_list_frame.pack(fill="x")
        self._crash_empty_lbl = ctk.CTkLabel(frame, text="No crash history — app is running cleanly.",
                                              font=ctk.CTkFont(size=11), text_color="#475569")
        self._crash_empty_lbl.pack(anchor="w")

    def _section_recovery_actions(self, parent):
        _outer, frame = _make_section_card(parent, "Recovery Actions",
                                           "Use these to repair the app without losing your settings.")
        grid = ctk.CTkFrame(frame, fg_color="transparent")
        grid.pack(fill="x")

        actions = [
            ("Reset Config to Defaults",  "danger",   reset_config_to_defaults,   "Removes custom settings, restores from templates."),
            ("Clear Toast Session",        "warning",  clear_toast_session,       "Sign out of Toast and clear saved session files."),
            ("Toggle Safe Mode",           "neutral",  toggle_safe_mode,         "Enable or disable safe mode manually."),
            ("Open Runtime Folder",        "neutral",  open_runtime_folder,      "Open the app folder in Windows Explorer."),
        ]
        for i, (label, tone, fn, desc) in enumerate(actions):
            col = i % 2
            row = i // 2
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=row, column=col, sticky="w", padx=(0, 12), pady=6)
            btn = _make_action_button(cell, label, lambda f=fn: self._run_action(f), tone=tone, width=200)
            btn.pack(anchor="w")
            ctk.CTkLabel(cell, text=desc, font=ctk.CTkFont(size=10),
                         text_color="#64748b", anchor="w", wraplength=280).pack(anchor="w", pady=(2, 0))

        # Result message area
        self._action_result_lbl = ctk.CTkLabel(
            frame, text="", font=ctk.CTkFont(size=11),
            text_color="#22c55e", anchor="w", wraplength=560)
        self._action_result_lbl.pack(anchor="w", pady=(12, 0))

    def _section_support_bundle(self, parent):
        _outer, frame = _make_section_card(parent, "Support Bundle",
                                           "Export all logs and config to share with IT.")
        bundle_row = ctk.CTkFrame(frame, fg_color="transparent")
        bundle_row.pack(fill="x")
        self._bundle_btn = _make_action_button(bundle_row, "Export Support Bundle",
                                              self._export_bundle, tone="primary", width=200)
        self._bundle_btn.pack(side="left")
        self._bundle_path_lbl = ctk.CTkLabel(bundle_row, text="", font=ctk.CTkFont(size=11),
                                             text_color="#64748b", anchor="w")
        self._bundle_path_lbl.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(frame, text="Share this file with IT if you need help.",
                     font=ctk.CTkFont(size=11), text_color="#475569").pack(anchor="w", pady=(4, 0))

    # ── Refresh logic ────────────────────────────────────────────────────
    def _refresh_all(self):
        """Re-fetch all health data and update the UI."""
        threading.Thread(target=self._refresh_all_worker, daemon=True).start()

    def _refresh_all_worker(self):
        health = get_app_health()
        cfg    = get_config_health()
        browser = get_browser_health()
        crashes = get_crash_history()
        self.after(0, lambda h=health, c=cfg, b=browser, cr=crashes:
                   self._apply_health_results(h, c, b, cr))

    def _apply_health_results(self, health, cfg, browser, crashes):
        # App health
        for key in ["python_version", "platform", "app_version"]:
            if key in self._health_labels:
                val = str(health.get(key, "—"))
                self._health_labels[key].configure(text=val)

        # Runtime dir + writable
        writable = health.get("runtime_dir_writable", False)
        dir_text = f"{health.get('runtime_dir', '—')} {'(writable)' if writable else '(read-only)'}"
        self._health_labels["runtime_dir"].configure(
            text=dir_text,
            text_color="#22c55e" if writable else "#ef4444"
        )

        # Safe mode
        safe = health.get("safe_mode_active", False)
        self._health_labels["safe_mode_active"].configure(
            text="ON" if safe else "OFF",
            text_color="#f59e0b" if safe else "#22c55e"
        )

        # Crash markers
        markers = health.get("crash_markers_present", 0)
        self._health_labels["crash_markers"].configure(
            text=str(markers),
            text_color="#ef4444" if markers else "#475569"
        )

        # Config health
        for fname, check in cfg.get("files", {}).items():
            if fname in self._config_labels:
                exists  = check.get("exists", False)
                malformed = check.get("malformed", False)
                if malformed:
                    status = "Malformed — will be repaired"
                    color = "#f59e0b"
                elif exists:
                    status = f"OK — {check.get('last_modified', 'unknown date')}"
                    color = "#22c55e"
                else:
                    status = "Not found"
                    color = "#ef4444"
                self._config_labels[fname]["status"].configure(text=status, text_color=color)

        # Browser health
        if browser.get("found"):
            self._browser_status_lbl.configure(
                text="Bundled and ready", text_color="#22c55e")
            self._browser_path_lbl.configure(text=browser.get("path", ""))
        else:
            err = browser.get("error", "Not found")
            self._browser_status_lbl.configure(
                text=f"Missing — {err}", text_color="#ef4444")
            self._browser_path_lbl.configure(text="")

        # Crash history
        for w in self._crash_list_frame.winfo_children():
            w.destroy()
        self._crash_empty_lbl.pack_forget()
        if not crashes:
            self._crash_empty_lbl.pack(anchor="w")
        else:
            for entry in crashes:
                card = ctk.CTkFrame(self._crash_list_frame, fg_color="#1e293b", corner_radius=6)
                card.pack(fill="x", pady=3)
                ctk.CTkLabel(card, text=entry.get("reason", "Unknown"),
                             font=ctk.CTkFont(size=11), text_color="#f8fafc",
                             anchor="w").pack(side="left", padx=8, pady=6)
                ctk.CTkLabel(card, text=entry.get("entered_at", ""),
                             font=ctk.CTkFont(size=10), text_color="#475569",
                             anchor="e").pack(side="right", padx=8, pady=6)

    def _run_action(self, fn):
        """Run a recovery action and show the result."""
        self._action_result_lbl.configure(text="Running...", text_color="#64748b")
        threading.Thread(target=self._action_worker, args=(fn,), daemon=True).start()

    def _action_worker(self, fn):
        try:
            ok, msg = fn()
            color = "#22c55e" if ok else "#ef4444"
        except Exception as exc:
            ok, color, msg = False, "#ef4444", f"Error: {exc}"
        self.after(0, lambda: self._action_result_lbl.configure(text=msg, text_color=color))
        # Refresh health after config changes
        if fn in (reset_config_to_defaults, clear_toast_session, toggle_safe_mode):
            self.after(500, self._refresh_all)

    def _export_bundle(self):
        self._bundle_btn.configure(state="disabled", text="Exporting...")
        self._bundle_path_lbl.configure(text="")
        threading.Thread(target=self._export_worker, daemon=True).start()

    def _export_worker(self):
        try:
            ok, msg = export_support_bundle()
            color = "#22c55e" if ok else "#ef4444"
        except Exception as exc:
            ok, color, msg = False, "#ef4444", f"Error: {exc}"
        self.after(0, lambda: self._bundle_btn.configure(state="normal", text="Export Support Bundle"))
        self.after(0, lambda: self._bundle_path_lbl.configure(text=msg, text_color=color))
