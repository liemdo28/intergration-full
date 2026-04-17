"""
UI state service — maps app state to client-safe display strings.
No raw technical language exposed in main UI.
"""


def get_feature_display_name(feature_key_value: str) -> str:
    """Returns client-safe display name for a feature key."""
    NAMES = {
        "report_download": "Download Reports",
        "google_drive": "Cloud Storage",
        "qb_sync": "QuickBooks Sync",
        "remove_tx": "Transaction Removal",
        "drive_coverage": "Drive Coverage Check",
        "marketplace": "Marketplace Integration",
        "home": "Home Dashboard",
        "recovery_center": "Recovery Center",
    }
    return NAMES.get(feature_key_value, feature_key_value.replace("_", " ").title())


def sanitize_error_for_display(raw_error: str) -> str:
    """
    Converts raw Python exception text into client-safe language.
    Strips file paths, module names, line numbers.
    """
    if not raw_error:
        return "An unexpected issue occurred."

    # Map known technical patterns to friendly messages
    PATTERNS = [
        ("playwright", "The report browser encountered an issue."),
        ("chromium", "The report browser encountered an issue."),
        ("FileNotFoundError", "A required file was not found."),
        ("PermissionError", "Access was denied to a required file or folder."),
        ("ConnectionError", "A network connection issue occurred."),
        ("TimeoutError", "The operation timed out."),
        ("JSONDecodeError", "A settings file is corrupted and needs repair."),
        ("token.json", "Google Drive authentication needs to be refreshed."),
        ("credentials.json", "Google Drive needs to be reconnected."),
        ("QB", "A QuickBooks connection issue occurred."),
        ("QBXML", "A QuickBooks connection issue occurred."),
    ]

    for keyword, message in PATTERNS:
        if keyword.lower() in raw_error.lower():
            return message

    # If none matched, return a generic message (never raw stack trace)
    if len(raw_error) > 120:
        return "An unexpected issue occurred. See Recovery Center for details."

    # Short errors that are somewhat readable can pass through
    return raw_error[:120]


def get_nav_theme() -> dict:
    """Returns the sidebar navigation theme configuration."""
    return {
        "home": {
            "title": "Home",
            "description": "Your operational dashboard and health summary.",
            "icon": "HM",
            "active_bg": "#6d28d9",
            "active_border": "#a78bfa",
        },
        "download": {
            "title": "Download",
            "description": "Pull Toast reports and save them cleanly.",
            "icon": "DL",
            "active_bg": "#2563eb",
            "active_border": "#60a5fa",
        },
        "qb": {
            "title": "QB Sync",
            "description": "Review and post sales into QuickBooks.",
            "icon": "QB",
            "active_bg": "#0f766e",
            "active_border": "#34d399",
        },
        "remove": {
            "title": "Remove",
            "description": "Find and clean up posted transactions.",
            "icon": "RM",
            "active_bg": "#b45309",
            "active_border": "#f59e0b",
        },
        "settings": {
            "title": "Settings",
            "description": "Control Drive, Toast, and app health.",
            "icon": "ST",
            "active_bg": "#475569",
            "active_border": "#94a3b8",
        },
        "recovery": {
            "title": "Recovery",
            "description": "Health checks, repair tools, and support export.",
            "icon": "RC",
            "active_bg": "#b45309",
            "active_border": "#f59e0b",
        },
        "audit": {
            "title": "Audit",
            "description": "Activity history and event log.",
            "icon": "AU",
            "active_bg": "#0f766e",
            "active_border": "#34d399",
        },
        "wizard_download": {
            "title": "Download Wizard",
            "description": "Guided report download",
            "icon": "↓",
            "active_bg": "#1e3a5f",
            "active_border": "#3b82f6",
        },
        "wizard_qb": {
            "title": "QB Sync Wizard",
            "description": "Guided QuickBooks sync",
            "icon": "⚙",
            "active_bg": "#14532d",
            "active_border": "#22c55e",
        },
    }


def get_diagnostics_status_display(report) -> dict:
    """
    Convert a diagnostics report to UI display data.
    Returns dict with: text, text_color, badge_fg, badge_border, status_bar_text
    """
    if report.error_count:
        return {
            "text": f"Environment: {report.error_count} error(s)",
            "text_color": "#fecaca",
            "badge_fg": "#3b1212",
            "badge_border": "#dc2626",
            "status_bar_text": "Environment issues detected. Open Settings > Startup Diagnostics.",
        }
    if report.warning_count:
        return {
            "text": f"Environment: {report.warning_count} warning(s)",
            "text_color": "#fde68a",
            "badge_fg": "#3f2f12",
            "badge_border": "#d97706",
            "status_bar_text": "Environment warnings detected. Open Settings > Startup Diagnostics.",
        }
    return {
        "text": "Environment: ready",
        "text_color": "#bbf7d0",
        "badge_fg": "#0f2f24",
        "badge_border": "#059669",
        "status_bar_text": "Ready",
    }


def format_status_for_statusbar(status: str) -> str:
    """
    Makes status bar messages client-safe.
    Converts technical messages to plain English.
    """
    REPLACEMENTS = {
        "token missing": "Google Drive needs to be reconnected",
        "token.json": "Google Drive authentication",
        "credentials.json": "Google Drive",
        "chromium": "report browser",
        "playwright": "report browser",
        "QB unavailable": "QuickBooks is not running",
        "QBXML": "QuickBooks",
        ".env.qb": "QB credentials",
        "AttributeError": "app issue",
        "Exception": "issue",
        "Traceback": "",
        "RuntimeError": "app issue",
    }
    result = status
    for tech, friendly in REPLACEMENTS.items():
        result = result.replace(tech, friendly)
    return result
