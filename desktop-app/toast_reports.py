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
    aliases: tuple[str, ...] = ()
    folder_aliases: tuple[str, ...] = ()
    stem_aliases: tuple[str, ...] = ()


REPORT_TYPES: dict[str, ToastReportType] = {
    "sales_summary": ToastReportType(
        key="sales_summary",
        label="Sale Summary",
        folder_name="Sale Summary",
        landing_path="sales/sales-summary",
        tab_label="Sales Summary",
        validation_profile="sales_summary",
        stem_aliases=("salessummary", "sales_summary", "sale_summary"),
    ),
    "orders": ToastReportType(
        key="orders",
        label="Orders",
        folder_name="Order",
        landing_path="sales/sales-summary",
        tab_label="Orders",
        aliases=("order",),
        folder_aliases=("Orders",),
        stem_aliases=("order", "orders"),
    ),
    "order_items": ToastReportType(
        key="order_items",
        label="Order Items",
        folder_name="Item Detail",
        landing_path="menus/product-mix",
        tab_label="Item Details",
        aliases=("item_detail", "item_details"),
        folder_aliases=("Order Items", "Item Details"),
        stem_aliases=("itemdetail", "itemdetails", "orderitem", "orderitems", "order_items"),
    ),
    "payments": ToastReportType(
        key="payments",
        label="Payments",
        folder_name="Payment",
        landing_path="sales/sales-summary",
        tab_label="Payments",
        aliases=("payment",),
        folder_aliases=("Payments",),
        stem_aliases=("payment", "payments"),
    ),
    "discounts": ToastReportType(
        key="discounts",
        label="Discounts",
        folder_name="Discount",
        landing_path="sales/sales-summary",
        tab_label="Discounts",
        aliases=("discount",),
        folder_aliases=("Discounts",),
        stem_aliases=("discount", "discounts"),
    ),
    "menu_items": ToastReportType(
        key="menu_items",
        label="Menu Items",
        folder_name="Menu Item",
        landing_path="menus/product-mix",
        tab_label="Top Items",
        aliases=("menu_item",),
        folder_aliases=("Menu Items", "Top Items"),
        stem_aliases=("menuitem", "menuitems", "menu_items", "topitem", "topitems"),
    ),
}


DEFAULT_REPORT_TYPE_KEYS = tuple(REPORT_TYPES.keys())
REPORT_TYPE_ALIASES = {
    alias: report.key
    for report in REPORT_TYPES.values()
    for alias in (report.key, *report.aliases)
}


def canonical_report_key(report_type: str | ToastReportType) -> str:
    if isinstance(report_type, ToastReportType):
        return report_type.key
    try:
        return REPORT_TYPE_ALIASES[report_type]
    except KeyError as exc:
        raise ValueError(f"Unknown Toast report type: {report_type}") from exc


def get_report_type(report_type: str | ToastReportType) -> ToastReportType:
    try:
        return REPORT_TYPES[canonical_report_key(report_type)]
    except ValueError as exc:
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


def infer_report_type(parts: tuple[str, ...] = (), filename: str = "") -> ToastReportType:
    lowered_parts = {part.strip().lower() for part in parts if part and part.strip()}
    for report in REPORT_TYPES.values():
        valid_parts = {report.folder_name.lower(), *(alias.lower() for alias in report.folder_aliases)}
        if lowered_parts & valid_parts:
            return report

    stem = Path(filename).stem.lower().replace("-", "").replace("_", "")
    for report in REPORT_TYPES.values():
        if any(alias.lower().replace("-", "").replace("_", "") in stem for alias in report.stem_aliases):
            return report

    return REPORT_TYPES["sales_summary"]


def build_local_report_dir(base_dir: str | Path, store_name: str, report_type: str | ToastReportType) -> Path:
    report = get_report_type(report_type)
    return Path(base_dir) / str(store_name) / report.folder_name
