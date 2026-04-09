"""
Google Drive Service - Upload/Download Toast reports
"""

import os
import re
import time
import json
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from app_paths import runtime_path
from toast_reports import (
    DEFAULT_REPORT_TYPE_KEYS,
    LEGACY_ROOT_FOLDER_NAMES,
    ROOT_FOLDER_NAME,
    get_report_type,
    normalize_report_types,
)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
LOCAL_CONFIG_FILE = runtime_path("local-config.json")
FOLDER_ID_RE = re.compile(r"/folders/([a-zA-Z0-9_-]+)")


def _drive_query_literal(value):
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


DATE_FOLDER_RE = re.compile(r"(20\d{2})[-_]?(\d{2})")


class GDriveService:
    def __init__(self, credentials_file=None, token_file=None, on_log=None, config=None):
        self.credentials_file = credentials_file or str(runtime_path("credentials.json"))
        self.token_file = token_file or str(runtime_path("token.json"))
        self.on_log = on_log or (lambda msg: None)
        self.service = None
        self._folder_cache = {}
        self._config = config or self._load_local_config()
        drive_cfg = dict(self._config.get("google_drive") or {})
        self._configured_root_folder_id = self._extract_folder_id(
            drive_cfg.get("root_folder_id") or drive_cfg.get("root_folder_url")
        )
        self._configured_brand_folder_name = str(drive_cfg.get("brand_folder_name") or "").strip()
        self._use_date_subfolders = bool(drive_cfg.get("use_date_subfolders", False))

    def log(self, msg):
        self.on_log(msg)

    @staticmethod
    def _load_local_config():
        if not LOCAL_CONFIG_FILE.exists():
            return {}
        try:
            return json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _extract_folder_id(value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        match = FOLDER_ID_RE.search(raw)
        if match:
            return match.group(1)
        if "/" not in raw and "?" not in raw and " " not in raw:
            return raw
        return ""

    def _execute_with_retry(self, action, *, attempts=3, delay_seconds=1.0, operation="Google Drive request"):
        last_error = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                return action()
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                wait_seconds = delay_seconds * attempt
                self.log(f"{operation} failed (attempt {attempt}/{attempts}): {exc}. Retrying in {wait_seconds:.0f}s...")
                time.sleep(wait_seconds)
        raise last_error

    def authenticate(self):
        """Authenticate with Google Drive. Returns True on success."""
        creds = None

        if os.path.exists(self.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            except Exception:
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                Path(self.token_file).write_text(creds.to_json(), encoding="utf-8")
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(self.credentials_file):
                self.log(f"credentials.json not found at {self.credentials_file}")
                self.log("Download from Google Cloud Console > APIs & Services > Credentials")
                return False

            flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)

            with open(self.token_file, "w") as f:
                f.write(creds.to_json())
            self.log("Google Drive authenticated successfully")

        self.service = build("drive", "v3", credentials=creds)
        return True

    def is_authenticated(self):
        return self.service is not None

    def get_user_email(self):
        """Get authenticated user's email."""
        if not self.service:
            return None
        try:
            about = self.service.about().get(fields="user").execute()
            return about.get("user", {}).get("emailAddress", "Unknown")
        except Exception:
            return None

    def _find_folder(self, name, parent_id=None):
        cache_key = f"{parent_id}:{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        q = f"name='{_drive_query_literal(name)}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            q += f" and '{parent_id}' in parents"

        results = self._execute_with_retry(
            lambda: self.service.files().list(q=q, spaces="drive", fields="files(id, name)").execute(),
            operation=f"Find Drive folder '{name}'",
        )
        files = results.get("files", [])

        if files:
            folder_id = files[0]["id"]
            self._folder_cache[cache_key] = folder_id
            return folder_id
        return None

    def _create_folder(self, name, parent_id=None):
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        folder = self._execute_with_retry(
            lambda: self.service.files().create(body=metadata, fields="id").execute(),
            operation=f"Create Drive folder '{name}'",
        )
        folder_id = folder["id"]
        cache_key = f"{parent_id}:{name}"
        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _get_or_create_folder(self, name, parent_id=None):
        folder_id = self._find_folder(name, parent_id)
        if folder_id:
            return folder_id
        return self._create_folder(name, parent_id)

    def _list_folders(self, parent_id):
        q = (
            "mimeType='application/vnd.google-apps.folder' and trashed=false "
            f"and '{parent_id}' in parents"
        )
        results = self._execute_with_retry(
            lambda: self.service.files().list(q=q, spaces="drive", fields="files(id, name)").execute(),
            operation="List Drive subfolders",
        )
        return results.get("files", [])

    def _list_folder_items(self, parent_id):
        q = f"'{parent_id}' in parents and trashed=false"
        results = self._execute_with_retry(
            lambda: self.service.files().list(
                q=q,
                spaces="drive",
                fields="files(id, name, mimeType, modifiedTime, size)",
                pageSize=200,
            ).execute(),
            operation="List Drive folder items",
        )
        return results.get("files", [])

    def _resolve_root_folder(self):
        if self._configured_root_folder_id:
            return self._configured_root_folder_id, f"id:{self._configured_root_folder_id}"
        root_id = self._get_or_create_folder(ROOT_FOLDER_NAME)
        return root_id, ROOT_FOLDER_NAME

    def _extract_year_month(self, filename):
        match = DATE_FOLDER_RE.search(str(filename or ""))
        if not match:
            return None, None
        return match.group(1), match.group(2)

    def _iter_store_folder_candidates(self, store_name):
        candidates = []
        seen = set()
        configured_root_id, configured_root_name = self._resolve_root_folder()
        roots = [(configured_root_name, configured_root_id)] if configured_root_id else self._find_existing_root_folders()
        for root_name, root_id in roots:
            if self._configured_brand_folder_name:
                brand_root_id = self._find_folder(self._configured_brand_folder_name, root_id)
                if brand_root_id:
                    root_name = f"{root_name}/{self._configured_brand_folder_name}"
                    root_id = brand_root_id
            direct_store_id = self._find_folder(store_name, root_id)
            if direct_store_id and direct_store_id not in seen:
                candidates.append(
                    {
                        "root_name": root_name,
                        "store_id": direct_store_id,
                        "relative_parts": [store_name],
                    }
                )
                seen.add(direct_store_id)

            for child in self._list_folders(root_id):
                brand_store_id = self._find_folder(store_name, child["id"])
                if brand_store_id and brand_store_id not in seen:
                    candidates.append(
                        {
                            "root_name": root_name,
                            "store_id": brand_store_id,
                            "relative_parts": [child["name"], store_name],
                        }
                    )
                    seen.add(brand_store_id)
        return candidates

    def _iter_report_folder_candidates(self, store_name, filename, report_type="sales_summary"):
        report_types = [get_report_type(report_type)] if report_type else normalize_report_types(list(DEFAULT_REPORT_TYPE_KEYS) + ["time_entries", "accounting", "menu", "kitchen_details", "cash_management", "modifier_selections", "product_mix", "discounts", "menu_items"])
        year, month = self._extract_year_month(filename)
        candidates = []
        seen = set()

        for store_info in self._iter_store_folder_candidates(store_name):
            store_id = store_info["store_id"]
            root_name = store_info["root_name"]
            relative_parts = list(store_info["relative_parts"])

            if report_type is None and store_id not in seen:
                candidates.append((store_id, root_name, relative_parts))
                seen.add(store_id)

            for report in report_types:
                folder_names = [report.folder_name, *report.folder_aliases]
                for folder_name in folder_names:
                    report_folder_id = self._find_folder(folder_name, store_id)
                    if report_folder_id and report_folder_id not in seen:
                        candidates.append((report_folder_id, root_name, [*relative_parts, folder_name]))
                        seen.add(report_folder_id)

            if year and month:
                year_id = self._find_folder(year, store_id)
                month_id = self._find_folder(month, year_id) if year_id else None
                if month_id:
                    for report in report_types:
                        folder_names = [report.folder_name, *report.folder_aliases]
                        for folder_name in folder_names:
                            nested_report_id = self._find_folder(folder_name, month_id)
                            if nested_report_id and nested_report_id not in seen:
                                candidates.append((nested_report_id, root_name, [*relative_parts, year, month, folder_name]))
                                seen.add(nested_report_id)
                    if month_id not in seen:
                        candidates.append((month_id, root_name, [*relative_parts, year, month]))
                        seen.add(month_id)

            if root_name != ROOT_FOLDER_NAME and store_id not in seen:
                candidates.append((store_id, root_name, relative_parts))
                seen.add(store_id)

        return candidates

    def _get_primary_root_folder(self):
        root_id, _root_name = self._resolve_root_folder()
        return root_id

    def _find_existing_root_folders(self):
        roots = []
        seen = set()
        for name in (ROOT_FOLDER_NAME, *LEGACY_ROOT_FOLDER_NAMES):
            folder_id = self._find_folder(name)
            if folder_id and folder_id not in seen:
                roots.append((name, folder_id))
                seen.add(folder_id)
        return roots

    def _get_store_folder(self, store_name):
        root_id, _root_name = self._resolve_root_folder()
        if self._configured_brand_folder_name:
            root_id = self._get_or_create_folder(self._configured_brand_folder_name, root_id)
        store_id = self._get_or_create_folder(store_name, root_id)
        return store_id

    def _get_report_folder(self, store_name, report_type="sales_summary", filename=None):
        report = get_report_type(report_type)
        root_id, root_name = self._resolve_root_folder()
        current_id = root_id
        relative_parts = [root_name]
        if self._configured_brand_folder_name:
            current_id = self._get_or_create_folder(self._configured_brand_folder_name, current_id)
            relative_parts.append(self._configured_brand_folder_name)
        current_id = self._get_or_create_folder(store_name, current_id)
        relative_parts.append(store_name)
        if self._use_date_subfolders:
            year, month = self._extract_year_month(filename or "")
            if year and month:
                current_id = self._get_or_create_folder(year, current_id)
                relative_parts.append(year)
                current_id = self._get_or_create_folder(month, current_id)
                relative_parts.append(month)
        current_id = self._get_or_create_folder(report.folder_name, current_id)
        relative_parts.append(report.folder_name)
        return current_id, relative_parts

    def _find_matching_files(self, folder_id, filename):
        q = f"name='{_drive_query_literal(filename)}' and '{folder_id}' in parents and trashed=false"
        return self._execute_with_retry(
            lambda: self.service.files().list(q=q, fields="files(id, name)").execute().get("files", []),
            operation=f"Find Drive report '{filename}'",
        )

    def _find_report_file_across_roots(self, store_name, filename, report_type="sales_summary"):
        for folder_id, root_name, relative_parts in self._iter_report_folder_candidates(store_name, filename, report_type):
            files = self._find_matching_files(folder_id, filename)
            if files:
                return files[0], root_name, relative_parts
        return None, None, None

    def report_exists(self, store_name, filename, report_type="sales_summary"):
        file_info, root_name, relative_parts = self._find_report_file_across_roots(store_name, filename, report_type)
        if not file_info:
            return None
        return {
            "file_id": file_info["id"],
            "file_name": file_info["name"],
            "root_name": root_name,
            "relative_parts": relative_parts or [store_name],
        }

    def setup_folders(self, store_names, report_types=None):
        reports = normalize_report_types(report_types or DEFAULT_REPORT_TYPE_KEYS)
        self.log("Setting up Google Drive folder structure...")
        root_id, root_name = self._resolve_root_folder()
        self.log(f"  Root folder: {root_name}")
        current_root_id = root_id
        current_root_parts = [root_name]
        if self._configured_brand_folder_name:
            current_root_id = self._get_or_create_folder(self._configured_brand_folder_name, current_root_id)
            current_root_parts.append(self._configured_brand_folder_name)
            self.log(f"  Brand folder: {'/'.join(current_root_parts)}")

        for name in store_names:
            store_id = self._get_or_create_folder(name, current_root_id)
            self.log(f"  Created/found: {'/'.join([*current_root_parts, name])}")
            for report in reports:
                self._get_or_create_folder(report.folder_name, store_id)
                self.log(f"    Ready: {'/'.join([*current_root_parts, name, report.folder_name])}")

        self.log("Folder structure ready")

    def upload_report(self, local_path, store_name, report_type="sales_summary"):
        if not self.service:
            raise RuntimeError("Not authenticated")

        report = get_report_type(report_type)
        folder_id, relative_parts = self._get_report_folder(store_name, report.key, filename=os.path.basename(local_path))
        filename = os.path.basename(local_path)
        existing = self._find_matching_files(folder_id, filename)
        media = MediaFileUpload(local_path, resumable=True)

        if existing:
            for old_file in existing:
                self._execute_with_retry(
                    lambda file_id=old_file["id"]: self.service.files().delete(fileId=file_id).execute(),
                    operation=f"Delete old Drive file '{filename}'",
                )
            self.log(f"  Deleted old: {'/'.join([*relative_parts, filename])}")

        metadata = {"name": filename, "parents": [folder_id]}
        result = self._execute_with_retry(
            lambda: self.service.files().create(body=metadata, media_body=media, fields="id").execute(),
            operation=f"Upload Drive file '{filename}'",
        )
        self.log(f"  Uploaded: {'/'.join([*relative_parts, filename])}")
        return result["id"]

    def delete_file(self, file_id):
        if not self.service:
            raise RuntimeError("Not authenticated")
        self._execute_with_retry(
            lambda: self.service.files().delete(fileId=file_id).execute(),
            operation=f"Delete Drive file '{file_id}'",
        )
        return True

    def scan_report_inventory(self, store_names=None, report_types=None):
        if not self.service:
            raise RuntimeError("Not authenticated")

        from report_inventory import extract_business_dates_from_name
        from toast_reports import infer_report_type

        allowed_stores = {item for item in (store_names or []) if item}
        allowed_report_keys = {report.key for report in normalize_report_types(report_types)} if report_types else None
        configured_root_id, configured_root_name = self._resolve_root_folder()
        current_root_id = configured_root_id
        current_root_parts = [configured_root_name]
        if self._configured_brand_folder_name:
            brand_id = self._find_folder(self._configured_brand_folder_name, configured_root_id)
            if not brand_id:
                return []
            current_root_id = brand_id
            current_root_parts.append(self._configured_brand_folder_name)

        rows = []
        for store_folder in self._list_folders(current_root_id):
            store_name = store_folder["name"]
            if allowed_stores and store_name not in allowed_stores:
                continue
            rows.extend(self._scan_store_folder(store_folder["id"], store_name, tuple(current_root_parts)))

        if allowed_report_keys is not None:
            rows = [row for row in rows if row["report_key"] in allowed_report_keys]
        return rows

    def _scan_store_folder(self, store_folder_id, store_name, root_parts):
        from report_inventory import extract_business_dates_from_name
        from toast_reports import infer_report_type

        rows = []
        stack = [(store_folder_id, [])]
        while stack:
            folder_id, relative_parts = stack.pop()
            for item in self._list_folder_items(folder_id):
                mime_type = item.get("mimeType", "")
                if mime_type == "application/vnd.google-apps.folder":
                    stack.append((item["id"], [*relative_parts, item["name"]]))
                    continue
                report = infer_report_type((store_name, *relative_parts), item["name"])
                business_dates = extract_business_dates_from_name(" ".join([*relative_parts, item["name"]]))
                if not business_dates:
                    business_dates = [None]
                relative_path = "/".join([*root_parts, store_name, *relative_parts, item["name"]])
                for business_date in business_dates:
                    rows.append(
                        {
                            "store": store_name,
                            "report_key": report.key,
                            "report_label": report.label,
                            "business_date": business_date,
                            "filepath": relative_path,
                            "filename": item["name"],
                            "modified_at": item.get("modifiedTime", ""),
                            "size_bytes": int(item.get("size", 0) or 0),
                            "source": "drive_inventory",
                            "file_id": item["id"],
                        }
                    )
        return rows

    def download_report(self, store_name, filename, local_dir, report_type="sales_summary"):
        if not self.service:
            raise RuntimeError("Not authenticated")

        # Try exact filename match first
        file_info, root_name, relative_parts = self._find_report_file_across_roots(store_name, filename, report_type)

        # Fallback: search by date pattern in filename.
        # Download creates files like "2026-04-01_SalesSummary_Stockton.xlsx"
        # but QB sync looks for "SalesSummary_2026-04-01_2026-04-01.xlsx".
        # Match any file in the report folder that contains the business date.
        if not file_info:
            from report_inventory import extract_business_dates_from_name
            target_dates = extract_business_dates_from_name(filename)
            if target_dates:
                target_date = target_dates[0]  # e.g. "2026-04-01"
                for folder_id, rn, rp in self._iter_report_folder_candidates(store_name, filename, report_type):
                    for item in self._list_folder_items(folder_id):
                        if item.get("mimeType", "").startswith("application/vnd.google-apps.folder"):
                            continue
                        item_dates = extract_business_dates_from_name(item["name"])
                        if target_date in item_dates:
                            file_info = item
                            root_name = rn
                            relative_parts = rp
                            filename = item["name"]  # Use actual Drive filename
                            break
                    if file_info:
                        break

        if not file_info:
            report = get_report_type(report_type)
            raise FileNotFoundError(f"File not found: {ROOT_FOLDER_NAME}/{store_name}/{report.folder_name}/{filename}")

        file_id = file_info["id"]
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, filename)

        request = self.service.files().get_media(fileId=file_id)
        with open(local_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _status, done = self._execute_with_retry(
                    downloader.next_chunk,
                    operation=f"Download Drive report '{filename}'",
                )

        source_path = "/".join([root_name, *(relative_parts or [store_name]), filename])
        self.log(f"  Downloaded: {source_path} -> {local_path}")
        return local_path

    def list_reports(self, store_name=None, report_type=None):
        if not self.service:
            raise RuntimeError("Not authenticated")

        if store_name:
            aggregated = []
            seen_ids = set()
            candidates = self._iter_report_folder_candidates(store_name, "", report_type or "sales_summary")
            for folder_id, _root_name, _relative_parts in candidates:
                results = self.service.files().list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    fields="files(id, name, size, modifiedTime, parents)",
                    orderBy="name desc",
                    pageSize=100,
                ).execute()
                for file_info in results.get("files", []):
                    if file_info["id"] in seen_ids:
                        continue
                    aggregated.append(file_info)
                    seen_ids.add(file_info["id"])
            return aggregated

        root_id = self._find_folder(ROOT_FOLDER_NAME)
        if not root_id:
            return []
        results = self.service.files().list(
            q=f"'{root_id}' in parents and trashed=false",
            fields="files(id, name, size, modifiedTime, parents)",
            orderBy="name desc",
            pageSize=100,
        ).execute()
        return results.get("files", [])

    def list_store_reports(self, store_name, date_prefix=None, report_type=None):
        files = self.list_reports(store_name=store_name, report_type=report_type)
        if not date_prefix:
            return files
        return [file_info for file_info in files if date_prefix in file_info.get("name", "")]
