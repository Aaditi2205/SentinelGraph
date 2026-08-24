"""Compile merchant-supplied payment events into a bounded containment incident.

This module deliberately does not pretend that a Razorpay-shaped payment contains
the 50 IEEE-CIS model features.  A merchant supplies the risk produced by its
detector; SentinelGraph owns the separate question: what scope may that detector
act on without freezing unrelated payments?
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from riskpilot.containment import compile_containment
from riskpilot.policy import PolicyConfig, decide


MAX_EVENTS = 80
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
ENTITY_FIELDS = ("card", "device", "address")


def _transaction_id(value: object) -> str:
    transaction_id = str(value or "").strip()
    if not SAFE_ID.fullmatch(transaction_id):
        raise ValueError("transactionId must be 1-64 letters, numbers, dots, colons, underscores or hyphens")
    return transaction_id


def _number(value: object, name: str, *, minimum: float, maximum: float) -> float:
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def _entity_token(kind: str, value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    digest = hashlib.sha256(f"{kind}:{raw}".encode("utf-8")).hexdigest()[:10]
    return f"{kind}_{digest}"


def compile_workbench(payload: dict, config: PolicyConfig | None = None) -> dict:
    """Build and evaluate a fresh, model-agnostic merchant incident.

    Risk probabilities are an explicit upstream contract.  They are never
    described as SentinelGraph inference.  Entity values are tokenised before
    entering the returned graph and are not persisted by this function.
    """
    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or not 2 <= len(raw_events) <= MAX_EVENTS:
        raise ValueError(f"events must contain between 2 and {MAX_EVENTS} payments")
    config = config or PolicyConfig()
    target_transaction = _transaction_id(payload.get("targetTransaction"))
    proposal = str(payload.get("proposal", "block_card"))
    incident_id = str(payload.get("incidentId") or "SG-LIVE-WORKBENCH")[:64]

    events: list[dict] = []
    nodes: list[dict] = []
    edges: list[dict] = []
    decisions: dict[str, dict] = {}
    seen_ids: set[str] = set()

    for index, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            raise ValueError("every event must be an object")
        transaction_id = _transaction_id(raw.get("transactionId"))
        if transaction_id in seen_ids:
            raise ValueError(f"duplicate transactionId: {transaction_id}")
        seen_ids.add(transaction_id)
        amount = _number(raw.get("amount"), "amount", minimum=0.01, maximum=100_000_000)
        contextual_risk = _number(
            raw.get("contextualRisk", raw.get("risk")), "contextualRisk", minimum=0.0, maximum=1.0
        )
        transaction_risk = _number(
            raw.get("transactionRisk", contextual_risk), "transactionRisk", minimum=0.0, maximum=1.0
        )
        offset = _number(raw.get("offsetMinutes", index * 10), "offsetMinutes", minimum=0.0, maximum=1_000_000)
        truth = raw.get("truth") if raw.get("truth") in {"fraud", "legitimate"} else None
        entity_tokens = {
            kind: _entity_token(kind, raw.get(kind)) for kind in ENTITY_FIELDS
        }
        if not any(entity_tokens.values()):
            raise ValueError(f"{transaction_id} must include at least one card, device or address token")

        decision = decide(contextual_risk, amount, config).to_dict()
        decisions[transaction_id] = decision
        evidence_ids = []
        for kind, token in entity_tokens.items():
            if not token:
                continue
            edges.append({"source": transaction_id, "target": token, "type": kind})
            evidence_ids.append(f"LIVE-{kind.upper()}-{token[-6:]}")

        event = {
            "sequence": index + 1,
            "transactionId": transaction_id,
            "amount": round(amount, 2),
            "offsetMinutes": round(offset, 2),
            "transactionRisk": transaction_risk,
            "ringRisk": contextual_risk,
            "riskSource": str(raw.get("riskSource") or "merchant_upstream_detector")[:80],
            "evidenceIds": evidence_ids,
        }
        if truth:
            event["truth"] = truth
        events.append(event)
        nodes.append({
            "id": transaction_id,
            "kind": "transaction",
            "label": f"₹{amount:,.0f}",
            "isTarget": transaction_id == target_transaction,
            **({"truth": truth} if truth else {}),
        })

    if target_transaction not in seen_ids:
        raise ValueError("targetTransaction must identify one of the supplied events")

    entity_kinds = {edge["target"]: edge["type"] for edge in edges}
    nodes.extend({"id": token, "kind": kind, "label": token} for token, kind in entity_kinds.items())
    blast_radius = {"hold": [], "review": [], "allow": []}
    for transaction_id, decision in decisions.items():
        blast_radius[decision["action"]].append(transaction_id)
    for action in blast_radius:
        blast_radius[action].sort()
    blast_radius["rule"] = (
        "Merchant/upstream risk chooses transaction actions; shared entities remain analyst-gated."
    )

    target_edges = [edge for edge in edges if edge["source"] == target_transaction]
    repeated_counts = []
    for edge in target_edges:
        count = sum(1 for candidate in edges if candidate["target"] == edge["target"]) - 1
        if count > 0:
            repeated_counts.append((edge["type"], count))
    target = next(event for event in events if event["transactionId"] == target_transaction)
    target_decision = decisions[target_transaction]
    relationship_summary = (
        ", ".join(f"{kind} shared with {count} prior payment{'s' if count != 1 else ''}" for kind, count in repeated_counts)
        if repeated_counts else "no target entity is repeated in this batch"
    )
    facts = [
        {"id": "LIVE-BATCH-01", "text": f"The supplied batch contains {len(events)} payments and {len(entity_kinds)} tokenised entities."},
        {"id": "LIVE-BATCH-02", "text": f"For {target_transaction}, {relationship_summary}."},
        {"id": "LIVE-RISK-01", "text": f"The merchant/upstream policy score for {target_transaction} is {target['ringRisk']:.1%}; SentinelGraph did not invent missing gateway features."},
    ]
    incident = {
        "incidentId": incident_id,
        "title": str(payload.get("title") or "Merchant-supplied containment simulation")[:120],
        "targetTransaction": target_transaction,
        "nodes": nodes,
        "edges": edges,
        "events": events,
        "facts": facts,
        "graphCounterfactuals": [],
        "agentSummary": f"Fresh merchant batch compiled. {relationship_summary.capitalize()}. The authority engine will evaluate the proposed scope before any entity-wide action is permitted.",
        "proposedAction": target_decision,
        "blastRadius": blast_radius,
        "toolTrace": [
            {"tool": "validate_merchant_batch", "result": f"{len(events)} unique payments · schema valid", "status": "passed"},
            {"tool": "tokenise_entity_keys", "result": f"{len(entity_kinds)} card/device/address tokens · raw values not retained", "status": "passed"},
            {"tool": "build_bipartite_graph", "result": f"{len(nodes)} nodes · {len(edges)} relationships", "status": "passed"},
            {"tool": "apply_cost_policy", "result": f"{len(blast_radius['hold'])} hold · {len(blast_radius['review'])} review · {len(blast_radius['allow'])} allow", "status": "passed"},
            {"tool": "compile_action_scope", "result": "Entity action converted to a reversible transaction plan", "status": "gated"},
        ],
        "scoringMode": "Merchant/upstream probabilities are supplied explicitly. SentinelGraph validates, tokenises, builds the graph, applies expected-cost policy and compiles containment live.",
        "dataNote": "Workbench rows are supplied by the operator. Entity values are SHA-256 tokenised in memory and the endpoint does not persist the batch.",
        "source": "merchant_workbench",
    }
    contract = compile_containment(incident, proposal)
    affected = set(contract["requested"]["transactionIds"])
    entity_by_transaction = {
        event["transactionId"]: [edge["type"] for edge in edges if edge["source"] == event["transactionId"]]
        for event in events
    }
    impact_ledger = [
        {
            "transactionId": event["transactionId"],
            "amount": event["amount"],
            "risk": event["ringRisk"],
            "policyAction": decisions[event["transactionId"]]["action"],
            "policyReason": decisions[event["transactionId"]]["reason"],
            "affectedByProposal": event["transactionId"] in affected,
            "relationships": entity_by_transaction[event["transactionId"]],
        }
        for event in events
    ]
    counts = Counter(item["policyAction"] for item in impact_ledger)
    return {
        "incident": incident,
        "containment": contract,
        "impactLedger": impact_ledger,
        "policySummary": {"hold": counts["hold"], "review": counts["review"], "allow": counts["allow"]},
        "inputContract": {
            "riskAuthority": "merchant_upstream_detector",
            "containmentAuthority": "sentinelgraph_transaction_only",
            "persisted": False,
            "entityValuesReturned": False,
        },
    }
