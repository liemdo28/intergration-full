from toast_reports import DEFAULT_REPORT_TYPE_KEYS, get_download_report_types, get_report_type, infer_report_type, normalize_report_types


def test_get_report_type_supports_legacy_aliases():
    assert get_report_type("order").key == "orders"
    assert get_report_type("item_detail").key == "order_items"
    assert get_report_type("payment").key == "payments"
    assert get_report_type("modifier_selection_details").key == "modifier_selections"
    assert get_report_type("product_mix_all_items").key == "product_mix"
    assert get_report_type("labor_summary").key == "time_entries"


def test_normalize_report_types_deduplicates_canonical_and_legacy_keys():
    reports = normalize_report_types(["orders", "order", "payments", "payment"])

    assert [report.key for report in reports] == ["orders", "payments"]


def test_infer_report_type_reads_legacy_folder_names():
    report = infer_report_type(("Stockton", "Item Detail"), "ItemDetails_2026-04-06.csv")

    assert report.key == "order_items"


def test_infer_report_type_reads_new_filename_patterns():
    report = infer_report_type(("Stockton",), "menu_items_2026-04-06.csv")

    assert report.key == "menu_items"


def test_infer_report_type_reads_toast_export_filenames():
    assert infer_report_type(("Stockton", "Order Details"), "2026-04-01_OrderDetails_Store01.csv").key == "orders"
    assert infer_report_type(("Stockton", "Item Selection Details"), "2026-04-01_ItemSelectionDetails_Store01.csv").key == "order_items"
    assert infer_report_type(("Stockton", "Modifier Selection Details"), "2026-04-01_ModifierSelectionDetails_Store01.csv").key == "modifier_selections"
    assert infer_report_type(("Stockton", "Product Mix"), "2026-04-01_ProductMix_Store01.csv").key == "product_mix"


def test_default_download_report_keys_exclude_ingest_only_exports():
    download_keys = {item.key for item in get_download_report_types()}

    assert set(DEFAULT_REPORT_TYPE_KEYS) == download_keys
    assert "time_entries" not in download_keys
    assert "accounting" not in download_keys
