from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app_paths import runtime_path
from report_validator import compute_sha256


LEDGER_DB_PATH = runtime_path("sync-ledger.db")
SYNC_AUDIT_DIR = runtime_path("audit-logs", "sync-runs")


STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_BLOCKED_DUPLICATE = "blocked_duplicate"
STATUS_BLOCKED_VALIDATION = "blocked_validation"
STATUS_PREVIEW_SUCCESS = "preview_success"


@dataclass
class ReportIdentity:
    path: Path
    report_hash: str
    report_size: int
    report_mtime: str


@dataclass
class BeginRunResult:
    allowed: bool
    sync_id: str
    status: str
    message: str
    existing_sync_id: str | None = None


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_report_identity(report_path: str | Path) -> ReportIdentity:
    path = Path(report_path)
    stat = path.stat()
    # FIX C2: Store mtime in UTC for consistency with utc_now() timestamps.
    # Previously stored as local time, causing off-by-one near DST transitions.
    utc_mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC).replace(microsecond=0)
    return ReportIdentity(
        path=path,
        report_hash=compute_sha256(path),
        report_size=stat.st_size,
        report_mtime=utc_mtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


class SyncLedger:
    def __init__(self, db_path: str | Path | None = None, audit_dir: str | Path | None = None):
        self.db_path = Path(db_path or LEDGER_DB_PATH)
        self.audit_dir = Path(audit_dir or SYNC_AUDIT_DIR)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_runs (
                    sync_id TEXT PRIMARY KEY,
                    store TEXT NOT NULL,
                    date TEXT NOT NULL,
                    source_name TEXT NOT NULL DEFAULT '',
                    report_path TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    report_size INTEGER NOT NULL,
                    report_mtime TEXT NOT NULL,
                    ref_number TEXT,
                    preview INTEGER NOT NULL,
                    strict_mode INTEGER NOT NULL,
                    qb_company_file TEXT,
                    status TEXT NOT NULL,
                    validation_error_count INTEGER NOT NULL DEFAULT 0,
                    validation_warning_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error_message TEXT,
                    override_reason TEXT
                )
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(sync_runs)").fetchall()}
            if "source_name" not in columns:
                conn.execute("ALTER TABLE sync_runs ADD COLUMN source_name TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    payload_json TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_store_date ON sync_runs(store, date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_store_date_source ON sync_runs(store, date, source_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_status ON sync_runs(status)")
            conn.commit()

    def record_event(self, sync_id: str, event_type: str, payload: dict | None = None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sync_events(sync_id, event_type, event_time, payload_json) VALUES (?, ?, ?, ?)",
                (sync_id, event_type, utc_now(), json.dumps(payload or {}, ensure_ascii=False)),
            )
            conn.commit()

    def mark_stale_runs_failed(self, *, stale_after_minutes: int = 30) -> int:
        cutoff = datetime.now(UTC) - timedelta(minutes=stale_after_minutes)
        cutoff_iso = cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self._connect() as conn:
            stale_rows = conn.execute(
                "SELECT sync_id FROM sync_runs WHERE status = ? AND started_at <= ?",
                (STATUS_RUNNING, cutoff_iso),
            ).fetchall()
            for row in stale_rows:
                conn.execute(
                    "UPDATE sync_runs SET status = ?, finished_at = ?, error_message = ? WHERE sync_id = ?",
                    (STATUS_FAILED, utc_now(), "Marked failed after stale running timeout", row["sync_id"]),
                )
                conn.execute(
                    "INSERT INTO sync_events(sync_id, event_type, event_time, payload_json) VALUES (?, ?, ?, ?)",
                    (row["sync_id"], "stale_marked_failed", utc_now(), json.dumps({}, ensure_ascii=False)),
                )
            conn.commit()
            return len(stale_rows)

    def begin_run(
        self,
        *,
        store: str,
        date: str,
        source_name: str,
        report_path: str | Path,
        report_hash: str,
        report_size: int,
        report_mtime: str,
        ref_number: str,
        preview: bool,
        strict_mode: bool,
        qb_company_file: str | None,
        validation_error_count: int = 0,
        validation_warning_count: int = 0,
        stale_after_minutes: int = 30,
        override_reason: str | None = None,
    ) -> BeginRunResult:
        self.mark_stale_runs_failed(stale_after_minutes=stale_after_minutes)
        sync_id = str(uuid.uuid4())

        with self._connect() as conn:
            start_message = "Sync run started."
            running = conn.execute(
                """
                SELECT sync_id FROM sync_runs
                WHERE store = ? AND date = ? AND source_name = ? AND status = ?
                ORDER BY started_at DESC, rowid DESC
                LIMIT 1
                """,
                (store, date, source_name, STATUS_RUNNING),
            ).fetchone()
            if running:
                self._insert_blocked(
                    conn,
                    sync_id=sync_id,
                    store=store,
                    date=date,
                    source_name=source_name,
                    report_path=str(report_path),
                    report_hash=report_hash,
                    report_size=report_size,
                    report_mtime=report_mtime,
                    ref_number=ref_number,
                    preview=preview,
                    strict_mode=strict_mode,
                    qb_company_file=qb_company_file,
                    status=STATUS_BLOCKED_DUPLICATE,
                    error_message="A sync is already running for this store/date.",
                    validation_error_count=validation_error_count,
                    validation_warning_count=validation_warning_count,
                )
                conn.commit()
                return BeginRunResult(
                    allowed=False,
                    sync_id=sync_id,
                    status=STATUS_BLOCKED_DUPLICATE,
                    message="A sync is already running for this store/date.",
                    existing_sync_id=running["sync_id"],
                )

            if not preview:
                existing_success = conn.execute(
                    """
                    SELECT sync_id FROM sync_runs
                    WHERE store = ? AND date = ? AND source_name = ? AND report_hash = ? AND status = ?
                    ORDER BY started_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (store, date, source_name, report_hash, STATUS_SUCCESS),
                ).fetchone()
                if existing_success:
                    if not override_reason:
                        self._insert_blocked(
                            conn,
                            sync_id=sync_id,
                            store=store,
                            date=date,
                            source_name=source_name,
                            report_path=str(report_path),
                            report_hash=report_hash,
                            report_size=report_size,
                            report_mtime=report_mtime,
                            ref_number=ref_number,
                            preview=preview,
                            strict_mode=strict_mode,
                            qb_company_file=qb_company_file,
                            status=STATUS_BLOCKED_DUPLICATE,
                            error_message="This report was already synced successfully.",
                            validation_error_count=validation_error_count,
                            validation_warning_count=validation_warning_count,
                        )
                        conn.commit()
                        return BeginRunResult(
                            allowed=False,
                            sync_id=sync_id,
                            status=STATUS_BLOCKED_DUPLICATE,
                            message="This report was already synced successfully.",
                            existing_sync_id=existing_success["sync_id"],
                        )
                    start_message = "Override re-run requested for a report that was already synced successfully."

            if not preview:
                existing_other_success = conn.execute(
                    """
                    SELECT sync_id FROM sync_runs
                    WHERE store = ? AND date = ? AND source_name = ? AND report_hash != ? AND status = ?
                    ORDER BY started_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (store, date, source_name, report_hash, STATUS_SUCCESS),
                ).fetchone()
                if existing_other_success:
                    start_message = "A different report version was previously synced for this store/date. Review carefully."

            conn.execute(
                """
                INSERT INTO sync_runs(
                    sync_id, store, date, source_name, report_path, report_hash, report_size, report_mtime,
                    ref_number, preview, strict_mode, qb_company_file, status,
                    validation_error_count, validation_warning_count, started_at, override_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sync_id,
                    store,
                    date,
                    source_name,
                    str(report_path),
                    report_hash,
                    report_size,
                    report_mtime,
                    ref_number,
                    int(preview),
                    int(strict_mode),
                    qb_company_file or "",
                    STATUS_RUNNING,
                    validation_error_count,
                    validation_warning_count,
                    utc_now(),
                    override_reason or "",
                ),
            )
            conn.execute(
                "INSERT INTO sync_events(sync_id, event_type, event_time, payload_json) VALUES (?, ?, ?, ?)",
                (sync_id, "run_started", utc_now(), json.dumps({}, ensure_ascii=False)),
            )
            if override_reason:
                conn.execute(
                    "INSERT INTO sync_events(sync_id, event_type, event_time, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        sync_id,
                        "override_rerun_requested",
                        utc_now(),
                        json.dumps({"reason": override_reason}, ensure_ascii=False),
                    ),
                )
            conn.commit()
            return BeginRunResult(
                allowed=True,
                sync_id=sync_id,
                status=STATUS_RUNNING,
                message=start_message,
            )

    def _insert_blocked(
        self,
        conn,
        *,
        sync_id: str,
        store: str,
        date: str,
        source_name: str,
        report_path: str,
        report_hash: str,
        report_size: int,
        report_mtime: str,
        ref_number: str,
        preview: bool,
        strict_mode: bool,
        qb_company_file: str | None,
        status: str,
        error_message: str,
        validation_error_count: int,
        validation_warning_count: int,
    ):
        now = utc_now()
        conn.execute(
            """
            INSERT INTO sync_runs(
                sync_id, store, date, source_name, report_path, report_hash, report_size, report_mtime,
                ref_number, preview, strict_mode, qb_company_file, status,
                validation_error_count, validation_warning_count, started_at, finished_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sync_id,
                store,
                date,
                source_name,
                report_path,
                report_hash,
                report_size,
                report_mtime,
                ref_number,
                int(preview),
                int(strict_mode),
                qb_company_file or "",
                status,
                validation_error_count,
                validation_warning_count,
                now,
                now,
                error_message,
            ),
        )
        conn.execute(
            "INSERT INTO sync_events(sync_id, event_type, event_time, payload_json) VALUES (?, ?, ?, ?)",
            (sync_id, status, now, json.dumps({"message": error_message}, ensure_ascii=False)),
        )

    def mark_status(self, sync_id: str, status: str, *, error_message: str | None = None, payload: dict | None = None):
        with self._connect() as conn:
            conn.execute(
                "UPDATE sync_runs SET status = ?, finished_at = ?, error_message = ? WHERE sync_id = ?",
                (status, utc_now(), error_message, sync_id),
            )
            conn.execute(
                "INSERT INTO sync_events(sync_id, event_type, event_time, payload_json) VALUES (?, ?, ?, ?)",
                (sync_id, status, utc_now(), json.dumps(payload or {}, ensure_ascii=False)),
            )
            conn.commit()

    def mark_success(self, sync_id: str, *, txn_id: str | None = None, preview: bool = False):
        payload = {"txn_id": txn_id or ""}
        self.mark_status(sync_id, STATUS_PREVIEW_SUCCESS if preview else STATUS_SUCCESS, payload=payload)

    def mark_failed(self, sync_id: str, error_message: str):
        self.mark_status(sync_id, STATUS_FAILED, error_message=error_message)

    def operator_mark_failed(self, sync_id: str, reason: str):
        self.mark_status(sync_id, STATUS_FAILED, error_message=reason, payload={"reason": reason})
        self.record_event(sync_id, "operator_mark_failed", {"reason": reason})

    def record_blocked_validation(
        self,
        *,
        store: str,
        date: str,
        source_name: str,
        report_path: str | Path,
        report_hash: str,
        report_size: int,
        report_mtime: str,
        ref_number: str,
        preview: bool,
        strict_mode: bool,
        qb_company_file: str | None,
        validation_error_count: int,
        validation_warning_count: int,
        error_message: str,
    ) -> str:
        sync_id = str(uuid.uuid4())
        with self._connect() as conn:
            self._insert_blocked(
                conn,
                sync_id=sync_id,
                store=store,
                date=date,
                source_name=source_name,
                report_path=str(report_path),
                report_hash=report_hash,
                report_size=report_size,
                report_mtime=report_mtime,
                ref_number=ref_number,
                preview=preview,
                strict_mode=strict_mode,
                qb_company_file=qb_company_file,
                status=STATUS_BLOCKED_VALIDATION,
                error_message=error_message,
                validation_error_count=validation_error_count,
                validation_warning_count=validation_warning_count,
            )
            conn.commit()
        return sync_id

    def get_last_run(self, store: str, date: str, source_name: str | None = None):
        with self._connect() as conn:
            if source_name is None:
                row = conn.execute(
                    """
                    SELECT * FROM sync_runs
                    WHERE store = ? AND date = ?
                    ORDER BY started_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (store, date),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM sync_runs
                    WHERE store = ? AND date = ? AND source_name = ?
                    ORDER BY started_at DESC, rowid DESC
                    LIMIT 1
                    """,
                    (store, date, source_name),
                ).fetchone()
            return dict(row) if row else None

    def get_latest_runs_by_source(self, store: str, date: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sync_runs
                WHERE store = ? AND date = ?
                ORDER BY started_at DESC, rowid DESC
                """,
                (store, date),
            ).fetchall()
        latest: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            source_name = item.get("source_name") or "Unknown"
            if source_name not in latest:
                latest[source_name] = item
        return list(latest.values())

    def get_run(self, sync_id: str):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sync_runs WHERE sync_id = ?", (sync_id,)).fetchone()
            return dict(row) if row else None

    def get_run_events(self, sync_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type, event_time, payload_json FROM sync_events WHERE sync_id = ? ORDER BY id ASC",
                (sync_id,),
            ).fetchall()
            events = []
            for row in rows:
                payload = {}
                if row["payload_json"]:
                    try:
                        payload = json.loads(row["payload_json"])
                    except Exception:
                        payload = {"raw": row["payload_json"]}
                events.append(
                    {
                        "event_type": row["event_type"],
                        "event_time": row["event_time"],
                        "payload": payload,
                    }
                )
            return events

    def export_run_audit(self, sync_id: str) -> Path:
        run = self.get_run(sync_id)
        if not run:
            raise FileNotFoundError(f"Sync run not found: {sync_id}")
        events = self.get_run_events(sync_id)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        path = self.audit_dir / f"sync-run-{sync_id}.json"
        path.write_text(
            json.dumps({"run": run, "events": events}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def diagnostics_snapshot(self) -> dict:
        with self._connect() as conn:
            stale_cutoff = (datetime.now(UTC) - timedelta(minutes=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            running_count = conn.execute(
                "SELECT COUNT(*) FROM sync_runs WHERE status = ?",
                (STATUS_RUNNING,),
            ).fetchone()[0]
            stale_count = conn.execute(
                "SELECT COUNT(*) FROM sync_runs WHERE status = ? AND started_at < ?",
                (STATUS_RUNNING, stale_cutoff),
            ).fetchone()[0]
            failed_count = conn.execute(
                "SELECT COUNT(*) FROM sync_runs WHERE status = ?",
                (STATUS_FAILED,),
            ).fetchone()[0]
        return {
            "db_path": str(self.db_path),
            "running_count": running_count,
            "stale_running_count": stale_count,
            "failed_count": failed_count,
        }
