"""Preflight validation before running download or QB sync workflows."""
from __future__ import annotations
import os
from pathlib import Path
from models.validation_result import ValidationResult


def _runtime_dir() -> Path:
    try:
        from app_paths import RUNTIME_DIR
        return RUNTIME_DIR
    except Exception:
        return Path.cwd()


def validate_download_readiness(
    stores: list,
    date_start: str,
    date_end: str,
    report_types: list,
) -> ValidationResult:
    result = ValidationResult()

    result.add(
        "Stores selected",
        ok=bool(stores),
        message=f"{len(stores)} store(s) selected" if stores else "No stores selected",
        fix_hint="Select at least one store to continue.",
    )

    result.add(
        "Date range",
        ok=bool(date_start and date_end),
        message=f"{date_start} to {date_end}" if (date_start and date_end) else "No date range set",
        fix_hint="Select a start and end date.",
    )

    result.add(
        "Report types",
        ok=bool(report_types),
        message=f"{len(report_types)} type(s) selected" if report_types else "No report types selected",
        fix_hint="Select at least one report type.",
    )

    browser_ok = False
    browser_msg = "The report browser is not ready on this machine"
    browser_fix = "Run: playwright install chromium"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
        browser_ok = os.path.exists(str(exe))
        browser_msg = "Report browser is ready" if browser_ok else browser_msg
    except Exception:
        pass
    result.add("Report browser", ok=browser_ok, message=browser_msg, fix_hint=browser_fix)

    return result


def validate_qb_sync_readiness(
    stores: list,
    date_start: str,
    date_end: str,
) -> ValidationResult:
    result = ValidationResult()

    result.add(
        "Stores selected",
        ok=bool(stores),
        message=f"{len(stores)} store(s) selected" if stores else "No stores selected",
        fix_hint="Select at least one store to continue.",
    )

    result.add(
        "Date range",
        ok=bool(date_start and date_end),
        message=f"{date_start} to {date_end}" if (date_start and date_end) else "No date range set",
        fix_hint="Select a start and end date.",
    )

    qb_ok = False
    try:
        from diagnostics import _resolve_qb_executable
        exe = _resolve_qb_executable()
        qb_ok = bool(exe and os.path.exists(str(exe)))
    except Exception:
        pass
    result.add(
        "QuickBooks Desktop",
        ok=qb_ok,
        message="QuickBooks Desktop found" if qb_ok else "QuickBooks Desktop is not found on this machine",
        fix_hint="Install QuickBooks Desktop or check the path in Settings.",
    )

    rt = _runtime_dir()
    env_ok = (rt / ".env.qb").exists()
    result.add(
        "QB credentials",
        ok=env_ok,
        message="QB credentials file found" if env_ok else "QB credentials file is missing",
        fix_hint="Open Settings > QuickBooks to configure your QB password.",
    )

    creds_ok = (rt / "credentials.json").exists() and (rt / "token.json").exists()
    result.add(
        "Google Drive",
        ok=creds_ok,
        message="Google Drive is connected" if creds_ok else "Google Drive is not connected yet",
        fix_hint="Open Settings > Google Drive and connect your account.",
    )

    return result
