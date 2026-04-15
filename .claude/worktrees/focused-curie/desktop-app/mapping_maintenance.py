from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app_paths import app_path


MAPPING_FILE = app_path("qb-mapping.json")
MAP_DIR = app_path("Map")
CSV_HEADERS = ["QB", "Report", "Note"]
MARKETPLACE_CSV_HEADERS = ["QB", "Column", "Type"]
MAPPING_ISSUE_CODES = {
    "unmapped_categories",
    "unmapped_tax",
    "unmapped_payment_subtype",
    "unmapped_other_payment",
    "unmapped_payment_type",
}
MARKETPLACE_ISSUE_CODES = {
    "marketplace_missing_column",
    "marketplace_invalid_mapping_type",
    "marketplace_unbalanced_receipt",
}


@dataclass
class MappingCandidate:
    key: str
    store: str
    date: str
    issue_code: str
    title: str
    report: str
    note: str
    current_qb: str = ""
    map_kind: str = "toast"
    source_name: str = ""
    mapping_type: str = ""
    map_path: str = ""
    guidance: str = ""
    meta: dict | None = None

    def to_dict(self):
        return asdict(self)


def load_mapping_config(mapping_file: str | Path | None = None) -> dict:
    path = Path(mapping_file or MAPPING_FILE)
    if not path.exists():
        return {"global": {}, "stores": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_store_config(store_name: str, *, mapping_file: str | Path | None = None) -> dict:
    config = load_mapping_config(mapping_file)
    return dict(config.get("stores", {}).get(store_name, {}))


def resolve_csv_map_path(
    store_name: str,
    *,
    store_config: dict | None = None,
    mapping_file: str | Path | None = None,
    map_dir: str | Path | None = None,
) -> Path:
    store_config = dict(store_config or get_store_config(store_name, mapping_file=mapping_file))
    directory = Path(map_dir or MAP_DIR)
    csv_name = store_config.get("csv_map") or f"{store_name.lower().replace(' ', '_')}.csv"
    return directory / csv_name


def resolve_marketplace_csv_map_path(
    store_name: str,
    source_name: str,
    *,
    store_config: dict | None = None,
    mapping_file: str | Path | None = None,
    map_dir: str | Path | None = None,
) -> Path:
    store_config = dict(store_config or get_store_config(store_name, mapping_file=mapping_file))
    directory = Path(map_dir or MAP_DIR)
    for source in store_config.get("additional_sale_receipts", []):
        if _norm(source.get("name")) == _norm(source_name):
            csv_name = source.get("csv_map")
            if not csv_name:
                break
            return directory / csv_name
    fallback_name = f"{source_name.lower().replace(' ', '_')}_{store_name.lower().replace(' ', '_')}.csv"
    return directory / fallback_name


def load_csv_rows(csv_path: str | Path) -> list[dict]:
    path = Path(csv_path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append(
                {
                    "QB": (row.get("QB") or "").strip(),
                    "Report": (row.get("Report") or "").strip(),
                    "Note": (row.get("Note") or "").strip(),
                }
            )
        return rows


def save_csv_rows(csv_path: str | Path, rows: list[dict]):
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "QB": (row.get("QB") or "").strip(),
                    "Report": (row.get("Report") or "").strip(),
                    "Note": (row.get("Note") or "").strip(),
                }
            )


def load_marketplace_csv_rows(csv_path: str | Path) -> list[dict]:
    path = Path(csv_path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append(
                {
                    "QB": (row.get("QB") or "").strip(),
                    "Column": (row.get("Column") or "").strip(),
                    "Type": (row.get("Type") or "").strip().lower(),
                }
            )
        return rows


def save_marketplace_csv_rows(csv_path: str | Path, rows: list[dict]):
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MARKETPLACE_CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "QB": (row.get("QB") or "").strip(),
                    "Column": (row.get("Column") or "").strip(),
                    "Type": (row.get("Type") or "").strip().lower(),
                }
            )


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _payment_report_label(payment_type: str) -> str:
    normalized = _norm(payment_type)
    if normalized == "cash":
        return "Total of Cash"
    if normalized in {"credit/debit", "credit", "debit"}:
        return "Total of Credit/debit"
    if normalized in {"gift card", "gift"}:
        return "Total of Gift Card"
    if normalized == "other":
        return "Other"
    return payment_type


def _candidate_key(store: str, issue_code: str, report: str, note: str) -> str:
    return "||".join([store, issue_code, _norm(report), _norm(note)])


def _candidate_title(store: str, date: str, issue_code: str, report: str) -> str:
    label = issue_code.replace("_", " ")
    return f"{store} | {date} | {label} | {report}"


def _row_qb_for(report: str, note: str, rows: list[dict]) -> str:
    for row in rows:
        if _norm(row.get("Report")) == _norm(report) and _norm(row.get("Note")) == _norm(note):
            return row.get("QB", "")
    return ""


def _marketplace_row_for(column: str, mapping_type: str, rows: list[dict], *, qb_hint: str = "") -> dict | None:
    qb_hint_norm = _norm(qb_hint)
    column_norm = _norm(column)
    type_norm = _norm(mapping_type)
    for row in rows:
        if qb_hint_norm and _norm(row.get("QB")) == qb_hint_norm:
            return row
    for row in rows:
        if _norm(row.get("Column")) == column_norm and _norm(row.get("Type")) == type_norm:
            return row
    if type_norm == "balance":
        for row in rows:
            if _norm(row.get("Type")) == "balance":
                return row
    return None


def _build_candidates_from_issue(record: dict, issue: dict) -> list[MappingCandidate]:
    store = record.get("store", "")
    date = record.get("date", "")
    code = issue.get("code", "")
    meta = dict(issue)
    candidates: list[MappingCandidate] = []

    if code == "unmapped_categories":
        for category in issue.get("categories", []):
            note = "Gross Sale"
            candidates.append(
                MappingCandidate(
                    key=_candidate_key(store, code, category, note),
                    store=store,
                    date=date,
                    issue_code=code,
                    title=_candidate_title(store, date, code, category),
                    report=category,
                    note=note,
                    meta=meta,
                )
            )
        return candidates

    if code == "unmapped_tax":
        report = issue.get("tax_rate", "")
        note = "Tax Summary"
    elif code == "unmapped_payment_subtype":
        report = issue.get("payment_sub_type", "")
        note = "Payments Summary - Other sub type"
    elif code == "unmapped_other_payment":
        report = "Other"
        note = "Payments Summary"
    elif code == "unmapped_payment_type":
        report = _payment_report_label(issue.get("payment_type", ""))
        note = "Payments Summary"
    elif code in MARKETPLACE_ISSUE_CODES:
        source_name = record.get("source", "")
        report = issue.get("column") or "auto-balance"
        note = f"{source_name} marketplace map"
        mapping_type = issue.get("mapping_type") or ("balance" if code == "marketplace_unbalanced_receipt" else "item")
        guidance = "Update the marketplace CSV map and re-run preview."
        if code == "marketplace_missing_column":
            guidance = "Fix the mapped CSV column name for this marketplace source."
        elif code == "marketplace_invalid_mapping_type":
            guidance = "Fix the marketplace map Type value (item, payment, or balance)."
        elif code == "marketplace_unbalanced_receipt":
            guidance = "Add or correct a balance row so this marketplace receipt can auto-balance."

        candidates.append(
            MappingCandidate(
                key=_candidate_key(store, code, report, note),
                store=store,
                date=date,
                issue_code=code,
                title=_candidate_title(store, date, code, f"{source_name} | {report}"),
                report=report,
                note=note,
                map_kind="marketplace",
                source_name=source_name,
                mapping_type=mapping_type,
                guidance=guidance,
                meta=meta,
            )
        )
        return candidates
    else:
        return []

    candidates.append(
        MappingCandidate(
            key=_candidate_key(store, code, report, note),
            store=store,
            date=date,
            issue_code=code,
            title=_candidate_title(store, date, code, report),
            report=report,
            note=note,
            meta=meta,
        )
    )
    return candidates


def collect_mapping_candidates(
    validation_records: list[dict],
    *,
    mapping_file: str | Path | None = None,
    map_dir: str | Path | None = None,
) -> list[dict]:
    candidates: list[MappingCandidate] = []
    store_rows_cache: dict[str, list[dict]] = {}
    store_cfg_cache: dict[str, dict] = {}
    marketplace_rows_cache: dict[tuple[str, str], list[dict]] = {}
    seen: set[str] = set()

    for record in validation_records or []:
        store = record.get("store", "")
        base_store = store.split(" ", 1)[0] if store.startswith("Copper ") else store
        store_cfg = store_cfg_cache.setdefault(base_store, get_store_config(base_store, mapping_file=mapping_file))
        csv_path = resolve_csv_map_path(
            base_store,
            store_config=store_cfg,
            mapping_file=mapping_file,
            map_dir=map_dir,
        )
        rows = store_rows_cache.setdefault(base_store, load_csv_rows(csv_path))

        for issue in record.get("issues", []):
            if issue.get("code") not in MAPPING_ISSUE_CODES and issue.get("code") not in MARKETPLACE_ISSUE_CODES:
                continue
            for candidate in _build_candidates_from_issue(record, issue):
                if candidate.key in seen:
                    continue
                if candidate.map_kind == "marketplace":
                    source_name = candidate.source_name or record.get("source", "")
                    marketplace_csv_path = resolve_marketplace_csv_map_path(
                        base_store,
                        source_name,
                        store_config=store_cfg,
                        mapping_file=mapping_file,
                        map_dir=map_dir,
                    )
                    marketplace_rows = marketplace_rows_cache.setdefault(
                        (base_store, source_name),
                        load_marketplace_csv_rows(marketplace_csv_path),
                    )
                    row = _marketplace_row_for(
                        candidate.report,
                        candidate.mapping_type,
                        marketplace_rows,
                        qb_hint=(candidate.meta or {}).get("qb_item", ""),
                    )
                    candidate.current_qb = (row or {}).get("QB", "")
                    candidate.mapping_type = (row or {}).get("Type", candidate.mapping_type)
                    candidate.report = (row or {}).get("Column", candidate.report)
                    candidate.map_path = str(marketplace_csv_path)
                else:
                    candidate.current_qb = _row_qb_for(candidate.report, candidate.note, rows)
                    candidate.map_path = str(csv_path)
                seen.add(candidate.key)
                candidates.append(candidate)

    candidates.sort(key=lambda item: (item.store.lower(), item.date, item.issue_code, item.report.lower()))
    return [candidate.to_dict() for candidate in candidates]


def upsert_candidate_mapping(
    candidate: dict,
    qb_item: str,
    *,
    override_report: str | None = None,
    override_type: str | None = None,
    mapping_file: str | Path | None = None,
    map_dir: str | Path | None = None,
) -> dict:
    store_name = candidate.get("store", "")
    if store_name.startswith("Copper "):
        store_name = "Copper"
    qb_item = (qb_item or "").strip()
    if not qb_item:
        raise ValueError("QB item name is required")

    if candidate.get("map_kind") == "marketplace":
        source_name = candidate.get("source_name") or candidate.get("meta", {}).get("source_name") or ""
        if not source_name:
            raise ValueError("Marketplace source name is required")
        column_name = (override_report if override_report is not None else candidate.get("report", "")).strip()
        mapping_type = (override_type if override_type is not None else candidate.get("mapping_type", "")).strip().lower()
        if not column_name:
            raise ValueError("Marketplace column name is required")
        if mapping_type not in {"item", "payment", "balance"}:
            raise ValueError("Marketplace mapping type must be item, payment, or balance")

        store_cfg = get_store_config(store_name, mapping_file=mapping_file)
        csv_path = resolve_marketplace_csv_map_path(
            store_name,
            source_name,
            store_config=store_cfg,
            mapping_file=mapping_file,
            map_dir=map_dir,
        )
        rows = load_marketplace_csv_rows(csv_path)
        meta = candidate.get("meta") or {}
        existing_row = _marketplace_row_for(column_name, mapping_type, rows, qb_hint=meta.get("qb_item", ""))
        updated = False
        if existing_row:
            existing_row["QB"] = qb_item
            existing_row["Column"] = column_name
            existing_row["Type"] = mapping_type
            updated = True
        else:
            rows.append({"QB": qb_item, "Column": column_name, "Type": mapping_type})

        save_marketplace_csv_rows(csv_path, rows)
        return {
            "path": str(csv_path),
            "action": "updated" if updated else "created",
            "row": {"QB": qb_item, "Column": column_name, "Type": mapping_type},
            "map_kind": "marketplace",
        }

    return _upsert_toast_candidate_mapping(
        candidate,
        qb_item,
        mapping_file=mapping_file,
        map_dir=map_dir,
    )


def _upsert_toast_candidate_mapping(
    candidate: dict,
    qb_item: str,
    *,
    mapping_file: str | Path | None = None,
    map_dir: str | Path | None = None,
) -> dict:
    store_name = candidate.get("store", "")
    if store_name.startswith("Copper "):
        store_name = "Copper"
    qb_item = (qb_item or "").strip()
    if not qb_item:
        raise ValueError("QB item name is required")

    store_cfg = get_store_config(store_name, mapping_file=mapping_file)
    csv_path = resolve_csv_map_path(
        store_name,
        store_config=store_cfg,
        mapping_file=mapping_file,
        map_dir=map_dir,
    )
    rows = load_csv_rows(csv_path)

    report = candidate.get("report", "")
    note = candidate.get("note", "")
    updated = False
    for row in rows:
        if _norm(row.get("Report")) == _norm(report) and _norm(row.get("Note")) == _norm(note):
            row["QB"] = qb_item
            updated = True
            break

    if not updated:
        rows.append({"QB": qb_item, "Report": report, "Note": note})

    save_csv_rows(csv_path, rows)
    return {
        "path": str(csv_path),
        "action": "updated" if updated else "created",
        "row": {"QB": qb_item, "Report": report, "Note": note},
    }
