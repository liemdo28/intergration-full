# Toast POS Manager

Ung dung desktop hop nhat cho quy trinh Toast -> QuickBooks:

- `Download Reports`: dang nhap Toast bang Playwright, tai report theo ngay/store.
- `QB Sync`: doc file Excel Toast da tai va tao `Sales Receipt` trong QuickBooks Desktop.
- `Remove Transactions`: query va xoa transaction trong QuickBooks Desktop bang QBXML COM.
- `Settings`: Google Drive token, Toast session, quick links va trang thai cau hinh.

## Yeu cau

- Windows
- Python 3.12+
- QuickBooks Desktop da cai san
- Chromium cho Playwright (`python -m playwright install chromium`)
- Nen dung Python 64-bit de pywin32/QB COM on dinh

## Cau truc chinh

- `app.py`: giao dien desktop hop nhat
- `toast_downloader.py`: Toast web scraper
- `qb_sync.py`: tao Sales Receipt tu Excel Toast
- `qb_client.py`: query/xoa transaction trong QB Desktop
- `qb_automate.py`: mo QB file va tu dong login
- `gdrive_service.py`: upload report len Google Drive
- `qb-mapping.json`: mapping store va item
- `Map/`: mapping CSV theo store

## Cai dat

```powershell
cd "E:\Project\Toasttab Quickbook\New folder\integration-toasttab-qb\desktop-app"
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

## Cau hinh

1. Tao `.env.qb` tu `.env.qb.example`.
2. Dien password QuickBooks vao `QB_PASSWORD1..3`.
3. Neu dung Google Drive, dat `credentials.json` o cung thu muc app.
4. `local-config.json` se duoc tao tu dong khi ban chon file `.qbw` trong giao dien.

Mau `local-config.json` xem trong `local-config.example.json`.

## Chay app

```powershell
python app.py
```

Hoac chay:

```bat
launch.bat
```

## Startup diagnostics

App tu chay environment diagnostics luc khoi dong va hien ket qua trong tab Settings.

Co the chay doctor bang CLI:

```powershell
python app.py --doctor-cli
```

## Build EXE

```powershell
python -m pip install -r requirements-build.txt
python -m playwright install chromium
pyinstaller ToastPOSManager.spec --noconfirm
```

Hoac dung script:

```powershell
.\build_release.ps1
```

Output:

- `dist\ToastPOSManager\ToastPOSManager.exe`

## Release planning

- P0/P1/P2 checklist: [docs/RELEASE_READINESS_CHECKLIST.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/RELEASE_READINESS_CHECKLIST.md)
- Secret cleanup plan: [docs/SECRET_REMEDIATION.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/SECRET_REMEDIATION.md)

## Delete safety

- Remove Transactions co `Dry run only` mac dinh
- App tu export snapshot truoc khi xoa
- Ket qua xoa/giả lập duoc ghi vao `audit-logs\delete-transactions`
- Live delete bi khoa theo policy mac dinh; chi mo khoa bang `local-config.json` hoac `ALLOW_LIVE_DELETE=1` trong `.env.qb` trong maintenance window da duoc phe duyet

## Ghi chu van hanh

- Report tai ve duoc luu trong `toast-reports/` va khong dua vao git.
- `token.json`, `.toast-session.json`, `credentials.json`, `.env.qb`, `local-config.json` la file may cuc bo.
- Neu Playwright chua co browser, `launch.bat` se tu cai Chromium.
