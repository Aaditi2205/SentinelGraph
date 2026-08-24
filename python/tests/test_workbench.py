from riskpilot.policy import PolicyConfig
from riskpilot.workbench import compile_workbench


def fixture():
    return {
        "incidentId": "LIVE-TEST",
        "targetTransaction": "pay_4",
        "proposal": "block_card",
        "events": [
            {"transactionId": "pay_1", "amount": 900, "risk": 0.01, "card": "card-a", "device": "device-1", "address": "addr-1"},
            {"transactionId": "pay_2", "amount": 1200, "risk": 0.04, "card": "card-a", "device": "device-2", "address": "addr-2"},
            {"transactionId": "pay_3", "amount": 800, "risk": 0.03, "card": "card-b", "device": "device-3", "address": "addr-9"},
            {"transactionId": "pay_4", "amount": 5000, "risk": 0.82, "card": "card-a", "device": "device-4", "address": "addr-9"},
            {"transactionId": "pay_5", "amount": 700, "risk": 0.02, "card": "card-z", "device": "device-z", "address": "addr-z"},
        ],
    }


def test_workbench_rebuilds_scope_from_merchant_entities():
    result = compile_workbench(fixture(), PolicyConfig())
    assert result["containment"]["requested"]["paymentsTouched"] == 3
    assert result["containment"]["operationalImpact"]["paymentsSparedFromBroadAction"] >= 1
    assert result["inputContract"]["persisted"] is False
    assert all(edge["target"] not in {"card-a", "addr-9"} for edge in result["incident"]["edges"])


def test_workbench_change_really_changes_blast_radius():
    payload = fixture()
    payload["events"][1]["card"] = "card-new"
    result = compile_workbench(payload)
    assert result["containment"]["requested"]["paymentsTouched"] == 2


def test_component_does_not_include_disconnected_payments():
    payload = fixture()
    payload["proposal"] = "hold_component"
    result = compile_workbench(payload)
    assert "pay_5" not in result["containment"]["requested"]["transactionIds"]


def test_duplicate_payment_fails_closed():
    payload = fixture()
    payload["events"][1]["transactionId"] = "pay_1"
    try:
        compile_workbench(payload)
    except ValueError as exc:
        assert "duplicate transactionId" in str(exc)
    else:
        raise AssertionError("duplicate transaction IDs must be rejected")
