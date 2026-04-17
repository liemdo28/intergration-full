# Toast POS Manager — Phase 2 QA Checklist

**Version**: v2.3 Client-Safe Release Candidate  
**Status**: Pre-release QA  
**Updated**: 2026-04

---

## Pre-Release Acceptance Criteria

All items below must pass before a version is approved for client delivery.

---

## 1. Clean-Machine Install Test

Test on a machine with NO Python, NO existing Toast POS Manager installation.

- [ ] Run `ToastPOSManager-Setup.exe` installer
- [ ] Installer completes without errors
- [ ] Desktop shortcut created
- [ ] App launches from Start Menu shortcut
- [ ] App launches from desktop shortcut
- [ ] First-run wizard appears on first launch
- [ ] No Python console window appears
- [ ] App does not crash on startup

---

## 2. Bootstrap & First-Run Wizard

- [ ] First-run wizard shows on first launch
- [ ] Welcome screen is readable and non-technical
- [ ] Store selection works
- [ ] QuickBooks setup step works (or allows skip)
- [ ] Google Drive setup step works (or allows skip)
- [ ] Review step shows "connected / not configured yet" (not file paths)
- [ ] "Open App" button closes wizard and opens main app
- [ ] No raw exception messages shown during wizard

---

## 3. Home Dashboard

- [ ] Home tab shows all 4 readiness cards (Download, QB Sync, Drive, Remove TX)
- [ ] Each card shows: status pill + reason + next step text
- [ ] "Recommended Next Step" panel shows a relevant action
- [ ] CTA button label changes based on app state (Fix / Get Started / All Clear)
- [ ] Clicking CTA navigates to correct wizard
- [ ] Recent Activity section shows last 5 actions (or "No recent activity" if empty)
- [ ] Safe Mode banner appears when safe mode is active

---

## 4. Download Reports Wizard

- [ ] All 7 stores available for selection
- [ ] "Select All" toggle works
- [ ] Date range quick-select buttons work (Today, Yesterday, Last 7, Last 30)
- [ ] Readiness check runs automatically on step 3
- [ ] Readiness check shows green/red per item
- [ ] "Next" is blocked if readiness fails
- [ ] Download starts on step 4, progress log appears
- [ ] Stop button cancels download
- [ ] Result screen shows: Completed / Completed with Warnings / Failed Safely header
- [ ] Result screen shows: Downloaded count, Failed count, Stores count
- [ ] "→ Sync to QuickBooks" button navigates to QB Wizard

---

## 5. QB Sync Wizard

- [ ] Consolidated gate runs before preview (step 4)
- [ ] If source files missing: red block card shown with exact missing files listed
- [ ] If Drive unavailable: Drive connection block shown
- [ ] If duplicates detected: amber warning shown (non-blocking)
- [ ] Preview table shows all entries to sync
- [ ] "Confirm & Sync" triggers sync
- [ ] Progress log shows during sync
- [ ] Stop button cancels sync
- [ ] Result screen shows: Synced count, Entries Created, Gross Sales, Warnings
- [ ] "Return Home" navigates correctly

---

## 6. Recovery Center

- [ ] App version and safe mode status visible without expanding
- [ ] "Technical Details" collapsed by default
- [ ] Expand toggle shows Python version, platform, runtime folder
- [ ] Repair Config action works
- [ ] Clear Session action works
- [ ] Export Support Bundle creates a file and shows path
- [ ] Bundle file contains logs and health info

---

## 7. Settings

- [ ] Operator mode toggle visible (Standard / Admin)
- [ ] Switching to Standard mode hides advanced tabs after restart
- [ ] Switching to Admin mode shows all tabs
- [ ] QB company file path configurable per store
- [ ] Google Drive connection button works

---

## 8. Error Handling / Anti-Technical Language

- [ ] No raw Python exception text visible to user in normal flows
- [ ] Blocked states show: what happened + why it matters + what to do
- [ ] Recovery Center shows detailed info for support use only
- [ ] Status bar messages are plain English

---

## 9. Packaged App Smoke Test

Run `scripts/smoke_test_built_app.ps1` against the built EXE.

- [ ] All bundle artifact checks pass
- [ ] Launch smoke test passes
- [ ] Bootstrap log created
- [ ] Key modules confirmed bundled (bootstrap, launcher, wizards, services)
- [ ] No failed checks in smoke test output

---

## 10. Sign-Off

| Area | QA Reviewer | Status | Date |
|------|-------------|--------|------|
| Install & first run | | | |
| Home dashboard | | | |
| Download wizard | | | |
| QB sync wizard | | | |
| Recovery center | | | |
| Error language | | | |
| Smoke test | | | |

**Overall Status**: ⬜ Not started / 🟡 In progress / ✅ Approved / ❌ Failed

---

*For developer build instructions, see `build_release.ps1`.*  
*For support bundle export, use Recovery Center > Export Support Bundle.*
