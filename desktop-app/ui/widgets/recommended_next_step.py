"""
ToastPOSManager — Recommended Next Step Widget

Displays the most urgent non-ready feature with a plain-English
reason and a clear next step, or an "All Clear" success card
when everything is configured.

Usage:
    from ui.widgets.recommended_next_step import RecommendedNextStep
    from services.feature_readiness_service import get_most_urgent

    widget = RecommendedNextStep(parent, feature_readiness=get_most_urgent())
    widget.pack(padx=15, pady=7, fill="x")

    # Refresh:
    widget.update(get_most_urgent())
"""

from __future__ import annotations

from typing import Optional

try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False
    object = object  # cosmetic; already available in Python 3

from models.feature_readiness import FeatureReadiness


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
CARD_BG        = "#1e293b"   # dark slate card background
ACCENT_AMBER   = "#f59e0b"   # amber-500 — warning/next-step accent
ACCENT_GREEN   = "#22c55e"   # green-500 — all-clear accent
TEXT_BRIGHT    = "#f8fafc"   # slate-50
TEXT_MUTED     = "#94a3b8"   # slate-400
TEXT_NEXT_STEP = "#22c55e"   # green-500 — next-step text


class RecommendedNextStep(ctk.CTkFrame if CTK else object):
    """
    A compact card showing the recommended next step for the operator.

    States
    ------
    urgent  — amber accent bar + "! " icon + reason + next step
    all-clear — green accent bar + "✓ " icon + title + body
    """

    # ---- public API -------------------------------------------------------

    def __init__(
        self,
        master,
        *,
        feature_readiness: Optional[FeatureReadiness] = None,
        **kwargs,
    ):
        # Strip CTk-only kwargs when CTK is False so the non-CTk object
        # base-class doesn't complain about unknown parameters.
        if not CTK:
            kwargs = {}

        super().__init__(master, fg_color=CARD_BG, corner_radius=8, **kwargs)

        self._feature_readiness = feature_readiness

        if feature_readiness is not None:
            self._show_urgent(feature_readiness)
        else:
            self._show_all_clear()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update(self, feature_readiness: Optional[FeatureReadiness]) -> None:
        """
        Refresh the card with a new (or None) FeatureReadiness object.

        Passing None redraws the all-clear state.
        """
        self._feature_readiness = feature_readiness
        if feature_readiness is None:
            self._show_all_clear()
        else:
            self._show_urgent(feature_readiness)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clear_content(self) -> None:
        """Remove all child widgets so the card can be redrawn."""
        for widget in self.winfo_children() if CTK else []:
            widget.destroy()

    def _show_all_clear(self) -> None:
        """Draw the success / all-clear variant of the card."""
        self._clear_content()
        self._layout(
            accent=ACCENT_GREEN,
            icon="✓",
            icon_color=ACCENT_GREEN,
            title="All Clear",
            reason="",
            next_step="All features are configured and ready.",
        )

    def _show_urgent(self, fr: FeatureReadiness) -> None:
        """Draw the urgent / next-step variant of the card."""
        self._clear_content()
        self._layout(
            accent=ACCENT_AMBER,
            icon="!",
            icon_color=ACCENT_AMBER,
            title="Recommended Next Step",
            reason=fr.reason,
            next_step=fr.next_step,
        )

    def _layout(
        self,
        accent: str,
        icon: str,
        icon_color: str,
        title: str,
        reason: str,
        next_step: str,
    ) -> None:
        """
        Build the card layout once the content is known.

        Structure
        ---------
        ┌──────────────────────────────────────────────┐
        │ ▌ [icon]  Title text (bold 14px)             │
        │        Reason text (11px muted)               │
        │        Next step text (11px green)            │
        └──────────────────────────────────────────────┘
          ^-- 4px left accent bar
        """
        # ── Left accent bar ────────────────────────────────────────────
        accent_bar = ctk.CTkFrame(
            self,
            width=4,
            fg_color=accent,
            corner_radius=2,
        )
        accent_bar.pack(side="left", fill="y", padx=(12, 10), pady=10)

        # ── Main text column ─────────────────────────────────────────────
        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.pack(side="left", fill="both", expand=True, pady=10, padx=(0, 14))

        # Icon + Title row
        header_frame = ctk.CTkFrame(text_frame, fg_color="transparent")
        header_frame.pack(anchor="w", fill="x")

        icon_lbl = ctk.CTkLabel(
            header_frame,
            text=icon,
            text_color=icon_color,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        icon_lbl.pack(side="left", padx=(0, 6))

        title_lbl = ctk.CTkLabel(
            header_frame,
            text=title,
            text_color=TEXT_BRIGHT,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        title_lbl.pack(side="left")

        # ── Reason (optional, suppressed when blank for all-clear) ──────
        if reason:
            reason_lbl = ctk.CTkLabel(
                text_frame,
                text=reason,
                text_color=TEXT_MUTED,
                font=ctk.CTkFont(size=11),
                anchor="w",
                wraplength=620,
                justify="left",
            )
            reason_lbl.pack(anchor="w", fill="x", pady=(4, 0))

        # ── Next step ────────────────────────────────────────────────────
        next_step_lbl = ctk.CTkLabel(
            text_frame,
            text=next_step,
            text_color=TEXT_NEXT_STEP,
            font=ctk.CTkFont(size=11),
            anchor="w",
            wraplength=620,
            justify="left",
        )
        next_step_lbl.pack(anchor="w", fill="x", pady=(4, 0))


# ---------------------------------------------------------------------------
# Non-CTk fallback (used in headless / test environments)
# ---------------------------------------------------------------------------

class PlainRecommendedNextStep:
    """
    Lightweight non-GUI fallback when customtkinter is unavailable.

    Tracks the same state as the real widget so test assertions or
    console rendering can inspect the content without instantiating a
    window.
    """

    def __init__(
        self,
        feature_readiness: Optional[FeatureReadiness] = None,
    ):
        self._feature_readiness = feature_readiness

    def update(self, feature_readiness: Optional[FeatureReadiness]) -> None:
        self._feature_readiness = feature_readiness

    def __repr__(self) -> str:
        if self._feature_readiness is None:
            return "RecommendedNextStep(all_clear=True)"
        fr = self._feature_readiness
        return (
            f"RecommendedNextStep(feature={fr.feature_key.value}, "
            f"status={fr.status.value}, reason={fr.reason!r})"
        )
