import csv
import json
from datetime import datetime
from pathlib import Path


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_dir(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def export_transactions_snapshot(transactions: list[dict], base_dir: Path, prefix: str, metadata: dict | None = None) -> dict[str, str]:
    base_dir = _ensure_dir(base_dir)
    stamp = _timestamp()
    csv_path = base_dir / f"{stamp}_{prefix}.csv"
    json_path = base_dir / f"{stamp}_{prefix}.json"

    fieldnames = [
        "TxnID",
        "TxnType",
        "TxnDate",
        "Label",
        "Account",
        "RefNumber",
        "Amount",
        "Memo",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for txn in transactions:
            writer.writerow({key: txn.get(key, "") for key in fieldnames})

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": metadata or {},
        "transactions": transactions,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"csv_path": str(csv_path), "json_path": str(json_path)}


def write_delete_audit(rows: list[dict], summary: dict, base_dir: Path, prefix: str) -> dict[str, str]:
    base_dir = _ensure_dir(base_dir)
    stamp = _timestamp()
    csv_path = base_dir / f"{stamp}_{prefix}_results.csv"
    json_path = base_dir / f"{stamp}_{prefix}_results.json"

    fieldnames = [
        "status",
        "message",
        "TxnID",
        "TxnType",
        "TxnDate",
        "Label",
        "Account",
        "RefNumber",
        "Amount",
        "Memo",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"csv_path": str(csv_path), "json_path": str(json_path)}


def write_item_creation_audit(payload: dict, base_dir: Path, prefix: str = "item-create") -> dict[str, str]:
    base_dir = _ensure_dir(base_dir)
    stamp = _timestamp()
    csv_path = base_dir / f"{stamp}_{prefix}.csv"
    json_path = base_dir / f"{stamp}_{prefix}.json"

    fieldnames = [
        "generated_at",
        "operator",
        "store",
        "qbw_path",
        "candidate_key",
        "candidate_issue_code",
        "candidate_store",
        "candidate_date",
        "candidate_report",
        "candidate_note",
        "source_name",
        "created_item",
        "created_item_type",
        "template_name",
        "template_type",
        "template_account",
        "status",
        "message",
    ]

    row = {key: payload.get(key, "") for key in fieldnames}
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"csv_path": str(csv_path), "json_path": str(json_path)}


def load_recent_item_creation_audits(base_dir: Path, *, limit: int = 20) -> list[dict]:
    base_dir = Path(base_dir)
    if not base_dir.exists():
        return []

    records: list[dict] = []
    for path in sorted(base_dir.glob("*_item-create.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        payload["_audit_path"] = str(path)
        payload["_modified_at"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        records.append(payload)
        if len(records) >= limit:
            break
    return records
