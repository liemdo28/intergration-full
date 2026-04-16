"""
ToastPOSManager — Recent Activity List Widget

Displays the N most recent activity events in a compact dark-themed list.
"""

from __future__ import annotations

import logging
from datetime import datetime

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CTK import with graceful fallback
# ---------------------------------------------------------------------------
try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False


# ---------------------------------------------------------------------------
# Color / icon mappings
# ---------------------------------------------------------------------------

_SEVERITY_COLORS = {
    "info":     "#3b82f6",   # blue
    "warning":  "#f59e0b",   # amber
    "error":    "#ef4444",   # red
    "critical": "#7f1d1d",   # dark red
}

_CATEGORY_ICONS = {
    "download":      "↓",
    "qb_sync":       "⚙",
    "remove_tx":     "✕",
    "drive_upload":  "↑",
    "crash":         "!",
}
_DEFAULT_ICON = "•"


def _category_icon(category_value: str) -> str:
    return _CATEGORY_ICONS.get(category_value, _DEFAULT_ICON)


def _severity_color(severity_value: str) -> str:
    return _SEVERITY_COLORS.get(severity_value, "#3b82f6")


def _fmt_ts(ts: str) -> str:
    """Format ISO timestamp as 'Apr 16  14:32'."""
    try:
        dt = datetime.fromisoformat(ts.rstrip("Z"))
        return dt.strftime("%b %d  %H:%M")
    except Exception:
        return ts[:16] if len(ts) >= 16 else ts


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class RecentActivityList(ctk.CTkFrame if CTK else object):
    """
    Compact list of recent activity events.

    Parameters
    ----------
    master : widget
        Parent container.
    count : int
        Number of events to fetch and display.
    """

    def __init__(self, master, *, count: int = 5, **kwargs):
        if not CTK:
            kwargs = {}
        super().__init__(master, fg_color="transparent", **kwargs)
        self._count = count
        self._rows: list = []
        self.refresh()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Fetch events and rebuild the list."""
        events = []
        try:
            from services.activity_log_service import get_recent_activity
            events = get_recent_activity(count=self._count)
        except Exception as exc:
            _log.warning("RecentActivityList: could not fetch events: %s", exc)

        self._render(events)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _render(self, events: list) -> None:
        """Clear and rebuild the event rows."""
        for child in self.winfo_children():
            child.destroy()
        self._rows.clear()

        if not events:
            if CTK:
                ctk.CTkLabel(
                    self,
                    text="No recent activity.",
                    font=ctk.CTkFont(size=12),
                    text_color="#64748b",
                    anchor="w",
                ).pack(anchor="w", pady=6)
            return

        for event in events:
            self._render_row(event)

    def _render_row(self, event) -> None:
        """Render a single event row."""
        severity_val = getattr(event.severity, "value", str(event.severity))
        category_val = getattr(event.category, "value", str(event.category))
        color = _severity_color(severity_val)
        icon = _category_icon(category_val)
        store = event.store or ""
        title = event.title or ""
        detail = (event.detail or "")
        if len(detail) > 80:
            detail = detail[:77] + "..."
        ts = _fmt_ts(event.timestamp)

        row = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=8)
        row.pack(fill="x", pady=2)

        # Icon bubble
        bubble = ctk.CTkFrame(row, width=30, height=30, corner_radius=8, fg_color=color)
        bubble.pack(side="left", padx=(8, 10), pady=6)
        bubble.pack_propagate(False)
        ctk.CTkLabel(
            bubble,
            text=icon,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#ffffff",
        ).place(relx=0.5, rely=0.5, anchor="center")

        # Main text column
        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True, pady=4)

        # Title + store
        header_text = title
        if store:
            header_text = f"{title}  [{store}]"
        ctk.CTkLabel(
            text_col,
            text=header_text,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f1f5f9",
            anchor="w",
            justify="left",
        ).pack(anchor="w")

        if detail:
            ctk.CTkLabel(
                text_col,
                text=detail,
                font=ctk.CTkFont(size=11),
                text_color="#94a3b8",
                anchor="w",
                justify="left",
            ).pack(anchor="w", pady=(1, 0))

        # Timestamp (right side)
        ctk.CTkLabel(
            row,
            text=ts,
            font=ctk.CTkFont(size=10),
            text_color="#475569",
            anchor="e",
        ).pack(side="right", padx=(6, 10), pady=6)

        self._rows.append(row)


# ---------------------------------------------------------------------------
# Headless fallback
# ---------------------------------------------------------------------------

class PlainRecentActivityList:
    """Headless fallback for environments without CustomTkinter."""

    def __init__(self, master=None, *, count: int = 5, **kwargs):
        self._count = count

    def refresh(self) -> None:
        """No-op in headless mode."""
        pass
