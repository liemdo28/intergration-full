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
