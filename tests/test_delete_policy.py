from delete_policy import load_delete_policy


def test_delete_policy_defaults_to_locked():
    policy = load_delete_policy({}, {})
    assert policy.is_locked is True
    assert policy.mode_label == "Dry-run only"
    assert policy.source == "default"


def test_delete_policy_can_be_enabled_from_local_config():
    policy = load_delete_policy(
        {"delete_policy": {"allow_live_delete": True, "approver": "Accounting Lead"}},
        {},
    )
    assert policy.allow_live_delete is True
    assert policy.source == "local-config.json"
    assert policy.approver == "Accounting Lead"


def test_delete_policy_env_overrides_local_config():
    policy = load_delete_policy(
        {"delete_policy": {"allow_live_delete": False}},
        {"ALLOW_LIVE_DELETE": "1", "DELETE_APPROVER": "Ops Admin"},
    )
    assert policy.allow_live_delete is True
    assert policy.source == ".env.qb"
    assert policy.approver == "Ops Admin"
