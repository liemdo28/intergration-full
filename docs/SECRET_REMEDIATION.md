# Secret Remediation

Legacy history in this repository contains committed Toast credentials.

Verified example:

- Commit `913a0fa`
- File path: `intergration Toast/config.json`

## Immediate actions

1. Revoke or rotate every Toast credential that ever appeared in legacy config files.
2. Replace them with fresh credentials.
3. Confirm no downstream automation still depends on the revoked values.

## Current repo hardening

- Runtime secrets are kept out of git via [.gitignore](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/.gitignore)
- Example config files stay in source, writable files stay local
- `history_secret_audit.ps1` helps re-scan history before cleanup
- `rewrite_secret_history.ps1` prepares or executes a controlled history rewrite after rotation is complete

## Recommended history cleanup

History rewrite has collaboration impact because it requires force-pushing rewritten refs.

Suggested flow:

1. Freeze pushes to the repository.
2. Create a backup clone.
3. Rewrite history to remove legacy secret-bearing files.
4. Force-push rewritten refs to every remote.
5. Ask all collaborators to re-clone or hard reset to the rewritten history.

Helper scripts:

- Audit only: [tools/history_secret_audit.ps1](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/tools/history_secret_audit.ps1)
- Rewrite helper: [tools/rewrite_secret_history.ps1](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/tools/rewrite_secret_history.ps1)

## Candidate paths to purge from history

- `intergration Toast/config.json`
- `intergration Toast/config.example.json`
- `Codex/config112.json`
- `Codex/config.example.json`
- `Claude/config_cl.json`
- `Claude/config(withIDAPP).json`
