from __future__ import annotations

from gdrive_service import GDriveService


def test_extract_folder_id_supports_drive_url_and_raw_id():
    url = "https://drive.google.com/drive/folders/1wxRPFHVIjigvsSqEL_8UI_Vzvw-0EBVG?usp=drive_link"

    assert GDriveService._extract_folder_id(url) == "1wxRPFHVIjigvsSqEL_8UI_Vzvw-0EBVG"
    assert GDriveService._extract_folder_id("1wxRPFHVIjigvsSqEL_8UI_Vzvw-0EBVG") == "1wxRPFHVIjigvsSqEL_8UI_Vzvw-0EBVG"


def test_get_report_folder_uses_configured_root_brand_and_report_folder(monkeypatch):
    service = GDriveService(
        config={
            "google_drive": {
                "root_folder_id": "root-123",
                "brand_folder_name": "Bakudan_Ramen",
                "use_date_subfolders": False,
            }
        }
    )

    calls = []

    def fake_get_or_create(name, parent_id=None):
        calls.append((name, parent_id))
        return f"{parent_id}/{name}" if parent_id else name

    monkeypatch.setattr(service, "_get_or_create_folder", fake_get_or_create)

    folder_id, relative_parts = service._get_report_folder("Stockton", "orders", filename="2026-04-01_OrderDetails_Store01.csv")

    assert calls == [
        ("Bakudan_Ramen", "root-123"),
        ("Stockton", "root-123/Bakudan_Ramen"),
        ("Order Details", "root-123/Bakudan_Ramen/Stockton"),
    ]
    assert folder_id == "root-123/Bakudan_Ramen/Stockton/Order Details"
    assert relative_parts == ["id:root-123", "Bakudan_Ramen", "Stockton", "Order Details"]


def test_get_report_folder_can_add_year_month_when_enabled(monkeypatch):
    service = GDriveService(
        config={
            "google_drive": {
                "root_folder_id": "root-123",
                "brand_folder_name": "",
                "use_date_subfolders": True,
            }
        }
    )

    calls = []

    def fake_get_or_create(name, parent_id=None):
        calls.append((name, parent_id))
        return f"{parent_id}/{name}" if parent_id else name

    monkeypatch.setattr(service, "_get_or_create_folder", fake_get_or_create)

    folder_id, relative_parts = service._get_report_folder("Stockton", "payments", filename="2026-04-01_PaymentDetails_Store01.csv")

    assert calls == [
        ("Stockton", "root-123"),
        ("2026", "root-123/Stockton"),
        ("04", "root-123/Stockton/2026"),
        ("Payment Details", "root-123/Stockton/2026/04"),
    ]
    assert folder_id == "root-123/Stockton/2026/04/Payment Details"
    assert relative_parts == ["id:root-123", "Stockton", "2026", "04", "Payment Details"]


class _FakeDeleteRequest:
    def __init__(self, calls, file_id):
        self.calls = calls
        self.file_id = file_id

    def execute(self):
        self.calls.append(self.file_id)
        return {}


class _FakeFilesApi:
    def __init__(self, calls):
        self.calls = calls

    def delete(self, fileId):
        return _FakeDeleteRequest(self.calls, fileId)


class _FakeDriveService:
    def __init__(self, calls):
        self.calls = calls

    def files(self):
        return _FakeFilesApi(self.calls)


def test_delete_file_calls_drive_delete_api():
    calls = []
    service = GDriveService(config={"google_drive": {"root_folder_id": "root-123"}})
    service.service = _FakeDriveService(calls)

    ok = service.delete_file("drive-file-123")

    assert ok is True
    assert calls == ["drive-file-123"]
