"""
ToastPOSManager — Feature Readiness Model

Standardised readiness contract for every feature in the app.
Every feature returns the same shape so the Home Dashboard and
Settings Readiness panel can render them uniformly.

Usage:
    from models.feature_readiness import FeatureReadiness, FeatureKey

    fr = FeatureReadiness(
        feature_key=FeatureKey.QB_SYNC,
        status=Status.BLOCKED,
        reason="QuickBooks Desktop not found on this machine.",
        next_step="Install or open QuickBooks Desktop, then restart the app.",
        is_blocking=True,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class FeatureKey(str, Enum):
    HOME              = "home"
    REPORT_DOWNLOAD   = "report_download"
    GOOGLE_DRIVE      = "google_drive"
    QB_SYNC           = "qb_sync"
    REMOVE_TX         = "remove_transactions"
    DRIVE_COVERAGE    = "drive_coverage"
    RECOVERY_CENTER   = "recovery_center"
    MARKETPLACE       = "marketplace"


class ReadinessStatus(str, Enum):
    READY    = "ready"      # all requirements met
    PARTIAL  = "partial"    # partially configured (warn, not block)
    BLOCKED  = "blocked"    # cannot use this feature
    WARNING  = "warning"     # caution, may need attention
    UNKNOWN  = "unknown"     # not yet checked


@dataclass
class FeatureReadiness:
    """
    Standard readiness record for one app feature.

    Attributes
    ----------
    feature_key : FeatureKey
        Which feature this record describes.
    status : ReadinessStatus
        Overall readiness state.
    reason : str
        Plain-English reason for the current state.
        Must answer: "Why am I seeing this status?"
    next_step : str
        Plain-English next action the operator should take.
        Must answer: "What should I do right now?"
    support_hint : str | None
        Optional hint shown in a support context.
        Example: "Run Refresh Drive Inventory in Settings."
    is_blocking : bool
        True if this feature's blockers prevent the app from
        doing useful work entirely (rare — most features degrade gracefully).
    checked_at : str
        ISO timestamp when this readiness was evaluated.
    extra : dict | None
        Arbitrary key/value for feature-specific detail.
    """

    feature_key: FeatureKey
    status: ReadinessStatus
    reason: str
    next_step: str = ""
    support_hint: str | None = None
    is_blocking: bool = False
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat())
    extra: dict | None = None

    # Convenience class attribute for renderers
    STATUS_ORDER = {
        ReadinessStatus.BLOCKED: 0,
        ReadinessStatus.WARNING:  1,
        ReadinessStatus.UNKNOWN:  2,
        ReadinessStatus.PARTIAL:  3,
        ReadinessStatus.READY:    4,
    }

    @property
    def priority(self) -> int:
        """Lower = more urgent. Used for sorting."""
        return self.STATUS_ORDER.get(self.status, 99)

    def to_dict(self) -> dict:
        return {
            "feature_key": self.feature_key.value,
            "status": self.status.value,
            "reason": self.reason,
            "next_step": self.next_step,
            "support_hint": self.support_hint,
            "is_blocking": self.is_blocking,
            "checked_at": self.checked_at,
            "extra": self.extra or {},
        }
