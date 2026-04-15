from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from app_paths import runtime_path


MARKETPLACE_REPORTS_DIR = runtime_path("marketplace-reports")


@dataclass
class MarketplaceSource:
    name: str
    customer_name: str
    ref_prefix: str
    csv_map: str
    file_name: str
    report_path: Path
    selected_by_user: bool = False


def d(value):
    if value is None or value == "" or value == "None":
        return Decimal("0")
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0")


def _normalize_header(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def default_marketplace_search_dirs() -> list[Path]:
    return [
        MARKETPLACE_REPORTS_DIR,
        Path.home() / "Downloads",
    ]


def resolve_marketplace_report_path(
    file_name: str,
    search_dirs: list[str | Path] | None = None,
    *,
    explicit_path: str | Path | None = None,
    require_explicit: bool = False,
) -> Path | None:
    if explicit_path:
        explicit = Path(explicit_path)
        if explicit.exists():
            return explicit
        if require_explicit:
            return None
    elif require_explicit:
        return None

    candidates = []
    for base in search_dirs or default_marketplace_search_dirs():
        base_path = Path(base)
        candidates.append(base_path / file_name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def get_marketplace_sources_for_store(
    store_config: dict,
    *,
    map_dir: str | Path,
    search_dirs: list[str | Path] | None = None,
    uploaded_paths: dict[str, str] | None = None,
    require_uploaded_path: bool = False,
) -> list[MarketplaceSource]:
    sources = []
    for raw in store_config.get("additional_sale_receipts", []):
        map_path = Path(map_dir) / raw.get("csv_map", "")
        explicit_path = (uploaded_paths or {}).get(raw.get("name", "")) or raw.get("report_path", "")
        report_path = resolve_marketplace_report_path(
            raw.get("file_name", ""),
            search_dirs=search_dirs,
            explicit_path=explicit_path,
            require_explicit=require_uploaded_path,
        )
        if not map_path.exists() or not report_path:
            continue
        sources.append(
            MarketplaceSource(
                name=raw.get("name", ""),
                customer_name=raw.get("customer_name", raw.get("name", "")),
                ref_prefix=raw.get("ref_prefix", ""),
                csv_map=raw.get("csv_map", ""),
                file_name=raw.get("file_name", ""),
                report_path=report_path,
                selected_by_user=bool(explicit_path),
            )
        )
    return sources


def load_marketplace_map(map_path: str | Path) -> list[dict]:
    with open(map_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [
            {
                "QB": (row.get("QB") or "").strip(),
                "Column": (row.get("Column") or "").strip(),
                "Type": (row.get("Type") or "").strip().lower(),
            }
            for row in reader
        ]


def normalize_marketplace_date(value: str) -> str | None:
    text = (value or "").strip()
    if not text or text.lower() == "grand total":
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def find_marketplace_row(report_path: str | Path, date_str: str) -> dict | None:
    with open(report_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            normalized = normalize_marketplace_date(row.get("Row Labels", ""))
            if normalized == date_str:
                return row
    return None


def _issue(code: str, message: str, *, severity: str = "error", blocking: bool = True, **meta) -> dict:
    return {
        "code": code,
        "message": message,
        "severity": severity,
        "blocking": blocking,
        **meta,
    }


def extract_marketplace_receipt_lines(
    *,
    report_path: str | Path,
    date_str: str,
    map_path: str | Path,
    source_name: str,
) -> tuple[list[dict], list[dict], dict | None]:
    row = find_marketplace_row(report_path, date_str)
    if not row:
        return [], [], None

    mapping_rows = load_marketplace_map(map_path)
    lines = []
    issues = []
    over_short_item = None
    row_lookup = {_normalize_header(key): key for key in row.keys()}

    for mapping in mapping_rows:
        qb_item = mapping.get("QB", "")
        column = mapping.get("Column", "")
        entry_type = mapping.get("Type", "")
        actual_column = row_lookup.get(_normalize_header(column))

        if entry_type == "balance":
            over_short_item = qb_item
            continue

        if not actual_column:
            issues.append(
                _issue(
                    "marketplace_missing_column",
                    f"{source_name} mapping column not found: {column}",
                    column=column,
                    qb_item=qb_item,
                    mapping_type=entry_type,
                    source_name=source_name,
                )
            )
            continue

        amount = d(row.get(actual_column, 0))
        if amount == 0:
            continue

        if entry_type == "payment":
            amount = -amount
        elif entry_type != "item":
            issues.append(
                _issue(
                    "marketplace_invalid_mapping_type",
                    f"{source_name} mapping has invalid Type: {entry_type}",
                    column=column,
                    qb_item=qb_item,
                    mapping_type=entry_type,
                    source_name=source_name,
                )
            )
            continue

        lines.append(
            {
                "item_name": qb_item,
                "amount": amount,
                "desc": f"{source_name} - {column}",
            }
        )

    balance = sum(line["amount"] for line in lines)
    if balance != 0:
        if over_short_item:
            lines.append(
                {
                    "item_name": over_short_item,
                    "amount": -balance,
                    "desc": f"{source_name} - Over/Short adjustment",
                }
            )
            balance = sum(line["amount"] for line in lines)
        if balance != 0:
            issues.append(
                _issue(
                    "marketplace_unbalanced_receipt",
                    f"{source_name} receipt lines are not balanced by {balance}",
                    balance=str(balance),
                    source_name=source_name,
                    mapping_type="balance",
                    column="auto-balance",
                )
            )

    return lines, issues, row
