"""Razorpay webhook verification and monotonic payment-state ingestion.

This module deliberately operates on raw bytes. Razorpay signs the exact raw
request body, and event delivery can be duplicated or arrive out of order.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field


PAYMENT_STAGE = {
    "payment.failed": 0,
    "payment.authorized": 1,
    "payment.captured": 2,
}


def scoring_contract() -> dict:
    """Describe the explicit boundary between gateway events and model features."""
    return {
        "contract": "RiskEventV1",
        "eligibleForIeeeModel": False,
        "gatewayFields": ["paymentId", "orderId", "amountPaise", "currency", "method", "status", "createdAt"],
        "missingFeatureGroups": [
            "merchant transaction history", "card-profile aggregates",
            "device or identity links", "address links", "velocity windows",
        ],
        "fallback": "Authenticate, deduplicate, preserve payment state, and route to feature enrichment; do not fabricate an IEEE-CIS score.",
    }


def signature_for(raw_body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def valid_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    return hmac.compare_digest(signature_for(raw_body, secret), signature)


def canonical_payment_event(payload: dict) -> dict:
    event = str(payload.get("event", ""))
    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if not event or not payment.get("id"):
        raise ValueError("Razorpay event and payload.payment.entity.id are required")
    return {
        "event": event,
        "paymentId": str(payment["id"]),
        "orderId": payment.get("order_id"),
        "amountPaise": int(payment.get("amount", 0)),
        "amountRupees": round(int(payment.get("amount", 0)) / 100, 2),
        "currency": payment.get("currency", "INR"),
        "method": payment.get("method"),
        "status": payment.get("status"),
        "createdAt": int(payment.get("created_at", payload.get("created_at", 0))),
    }


@dataclass
class RazorpayIngress:
    seen_event_ids: set[str] = field(default_factory=set)
    payment_state: dict[str, dict] = field(default_factory=dict)

    def ingest(self, raw_body: bytes, signature: str, event_id: str, secret: str) -> tuple[dict, int]:
        if not valid_signature(raw_body, signature, secret):
            return {"accepted": False, "signatureValid": False, "reason": "signature mismatch"}, 401
        if not event_id:
            return {"accepted": False, "signatureValid": True, "reason": "missing x-razorpay-event-id"}, 400
        if event_id in self.seen_event_ids:
            return {
                "accepted": False, "signatureValid": True, "duplicate": True,
                "eventId": event_id, "reason": "event id already processed",
            }, 200
        try:
            payload = json.loads(raw_body)
            canonical = canonical_payment_event(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return {"accepted": False, "signatureValid": True, "reason": str(exc)}, 400

        self.seen_event_ids.add(event_id)
        previous = self.payment_state.get(canonical["paymentId"])
        incoming_rank = PAYMENT_STAGE.get(canonical["event"], -1)
        previous_rank = PAYMENT_STAGE.get(previous["event"], -1) if previous else -1
        out_of_order = previous is not None and incoming_rank < previous_rank
        state_applied = not out_of_order
        if state_applied:
            self.payment_state[canonical["paymentId"]] = canonical
        return {
            "accepted": True, "signatureValid": True, "duplicate": False,
            "eventId": event_id, "outOfOrder": out_of_order,
            "stateApplied": state_applied, "canonical": canonical,
            "currentState": self.payment_state.get(canonical["paymentId"]),
            "riskScoring": scoring_contract(),
        }, 202
