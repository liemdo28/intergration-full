"""
ToastPOSManager — Feature Readiness Service

Single source of truth for all feature readiness checks.
Used by Home Dashboard, Settings Readiness panel, and diagnostics.

Every feature returns a FeatureReadiness dataclass so the UI has
a uniform contract: status + reason + next_step.

Rules
-----
- A missing dependency MUST NOT crash the app.
- A missing dependency returns BLOCKED or WARNING, never an exception.
- One blocked feature MUST NOT block other features.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from models.feature_readiness import (
    FeatureKey,
    FeatureReadiness,
    ReadinessStatus,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers (same pattern as app_paths)
# ---------------------------------------------------------------------------
def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


BUNDLE_DIR = _resolve_bundle_dir()
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE_DIR


def _runtime_path(*parts: str) -> Path:
    return RUNTIME_DIR.joinpath(*parts)


# ---------------------------------------------------------------------------
# Per-feature checks
# ---------------------------------------------------------------------------

def _check_download() -> FeatureReadiness:
    """Report Download readiness (Playwright + Chromium)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            bp = Path(pw.chromium.executable_path)
            if bp.exists():
                return FeatureReadiness(
                    feature_key=FeatureKey.REPORT_DOWNLOAD,
                    status=ReadinessStatus.READY,
                    reason="The report browser (Chromium) is bundled and ready.",
                    next_step="Select a store and date range, then click Download Reports.",
                    is_blocking=False,
                )
            else:
                return FeatureReadiness(
                    feature_key=FeatureKey.REPORT_DOWNLOAD,
                    status=ReadinessStatus.BLOCKED,
                    reason="The report browser (Chromium) is not bundled in this release.",
                    next_step="Re-download the app or contact IT support with a support bundle.",
                    support_hint="Run: python -m playwright install chromium  (developer only)",
                    is_blocking=False,
                )
    except ImportError:
        return FeatureReadiness(
            feature_key=FeatureKey.REPORT_DOWNLOAD,
            status=ReadinessStatus.BLOCKED,
            reason="Playwright browser library is not available.",
            next_step="This installation of the app is incomplete. Reinstall from the official release.",
            is_blocking=False,
        )
    except Exception as exc:
        return FeatureReadiness(
            feature_key=FeatureKey.REPORT_DOWNLOAD,
            status=ReadinessStatus.WARNING,
            reason=f"Could not verify browser readiness: {exc}",
            next_step="Try running Download Reports. If it fails, export a support bundle from Recovery Center.",
            is_blocking=False,
        )


def _check_qb_sync() -> FeatureReadiness:
    """Report QB Sync readiness."""
    # 1. QB installed?
    qb_exe: Path | None = None
    try:
        from qb_automate import resolve_qb_executable
        result = resolve_qb_executable()
        if result and Path(result).exists():
            qb_exe = Path(result)
    except Exception:
        pass

    if not qb_exe:
        return FeatureReadiness(
            feature_key=FeatureKey.QB_SYNC,
            status=ReadinessStatus.BLOCKED,
            reason="QuickBooks Desktop was not found on this machine.",
            next_step="Install QuickBooks Desktop on this machine, or use this app on the QB workstation.",
            support_hint="QB Sync features require QuickBooks to be installed.",
            is_blocking=False,
        )

    # 2. QB passwords configured?
    env_path = _runtime_path(".env.qb")
    has_password = False
    if env_path.exists():
        try:
            text = env_path.read_text(encoding="utf-8", errors="replace")
            has_password = any("QB_PASSWORD" in line for line in text.splitlines() if "=" in line)
        except Exception:
            pass

    if not has_password:
        return FeatureReadiness(
            feature_key=FeatureKey.QB_SYNC,
            status=ReadinessStatus.WARNING,
            reason="QB passwords are not configured. QB Sync will use blank credentials.",
            next_step="Open Settings → QB Sync options and enter at least one QB_PASSWORD in .env.qb.",
            support_hint="See README.md for .env.qb setup instructions.",
            is_blocking=False,
        )

    # 3. QBW paths in local-config?
    cfg_path = _runtime_path("local-config.json")
    has_qbw = False
    if cfg_path.exists():
        try:
            import json
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            qbw_paths = data.get("qbw_paths", {})
            has_qbw = bool(qbw_paths and any(Path(p).exists() for p in qbw_paths.values() if isinstance(p, str)))
        except Exception:
            pass

    if not has_qbw:
        return FeatureReadiness(
            feature_key=FeatureKey.QB_SYNC,
            status=ReadinessStatus.PARTIAL,
            reason="QBW paths are not yet configured for any store.",
            next_step="Open Settings → QB Sync options and add at least one QB company file path.",
            is_blocking=False,
        )

    return FeatureReadiness(
        feature_key=FeatureKey.QB_SYNC,
        status=ReadinessStatus.READY,
        reason="QuickBooks Desktop is installed and configured.",
        next_step="Select stores and a date range, then click Start QB Sync.",
        is_blocking=False,
    )


def _check_drive() -> FeatureReadiness:
    """Report Google Drive readiness."""
    cred_path = _runtime_path("credentials.json")
    token_path = _runtime_path("token.json")

    if not cred_path.exists():
        return FeatureReadiness(
            feature_key=FeatureKey.GOOGLE_DRIVE,
            status=ReadinessStatus.BLOCKED,
            reason="Google Drive credentials file not found.",
            next_step="Place credentials.json from Google Cloud Console in the app folder. See README.md.",
            support_hint="Drive features require a Google Cloud project with Drive API enabled.",
            is_blocking=False,
        )

    if not token_path.exists():
        return FeatureReadiness(
            feature_key=FeatureKey.GOOGLE_DRIVE,
            status=ReadinessStatus.PARTIAL,
            reason="Google account not yet connected.",
            next_step="Open Settings → Google Drive → Connect Google Drive to authorize.",
            is_blocking=False,
        )

    return FeatureReadiness(
        feature_key=FeatureKey.GOOGLE_DRIVE,
        status=ReadinessStatus.READY,
        reason="Google Drive is connected and ready.",
        next_step="Use Drive Inventory in Settings to scan coverage, or enable auto-upload in QB Sync options.",
        is_blocking=False,
    )


def _check_remove_tx() -> FeatureReadiness:
    """Report Remove Transactions readiness (same dependency as QB Sync)."""
    try:
        from qb_automate import resolve_qb_executable
        result = resolve_qb_executable()
        if result and Path(result).exists():
            return FeatureReadiness(
                feature_key=FeatureKey.REMOVE_TX,
                status=ReadinessStatus.READY,
                reason="QuickBooks Desktop is available.",
                next_step="Open Remove Transactions, select a store/date range, and follow the guided steps.",
                is_blocking=False,
            )
    except Exception:
        pass
    return FeatureReadiness(
        feature_key=FeatureKey.REMOVE_TX,
        status=ReadinessStatus.BLOCKED,
        reason="QuickBooks Desktop not found. Remove Transactions requires QB.",
        next_step="Use this app on the machine where QuickBooks is installed.",
        is_blocking=False,
    )


def _check_drive_coverage() -> FeatureReadiness:
    """Report Drive Coverage feature readiness."""
    if not _runtime_path("credentials.json").exists():
        return FeatureReadiness(
            feature_key=FeatureKey.DRIVE_COVERAGE,
            status=ReadinessStatus.BLOCKED,
            reason="Google Drive not connected.",
            next_step="Connect Google Drive in Settings first.",
            is_blocking=False,
        )
    return FeatureReadiness(
        feature_key=FeatureKey.DRIVE_COVERAGE,
        status=ReadinessStatus.READY,
        reason="Drive Coverage is available.",
        next_step="Open Settings → Drive Inventory Center and click Refresh Drive Inventory.",
        is_blocking=False,
    )


def _check_marketplace() -> FeatureReadiness:
    """Report Marketplace Uploads readiness."""
    cfg_path = _runtime_path("local-config.json")
    has_mp = False
    if cfg_path.exists():
        try:
            import json
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            mp_paths = data.get("marketplace_paths", {})
            has_mp = bool(mp_paths)
        except Exception:
            pass
    if not has_mp:
        return FeatureReadiness(
            feature_key=FeatureKey.MARKETPLACE,
            status=ReadinessStatus.PARTIAL,
            reason="No marketplace CSV paths configured yet.",
            next_step="Open Settings → Marketplace Uploads and add at least one CSV path per store.",
            is_blocking=False,
        )
    return FeatureReadiness(
        feature_key=FeatureKey.MARKETPLACE,
        status=ReadinessStatus.READY,
        reason="Marketplace uploads are configured.",
        next_step="Run QB Sync to include marketplace data in your sales receipts.",
        is_blocking=False,
    )


def _check_home() -> FeatureReadiness:
    """Home is always ready — it just reflects other feature states."""
    return FeatureReadiness(
        feature_key=FeatureKey.HOME,
        status=ReadinessStatus.READY,
        reason="Home dashboard is always available.",
        next_step="Review Today's Readiness below and choose an action.",
        is_blocking=False,
    )


def _check_recovery_center() -> FeatureReadiness:
    """Recovery Center is always available as a troubleshooting hub."""
    return FeatureReadiness(
        feature_key=FeatureKey.RECOVERY_CENTER,
        status=ReadinessStatus.READY,
        reason="Recovery Center is always available.",
        next_step="Click Recovery Center below to view health, export a support bundle, or repair settings.",
        is_blocking=False,
    )


# ---------------------------------------------------------------------------
# Master check — all features
# ---------------------------------------------------------------------------

_CHECKS: dict[FeatureKey, callable] = {
    FeatureKey.HOME:             _check_home,
    FeatureKey.REPORT_DOWNLOAD:  _check_download,
    FeatureKey.GOOGLE_DRIVE:    _check_drive,
    FeatureKey.QB_SYNC:         _check_qb_sync,
    FeatureKey.REMOVE_TX:        _check_remove_tx,
    FeatureKey.DRIVE_COVERAGE:  _check_drive_coverage,
    FeatureKey.MARKETPLACE:     _check_marketplace,
    FeatureKey.RECOVERY_CENTER: _check_recovery_center,
}


def check_feature(key: FeatureKey) -> FeatureReadiness:
    """Return readiness for one feature. Never raises."""
    checker = _CHECKS.get(key)
    if not checker:
        return FeatureReadiness(
            feature_key=key,
            status=ReadinessStatus.UNKNOWN,
            reason=f"No readiness check defined for {key.value}.",
            next_step="Contact IT support.",
            is_blocking=False,
        )
    try:
        return checker()
    except Exception as exc:
        _log.warning(f"Readiness check for {key.value} raised: {exc}")
        return FeatureReadiness(
            feature_key=key,
            status=ReadinessStatus.WARNING,
            reason=f"Readiness check for {key.value} failed: {exc}",
            next_step="Try the feature directly. If it fails, use Recovery Center.",
            is_blocking=False,
        )


def check_all_features() -> dict[FeatureKey, FeatureReadiness]:
    """Return readiness for all features. Never raises."""
    results = {}
    for key in FeatureKey:
        results[key] = check_feature(key)
    return results


def get_smart_recommendation() -> FeatureReadiness | None:
    """
    Returns the most actionable next step for the operator.
    Priority order:
      1. Infrastructure blocked (browser missing, QB missing, Drive not connected)
      2. Missing reports in Drive for recent dates → suggest download
      3. Reports available but not yet synced → suggest QB sync
      4. All clear

    Falls back gracefully — never raises.
    """
    # First check infrastructure
    all_features = check_all_features()
    blocked = [f for f in all_features.values() if f.status == ReadinessStatus.BLOCKED and f.is_blocking]
    if blocked:
        blocked.sort(key=lambda f: f.priority)
        return blocked[0]

    # Check for missing recent reports in Drive
    try:
        missing = _check_recent_drive_coverage()
        if missing:
            return FeatureReadiness(
                feature_key=FeatureKey.REPORT_DOWNLOAD,
                status=ReadinessStatus.WARNING,
                reason=f"{missing} report file(s) are missing from Google Drive for recent dates.",
                next_step="Open Download Wizard to download missing reports.",
                is_blocking=False,
            )
    except Exception:
        pass

    # Check for reports that exist in Drive but haven't been synced to QB
    try:
        ready_for_sync = _check_reports_ready_for_sync()
        if ready_for_sync:
            return FeatureReadiness(
                feature_key=FeatureKey.QB_SYNC,
                status=ReadinessStatus.WARNING,
                reason=f"{ready_for_sync} report(s) in Drive are ready but not yet synced to QuickBooks.",
                next_step="Open QB Sync Wizard to sync pending reports.",
                is_blocking=False,
            )
    except Exception:
        pass

    # Check non-blocking issues
    non_ready = [f for f in all_features.values() if f.status != ReadinessStatus.READY]
    if non_ready:
        non_ready.sort(key=lambda f: f.priority)
        return non_ready[0]

    return None


def _check_recent_drive_coverage() -> int:
    """Returns count of missing report files in Drive for past 7 days. 0 if drive not ready or all present."""
    try:
        from datetime import date, timedelta
        from report_inventory import get_drive_inventory_summary

        today = date.today()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 8)]
        summary = get_drive_inventory_summary()
        missing = summary.get("missing_count", 0)
        return missing
    except Exception:
        return 0


def _check_reports_ready_for_sync() -> int:
    """Returns count of Drive reports not yet synced to QB. 0 if can't determine."""
    try:
        from sync_ledger import count_pending_sync
        return count_pending_sync()
    except Exception:
        return 0


def get_most_urgent() -> FeatureReadiness | None:
    """Alias for get_smart_recommendation() for backward compatibility."""
    return get_smart_recommendation()
