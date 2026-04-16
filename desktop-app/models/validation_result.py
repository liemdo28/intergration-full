"""Validation result models for preflight checks."""
from dataclasses import dataclass, field


@dataclass
class ValidationItem:
    label: str
    ok: bool
    message: str
    fix_hint: str = ""


@dataclass
class ValidationResult:
    items: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(i.ok for i in self.items)

    @property
    def blocking_items(self) -> list:
        return [i for i in self.items if not i.ok]

    def add(self, label: str, ok: bool, message: str, fix_hint: str = "") -> "ValidationResult":
        self.items.append(ValidationItem(label, ok, message, fix_hint))
        return self
