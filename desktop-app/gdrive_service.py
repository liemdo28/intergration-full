"""
Google Drive Service - Upload/Download Toast reports
"""

import os
import time
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


def _drive_query_literal(value):
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


class GDriveService:
    def __init__(self, credentials_file=None, token_file=None, on_log=None):
        self.credentials_file = credentials_file or str(runtime_path("credentials.json"))
        self.token_file = token_file or str(runtime_path("token.json"))
        self.on_log = on_log or (lambda msg: None)
        self.service = None
        self._folder_cache = {}

    def log(self, msg):
        self.on_log(msg)

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

    def _get_primary_root_folder(self):
        return self._get_or_create_folder(ROOT_FOLDER_NAME)

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
        root_id = self._get_primary_root_folder()
        store_id = self._get_or_create_folder(store_name, root_id)
        return store_id

    def _get_report_folder(self, store_name, report_type="sales_summary"):
        report = get_report_type(report_type)
        root_id = self._get_primary_root_folder()
        store_id = self._get_or_create_folder(store_name, root_id)
        return self._get_or_create_folder(report.folder_name, store_id)

    def _find_matching_files(self, folder_id, filename):
        q = f"name='{_drive_query_literal(filename)}' and '{folder_id}' in parents and trashed=false"
        return self._execute_with_retry(
            lambda: self.service.files().list(q=q, fields="files(id, name)").execute().get("files", []),
            operation=f"Find Drive report '{filename}'",
        )

    def _find_report_file_across_roots(self, store_name, filename, report_type="sales_summary"):
        report = get_report_type(report_type)
        search_paths = []
        for root_name, root_id in self._find_existing_root_folders():
            store_id = self._find_folder(store_name, root_id)
            if not store_id:
                continue
            if root_name == ROOT_FOLDER_NAME:
                report_folder_id = self._find_folder(report.folder_name, store_id)
                if report_folder_id:
                    search_paths.append((root_name, store_name, report.folder_name, report_folder_id))
            search_paths.append((root_name, store_name, None, store_id))

        for root_name, _store_name, report_folder_name, folder_id in search_paths:
            files = self._find_matching_files(folder_id, filename)
            if files:
                return files[0], root_name, report_folder_name
        return None, None, None

    def setup_folders(self, store_names, report_types=None):
        reports = normalize_report_types(report_types or DEFAULT_REPORT_TYPE_KEYS)
        self.log("Setting up Google Drive folder structure...")
        root_id = self._get_primary_root_folder()
        self.log(f"  Root folder: {ROOT_FOLDER_NAME}")

        for name in store_names:
            store_id = self._get_or_create_folder(name, root_id)
            self.log(f"  Created/found: {ROOT_FOLDER_NAME}/{name}")
            for report in reports:
                self._get_or_create_folder(report.folder_name, store_id)
                self.log(f"    Ready: {ROOT_FOLDER_NAME}/{name}/{report.folder_name}")

        self.log("Folder structure ready")

    def upload_report(self, local_path, store_name, report_type="sales_summary"):
        if not self.service:
            raise RuntimeError("Not authenticated")

        report = get_report_type(report_type)
        folder_id = self._get_report_folder(store_name, report.key)
        filename = os.path.basename(local_path)
        existing = self._find_matching_files(folder_id, filename)
        media = MediaFileUpload(local_path, resumable=True)

        if existing:
            for old_file in existing:
                self._execute_with_retry(
                    lambda file_id=old_file["id"]: self.service.files().delete(fileId=file_id).execute(),
                    operation=f"Delete old Drive file '{filename}'",
                )
            self.log(f"  Deleted old: {ROOT_FOLDER_NAME}/{store_name}/{report.folder_name}/{filename}")

        metadata = {"name": filename, "parents": [folder_id]}
        result = self._execute_with_retry(
            lambda: self.service.files().create(body=metadata, media_body=media, fields="id").execute(),
            operation=f"Upload Drive file '{filename}'",
        )
        self.log(f"  Uploaded: {ROOT_FOLDER_NAME}/{store_name}/{report.folder_name}/{filename}")
        return result["id"]

    def download_report(self, store_name, filename, local_dir, report_type="sales_summary"):
        if not self.service:
            raise RuntimeError("Not authenticated")

        file_info, root_name, report_folder_name = self._find_report_file_across_roots(store_name, filename, report_type)
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

        if report_folder_name:
            source_path = f"{root_name}/{store_name}/{report_folder_name}/{filename}"
        else:
            source_path = f"{root_name}/{store_name}/{filename}"
        self.log(f"  Downloaded: {source_path} -> {local_path}")
        return local_path

    def list_reports(self, store_name=None, report_type=None):
        if not self.service:
            raise RuntimeError("Not authenticated")

        if store_name:
            aggregated = []
            seen_ids = set()
            report_folder_name = get_report_type(report_type).folder_name if report_type else None
            for root_name, root_id in self._find_existing_root_folders():
                store_id = self._find_folder(store_name, root_id)
                if not store_id:
                    continue
                folder_id = store_id
                if report_folder_name and root_name == ROOT_FOLDER_NAME:
                    nested_folder_id = self._find_folder(report_folder_name, store_id)
                    if not nested_folder_id:
                        continue
                    folder_id = nested_folder_id
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
