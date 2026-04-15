from dataclasses import dataclass


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


@dataclass
class DeletePolicy:
    allow_live_delete: bool
    source: str
    approver: str = ""

    @property
    def is_locked(self) -> bool:
        return not self.allow_live_delete

    @property
    def mode_label(self) -> str:
        return "Dry-run only" if self.is_locked else "Live delete enabled"

    @property
    def guidance(self) -> str:
        if self.is_locked:
            return (
                "Live delete is locked by policy. Dry-run/export remains available. "
                "Enable only for approved accounting maintenance."
            )
        approver_text = f" Approver: {self.approver}." if self.approver else ""
        return f"Live delete is enabled from {self.source}.{approver_text}".strip()


def load_delete_policy(local_config: dict | None = None, env_values: dict | None = None) -> DeletePolicy:
    local_config = local_config or {}
    env_values = env_values or {}
    config_policy = local_config.get("delete_policy", {}) or {}

    allow = _parse_bool(config_policy.get("allow_live_delete"))
    source = "default"

    env_allow = _parse_bool(env_values.get("ALLOW_LIVE_DELETE"))
    if env_allow is not None:
        allow = env_allow
        source = ".env.qb"
    elif allow is not None:
        source = "local-config.json"

    if allow is None:
        allow = False

    approver = str(env_values.get("DELETE_APPROVER") or config_policy.get("approver") or "").strip()
    return DeletePolicy(allow_live_delete=allow, source=source, approver=approver)
