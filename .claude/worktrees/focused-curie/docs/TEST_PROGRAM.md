# Test Program

Tai lieu nay thay the cach noi "5000 tester test app" bang mot chuong trinh regression co the lap lai duoc.

## Current automated coverage

- `pytest` cho business logic trong `qb_sync.py`
- `pytest` cho delete policy
- `py_compile` cho cac module chinh
- `doctor-cli` smoke check cho environment diagnostics
- GitHub Actions Windows CI de chay test + build release pipeline tren moi push/PR, gom ca release zip va installer artifact

## Coverage groups

### A. Download Reports

- Session con hieu luc / session het han
- Chon location
- Chon date / custom date
- Download trigger
- Fallback tu selector-first sang keyboard flow

### B. QB Sync

- Category mapping
- Discounts
- Refunds
- Tax map
- Tips / gratuity
- Deferred gift cards
- Service charges
- Payment mapping
- Over/short balancing
- Missing optional sheets

### C. Remove Transactions

- Dry-run default
- Live delete policy lock
- Export snapshot truoc delete
- Audit artifact sau run

### D. Packaging / Runtime

- Diagnostics startup
- CLI doctor
- PyInstaller build smoke test

## Recommended manual matrix before release

- Windows 10 / 11
- QuickBooks Enterprise 22 / 23 / 24
- 1-store va multi-store
- Local file va Google Drive source
- Toast login moi / saved session
- Delete flow voi dry-run va maintenance-only live delete

## Exit criteria for internal release

- CI xanh tren `main`
- Tat ca `pytest` pass
- Build smoke pass
- Diagnostics chi con warning expected tren may chua config
- Manual smoke pass cho Download Reports, QB Sync, Remove Transactions
