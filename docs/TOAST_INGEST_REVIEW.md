# Toast Report Ingest Review

## Scope

This repository is a desktop integration app for:

- Toast report download
- Google Drive upload/download
- file validation
- QuickBooks sync from Toast sales summary

It is not yet a full warehouse-style ingestion service with `upload_files`, `raw_*`, and `normalized_*` database tables for every Toast export. That gap is architectural, not just a missing parser.

## Current Coverage Matrix

| Report | Detect | Validate | Download Automation | Google Drive Folder/Search | QB Consumer |
| --- | --- | --- | --- | --- | --- |
| Sale Summary | Yes | Yes | Yes | Yes | Yes |
| Order Details | Yes | Yes | Yes | Yes | No |
| Payment Details | Yes | Yes | Yes | Yes | No |
| Item Selection Details | Yes | Yes | Yes | Yes | No |
| Modifier Selection Details | Yes | Yes | Yes | Yes | No |
| Product Mix (All Items) | Yes | Yes | Yes | Yes | No |
| Discounts | Yes | Yes | Yes | Yes | No |
| Time Entries / Labor Summary / Payroll Export | Yes | Yes | No | Yes | No |
| Accounting | Yes | Yes | No | Yes | No |
| Menu Configuration | Yes | Yes | No | Yes | No |
| Kitchen Details | Yes | Basic | No | Yes | No |
| Cash Management | Yes | Basic | No | Yes | No |

## Notes

- `Download Automation = Yes` means the UI can select the report and the Playwright flow has a verified or intentionally mapped report page/tab.
- `Download Automation = No` means the source supports manual export plus Google Drive upload, but the desktop app does not claim a verified Toast navigation path for that report yet.
- `QB Consumer = Yes` is currently limited to Sale Summary for accounting sync into QuickBooks.

## Google Drive Conventions Supported

The source now tolerates:

- `Toasttab/<Store>/<Report>/file`
- `Toasttab/<Store>/<YYYY>/<MM>/<Report>/file`
- `ToastUploads/<Brand>/<Store>/<YYYY>/<MM>/<Report>/file`
- legacy `Toast Reports/<Store>/file`

## Validation Rules Implemented

Schema-aware validation now checks required columns for:

- Order Details
- Payment Details
- Discounts
- Item Selection Details
- Modifier Selection Details
- Product Mix
- Time Entries
- Accounting
- Menu

Sales Summary still uses workbook sheet validation.

Kitchen Details and Cash Management currently have lightweight required-column checks only.

## Remaining Gaps To Reach Full Database-Team Goal

This repo still needs a backend ingestion service if the company wants true:

- `upload_files`
- `raw_order_details`, `raw_payment_details`, `raw_item_selection`, `raw_modifier_selection`
- `orders`, `payments`, `order_items`, `order_item_modifiers`, `daily_store_sales`
- row-level dedup
- data dictionary API
- error catalog UI for warehouse ingestion

Those features should live in a dedicated API/data pipeline project, or a new ingestion module, not only inside this desktop QB integration app.
