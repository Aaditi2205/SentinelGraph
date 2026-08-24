import pytest

from riskpilot.feedback import FeedbackStore


def test_feedback_updates_entities_and_records_reversal():
    store = FeedbackStore()
    first = store.apply("inc-1", "confirm_fraud", "analyst@example.com", "confirmed", ["card-1", "device-1"])
    assert first["updatedEntities"] == 2
    second = store.apply("inc-1", "mark_legitimate", "analyst@example.com", "customer verified", ["card-1", "device-1"])
    assert second["reversal"] is True
    assert second["entityState"]["card-1"]["confirmedLegitimate"] == 1
    assert second["entityState"]["card-1"]["confirmedFraud"] == 0
    assert second["entityState"]["card-1"]["riskSignal"] == -1


def test_repeated_same_decision_is_idempotent():
    store = FeedbackStore()
    store.apply("inc-1", "confirm_fraud", "analyst", "", ["card-1"])
    repeated = store.apply("inc-1", "confirm_fraud", "analyst", "duplicate click", ["card-1"])
    assert repeated["stateChanged"] is False
    assert repeated["updatedEntities"] == 0
    assert repeated["entityState"]["card-1"]["confirmedFraud"] == 1


def test_clear_resolution_reverses_contribution():
    store = FeedbackStore()
    store.apply("INC-1", "confirm_fraud", "analyst", "", ["card:1", "device:1"])
    cleared = store.clear("INC-1", "analyst", "customer verified")
    assert cleared["stateChanged"] is True
    assert cleared["previousDecision"] == "confirm_fraud"
    assert "INC-1" not in store.incident_state
    assert cleared["entityState"]["card:1"]["confirmedFraud"] == 0
    assert cleared["entityState"]["card:1"]["riskSignal"] == 0
    assert cleared["entityState"]["card:1"]["lastDecision"] == "reversed:confirm_fraud"
    assert "card:1" not in store.entity_state


def test_feedback_is_bounded():
    with pytest.raises(ValueError):
        FeedbackStore().apply("inc-1", "block_everything", "analyst", "", [])
