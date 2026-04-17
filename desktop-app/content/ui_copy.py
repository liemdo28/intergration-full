"""
ToastPOSManager — Operator-Friendly Copy Layer

All human-readable messages in the app pass through this module.
Replaces raw technical messages with plain-English guidance.

Rules:
  - Every message explains WHAT happened and WHAT TO DO NEXT
  - No raw exception text shown to operators
  - Advanced details available in diagnostics/support views only

Usage:
    from content.ui_copy import operator_msg, BLOCKER_MSG, NEXT_STEP

    label.configure(text=operator_msg("QB_NOT_FOUND"))
"""

from __future__ import annotations
from enum import Enum


class CopyKey(str, Enum):
    # Bootstrap / startup
    RUNTIME_NOT_WRITABLE   = "runtime_not_writable"
    CONFIG_CREATED_FIRST   = "config_created_first"
    CONFIG_REGENERATED     = "config_regenerated"

    # Feature readiness
    QB_NOT_FOUND           = "qb_not_found"
    QB_PASSWORD_MISSING    = "qb_password_missing"
    QB_NOT_CONFIGURED      = "qb_not_configured"
    QB_READY               = "qb_ready"
    CHROMIUM_MISSING       = "chromium_missing"
    CHROMIUM_READY         = "chromium_ready"
    DRIVE_NO_CREDENTIALS   = "drive_no_credentials"
    DRIVE_NO_TOKEN         = "drive_no_token"
    DRIVE_READY            = "drive_ready"
    MARKETPLACE_MISSING     = "marketplace_missing"
    MARKETPLACE_READY      = "marketplace_ready"

    # Wizard / first run
    WELCOME_TITLE          = "welcome_title"
    WELCOME_BODY           = "welcome_body"

    # Crash / recovery
    APP_CRASHED            = "app_crashed"
    BOOTSTRAP_FAILED      = "bootstrap_failed"
    SAFE_MODE_ACTIVE       = "safe_mode_active"
    SUPPORT_BUNDLE_READY   = "support_bundle_ready"

    # General
    NEXT_STEP_DEFAULT      = "next_step_default"
    CONTACT_IT             = "contact_it"

    # Wizard / workflow
    DOWNLOAD_BROWSER_NOT_READY = "download_browser_not_ready"
    DRIVE_NOT_CONNECTED        = "drive_not_connected"
    QB_NOT_RUNNING             = "qb_not_running"
    CONFIG_NEEDS_REPAIR        = "config_needs_repair"
    WIZARD_DOWNLOAD_INTRO      = "wizard_download_intro"
    WIZARD_QB_INTRO            = "wizard_qb_intro"

    # QB company file
    QB_COMPANY_FILE_NOT_SET    = "qb_company_file_not_set"
    QB_COMPANY_FILE_NOT_FOUND  = "qb_company_file_not_found"

    # Sync blockers
    SYNC_BLOCKED_MISSING_FILES = "sync_blocked_missing_files"
    SYNC_BLOCKED_DUPLICATE     = "sync_blocked_duplicate"

    # Download wizard
    DOWNLOAD_NO_DATES_SELECTED  = "download_no_dates_selected"
    DOWNLOAD_NO_STORES_SELECTED = "download_no_stores_selected"

    # Onboarding / completion
    FIRST_RUN_COMPLETE          = "first_run_complete"

    # Recovery
    RECOVERY_ACTION_SUCCESS     = "recovery_action_success"
    RECOVERY_EXPORT_SUCCESS     = "recovery_export_success"

    # Pre-sync validation
    PRESYNC_INVALID_FILE        = "presync_invalid_file"
    PRESYNC_DATE_GAP            = "presync_date_gap"


_MESSAGES: dict[CopyKey, dict[str, str]] = {
    CopyKey.RUNTIME_NOT_WRITABLE: {
        "title": "App folder is locked",
        "body": (
            "The app cannot write to its folder. This usually means it was placed "
            "in a protected location like Program Files.\n\n"
            "Move the app to a writable folder like Desktop or Documents, then try again."
        ),
        "next_step": "Move the app to a writable folder and relaunch.",
    },
    CopyKey.CONFIG_CREATED_FIRST: {
        "title": "Welcome! App is being set up",
        "body": "The app created its configuration files. You can customise them any time in Settings.",
        "next_step": "Click Next to continue with the setup wizard.",
    },
    CopyKey.CONFIG_REGENERATED: {
        "title": "Settings file was repaired",
        "body": (
            "Your app settings file was damaged. The app repaired it automatically by "
            "creating a fresh copy from the template.\n\n"
            "Your original file was backed up."
        ),
        "next_step": "Check Settings to re-enter any values that were in the backup file.",
    },
    CopyKey.QB_NOT_FOUND: {
        "title": "QuickBooks Desktop not found",
        "body": (
            "The app cannot find QuickBooks Desktop on this machine. "
            "QB Sync and Remove Transactions features need QuickBooks to be installed.\n\n"
            "These features will be unavailable on this machine."
        ),
        "next_step": "Open QuickBooks on this machine, or use the app on the workstation where QuickBooks is installed.",
        "support": "QB Sync features require QuickBooks Desktop to be installed on the same machine.",
    },
    CopyKey.QB_PASSWORD_MISSING: {
        "title": "QuickBooks password not set",
        "body": (
            "The app cannot sign in to QuickBooks because the password has not been entered.\n\n"
            "This does not block the app from running."
        ),
        "next_step": "Open Settings → QB Sync options and enter your QB_PASSWORD in the .env.qb file.",
        "support": "See README.md for how to set up the .env.qb file.",
    },
    CopyKey.QB_NOT_CONFIGURED: {
        "title": "QuickBooks not configured",
        "body": "No QuickBooks company file paths are set up yet.",
        "next_step": "Open Settings → QB Sync options and add your QB company file (.qbw) paths.",
    },
    CopyKey.QB_READY: {
        "title": "QuickBooks is ready",
        "body": "QuickBooks Desktop is installed and configured.",
        "next_step": "Select stores and a date range, then click Start QB Sync.",
    },
    CopyKey.CHROMIUM_MISSING: {
        "title": "Report browser is not ready",
        "body": (
            "The browser needed for automated report downloads is not bundled "
            "in this installation.\n\n"
            "This is a packaging issue — please re-download the app from the official release."
        ),
        "next_step": "Re-download the app from the official release. If this persists, contact IT support.",
        "support": "Run: python -m playwright install chromium  (developer only)",
    },
    CopyKey.CHROMIUM_READY: {
        "title": "Report browser is ready",
        "body": "The report browser (Chromium) is bundled and available.",
        "next_step": "Select a store and date range, then click Download Reports.",
    },
    CopyKey.DRIVE_NO_CREDENTIALS: {
        "title": "Google Drive not connected",
        "body": (
            "The app needs a Google Drive credentials file to use Drive features.\n\n"
            "This does not block Downloads or QB Sync."
        ),
        "next_step": "Place credentials.json in the app folder. Then open Settings → Google Drive → Connect.",
        "support": "Create a Google Cloud project, enable the Drive API, download credentials.json, and place it in the app folder.",
    },
    CopyKey.DRIVE_NO_TOKEN: {
        "title": "Google account not connected",
        "body": "The credentials file is present but the Google account has not been authorised yet.",
        "next_step": "Open Settings → Google Drive → Connect Google Drive and follow the browser sign-in prompt.",
    },
    CopyKey.DRIVE_READY: {
        "title": "Google Drive is connected",
        "body": "Google Drive is authorised and ready for uploads and coverage scanning.",
        "next_step": "Use Settings → Drive Inventory to scan coverage, or enable Drive Upload in QB Sync options.",
    },
    CopyKey.MARKETPLACE_MISSING: {
        "title": "Marketplace files not set up",
        "body": "No marketplace CSV file paths are configured yet.",
        "next_step": "Open Settings → Marketplace Uploads and add your Uber, DoorDash, or Grubhub CSV paths.",
    },
    CopyKey.MARKETPLACE_READY: {
        "title": "Marketplace uploads are configured",
        "body": "Marketplace CSV files are set up for your stores.",
        "next_step": "Run QB Sync to include marketplace data in your sales receipts.",
    },
    CopyKey.WELCOME_TITLE: {
        "title": "Welcome to ToastPOSManager",
        "body": (
            "This app automates Toast report downloads, QuickBooks syncing, "
            "and Google Drive uploads.\n\n"
            "This quick setup will take about 2 minutes. You can change everything later in Settings."
        ),
        "next_step": "Click Next to begin.",
    },
    CopyKey.APP_CRASHED: {
        "title": "App closed unexpectedly",
        "body": (
            "The app encountered an error and had to close.\n\n"
            "A support bundle has been saved. Please share it with your IT contact."
        ),
        "next_step": "Click OK to open Recovery Center, or relaunch the app.",
    },
    CopyKey.BOOTSTRAP_FAILED: {
        "title": "App could not start",
        "body": (
            "The app's startup preparation failed. A crash report was saved.\n\n"
            "Please share the crash bundle with your IT contact."
        ),
        "next_step": "Relaunch the app. If it fails again, contact IT support.",
    },
    CopyKey.SAFE_MODE_ACTIVE: {
        "title": "Safe Mode is active",
        "body": (
            "The app started in Safe Mode because it did not close cleanly last time. "
            "Background actions are disabled to protect your data.\n\n"
            "Safe Mode will clear automatically after a successful session."
        ),
        "next_step": "Use the app normally. Safe Mode will turn off after one clean exit.",
    },
    CopyKey.SUPPORT_BUNDLE_READY: {
        "title": "Support bundle ready",
        "body": "A support bundle was created and saved to the crash-reports folder.",
        "next_step": "Email this file to your IT contact.",
    },
    CopyKey.NEXT_STEP_DEFAULT: {
        "title": "App is ready",
        "body": "All core features are configured.",
        "next_step": "Choose an action from the Quick Actions panel.",
    },
    CopyKey.CONTACT_IT: {
        "title": "Need help?",
        "body": "Export a support bundle from Recovery Center and share it with your IT contact.",
        "next_step": "Recovery Center → Export Support Bundle.",
    },
    CopyKey.DOWNLOAD_BROWSER_NOT_READY: {
        "title": "The report browser is not ready",
        "body": (
            "The automated browser (Chromium) used to download reports is not installed "
            "or configured on this machine."
        ),
        "next_step": "Contact your IT support, or run: playwright install chromium",
    },
    CopyKey.DRIVE_NOT_CONNECTED: {
        "title": "Google Drive is not connected yet",
        "body": (
            "The app needs access to your Google Drive account to read and store report files. "
            "No credentials were found."
        ),
        "next_step": "Open Settings > Google Drive and connect your Google account.",
    },
    CopyKey.QB_NOT_RUNNING: {
        "title": "QuickBooks features are unavailable",
        "body": (
            "QuickBooks Desktop is not running or not installed on this machine. "
            "The sync feature requires QuickBooks to be open."
        ),
        "next_step": "Open QuickBooks Desktop, then click Refresh in the app.",
    },
    CopyKey.CONFIG_NEEDS_REPAIR: {
        "title": "Your app settings need repair",
        "body": (
            "One or more required settings are missing or contain invalid values. "
            "The app cannot proceed until these are fixed."
        ),
        "next_step": "Open Recovery Center and use Repair Config to restore default settings.",
    },
    CopyKey.WIZARD_DOWNLOAD_INTRO: {
        "title": "Download Reports",
        "body": (
            "This wizard guides you through downloading Toast POS reports for one or more stores. "
            "You will select stores, choose a date range, and the app will handle the rest."
        ),
        "next_step": "Click Next to select stores.",
    },
    CopyKey.WIZARD_QB_INTRO: {
        "title": "Sync to QuickBooks",
        "body": (
            "This wizard guides you through syncing your Toast report data into QuickBooks Desktop. "
            "You will see exactly what will be synced before anything is committed."
        ),
        "next_step": "Click Next to select stores.",
    },
    CopyKey.QB_COMPANY_FILE_NOT_SET: {
        "title": "QuickBooks company file is not configured",
        "body": "This store's QuickBooks company file (.qbw) has not been set up in the app yet.",
        "next_step": "Open Settings > QuickBooks and point the app at the correct .qbw file.",
    },
    CopyKey.QB_COMPANY_FILE_NOT_FOUND: {
        "title": "QuickBooks company file could not be found",
        "body": "The QuickBooks company file path is configured, but the file no longer exists at that location.",
        "next_step": "Open Settings > QuickBooks and re-select the company file.",
    },
    CopyKey.SYNC_BLOCKED_MISSING_FILES: {
        "title": "This sync cannot continue because required source files are missing",
        "body": "One or more required Sale Summary files are missing from Google Drive for the selected dates.",
        "next_step": "Download the missing reports first, then return to sync.",
    },
    CopyKey.SYNC_BLOCKED_DUPLICATE: {
        "title": "This sync is blocked because a potential duplicate was detected",
        "body": "One or more of the selected dates appears to have already been synced to QuickBooks.",
        "next_step": "Review the sync history in the Activity Log before proceeding.",
    },
    CopyKey.DOWNLOAD_NO_DATES_SELECTED: {
        "title": "No date range selected",
        "body": "Select a start and end date before running the download.",
        "next_step": "Choose a date range in step 2.",
    },
    CopyKey.DOWNLOAD_NO_STORES_SELECTED: {
        "title": "No stores selected",
        "body": "At least one store must be selected before downloading reports.",
        "next_step": "Select one or more stores in step 1.",
    },
    CopyKey.FIRST_RUN_COMPLETE: {
        "title": "Setup complete",
        "body": "Your app is configured and ready to use. Start by downloading your most recent reports.",
        "next_step": "Click Download Reports to get started.",
    },
    CopyKey.RECOVERY_ACTION_SUCCESS: {
        "title": "Recovery action completed",
        "body": "The selected recovery action ran successfully. Check the Recovery Center for current health status.",
        "next_step": "Return to the app and verify the fixed feature is now working.",
    },
    CopyKey.RECOVERY_EXPORT_SUCCESS: {
        "title": "Support bundle exported",
        "body": "A support bundle containing app logs and health information has been saved. Share this file with support if requested.",
        "next_step": "Attach the bundle file to your support request.",
    },
    CopyKey.PRESYNC_INVALID_FILE: {
        "title": "A report file appears to be corrupted or incomplete",
        "body": "One or more report files cannot be read correctly. Syncing from a corrupted file may create incorrect accounting entries.",
        "next_step": "Re-download the affected report, then verify it before syncing.",
    },
    CopyKey.PRESYNC_DATE_GAP: {
        "title": "A gap was detected in report date coverage",
        "body": "There are missing dates in the selected report range. Syncing with gaps may leave accounting records incomplete.",
        "next_step": "Download the missing dates before syncing.",
    },
}


def operator_msg(key: CopyKey) -> dict[str, str]:
    """
    Return all parts of a copy message for a key.
    Usage: msg = operator_msg(CopyKey.QB_NOT_FOUND)
           title_lbl.configure(text=msg["title"])
           body_lbl.configure(text=msg["body"])
    """
    return _MESSAGES.get(key, {
        "title": "Notice",
        "body": "No details available. Contact IT support.",
        "next_step": "Open Recovery Center → Export Support Bundle.",
    })


def title(key: CopyKey) -> str:
    return operator_msg(key)["title"]


def body(key: CopyKey) -> str:
    return operator_msg(key)["body"]


def next_step(key: CopyKey) -> str:
    return operator_msg(key)["next_step"]
