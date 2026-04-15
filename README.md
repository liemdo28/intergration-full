# integration-toasttab-qb

> A Windows desktop application that bridges **Toast POS** (scraped via the Toast web portal) and **QuickBooks Desktop** (automated via the QB UI), enabling multi-store POS operators to reconcile daily sales receipts without manual data entry.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Tech Stack](#2-tech-stack)
3. [Project Structure](#3-project-structure)
4. [Key Features](#4-key-features)
5. [Running Locally](#5-running-locally)
6. [Building the Windows EXE](#6-building-the-windows-exe)
7. [Planning & Operations Docs](#7-planning--operations-docs)
8. [Pre-Release Gate](#8-pre-release-gate)
9. [Security Notes](#9-security-notes)
10. [Engineering Policy Summary](#10-engineering-policy-summary)

---

## 1. Project Overview

Toast POS Manager consolidates three POS-to-accounting workflows into a single desktop application:

| Tab | Description |
|-----|-------------|
| **Download Reports** | Scrapes the Toast web portal (via Playwright) to pull sales, tips, and payment reports for any store and date range |
| **QB Integration** | Reads scraped Excel reports and creates corresponding Sales Receipts in QuickBooks Desktop via UI automation |
| **Remove Transactions** | Queries and deletes previously synced QB transactions for reconciliation corrections |

All data originates from Toast website scraping and local Excel files — no Toast API is required.

### Supported Stores

7 locations: Stockton, The Rim, Stone Oak, Bandera, WA1, WA2, WA3 (Copper)

---

## 2. Tech Stack

| Layer | Technology |
|-------|------------|
| **UI Framework** | CustomTkinter (modern `tkinter` wrapper) |
| **Web Scraping** | Playwright (Chromium) |
| **QB Automation** | PyWin32 + PyWinauto (Windows UI automation) |
| **Google Drive** | Google API Python Client |
| **Data / Excel** | OpenPyXL |
| **Date Picking** | TkCalendar |
| **Build / Package** | PyInstaller (via `build_release.ps1`) |
| **Tests** | pytest |

---

## 3. Project Structure

```
integration-full/
├── desktop-app/                    # All application source code
│   ├── app.py                     # Main entry point; defines all 4 UI tabs
│   ├── app_paths.py               # Cross-platform path constants
│   │
│   │   # ── Tab 1: Download Reports ───────────────────────────────
│   ├── toast_downloader.py        # Playwright scraper for Toast portal
│   ├── toast_reports.py           # Report type definitions, dir setup, planner
│   ├── date_parser.py             # Date range parsing from UI inputs
│   ├── report_validator.py        # Validates downloaded Toast Excel files
│   ├── report_inventory.py        # Tracks downloaded reports (SQLite)
│   │
│   │   # ── Tab 2: QB Integration ───────────────────────────────
│   ├── qb_sync.py                 # Orchestrates QB receipt creation workflow
│   ├── qb_client.py               # QuickBooks COM client wrapper
│   ├── qb_automate.py             # PyWinauto QB window automation
│   ├── sync_ledger.py             # Sync history tracking (SQLite)
│   ├── mapping_maintenance.py     # Category / payment QB mapping editor
│   ├── marketplace_sync.py        # Uber/DoorDash/Grubhub CSV reconciliation
│   │
│   │   # ── Tab 3: Remove Transactions ───────────────────────
│   ├── delete_policy.py            # Dry-run guard, policy enforcement
│   ├── audit_utils.py             # Audit log and transaction snapshots
│   │
│   │   # ── Tab 4: Settings ───────────────────────────────────
│   ├── diagnostics.py             # Environment health checks
│   ├── recovery_center.py        # Backup, restore, support-bundle export
│   ├── integration_status.py      # Auto-sync planning, world clocks
│   ├── gdrive_service.py          # Google Drive API client
│   ├── agentai_sync.py            # Agent.ai integration sync
│   │
│   │   # ── Config (machine-local, NOT committed) ─────────────
│   ├── qb-mapping.json            # Category → QB account mappings
│   ├── Map/*.csv                  # Per-store mapping overrides
│   ├── credentials.json           # Google Drive OAuth (not committed)
│   ├── local-config.json          # Operator runtime config (not committed)
│   ├── local-config.example.json   # Config template
│   │
│   │   # ── Build ──────────────────────────────────────────────
│   ├── build_release.ps1           # PyInstaller build script
│   ├── ToastPOSManager.spec       # PyInstaller spec file
│   ├── requirements.txt           # Production dependencies
│   ├── requirements-dev.txt      # Dev dependencies (pytest)
│   ├── requirements-build.txt     # Build dependencies
│   └── launch.bat                 # Quick launcher
│
├── docs/                           # Planning and operations documentation
│   ├── RELEASE_READINESS_CHECKLIST.md
│   ├── SECRET_REMEDIATION.md
│   ├── TEST_PROGRAM.md
│   ├── CURRENT_STATE_REVIEW.md
│   ├── OPERATOR_GUIDE.md
│   ├── FINAL_APP_REQUIREMENTS.md
│   └── FIVE_YEAR_SELF_RECOVERY_RUNBOOK.md
│
├── tools/
│   └── final_app_gate.ps1         # Pre-release gate check
│
├── tests/                          # pytest test suite
├── launch.bat                      # Repo root launcher
└── POLICY.md                       # Engineering policy
```

---

## 4. Key Features

### Download Reports
- Logs into the Toast web portal using stored session credentials
- Scrapes sales summaries, tips, and payment mix reports for configurable store/date ranges
- Supports automated batch downloads via an auto-download planner
- Stores reports locally with inventory tracking in SQLite
- Optional upload to Google Drive

### QB Integration
- Reads validated Toast Excel reports and maps POS categories/payments to QB accounts
- Creates Sales Receipts in QuickBooks Desktop via UI automation (no QB API key required)
- **Strict accounting mode by default** — unmapped categories, taxes, and payment types block the run
- Maintains a sync ledger tracking every receipt created (store, date, amount, QB reference)
- Validation issues panel with fix suggestions and CSV/JSON export
- Preview mode — dry run before actual sync
- QB Item suggestion engine for unmapped items
- Supports Uber / DoorDash / Grubhub CSV (Stockton)

### Remove Transactions
- Loads operator-defined delete allowlists from `local-config.json`
- Takes full transaction snapshots before deletion (audit + self-recovery)
- Supports targeted deletion by date range or receipt reference
- **Live delete is policy-locked** — only enabled during approved maintenance windows

### Settings & Operations
- Google Drive OAuth connection
- Toast session and browser profile management
- QB company file path, automation timing, retry behavior
- Recovery Center: backup/restore, runbooks, support bundle export
- Agent.ai integration: headless worker polling + runtime snapshot publishing

---

## 5. Running Locally

### Option 1 — Launcher (recommended)
```bat
# From repo root or double-click
launch.bat
```

### Option 2 — Direct Python
```powershell
cd "E:\Project\Master\integration-full\desktop-app"
python app.py
```

> **Note:** Always run from the `desktop-app` directory so relative paths (`./Map`, `./logs`, etc.) resolve correctly.

### Dependencies Setup

```powershell
cd "E:\Project\Master\integration-full\desktop-app"

# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Install Chromium for Playwright
python -m playwright install chromium
```

---

## 6. Building the Windows EXE

### Option 1 — Build Script (recommended)
```powershell
cd "E:\Project\Master\integration-full\desktop-app"
.\build_release.ps1
```

### Option 2 — Manual
```powershell
pip install -r requirements-build.txt
python -m playwright install chromium
pyinstaller ToastPOSManager.spec --noconfirm
```

**Outputs:**
- `desktop-app\dist\ToastPOSManager\ToastPOSManager.exe`
- `desktop-app\release\ToastPOSManager-<timestamp>-<commit>.zip`

---

## 7. Planning & Operations Docs

| Document | Purpose |
|----------|---------|
| `docs/FINAL_APP_REQUIREMENTS.md` | P0–P3 release blockers and feature roadmap |
| `docs/RELEASE_READINESS_CHECKLIST.md` | Pre-release validation checklist |
| `docs/SECRET_REMEDIATION.md` | Credential rotation and Git history remediation |
| `docs/TEST_PROGRAM.md` | Regression and integration test plan |
| `docs/CURRENT_STATE_REVIEW.md` | Current state assessment |
| `docs/OPERATOR_GUIDE.md` | Step-by-step guide for end operators |
| `docs/FIVE_YEAR_SELF_RECOVERY_RUNBOOK.md` | Long-term operational runbooks |
| `POLICY.md` | Engineering policy (data safety, release, automation safety, testing) |

---

## 8. Pre-Release Gate

Before tagging a release, run the full gate:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\final_app_gate.ps1
```

This runs diagnostics, the test suite, and the build in sequence. The release bundle is only valid if all steps pass.

---

## 9. Security Notes

> **Historical exposure:** Earlier commits in this repository may contain exposed credentials for Toast, QuickBooks, and Google integrations. Secret hardening has been added to the source, but **rotating all secrets and rewriting Git history** to purge the leaks is a **mandatory prerequisite** before any public distribution.

**Guidelines:**
- No real secrets may be committed to the repository.
- Local credentials must reside exclusively in `credentials.json`, `local-config.json`, or environment variables — never hardcoded.
- Secret scanning must be enabled in CI to block new leaks automatically.
- Any exposure incident triggers mandatory rotation and history rewrite.

---

## 10. Engineering Policy Summary

| Area | Rule |
|------|------|
| **Accounting** | Strict accounting mode stays on by default; no silent mismatches allowed |
| **Validation** | Unmapped values block in strict mode and always surface visibly |
| **Release** | Every RC passes CI tests, diagnostics, and build; metadata includes version + commit hash |
| **Automation** | Every critical step has retry, timeout, and failure classification |
| **Audit** | Destructive actions produce snapshots; logs are operator-readable |
| **Testing** | Core flows have regression coverage; bug fixes include targeted tests |
| **Delete Policy** | Dry-run is default; live delete only in approved maintenance windows |

---

## Quick Reference: Local Config Keys

```json
{
  "qbw_files": {
    "rim": "/path/to/Rim.qbw",
    "stone_oak": "/path/to/StoneOak.qbw"
  },
  "google_drive": {
    "root_folder_url": "https://drive.google.com/...",
    "brand_folder_name": "Toast Reports"
  },
  "agentai_sync": {
    "enabled": false,
    "api_url": "https://agentai.yourdomain.com",
    "token": "YOUR_TOKEN"
  }
}
```
