"""
ToastPOSManager — Action Card Widget

A large, clickable card for the Home Dashboard's Quick Actions section.
Each card has a title, optional description, accent color, and icon.

Usage:
    card = ActionCard(
        parent,
        title="Download Reports",
        description="Download missing Toast reports for selected stores",
        accent="#22c55e",
        icon="▼",
        command=self._on_download,
    )
    card.pack(pady=4)
"""

from __future__ import annotations

try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False


class ActionCard(ctk.CTkFrame if CTK else object):
    """Clickable action card with title, description, and icon."""

    DEFAULT_ACCENT = "#3b82f6"   # blue-500

    def __init__(
        self,
        master,
        *,
        title: str,
        description: str = "",
        accent: str | None = None,
        icon: str = "▶",
        command=None,
        enabled: bool = True,
        **kwargs,
    ):
        accent = accent or self.DEFAULT_ACCENT
        self._bg_normal = "#1e293b"
        self._bg_hover  = self._lighten(accent, 0.15) if CTK else "#cccccc"

        # CTkFrame does not support hover_color — implement hover via bindings
        super().__init__(
            master,
            fg_color=self._bg_normal,
            corner_radius=8,
            cursor="hand2" if enabled else "arrow",
            **kwargs,
        )

        self._command = command
        self._accent = accent

        # Left accent bar
        self._accent_bar = ctk.CTkFrame(self, width=4, fg_color=accent, corner_radius=2)
        self._accent_bar.pack(side="left", fill="y", padx=(8, 6), pady=8)

        # Icon
        self._icon_lbl = ctk.CTkLabel(
            self, text=icon, text_color=accent,
            font=ctk.CTkFont(size=20),
        )
        self._icon_lbl.pack(side="left", pady=8)

        # Text column
        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.pack(side="left", fill="both", expand=True, pady=8, padx=(0, 12))
        self._title_lbl = ctk.CTkLabel(
            text_frame, text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#f1f5f9", anchor="w",
        )
        self._title_lbl.pack(anchor="w")
        if description:
            self._desc_lbl = ctk.CTkLabel(
                text_frame, text=description,
                font=ctk.CTkFont(size=11),
                text_color="#64748b", anchor="w", wraplength=320,
            )
            self._desc_lbl.pack(anchor="w", pady=(2, 0))

        # Right arrow
        self._arrow_lbl = ctk.CTkLabel(
            self, text="→", text_color="#475569",
            font=ctk.CTkFont(size=16),
        )
        self._arrow_lbl.pack(side="right", padx=12)

        if enabled:
            self.bind("<Button-1>", self._on_click)
            self.bind("<Enter>", self._on_hover_enter)
            self.bind("<Leave>", self._on_hover_leave)
            for child in self.winfo_children():
                child.bind("<Button-1>", self._on_click)
                child.bind("<Enter>", self._on_hover_enter)
                child.bind("<Leave>", self._on_hover_leave)
        else:
            self._title_lbl.configure(text_color="#475569")
            if description:
                self._desc_lbl.configure(text_color="#334155")

    def _on_click(self, event=None) -> None:
        if self._command:
            self._command()

    def _on_hover_enter(self, event=None) -> None:
        self.configure(fg_color=self._bg_hover)

    def _on_hover_leave(self, event=None) -> None:
        self.configure(fg_color=self._bg_normal)

    def configure_command(self, command) -> None:
        self._command = command
        if command:
            self.bind("<Button-1>", self._on_click)

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.configure(cursor="hand2")
            self._title_lbl.configure(text_color="#f1f5f9")
        else:
            self.configure(cursor="arrow")
            self._title_lbl.configure(text_color="#475569")

    @staticmethod
    def _lighten(hex_color: str, amount: float) -> str:
        """Lighten a hex color by blending toward white."""
        try:
            r = int(hex_color[1:3], 16)
            g = int(hex_color[3:5], 16)
            b = int(hex_color[5:7], 16)
            r = min(255, int(r + (255 - r) * amount))
            g = min(255, int(g + (255 - g) * amount))
            b = min(255, int(b + (255 - b) * amount))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color


class ActionCardRow(ctk.CTkFrame if CTK else object):
    """A row of ActionCards that wraps automatically."""

    def __init__(self, master, cards: list[ActionCard], **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        for card in cards:
            card.pack(in_=self, fill="x", pady=4)
