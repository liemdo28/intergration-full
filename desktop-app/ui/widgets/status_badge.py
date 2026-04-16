"""
ToastPOSManager — Status Badge Widget

Reusable status indicator with 4 states:
  ready   → green
  warning → yellow/amber
  blocked → red
  unknown → gray

Provides consistent visual language across the entire app.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False


class Status(str, Enum):
    READY = "ready"
    WARNING = "warning"
    PARTIAL = "partial"   # same visual as warning
    BLOCKED = "blocked"
    UNKNOWN = "unknown"

    @property
    def color(self) -> str:
        return {
            Status.READY:    "#22c55e",   # green-500
            Status.WARNING:  "#f59e0b",   # amber-500
            Status.PARTIAL:   "#f59e0b",
            Status.BLOCKED:  "#ef4444",   # red-500
            Status.UNKNOWN:  "#6b7280",   # gray-500
        }[self]

    @property
    def bg_color(self) -> str:
        return {
            Status.READY:    "#052e16",   # green-950 bg
            Status.WARNING:  "#451a03",   # amber-950 bg
            Status.PARTIAL:   "#451a03",
            Status.BLOCKED:  "#450a0a",   # red-950 bg
            Status.UNKNOWN:  "#1f2937",   # gray-800 bg
        }[self]

    @property
    def label(self) -> str:
        return {
            Status.READY:    "Ready",
            Status.WARNING:  "Warning",
            Status.PARTIAL:   "Partial",
            Status.BLOCKED:  "Blocked",
            Status.UNKNOWN:  "Unknown",
        }[self]

    @property
    def icon(self) -> str:
        return {
            Status.READY:    "●",
            Status.WARNING:  "●",
            Status.PARTIAL:   "●",
            Status.BLOCKED:  "●",
            Status.UNKNOWN:  "○",
        }[self]


def status_from_bool(ok: bool, missing_reason: str | None = None) -> Status:
    """Convenience: Status.READY if ok else BLOCKED."""
    return Status.READY if ok else Status.BLOCKED


class StatusBadge(ctk.CTkFrame if CTK else object):
    """
    A compact badge showing a status icon + label.

    Usage:
        badge = StatusBadge(parent, status=Status.READY)
        badge.pack()
        badge.set(Status.WARNING, "QB not configured")
    """

    def __init__(
        self,
        master,
        status: Status = Status.UNKNOWN,
        text: str | None = None,
        **kwargs,
    ):
        # Extract status-specific colors from kwargs if present
        fg_color = kwargs.pop("fg_color", status.bg_color)
        super().__init__(master, fg_color=fg_color, corner_radius=6, **kwargs)

        self._status = status
        self._icon = ctk.CTkLabel(self, text=status.icon, text_color=status.color, font=ctk.CTkFont(size=12))
        self._icon.pack(side="left", padx=(8, 4), pady=4)
        self._label = ctk.CTkLabel(
            self, text=text or status.label,
            text_color=status.color, font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._label.pack(side="left", padx=(0, 8), pady=4)

    def set(self, status: Status, text: str | None = None) -> None:
        """Update the badge to a new status."""
        self._status = status
        self.configure(fg_color=status.bg_color)
        self._icon.configure(text=status.icon, text_color=status.color)
        self._label.configure(text=text or status.label, text_color=status.color)

    def get_status(self) -> Status:
        return self._status


# ---------------------------------------------------------------------------
# Convenience factory for non-CTK contexts (e.g. tests)
# ---------------------------------------------------------------------------
class PlainStatusBadge:
    """Fallback when customtkinter is not available."""

    def __init__(self, status: Status = Status.UNKNOWN, text: str | None = None):
        self._status = status
        self._text = text or status.label

    def set(self, status: Status, text: str | None = None) -> None:
        self._status = status
        self._text = text or status.label

    def get_status(self) -> Status:
        return self._status

    def __repr__(self) -> str:
        return f"StatusBadge({self._status.value}, {self._text!r})"
