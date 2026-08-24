"""Deterministic authority boundary for graph-derived containment proposals.

The detector may recommend an action, but this module owns the scope of that
action.  Entity-wide proposals are converted into a transaction-level plan and
both the requested and enforced contracts receive canonical SHA-256 identities.
"""

from __future__ import annotations

import hashlib
import json


PROPOSALS = {
    "block_card": {
        "title": "Block shared card profile",
        "entityType": "card",
        "requestedAction": "block_entity",
    },
    "block_address": {
        "title": "Block shared address",
        "entityType": "address",
        "requestedAction": "block_entity",
    },
    "hold_component": {
        "title": "Hold entire connected component",
        "entityType": "component",
        "requestedAction": "hold_component",
    },
}


def canonical_identity(value: dict) -> str:
    """Return a stable identity for the exact contract being evaluated."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _target_entity(incident: dict, entity_type: str) -> str:
    if entity_type == "component":
        return f"component:{incident['incidentId']}"
    for edge in incident["edges"]:
        if edge["source"] == incident["targetTransaction"] and edge["type"] == entity_type:
            return edge["target"]
    raise ValueError(f"target transaction has no {entity_type} relationship")


def _component_transaction_ids(incident: dict) -> list[str]:
    """Return only transactions connected to the target in the bipartite graph."""
    adjacency: dict[str, set[str]] = {}
    for edge in incident["edges"]:
        adjacency.setdefault(edge["source"], set()).add(edge["target"])
        adjacency.setdefault(edge["target"], set()).add(edge["source"])
    visited: set[str] = set()
    pending = [incident["targetTransaction"]]
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        pending.extend(adjacency.get(node, set()) - visited)
    event_ids = {event["transactionId"] for event in incident["events"]}
    return sorted(visited & event_ids)


def compile_containment(incident: dict, proposal: str = "block_card") -> dict:
    """Preview the collateral impact and compile the smallest permitted plan.

    Historical truth is used only for replay evaluation fields. It is explicitly
    excluded from the policy input and therefore cannot influence the live plan.
    """
    if proposal not in PROPOSALS:
        raise ValueError(f"proposal must be one of {sorted(PROPOSALS)}")
    spec = PROPOSALS[proposal]
    entity_id = _target_entity(incident, spec["entityType"])
    events = {event["transactionId"]: event for event in incident["events"]}
    if spec["entityType"] == "component":
        affected_ids = _component_transaction_ids(incident)
    else:
        affected_ids = sorted({
            edge["source"] for edge in incident["edges"]
            if edge["target"] == entity_id and edge["source"] in events
        })

    safe_plan = {
        "hold": sorted(incident["blastRadius"]["hold"]),
        "review": sorted(incident["blastRadius"]["review"]),
        "allow": sorted(incident["blastRadius"]["allow"]),
    }
    allowed_ids = set(safe_plan["allow"])
    affected = [events[transaction_id] for transaction_id in affected_ids]
    known_legitimate = [event for event in affected if event.get("truth") == "legitimate"]
    spared = [event for event in known_legitimate if event["transactionId"] in allowed_ids]
    operational_spared = [event for event in affected if event["transactionId"] in allowed_ids]
    labelled = [event for event in affected if event.get("truth") in {"fraud", "legitimate"}]

    policy_input = {
        "schemaVersion": "sentinelgraph.containment.v1",
        "interventionPoint": "pre_containment_action",
        "incidentId": incident["incidentId"],
        "targetTransaction": incident["targetTransaction"],
        "proposal": {
            "kind": spec["requestedAction"],
            "entityType": spec["entityType"],
            "entityId": entity_id,
            "transactionIds": affected_ids,
        },
    }
    enforced_contract = {
        **policy_input,
        "proposal": {
            "kind": "transaction_plan",
            "hold": safe_plan["hold"],
            "review": safe_plan["review"],
            "allow": safe_plan["allow"],
            "reversible": True,
            "requiresAnalystApproval": True,
        },
    }
    input_identity = canonical_identity(policy_input)
    enforced_identity = canonical_identity(enforced_contract)

    return {
        "schemaVersion": "sentinelgraph.containment.v1",
        "verdict": "transform",
        "reasonCode": "ENTITY_SCOPE_EXCEEDS_AUTHORITY",
        "reason": (
            "Entity-wide containment may affect unrelated customers. The proposal "
            "was replaced with reversible transaction-level actions that require analyst approval."
        ),
        "requested": {
            "proposal": proposal,
            "title": spec["title"],
            "entityType": spec["entityType"],
            "entityId": entity_id,
            "transactionIds": affected_ids,
            "paymentsTouched": len(affected),
            "volumeFrozenInr": round(sum(float(event["amount"]) for event in affected), 2),
        },
        "safePlan": {
            **safe_plan,
            "reviewCount": len(safe_plan["review"]),
            "holdCount": len(safe_plan["hold"]),
            "untouchedCount": len(safe_plan["allow"]),
        },
        "resolvedReplayEvaluation": {
            "knownLegitimateTouched": len(known_legitimate),
            "knownLegitimateVolumeSparedInr": round(
                sum(float(event["amount"]) for event in spared), 2
            ),
            "labelAvailability": "post-resolution only; excluded from policy input",
            "labelsAvailable": len(labelled),
        },
        "operationalImpact": {
            "paymentsSparedFromBroadAction": len(operational_spared),
            "volumeSparedFromBroadActionInr": round(
                sum(float(event["amount"]) for event in operational_spared), 2
            ),
            "method": "Affected payments assigned allow by the transaction policy; no outcome labels required.",
        },
        "inputIdentity": input_identity,
        "enforcedIdentity": enforced_identity,
        "identityChanged": input_identity != enforced_identity,
    }
