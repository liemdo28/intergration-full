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
