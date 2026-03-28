"""
Google Drive Service - Upload/Download Toast reports
"""

import os
import io
import json
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from app_paths import runtime_path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
ROOT_FOLDER_NAME = "Toast Reports"


class GDriveService:
    def __init__(self, credentials_file=None, token_file=None, on_log=None):
        self.credentials_file = credentials_file or str(runtime_path("credentials.json"))
        self.token_file = token_file or str(runtime_path("token.json"))
        self.on_log = on_log or (lambda msg: None)
        self.service = None
        self._folder_cache = {}  # name -> id

    def log(self, msg):
        self.on_log(msg)

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
        """Find a folder by name under a parent."""
        cache_key = f"{parent_id}:{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            q += f" and '{parent_id}' in parents"

        results = self.service.files().list(q=q, spaces="drive", fields="files(id, name)").execute()
        files = results.get("files", [])

        if files:
            folder_id = files[0]["id"]
            self._folder_cache[cache_key] = folder_id
            return folder_id
        return None

    def _create_folder(self, name, parent_id=None):
        """Create a folder and cache its ID."""
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        folder = self.service.files().create(body=metadata, fields="id").execute()
        folder_id = folder["id"]
        cache_key = f"{parent_id}:{name}"
        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _get_or_create_folder(self, name, parent_id=None):
        """Find or create a folder."""
        folder_id = self._find_folder(name, parent_id)
        if folder_id:
            return folder_id
        return self._create_folder(name, parent_id)

    def _get_store_folder(self, store_name):
        """Get or create Toast Reports/{store_name}/ folder."""
        root_id = self._get_or_create_folder(ROOT_FOLDER_NAME)
        store_id = self._get_or_create_folder(store_name, root_id)
        return store_id

    def setup_folders(self, store_names):
        """Create folder structure for all stores."""
        self.log("Setting up Google Drive folder structure...")
        root_id = self._get_or_create_folder(ROOT_FOLDER_NAME)
        self.log(f"  Root folder: {ROOT_FOLDER_NAME}")

        for name in store_names:
            self._get_or_create_folder(name, root_id)
            self.log(f"  Created/found: {ROOT_FOLDER_NAME}/{name}")

        self.log("Folder structure ready")

    def upload_report(self, local_path, store_name):
        """Upload a file to Toast Reports/{store_name}/. Returns file ID."""
        if not self.service:
            raise RuntimeError("Not authenticated")

        folder_id = self._get_store_folder(store_name)
        filename = os.path.basename(local_path)

        # Check if file already exists
        q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        existing = self.service.files().list(q=q, fields="files(id)").execute().get("files", [])

        media = MediaFileUpload(local_path, resumable=True)

        if existing:
            # Delete old file(s) and upload fresh
            for old_file in existing:
                self.service.files().delete(fileId=old_file["id"]).execute()
            self.log(f"  Deleted old: {ROOT_FOLDER_NAME}/{store_name}/{filename}")
        # Upload new file
        if True:
            metadata = {"name": filename, "parents": [folder_id]}
            result = self.service.files().create(body=metadata, media_body=media, fields="id").execute()
            self.log(f"  Uploaded: {ROOT_FOLDER_NAME}/{store_name}/{filename}")
            return result["id"]

    def download_report(self, store_name, filename, local_dir):
        """Download a file from Drive to local directory. Returns local path."""
        if not self.service:
            raise RuntimeError("Not authenticated")

        folder_id = self._get_store_folder(store_name)
        q = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        results = self.service.files().list(q=q, fields="files(id, name)").execute()
        files = results.get("files", [])

        if not files:
            raise FileNotFoundError(f"File not found: {ROOT_FOLDER_NAME}/{store_name}/{filename}")

        file_id = files[0]["id"]
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, filename)

        request = self.service.files().get_media(fileId=file_id)
        with open(local_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()

        self.log(f"  Downloaded: {filename} -> {local_path}")
        return local_path

    def list_reports(self, store_name=None):
        """List available reports on Drive."""
        if not self.service:
            raise RuntimeError("Not authenticated")

        if store_name:
            folder_id = self._find_folder(store_name, self._find_folder(ROOT_FOLDER_NAME))
            if not folder_id:
                return []
            q = f"'{folder_id}' in parents and trashed=false"
        else:
            root_id = self._find_folder(ROOT_FOLDER_NAME)
            if not root_id:
                return []
            q = f"'{root_id}' in parents and trashed=false"

        results = self.service.files().list(
            q=q, fields="files(id, name, size, modifiedTime, parents)", orderBy="name desc", pageSize=100
        ).execute()

        return results.get("files", [])

    def list_store_reports(self, store_name, date_prefix=None):
        """List report files for a specific store, optionally filtered by date."""
        folder_id = self._get_store_folder(store_name)
        q = f"'{folder_id}' in parents and trashed=false"
        if date_prefix:
            q += f" and name contains '{date_prefix}'"

        results = self.service.files().list(
            q=q, fields="files(id, name, size, modifiedTime)", orderBy="name desc", pageSize=100
        ).execute()

        return results.get("files", [])
