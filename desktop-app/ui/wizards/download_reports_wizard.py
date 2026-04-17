"""
ToastPOSManager — Download Reports Wizard

Guides the operator through:
  1. Select Stores
  2. Select Dates
  3. Check Readiness
  4. Download
  5. Result
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

_log = logging.getLogger(__name__)

try:
    import customtkinter as ctk
    CTK = True
except ImportError:
    CTK = False

from ui.wizards.wizard_base import WizardBase
from ui.wizards.wizard_result_view import WizardResultView
from services import workflow_state_service

# ---------------------------------------------------------------------------
# Store list
# ---------------------------------------------------------------------------
STORES = ["Stockton", "The Rim", "Stone Oak", "Bandera", "WA1", "WA2", "WA3"]

_STEPS = [
    "Select Stores",
    "Select Dates",
    "Check Readiness",
    "Download",
    "Result",
]

_MUTED = "#94a3b8"
_CARD_BG = "#111827"
_INPUT_BG = "#1e293b"


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")


def _n_days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


class DownloadReportsWizard(WizardBase):
    """Guided wizard for downloading Toast reports."""

    def __init__(self, master, *, status_var=None, **kwargs):
        self._state = workflow_state_service.create_workflow("dl_wizard")
        self._stop_event = threading.Event()
        self._download_thread: threading.Thread | None = None
        self._result_view: WizardResultView | None = None
        self._activated_once = False  # skip reset on very first show

        super().__init__(
            master,
            title="Download Reports",
            steps=_STEPS,
            status_var=status_var,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # State reset — Finding 3: wizard state must not leak between runs
    # ------------------------------------------------------------------

    def on_wizard_activated(self) -> None:
        """Reset to a clean state each time the operator re-enters this wizard."""
        if not self._activated_once:
            self._activated_once = True
            return  # first show — state was just created, nothing to reset
        self._reset_for_new_run()

    def _reset_for_new_run(self) -> None:
        """Clear all run state so the next wizard pass starts completely fresh."""
        self._stop_event.clear()
        self._state = workflow_state_service.create_workflow("dl_wizard")
        self._step_index = 0
        self._refresh()
        self.on_step_changed(0)

    # ------------------------------------------------------------------
    # Step dispatch
    # ------------------------------------------------------------------

    def on_step_changed(self, step_index: int) -> None:
        # Clear content frame
        for child in self._content_frame.winfo_children():
            child.destroy()
        self._result_view = None

        if step_index == 0:
            self._step_select_stores()
        elif step_index == 1:
            self._step_select_dates()
        elif step_index == 2:
            self._step_check_readiness()
        elif step_index == 3:
            self._step_download()
        elif step_index == 4:
            self._step_result()

    # ------------------------------------------------------------------
    # Step 1: Select Stores
    # ------------------------------------------------------------------

    def _step_select_stores(self) -> None:
        self.set_next_enabled(bool(self._state.selected_stores))

        frame = ctk.CTkFrame(self._content_frame, fg_color=_CARD_BG, corner_radius=12)
        frame.pack(fill="x", padx=20, pady=20)

        ctk.CTkLabel(
            frame,
            text="Select the stores to download reports for.",
            font=ctk.CTkFont(size=13),
            text_color=_MUTED,
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 4))

        # Select All toggle
        self._all_var = ctk.BooleanVar(value=len(self._state.selected_stores) == len(STORES))

        def _toggle_all():
            all_on = self._all_var.get()
            for var in self._store_vars.values():
                var.set(all_on)
            self._state.selected_stores = list(STORES) if all_on else []
            self.set_next_enabled(bool(self._state.selected_stores))

        ctk.CTkCheckBox(
            frame,
            text="Select All",
            variable=self._all_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#f1f5f9",
            command=_toggle_all,
        ).pack(anchor="w", padx=16, pady=(8, 4))

        ctk.CTkFrame(frame, height=1, fg_color="#1e293b").pack(fill="x", padx=16, pady=4)

        self._store_vars: dict[str, ctk.BooleanVar] = {}

        def _on_store_change(store: str):
            if self._store_vars[store].get():
                if store not in self._state.selected_stores:
                    self._state.selected_stores.append(store)
            else:
                self._state.selected_stores = [s for s in self._state.selected_stores if s != store]
            all_checked = all(v.get() for v in self._store_vars.values())
            self._all_var.set(all_checked)
            self.set_next_enabled(bool(self._state.selected_stores))

        for store in STORES:
            var = ctk.BooleanVar(value=store in self._state.selected_stores)
            self._store_vars[store] = var
            ctk.CTkCheckBox(
                frame,
                text=store,
                variable=var,
                font=ctk.CTkFont(size=12),
                text_color="#e2e8f0",
                command=lambda s=store: _on_store_change(s),
            ).pack(anchor="w", padx=24, pady=3)

        ctk.CTkFrame(frame, height=1, fg_color="transparent").pack(pady=8)

    # ------------------------------------------------------------------
    # Step 2: Select Dates
    # ------------------------------------------------------------------

    def _step_select_dates(self) -> None:
        self.set_next_enabled(bool(self._state.date_start and self._state.date_end))

        frame = ctk.CTkFrame(self._content_frame, fg_color=_CARD_BG, corner_radius=12)
        frame.pack(fill="x", padx=20, pady=20)

        ctk.CTkLabel(
            frame,
            text="Select a date range for the reports.",
            font=ctk.CTkFont(size=13),
            text_color=_MUTED,
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 10))

        # Quick-select buttons
        quick_row = ctk.CTkFrame(frame, fg_color="transparent")
        quick_row.pack(fill="x", padx=16, pady=(0, 12))

        def _set_range(start: str, end: str):
            self._start_entry.delete(0, "end")
            self._start_entry.insert(0, start)
            self._end_entry.delete(0, "end")
            self._end_entry.insert(0, end)
            self._state.date_start = start
            self._state.date_end = end
            self.set_next_enabled(True)

        quick_btns = [
            ("Today", _today(), _today()),
            ("Yesterday", _yesterday(), _yesterday()),
            ("Last 7 Days", _n_days_ago(6), _today()),
            ("Last 30 Days", _n_days_ago(29), _today()),
        ]

        for label, s, e in quick_btns:
            ctk.CTkButton(
                quick_row,
                text=label,
                width=100,
                height=28,
                fg_color=_INPUT_BG,
                hover_color="#334155",
                text_color="#f1f5f9",
                corner_radius=6,
                font=ctk.CTkFont(size=11),
                command=lambda s=s, e=e: _set_range(s, e),
            ).pack(side="left", padx=4)

        # Manual entries
        entries_frame = ctk.CTkFrame(frame, fg_color="transparent")
        entries_frame.pack(fill="x", padx=16, pady=(0, 16))

        def _lbl(parent, text):
            ctk.CTkLabel(
                parent,
                text=text,
                font=ctk.CTkFont(size=12),
                text_color=_MUTED,
                anchor="w",
            ).pack(anchor="w")

        left = ctk.CTkFrame(entries_frame, fg_color="transparent")
        left.pack(side="left", padx=(0, 20))
        _lbl(left, "Start Date (YYYY-MM-DD)")
        self._start_entry = ctk.CTkEntry(
            left,
            width=160,
            fg_color=_INPUT_BG,
            border_color="#334155",
            text_color="#f1f5f9",
            placeholder_text="e.g. 2025-01-01",
        )
        self._start_entry.pack()
        if self._state.date_start:
            self._start_entry.insert(0, self._state.date_start)

        right = ctk.CTkFrame(entries_frame, fg_color="transparent")
        right.pack(side="left")
        _lbl(right, "End Date (YYYY-MM-DD)")
        self._end_entry = ctk.CTkEntry(
            right,
            width=160,
            fg_color=_INPUT_BG,
            border_color="#334155",
            text_color="#f1f5f9",
            placeholder_text="e.g. 2025-01-31",
        )
        self._end_entry.pack()
        if self._state.date_end:
            self._end_entry.insert(0, self._state.date_end)

        def _on_entry_change(*args):
            s = self._start_entry.get().strip()
            e = self._end_entry.get().strip()
            self._state.date_start = s
            self._state.date_end = e
            self.set_next_enabled(bool(s and e))

        self._start_entry.bind("<KeyRelease>", _on_entry_change)
        self._end_entry.bind("<KeyRelease>", _on_entry_change)

    # ------------------------------------------------------------------
    # Step 3: Check Readiness
    # ------------------------------------------------------------------

    def _step_check_readiness(self) -> None:
        self.set_next_enabled(False)

        frame = ctk.CTkFrame(self._content_frame, fg_color=_CARD_BG, corner_radius=12)
        frame.pack(fill="x", padx=20, pady=20)

        ctk.CTkLabel(
            frame,
            text="Checking prerequisites before download...",
            font=ctk.CTkFont(size=13),
            text_color=_MUTED,
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 10))

        results_frame = ctk.CTkFrame(frame, fg_color="transparent")
        results_frame.pack(fill="x", padx=16, pady=(0, 16))

        def _run():
            try:
                from services.preflight_validation_service import validate_download_readiness
                result = validate_download_readiness(
                    stores=self._state.selected_stores,
                    date_start=self._state.date_start,
                    date_end=self._state.date_end,
                    report_types=self._state.selected_report_types or ["sales_summary"],
                )
                self.after(0, lambda: _render_results(result))
            except Exception as exc:
                self.after(0, lambda: _render_error(str(exc)))

        def _render_results(result):
            for child in results_frame.winfo_children():
                child.destroy()

            for item in result.items:
                row = ctk.CTkFrame(results_frame, fg_color="#0f172a", corner_radius=6)
                row.pack(fill="x", pady=2)

                icon = "✓" if item.ok else "✗"
                color = "#22c55e" if item.ok else "#ef4444"

                ctk.CTkLabel(
                    row,
                    text=icon,
                    font=ctk.CTkFont(size=14, weight="bold"),
                    text_color=color,
                    width=28,
                ).pack(side="left", padx=(10, 6), pady=8)

                text_col = ctk.CTkFrame(row, fg_color="transparent")
                text_col.pack(side="left", fill="x", expand=True, pady=4)

                ctk.CTkLabel(
                    text_col,
                    text=item.label,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    text_color="#f1f5f9",
                    anchor="w",
                ).pack(anchor="w")

                ctk.CTkLabel(
                    text_col,
                    text=item.message,
                    font=ctk.CTkFont(size=11),
                    text_color=_MUTED,
                    anchor="w",
                ).pack(anchor="w")

                if not item.ok and item.fix_hint:
                    ctk.CTkLabel(
                        text_col,
                        text=f"Fix: {item.fix_hint}",
                        font=ctk.CTkFont(size=11),
                        text_color="#f59e0b",
                        anchor="w",
                    ).pack(anchor="w")

            self.set_next_enabled(result.passed)

        def _render_error(msg: str):
            ctk.CTkLabel(
                results_frame,
                text=f"Validation error: {msg}",
                font=ctk.CTkFont(size=12),
                text_color="#ef4444",
                anchor="w",
            ).pack(anchor="w")

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Step 4: Download
    # ------------------------------------------------------------------

    def _step_download(self) -> None:
        self.set_next_enabled(False)
        self._stop_event.clear()

        frame = ctk.CTkFrame(self._content_frame, fg_color=_CARD_BG, corner_radius=12)
        frame.pack(fill="x", padx=20, pady=20)

        ctk.CTkLabel(
            frame,
            text="Download progress",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#f8fafc",
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 6))

        log_box = ctk.CTkTextbox(
            frame,
            height=260,
            fg_color="#0b1220",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=11, family="Courier"),
        )
        log_box.pack(fill="x", padx=16, pady=(0, 10))
        log_box.configure(state="disabled")

        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        def _append_log(msg: str):
            try:
                log_box.configure(state="normal")
                log_box.insert("end", msg + "\n")
                log_box.see("end")
                log_box.configure(state="disabled")
            except Exception:
                pass

        def _on_progress(msg: str):
            self.after(0, lambda m=msg: _append_log(m))

        def _run_download():
            try:
                from services.download_reports_service import run_download
                result = run_download(
                    stores=self._state.selected_stores,
                    date_start=self._state.date_start,
                    date_end=self._state.date_end,
                    report_types=self._state.selected_report_types or ["sales_summary"],
                    on_progress=_on_progress,
                    stop_event=self._stop_event,
                )
                self._state.result = {
                    "ok": result.ok,
                    "success_count": result.success_count,
                    "fail_count": result.fail_count,
                    "total_count": result.total_count,
                    "warnings": result.warnings,
                }
                self._state.is_complete = True
            except Exception as exc:
                self._state.result = {
                    "ok": False,
                    "error": str(exc),
                    "warnings": [str(exc)],
                }
                self._state.is_complete = True
                _on_progress(f"Error: {exc}")
            self.after(0, lambda: self.set_next_enabled(True))
            self.after(0, lambda: start_btn.configure(state="disabled"))
            self.after(0, lambda: stop_btn.configure(state="disabled"))

        def _start():
            start_btn.configure(state="disabled")
            stop_btn.configure(state="normal")
            self._download_thread = threading.Thread(target=_run_download, daemon=True)
            self._download_thread.start()

        def _stop():
            self._stop_event.set()
            stop_btn.configure(state="disabled")
            _append_log("Stop requested...")

        start_btn = ctk.CTkButton(
            btn_row,
            text="Start Download",
            fg_color="#22c55e",
            hover_color="#16a34a",
            text_color="#ffffff",
            corner_radius=8,
            width=140,
            command=_start,
        )
        start_btn.pack(side="left", padx=4)

        stop_btn = ctk.CTkButton(
            btn_row,
            text="Stop",
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            text_color="#ffffff",
            corner_radius=8,
            width=80,
            state="disabled",
            command=_stop,
        )
        stop_btn.pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # Step 5: Result
    # ------------------------------------------------------------------

    def _step_result(self) -> None:
        res = self._state.result
        ok = res.get("ok", False)
        s_count = res.get("success_count", 0)
        f_count = res.get("fail_count", 0)
        t_count = res.get("total_count", 0)
        warnings = res.get("warnings", [])
        error = res.get("error", "")

        # Determine outcome type — Finding 5: must reflect reality, not just ok flag.
        # "completed" requires: ok=True AND no failures AND no warnings.
        # Any warning (even with ok=True) → "completed_with_warnings".
        if ok and f_count == 0 and not warnings:
            outcome_type = "completed"
        elif ok and (f_count > 0 or warnings):
            outcome_type = "completed_with_warnings"
        elif not ok and s_count == 0:
            outcome_type = "failed_safely"
        else:
            outcome_type = "completed_with_warnings"

        if ok:
            title = "Download Complete"
            summary = [
                f"{s_count} of {t_count} files downloaded successfully.",
                f"Stores: {', '.join(self._state.selected_stores)}",
                f"Date range: {self._state.date_start} to {self._state.date_end}",
            ]
        else:
            title = "Download Finished with Errors"
            summary = [
                f"{s_count} succeeded, {f_count} failed out of {t_count} total.",
                f"Stores: {', '.join(self._state.selected_stores)}",
            ]
            if error:
                summary.append(f"Error: {error}")

        def _done():
            if self._status_var is not None:
                self._status_var.set("navigate:home")

        def _go_qb():
            if self._status_var is not None:
                self._status_var.set("navigate:wizard_qb")

        def _go_home():
            if self._status_var is not None:
                self._status_var.set("navigate:home")

        stats = [
            ("Downloaded", str(s_count)),
            ("Failed", str(f_count)),
            ("Stores", str(len(self._state.selected_stores))),
        ]
        if warnings:
            stats.append(("Warnings", str(len(warnings))))

        self._result_view = WizardResultView(
            self._content_frame,
            outcome_type=outcome_type,
            title=title,
            summary_lines=summary,
            warnings=warnings,
            stats=stats,
            next_action_label="→ Sync to QuickBooks",
            next_action_command=_go_qb,
            secondary_action_label="Return Home",
            secondary_action_command=_go_home,
            done_command=_done,
        )
        self._result_view.pack(fill="x", padx=20, pady=20)
        self.set_next_enabled(True)
