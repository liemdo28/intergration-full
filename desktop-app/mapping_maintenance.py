from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app_paths import app_path


MAPPING_FILE = app_path("qb-mapping.json")
MAP_DIR = app_path("Map")
CSV_HEADERS = ["QB", "Report", "Note"]
MAPPING_ISSUE_CODES = {
    "unmapped_categories",
    "unmapped_tax",
    "unmapped_payment_subtype",
    "unmapped_other_payment",
    "unmapped_payment_type",
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
            if issue.get("code") not in MAPPING_ISSUE_CODES:
                continue
            for candidate in _build_candidates_from_issue(record, issue):
                if candidate.key in seen:
                    continue
                candidate.current_qb = _row_qb_for(candidate.report, candidate.note, rows)
                seen.add(candidate.key)
                candidates.append(candidate)

    candidates.sort(key=lambda item: (item.store.lower(), item.date, item.issue_code, item.report.lower()))
    return [candidate.to_dict() for candidate in candidates]


def upsert_candidate_mapping(
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
