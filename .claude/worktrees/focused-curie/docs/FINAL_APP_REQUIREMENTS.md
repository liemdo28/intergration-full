# Final App Requirements

This checklist defines what must be true before the app should be called a final Windows product.

## P0 Blockers

- Rotate all exposed or previously exposed secrets for Toast, QuickBooks, and Google integrations.
- Rewrite Git history to remove leaked credentials and force-push remediated history.
- Add secret scanning in CI so new leaks are blocked automatically.

## P1 Reliability

- Add retry, timeout, and fallback behavior for every critical Toast automation step.
- Add stronger QuickBooks window-state detection and recovery instead of timing-only waits.
- Capture and classify automation failures so ops can tell the difference between environment issues and report-data issues.

## P1 Distribution

- Keep installer generation guaranteed in CI.
- Add release manifest metadata with version, commit, build time, and artifact names.
- Add artifact signing for public distribution.

## P2 Product Readiness

- Add startup version check and an update delivery path.
- Add crash logging with exportable reports.
- Improve user-facing error messages so operators do not need raw logs for common failures.

## P2 UX

- Group validation issues by severity, store, and report.
- Add fix suggestions for common issue types such as missing mappings.
- Add a per-run summary view for stores, dates, success counts, and blocked counts.

## P3 Scalability

- Continue moving Toast logic toward more stable selectors and structured navigation.
- Keep QuickBooks configuration data-driven so store changes do not require code edits.
- Expand workbook fixtures and regression tests whenever Toast report formats drift.
