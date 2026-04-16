"""
ToastPOSManager — Launcher Wrapper

Starts the app with full bootstrap validation.
Handles fatal startup errors with crash logging and a friendly recovery dialog.
Can launch in safe mode (skip background workers, open Settings first).

Usage:
    python launcher.py              # normal boot
    python launcher.py --safe      # safe mode (dev/debug)
    python launcher.py --wizard    # force first-run wizard
"""

from __future__ import annotations
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path

# ---- Ensure we can import from the same package ----
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bootstrap_runtime import run_bootstrap, RUNTIME_DIR, APP_DIR
from safe_mode import activate_from_bootstrap_report, deactivate_safe_mode

_log = logging.getLogger("launcher")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Returns 0 on success, non-zero on fatal failure."""
    _setup_logging()
    _log.info("=== ToastPOSManager launcher starting ===")
    _log.info(f"sys.executable={sys.executable}")
    _log.info(f"frozen={getattr(sys, 'frozen', False)}")
    _log.info(f"RUNTIME_DIR={RUNTIME_DIR}")
    _log.info(f"APP_DIR={APP_DIR}")

    safe_mode = "--safe" in sys.argv
    force_wizard = "--wizard" in sys.argv

    try:
        report = run_bootstrap()
    except Exception as exc:
        _log.critical(f"Bootstrap itself crashed: {exc}", exc_info=True)
        _write_crash_dump("bootstrap_failure", str(exc), traceback.format_exc())
        _show_fatal_dialog("Bootstrap Failed",
            f"The app's startup preparation failed:\n\n{exc}\n\n"
            f"A crash report was saved to:\n{RUNTIME_DIR / 'logs'}")
        return 1

    _log.info(f"Bootstrap result: can_run={report.can_run} "
              f"is_first_run={report.is_first_run} portable={report.portable_mode}")

    # --- Activate safe mode based on bootstrap + crash markers ---
    activate_from_bootstrap_report(report)  # noqa: F811 — local alias is intentional

    # --- Save report for app to read ---
    try:
        from bootstrap_runtime import BootstrapReport
        import json
        report_out = RUNTIME_DIR / "bootstrap_report.json"
        report_out.write_text(
            json.dumps({
                "can_run": report.can_run,
                "is_first_run": report.is_first_run,
                "portable_mode": report.portable_mode,
                "summary": report.summary(),
                "blockers": [{"name": i.name, "message": i.message} for i in report.blockers],
                "warnings": [{"name": i.name, "message": i.message} for i in report.warnings],
                "bootstrap_time": report.bootstrap_time,
            }, indent=2),
            encoding="utf-8"
        )
        _log.info(f"Bootstrap report written to {report_out}")
    except Exception as exc:
        _log.warning(f"Could not write bootstrap report: {exc}")

    if not report.can_run:
        _log.error(f"Bootstrap blockers: {[i.message for i in report.blockers]}")
        blocker_msg = "\n".join(f"  • {i.message}" for i in report.blockers)
        from crash_reporter import generate_support_bundle
        bundle_path = generate_support_bundle(
            title="App Startup Failed",
            description=f"Bootstrap found {len(report.blockers)} blocker(s):\n{blocker_msg}",
            fatal=True,
        )
        _show_fatal_dialog(
            "Cannot Start App",
            f"The app cannot start due to the following blocker(s):\n\n{blocker_msg}\n\n"
            f"A support bundle was saved to:\n{bundle_path}\n\n"
            f"Please share this with your IT contact.",
            details=blocker_msg,
        )
        return 1

    if report.is_first_run or force_wizard:
        _log.info("First run detected — launching setup wizard")
        return _launch_wizard()

    if safe_mode:
        _log.info("Safe mode requested — launching app in safe mode")
        return _launch_app(safe_mode=True)

    return _launch_app(safe_mode=False)


def _setup_logging() -> None:
    log_dir = RUNTIME_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"launcher_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _launch_app(safe_mode: bool = False) -> int:
    """Import and run app.py. safe_mode=True opens Settings first and skips workers."""
    import atexit
    _log.info("Launching app...")
    try:
        import app
        # Pass safe_mode flag via environment or module attribute
        if safe_mode:
            app._SAFE_MODE = True
            _log.info("App running in safe mode")
        # Register safe-mode deactivation on clean exit (clears crash markers)
        atexit.register(deactivate_safe_mode)
        app.main()
        return 0
    except Exception as exc:
        _log.critical(f"App crashed during launch: {exc}", exc_info=True)
        tb = traceback.format_exc()
        crash_file = _write_crash_dump("app_crash", str(exc), tb)
        _show_fatal_dialog(
            "App Crashed",
            f"The app crashed during startup:\n\n{exc}\n\n"
            f"Crash details saved to:\n{crash_file}",
            details=tb,
        )
        return 1


def _launch_wizard() -> int:
    """Import and run the first-run wizard."""
    _log.info("Launching first-run wizard...")
    try:
        import first_run_wizard
        first_run_wizard.run()
        # After wizard completes, run the app
        return _launch_app(safe_mode=False)
    except Exception as exc:
        _log.critical(f"Wizard crashed: {exc}", exc_info=True)
        tb = traceback.format_exc()
        _write_crash_dump("wizard_crash", str(exc), tb)
        _show_fatal_dialog(
            "Setup Wizard Failed",
            f"The setup wizard failed:\n\n{exc}\n\n"
            f"The app may still run — click OK to continue.",
            details=tb,
        )
        # Don't block launch — continue to app
        return _launch_app(safe_mode=False)


def _write_crash_dump(reason: str, message: str, traceback_str: str) -> Path:
    from crash_reporter import generate_support_bundle
    return generate_support_bundle(
        title=f"Startup Crash: {reason}",
        description=f"{reason}: {message}",
        fatal=True,
        extra_traceback=traceback_str,
    )


def _show_fatal_dialog(title: str, message: str, details: str | None = None) -> None:
    """Show a Tkinter error dialog. Fall back to ctypes MessageBox if tkinter unavailable."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        # Fallback to Windows MessageBox via ctypes
        try:
            import ctypes
            MB_ICONERROR = 0x10
            ctypes.windll.user32.MessageBoxW(0, message, title, MB_ICONERROR)
        except Exception:
            print(f"FATAL: {title}\n{message}", file=sys.stderr)
            if details:
                print(details, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
