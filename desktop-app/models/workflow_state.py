"""Wizard workflow state models."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WizardStep(str, Enum):
    SELECT_STORE = "select_store"
    SELECT_DATE_RANGE = "select_date_range"
    VALIDATE_READINESS = "validate_readiness"
    RUN_ACTION = "run_action"
    SHOW_RESULT = "show_result"


@dataclass
class WorkflowState:
    wizard_id: str
    current_step: WizardStep = WizardStep.SELECT_STORE
    selected_stores: list = field(default_factory=list)
    date_start: str = ""  # YYYY-MM-DD
    date_end: str = ""
    selected_report_types: list = field(default_factory=list)
    is_running: bool = False
    is_complete: bool = False
    result: dict = field(default_factory=dict)
    error: str = ""

    @property
    def step_number(self) -> int:
        order = list(WizardStep)
        try:
            return order.index(self.current_step) + 1
        except ValueError:
            return 1

    @property
    def total_steps(self) -> int:
        return len(WizardStep)
