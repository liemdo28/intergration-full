# Phase 1 — One-Click App QA Checklist

**App version:** see `version.json` → `app_version`
**Build:** see `version.json` → `build` (`dev` | `release`)
**Commit:** see `version.json` → `commit_hash`

---

## Environments to Test

| Environment | Required |
|---|---|
| Clean Windows 10 (no Python, no Playwright) | **Required** |
| Clean Windows 11 (no Python, no Playwright) | **Required** |
| Dev machine (has Python + deps installed) | Smoke only |
| Portable ZIP extracted to Desktop | **Required** |
| Installer EXE install on clean VM | **Required** |

> "Clean" = fresh VM snapshot before the test, reverted after.

---

## Test A — Portable ZIP

### A1 — Build the portable ZIP
```powershell
.\build_release.ps1
# Confirm: dist\ToastPOSManager\ exists
# Confirm: release\ToastPOSManager-*.zip exists
```

### A2 — Extract and launch
```
1. Copy the release zip to a clean machine
2. Extract to:  C:\ToastPOSManager\
3. Double-click:  ToastPOSManager.exe
```

**PASS:** App opens without Python or pip popup.

**FAIL:** Python or DLL error → Chromium not bundled correctly.

### A3 — Runtime structure auto-created
```
Expected folders alongside exe:
  ToastPOSManager.exe
  logs/                   ← auto-created
  audit-logs/            ← auto-created
  toast-reports/         ← auto-created
  recovery-backups/     ← auto-created
  marketplace-reports/   ← auto-created
  PORTABLE_MODE.txt      ← auto-created by build
```

**PASS:** All folders created on first launch.

**FAIL:** Any missing folder → bootstrap_runtime `_ensure_folders` not working.

### A4 — Config self-heal on first run
```
Expected files alongside exe after first launch:
  .env.qb                    ← copied from .env.qb.example
  local-config.json          ← copied from local-config.example.json
  bootstrap_report.json      ← written by launcher
```

**PASS:** Both files created from examples automatically.

**FAIL:** File missing → bootstrap_runtime `_ensure_config_files` not working.

### A5 — Missing config → app shows clear blocker message
```
Manual test:
1. Delete .env.qb and local-config.json
2. Run the app again
```

**PASS:** Dialog says "Cannot Start App" with BLOCKER reason, not a raw crash.

**FAIL:** Raw Python traceback → launcher `_show_fatal_dialog` not working.

### A6 — Wizard launches on first run
```
Expected: Setup wizard window opens instead of main app.
```

**PASS:** Wizard appears with Welcome → Stores → QB → Drive → Review steps.

**FAIL:** App opens directly without wizard → `is_first_run` flag not set.

### A7 — Wizard Skip Setup → app continues
```
Expected: clicking "Skip Setup" closes wizard, app opens normally.
```

**PASS:** App main window appears after skip.

**FAIL:** App crashes or hangs → wizard `_skip_and_close` broken.

### A8 — Chromium bundled → Download tab works
```
From the built portable, check bootstrap log:
  logs/bootstrap_YYYYMMDD.log
Expected line:  [INFO/ok] Playwright Chromium: ...playwright_browser\...
```

**PASS:** Chromium found inside the bundle.

**FAIL:** `[WARNING/missing] Playwright Chromium: browser path .../chromium does not exist`

→ Playwright browser not collected into bundle correctly.
→ Check `ToastPOSManager.spec` binaries section.

### A9 — Malformed config → auto-backup and regenerate
```
Manual test:
1. Write garbage to local-config.json
2. Run the app
```

**PASS:** Backup file created `local-config.json.backup_YYYYMMDD_HHMMSS`,
clean template regenerated, app continues.

**FAIL:** App crashes on bad JSON → `_ensure_config_files` error handling broken.

---

## Test B — Installer EXE

### B1 — Installer build
```powershell
# Requires Inno Setup 6 installed
.\build_release.ps1
# Confirm: release\installer\ToastPOSManager-Setup.exe exists
```

### B2 — Install
```
1. Run ToastPOSManager-Setup.exe on clean Windows
2. Next → Next → Install
3. Do NOT uncheck "Launch after install" (default: ON)
```

**PASS:** App launches automatically after install.

**FAIL:** Install fails → check `installer/ToastPOSManager.iss`.

### B3 — Shortcuts
```
Expected Start Menu shortcut:  ToastPOSManager
Expected Desktop shortcut (if selected):  ToastPOSManager
```

**PASS:** Both shortcuts present and launch the app.

**FAIL:** Missing shortcuts → check ISS `[Icons]` section.

### B4 — Uninstall
```
1. Windows Settings → Apps → ToastPOSManager → Uninstall
2. Confirm uninstall
```

**PASS:** App uninstalled cleanly. App folder removed.

**FAIL:** Files left behind → check ISS `[UninstallDelete]` section.

### B5 — Post-uninstall config preservation
```
Test on a machine where user already has configured local-config.json:
1. Install over existing installation
2. Uninstall
```

**PASS:** User's `local-config.json` preserved in `%APPDATA%` or equivalent.

**FAIL:** Config wiped on update → installer must not delete user data on reinstall.

---

## Test C — Bootstrap Behavior

### C1 — Writable runtime detection
```
Manual test: make the runtime folder read-only before launch
```

**PASS:** App shows BLOCKER "Runtime folder not writable" dialog.

**FAIL:** App silently fails or writes elsewhere → `_check_writable_runtime` broken.

### C2 — Folder creation
```
Before first launch, delete all subfolders of the runtime dir.
Launch app.
```

**PASS:** All required folders created automatically.

**FAIL:** Missing folders created manually → `_ensure_folders` broken.

### C3 — Bootstrap log written
```
After launch, check:  logs/bootstrap_YYYYMMDD.log
```

**PASS:** Log exists and contains `[BOOTSTRAP]` line with `can_run=...`.

**FAIL:** No log → `_write_bootstrap_log` not working.

### C4 — Chromium verification
```
If Chromium IS bundled:  [INFO/ok] Playwright Chromium: C:\...
If Chromium is NOT bundled:  [WARNING/missing] Playwright Chromium: ...
```

**PASS:** Correct level (INFO not WARNING) when bundled.

**FAIL:** WARNING even when bundled → browser path not found at runtime.

### C5 — Safe mode entry from marker
```
Manual test:
1. Run app normally and crash it (e.g. break a required import)
2. Run app again
```

**PASS:** Safe mode banner appears on second run with crash reason shown.

**FAIL:** No safe mode → `activate_from_bootstrap_report` not called in launcher.

### C6 — Safe mode exit (clean run)
```
1. Start app in safe mode (--safe flag or crash marker)
2. Let app run normally (close cleanly via window X, not force-kill)
3. Run app again
```

**PASS:** Safe mode is OFF on third run (amber banner gone).

**FAIL:** Safe mode persists → `deactivate_safe_mode` via atexit not working.

---

## Test D — Crash Path

### D1 — Broken config JSON → no silent crash
```
Write "NOT VALID JSON {{{" to local-config.json, run app.
```

**PASS:** Dialog: "Malformed config was backed up and regenerated from example"

**FAIL:** Raw JSONDecodeError or silent crash → `_ensure_config_files` error handling.

### D2 — Missing browser runtime → no crash, shows warning
```
Test on a build WITHOUT Chromium bundled (remove playwright_browser/ folder).
```

**PASS:** App opens normally. Readiness shows Download Reports: blocked with reason.
Download tab shows helpful message, not raw error.

**FAIL:** App crashes on missing browser → `_check_playwright_browser` must not raise.

### D3 — Bootstrap itself raises exception
```
Manual: temporarily break bootstrap_runtime.py (e.g. syntax error)
Build again, run on clean machine.
```

**PASS:** Crash bundle generated, dialog shows "Bootstrap Failed".

**FAIL:** No dialog or no bundle → launcher.py exception handler in `main()` broken.

### D4 — Secret redaction in crash bundle
```
Trigger a crash, open the generated support bundle:
  crash-reports/support_bundle_YYYYMMDD_HHMMSS.zip
```

**PASS:** `logs/*.log` inside the zip have `QB_PASSWORD...` lines replaced with `[REDACTED]`.

**FAIL:** Password visible in plain text → `_redact_line` not working.

---

## Test E — Feature Readiness

### E1 — Download Reports readiness
```
State machine:
  Chromium bundled + playwright ok  →  🟢 Ready
  Chromium missing                 →  🔴 Blocked: reason shown
  Playwright import error          →  🔴 Blocked: reason shown
```

**PASS:** Readiness panel shows correct state with reason + next step.

**FAIL:** Always shows "Checking..." → `_update_readiness` not called or not working.

### E2 — QB Sync readiness (QB not installed)
```
Test on a machine WITHOUT QuickBooks Desktop installed.
```

**PASS:** QB Sync shows 🔴 Blocked: "QuickBooks Desktop not found on this machine"
App does NOT crash. QB tabs are disabled but not grayed out silently.

**FAIL:** App crashes → QB detection must be in try/except with BLOCKER classification.

### E3 — QB Sync readiness (QB installed, no password)
```
QB is installed, .env.qb has no QB_PASSWORD set.
```

**PASS:** QB Sync shows 🟡 Partially configured: "QB Password not set"

**FAIL:** Shows 🟢 Ready (wrong) or 🔴 Blocked (too aggressive for missing password).

### E4 — Drive Upload readiness (no credentials)
```
No credentials.json in app folder.
```

**PASS:** Drive shows 🔴 Not connected: "credentials.json not found — see Settings"
with a button or link to help.

**FAIL:** Shows 🟢 Ready or crashes when trying to check Drive.

### E5 — One feature blocked does NOT block others
```
With all features in various states, verify:
- Download tab is disabled only if Chromium missing
- QB Sync tab is disabled only if QB not found
- Drive tab shows connection UI (not crash) if Drive not connected
- App overall opens regardless of which features are blocked
```

**PASS:** Independent feature degradation.

**FAIL:** One missing dependency crashes the whole app → need try/except isolation.

### E6 — Readiness panel in Settings shows live state
```
Open Settings → Feature Readiness
All four features: Download Reports, QB Sync, Remove Transactions, Drive Upload
Each should show icon + reason + next step.
```

**PASS:** Live updating readiness values.

**FAIL:** All show "Checking..." → SettingsTab `_app.get_readiness()` not connected.

---

## Test F — Entry Point Alignment

### F1 — launcher.py is the only production entry point
```
Check the built EXE:
  strings dist\ToastPOSManager\ToastPOSManager.exe | findstr launcher
  Should find: "launcher" referenced in frozen binary
```

**PASS:** `launcher.py` is bundled as entry point (not `app.py`).

**FAIL:** `app.py` is still the PyInstaller entry point → spec has wrong entry file.

### F2 — Build pipeline generates portable ZIP + optional installer
```
Run:  .\build_release.ps1
```

**PASS:** Files created:
  `dist/ToastPOSManager/ToastPOSManager.exe`  (built artifact)
  `release/ToastPOSManager-*.zip`  (portable zip)
  `release/installer/ToastPOSManager-Setup.exe`  (if Inno Setup present)

**FAIL:** Missing outputs → build pipeline step missing or failing silently.

### F3 — Built EXE has version metadata
```
Right-click built ToastPOSManager.exe → Properties → Details
```

**PASS:** Version tab shows app version and description.

**FAIL:** No metadata → `version-info.txt` not created or not linked in spec.

### F4 — Post-build artifact validation passes
```
After build, the pipeline runs validation that checks:
  - EXE exists
  - version.json present in bundle
  - playwright_browser/ folder present (if Chromium bundled)
  - checksums.json generated
  - PORTABLE_MODE.txt present
```

**PASS:** Pipeline prints ✅ PASS for each check.

**FAIL:** Validation silently skipped → pipeline lacks `Assert-ArtifactValid` step.

---

## Sign-Off Sheet

| Test | Result | Tester | Date |
|---|---|---|---|
| A1 Portable ZIP build | PASS / FAIL | | |
| A2 App opens | PASS / FAIL | | |
| A3 Runtime folders created | PASS / FAIL | | |
| A4 Config files created | PASS / FAIL | | |
| A5 Clear blocker message | PASS / FAIL | | |
| A6 Wizard launches | PASS / FAIL | | |
| A7 Wizard skip → app continues | PASS / FAIL | | |
| A8 Chromium bundled | PASS / FAIL | | |
| A9 Malformed config self-heal | PASS / FAIL | | |
| B2 Installer runs | PASS / FAIL | | |
| B3 Shortcuts created | PASS / FAIL | | |
| B4 Uninstall clean | PASS / FAIL | | |
| C1 Writable detection | PASS / FAIL | | |
| C2 Folder creation | PASS / FAIL | | |
| C3 Bootstrap log | PASS / FAIL | | |
| C4 Chromium verification level | PASS / FAIL | | |
| C5 Safe mode from marker | PASS / FAIL | | |
| C6 Safe mode exit | PASS / FAIL | | |
| D1 Broken config → no crash | PASS / FAIL | | |
| D2 Missing browser → no crash | PASS / FAIL | | |
| D3 Bootstrap exception → dialog | PASS / FAIL | | |
| D4 Secret redaction in bundle | PASS / FAIL | | |
| E1 Download Readiness correct | PASS / FAIL | | |
| E2 QB not installed → no crash | PASS / FAIL | | |
| E3 QB no password → partial | PASS / FAIL | | |
| E4 Drive not connected → UI | PASS / FAIL | | |
| E5 Independent feature degradation | PASS / FAIL | | |
| E6 Readiness panel live | PASS / FAIL | | |
| F1 launcher.py entry point | PASS / FAIL | | |
| F2 Build outputs present | PASS / FAIL | | |
| F3 Version metadata in EXE | PASS / FAIL | | |
| F4 Artifact validation passes | PASS / FAIL | | |

**Sign-off criteria:** All tests marked PASS → ready for Phase 2.

**Blocker count ≥ 1** → Phase 1 not complete. Fix blockers before proceeding.
