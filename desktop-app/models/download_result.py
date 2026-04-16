"""Download job result model."""
from dataclasses import dataclass, field


@dataclass
class DownloadFileResult:
    store: str
    date: str
    report_type: str
    success: bool
    file_path: str = ""
    error: str = ""
    uploaded_to_drive: bool = False


@dataclass
class DownloadResult:
    stores: list = field(default_factory=list)
    date_start: str = ""
    date_end: str = ""
    report_types: list = field(default_factory=list)
    files: list = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    warnings: list = field(default_factory=list)
    audit_log_path: str = ""

    @property
    def success_count(self) -> int:
        return sum(1 for f in self.files if f.success)

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.files if not f.success)

    @property
    def total_count(self) -> int:
        return len(self.files)

    @property
    def ok(self) -> bool:
        return self.fail_count == 0 and self.total_count > 0
