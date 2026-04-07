from toast_reports import get_report_type, infer_report_type, normalize_report_types


def test_get_report_type_supports_legacy_aliases():
    assert get_report_type("order").key == "orders"
    assert get_report_type("item_detail").key == "order_items"
    assert get_report_type("payment").key == "payments"


def test_normalize_report_types_deduplicates_canonical_and_legacy_keys():
    reports = normalize_report_types(["orders", "order", "payments", "payment"])

    assert [report.key for report in reports] == ["orders", "payments"]


def test_infer_report_type_reads_legacy_folder_names():
    report = infer_report_type(("Stockton", "Item Detail"), "ItemDetails_2026-04-06.csv")

    assert report.key == "order_items"


def test_infer_report_type_reads_new_filename_patterns():
    report = infer_report_type(("Stockton",), "menu_items_2026-04-06.csv")

    assert report.key == "menu_items"
