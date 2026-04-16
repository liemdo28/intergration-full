"""
ToastPOSManager — Wizard Base

Provides a reusable multi-step wizard frame with:
  - Progress indicator (numbered circles + connecting lines)
  - Scrollable content area
  - Back / Next / Cancel footer buttons
"""

from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)

try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------
_BG             = "transparent"
_STEP_FUTURE    = "#334155"
_STEP_CURRENT   = "#3b82f6"
_STEP_DONE      = "#22c55e"
_LINE_COLOR     = "#334155"
_BTN_CANCEL_BG  = "#1e293b"
_BTN_BACK_BG    = "#1e293b"
_BTN_NEXT_BG    = "#2563eb"
_BTN_FINISH_BG  = "#22c55e"
_TEXT_MUTED     = "#94a3b8"
_TEXT_LABEL     = "#f1f5f9"


class WizardBase(ctk.CTkFrame if CTK else object):
    """
    Multi-step wizard base frame.

    Parameters
    ----------
    master : widget
        Parent container.
    title : str
        Wizard title shown at the top.
    steps : list[str]
        Labels for each step (length determines total step count).
    status_var : tk.StringVar | None
        When Cancel is pressed, sets ``status_var`` to ``"navigate:home"``.
    **kwargs
        Forwarded to ctk.CTkFrame.
    """

    def __init__(
        self,
        master,
        *,
        title: str,
        steps: list,
        status_var=None,
        **kwargs,
    ):
        if not CTK:
            kwargs = {}
        super().__init__(master, fg_color=_BG, **kwargs)

        self._title = title
        self._steps = steps
        self._status_var = status_var
        self._step_index = 0
        self._next_enabled = True

        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def go_next(self) -> None:
        """Advance to the next step."""
        if not self._next_enabled:
            return
        if self._step_index < len(self._steps) - 1:
            self._step_index += 1
            self._refresh()
            self.on_step_changed(self._step_index)

    def go_back(self) -> None:
        """Go back to the previous step."""
        if self._step_index > 0:
            self._step_index -= 1
            self._refresh()
            self.on_step_changed(self._step_index)

    def cancel(self) -> None:
        """Cancel the wizard and navigate home (or reset to step 0)."""
        if self._status_var is not None:
            self._status_var.set("navigate:home")
        else:
            self._step_index = 0
            self._refresh()
            self.on_step_changed(0)

    def set_next_enabled(self, enabled: bool) -> None:
        """Allow or block the Next button."""
        self._next_enabled = enabled
        self._update_footer_buttons()

    def on_step_changed(self, step_index: int) -> None:
        """Override in subclass to render step content."""
        pass

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Title bar
        title_bar = ctk.CTkFrame(self, fg_color="#1e293b", corner_radius=0)
        title_bar.pack(fill="x", pady=(0, 0))

        ctk.CTkLabel(
            title_bar,
            text=self._title,
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#f8fafc",
            anchor="w",
        ).pack(side="left", padx=20, pady=14)

        # Progress bar area
        self._progress_outer = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=0)
        self._progress_outer.pack(fill="x")

        self._progress_row = ctk.CTkFrame(self._progress_outer, fg_color="transparent")
        self._progress_row.pack(pady=14)

        # Content
        self._content_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
        )
        self._content_frame.pack(fill="both", expand=True, padx=0, pady=0)

        # Footer
        footer = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=0)
        footer.pack(fill="x", side="bottom")

        btn_row = ctk.CTkFrame(footer, fg_color="transparent")
        btn_row.pack(pady=10, padx=16, anchor="e", side="right")

        self._btn_cancel = ctk.CTkButton(
            btn_row,
            text="✕ Cancel",
            width=100,
            fg_color=_BTN_CANCEL_BG,
            hover_color="#334155",
            text_color="#94a3b8",
            corner_radius=8,
            command=self.cancel,
        )
        self._btn_cancel.pack(side="left", padx=4)

        self._btn_back = ctk.CTkButton(
            btn_row,
            text="← Back",
            width=100,
            fg_color=_BTN_BACK_BG,
            hover_color="#334155",
            text_color="#f1f5f9",
            corner_radius=8,
            command=self.go_back,
        )
        self._btn_back.pack(side="left", padx=4)

        self._btn_next = ctk.CTkButton(
            btn_row,
            text="Next →",
            width=110,
            fg_color=_BTN_NEXT_BG,
            hover_color="#1d4ed8",
            text_color="#ffffff",
            corner_radius=8,
            command=self.go_next,
        )
        self._btn_next.pack(side="left", padx=4)

        self._render_progress(self._step_index)
        self._update_footer_buttons()
        # Render initial step
        self.after(50, lambda: self.on_step_changed(0))

    # ------------------------------------------------------------------
    # Progress indicator
    # ------------------------------------------------------------------

    def _render_progress(self, current: int) -> None:
        """Rebuild the numbered-circle progress row."""
        for child in self._progress_row.winfo_children():
            child.destroy()

        for i, label in enumerate(self._steps):
            if i > 0:
                # Connecting line
                line = ctk.CTkFrame(
                    self._progress_row,
                    width=36,
                    height=2,
                    fg_color=_STEP_DONE if i <= current else _LINE_COLOR,
                    corner_radius=0,
                )
                line.pack(side="left", padx=0)

            # Step column (circle + label)
            col = ctk.CTkFrame(self._progress_row, fg_color="transparent")
            col.pack(side="left")

            if i < current:
                bg = _STEP_DONE
                txt = "✓"
                txt_color = "#ffffff"
            elif i == current:
                bg = _STEP_CURRENT
                txt = str(i + 1)
                txt_color = "#ffffff"
            else:
                bg = _STEP_FUTURE
                txt = str(i + 1)
                txt_color = "#94a3b8"

            circle = ctk.CTkFrame(col, width=30, height=30, corner_radius=15, fg_color=bg)
            circle.pack()
            circle.pack_propagate(False)
            ctk.CTkLabel(
                circle,
                text=txt,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=txt_color,
            ).place(relx=0.5, rely=0.5, anchor="center")

            ctk.CTkLabel(
                col,
                text=label,
                font=ctk.CTkFont(size=10),
                text_color="#f1f5f9" if i == current else _TEXT_MUTED,
            ).pack(pady=(2, 0))

    # ------------------------------------------------------------------
    # Footer state
    # ------------------------------------------------------------------

    def _update_footer_buttons(self) -> None:
        is_last = self._step_index == len(self._steps) - 1

        # Back
        self._btn_back.configure(
            state="normal" if self._step_index > 0 else "disabled",
            fg_color=_BTN_BACK_BG if self._step_index > 0 else "#0f172a",
            text_color="#f1f5f9" if self._step_index > 0 else "#475569",
        )

        # Next / Finish
        next_text = "Finish" if is_last else "Next →"
        next_bg = _BTN_FINISH_BG if is_last else _BTN_NEXT_BG
        self._btn_next.configure(
            text=next_text,
            fg_color=next_bg if self._next_enabled else "#1e293b",
            text_color="#ffffff" if self._next_enabled else "#475569",
            state="normal" if self._next_enabled else "disabled",
        )

    def _refresh(self) -> None:
        """Re-render the progress bar and update footer buttons."""
        self._render_progress(self._step_index)
        self._update_footer_buttons()
        # Reset next-enabled to True for newly entered steps (subclass can override)
        self._next_enabled = True
