# Five-Year Self-Recovery Runbook

This runbook is written for operators who may need to keep the app running without active developer support.

## Core rule

Always start with `Settings -> Startup Diagnostics` and `Settings -> Recovery Center -> Export Health Report` before changing runtime files.

## Scenario: Toast password changed

Symptoms:
- Toast download opens a login screen again
- The saved Toast session no longer works
- Download flow never reaches the report page

Steps:
1. Open `Settings -> Recovery Center`.
2. Use `Backup + Reset Toast Session`.
3. Run one small download again and sign in with the new Toast password.
4. Test one store and one date before any batch run.

## Scenario: Internet is down or unstable

Symptoms:
- Toast reports cannot load
- Google Drive auth or upload fails
- Diagnostics show reachability warnings

Steps:
1. Re-run diagnostics and confirm the network warning.
2. If reports already exist locally, continue with `QB Sync` from local files.
3. Do not reset Toast session or Google token only because the network is down.
4. Retry download or upload only after the connection is stable.

## Scenario: QuickBooks does not open or connect

Symptoms:
- QB Sync fails before QuickBooks becomes ready
- Remove Transactions cannot attach to QuickBooks
- Diagnostics warn about QB executable or company files

Steps:
1. Confirm the correct `.qbw` path still exists in `local-config.json`.
2. Open QuickBooks manually with the expected company file.
3. Retry one preview sync from the app.
4. If QuickBooks itself is unstable, stop and resolve the QB environment before running production sync.

## Scenario: Strict mode blocks sync

Symptoms:
- Validation issues appear and sync stops
- New category, tax, or payment types are unmapped

Steps:
1. Export the validation issues from the `QB Sync` tab.
2. Update the relevant mapping file.
3. Re-run in preview mode.
4. Only run production sync after preview is clean.

## Scenario: Google Drive token expired

Symptoms:
- Uploads fail
- Google auth prompts again

Steps:
1. Use `Backup + Reset Google Token`.
2. Reconnect Google Drive in `Settings`.
3. Test one small upload before batch work.

## Scenario: New machine after years of use

Steps:
1. Install QuickBooks Desktop and confirm the correct `.qbw` files are available.
2. Build or install the app.
3. Open `Recovery Center` and create `.env.qb` and `local-config.json` from examples if missing.
4. Reconnect Google Drive and sign in to Toast again.
5. Test one download, one preview sync, and one dry-run delete search before using the app normally.

## Hard stop rules

- Never disable strict mode for normal production use.
- Never run live delete before verifying the snapshot and audit flow.
- Never guess mappings when validation says a category, payment, or tax type is unmapped.
- Never assume an internet problem is a credential problem.

## What to archive for long-term survival

- The latest known-good installer
- At least one older rollback installer
- A copy of local mapping files
- Health reports for major environment changes
- QuickBooks backup procedures owned by ops/accounting
