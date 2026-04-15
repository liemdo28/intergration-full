import os

from audit_utils import load_recent_item_creation_audits, write_item_creation_audit


def test_load_recent_item_creation_audits_returns_newest_first(tmp_path):
    first = write_item_creation_audit(
        {
            "generated_at": "2026-03-28T20:00:00",
            "store": "Stockton",
            "created_item": "Marketplace:Uber Fee",
            "status": "created",
        },
        tmp_path,
        prefix="a_item-create",
    )

    second = write_item_creation_audit(
        {
            "generated_at": "2026-03-28T20:01:00",
            "store": "Stockton",
            "created_item": "Marketplace:DoorDash Fee",
            "status": "created",
        },
        tmp_path,
        prefix="b_item-create",
    )

    os.utime(first["json_path"], (1, 1))
    os.utime(second["json_path"], (2, 2))

    records = load_recent_item_creation_audits(tmp_path, limit=5)

    assert len(records) == 2
    assert records[0]["created_item"] == "Marketplace:DoorDash Fee"
    assert records[1]["created_item"] == "Marketplace:Uber Fee"
    assert records[0]["_audit_path"].endswith(".json")
