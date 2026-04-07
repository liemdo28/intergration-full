from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_FOLDER_NAME = "Toasttab"
LEGACY_ROOT_FOLDER_NAMES = ("Toast Reports",)


@dataclass(frozen=True)
class ToastReportType:
    key: str
    label: str
    folder_name: str
    landing_path: str
    tab_label: str | None
    validation_profile: str = "tabular"


REPORT_TYPES: dict[str, ToastReportType] = {
    "sales_summary": ToastReportType(
        key="sales_summary",
        label="Sale Summary",
        folder_name="Sale Summary",
        landing_path="sales/sales-summary",
        tab_label="Sales Summary",
        validation_profile="sales_summary",
    ),
    "order": ToastReportType(
        key="order",
        label="Order",
        folder_name="Order",
        landing_path="sales/sales-summary",
        tab_label="Orders",
    ),
    "item_detail": ToastReportType(
        key="item_detail",
        label="Item Detail",
        folder_name="Item Detail",
        landing_path="menus/product-mix",
        tab_label="Item Details",
    ),
    "payment": ToastReportType(
        key="payment",
        label="Payment",
        folder_name="Payment",
        landing_path="sales/sales-summary",
        tab_label="Payments",
    ),
}


DEFAULT_REPORT_TYPE_KEYS = tuple(REPORT_TYPES.keys())


def get_report_type(report_type: str | ToastReportType) -> ToastReportType:
    if isinstance(report_type, ToastReportType):
        return report_type
    try:
        return REPORT_TYPES[report_type]
    except KeyError as exc:
        raise ValueError(f"Unknown Toast report type: {report_type}") from exc


def normalize_report_types(report_types: list[str] | tuple[str, ...] | None) -> list[ToastReportType]:
    keys = list(report_types or ["sales_summary"])
    normalized: list[ToastReportType] = []
    seen: set[str] = set()
    for key in keys:
        report = get_report_type(key)
        if report.key in seen:
            continue
        seen.add(report.key)
        normalized.append(report)
    return normalized


def build_local_report_dir(base_dir: str | Path, store_name: str, report_type: str | ToastReportType) -> Path:
    report = get_report_type(report_type)
    return Path(base_dir) / str(store_name) / report.folder_name
