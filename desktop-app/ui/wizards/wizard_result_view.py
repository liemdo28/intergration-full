"""
ToastPOSManager — Wizard Result View

Displays the outcome of a wizard action (download or QB sync).
"""

from __future__ import annotations

import logging
from typing import Optional, Callable

_log = logging.getLogger(__name__)

try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False

_SUCCESS_COLOR = "#22c55e"
_FAIL_COLOR    = "#ef4444"
_WARN_COLOR    = "#f59e0b"
_BG_CARD       = "#111827"
_BG_WARN       = "#451a03"
_MUTED         = "#94a3b8"

_OUTCOME_STYLES = {
    "completed": {
        "icon": "✓", "icon_color": "#22c55e", "header_bg": "#052e16",
        "title_prefix": "Completed", "border": "#22c55e",
    },
    "completed_with_warnings": {
        "icon": "⚠", "icon_color": "#f59e0b", "header_bg": "#2d1b00",
        "title_prefix": "Completed with Warnings", "border": "#f59e0b",
    },
    "blocked": {
        "icon": "✕", "icon_color": "#ef4444", "header_bg": "#1f0a0a",
        "title_prefix": "Blocked", "border": "#ef4444",
    },
    "failed_safely": {
        "icon": "!", "icon_color": "#f97316", "header_bg": "#2c1000",
        "title_prefix": "Failed Safely", "border": "#f97316",
    },
}


class WizardResultView(ctk.CTkFrame if CTK else object):
    """
    Result summary view shown in the final wizard step.

    Parameters
    ----------
    master : widget
        Parent container.
    success : bool
        Whether the action succeeded (used when outcome_type is not specified).
    outcome_type : str
        One of: "completed", "completed_with_warnings", "blocked", "failed_safely".
    title : str
        Short headline.
    summary_lines : list[str]
        Bullet-point summary items.
    warnings : list[str] | None
        Optional warnings shown in collapsible section.
    stats : list | None
        List of (label, value) tuples for stat pills.
    next_action_label : str
        Label for the primary "Next Action" button.
    next_action_command : Callable | None
        Callback for the next action button.
    secondary_action_label : str
        Label for an optional secondary action button.
    secondary_action_command : Callable | None
        Callback for the secondary button (navigates home if None).
    done_command : Callable | None
        Callback for the "Done" button.
    """

    def __init__(
        self,
        master,
        *,
        success: bool = True,
        outcome_type: str = "",
        title: str = "",
        summary_lines: list = None,
        warnings: list = None,
        stats: list = None,
        next_action_label: str = "",
        next_action_command: Optional[Callable] = None,
        secondary_action_label: str = "",
        secondary_action_command: Optional[Callable] = None,
        done_command: Optional[Callable] = None,
        **kwargs,
    ):
        if not CTK:
            kwargs = {}
        super().__init__(master, fg_color="transparent", **kwargs)
        self._done_command = done_command
        self._warnings_expanded = False
        # Derive outcome_type from success flag if not explicitly given
        if not outcome_type:
            outcome_type = "completed" if success else "failed_safely"
        self._build(
            outcome_type, title,
            summary_lines or [], warnings or [],
            next_action_label, next_action_command,
            secondary_action_label, secondary_action_command,
            stats or [],
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update(
        self,
        success: bool,
        title: str,
        summary_lines: list,
        warnings: list,
        next_action_label: str,
        next_action_command: Optional[Callable],
        stats: list = None,
        outcome_type: str = "",
        secondary_action_label: str = "",
        secondary_action_command: Optional[Callable] = None,
    ) -> None:
        """Rebuild the view with new data."""
        for child in self.winfo_children():
            child.destroy()
        self._warnings_expanded = False
        if not outcome_type:
            outcome_type = "completed" if success else "failed_safely"
        self._build(
            outcome_type, title, summary_lines, warnings,
            next_action_label, next_action_command,
            secondary_action_label, secondary_action_command,
            stats or [],
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build(
        self,
        outcome_type: str,
        title: str,
        summary_lines: list,
        warnings: list,
        next_action_label: str,
        next_action_command: Optional[Callable],
        secondary_action_label: str,
        secondary_action_command: Optional[Callable],
        stats: list = None,
    ) -> None:
        style = _OUTCOME_STYLES.get(outcome_type, _OUTCOME_STYLES["completed"])
        icon_text  = style["icon"]
        icon_color = style["icon_color"]
        header_bg  = style["header_bg"]
        border_col = style["border"]

        # Outer card with outcome-specific border
        card = ctk.CTkFrame(
            self,
            fg_color=_BG_CARD,
            corner_radius=16,
            border_width=1,
            border_color=border_col,
        )
        card.pack(fill="x", padx=20, pady=20)

        # Colored header banner
        header_banner = ctk.CTkFrame(card, fg_color=header_bg, corner_radius=0)
        header_banner.pack(fill="x")

        # Large icon (72px)
        icon_lbl = ctk.CTkLabel(
            header_banner,
            text=icon_text,
            font=ctk.CTkFont(size=72, weight="bold"),
            text_color=icon_color,
        )
        icon_lbl.pack(pady=(16, 4))

        # Title (22px bold)
        ctk.CTkLabel(
            header_banner,
            text=title,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#f8fafc",
            wraplength=500,
            justify="center",
        ).pack(pady=(0, 16))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=(16, 24))

        ctk.CTkFrame(inner, height=1, fg_color="#1e293b").pack(fill="x", pady=(0, 16))

        # Stats row — pill cards with key numbers
        if stats:
            stats_row = ctk.CTkFrame(inner, fg_color="transparent")
            stats_row.pack(fill="x", pady=(0, 16))
            success = outcome_type == "completed"
            for label, value in stats:
                pill = ctk.CTkFrame(
                    stats_row,
                    fg_color="#1e293b",
                    corner_radius=10,
                    border_width=1,
                    border_color="#334155",
                )
                pill.pack(side="left", padx=6, pady=4)
                pill_inner = ctk.CTkFrame(pill, fg_color="transparent")
                pill_inner.pack(padx=14, pady=8)
                ctk.CTkLabel(
                    pill_inner,
                    text=str(value),
                    font=ctk.CTkFont(size=20, weight="bold"),
                    text_color=icon_color,
                ).pack()
                ctk.CTkLabel(
                    pill_inner,
                    text=label,
                    font=ctk.CTkFont(size=11),
                    text_color=_MUTED,
                ).pack()

        # Summary bullets
        if summary_lines:
            summary_frame = ctk.CTkFrame(inner, fg_color="transparent")
            summary_frame.pack(fill="x", pady=(0, 12))
            for line in summary_lines:
                row = ctk.CTkFrame(summary_frame, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkLabel(
                    row,
                    text="•",
                    font=ctk.CTkFont(size=12),
                    text_color="#64748b",
                    width=18,
                ).pack(side="left")
                ctk.CTkLabel(
                    row,
                    text=line,
                    font=ctk.CTkFont(size=12),
                    text_color="#cbd5e1",
                    anchor="w",
                    justify="left",
                    wraplength=460,
                ).pack(side="left", fill="x")

        # Warnings (collapsible amber card)
        if warnings:
            warn_frame = ctk.CTkFrame(inner, fg_color=_BG_WARN, corner_radius=8)
            warn_frame.pack(fill="x", pady=(4, 12))

            toggle_text = f"⚠ {len(warnings)} warning(s) — tap to expand"
            self._warn_toggle_btn = ctk.CTkButton(
                warn_frame,
                text=toggle_text,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=_WARN_COLOR,
                fg_color="transparent",
                hover_color="#5c2a06",
                anchor="w",
                command=lambda: self._toggle_warnings(warn_frame, warnings),
            )
            self._warn_toggle_btn.pack(fill="x", padx=12, pady=8)

            self._warn_list_frame = ctk.CTkFrame(warn_frame, fg_color="transparent")
            # Not packed initially (collapsed)

        # Action buttons row
        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.pack(fill="x", pady=(8, 4))

        # Primary action button — large, full-width or side-by-side
        if next_action_label and next_action_command:
            ctk.CTkButton(
                btn_row,
                text=next_action_label,
                fg_color="#2563eb",
                hover_color="#1d4ed8",
                text_color="#ffffff",
                corner_radius=8,
                height=44,
                command=next_action_command,
            ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        # Secondary action button
        if secondary_action_label:
            sec_cmd = secondary_action_command if secondary_action_command else lambda: None
            ctk.CTkButton(
                btn_row,
                text=secondary_action_label,
                fg_color="#1e293b",
                hover_color="#334155",
                text_color="#f1f5f9",
                corner_radius=8,
                height=44,
                command=sec_cmd,
            ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Done button — neutral, below action row
        ctk.CTkButton(
            inner,
            text="Done",
            fg_color="#1e293b",
            hover_color="#334155",
            text_color="#f1f5f9",
            corner_radius=8,
            height=36,
            command=self._done_command if self._done_command else lambda: None,
        ).pack(fill="x", pady=(0, 4))

    def _toggle_warnings(self, warn_frame, warnings: list) -> None:
        self._warnings_expanded = not self._warnings_expanded
        if self._warnings_expanded:
            # Clear and rebuild warning list
            for child in self._warn_list_frame.winfo_children():
                child.destroy()
            for w in warnings:
                ctk.CTkLabel(
                    self._warn_list_frame,
                    text=f"  • {w}",
                    font=ctk.CTkFont(size=11),
                    text_color=_WARN_COLOR,
                    anchor="w",
                    justify="left",
                    wraplength=440,
                ).pack(anchor="w", padx=12, pady=1)
            self._warn_list_frame.pack(fill="x", padx=0, pady=(0, 8))
        else:
            self._warn_list_frame.pack_forget()
