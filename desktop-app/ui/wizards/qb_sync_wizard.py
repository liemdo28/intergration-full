"""
ToastPOSManager — QB Sync Wizard

Guides the operator through:
  1. Select Stores
  2. Select Dates
  3. Check Readiness
  4. Preview Sync
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
    "Preview Sync",
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


class QBSyncWizard(WizardBase):
    """Guided wizard for syncing reports to QuickBooks."""

    def __init__(self, master, *, status_var=None, **kwargs):
        self._state = workflow_state_service.create_workflow("qb_wizard")
        self._stop_event = threading.Event()
        self._sync_thread: threading.Thread | None = None
        self._result_view: WizardResultView | None = None

        super().__init__(
            master,
            title="Sync to QuickBooks",
            steps=_STEPS,
            status_var=status_var,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Step dispatch
    # ------------------------------------------------------------------

    def on_step_changed(self, step_index: int) -> None:
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
            self._step_preview_sync()
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
            text="Select the stores to sync to QuickBooks.",
            font=ctk.CTkFont(size=13),
            text_color=_MUTED,
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 4))

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
            text="Select a date range for the QuickBooks sync.",
            font=ctk.CTkFont(size=13),
            text_color=_MUTED,
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 10))

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
            text="Checking prerequisites before QB sync...",
            font=ctk.CTkFont(size=13),
            text_color=_MUTED,
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 10))

        results_frame = ctk.CTkFrame(frame, fg_color="transparent")
        results_frame.pack(fill="x", padx=16, pady=(0, 16))

        def _run():
            try:
                from services.preflight_validation_service import validate_qb_sync_readiness
                result = validate_qb_sync_readiness(
                    stores=self._state.selected_stores,
                    date_start=self._state.date_start,
                    date_end=self._state.date_end,
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
    # Step 4: Preview Sync
    # ------------------------------------------------------------------

    def _show_safety_issues(self, parent, safety) -> None:
        """Renders a safety warning card with issues listed by severity."""
        safety_frame = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=10, border_width=1, border_color="#334155")
        safety_frame.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkLabel(
            safety_frame,
            text="Pre-Sync Safety Check",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f8fafc",
            anchor="w",
        ).pack(anchor="w", padx=14, pady=(10, 6))

        for issue in safety.issues:
            is_error = issue.severity == "error"
            row_color = "#3b0a0a" if is_error else "#2d1f00"
            text_color = "#ef4444" if is_error else "#f59e0b"
            icon = "✗" if is_error else "⚠"

            row = ctk.CTkFrame(safety_frame, fg_color=row_color, corner_radius=6)
            row.pack(fill="x", padx=10, pady=3)

            ctk.CTkLabel(
                row,
                text=f"{icon} {issue.title}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=text_color,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(6, 2))

            ctk.CTkLabel(
                row,
                text=issue.detail,
                font=ctk.CTkFont(size=11),
                text_color="#cbd5e1",
                anchor="w",
                justify="left",
                wraplength=480,
            ).pack(anchor="w", padx=12, pady=(0, 2))

            if issue.fix_hint:
                ctk.CTkLabel(
                    row,
                    text=f"Fix: {issue.fix_hint}",
                    font=ctk.CTkFont(size=11),
                    text_color="#f59e0b",
                    anchor="w",
                    wraplength=480,
                ).pack(anchor="w", padx=12, pady=(0, 6))

        if safety.has_errors:
            ctk.CTkLabel(
                safety_frame,
                text="Sync blocked — fix issues above before proceeding.",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#ef4444",
                anchor="w",
            ).pack(anchor="w", padx=14, pady=(6, 10))
        else:
            ctk.CTkLabel(
                safety_frame,
                text="Review warnings above, then confirm to proceed.",
                font=ctk.CTkFont(size=12),
                text_color="#f59e0b",
                anchor="w",
            ).pack(anchor="w", padx=14, pady=(6, 10))

    def _show_completeness_block(self, parent, completeness):
        """Show a hard block UI when source files are missing."""
        block_card = ctk.CTkFrame(parent, fg_color="#3b0f0f", corner_radius=12, border_width=1, border_color="#ef4444")
        block_card.pack(fill="x", padx=20, pady=(16, 8))

        # Header
        header = ctk.CTkFrame(block_card, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(header, text="✕", font=ctk.CTkFont(size=20, weight="bold"), text_color="#ef4444").pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text="Source reports are missing — sync is blocked",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color="#fca5a5", anchor="w").pack(side="left")

        # Explanation
        ctk.CTkLabel(block_card,
                     text="QuickBooks sync requires ALL reports to be present in Drive before writing.\n"
                          "Partial sync would create accounting gaps.",
                     font=ctk.CTkFont(size=12), text_color="#fca5a5",
                     anchor="w", justify="left", wraplength=550).pack(anchor="w", padx=16, pady=(0, 8))

        # Missing files list
        if completeness.missing_files:
            missing_frame = ctk.CTkScrollableFrame(block_card, height=120, fg_color="#2a0a0a", corner_radius=6)
            missing_frame.pack(fill="x", padx=16, pady=(0, 8))

            ctk.CTkLabel(missing_frame,
                         text=f"Missing ({completeness.missing_count} of {completeness.total_count}):",
                         font=ctk.CTkFont(size=11, weight="bold"), text_color="#f87171",
                         anchor="w").pack(anchor="w", padx=8, pady=(4, 2))

            for sf in completeness.missing_files[:20]:
                ctk.CTkLabel(missing_frame,
                             text=f"  • {sf.label}  ({sf.report_type.replace('_', ' ').title()})",
                             font=ctk.CTkFont(size=11), text_color="#fca5a5",
                             anchor="w").pack(anchor="w", padx=8, pady=1)

            if completeness.missing_count > 20:
                ctk.CTkLabel(missing_frame,
                             text=f"  … and {completeness.missing_count - 20} more",
                             font=ctk.CTkFont(size=11), text_color="#94a3b8",
                             anchor="w").pack(anchor="w", padx=8, pady=1)

        # Next step
        ctk.CTkLabel(block_card,
                     text="Next step: Use the Download Wizard to download missing reports, then return here.",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color="#fdba74",
                     anchor="w", wraplength=550).pack(anchor="w", padx=16, pady=(0, 14))

        # Go to Download button
        ctk.CTkButton(block_card, text="→ Open Download Wizard",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      fg_color="#7c3aed", hover_color="#6d28d9", height=34,
                      command=lambda: self._nav("navigate:wizard_download")).pack(anchor="w", padx=16, pady=(0, 14))

    def _show_gate_block(self, parent, gate):
        block = ctk.CTkFrame(parent, fg_color="#1f0a0a", corner_radius=12, border_width=1, border_color="#ef4444")
        block.pack(fill="x", padx=20, pady=(16, 8))

        ctk.CTkLabel(block, text="✕  Sync Blocked — fix the issues below before continuing",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color="#fca5a5",
                     anchor="w").pack(anchor="w", padx=16, pady=(14, 6))

        for issue in gate.blockers:
            row = ctk.CTkFrame(block, fg_color="#2a0a0a", corner_radius=8)
            row.pack(fill="x", padx=16, pady=3)
            ctk.CTkLabel(row, text=f"• {issue.title}", font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#f87171", anchor="w").pack(anchor="w", padx=10, pady=(8, 2))
            ctk.CTkLabel(row, text=issue.detail, font=ctk.CTkFont(size=11), text_color="#fca5a5",
                         anchor="w", wraplength=520, justify="left").pack(anchor="w", padx=10)
            if issue.fix_hint:
                ctk.CTkLabel(row, text=f"→ {issue.fix_hint}", font=ctk.CTkFont(size=11, weight="bold"),
                             text_color="#f59e0b", anchor="w", wraplength=520).pack(anchor="w", padx=10, pady=(2, 8))
            if issue.nav_target:
                ctk.CTkButton(row, text="Go There →", height=28, width=100, corner_radius=6,
                              fg_color="#7c3aed", hover_color="#6d28d9",
                              font=ctk.CTkFont(size=11),
                              command=lambda t=issue.nav_target: self._nav(t)).pack(anchor="w", padx=10, pady=(0, 8))

    def _show_gate_warnings(self, parent, gate):
        warn = ctk.CTkFrame(parent, fg_color="#1f1500", corner_radius=12, border_width=1, border_color="#f59e0b")
        warn.pack(fill="x", padx=20, pady=(16, 8))

        ctk.CTkLabel(warn, text="⚠  Warnings — review before continuing",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color="#fcd34d",
                     anchor="w").pack(anchor="w", padx=16, pady=(14, 6))

        for issue in gate.warnings:
            row = ctk.CTkFrame(warn, fg_color="#2a1a00", corner_radius=8)
            row.pack(fill="x", padx=16, pady=3)
            ctk.CTkLabel(row, text=f"• {issue.title}", font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#fbbf24", anchor="w").pack(anchor="w", padx=10, pady=(8, 2))
            ctk.CTkLabel(row, text=issue.detail, font=ctk.CTkFont(size=11), text_color="#fcd34d",
                         anchor="w", wraplength=520, justify="left").pack(anchor="w", padx=10)
            if issue.fix_hint:
                ctk.CTkLabel(row, text=f"→ {issue.fix_hint}", font=ctk.CTkFont(size=11),
                             text_color="#94a3b8", anchor="w", wraplength=520).pack(anchor="w", padx=10, pady=(2, 8))

    def _nav(self, destination: str):
        if self._status_var:
            self._status_var.set(destination)

    def _step_preview_sync(self) -> None:
        self.set_next_enabled(False)
        self._stop_event.clear()

        frame = ctk.CTkFrame(self._content_frame, fg_color=_CARD_BG, corner_radius=12)
        frame.pack(fill="x", padx=20, pady=20)

        ctk.CTkLabel(
            frame,
            text="Preview: what will be synced to QuickBooks",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#f8fafc",
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(14, 6))

        # Consolidated pre-sync gate — runs all safety checks in one pass
        try:
            from services.consolidated_sync_gate import run_consolidated_gate
            gate = run_consolidated_gate(
                stores=self._state.selected_stores,
                date_start=self._state.date_start,
                date_end=self._state.date_end,
            )

            if not gate.can_proceed:
                self._show_gate_block(frame, gate)
                self.set_next_enabled(False)
                return

            if gate.warnings:
                self._show_gate_warnings(frame, gate)
                # Still allow proceeding with warnings
        except Exception:
            pass

        # Table header
        hdr = ctk.CTkFrame(frame, fg_color="#0f172a", corner_radius=0)
        hdr.pack(fill="x", padx=16, pady=(0, 2))
        for col_text, w in [("Store", 120), ("Date", 100), ("File", 200), ("Status", 100)]:
            ctk.CTkLabel(
                hdr,
                text=col_text,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#64748b",
                width=w,
                anchor="w",
            ).pack(side="left", padx=6, pady=4)

        table_frame = ctk.CTkScrollableFrame(frame, height=180, fg_color="transparent")
        table_frame.pack(fill="x", padx=16, pady=(0, 10))

        summary_lbl = ctk.CTkLabel(
            frame,
            text="Building preview...",
            font=ctk.CTkFont(size=12),
            text_color=_MUTED,
            anchor="w",
        )
        summary_lbl.pack(anchor="w", padx=16, pady=(0, 8))

        # Progress log (shown during sync)
        log_box = ctk.CTkTextbox(
            frame,
            height=120,
            fg_color="#0b1220",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=11, family="Courier"),
        )
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

        def _build_preview():
            try:
                from services.qb_sync_preview_service import build_qb_sync_preview, get_preview_summary_text
                preview = build_qb_sync_preview(
                    stores=self._state.selected_stores,
                    date_start=self._state.date_start,
                    date_end=self._state.date_end,
                )
                self.after(0, lambda: _render_preview(preview))
            except Exception as exc:
                self.after(0, lambda: summary_lbl.configure(text=f"Preview error: {exc}", text_color="#ef4444"))

        def _render_preview(preview):
            for child in table_frame.winfo_children():
                child.destroy()

            for entry in preview.entries:
                status_text = "Ready"
                status_color = "#22c55e"
                if entry.warnings:
                    status_text = "Warning"
                    status_color = "#f59e0b"

                row = ctk.CTkFrame(table_frame, fg_color="#0f172a", corner_radius=4)
                row.pack(fill="x", pady=1)

                for text, w in [
                    (entry.store, 120),
                    (entry.date, 100),
                    (entry.file_name[:28] + "..." if len(entry.file_name) > 28 else entry.file_name, 200),
                ]:
                    ctk.CTkLabel(
                        row,
                        text=text,
                        font=ctk.CTkFont(size=11),
                        text_color="#cbd5e1",
                        width=w,
                        anchor="w",
                    ).pack(side="left", padx=6, pady=4)

                ctk.CTkLabel(
                    row,
                    text=status_text,
                    font=ctk.CTkFont(size=11),
                    text_color=status_color,
                    width=100,
                    anchor="w",
                ).pack(side="left", padx=6, pady=4)

            try:
                from services.qb_sync_preview_service import get_preview_summary_text
                summary_lbl.configure(text=get_preview_summary_text(preview), text_color=_MUTED)
            except Exception:
                summary_lbl.configure(text=f"{len(preview.entries)} items queued.", text_color=_MUTED)

            if preview.can_proceed:
                confirm_btn.configure(state="normal")
            else:
                summary_lbl.configure(
                    text=preview.block_reason or "Cannot proceed.",
                    text_color="#ef4444",
                )

        def _run_sync():
            try:
                from services.qb_sync_service import run_qb_sync
                res = run_qb_sync(
                    stores=self._state.selected_stores,
                    date_start=self._state.date_start,
                    date_end=self._state.date_end,
                    on_progress=_on_progress,
                    stop_event=self._stop_event,
                )
                self._state.result = res
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
            self.after(0, lambda: confirm_btn.configure(state="disabled"))
            self.after(0, lambda: stop_btn.configure(state="disabled"))

        def _confirm_sync():
            confirm_btn.configure(state="disabled")
            stop_btn.configure(state="normal")
            log_box.pack(fill="x", padx=16, pady=(0, 10))
            self._sync_thread = threading.Thread(target=_run_sync, daemon=True)
            self._sync_thread.start()

        def _stop():
            self._stop_event.set()
            stop_btn.configure(state="disabled")
            _append_log("Stop requested...")

        confirm_btn = ctk.CTkButton(
            btn_row,
            text="Confirm & Sync",
            fg_color="#0f766e",
            hover_color="#0d6e66",
            text_color="#ffffff",
            corner_radius=8,
            width=140,
            state="disabled",
            command=_confirm_sync,
        )
        confirm_btn.pack(side="left", padx=4)

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

        t = threading.Thread(target=_build_preview, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Step 5: Result
    # ------------------------------------------------------------------

    def _step_result(self) -> None:
        res = self._state.result
        ok = res.get("ok", False)
        s_count = res.get("success_count", 0)
        f_count = res.get("fail_count", 0)
        entry_count = res.get("entry_count", 0)
        total_amt = res.get("total_amount", 0.0)
        warnings = res.get("warnings", [])
        error = res.get("error", "")

        # Determine outcome type
        if ok and f_count == 0:
            outcome_type = "completed"
        elif ok and f_count > 0:
            outcome_type = "completed_with_warnings"
        elif s_count == 0 and f_count > 0:
            outcome_type = "failed_safely"
        else:
            outcome_type = "completed_with_warnings"

        if ok:
            title = "QB Sync Complete"
            summary = [
                f"{s_count} date/store combinations synced successfully.",
                f"{entry_count} receipts synced, total amount: ${total_amt:,.2f}",
                f"Stores: {', '.join(self._state.selected_stores)}",
                f"Date range: {self._state.date_start} to {self._state.date_end}",
            ]
            if not error:
                summary.append("Audit log saved.")
        else:
            title = "QB Sync Finished with Errors"
            summary = [
                f"{s_count} succeeded, {f_count} failed.",
                f"Stores: {', '.join(self._state.selected_stores)}",
            ]
            if error:
                summary.append(f"Error: {error}")

        # Build stats list
        stats = [
            ("Synced", str(s_count)),
            ("Entries Created", str(entry_count)),
        ]
        if total_amt > 0:
            stats.append(("Gross Sales", f"${total_amt:,.2f}"))

        def _done():
            if self._status_var is not None:
                self._status_var.set("navigate:home")

        def _go_home():
            if self._status_var is not None:
                self._status_var.set("navigate:home")

        def _go_download():
            if self._status_var is not None:
                self._status_var.set("navigate:wizard_download")

        self._result_view = WizardResultView(
            self._content_frame,
            outcome_type=outcome_type,
            title=title,
            summary_lines=summary,
            warnings=warnings,
            stats=stats,
            next_action_label="Return Home",
            next_action_command=_go_home,
            secondary_action_label="Download More Reports",
            secondary_action_command=_go_download,
            done_command=_done,
        )
        self._result_view.pack(fill="x", padx=20, pady=20)
        self.set_next_enabled(True)
