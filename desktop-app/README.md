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
Tab `Settings` gio co them `Recovery Center` de user tu xuat health report, reset session/token an toan, tao file config mau, va doc playbook xu ly su co thong dung ma khong can dev.

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
- `release\ToastPOSManager-<timestamp>-<commit>.zip`

Neu may build co Inno Setup (`ISCC.exe`), script se build them installer `.exe` trong thu muc `release\`.

## QB Sync safety

- `Strict accounting mode` duoc bat mac dinh de chan sync khi report co unmapped category/tax/payment hoac receipt khong can bang
- Co the tat strict mode khi can debug, nhung khong nen dung cho run production binh thuong
- Tab `QB Sync` co panel `Validation Issues` rieng de operator xem va export issue CSV/JSON thay vi chi doc log textbox
- Download Reports gio validate file `.xlsx` sau khi tai, retry neu download/validation fail, va ghi audit manifest vao `audit-logs\download-reports`
- QB open/sync gio co `company-file guard` dua tren `qbw_match` de giam nguy co mo nham company file
- Neu Toast session het han hoac password/login flow thay doi, app se hien warning ro rang va huong user sang `Settings -> Recovery Center`
- QB Sync gio co `sync ledger` bang SQLite (`sync-ledger.db`) de block duplicate same-report, ghi preview/live run, va detect stale running sync
- Tab `QB Sync` gio co `Last Sync Status` de operator xem run gan nhat, export sync audit, mark stale run as failed, va arm `Force Re-run` voi ly do luu vao ledger
- Tab `QB Sync` gio co `Mapping Maintenance` de operator sua map category/tax/payment ngay trong app, luu vao CSV map, roi re-run preview ma khong can dev sua file thu cong

## Release planning

- P0/P1/P2 checklist: [docs/RELEASE_READINESS_CHECKLIST.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/RELEASE_READINESS_CHECKLIST.md)
- Secret cleanup plan: [docs/SECRET_REMEDIATION.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/SECRET_REMEDIATION.md)
- Current state review: [docs/CURRENT_STATE_REVIEW.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/CURRENT_STATE_REVIEW.md)
- Operator guide: [docs/OPERATOR_GUIDE.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/OPERATOR_GUIDE.md)
- Final app requirements: [docs/FINAL_APP_REQUIREMENTS.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/FINAL_APP_REQUIREMENTS.md)
- Five-year self-recovery runbook: [docs/FIVE_YEAR_SELF_RECOVERY_RUNBOOK.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/FIVE_YEAR_SELF_RECOVERY_RUNBOOK.md)
- Engineering policy: [POLICY.md](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/POLICY.md)

## Delete safety

- Remove Transactions co `Dry run only` mac dinh
- App tu export snapshot truoc khi xoa
- Ket qua xoa/giả lập duoc ghi vao `audit-logs\delete-transactions`
- Live delete bi khoa theo policy mac dinh; chi mo khoa bang `local-config.json` hoac `ALLOW_LIVE_DELETE=1` trong `.env.qb` trong maintenance window da duoc phe duyet

## Ghi chu van hanh

- Report tai ve duoc luu trong `toast-reports/` va khong dua vao git.
- `token.json`, `.toast-session.json`, `credentials.json`, `.env.qb`, `local-config.json` la file may cuc bo.
- Neu Playwright chua co browser, `launch.bat` se tu cai Chromium.
