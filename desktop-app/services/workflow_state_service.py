"""Simple in-memory workflow state manager for wizards."""
import uuid
from models.workflow_state import WorkflowState, WizardStep

_states: dict = {}


def create_workflow(wizard_id: str = "") -> WorkflowState:
    wid = wizard_id or uuid.uuid4().hex[:8]
    state = WorkflowState(wizard_id=wid)
    _states[wid] = state
    return state


def get_workflow(wizard_id: str):
    return _states.get(wizard_id)


def advance_step(state: WorkflowState) -> WorkflowState:
    order = list(WizardStep)
    idx = order.index(state.current_step)
    if idx + 1 < len(order):
        state.current_step = order[idx + 1]
    return state


def go_back(state: WorkflowState) -> WorkflowState:
    order = list(WizardStep)
    idx = order.index(state.current_step)
    if idx > 0:
        state.current_step = order[idx - 1]
    return state


def reset_workflow(state: WorkflowState) -> WorkflowState:
    state.current_step = WizardStep.SELECT_STORE
    state.is_running = False
    state.is_complete = False
    state.result = {}
    state.error = ""
    return state
