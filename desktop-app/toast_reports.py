from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_FOLDER_NAME = "Toasttab"
LEGACY_ROOT_FOLDER_NAMES = ("Toast Reports", "ToastUploads")


@dataclass(frozen=True)
class ToastReportType:
    key: str
    label: str
    folder_name: str
    landing_path: str | None
    tab_label: str | None
    validation_profile: str = "tabular"
    download_supported: bool = True
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
        aliases=("salessummary",),
        folder_aliases=("Sales Summary",),
        stem_aliases=("salessummary", "sales_summary", "sale_summary", "salessummaryreport"),
    ),
    "orders": ToastReportType(
        key="orders",
        label="Order Details",
        folder_name="Order Details",
        landing_path="sales/sales-summary",
        tab_label="Orders",
        aliases=("order", "order_details", "orderdetails"),
        folder_aliases=("Order", "Orders"),
        stem_aliases=("order", "orders", "orderdetails", "order_details"),
    ),
    "order_items": ToastReportType(
        key="order_items",
        label="Item Selection Details",
        folder_name="Item Selection Details",
        landing_path="menus/product-mix",
        tab_label="Item Details",
        aliases=("item_detail", "item_details", "item_selection_details", "itemselectiondetails", "items"),
        folder_aliases=("Item Detail", "Order Items", "Item Details", "Items"),
        stem_aliases=(
            "itemdetail",
            "itemdetails",
            "orderitem",
            "orderitems",
            "order_items",
            "itemselectiondetails",
            "item_selection_details",
        ),
    ),
    "payments": ToastReportType(
        key="payments",
        label="Payment Details",
        folder_name="Payment Details",
        landing_path="sales/sales-summary",
        tab_label="Payments",
        aliases=("payment", "payment_details", "paymentdetails"),
        folder_aliases=("Payment", "Payments"),
        stem_aliases=("payment", "payments", "paymentdetails", "payment_details"),
    ),
    "discounts": ToastReportType(
        key="discounts",
        label="Discounts",
        folder_name="Discounts",
        landing_path="sales/sales-summary",
        tab_label="Discounts",
        aliases=("discount",),
        folder_aliases=("Discount",),
        stem_aliases=("discount", "discounts"),
    ),
    "modifier_selections": ToastReportType(
        key="modifier_selections",
        label="Modifier Selection Details",
        folder_name="Modifier Selection Details",
        landing_path="menus/product-mix",
        tab_label="Modifier Details",
        aliases=("modifier_selection_details", "modifierselectiondetails", "modifier_details", "modifier_details_selection"),
        folder_aliases=("Modifier Detail", "Modifier Details", "Modifiers", "Modifier Selections"),
        stem_aliases=(
            "modifierselectiondetails",
            "modifier_selection_details",
            "modifierdetails",
            "modifierdetail",
            "modifiers",
        ),
    ),
    "product_mix": ToastReportType(
        key="product_mix",
        label="Product Mix (All Items)",
        folder_name="Product Mix",
        landing_path="menus/product-mix",
        tab_label="Product Mix",
        aliases=("product_mix_all_items", "productmix", "all_items"),
        folder_aliases=("Product Mix (All Items)", "All Items", "Top Items"),
        stem_aliases=("productmix", "product_mix", "productmixallitems", "allitems"),
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
    "time_entries": ToastReportType(
        key="time_entries",
        label="Time Entries / Labor Export",
        folder_name="Time Entries",
        landing_path=None,
        tab_label=None,
        download_supported=False,
        aliases=("labor", "labor_summary", "payroll_export", "timeentries"),
        folder_aliases=("Labor", "Labor Summary", "Payroll Export"),
        stem_aliases=("timeentries", "time_entries", "laborsummary", "labor_summary", "payrollexport"),
    ),
    "accounting": ToastReportType(
        key="accounting",
        label="Accounting",
        folder_name="Accounting",
        landing_path=None,
        tab_label=None,
        download_supported=False,
        aliases=("accounting_export",),
        folder_aliases=("Accounting Export",),
        stem_aliases=("accounting", "accountingexport"),
    ),
    "menu": ToastReportType(
        key="menu",
        label="Menu Configuration",
        folder_name="Menu",
        landing_path=None,
        tab_label=None,
        download_supported=False,
        aliases=("menu_export", "menu_configuration"),
        folder_aliases=("Menu Export", "Menu Configuration"),
        stem_aliases=("menu", "menuexport", "menuconfiguration"),
    ),
    "kitchen_details": ToastReportType(
        key="kitchen_details",
        label="Kitchen Details",
        folder_name="Kitchen Details",
        landing_path=None,
        tab_label=None,
        download_supported=False,
        aliases=("kitchen",),
        folder_aliases=("Kitchen Detail",),
        stem_aliases=("kitchendetails", "kitchen_details", "kitchen"),
    ),
    "cash_management": ToastReportType(
        key="cash_management",
        label="Cash Management",
        folder_name="Cash Management",
        landing_path=None,
        tab_label=None,
        download_supported=False,
        aliases=("cash", "cash_management_details"),
        folder_aliases=("Cash Drawer History", "Cash Activity Audit"),
        stem_aliases=("cashmanagement", "cash_management", "cashdrawer", "cashactivity"),
    ),
}


DEFAULT_REPORT_TYPE_KEYS = tuple(key for key, report in REPORT_TYPES.items() if report.download_supported)
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


def get_download_report_types() -> list[ToastReportType]:
    return [report for report in REPORT_TYPES.values() if report.download_supported]


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
