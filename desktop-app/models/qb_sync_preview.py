"""QB Sync preview/estimate model."""
from dataclasses import dataclass, field


@dataclass
class QBSyncPreviewEntry:
    store: str
    date: str
    file_name: str
    estimated_receipts: int
    total_amount: float
    warnings: list = field(default_factory=list)


@dataclass
class QBSyncPreview:
    stores: list = field(default_factory=list)
    date_start: str = ""
    date_end: str = ""
    entries: list = field(default_factory=list)
    total_estimated_receipts: int = 0
    total_estimated_amount: float = 0.0
    warnings: list = field(default_factory=list)
    can_proceed: bool = True
    block_reason: str = ""
