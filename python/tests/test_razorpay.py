import json

from riskpilot.razorpay import RazorpayIngress, scoring_contract, signature_for


def event(name="payment.captured", status="captured"):
    return {"event": name, "created_at": 1, "payload": {"payment": {"entity": {"id": "pay_1", "order_id": "order_1", "amount": 12345, "currency": "INR", "method": "upi", "status": status, "created_at": 1}}}}


def test_signature_duplicate_and_canonical_amount():
    ingress, secret = RazorpayIngress(), "secret"
    raw = json.dumps(event(), separators=(",", ":")).encode()
    result, status = ingress.ingest(raw, signature_for(raw, secret), "evt_1", secret)
    assert status == 202 and result["canonical"]["amountRupees"] == 123.45
    duplicate, _ = ingress.ingest(raw, signature_for(raw, secret), "evt_1", secret)
    assert duplicate["duplicate"] and not duplicate["accepted"]


def test_tampering_and_out_of_order_do_not_mutate_state():
    ingress, secret = RazorpayIngress(), "secret"
    captured = json.dumps(event(), separators=(",", ":")).encode()
    ingress.ingest(captured, signature_for(captured, secret), "evt_1", secret)
    authorized = json.dumps(event("payment.authorized", "authorized"), separators=(",", ":")).encode()
    result, _ = ingress.ingest(authorized, signature_for(authorized, secret), "evt_2", secret)
    assert result["outOfOrder"] and not result["stateApplied"]
    assert result["currentState"]["event"] == "payment.captured"
    rejected, status = ingress.ingest(authorized, "bad", "evt_3", secret)
    assert status == 401 and not rejected["signatureValid"]


def test_gateway_payload_cannot_silently_enter_ieee_model():
    contract = scoring_contract()
    assert contract["eligibleForIeeeModel"] is False
    assert "device or identity links" in contract["missingFeatureGroups"]
    assert "do not fabricate" in contract["fallback"]
