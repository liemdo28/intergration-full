"""
ToastPOSManager — Home Dashboard Tab

The default first tab. Replaces or complements the old QB tab.

Sections
-------
  1. Hero Welcome        — greeting + app version + safe-mode indicator
  2. Today's Readiness  — 2×2 grid of StatusBadges for the 4 core features
  3. Quick Actions      — ActionCard row (Download / QB Sync / Recovery)
  4. Recommended Next Step — RecommendedNextStep widget
  5. Safe Mode Banner   — amber bar when safe mode is active

Navigation via status_var
-------------------------
Caller sets status_var to trigger tab switches:
  "navigate:download"  → Download Reports tab
  "navigate:qb"        → QB Sync tab
  "navigate:settings"  → Settings / Recovery tab

Usage:
    from ui.home_dashboard import HomeDashboard

    dashboard = HomeDashboard(tab_parent, status_var=nav_var)
    dashboard.pack(fill="both", expand=True)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# CTK import with graceful fallback
# ---------------------------------------------------------------------------
try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False
    object = object  # cosmetic shim so class bases don't need extra guards

# ---------------------------------------------------------------------------
# Internal imports
# ---------------------------------------------------------------------------
from ui.widgets.status_badge import StatusBadge, Status as BadgeStatus
from ui.widgets.action_card import ActionCard, ActionCardRow
from ui.widgets.recommended_next_step import RecommendedNextStep

from models.feature_readiness import (
    FeatureKey,
    FeatureReadiness,
    ReadinessStatus,
)
from services.feature_readiness_service import check_all_features, get_most_urgent

from safe_mode import is_safe_mode, get_safe_mode_config

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers (same pattern as app_paths / safe_mode)
# ---------------------------------------------------------------------------

def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


BUNDLE_DIR = _resolve_bundle_dir()
RUNTIME_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else BUNDLE_DIR
)


def _runtime_path(*parts: str) -> Path:
    return RUNTIME_DIR.joinpath(*parts)


# ---------------------------------------------------------------------------
# Version loader
# ---------------------------------------------------------------------------

def _load_version() -> str:
    """
    Read app version from version.json if present,
    falling back to "v2.2" when unavailable or on first-run.
    """
    try:
        vpath = _runtime_path("version.json")
        if vpath.exists():
            data = json.loads(vpath.read_text(encoding="utf-8", errors="replace"))
            return f"v{data.get('app_version', '2.2')}"
    except Exception:
        pass
    return "v2.2"


# ---------------------------------------------------------------------------
# Greeting helper
# ---------------------------------------------------------------------------

def _greeting() -> str:
    """Return a time-appropriate greeting."""
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _operator_name() -> str:
    """Return the configured operator name, or 'Operator'."""
    try:
        cfg_path = _runtime_path("local-config.json")
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text(encoding="utf-8", errors="replace"))
            name = data.get("operator_name", "").strip()
            if name:
                return name
    except Exception:
        pass
    return "Operator"


# ---------------------------------------------------------------------------
# ReadinessStatus → BadgeStatus helper
# ---------------------------------------------------------------------------

_STATUS_TO_BADGE: dict[ReadinessStatus, BadgeStatus] = {
    ReadinessStatus.READY:   BadgeStatus.READY,
    ReadinessStatus.PARTIAL:  BadgeStatus.PARTIAL,
    ReadinessStatus.WARNING:  BadgeStatus.WARNING,
    ReadinessStatus.BLOCKED: BadgeStatus.BLOCKED,
    ReadinessStatus.UNKNOWN: BadgeStatus.UNKNOWN,
}


def _status_to_badge(status: ReadinessStatus) -> BadgeStatus:
    return _STATUS_TO_BADGE.get(status, BadgeStatus.UNKNOWN)


# ---------------------------------------------------------------------------
# Make section card helper (mirrors app.py make_section_card)
# ---------------------------------------------------------------------------

_UI_CARD_FG     = "#111827"
_UI_CARD_BORDER  = "#1e293b"
_UI_MUTED_TEXT   = "#94a3b8"


def _make_section_card(parent, title, subtitle: Optional[str] = None):
    """Create a standard dark section card frame with header and body."""
    card = ctk.CTkFrame(
        parent,
        fg_color=_UI_CARD_FG,
        corner_radius=18,
        border_width=1,
        border_color=_UI_CARD_BORDER,
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
            text_color=_UI_MUTED_TEXT,
            justify="left",
            wraplength=900,
        ).pack(anchor="w", pady=(3, 0))

    body = ctk.CTkFrame(card, fg_color="transparent")
    body.pack(fill="x", padx=16, pady=(0, 16))
    return card, body


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------

class HomeDashboard(ctk.CTkFrame if CTK else object):
    """
    Drop-in tab content for the Home Dashboard.

    Parameters
    ----------
    master : CTkFrame or tkinter.Widget
        Parent container.
    status_var : tk.StringVar, optional
        When set, the dashboard writes navigation tokens to it so the
        parent tab controller can switch tabs.  Values:
          - "navigate:download"
          - "navigate:qb"
          - "navigate:settings"
    **kwargs
        Passed through to ctk.CTkFrame.
    """

    # Features shown in the Today's Readiness grid
    _READINESS_GRID_KEYS = [
        FeatureKey.REPORT_DOWNLOAD,
        FeatureKey.QB_SYNC,
        FeatureKey.REMOVE_TX,
        FeatureKey.GOOGLE_DRIVE,
    ]

    # Feature key → human-readable label
    _FEATURE_LABELS: dict[FeatureKey, str] = {
        FeatureKey.REPORT_DOWNLOAD: "Download Reports",
        FeatureKey.QB_SYNC:         "QB Sync",
        FeatureKey.REMOVE_TX:        "Remove Transactions",
        FeatureKey.GOOGLE_DRIVE:    "Drive Upload",
    }

    def __init__(
        self,
        master,
        *,
        status_var=None,
        **kwargs,
    ):
        if not CTK:
            kwargs = {}

        super().__init__(master, fg_color="transparent", **kwargs)

        self._status_var = status_var
        self._version = _load_version()
        self._operator = _operator_name()

        # Writable copy of readiness so we can refresh without re-calling
        self._readiness_cache: dict[FeatureKey, FeatureReadiness] = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read feature readiness and update all dynamic widgets."""
        self._readiness_cache = check_all_features()
        self._update_readiness_grid()
        self._update_recommended_step()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble all sections of the dashboard."""
        # ── Scroll container ─────────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
        )
        scroll.pack(fill="both", expand=True)
        self._configure_scrollbar(scroll)

        parent = scroll  # all sections are children of the scroll frame

        # ── 1. Hero welcome ───────────────────────────────────────────────
        self._hero(parent)

        # ── 2. Today's Readiness ─────────────────────────────────────────
        self._readiness_section(parent)

        # ── 3. Quick Actions ─────────────────────────────────────────────
        self._quick_actions_section(parent)

        # ── 4. Recommended Next Step ────────────────────────────────────
        self._recommended_section(parent)

        # ── 5. Safe Mode Banner ──────────────────────────────────────────
        if is_safe_mode():
            self._safe_mode_banner(parent)

    def _configure_scrollbar(self, scroll) -> None:
        """Style the scroll frame's scrollbar to match the dark theme."""
        try:
            scroll._scrollbar.configure(
                button_color="#334155",
                button_hover_color="#475569",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _hero(self, parent) -> None:
        """Build the hero welcome card."""
        hero = ctk.CTkFrame(
            parent,
            fg_color="#1e293b",
            corner_radius=12,
        )
        hero.pack(fill="x", padx=15, pady=(12, 4))

        # Left: greeting + subtitle
        left = ctk.CTkFrame(hero, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=20, pady=18)

        greeting_text = f"{_greeting()}, {self._operator}"
        ctk.CTkLabel(
            left,
            text=greeting_text,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#f8fafc",
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            left,
            text="ToastPOSManager is running.",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        # Right: version + safe-mode badge
        right = ctk.CTkFrame(hero, fg_color="transparent")
        right.pack(side="right", padx=20, pady=18, anchor="e")

        ctk.CTkLabel(
            right,
            text=self._version,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#64748b",
            anchor="e",
        ).pack(anchor="e")

        if is_safe_mode():
            cfg = get_safe_mode_config()
            ctk.CTkLabel(
                right,
                text=f"Safe Mode: {cfg.reason or 'Active'}",
                font=ctk.CTkFont(size=11),
                text_color="#f59e0b",
                anchor="e",
            ).pack(anchor="e", pady=(4, 0))

    def _readiness_section(self, parent) -> None:
        """Build Today's Readiness: a 2×2 grid of StatusBadges."""
        card, body = _make_section_card(parent, "Today's Readiness")

        # Refresh readiness on first build
        self._readiness_cache = check_all_features()

        # Grid: 2 columns × 2 rows
        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(fill="x", pady=(0, 4))
        grid.columnconfigure(0, weight=1, uniform="half")
        grid.columnconfigure(1, weight=1, uniform="half")

        keys = self._READINESS_GRID_KEYS
        for idx, key in enumerate(keys):
            row = idx // 2
            col = idx % 2
            fr = self._readiness_cache.get(key)
            self._place_badge(grid, key, fr, row, col)

    def _place_badge(
        self,
        grid,
        key: FeatureKey,
        fr: Optional[FeatureReadiness],
        row: int,
        col: int,
    ) -> None:
        """Place a single readiness badge in the grid."""
        status = fr.status if fr else ReadinessStatus.UNKNOWN
        badge_status = _status_to_badge(status)
        label_text = fr.reason if fr else "Status unknown."

        badge = StatusBadge(
            grid,
            status=badge_status,
            text=self._FEATURE_LABELS.get(key, key.value),
        )

        # Store ref for later refresh
        attr_name = f"_badge_{key.value}"
        setattr(self, attr_name, badge)

        badge.grid(row=row, column=col, padx=6, pady=5, sticky="ew")

    def _update_readiness_grid(self) -> None:
        """Refresh each badge in the readiness grid."""
        for key in self._READINESS_GRID_KEYS:
            fr = self._readiness_cache.get(key)
            badge: StatusBadge = getattr(self, f"_badge_{key.value}", None)
            if badge is None:
                continue
            status = fr.status if fr else ReadinessStatus.UNKNOWN
            badge_status = _status_to_badge(status)
            reason_text = fr.reason if fr else "Status unknown."
            badge.set(badge_status, self._FEATURE_LABELS.get(key, key.value))

    def _quick_actions_section(self, parent) -> None:
        """Build the Quick Actions section with three ActionCards."""
        card, body = _make_section_card(parent, "Quick Actions")

        def _nav(target: str):
            if self._status_var is not None:
                self._status_var.set(target)

        cards = [
            ActionCard(
                body,
                title="Download Reports",
                description="Download missing Toast reports for selected stores",
                accent="#22c55e",
                icon="▼",
                command=lambda: _nav("navigate:download"),
            ),
            ActionCard(
                body,
                title="Run QB Sync",
                description="Sync sales receipts to QuickBooks Desktop for selected stores",
                accent="#0f766e",
                icon="⬆",
                command=lambda: _nav("navigate:qb"),
            ),
            ActionCard(
                body,
                title="Recovery Center",
                description="Troubleshoot issues, export support bundles, repair settings",
                accent="#475569",
                icon="⚙",
                command=lambda: _nav("navigate:settings"),
            ),
        ]

        ActionCardRow(body, cards)

    def _recommended_section(self, parent) -> None:
        """Build the Recommended Next Step section."""
        card, body = _make_section_card(parent, "Recommended Next Step")

        self._recommended_step = RecommendedNextStep(body)
        self._recommended_step.pack(fill="x", padx=(0, 0), pady=(0, 4))
        self._update_recommended_step()

    def _update_recommended_step(self) -> None:
        """Refresh the RecommendedNextStep widget."""
        fr = get_most_urgent()
        if hasattr(self, "_recommended_step"):
            self._recommended_step.update(fr)

    def _safe_mode_banner(self, parent) -> None:
        """Draw the amber safe-mode banner across the bottom."""
        banner = ctk.CTkFrame(
            parent,
            fg_color="#451a03",
            corner_radius=0,
            border_width=1,
            border_color="#b45309",
        )
        banner.pack(fill="x", padx=0, pady=(0, 0))

        label = ctk.CTkLabel(
            banner,
            text="⚠  Safe Mode Active — Background workers are disabled.",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f59e0b",
        )
        label.pack(padx=16, pady=10)