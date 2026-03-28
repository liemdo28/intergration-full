# Release Readiness Checklist

Tai lieu nay chot lai bug list P0/P1/P2 va roadmap de dua `Toast POS Manager` tu desktop tool noi bo thanh ban Windows release-ready.

## Current snapshot

Da hoan tat:

- App da hop nhat 3 luong chinh: Download Reports, QB Sync, Remove Transactions
- Da co diagnostics startup va CLI doctor
- Da co safe-delete co `dry run`, export snapshot, va audit log
- Da co PyInstaller spec va script build EXE
- Da co test ban dau cho `qb_sync.py`

Chua hoan tat:

- Chua co installer / signed release / auto-update
- Chua co CI cho test + build
- Chua rewrite git history de xoa secret cu
- Chua du test fixture cho cac edge case accounting thuc te
- Toast/QB automation van con brittle o mot so flow

## P0 - Must fix before broader rollout

- [ ] Rotate toan bo Toast credentials da tung xuat hien trong git history
- [ ] Xac nhan automation/may that khong con dung credential cu
- [ ] Rewrite git history de purge file config cu roi force-push lai cac remote
- [x] Khoa live delete theo policy mac dinh, chi mo khoa qua config duoc phe duyet
- [ ] Chot quy trinh delete an toan cho user that
  - [ ] Xac dinh role nao duoc delete that
  - [x] Mac dinh dry-run cho user thuong
  - [x] Luu audit log va snapshot nhu artifact bat buoc
- [ ] Giam hanh vi recovery manh tay trong QuickBooks automation
  - [ ] Han che kill process khong can thiet
  - [ ] Them huong dan loi ro rang khi open/login that bai

## P1 - Needed for stable internal release

- [ ] Tang do ben cho Toast automation
  - [ ] Giam phu thuoc vao `Tab`, `Enter`, `wait_for_timeout`
  - [ ] Tang selector theo structure thay vi text-only
  - [ ] Them retry/fallback cho login, chon location, chon date
- [ ] Tang semantic validation trong diagnostics
  - [ ] Check `.qbw` path con ton tai
  - [ ] Check mapping store co du field bat buoc
  - [ ] Check auth files co dung format co ban
- [ ] Mo rong test cho `qb_sync.py`
  - [ ] discounts
  - [ ] refunds
  - [ ] tax mapping
  - [ ] tips / gratuity
  - [ ] service charges
  - [ ] payment split
  - [ ] over/short
  - [ ] malformed workbook / missing sheet
- [ ] Hoan thien packaging
  - [ ] icon / version metadata
  - [ ] installer
  - [ ] release note/checklist
  - [ ] huong dan update cho user noi bo

## P2 - Important for maintainability

- [ ] Tach bot orchestration khoi `desktop-app/app.py`
- [ ] Them CI cho syntax check, pytest, va desktop build smoke test
- [ ] Nang cap UX cho less-technical users
  - [ ] thong diep loi ro hon
  - [ ] summary ket qua sau moi job
  - [ ] huong dan config ngay trong app
- [ ] Chuan hoa release checklist va folder artifact

## Suggested execution order

1. Security freeze
2. Delete safety policy
3. Toast/QB reliability hardening
4. Test expansion
5. Packaging + installer
6. CI + refactor

## Working definition of release-ready

Chi nen goi la release-ready khi dat du cac dieu kien sau:

- Secrets cu da duoc rotate va purge khoi history
- Build EXE lap lai duoc bang script, co version ro rang
- Installer cho phep user noi bo cai dat khong can setup tay Python
- Diagnostics bat duoc loi cau hinh pho bien truoc khi user thao tac
- Delete flow co guardrail ro rang, audit day du, va policy su dung
- `qb_sync.py` co regression tests cho cac workbook quan trong
- Co CI de chan regressions co ban truoc khi push release

## Linked docs

- [Secret remediation](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/docs/SECRET_REMEDIATION.md)
- [Desktop app README](E:/Project/Toasttab Quickbook/New folder/integration-toasttab-qb/desktop-app/README.md)
