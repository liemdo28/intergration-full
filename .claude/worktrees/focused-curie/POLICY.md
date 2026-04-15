# Engineering Policy

This policy applies to all changes in this repository.

## 1. Data Safety

- No silent accounting mismatch is allowed.
- `Strict accounting mode` must remain enabled by default.
- Any code change that affects financial logic must include or update tests.

## 2. Validation Rules

- Unmapped categories, tax, and payment values must produce visible validation issues.
- Blocking issues must stop sync in strict mode.
- Non-blocking issues must still be visible to the operator.

## 3. Release Discipline

- Every release candidate must pass CI tests, diagnostics, and build steps.
- Every release bundle must include the desktop `.exe`, release zip, and installer artifact when the pipeline supports it.
- Release metadata must identify the version or timestamp and commit hash.

## 4. Security

- No real secrets may be committed to the repository.
- Local credentials must stay in environment files or machine-local config only.
- Secret rotation is mandatory after any exposure incident.

## 5. Automation Safety

- All UI automation must include retry, timeout, and fallback behavior where feasible.
- No critical step should depend only on fixed sleep timing.
- Failure messages should tell ops whether the problem is UI automation, configuration, or source data.

## 6. Testing

- Core flows must keep regression coverage for sync, validation, and destructive operations.
- Every bug fix should include a targeted test when the behavior is testable.
- Build and packaging scripts are part of the release surface and must be verified regularly.

## 7. Logging and Audit

- Destructive actions must keep snapshots and audit output.
- Validation and delete results must remain exportable for ops review.
- Logs should support operator troubleshooting without requiring source-code knowledge.

## 8. Responsibilities

- `Dev`: implement code, tests, and migration notes when needed.
- `Reviewer`: validate logic, safety, and release impact.
- `Ops`: verify the app against real-world environments and accounting outcomes.
