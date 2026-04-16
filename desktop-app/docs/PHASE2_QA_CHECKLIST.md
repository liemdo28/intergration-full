# Phase 2 QA Checklist — Client-Safe Operator Edition

**App version:** see `version.json` → `app_version`
**Build:** see `version.json` → `build` (`dev` | `release`)
**Commit:** see `version.json` → `commit_hash`
**Date:** ___
**Tester:** ___

---

## Environments to Test

| Environment | Required |
|---|---|
| Portable ZIP extracted to clean Windows 10/11 VM | **Required** |
| Portable ZIP extracted to Desktop on dev machine | **Required** |
| Installer EXE on clean VM (if built) | **Required** |
| Dev machine — app launch from built EXE | **Required** |

---

## A — Home Dashboard

### A1 — Default landing tab
```
Launch the app. Home tab should be the first screen.
```
**PASS:** Home Dashboard visible on launch.
**FAIL:** Another tab (e.g. QB Sync) shown first → `_active_tab = "home"` not set.

### A2 — Hero welcome card
```
Look at the top card. Should show:
  - Greeting: "Good [morning/afternoon/evening], [Operator]"
  - Subtitle: "ToastPOSManager is running."
```
**PASS:** Greeting + subtitle visible.
**FAIL:** Blank or wrong greeting → `_get_greeting()` broken.

### A3 — Today's Readiness badges
```
Look for 4 status badges: Download, QB Sync, Remove Transactions, Drive Upload.
Each badge should show READY / WARNING / BLOCKED / UNKNOWN.
```
**PASS:** All 4 badges visible with correct state.
**FAIL:** Fewer than 4 badges → `check_all_features()` not wired to HomeDashboard.

### A4 — Correct badge states
```
With no credentials configured:
  - Download: BLOCKED or WARNING (Playwright/Chromium)
  - QB Sync: BLOCKED or WARNING (QB not found / no password)
  - Drive Upload: BLOCKED (no credentials.json)
```
**PASS:** Each badge matches actual feature state.
**FAIL:** All green when dependencies missing → `feature_readiness_service` not called.

### A5 — Quick Actions section
```
Look for 3 ActionCards:
  - Download Reports (green accent)
  - QB Sync (teal accent)
  - Recovery (gray accent)
```
**PASS:** All 3 cards visible with correct accent colors.
**FAIL:** Cards missing → `ActionCard` not imported or not rendered.

### A6 — Quick Action navigation
```
Click "Download Reports" card.
```
**PASS:** App switches to Download tab.
**FAIL:** Tab doesn't switch → `status_var.set("navigate:download")` not wired to `_switch_tab`.

### A7 — Recommended Next Step card
```
Look for the card below Quick Actions.
With all features BLOCKED: should show amber card with reason + next step.
With all features READY: should show green "All Clear" card.
```
**PASS:** Urgent card shows when features are blocked; All Clear when all ready.
**FAIL:** Shows wrong state → `get_most_urgent()` not called or wrong.

### A8 — Safe Mode banner
```
Launch with --safe flag: .\ToastPOSManager.exe --safe
```
**PASS:** Amber banner at bottom: "SAFE MODE — [reason] — Background workers disabled."
**FAIL:** No banner → `is_safe_mode()` not checked in HomeDashboard.

---

## B — Recovery Center Tab

### B1 — App Health section
```
Open Recovery tab.
Look for: Python version, Platform, Runtime folder (writable), App version, Safe Mode status, Crash markers.
```
**PASS:** All 6 indicators shown. Runtime folder shows "(writable)" in green or "(read-only)" in red.
**FAIL:** Missing indicators → `get_app_health()` not called.

### B2 — App Health refresh
```
Click the Refresh button in the App Health section.
```
**PASS:** Values update immediately.
**FAIL:** No refresh → `_refresh_all()` not wired to button.

### B3 — Config Files section
```
Look for: .env.qb, local-config.json, credentials.json, token.json.
Each should show: ✓ (green) if OK, amber "Malformed" if bad, ✗ (red) if missing.
```
**PASS:** All 4 files shown with correct status icons.
**FAIL:** Wrong status or missing → `get_config_health()` not wired.

### B4 — Browser (Chromium) Status
```
Look for Chromium status indicator.
```
**PASS:** Green "Bundled and ready" if Chromium found; red "Missing — Download Reports may not work" if not.
**FAIL:** Wrong status → `get_browser_health()` not wired.

### B5 — Crash History
```
If there are crash/safe-mode markers in logs/:
  Should show a list of crash reasons + timestamps.
If no crashes:
  Should show "No crash history — app is running cleanly." in muted gray.
```
**PASS:** Correct state shown for actual crash history.
**FAIL:** Empty when crashes exist or vice versa → `get_crash_history()` not wired.

### B6 — Reset Config to Defaults
```
Click "Reset Config to Defaults" button.
```
**PASS:** Button turns action — existing `local-config.json` backed up, example copied.
Result message shows: "Backed up... reset to defaults."
**FAIL:** No backup or no reset → `reset_config_to_defaults()` not wired.

### B7 — Clear Toast Session
```
Click "Clear Toast Session" button.
```
**PASS:** Toast session files deleted. Result shows count of files removed.
**FAIL:** Files not deleted → `clear_toast_session()` not wired.

### B8 — Toggle Safe Mode
```
Click "Toggle Safe Mode" button. Launch app again, click again.
```
**PASS:** Safe mode toggles on/off. Banner shows/disappears.
**FAIL:** State doesn't change → `toggle_safe_mode()` not wired.

### B9 — Open Runtime Folder
```
Click "Open Runtime Folder" button.
```
**PASS:** Windows Explorer opens to the app folder.
**FAIL:** Nothing happens → `open_runtime_folder()` not wired.

### B10 — Export Support Bundle
```
Click "Export Support Bundle" button.
```
**PASS:** ZIP created in `crash-reports/`. Path shown in result label.
**FAIL:** No ZIP or error → `export_support_bundle()` not wired.

---

## C — Activity Audit Tab

### C1 — Summary cards visible
```
Open Audit tab.
Look for 4 summary cards at top:
  - Total Events
  - Success Rate (e.g. "48/50")
  - Failures
  - Stores Tracked
```
**PASS:** All 4 cards visible with real counts.
**FAIL:** Cards missing or show "0" when events exist → `get_activity_summary()` not called.

### C2 — Event list populated
```
After running Download, QB Sync, or Remove Transactions, open Audit tab.
Should see event rows with timestamp, category pill, title, and result.
```
**PASS:** Events appear with green border (success) or red border (failure).
**FAIL:** Empty list → `get_recent_activity()` not called or `ActivityEvent` not logged.

### C3 — Date filter works
```
Change date filter to "Today", "Last 7 Days", "Last 30 Days", "All Time".
```
**PASS:** Event list updates to match selected range.
**FAIL:** List unchanged → filter not wired to `get_events()`.

### C4 — Category filter works
```
Select a category from the dropdown (e.g. "QB Sync").
```
**PASS:** List shows only matching events.
**FAIL:** All events shown → category filter not wired.

### C5 — Export CSV
```
Click "Export CSV" button. Save to a location.
```
**PASS:** CSV file created with columns: timestamp, category, severity, title, detail, store, success, duration_seconds.
**FAIL:** No file or empty → `export_events_csv()` not wired.

### C6 — Live refresh
```
Wait 30+ seconds on Audit tab without closing.
```
**PASS:** New events automatically appear in the list (auto-refresh every ~30s).
**FAIL:** No auto-refresh → `self.after(30000, self._refresh)` not set.

### C7 — Empty state
```
On a fresh install with no events logged yet.
```
**PASS:** Shows "No activity recorded yet." in muted gray.
**FAIL:** Shows empty list with no empty state message.

---

## D — Feature Readiness in Settings

### D1 — StatusBadge grid visible
```
Open Settings → Feature Readiness section.
Look for 4 rows, each with: Feature name, StatusBadge, Reason label.
```
**PASS:** All 4 badges visible with correct text.
**FAIL:** Shows old emoji (🟢/🔴) text → old `_readiness_display` code still active.

### D2 — Badges update live
```
Trigger a state change (e.g. connect Google Drive, add QB password).
Wait 5 seconds.
```
**PASS:** Badge color and reason text update automatically.
**FAIL:** Stale → `_refresh_readiness_ui()` not scheduled or `get_readiness()` not wired.

### D3 — Ready state shown correctly
```
With all dependencies met:
  - Download: 🟢 Ready
  - QB Sync: 🟢 Ready
  - Remove: 🟢 Ready
  - Drive: 🟢 Ready
```
**PASS:** All green.
**FAIL:** Some blocked → dependencies not actually met.

---

## E — Navigation

### E1 — All 7 tabs accessible
```
Sidebar shows: Home, Download, QB Sync, Remove, Settings, Recovery, Audit
Click each tab.
```
**PASS:** Each tab loads its content.
**FAIL:** Missing tabs → `nav_order` incomplete.

### E2 — Active tab highlighted
```
Click QB Sync tab.
```
**PASS:** QB Sync nav button highlighted blue, others dim.
**FAIL:** No highlight change → `_apply_nav_styles()` broken.

### E3 — Tab switch from Home Quick Actions
```
Home → Quick Actions → "QB Sync" card clicked → QB Sync tab opens.
Home → Quick Actions → "Recovery" card clicked → Settings tab opens.
```
**PASS:** Correct tab opens for each card.
**FAIL:** Wrong tab or no switch → `status_var.set("navigate:...")` not wired to `_switch_tab`.

---

## F — Activity Logging

### F1 — Download completion logged
```
Run a download in Download tab. Complete the download.
Open Audit tab.
```
**PASS:** Event shown: category=download, title="Download Reports completed", success=True.
**FAIL:** No event → `log(EventCategory.DOWNLOAD, ...)` not in `_download_worker`.

### F2 — QB Sync completion logged
```
Run a QB Sync preview. Complete successfully.
Open Audit tab.
```
**PASS:** Event shown: category=qb_sync, title="QB Sync completed", success=True.
**FAIL:** No event → `log(EventCategory.QB_SYNC, ...)` not in `_sync_worker`.

### F3 — Remove Transactions logged
```
Run Remove Transactions. Complete.
Open Audit tab.
```
**PASS:** Event shown: category=remove_tx, title="Remove Transactions completed", success=True.
**FAIL:** No event → `log(EventCategory.REMOVE_TX, ...)` not in `_delete_worker`.

### F4 — Failed operations logged
```
Run a download that fails (e.g. bad credentials).
Open Audit tab.
```
**PASS:** Event shown: success=False, detail contains error text.
**FAIL:** No failure event → `except` block in worker not logging.

### F5 — App lifecycle events
```
Launch app. Close cleanly (X button, not Force Quit).
Open Audit tab.
```
**PASS:** Two events: "App started" (on launch) and "App closed cleanly" (on close).
**FAIL:** Missing lifecycle events → `log(EventCategory.APP_LIFECYCLE, ...)` not in `App.__init__` and `App._on_close`.

### F6 — JSONL file created
```
After running any operation, check: app-folder/activity-logs/activity_YYYYMM.jsonl
```
**PASS:** File exists and contains JSON lines.
**FAIL:** No file → `_ensure_log_dir()` or `log_event()` broken.

---

## G — Build Artifacts

### G1 — Portable ZIP builds
```
Run: .\build_release.ps1
```
**PASS:** `release/ToastPOSManager-*.zip` created.
**FAIL:** No ZIP → build pipeline step missing.

### G2 — ZIP contents check
```
Extract ZIP. Confirm:
  ToastPOSManager.exe
  version.json
  bootstrap_runtime.pyc
  app.pyc
  .env.qb.example
  local-config.example.json
  PORTABLE_MODE.txt
  checksums.json
  playwright_browser/  (if Chromium bundled)
```
**PASS:** All present.
**FAIL:** Missing files → build pipeline `New-PortableZip` incomplete.

### G3 — Built EXE launches
```
On a clean machine or after closing dev Python env, run:
  dist/ToastPOSManager/ToastPOSManager.exe
```
**PASS:** App opens without Python or pip popup.
**FAIL:** Requires Python installed → PyInstaller spec not correctly configured.

### G4 — checksums.json valid
```
Run: .\scripts\validate_release_artifacts.ps1
```
**PASS:** All checksums match. Stage [4/7] passes.
**FAIL:** Checksum mismatch → `New-PortableZip` not regenerating checksums correctly.

### G5 — Spec file correct
```
Check ToastPOSManager.spec contains:
  - launcher.py as entry point
  - hidden imports: playwright, customtkinter, tkcalendar
  - version.json in datas
```
**PASS:** All present.
**FAIL:** Missing → spec not updated after Sprint 1.

---

## H — Regression (Phase 1 Still Works)

### H1 — Portable ZIP build
```
Run: .\build_release.ps1
```
**PASS:** ZIP created without error.

### H2 — App opens without Python
```
Launch built EXE on machine without Python installed.
```
**PASS:** App opens. No "python" popup or DLL errors.

### H3 — First-run wizard launches
```
On first run (no local-config.json), launch app.
```
**PASS:** Setup Wizard opens. Welcome → Stores → QB → Drive → Review.
**FAIL:** App opens directly → `is_first_run` not set in bootstrap.

### H4 — Wizard Skip Setup works
```
In first-run wizard, click "Skip Setup".
```
**PASS:** Wizard closes. Main app opens normally.
**FAIL:** App crashes or hangs → `_skip_and_close()` broken.

### H5 — Runtime folders auto-created
```
On first launch, check for:
  logs/, audit-logs/, toast-reports/, recovery-backups/
```
**PASS:** All folders created automatically.
**FAIL:** Missing → `_ensure_folders()` not called.

### H6 — Config self-heal on malformed JSON
```
Write "NOT JSON {{{" to local-config.json. Launch app.
```
**PASS:** Backup created. Template restored. App opens.
**FAIL:** App crashes → `_ensure_config_files()` error handling broken.

### H7 — Bootstrap log written
```
After launch, check: logs/bootstrap_YYYYMMDD.log
```
**PASS:** File exists with `[BOOTSTRAP]` line.

### H8 — Safe mode entry from crash marker
```
Create logs/safe_mode_20260101_120000.marker manually.
Launch app.
```
**PASS:** Safe mode banner appears. Reason shows "previous crash: ..."
**FAIL:** No banner → `activate_from_bootstrap_report()` not reading markers.

### H9 — Safe mode exit after clean run
```
Start in safe mode. Close app cleanly (X button).
Launch again WITHOUT --safe.
```
**PASS:** Safe mode OFF — no banner.
**FAIL:** Banner persists → `deactivate_safe_mode()` not registered via atexit.

### H10 — Support bundle export
```
From Recovery Center, click "Export Support Bundle".
```
**PASS:** ZIP created in `crash-reports/`. Logs included with secrets redacted.

---

## Sign-Off Sheet

### Home Dashboard

| Test | Result | Tester | Date |
|---|---|---|---|
| A1 Default landing tab | PASS / FAIL | | |
| A2 Hero welcome card | PASS / FAIL | | |
| A3 Today's Readiness badges | PASS / FAIL | | |
| A4 Correct badge states | PASS / FAIL | | |
| A5 Quick Actions visible | PASS / FAIL | | |
| A6 Quick Action navigation | PASS / FAIL | | |
| A7 Recommended Next Step | PASS / FAIL | | |
| A8 Safe Mode banner | PASS / FAIL | | |

### Recovery Center

| Test | Result | Tester | Date |
|---|---|---|---|
| B1 App Health section | PASS / FAIL | | |
| B2 App Health refresh | PASS / FAIL | | |
| B3 Config Files section | PASS / FAIL | | |
| B4 Browser (Chromium) Status | PASS / FAIL | | |
| B5 Crash History | PASS / FAIL | | |
| B6 Reset Config to Defaults | PASS / FAIL | | |
| B7 Clear Toast Session | PASS / FAIL | | |
| B8 Toggle Safe Mode | PASS / FAIL | | |
| B9 Open Runtime Folder | PASS / FAIL | | |
| B10 Export Support Bundle | PASS / FAIL | | |

### Activity Audit

| Test | Result | Tester | Date |
|---|---|---|---|
| C1 Summary cards visible | PASS / FAIL | | |
| C2 Event list populated | PASS / FAIL | | |
| C3 Date filter works | PASS / FAIL | | |
| C4 Category filter works | PASS / FAIL | | |
| C5 Export CSV | PASS / FAIL | | |
| C6 Live refresh | PASS / FAIL | | |
| C7 Empty state | PASS / FAIL | | |

### Feature Readiness (Settings)

| Test | Result | Tester | Date |
|---|---|---|---|
| D1 StatusBadge grid visible | PASS / FAIL | | |
| D2 Badges update live | PASS / FAIL | | |
| D3 Ready state shown correctly | PASS / FAIL | | |

### Navigation

| Test | Result | Tester | Date |
|---|---|---|---|
| E1 All 7 tabs accessible | PASS / FAIL | | |
| E2 Active tab highlighted | PASS / FAIL | | |
| E3 Tab switch from Home Quick Actions | PASS / FAIL | | |

### Activity Logging

| Test | Result | Tester | Date |
|---|---|---|---|
| F1 Download completion logged | PASS / FAIL | | |
| F2 QB Sync completion logged | PASS / FAIL | | |
| F3 Remove Transactions logged | PASS / FAIL | | |
| F4 Failed operations logged | PASS / FAIL | | |
| F5 App lifecycle events | PASS / FAIL | | |
| F6 JSONL file created | PASS / FAIL | | |

### Build Artifacts

| Test | Result | Tester | Date |
|---|---|---|---|
| G1 Portable ZIP builds | PASS / FAIL | | |
| G2 ZIP contents check | PASS / FAIL | | |
| G3 Built EXE launches | PASS / FAIL | | |
| G4 Checksums valid | PASS / FAIL | | |
| G5 Spec file correct | PASS / FAIL | | |

### Regression

| Test | Result | Tester | Date |
|---|---|---|---|
| H1 Portable ZIP build | PASS / FAIL | | |
| H2 App opens without Python | PASS / FAIL | | |
| H3 First-run wizard launches | PASS / FAIL | | |
| H4 Wizard Skip Setup works | PASS / FAIL | | |
| H5 Runtime folders auto-created | PASS / FAIL | | |
| H6 Config self-heal on malformed JSON | PASS / FAIL | | |
| H7 Bootstrap log written | PASS / FAIL | | |
| H8 Safe mode entry from crash marker | PASS / FAIL | | |
| H9 Safe mode exit after clean run | PASS / FAIL | | |
| H10 Support bundle export | PASS / FAIL | | |

---

**Sign-off criteria:** All tests marked PASS → ready for production release.

**Blocker count ≥ 1** → Fix blockers before release.

---

## Dev Review — Scope Reminder

This app is a **strong foundation, not a final product**. The goal is **client-safe operator edition**.

Key principles for all future changes:
- No scope drift — stay inside `intergration-full`
- Productization over feature richness
- Operator UX clarity over technical complexity
- One-click readiness for non-technical users
- Guided workflows over raw functionality
- Regression safety over speed of delivery