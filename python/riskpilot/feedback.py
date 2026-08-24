"""Bounded analyst feedback and entity-risk state updates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


ALLOWED_DECISIONS = {"confirm_fraud", "mark_legitimate", "request_more_evidence"}


@dataclass
class FeedbackStore:
    incident_state: dict[str, dict] = field(default_factory=dict)
    entity_state: dict[str, dict] = field(default_factory=dict)

    def _apply_entity_label(self, entity_id: str, decision: str, direction: int, incident_id: str, timestamp: str) -> None:
        state = self.entity_state.setdefault(entity_id, {
            "confirmedFraud": 0, "confirmedLegitimate": 0, "riskSignal": 0,
            "lastDecision": None, "lastIncident": None, "lastUpdated": None,
        })
        if decision == "confirm_fraud":
            state["confirmedFraud"] = max(0, state["confirmedFraud"] + direction)
            state["riskSignal"] += direction
        elif decision == "mark_legitimate":
            state["confirmedLegitimate"] = max(0, state["confirmedLegitimate"] + direction)
            state["riskSignal"] -= direction
        state.update({
            "lastDecision": decision if direction > 0 else f"reversed:{decision}",
            "lastIncident": incident_id, "lastUpdated": timestamp,
        })

    def apply(self, incident_id: str, decision: str, analyst: str, note: str, entity_ids: list[str]) -> dict:
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"decision must be one of {sorted(ALLOWED_DECISIONS)}")
        if not analyst.strip():
            raise ValueError("analyst is required")
        previous = self.incident_state.get(incident_id)
        reversal = bool(previous and previous["decision"] != decision)
        normalized_entities = sorted(set(entity_ids))
        state_changed = not previous or previous["decision"] != decision or previous.get("entityIds", []) != normalized_entities
        timestamp = datetime.now(timezone.utc).isoformat()
        record = {
            "incidentId": incident_id, "decision": decision,
            "analyst": analyst.strip(), "note": note.strip()[:500],
            "timestamp": timestamp, "reversal": reversal,
            "previousDecision": previous["decision"] if previous else None,
            "stateChanged": state_changed, "entityIds": normalized_entities,
        }
        if state_changed and previous:
            for entity_id in previous.get("entityIds", []):
                self._apply_entity_label(entity_id, previous["decision"], -1, incident_id, timestamp)
        if state_changed:
            for entity_id in normalized_entities:
                self._apply_entity_label(entity_id, decision, 1, incident_id, timestamp)
        self.incident_state[incident_id] = record
        return {
            **record, "updatedEntities": len(normalized_entities) if state_changed else 0,
            "entityState": {key: self.entity_state[key] for key in normalized_entities},
        }

    def clear(self, incident_id: str, analyst: str, note: str = "") -> dict:
        """Remove an incident resolution and reverse only the entity state it contributed."""
        if not analyst.strip():
            raise ValueError("analyst is required")
        previous = self.incident_state.get(incident_id)
        timestamp = datetime.now(timezone.utc).isoformat()
        if not previous:
            return {
                "incidentId": incident_id, "decision": "clear_resolution",
                "analyst": analyst.strip(), "note": note.strip()[:500],
                "timestamp": timestamp, "previousDecision": None,
                "stateChanged": False, "updatedEntities": 0, "entityState": {},
            }
        entity_ids = previous.get("entityIds", [])
        reversed_state = {}
        for entity_id in entity_ids:
            self._apply_entity_label(entity_id, previous["decision"], -1, incident_id, timestamp)
            reversed_state[entity_id] = dict(self.entity_state[entity_id])
            state = self.entity_state[entity_id]
            if state["confirmedFraud"] == 0 and state["confirmedLegitimate"] == 0 and state["riskSignal"] == 0:
                del self.entity_state[entity_id]
        del self.incident_state[incident_id]
        return {
            "incidentId": incident_id, "decision": "clear_resolution",
            "analyst": analyst.strip(), "note": note.strip()[:500],
            "timestamp": timestamp, "previousDecision": previous["decision"],
            "stateChanged": True, "updatedEntities": len(entity_ids),
            "entityState": reversed_state,
        }
