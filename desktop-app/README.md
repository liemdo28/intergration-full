# Toast POS Manager

Automated reporting and accounting sync for Toast POS.

Download your store reports, verify coverage, and sync directly to QuickBooks Desktop — all from one guided app.

---

## What this app does

- **Download Reports** — Signs in to Toast and downloads your daily sales reports for one or more stores and date ranges. Files are saved locally and uploaded to Google Drive automatically.

- **QB Sync** — Reads downloaded reports and creates Sales Receipts in QuickBooks Desktop. Validates data before any write. Preview what will be synced before confirming.

- **Recovery Center** — Health checks, one-click config repair, support bundle export. If something breaks, start here.

---

## Installation

1. Run `ToastPOSManager-Setup.exe` (available in the latest release).
2. Follow the installer — no other software required.
3. Launch the app from the Start Menu or desktop shortcut.

On first launch, the app will guide you through connecting Google Drive and QuickBooks.

---

## First-time setup

The app walks you through setup on first launch:

1. **Select your stores** — Choose which Toast locations you manage.
2. **Connect Google Drive** — Authorize access to your team's shared Drive folder.
3. **Connect QuickBooks** — Point the app at your QuickBooks company file.

All setup is done inside the app. No config files to edit manually.

---

## Daily use

### Download reports
1. Click **Download Wizard** in the sidebar.
2. Select stores and date range.
3. The app validates readiness, then downloads.

### Sync to QuickBooks
1. Click **QB Sync Wizard** in the sidebar.
2. Select stores and date range.
3. The app checks that all source reports exist before it writes anything.
4. Preview what will be synced, then confirm.

---

## If something goes wrong

Open **Recovery Center** (in the sidebar). It shows:
- App health status
- Configuration health
- Browser status
- Last crash summary

One-click actions available:
- Repair Config
- Clear Session
- Export Support Bundle

If you need to contact support, use **Export Support Bundle** — it packages all relevant logs in one file.

---

## Release history

See [Releases](https://github.com/liemdo28/intergration-full/releases) for version history and changelogs.

---

## For developers

See `docs/` for architecture notes, service layer documentation, and build instructions.

To build from source: `powershell -File build_release.ps1`
To run tests: `python -m pytest tests/`
