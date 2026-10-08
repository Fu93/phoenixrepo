import pytest

from state.machine import InvalidTransition, PhoenixState, StateMachine


def test_valid_transition():
    machine = StateMachine(PhoenixState.INGESTED)
    machine.transition(PhoenixState.RECONNAISSANCE_COMPLETE)
    assert machine.current_state == PhoenixState.RECONNAISSANCE_COMPLETE


def test_invalid_transition():
    machine = StateMachine(PhoenixState.INGESTED)
    with pytest.raises(InvalidTransition):
        machine.transition(PhoenixState.BUILDING)


def test_value_decision_requires_evidence():
    machine = StateMachine(PhoenixState.MARKET_ANALYZED)
    with pytest.raises(InvalidTransition):
        machine.transition(PhoenixState.VALUE_DECISION)


def test_value_decision_with_evidence():
    machine = StateMachine(PhoenixState.MARKET_ANALYZED)
    machine.transition(PhoenixState.VALUE_DECISION, evidence_ids=["EV-001", "EV-002"])
    assert machine.current_state == PhoenixState.VALUE_DECISION
