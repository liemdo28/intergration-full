# Toast POS Manager — Changelog

---

## v2.3.0 — 2026-04-17 (Stable Release)

### Release status: STABLE — cleared for full deployment
All RC gates passed. Clean machine, operator acceptance, and QB machine validated.

### What changed from rc1

**RC hardening (pre-emptive fixes applied before QA)**
- Browser path: dual-probe strategy for both portable (`playwright-browsers/`) and spec-bundled (`_MEIPASS/playwright/`) Chromium distributions
- Config integrity: `_CONFIG_DEFAULTS` schema + `_heal_config()` enforced on every boot — no KeyError risk from partial configs
- Wizard state: `<Map>` event resets wizard state on re-entry — no cross-run data leak
- Sync gate: `date_gap` and `incomplete_report` escalated from WARN to BLOCK — incomplete data can no longer reach QuickBooks
- Result truth: `outcome_type="completed"` now requires `ok AND fail==0 AND no warnings` — no misleading result screens

### Release deliverables
- `docs/RELEASE_ANNOUNCEMENT_v2.3.0.md` — client-facing announcement
- `docs/QUICK_START_GUIDE.md` — operator quick start
- `docs/SUPPORT_HANDOFF.md` — internal support script

---

## v2.3.0-rc1 — 2026-04-17 (Release Candidate)

### Release status: GO with controlled rollout
Suitable for internal production and pilot client use.
Validate on one clean Windows machine before broad deployment.

### Validation required before full release
- [ ] Clean-machine packaged EXE validation (no Python installed)
- [ ] Real operator acceptance pass
- [ ] QB machine validation
- [ ] Non-QB machine validation

---

### What changed in Sprint 3 + Final Productization (7 patches)

**Home Dashboard — App Brain**
- Rich 3-line readiness cards: status pill + reason + next step per feature
- State-based CTA button: "Fix This Now" (blocked) / "Get Started" (warning) / "All Clear" (ready)
- Smart recommendation checks Drive coverage and sync backlog, not just infrastructure
- Recent Activity section shows last 5 actions

**Guided Wizards (wizard-first UX)**
- Download Reports Wizard: 5 steps — Stores → Dates → Readiness → Download → Result
- QB Sync Wizard: 5 steps — Stores → Dates → Readiness → Safety Gate/Preview → Result
- Result screens with explicit outcome type: Completed / Completed with Warnings / Blocked / Failed Safely
- Stats pills: Downloaded count, Gross Sales, Entries Created
- Primary + secondary CTA buttons (e.g. "→ Sync to QuickBooks" / "Return Home")

**Operator Mode**
- Standard mode: 5 sidebar items (guided wizards only)
- Admin mode: full 9 items (raw tabs + audit tools)
- Toggle in Settings > Access Level
- Default: Standard

**Consolidated Pre-Sync Gate (6 checks in 1 pass)**
- QB readiness (executable + credentials)
- Google Drive source completeness (ALL required reports must exist)
- Duplicate detection (sync ledger)
- Local file validity (pre-sync validator)
- Date sanity (future dates, extreme ranges)
- Explicit "Go There →" nav button for each blocker

**Technical Complexity Reduction**
- Recovery Center: Technical Details collapsed by default
- Copy rewrite: "Playwright" → "Report Browser", ".env.qb" → "QB Credentials"
- 11 new client-safe CopyKey messages (QB file errors, sync blockers, first-run, recovery)

**Architecture**
- app.py: 6661 → 956 lines across 3 sprints (−86%)
- Tab classes extracted to ui/tabs/ (DownloadTab, QBSyncTab, RemoveTab, SettingsTab)
- Services: feature_readiness, ui_state, activity_log, recovery, source_completeness, consolidated_sync_gate, workflow_state, preflight_validation, download_reports, qb_sync_preview, qb_sync
- Models: WorkflowState, ValidationResult, DownloadResult, QBSyncPreview

**Packaging & QA**
- docs/PHASE2_QA_CHECKLIST.md: 10-section QA checklist with sign-off table
- scripts/smoke_test_built_app.ps1: module presence checks for key modules, wizards, services
- Client-facing README (non-technical, English)
- Source completeness gate: sync is blocked if any source file is missing from Drive

---

## v2.2.0 — 2026-04-15 (Sprint 2)

- ActivityEvent wiring + Sprint 2 QA tooling
- ActivityAuditCenter tab
- feature_readiness_service initial implementation
- home_dashboard initial implementation
- recovery_center UI tab
- pre_sync_validator (643 lines)
- sync_ledger duplicate prevention
- report_coverage_validator
- First-run wizard

---

## v2.1.0 — 2026-04 (Sprint 1)

- Initial unified desktop app
- Toast POS Playwright download automation
- QB Sync via QBXML COM
- Remove Transactions
- Google Drive integration
- Bootstrap runtime + launcher
- Safe mode + crash reporter
- Diagnostics
