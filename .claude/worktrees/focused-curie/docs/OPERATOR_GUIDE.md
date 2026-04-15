# Operator Guide

This guide is for internal users running the Windows desktop app.

## Before you start

- Install QuickBooks Desktop on the same Windows machine.
- Keep the correct `.qbw` company file available locally or on a reachable drive.
- Make sure Chromium for Playwright is installed if you run from Python source.
- Keep `credentials.json`, `token.json`, `.toast-session.json`, `.env.qb`, and `local-config.json` local to the machine and out of Git.

## First-time setup

1. Open the app and review the diagnostics in `Settings`.
2. Create `.env.qb` from `.env.qb.example` and fill the required QuickBooks password slots.
3. Select each store's `.qbw` file so the app can save paths in `local-config.json`.
4. If you use Google Drive, place `credentials.json` in the app runtime folder and complete the auth flow.

## Download reports

1. Open `Download Reports`.
2. Choose the store and date or date range.
3. Start the download and wait for the run to complete.
4. Confirm the report appears in `toast-reports`.

If Toast login or navigation fails, re-check the session and be ready for UI changes on the Toast side.

## Sync to QuickBooks

1. Open `QB Sync`.
2. Keep `Strict accounting mode` enabled for production runs.
3. Select source, store, and date.
4. Review any `Validation Issues` before retrying or overriding.
5. Use preview mode when testing new mappings or debugging reports.

Do not disable strict mode for normal production use.

## Validation issues

- `Error` issues are blocking in strict mode.
- Export issues to `CSV` or `JSON` when you need to hand them to ops or dev.
- Fix missing mappings before re-running production sync.

## Remove transactions safely

1. Start with `Dry run only`.
2. Review the result set carefully.
3. Confirm that the snapshot and audit trail were created.
4. Only unlock live delete during an approved maintenance window.

There is no true undo after a real QB delete.

## Escalate to dev when

- Toast UI changes break report download.
- QuickBooks no longer opens or logs in reliably.
- Validation issues appear for mapped categories or payments that used to work.
- Reports look structurally different from past Toast exports.
