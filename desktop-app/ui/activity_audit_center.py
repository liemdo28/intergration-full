"""
ToastPOSManager — Activity & Audit Center UI

Shows event history, lets operators filter by date / category / store,
and export to CSV.
"""

from __future__ import annotations

import csv
import py_compile
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Path helpers (same pattern used throughout the codebase)
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

# ---------------------------------------------------------------------------
# Third-party / standard library imports
# ---------------------------------------------------------------------------

try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except Exception:  # pragma: no cover — defensive for environments without CTk
    CTK_AVAILABLE = False
    import tkinter as tk
    ctk = tk

try:
    import tkinter.messagebox as tk_messagebox
except Exception:
    tk_messagebox = None

# ---------------------------------------------------------------------------
# Local imports
# ---------------------------------------------------------------------------

from services.activity_log_service import (
    clear_old_events,
    export_events_csv,
    get_activity_summary,
    get_events,
    get_recent_activity,
)
from models.activity_event import ActivityEvent, EventCategory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_timestamp(ts: str) -> str:
    """Render an ISO timestamp in a human-friendly 'Apr 16, 10:32 AM' form."""
    try:
        dt = datetime.fromisoformat(ts.rstrip("Z"))
        return dt.strftime("%b %d, %I:%M %p")
    except Exception:
        return ts


def _fmt_duration(seconds: float | None) -> str:
    """Return a compact duration string like '2m 34s'."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


# ---------------------------------------------------------------------------
# Constants — colour palette matches the rest of the desktop app
# ---------------------------------------------------------------------------

C_BG = "#1a1a2e"          # outer background
C_CARD = "#16213e"        # card / panel background
C_CARD_HOVER = "#1f2b47"  # hover tint for event rows
C_BORDER = "#0f3460"      # subtle border colour
C_TEXT = "#e8e8f0"        # primary text
C_TEXT_MUTED = "#64748b"  # muted / secondary text
C_ACCENT = "#3b82f6"      # blue accent / category pill
C_SUCCESS_BAR = "#22c55e" # green — success indicator bar
C_ERROR_BAR = "#ef4444"   # red — failure indicator bar
C_SUCCESS_TEXT = "#4ade80"
C_ERROR_TEXT = "#f87171"
C_AMBER = "#f59e0b"       # warning / caution
C_SECTION_HEADER = "#94a3b8"  # section label colour


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _hex_lighten(hex_color: str, amount: int = 10) -> str:
    """Lighten a 6-char hex colour by ``amount`` steps (0–255)."""
    hex_color = hex_color.lstrip("#")
    r = min(255, int(hex_color[0:2], 16) + amount)
    g = min(255, int(hex_color[2:4], 16) + amount)
    b = min(255, int(hex_color[4:6], 16) + amount)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# ActivityAuditCenter
# ---------------------------------------------------------------------------

class ActivityAuditCenter(ctk.CTkFrame):
    """
    Full-width panel displaying a live-updating stream of application events
    with summary stats, filtering, and CSV export.
    """

    # ----- constructor --------------------------------------------------------

    def __init__(
        self,
        master,
        status_var=None,
        **kwargs,
    ):
        super().__init__(master, fg_color=C_BG, **kwargs)

        self._status_var = status_var
        self._all_events: list[ActivityEvent] = []
        self._filtered_events: list[ActivityEvent] = []
        self._last_refresh = datetime.utcnow()
        self._refresh_timer_id: int | None = None

        # ----- build UI --------------------------------------------------------
        self._build_header()
        self._build_summary_cards()
        self._build_filter_bar()
        self._build_event_list()

        # ----- load data -------------------------------------------------------
        self._load_data()

        # ----- schedule live refresh -------------------------------------------
        self._schedule_refresh()

    # ----- UI builders --------------------------------------------------------

    def _build_header(self) -> None:
        """Title bar."""
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(16, 4))

        title_lbl = ctk.CTkLabel(
            header,
            text="Activity & Audit Center",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=C_TEXT,
        )
        title_lbl.pack(side="left")

        # Last-updated badge
        self._updated_lbl = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=C_TEXT_MUTED,
        )
        self._updated_lbl.pack(side="right")

    def _build_summary_cards(self) -> None:
        """Four compact stat cards across the top."""
        cards_frame = ctk.CTkFrame(self, fg_color="transparent")
        cards_frame.pack(fill="x", padx=24, pady=(4, 8))

        self._summary_labels: dict[str, ctk.CTkLabel] = {}
        card_data = [
            ("total_events", "Total Events"),
            ("success_rate", "Success Rate"),
            ("failures", "Failures"),
            ("stores_tracked", "Stores Tracked"),
        ]

        for key, label_text in card_data:
            card = ctk.CTkFrame(
                cards_frame,
                fg_color=C_CARD,
                corner_radius=8,
                border_width=1,
                border_color=C_BORDER,
            )
            card.pack(side="left", expand=True, fill="both", padx=(0, 8))

            lbl = ctk.CTkLabel(
                card,
                text="—",
                font=ctk.CTkFont(size=22, weight="bold"),
                text_color=C_TEXT,
            )
            lbl.pack(padx=12, pady=(10, 0))

            desc = ctk.CTkLabel(
                card,
                text=label_text,
                font=ctk.CTkFont(size=11),
                text_color=C_TEXT_MUTED,
            )
            desc.pack(padx=12, pady=(0, 10))

            self._summary_labels[key] = lbl

    def _build_filter_bar(self) -> None:
        """Row of filter controls."""
        filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        filter_frame.pack(fill="x", padx=24, pady=(4, 8))

        # Date range dropdown
        date_lbl = ctk.CTkLabel(filter_frame, text="Date:", text_color=C_TEXT_MUTED, font=ctk.CTkFont(size=12))
        date_lbl.pack(side="left", padx=(0, 4))

        self._date_var = ctk.StringVar(value="Last 30 Days")
        date_combo = ctk.CTkComboBox(
            filter_frame,
            variable=self._date_var,
            values=["Today", "Last 7 Days", "Last 30 Days", "All Time"],
            width=140,
            button_color=C_BORDER,
            dropdown_fg_color=C_CARD,
            dropdown_hover_color=C_CARD_HOVER,
            text_color=C_TEXT,
            font=ctk.CTkFont(size=12),
        )
        date_combo.pack(side="left", padx=(0, 12))

        # Category dropdown
        cat_lbl = ctk.CTkLabel(filter_frame, text="Category:", text_color=C_TEXT_MUTED, font=ctk.CTkFont(size=12))
        cat_lbl.pack(side="left", padx=(0, 4))

        cat_options = [
            "All Categories",
            "Download",
            "QB Sync",
            "Remove Transactions",
            "Drive Upload",
            "Settings Change",
            "Recovery",
            "App Lifecycle",
            "Safe Mode",
        ]
        self._cat_var = ctk.StringVar(value="All Categories")
        cat_combo = ctk.CTkComboBox(
            filter_frame,
            variable=self._cat_var,
            values=cat_options,
            width=160,
            button_color=C_BORDER,
            dropdown_fg_color=C_CARD,
            dropdown_hover_color=C_CARD_HOVER,
            text_color=C_TEXT,
            font=ctk.CTkFont(size=12),
        )
        cat_combo.pack(side="left", padx=(0, 12))

        # Text search
        self._search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(
            filter_frame,
            placeholder_text="Search events...",
            placeholder_text_color=C_TEXT_MUTED,
            textvariable=self._search_var,
            width=200,
            fg_color=C_CARD,
            border_color=C_BORDER,
            text_color=C_TEXT,
            font=ctk.CTkFont(size=12),
        )
        search_entry.pack(side="left", padx=(0, 12))

        # Refresh button
        refresh_btn = ctk.CTkButton(
            filter_frame,
            text=" Refresh ",
            command=self._on_refresh,
            width=80,
            corner_radius=6,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        refresh_btn.pack(side="left", padx=(0, 8))

        # Export CSV button
        export_btn = ctk.CTkButton(
            filter_frame,
            text=" Export CSV ",
            command=self._on_export_csv,
            width=100,
            corner_radius=6,
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        export_btn.pack(side="left")

        # Bind Enter key on search box to refresh
        search_entry.bind("<Return>", lambda _: self._on_refresh())

    def _build_event_list(self) -> None:
        """Scrollable event list pane."""
        list_wrapper = ctk.CTkFrame(self, fg_color="transparent")
        list_wrapper.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # Canvas for custom scrolling
        self._canvas = ctk.CTkCanvas(
            list_wrapper,
            bg=C_BG,
            highlightthickness=0,
            confine=True,
        )
        self._canvas.pack(side="left", fill="both", expand=True)

        # Scrollbar
        scrollbar = ctk.CTkScrollbar(
            list_wrapper,
            orientation="vertical",
            command=self._canvas.yview,
            fg_color=C_CARD,
            button_color=C_BORDER,
            button_hover_color=C_ACCENT,
        )
        scrollbar.pack(side="right", fill="y")
        self._canvas.configure(yscrollcommand=scrollbar.set)

        # Scrollable inner frame
        self._list_inner = ctk.CTkFrame(self._canvas, fg_color="transparent")
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._list_inner, anchor="nw"
        )

        # Make inner frame resize with canvas width
        self._list_inner.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(self._canvas_window, width=e.width),
        )

        # Track scroll position for smart refresh
        self._scroll_top_y = 0
        self._canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self._canvas.bind("<Button-4>", self._on_mouse_wheel)
        self._canvas.bind("<Button-5>", self._on_mouse_wheel)

    def _on_mouse_wheel(self, event) -> None:
        self._canvas.yview_scroll(-1 * (event.delta if hasattr(event, "delta") else 120), "units")

    # ----- data loading --------------------------------------------------------

    def _load_data(self) -> None:
        """Fetch events and summary, then update the UI."""
        self._all_events = get_events(limit=200)
        self._apply_filters()
        self._update_summary()
        self._render_events()
        self._last_refresh = datetime.utcnow()
        self._updated_lbl.configure(
            text=f"Last updated: {self._last_refresh.strftime('%I:%M:%S %p')}"
        )

    def _apply_filters(self) -> None:
        """Apply the current filter controls to produce _filtered_events."""
        date_opt = self._date_var.get()
        cat_opt = self._cat_var.get()
        search = self._search_var.get().strip().lower()

        # Resolve date threshold
        since = None
        now = datetime.utcnow()
        if date_opt == "Today":
            since = datetime(now.year, now.month, now.day)
        elif date_opt == "Last 7 Days":
            since = now - timedelta(days=7)
        elif date_opt == "Last 30 Days":
            since = now - timedelta(days=30)
        # "All Time" → since=None

        # Resolve category
        cat_filter: EventCategory | None = None
        cat_map = {
            "Download": EventCategory.DOWNLOAD,
            "QB Sync": EventCategory.QB_SYNC,
            "Remove Transactions": EventCategory.REMOVE_TX,
            "Drive Upload": EventCategory.DRIVE_UPLOAD,
            "Settings Change": EventCategory.SETTINGS_CHANGE,
            "Recovery": EventCategory.RECOVERY,
            "App Lifecycle": EventCategory.APP_LIFECYCLE,
            "Safe Mode": EventCategory.SAFE_MODE,
        }
        if cat_opt in cat_map:
            cat_filter = cat_map[cat_opt]

        # Query the service
        self._filtered_events = get_events(since=since, category=cat_filter, limit=200)

        # Client-side text search
        if search:
            self._filtered_events = [
                e for e in self._filtered_events
                if search in e.title.lower() or search in e.detail.lower()
            ]

    def _update_summary(self) -> None:
        """Compute and display the four summary cards."""
        summary = get_activity_summary()
        total = summary.total_events
        successes = summary.success_count
        failures = summary.failure_count

        # Count unique stores
        stores = len({e.store for e in self._all_events if e.store})

        self._summary_labels["total_events"].configure(text=str(total))

        if total == 0:
            rate_text = "—"
            rate_color = C_TEXT_MUTED
        else:
            pct = successes / total * 100
            rate_text = f"{pct:.0f}%"
            rate_color = C_SUCCESS_TEXT if pct > 80 else C_AMBER

        self._summary_labels["success_rate"].configure(
            text=rate_text, text_color=rate_color
        )
        self._summary_labels["failures"].configure(
            text=str(failures),
            text_color=C_ERROR_TEXT if failures > 0 else C_TEXT_MUTED,
        )
        self._summary_labels["stores_tracked"].configure(text=str(stores))

    # ----- render -------------------------------------------------------------

    def _render_events(self, max_rows: int = 50) -> None:
        """Clear and redraw the event list."""
        # Remove all existing row widgets
        for widget in self._list_inner.winfo_children():
            widget.destroy()

        events_to_show = self._filtered_events[:max_rows]

        if not events_to_show:
            empty = ctk.CTkLabel(
                self._list_inner,
                text="No activity recorded yet.",
                font=ctk.CTkFont(size=13),
                text_color=C_TEXT_MUTED,
            )
            empty.pack(pady=40)
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
            return

        for event in events_to_show:
            self._render_event_row(event)

        # Load more button if there are more events
        if len(self._filtered_events) > max_rows:
            load_more = ctk.CTkButton(
                self._list_inner,
                text="Load more...",
                command=lambda: self._render_events(max_rows + 50),
                corner_radius=6,
                fg_color=C_BORDER,
                hover_color=C_CARD_HOVER,
                text_color=C_TEXT_MUTED,
                font=ctk.CTkFont(size=12),
                width=200,
            )
            load_more.pack(pady=(12, 20))

        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _render_event_row(self, event: ActivityEvent) -> None:
        """Create and pack one event row card."""
        bar_color = C_SUCCESS_BAR if event.success else C_ERROR_BAR

        row = ctk.CTkFrame(
            self._list_inner,
            fg_color=C_CARD,
            corner_radius=6,
            border_width=0,
        )
        row.pack(fill="x", padx=(0, 0), pady=3, ipady=0)

        # Hover effect
        def on_enter(_):
            row.configure(fg_color=C_CARD_HOVER)
        def on_leave(_):
            row.configure(fg_color=C_CARD)

        row.bind("<Enter>", on_enter)
        row.bind("<Leave>", on_leave)

        # Left colour bar
        bar = ctk.CTkFrame(row, fg_color=bar_color, width=4)
        bar.pack(side="left", fill="y", padx=(0, 8))

        # Content column
        content = ctk.CTkFrame(row, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True, padx=8, pady=6)

        # Top row: timestamp | category pill | duration badge
        top_row = ctk.CTkFrame(content, fg_color="transparent")
        top_row.pack(fill="x")

        ts_lbl = ctk.CTkLabel(
            top_row,
            text=_fmt_timestamp(event.timestamp),
            font=ctk.CTkFont(size=10),
            text_color=C_TEXT_MUTED,
        )
        ts_lbl.pack(side="left", padx=(0, 8))

        pill = ctk.CTkFrame(
            top_row,
            fg_color=C_ACCENT,
            corner_radius=4,
        )
        pill.pack(side="left", padx=(0, 8))
        pill_lbl = ctk.CTkLabel(
            pill,
            text=_category_display_name(event.category),
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color="white",
        )
        pill_lbl.pack(padx=6, pady=1)

        if event.duration_seconds is not None:
            dur_badge = ctk.CTkFrame(
                top_row,
                fg_color=C_BORDER,
                corner_radius=4,
            )
            dur_badge.pack(side="left")
            dur_lbl = ctk.CTkLabel(
                dur_badge,
                text=_fmt_duration(event.duration_seconds),
                font=ctk.CTkFont(size=9),
                text_color=C_TEXT_MUTED,
            )
            dur_lbl.pack(padx=6, pady=1)

        if event.store:
            store_badge = ctk.CTkFrame(
                top_row,
                fg_color="#4b3f6b",
                corner_radius=4,
            )
            store_badge.pack(side="left", padx=(4, 0))
            store_lbl = ctk.CTkLabel(
                store_badge,
                text=event.store,
                font=ctk.CTkFont(size=9),
                text_color="#c4b5fd",
            )
            store_lbl.pack(padx=6, pady=1)

        # Title
        title_lbl = ctk.CTkLabel(
            content,
            text=event.title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=C_TEXT,
            wraplength=600,
            anchor="w",
            justify="left",
        )
        title_lbl.pack(fill="x", pady=(4, 0))

        # Detail
        if event.detail:
            detail_lbl = ctk.CTkLabel(
                content,
                text=event.detail,
                font=ctk.CTkFont(size=11),
                text_color=C_TEXT_MUTED,
                wraplength=600,
                anchor="w",
                justify="left",
            )
            detail_lbl.pack(fill="x")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _category_display_name(cat: EventCategory) -> str:
    """Return a human-readable label for each EventCategory value."""
    mapping = {
        EventCategory.DOWNLOAD: "Download",
        EventCategory.QB_SYNC: "QB Sync",
        EventCategory.REMOVE_TX: "Remove Tx",
        EventCategory.DRIVE_UPLOAD: "Drive Upload",
        EventCategory.SETTINGS_CHANGE: "Settings",
        EventCategory.RECOVERY: "Recovery",
        EventCategory.APP_LIFECYCLE: "App",
        EventCategory.CRASH: "Crash",
        EventCategory.SAFE_MODE: "Safe Mode",
    }
    return mapping.get(cat, cat.value)


# ---------------------------------------------------------------------------
# Action handlers  (inside class — indented to class body level)
# ---------------------------------------------------------------------------

    def _on_refresh(self) -> None:
        """Re-run the query with current filter values."""
        self._load_data()

    def _on_export_csv(self) -> None:
        """Write the currently filtered events to a CSV file chosen via dialog."""
        if not self._filtered_events:
            self._message_box(
                "No Data", "No events to export. Adjust filters and try again."
            )
            return

        try:
            from tkinter import filedialog
            dest, _ = filedialog.asksaveasfile(
                title="Export Activity Log as CSV",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile="activity_log.csv",
            )
            if dest is None:
                return
            dest_path = Path(dest.name) if hasattr(dest, "name") else Path(dest)
            export_events_csv(self._filtered_events, dest_path)
            self._message_box(
                "Export Successful",
                f"Exported {len(self._filtered_events)} events to:\n{dest_path}",
            )
        except Exception as exc:
            self._message_box("Export Failed", f"Could not export CSV:\n{exc}")

    def _message_box(self, title: str, message: str) -> None:
        try:
            from tkinter import messagebox
            messagebox.showinfo(title, message)
        except Exception:
            pass

    # ----- live refresh scheduler --------------------------------------------

    def _schedule_refresh(self, interval_ms: int = 30000) -> None:
        """Schedule the next automatic refresh."""
        if self._refresh_timer_id is not None:
            try:
                self.after_cancel(self._refresh_timer_id)
            except Exception:
                pass
        self._refresh_timer_id = self.after(interval_ms, self._do_scheduled_refresh)

    def _do_scheduled_refresh(self) -> None:
        """Auto-refresh triggered by the timer."""
        self._load_data()
        self._schedule_refresh()

    def destroy(self) -> None:
        """Clean up the refresh timer before destroying the widget."""
        if self._refresh_timer_id is not None:
            try:
                self.after_cancel(self._refresh_timer_id)
            except Exception:
                pass
        super().destroy()