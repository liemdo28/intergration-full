# integration-toasttab-qb

Source cuoi cua ung dung nam trong folder [desktop-app](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/desktop-app).

## Repo layout

- [desktop-app](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/desktop-app): source app desktop Toast -> QuickBooks
- [launch.bat](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/launch.bat): launcher nhanh tu root repo
- [.gitignore](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/.gitignore): bo qua file local, build output, audit logs

## Chay local

```bat
launch.bat
```

Hoac:

```powershell
cd "E:\Project\Toasttab Quickbook\New folder\integration-toasttab-qb\desktop-app"
python app.py
```

## Build Windows EXE

```powershell
cd "E:\Project\Toasttab Quickbook\New folder\integration-toasttab-qb\desktop-app"
.\build_release.ps1
```

Output mac dinh:

- `desktop-app\dist\ToastPOSManager\ToastPOSManager.exe`
- `desktop-app\release\ToastPOSManager-<timestamp>-<commit>.zip`

## Planning docs

- [Release readiness checklist](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/RELEASE_READINESS_CHECKLIST.md)
- [Secret remediation](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/SECRET_REMEDIATION.md)
- [Test program](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/TEST_PROGRAM.md)
- [Current state review](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/CURRENT_STATE_REVIEW.md)
- [Operator guide](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/OPERATOR_GUIDE.md)
- [Final app requirements](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/FINAL_APP_REQUIREMENTS.md)
- [Five-year self-recovery runbook](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/FIVE_YEAR_SELF_RECOVERY_RUNBOOK.md)
- [Engineering policy](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/POLICY.md)

## Security note

Legacy commits cua repo cu co chua credential that. Muc hardening trong source da duoc them, nhung rotate secret va rewrite Git history van la buoc bat buoc truoc khi phat hanh rong.

## Repo gate

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\final_app_gate.ps1
```
