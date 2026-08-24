"""Dependency-light local server for the SentinelGraph buildathon demo."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import joblib
import pandas as pd

from riskpilot.policy import PolicyConfig, decide
from riskpilot.containment import compile_containment
from riskpilot.feedback import FeedbackStore
from riskpilot.copilot import InvestigationCopilot
from riskpilot.razorpay import RazorpayIngress, scoring_contract, signature_for
from riskpilot.retrieval import PolicyRetriever
from riskpilot.workbench import compile_workbench


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ARTIFACTS = ROOT / "artifacts"
RUNTIME_STATE_PATH = ARTIFACTS / "runtime_state.json"
POLICIES = PolicyRetriever(ROOT / "knowledge" / "policies.json")
DEMO_SECRET = b"sentinelgraph-local-demo-secret"
AUDIT_LOG: list[dict] = []
SEEN_EVENTS: set[str] = set()
LOCK = threading.Lock()
RAZORPAY_INGRESS = RazorpayIngress()
FEEDBACK = FeedbackStore()
TRANSACTION_BUNDLE = joblib.load(ARTIFACTS / "transaction_model.joblib")
GRAPH_BUNDLE = joblib.load(ARTIFACTS / "graph_model.joblib")
COPILOT = InvestigationCopilot(POLICIES)


def load_runtime_state() -> None:
    if not RUNTIME_STATE_PATH.exists():
        return
    try:
        state = json.loads(RUNTIME_STATE_PATH.read_text(encoding="utf-8"))
        AUDIT_LOG.extend(state.get("auditLog", []))
        FEEDBACK.incident_state.update(state.get("incidentState", {}))
        FEEDBACK.entity_state.update({
            entity_id: entity_state
            for entity_id, entity_state in state.get("entityState", {}).items()
            if any(entity_state.get(key, 0) != 0 for key in ("confirmedFraud", "confirmedLegitimate", "riskSignal"))
        })
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"Runtime state ignored because it could not be validated: {exc}")


def persist_runtime_state() -> None:
    state = {
        "version": 1, "auditLog": AUDIT_LOG,
        "incidentState": FEEDBACK.incident_state,
        "entityState": FEEDBACK.entity_state,
    }
    temporary = RUNTIME_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, RUNTIME_STATE_PATH)


load_runtime_state()


def read_json(name: str):
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def hash_record(record: dict) -> str:
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def verify_audit_chain(records: list[dict] | None = None) -> bool:
    chain = AUDIT_LOG if records is None else records
    previous = "GENESIS"
    for record in chain:
        candidate = {key: value for key, value in record.items() if key != "recordHash"}
        if candidate.get("previousHash") != previous:
            return False
        if not hmac.compare_digest(record.get("recordHash", ""), hash_record(candidate)):
            return False
        previous = record["recordHash"]
    return True


def score_model(bundle: dict, values: dict) -> dict:
    frame = pd.DataFrame([[values[column] for column in bundle["features"]]], columns=bundle["features"])
    raw = float(bundle["model"].predict_proba(frame)[:, 1][0])
    calibrated = float(bundle["calibrator"].predict([raw])[0])
    return {"rawScore": raw, "calibratedRisk": calibrated}


def live_incident_scores(incident: dict) -> dict:
    inputs = read_json("incident_inputs.json")
    transaction = score_model(TRANSACTION_BUNDLE, inputs["transaction"])
    graph = score_model(GRAPH_BUNDLE, inputs["graph"])
    counterfactuals = []
    templates = {item["removed"]: item for item in incident.get("graphCounterfactuals", [])}
    for removed, values in inputs["masked"].items():
        masked = score_model(GRAPH_BUNDLE, values)
        counterfactuals.append({
            **templates[removed],
            "riskAfterRemoval": masked["calibratedRisk"],
            "riskDeltaPp": (graph["calibratedRisk"] - masked["calibratedRisk"]) * 100,
            "rawScoreAfterRemoval": masked["rawScore"],
            "rawScoreDeltaPp": (graph["rawScore"] - masked["rawScore"]) * 100,
        })
    counterfactuals.sort(key=lambda item: item["riskDeltaPp"], reverse=True)
    return {"transaction": transaction, "graph": graph, "counterfactuals": counterfactuals}


def config_from(payload: dict) -> PolicyConfig:
    return PolicyConfig(
        fraud_loss_multiplier=float(payload.get("fraudLossMultiplier", 1.0)),
        chargeback_fee=float(payload.get("chargebackFee", 1_500)),
        review_cost=float(payload.get("reviewCost", 120)),
        false_hold_rate=float(payload.get("falseHoldRate", 0.12)),
        customer_friction_cost=float(payload.get("frictionCost", 250)),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "SentinelGraph/1.0"

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def json_response(self, value, status: int = 200) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.json_response({
                "status": "ok",
                "graphModel": "ready" if (ARTIFACTS / "incident.json").exists() else "missing",
                "version": "sentinelgraph-1.0",
            })
            return
        if path == "/api/metrics":
            self.json_response(read_json("sentinel_metrics.json"))
            return
        if path == "/api/incident":
            self.json_response(read_json("incident.json"))
            return
        if path == "/api/audit":
            with LOCK:
                self.json_response(AUDIT_LOG[-50:])
            return
        if path == "/api/audit/verify":
            with LOCK:
                self.json_response({"valid": verify_audit_chain(), "records": len(AUDIT_LOG)})
            return
        if path == "/api/feedback":
            with LOCK:
                self.json_response({"incidents": FEEDBACK.incident_state, "entities": FEEDBACK.entity_state})
            return
        if path == "/api/drift":
            metrics = read_json("sentinel_metrics.json")
            self.json_response(metrics.get("drift", {"status": "unavailable", "features": []}))
            return
        if path == "/api/razorpay/status":
            configured = bool(os.environ.get("RAZORPAY_WEBHOOK_SECRET"))
            self.json_response({
                "configured": configured,
                "mode": "test" if configured else "setup-required",
                "rawBodyVerification": True,
                "duplicateGuard": "x-razorpay-event-id",
                "outOfOrderProtection": True,
            })
            return
        if path == "/api/razorpay/coverage":
            self.json_response(scoring_contract())
            return
        if path == "/api/copilot/status":
            status = COPILOT.status()
            evaluation_path = ARTIFACTS / "rag_eval.json"
            status["retrievalEvaluation"] = (
                json.loads(evaluation_path.read_text(encoding="utf-8"))
                if evaluation_path.exists() else {"status": "not-run"}
            )
            self.json_response(status)
            return
        self.serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/razorpay/webhook":
                self.razorpay_webhook()
                return
            payload = self.body()
            if path == "/api/agent/decide":
                self.agent_decision(payload)
                return
            if path == "/api/containment/preview":
                self.containment_preview(payload)
                return
            if path == "/api/workbench/compile":
                self.workbench_compile(payload)
                return
            if path == "/api/webhook/verify":
                self.verify_demo_webhook(payload)
                return
            if path == "/api/feedback":
                self.analyst_feedback(payload)
                return
            if path == "/api/feedback/reset":
                self.clear_analyst_feedback(payload)
                return
            if path == "/api/razorpay/simulate":
                self.simulate_razorpay(payload)
                return
            if path == "/api/copilot/brief":
                self.copilot_brief(payload)
                return
            self.json_response({"error": "Not found"}, 404)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, 400)

    def containment_preview(self, payload: dict) -> None:
        """Return a read-only, server-computed proposal-to-plan transform."""
        incident = read_json("incident.json")
        result = compile_containment(incident, str(payload.get("proposal", "block_card")))
        self.json_response({
            **result,
            "computedBy": "riskpilot.containment.compile_containment",
            "live": True,
            "executed": False,
        })

    def workbench_compile(self, payload: dict) -> None:
        """Compile a fresh merchant batch; persist only an optional action receipt."""
        result = compile_workbench(payload, config_from(payload.get("costs", {})))
        audit = None
        if bool(payload.get("commit", False)):
            contract = result["containment"]
            incident = result["incident"]
            timestamp = datetime.now(timezone.utc).isoformat()
            with LOCK:
                previous_hash = AUDIT_LOG[-1]["recordHash"] if AUDIT_LOG else "GENESIS"
                record = {
                    "incidentId": incident["incidentId"],
                    "target": incident["targetTransaction"],
                    "action": "transform_entity_scope",
                    "failureMode": "healthy",
                    "guardrail": "blocked",
                    "policyId": "POL-MBR-01",
                    "inputIdentity": contract["inputIdentity"],
                    "enforcedIdentity": contract["enforcedIdentity"],
                    "source": "merchant_workbench",
                    "timestamp": timestamp,
                    "previousHash": previous_hash,
                }
                record["recordHash"] = hash_record(record)
                AUDIT_LOG.append(record)
                chain_valid = verify_audit_chain()
                persist_runtime_state()
            audit = {
                "previousHash": previous_hash,
                "recordHash": record["recordHash"],
                "chainValid": chain_valid,
            }
        self.json_response({
            **result,
            "computedBy": "riskpilot.workbench.compile_workbench",
            "live": True,
            "executed": bool(payload.get("commit", False)),
            "audit": audit,
        })

    def agent_decision(self, payload: dict) -> None:
        incident = read_json("incident.json")
        target = next(
            event for event in incident["events"]
            if event["transactionId"] == incident["targetTransaction"]
        )
        failure = payload.get("failureMode", "healthy")
        if failure == "model":
            live_scores = {"transaction": {"rawScore": None, "calibratedRisk": None}, "graph": {"rawScore": None, "calibratedRisk": None}, "counterfactuals": []}
        elif failure in {"graph", "identity"}:
            inputs = read_json("incident_inputs.json")
            transaction_score = score_model(TRANSACTION_BUNDLE, inputs["transaction"])
            live_scores = {"transaction": transaction_score, "graph": {"rawScore": None, "calibratedRisk": None}, "counterfactuals": []}
        else:
            live_scores = live_incident_scores(incident)
        unsafe_entity_block = bool(payload.get("requestEntityBlock", False))
        containment = (
            compile_containment(incident, str(payload.get("proposal", "block_card")))
            if unsafe_entity_block else None
        )
        graph_available = failure not in {"graph", "model", "identity"}
        evidence_available = failure != "evidence"
        score = live_scores["graph"]["calibratedRisk"] if graph_available else live_scores["transaction"]["calibratedRisk"]

        measured_drift = read_json("sentinel_metrics.json").get("drift", {})
        measured_pause = measured_drift.get("status") == "pause"
        distribution_stable = failure != "drift" and not measured_pause
        missing_identity = failure == "identity"
        if failure == "model":
            outcome = decide(None, target["amount"], config_from(payload.get("costs", {})), model_available=False).to_dict()
        elif not graph_available:
            config = config_from(payload.get("costs", {}))
            outcome = {
                "action": "review", "risk_probability": score, "degraded": True,
                "costs": {"allow": 0.0, "review": config.review_cost, "hold": 0.0},
                "reason": "Temporal graph context timed out. Ring-level containment is disabled; the transaction is routed to human review instead of guessing from incomplete evidence.",
            }
        else:
            outcome = decide(
                score, target["amount"], config_from(payload.get("costs", {})),
                distribution_stable=distribution_stable,
            ).to_dict()
        if missing_identity:
            outcome = {
                "action": "review", "risk_probability": score, "degraded": True,
                "costs": {"allow": 0.0, "review": config_from(payload.get("costs", {})).review_cost, "hold": 0.0},
                "reason": "Device and address identity are missing. The transaction model remains available, but automated graph containment is disabled until identity evidence is restored.",
            }

        analyst_resolution = FEEDBACK.incident_state.get(incident["incidentId"])
        if analyst_resolution:
            human_actions = {
                "confirm_fraud": ("hold", "An analyst confirmed fraud. A reversible transaction-level hold is now human-authorized."),
                "mark_legitimate": ("allow", "An analyst verified this transaction as legitimate. The human resolution supersedes the model recommendation."),
                "request_more_evidence": ("review", "An analyst requested more evidence. No automated hold or allow action will execute."),
            }
            outcome["action"], outcome["reason"] = human_actions[analyst_resolution["decision"]]
            outcome["degraded"] = False

        guardrail = {
            "requested": containment["requested"]["title"] if containment else "transaction-level containment",
            "status": "blocked" if unsafe_entity_block else "gated" if outcome["degraded"] else "passed",
            "reason": (
                containment["reason"]
                if unsafe_entity_block else
                outcome["reason"]
                if outcome["degraded"] else
                "All actions are transaction-scoped and reversible; no shared identity is globally blocked."
            ),
        }
        if analyst_resolution and not unsafe_entity_block:
            guardrail = {
                "requested": f"analyst resolution: {analyst_resolution['decision']}",
                "status": "human-authorized",
                "reason": f"Human decision by {analyst_resolution['analyst']} at {analyst_resolution['timestamp']} supersedes automation for this incident only.",
            }
        if unsafe_entity_block:
            outcome["action"] = "review"
            outcome["reason"] = "The requested entity-wide action was rejected. This case remains with a human reviewer."
            outcome["degraded"] = True

        facts = (
            [dict(fact) for fact in incident["facts"]]
            if evidence_available and graph_available and not missing_identity else []
        )
        if containment and facts:
            facts.extend([
                {
                    "id": "EV-SCOPE-01",
                    "text": (
                        f"The requested {containment['requested']['title'].lower()} "
                        f"touches {containment['requested']['paymentsTouched']} payments."
                    ),
                },
                {
                    "id": "EV-REWRITE-01",
                    "text": (
                        f"The permitted rewrite contains {containment['safePlan']['reviewCount']} reviews, "
                        f"{containment['safePlan']['holdCount']} holds, and "
                        f"{containment['safePlan']['untouchedCount']} untouched payments."
                    ),
                },
            ])
        if analyst_resolution:
            explanation = outcome["reason"]
        elif containment:
            explanation = (
                f"{containment['requested']['title']} would touch "
                f"{containment['requested']['paymentsTouched']} payments. "
                f"The Decision Firewall rejected entity-wide authority and rewrote it to "
                f"{containment['safePlan']['reviewCount']} reviews, "
                f"{containment['safePlan']['holdCount']} holds, and "
                f"{containment['safePlan']['untouchedCount']} untouched payments."
            )
        elif outcome["degraded"]:
            explanation = outcome["reason"]
        elif facts:
            explanation = incident["agentSummary"]
        else:
            explanation = "Structured graph evidence is unavailable. No generated claim is shown; inspect the raw transaction and retry the evidence service."
        policy = POLICIES.retrieve(
            "model timeout unavailable degraded route review" if not graph_available
            else f"{outcome['action']} expected cost human approval"
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        with LOCK:
            previous_hash = AUDIT_LOG[-1]["recordHash"] if AUDIT_LOG else "GENESIS"
            record = {
                "incidentId": incident["incidentId"], "target": target["transactionId"],
                "action": outcome["action"], "failureMode": failure,
                "guardrail": guardrail["status"], "policyId": policy["id"],
                "inputIdentity": containment["inputIdentity"] if containment else None,
                "enforcedIdentity": containment["enforcedIdentity"] if containment else None,
                "timestamp": timestamp, "previousHash": previous_hash,
            }
            record["recordHash"] = hash_record(record)
            AUDIT_LOG.append(record)
            chain_valid = verify_audit_chain()
            persist_runtime_state()

        tool_trace = [dict(step) for step in incident["toolTrace"]]
        envelope_step = {
            "tool": "check_operating_envelope",
            "result": (
                f"PAUSE · max PSI {float(measured_drift.get('max_psi', 0.0)):.3f} exceeds 0.25; automated hold disabled"
                if not distribution_stable else
                f"PASS · max PSI {float(measured_drift.get('max_psi', 0.0)):.3f} inside boundary"
            ),
            "status": "gated" if not distribution_stable else "passed",
        }
        tool_trace.insert(max(len(tool_trace) - 1, 0), envelope_step)
        for step in tool_trace:
            if step["tool"] == "verify_event":
                fixture_body = json.dumps({"incidentId": incident["incidentId"], "target": target["transactionId"]}, separators=(",", ":")).encode()
                fixture_signature = hmac.new(DEMO_SECRET, fixture_body, hashlib.sha256).hexdigest()
                fixture_valid = hmac.compare_digest(fixture_signature, hmac.new(DEMO_SECRET, fixture_body, hashlib.sha256).hexdigest())
                step["result"] = f"Replay fixture HMAC recomputed · valid={str(fixture_valid).lower()}"
            elif step["tool"] == "score_transaction":
                step["result"] = "Model inference unavailable" if failure == "model" else f"Live calibrated standalone risk {live_scores['transaction']['calibratedRisk']:.1%}"
            elif step["tool"] == "estimate_ring_risk" and graph_available:
                step["result"] = f"Live contextual risk {live_scores['graph']['calibratedRisk']:.1%}"
            if failure == "model" and step["tool"] in {"expand_temporal_graph", "estimate_ring_risk", "minimize_blast_radius"}:
                step["result"] = "Not invoked after model timeout"
            elif failure == "graph" and step["tool"] in {"expand_temporal_graph", "estimate_ring_risk", "minimize_blast_radius"}:
                step["result"] = "Graph dependency unavailable"
            elif failure == "identity" and step["tool"] in {"expand_temporal_graph", "estimate_ring_risk", "minimize_blast_radius"}:
                step["result"] = "Identity context missing"
            if analyst_resolution and step["tool"] == "minimize_blast_radius":
                step["result"] = f"Human resolution applied · {analyst_resolution['decision']}"
                step["status"] = "human-authorized"

        self.json_response({
            **outcome, "failureMode": failure, "guardrail": guardrail,
            "containment": containment,
            "analystResolution": analyst_resolution,
            "facts": facts, "explanation": explanation, "policyBasis": policy,
            "scores": {
                "transactionRisk": live_scores["transaction"]["calibratedRisk"],
                "graphRisk": live_scores["graph"]["calibratedRisk"],
            },
            "graphCounterfactuals": live_scores["counterfactuals"] if graph_available else [],
            "liveComputation": {
                "modelInference": failure != "model",
                "calibration": failure != "model",
                "counterfactualRescoring": graph_available and not missing_identity,
                "operatingEnvelope": {
                    "status": measured_drift.get("status", "unavailable"),
                    "maxPsi": measured_drift.get("max_psi"),
                    "automaticActionAllowed": distribution_stable,
                },
                "replaySource": "locked-test-derived fixture",
            },
            "toolTrace": [
                {**step, "status": "unavailable"}
                if (
                    failure in {"graph", "identity"} and step["tool"] in {"expand_temporal_graph", "estimate_ring_risk", "minimize_blast_radius"}
                ) or (
                    failure == "model" and step["tool"] in {"score_transaction", "expand_temporal_graph", "estimate_ring_risk", "minimize_blast_radius"}
                )
                else step for step in tool_trace
            ],
            "audit": {"previousHash": previous_hash, "recordHash": record["recordHash"], "chainValid": chain_valid},
        })

    def verify_demo_webhook(self, payload: dict) -> None:
        mode = payload.get("mode", "valid")
        event_id = payload.get("eventId", "evt_sg_042")
        body = json.dumps({"eventId": event_id, "incident": "SG-INC-042"}, separators=(",", ":")).encode()
        signature = hmac.new(DEMO_SECRET, body, hashlib.sha256).hexdigest()
        received = "tampered" if mode == "tampered" else signature
        valid = hmac.compare_digest(signature, received)
        with LOCK:
            duplicate = event_id in SEEN_EVENTS or mode == "duplicate"
            accepted = valid and not duplicate
            if accepted:
                SEEN_EVENTS.add(event_id)
        self.json_response({
            "eventId": event_id, "signatureValid": valid, "duplicate": duplicate,
            "accepted": accepted,
            "reason": "accepted" if accepted else "signature mismatch" if not valid else "replay key already seen",
        })

    def razorpay_webhook(self) -> None:
        """Verify the exact raw request body before parsing it."""
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
        if not secret:
            self.json_response({"accepted": False, "reason": "RAZORPAY_WEBHOOK_SECRET is not configured"}, 503)
            return
        signature = self.headers.get("X-Razorpay-Signature", "")
        event_id = self.headers.get("x-razorpay-event-id", "")
        with LOCK:
            result, status = RAZORPAY_INGRESS.ingest(raw_body, signature, event_id, secret)
        self.json_response(result, status)

    def simulate_razorpay(self, payload: dict) -> None:
        """Exercise the same ingress state machine with official-shaped fixtures."""
        mode = payload.get("mode", "ordered")
        secret = "sentinelgraph-fixture-only"
        run_id = str(payload.get("runId") or datetime.now(timezone.utc).strftime("%H%M%S%f"))
        payment_id = f"pay_demo_{run_id}"
        event_name = "payment.authorized" if mode == "out_of_order" else "payment.captured"
        fixture = {
            "event": event_name, "created_at": 1,
            "payload": {"payment": {"entity": {
                "id": payment_id, "order_id": "order_demo", "amount": 249900,
                "currency": "INR", "method": "upi",
                "status": event_name.split(".")[-1], "created_at": 1,
            }}},
        }
        if mode == "out_of_order" and payment_id not in RAZORPAY_INGRESS.payment_state:
            newer = json.loads(json.dumps(fixture))
            newer["event"] = "payment.captured"
            newer["payload"]["payment"]["entity"]["status"] = "captured"
            raw_newer = json.dumps(newer, separators=(",", ":")).encode()
            RAZORPAY_INGRESS.ingest(raw_newer, signature_for(raw_newer, secret), f"evt_demo_captured_{run_id}", secret)
        raw = json.dumps(fixture, separators=(",", ":")).encode()
        result, status = RAZORPAY_INGRESS.ingest(raw, signature_for(raw, secret), f"evt_demo_{mode}_{run_id}", secret)
        self.json_response(result, status)

    def analyst_feedback(self, payload: dict) -> None:
        incident = read_json("incident.json")
        incident_id = str(payload.get("incidentId", incident["incidentId"]))
        target_edges = [edge for edge in incident["edges"] if edge["source"] == incident["targetTransaction"]]
        entity_ids = [edge["target"] for edge in target_edges]
        with LOCK:
            result = FEEDBACK.apply(
                incident_id, str(payload.get("decision", "")),
                str(payload.get("analyst", "")), str(payload.get("note", "")), entity_ids,
            )
            previous_hash = AUDIT_LOG[-1]["recordHash"] if AUDIT_LOG else "GENESIS"
            record = {
                "incidentId": incident_id, "target": incident["targetTransaction"],
                "action": f"analyst:{result['decision']}", "failureMode": "none",
                "guardrail": "human-authorized", "policyId": "ANALYST-FEEDBACK",
                "timestamp": result["timestamp"], "previousHash": previous_hash,
            }
            record["recordHash"] = hash_record(record)
            AUDIT_LOG.append(record)
            result["audit"] = {"recordHash": record["recordHash"], "chainValid": verify_audit_chain()}
            persist_runtime_state()
        self.json_response(result, 201)

    def clear_analyst_feedback(self, payload: dict) -> None:
        incident = read_json("incident.json")
        incident_id = str(payload.get("incidentId", incident["incidentId"]))
        with LOCK:
            result = FEEDBACK.clear(
                incident_id, str(payload.get("analyst", "")), str(payload.get("note", "")),
            )
            previous_hash = AUDIT_LOG[-1]["recordHash"] if AUDIT_LOG else "GENESIS"
            record = {
                "incidentId": incident_id, "target": incident["targetTransaction"],
                "action": "analyst:clear_resolution", "failureMode": "none",
                "guardrail": "human-authorized", "policyId": "ANALYST-FEEDBACK",
                "timestamp": result["timestamp"], "previousHash": previous_hash,
            }
            record["recordHash"] = hash_record(record)
            AUDIT_LOG.append(record)
            result["audit"] = {"recordHash": record["recordHash"], "chainValid": verify_audit_chain()}
            persist_runtime_state()
        self.json_response(result, 200)

    def copilot_brief(self, payload: dict) -> None:
        incident = read_json("incident.json")
        decision = dict(incident["proposedAction"])
        with LOCK:
            analyst_resolution = FEEDBACK.incident_state.get(incident["incidentId"])
        if analyst_resolution:
            action_map = {
                "confirm_fraud": ("hold", "An analyst confirmed fraud and authorized a reversible transaction-level hold."),
                "mark_legitimate": ("allow", "An analyst verified this transaction as legitimate."),
                "request_more_evidence": ("review", "An analyst requested more evidence before any automated action."),
            }
            decision["action"], decision["reason"] = action_map[analyst_resolution["decision"]]
        result = COPILOT.generate(incident, decision, str(payload.get("question", "")))
        timestamp = datetime.now(timezone.utc).isoformat()
        with LOCK:
            previous_hash = AUDIT_LOG[-1]["recordHash"] if AUDIT_LOG else "GENESIS"
            policy_ids = [item["id"] for item in result["retrievedPolicies"]]
            record = {
                "incidentId": incident["incidentId"], "target": incident["targetTransaction"],
                "action": "copilot:brief", "failureMode": "none",
                "guardrail": "claim-gate-passed" if result["validation"]["passed"] else "claim-gate-blocked",
                "policyId": ",".join(policy_ids), "timestamp": timestamp,
                "previousHash": previous_hash,
            }
            record["recordHash"] = hash_record(record)
            AUDIT_LOG.append(record)
            result["audit"] = {"recordHash": record["recordHash"], "chainValid": verify_audit_chain()}
            persist_runtime_state()
        self.json_response(result)

    def serve_static(self, requested: str) -> None:
        relative = "index.html" if requested in {"", "/"} else requested.lstrip("/")
        target = (STATIC / relative).resolve()
        if STATIC.resolve() not in target.parents and target != STATIC.resolve():
            self.send_error(403)
            return
        if not target.is_file():
            target = STATIC / "index.html"
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    required = [ARTIFACTS / "sentinel_metrics.json", ARTIFACTS / "incident.json"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Missing artifacts: {', '.join(missing)}. Run build_sentinelgraph.py first.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"SentinelGraph running at http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
