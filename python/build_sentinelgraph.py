"""Build the leakage-safe SentinelGraph evaluation and replay artifacts.

The raw IEEE-CIS files are never committed.  This script expects the official
``train_transaction.csv`` and ``train_identity.csv`` files, sorts them by
``TransactionDT`` and emits every graph feature *before* adding the current
event to graph state.  Confirmed-fraud neighbour features use a 24 hour
feedback delay, which avoids pretending that chargeback labels are immediate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict, deque
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from riskpilot.policy import PolicyConfig, decide
from riskpilot.monitoring import drift_status, population_stability_index


ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
FEEDBACK_DELAY_SECONDS = 86_400
REVIEW_BUDGET = 0.01
SEED = 42
# IEEE-CIS TransactionAmt is USD. Financial-policy outputs are normalized to
# INR with this explicit, fixed scenario rate. It is a demo assumption, not an
# observed transaction FX rate or a claim about the historical dataset period.
USD_TO_INR_SCENARIO = 83.0

TRANSACTION_COLUMNS = [
    "TransactionID", "isFraud", "TransactionDT", "TransactionAmt", "ProductCD",
    "card1", "card2", "card3", "card4", "card5", "card6", "addr1", "addr2",
    "P_emaildomain", "R_emaildomain", "C1", "C2", "C4", "C5", "C6", "C8",
    "C10", "C11", "C12", "C13", "C14", "D1", "D2", "D3", "D4", "D5",
    "D10", "D11", "D15", "V14", "V17", "V20", "V45", "V67", "V87",
    "V258", "V294",
]
IDENTITY_COLUMNS = [
    "TransactionID", "id_02", "id_31", "DeviceType", "DeviceInfo"
]
BASE_FEATURES = [
    "TransactionAmt", "product_code", "card4_code", "card6_code", "C1", "C2",
    "C4", "C5", "C6", "C8", "C10", "C11", "C12", "C13", "C14", "D1",
    "D2", "D3", "D4", "D5", "D10", "D11", "D15", "V14", "V17", "V20",
    "V45", "V67", "V87", "V258", "V294", "hour_sin", "hour_cos",
]
GRAPH_FEATURES = [
    "card_prior_count", "device_prior_count", "address_prior_count",
    "card_recent_1h", "device_recent_1h", "address_recent_1h",
    "card_distinct_devices", "device_distinct_cards", "card_distinct_addresses",
    "address_distinct_cards", "new_card_device_link", "new_card_address_link",
    "confirmed_fraud_neighbours", "confirmed_neighbour_count",
    "confirmed_fraud_ratio", "entity_novelty", "component_pressure",
]
DRIFT_MONITOR_FEATURES = [
    "card_recent_1h", "device_recent_1h", "address_recent_1h",
    "new_card_device_link", "new_card_address_link",
    "confirmed_fraud_ratio", "entity_novelty", "component_pressure",
]


def token(prefix: str, value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return f"{prefix}:{text}"


def composite(prefix: str, row: pd.Series, columns: list[str]) -> str | None:
    values = []
    for column in columns:
        value = row[column]
        values.append("?" if pd.isna(value) else str(value))
    if all(value == "?" for value in values):
        return None
    return f"{prefix}:" + "|".join(values)


def public_id(value: str | None) -> str:
    if value is None:
        return "missing"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:7]
    return f"{value.split(':', 1)[0]}_{digest}"


def load_frame(data_dir: Path) -> pd.DataFrame:
    transaction_path = data_dir / "train_transaction.csv"
    identity_path = data_dir / "train_identity.csv"
    if not transaction_path.exists() or not identity_path.exists():
        raise FileNotFoundError(
            "Expected train_transaction.csv and train_identity.csv. See README.md."
        )
    transactions = pd.read_csv(transaction_path, usecols=TRANSACTION_COLUMNS)
    identity = pd.read_csv(identity_path, usecols=IDENTITY_COLUMNS)
    frame = transactions.merge(identity, on="TransactionID", how="left", validate="one_to_one")
    frame = frame.sort_values(["TransactionDT", "TransactionID"], kind="stable").reset_index(drop=True)
    frame["product_code"] = pd.factorize(frame["ProductCD"], sort=True)[0]
    frame["card4_code"] = pd.factorize(frame["card4"], sort=True)[0]
    frame["card6_code"] = pd.factorize(frame["card6"], sort=True)[0]
    seconds = frame["TransactionDT"].to_numpy(dtype=float)
    frame["hour_sin"] = np.sin(2 * np.pi * (seconds % 86_400) / 86_400)
    frame["hour_cos"] = np.cos(2 * np.pi * (seconds % 86_400) / 86_400)
    return frame


def add_temporal_graph_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Stream events and return past-only features plus private entity metadata."""
    count: defaultdict[str, int] = defaultdict(int)
    first_seen: dict[str, int] = {}
    recent: defaultdict[str, deque[int]] = defaultdict(deque)
    confirmed_count: defaultdict[str, int] = defaultdict(int)
    confirmed_fraud: defaultdict[str, int] = defaultdict(int)
    card_devices: defaultdict[str, set[str]] = defaultdict(set)
    device_cards: defaultdict[str, set[str]] = defaultdict(set)
    card_addresses: defaultdict[str, set[str]] = defaultdict(set)
    address_cards: defaultdict[str, set[str]] = defaultdict(set)
    feedback: deque[tuple[int, int, tuple[str, ...]]] = deque()

    arrays = {feature: np.zeros(len(frame), dtype=np.float32) for feature in GRAPH_FEATURES}
    metadata: list[dict] = []

    for position, (_, row) in enumerate(frame.iterrows()):
        now = int(row["TransactionDT"])
        while feedback and feedback[0][0] <= now - FEEDBACK_DELAY_SECONDS:
            _, label, old_entities = feedback.popleft()
            for entity in old_entities:
                confirmed_count[entity] += 1
                confirmed_fraud[entity] += label

        card = composite("card", row, ["card1", "card2", "card3", "card5", "card6"])
        address = composite("addr", row, ["addr1", "addr2"])
        device = token("identity", row["id_02"])
        if device is None:
            device = token("device", row["DeviceInfo"])
        entities = tuple(entity for entity in (card, device, address) if entity)

        for entity in entities:
            window = recent[entity]
            while window and window[0] < now - 3_600:
                window.popleft()

        arrays["card_prior_count"][position] = count[card] if card else 0
        arrays["device_prior_count"][position] = count[device] if device else 0
        arrays["address_prior_count"][position] = count[address] if address else 0
        arrays["card_recent_1h"][position] = len(recent[card]) if card else 0
        arrays["device_recent_1h"][position] = len(recent[device]) if device else 0
        arrays["address_recent_1h"][position] = len(recent[address]) if address else 0
        arrays["card_distinct_devices"][position] = len(card_devices[card]) if card else 0
        arrays["device_distinct_cards"][position] = len(device_cards[device]) if device else 0
        arrays["card_distinct_addresses"][position] = len(card_addresses[card]) if card else 0
        arrays["address_distinct_cards"][position] = len(address_cards[address]) if address else 0
        arrays["new_card_device_link"][position] = float(
            bool(card and device and device not in card_devices[card])
        )
        arrays["new_card_address_link"][position] = float(
            bool(card and address and address not in card_addresses[card])
        )
        fraud_neighbours = sum(confirmed_fraud[entity] for entity in entities)
        neighbour_count = sum(confirmed_count[entity] for entity in entities)
        arrays["confirmed_fraud_neighbours"][position] = fraud_neighbours
        arrays["confirmed_neighbour_count"][position] = neighbour_count
        arrays["confirmed_fraud_ratio"][position] = fraud_neighbours / max(neighbour_count, 1)
        arrays["entity_novelty"][position] = sum(count[entity] == 0 for entity in entities) / max(len(entities), 1)
        arrays["component_pressure"][position] = (
            arrays["card_recent_1h"][position]
            + arrays["device_recent_1h"][position]
            + arrays["device_distinct_cards"][position]
            + arrays["address_distinct_cards"][position]
        )

        metadata.append({
            "card": card, "device": device, "address": address,
            "cardPublic": public_id(card), "devicePublic": public_id(device),
            "addressPublic": public_id(address),
        })
        for entity in entities:
            first_seen.setdefault(entity, now)
            count[entity] += 1
            recent[entity].append(now)
        if card and device:
            card_devices[card].add(device)
            device_cards[device].add(card)
        if card and address:
            card_addresses[card].add(address)
            address_cards[address].add(card)
        feedback.append((now, int(row["isFraud"]), entities))

    feature_frame = pd.DataFrame(arrays)
    return feature_frame, metadata


def at_budget(
    y: np.ndarray,
    scores: np.ndarray,
    amounts: np.ndarray,
    budget: float = REVIEW_BUDGET,
    ranking_scores: np.ndarray | None = None,
) -> dict:
    reviewed = max(1, int(len(scores) * budget))
    selected = np.argsort(scores if ranking_scores is None else ranking_scores)[-reviewed:]
    flags = np.zeros(len(scores), dtype=bool)
    flags[selected] = True
    fraud = y == 1
    caught = fraud & flags
    false_positive = (~fraud) & flags
    legitimate = ~fraud
    review_cost = PolicyConfig().review_cost
    return {
        "review_budget_pct": budget * 100,
        "reviewed": int(reviewed),
        "precision": float(caught.sum() / max(flags.sum(), 1)),
        "recall": float(caught.sum() / max(fraud.sum(), 1)),
        "fraud_count_caught": int(caught.sum()),
        "fraud_count_total": int(fraud.sum()),
        "false_positive_count": int(false_positive.sum()),
        "false_positive_rate": float(false_positive.sum() / max(legitimate.sum(), 1)),
        "false_positive_review_cost": float(false_positive.sum() * review_cost),
        "false_positive_cost_assumption": f"₹{review_cost:,.0f} analyst review cost per legitimate queued event",
        "fraud_value_captured": float(amounts[caught].sum()),
        "fraud_value_total": float(amounts[fraud].sum()),
        "fraud_value_capture": float(amounts[caught].sum() / max(amounts[fraud].sum(), 1)),
    }


MERCHANT_POLICIES = [
    {
        "id": "growth",
        "name": "Growth protection",
        "description": "Protect legitimate conversion; interrupt only when expected fraud benefit clearly covers customer friction.",
        "review_cost": 120.0,
        "legitimate_friction_cost": 750.0,
        "fraud_prevention_rate": 0.85,
    },
    {
        "id": "balanced",
        "name": "Balanced operations",
        "description": "Balance prevented fraud, analyst capacity, and the cost of interrupting a legitimate payment.",
        "review_cost": 120.0,
        "legitimate_friction_cost": 300.0,
        "fraud_prevention_rate": 0.90,
    },
    {
        "id": "loss_minimization",
        "name": "Loss minimization",
        "description": "Accept more customer friction when the merchant's current priority is reducing fraud exposure.",
        "review_cost": 120.0,
        "legitimate_friction_cost": 80.0,
        "fraud_prevention_rate": 0.95,
    },
]


def merchant_value_at_capacity(
    y: np.ndarray,
    scores: np.ndarray,
    amounts: np.ndarray,
    budget: float,
    policy: dict,
) -> dict:
    """Evaluate a capacity-bounded queue as incremental scenario value."""
    exposure = amounts + PolicyConfig().chargeback_fee
    priority = (
        scores * exposure * policy["fraud_prevention_rate"]
        - policy["review_cost"]
        - (1.0 - scores) * policy["legitimate_friction_cost"]
    )
    capacity = max(1, int(len(scores) * budget))
    ranked = np.argsort(priority)[::-1]
    selected = ranked[priority[ranked] > 0][:capacity]
    flags = np.zeros(len(scores), dtype=bool)
    flags[selected] = True
    fraud = y == 1
    caught = fraud & flags
    false_positive = (~fraud) & flags
    prevented = float((exposure[caught] * policy["fraud_prevention_rate"]).sum())
    review_cost = float(flags.sum() * policy["review_cost"])
    friction_cost = float(false_positive.sum() * policy["legitimate_friction_cost"])
    return {
        "capacity_pct": budget * 100,
        "capacity_slots": int(capacity),
        "reviewed": int(flags.sum()),
        "unused_slots": int(capacity - flags.sum()),
        "fraud_count_caught": int(caught.sum()),
        "fraud_count_total": int(fraud.sum()),
        "precision": float(caught.sum() / max(flags.sum(), 1)),
        "false_positive_count": int(false_positive.sum()),
        "fraud_exposure_prevented": prevented,
        "manual_review_cost": review_cost,
        "legitimate_friction_cost": friction_cost,
        "merchant_value": prevented - review_cost - friction_cost,
    }


def build_merchant_policy_lab(
    y: np.ndarray,
    base_scores: np.ndarray,
    graph_scores: np.ndarray,
    amounts: np.ndarray,
) -> dict:
    budgets = (0.0025, 0.005, 0.01, 0.02, 0.05)
    policies = []
    for policy in MERCHANT_POLICIES:
        points = []
        for budget in budgets:
            baseline = merchant_value_at_capacity(y, base_scores, amounts, budget, policy)
            graph = merchant_value_at_capacity(y, graph_scores, amounts, budget, policy)
            winner = "graph" if graph["merchant_value"] > baseline["merchant_value"] else "baseline"
            points.append({
                "capacity_pct": budget * 100,
                "baseline": baseline,
                "graph": graph,
                "recommended_model": winner,
                "value_delta": float(graph["merchant_value"] - baseline["merchant_value"]),
            })
        policies.append({**policy, "points": points})
    return {
        "policies": policies,
        "value_formula": "fraud exposure prevented − manual review cost − legitimate-customer friction cost",
        "queue_formula": "P(fraud) × exposure × prevention rate − review cost − (1−P(fraud)) × friction cost",
        "status": "scenario evaluation on locked outcomes; not claimed realized savings",
    }


def cold_start_evaluation(
    y: np.ndarray,
    graph_scores: np.ndarray,
    amounts: np.ndarray,
    graph_features: pd.DataFrame,
    metadata: list[dict],
) -> dict:
    """Slice the same global 1% queue by entity history available at score time."""
    reviewed = max(1, int(len(graph_scores) * REVIEW_BUDGET))
    selected = np.argsort(graph_scores)[-reviewed:]
    flags = np.zeros(len(graph_scores), dtype=bool)
    flags[selected] = True
    card_known = graph_features["card_prior_count"].to_numpy() > 0
    device_known = graph_features["device_prior_count"].to_numpy() > 0
    device_present = np.array([item.get("device") is not None for item in metadata], dtype=bool)
    groups = [
        ("known_card_known_device", "Known card + known device", card_known & device_known & device_present),
        ("known_card_new_device", "Known card + unseen device", card_known & ~device_known & device_present),
        ("new_card_known_device", "Unseen card + known device", ~card_known & device_known & device_present),
        ("new_card_new_device", "Unseen card + unseen device", ~card_known & ~device_known & device_present),
        ("missing_device", "Missing device identity", ~device_present),
    ]
    rows = []
    for group_id, label, mask in groups:
        group_y = y[mask]
        group_flags = flags[mask]
        group_fraud = group_y == 1
        caught = group_flags & group_fraud
        fp = group_flags & ~group_fraud
        group_amounts = amounts[mask]
        ap = float(average_precision_score(group_y, graph_scores[mask])) if group_fraud.any() else None
        rows.append({
            "id": group_id,
            "label": label,
            "rows": int(mask.sum()),
            "frauds": int(group_fraud.sum()),
            "reviewed": int(group_flags.sum()),
            "precision": float(caught.sum() / max(group_flags.sum(), 1)),
            "recall": float(caught.sum() / max(group_fraud.sum(), 1)),
            "average_precision": ap,
            "exposure_capture": float(group_amounts[caught].sum() / max(group_amounts[group_fraud].sum(), 1)),
        })
    return {
        "threshold_scope": "one global graph-model threshold selecting the top 1% of the full chronological test stream",
        "definition": "Unseen means prior_count = 0 before the event is inserted. Missing device identity is reported separately.",
        "buckets": rows,
    }


def graph_rescue_examples(
    frame: pd.DataFrame,
    y: np.ndarray,
    base_scores: np.ndarray,
    graph_scores: np.ndarray,
    amounts: np.ndarray,
    graph_features: pd.DataFrame,
    test_start: int,
    count: int = 5,
) -> dict:
    """Return held-out frauds whose risk assessment changes on past-only graph evidence."""
    reviewed = max(1, int(len(y) * REVIEW_BUDGET))
    base_flags = np.zeros(len(y), dtype=bool)
    graph_flags = np.zeros(len(y), dtype=bool)
    base_flags[np.argsort(base_scores)[-reviewed:]] = True
    graph_flags[np.argsort(graph_scores)[-reviewed:]] = True
    relational = (
        (graph_features["component_pressure"].to_numpy() >= 4)
        & (graph_features["device_distinct_cards"].to_numpy() >= 1)
    )
    candidates = np.flatnonzero(
        (y == 1)
        & (base_scores <= 0.25)
        & (graph_scores >= 0.30)
        & relational
    )
    if len(candidates) < count:
        candidates = np.flatnonzero((y == 1) & (base_scores <= 0.35) & (graph_scores > base_scores))
    importance = (graph_scores[candidates] - base_scores[candidates]) * np.log1p(amounts[candidates])
    ranked_candidates = candidates[np.argsort(importance)[::-1]]
    chosen_list = []
    chosen_times = []
    # Avoid filling the gallery with five near-duplicate payments from one burst.
    # Six-hour separation makes each card a distinct temporal investigation.
    for local in ranked_candidates:
        event_time = int(frame.iloc[test_start + int(local)]["TransactionDT"])
        if all(abs(event_time - prior_time) >= 21_600 for prior_time in chosen_times):
            chosen_list.append(int(local))
            chosen_times.append(event_time)
        if len(chosen_list) == count:
            break
    if len(chosen_list) < count:
        for local in ranked_candidates:
            if int(local) not in chosen_list:
                chosen_list.append(int(local))
            if len(chosen_list) == count:
                break
    chosen = np.asarray(chosen_list, dtype=int)
    rows = []
    for rank, local in enumerate(chosen, start=1):
        feature = graph_features.iloc[local]
        source = frame.iloc[test_start + int(local)]
        rows.append({
            "id": f"GR-{rank:02d}",
            "transaction_id": f"TX-{int(source['TransactionID'])}",
            "dataset_elapsed_second": int(source["TransactionDT"]),
            "source_amount_usd": float(source["TransactionAmt"]),
            "scenario_amount_inr": float(amounts[local]),
            "transaction_risk": float(base_scores[local]),
            "graph_risk": float(graph_scores[local]),
            "risk_lift_pp": float((graph_scores[local] - base_scores[local]) * 100),
            "transaction_action": decide(float(base_scores[local]), float(amounts[local]), PolicyConfig()).action,
            "graph_action": decide(float(graph_scores[local]), float(amounts[local]), PolicyConfig()).action,
            "evidence": [
                f"component pressure {int(feature['component_pressure'])}",
                f"device linked to {int(feature['device_distinct_cards'])} prior card profiles",
                f"card had {int(feature['card_recent_1h'])} events in the prior hour",
                f"{int(feature['confirmed_fraud_neighbours'])} delayed-confirmed fraud neighbours available at score time",
            ],
            "outcome": "fraud",
            "queue_result": "transaction-only risk was at or below 25%; past-only graph risk crossed the predeclared 30% investigation boundary",
        })
    noise_candidates = np.flatnonzero((y == 0) & ~base_flags & graph_flags)
    noise_case = None
    if len(noise_candidates):
        local = int(noise_candidates[np.argmax(amounts[noise_candidates])])
        source = frame.iloc[test_start + local]
        noise_case = {
            "transaction_id": f"TX-{int(source['TransactionID'])}",
            "scenario_amount_inr": float(amounts[local]),
            "transaction_risk": float(base_scores[local]),
            "graph_risk": float(graph_scores[local]),
            "outcome": "legitimate",
            "verdict": "Graph context added noise here. Keep the graph as a challenger, not an unquestioned authority.",
        }
    return {
        "definition": "Resolved held-out frauds with transaction-only risk ≤25%, graph risk ≥30%, component pressure ≥4, and at least one prior device-card relationship; examples are separated by at least six dataset-hours where possible.",
        "examples": rows,
        "graph_noise_case": noise_case,
    }


def moving_block_sample(length: int, rng: np.random.Generator, block_size: int = 1024) -> np.ndarray:
    """Resample contiguous chronological blocks so local fraud bursts remain correlated."""
    block_size = min(block_size, length)
    block_count = int(np.ceil(length / block_size))
    starts = rng.integers(0, length - block_size + 1, block_count)
    return np.concatenate([np.arange(start, start + block_size) for start in starts])[:length]


def bootstrap_delta(y: np.ndarray, base: np.ndarray, graph: np.ndarray, draws: int = 500) -> list[float]:
    """Paired moving-block bootstrap for AP delta on the chronological test stream."""
    rng = np.random.default_rng(SEED)
    deltas = []
    for _ in range(draws):
        sample = moving_block_sample(len(y), rng)
        if y[sample].sum() == 0:
            continue
        deltas.append(average_precision_score(y[sample], graph[sample]) - average_precision_score(y[sample], base[sample]))
    return [float(np.quantile(deltas, 0.025)), float(np.quantile(deltas, 0.975))]


def bootstrap_operating_point(y: np.ndarray, scores: np.ndarray, amounts: np.ndarray, draws: int = 300) -> dict:
    """Moving-block bootstrap intervals for the 1% queue."""
    rng = np.random.default_rng(SEED + 1)
    values = defaultdict(list)
    for _ in range(draws):
        sample = moving_block_sample(len(y), rng)
        result = at_budget(y[sample], scores[sample], amounts[sample])
        values["recall"].append(result["recall"])
        values["precision"].append(result["precision"])
        values["fraud_value_capture"].append(result["fraud_value_capture"])
    return {
        key: [float(np.quantile(item, 0.025)), float(np.quantile(item, 0.975))]
        for key, item in values.items()
    }


def fixed_fpr_metrics(y: np.ndarray, validation_scores: np.ndarray, scores: np.ndarray, rates=(0.005, 0.01)) -> list[dict]:
    validation_y = y["validation"]
    test_y = y["test"]
    validation_negative = validation_scores[validation_y == 0]
    rows = []
    for target_fpr in rates:
        threshold = float(np.quantile(validation_negative, 1 - target_fpr))
        flagged = scores >= threshold
        tp = int(((test_y == 1) & flagged).sum())
        fp = int(((test_y == 0) & flagged).sum())
        rows.append({
            "target_fpr": target_fpr, "validation_threshold": threshold,
            "test_fpr": float(fp / max((test_y == 0).sum(), 1)),
            "precision": float(tp / max(flagged.sum(), 1)),
            "recall": float(tp / max((test_y == 1).sum(), 1)),
            "flagged": int(flagged.sum()),
        })
    return rows


def inference_latency(bundle, frame: pd.DataFrame, repeats: int = 60, batch_size: int = 128) -> dict:
    sample = frame.iloc[:batch_size]
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        raw = bundle["model"].predict_proba(sample)[:, 1]
        bundle["calibrator"].predict(raw)
        durations.append((time.perf_counter() - started) * 1000)
    return {
        "batch_size": len(sample), "p50_batch_ms": float(np.percentile(durations, 50)),
        "p95_batch_ms": float(np.percentile(durations, 95)),
        "p95_per_event_ms": float(np.percentile(durations, 95) / max(len(sample), 1)),
        "environment": "local CPU; model + calibration only; excludes HTTP and feature assembly",
    }


def realized_policy(y: np.ndarray, scores: np.ndarray, amounts: np.ndarray) -> dict:
    config = PolicyConfig()
    actions = [decide(float(p), float(a), config).action for p, a in zip(scores, amounts)]
    total = 0.0
    counts = defaultdict(int)
    legitimate_held = 0.0
    fraud_allowed = 0.0
    for label, action, amount in zip(y, actions, amounts):
        counts[action] += 1
        exposure = float(amount) + config.chargeback_fee
        if action == "allow" and label:
            total += exposure
            fraud_allowed += amount
        elif action == "review":
            total += config.review_cost + (0.10 * exposure if label else 0)
        elif action == "hold" and not label:
            cost = amount * config.false_hold_rate + config.customer_friction_cost
            total += cost
            legitimate_held += amount
    return {
        "estimated_realized_cost": total,
        "action_counts": dict(counts),
        "legitimate_gmv_held": legitimate_held,
        "fraud_value_allowed": fraud_allowed,
        "assumption": "Review prevents 90% of fraud; hold cost is 12% of amount plus ₹250 friction.",
    }


def build_replay(
    frame: pd.DataFrame,
    metadata: list[dict],
    graph_features: pd.DataFrame,
    test_start: int,
    base_scores: np.ndarray,
    graph_scores: np.ndarray,
    x_graph: pd.DataFrame,
    graph_model: HistGradientBoostingClassifier,
    graph_calibrator: IsotonicRegression,
    validation_scores: np.ndarray,
    validation_y: np.ndarray,
    max_psi: float,
) -> dict:
    test = frame.iloc[test_start:].reset_index(drop=True)
    meta = metadata[test_start:]
    features = graph_features.iloc[test_start:].reset_index(drop=True)
    y = test["isFraud"].to_numpy(dtype=int)
    delta = graph_scores - base_scores
    eligible = np.flatnonzero(
        (y == 1)
        & (features["component_pressure"].to_numpy() >= 4)
        & (features["device_distinct_cards"].to_numpy() >= 1)
        & (base_scores <= 0.25)
        & (graph_scores >= 0.30)
    )
    if len(eligible) == 0:
        eligible = np.flatnonzero(y == 1)
    # Prefer an incident whose lift is not only large, but whose trained model
    # responds to more than one relational channel when those features are
    # masked. This selection uses only locked-test model outputs for a demo
    # case; it does not change the published aggregate metrics.
    candidates = eligible[np.argsort(delta[eligible])[-500:]]
    candidate_rows = x_graph.iloc[test_start + candidates].copy()
    contribution_total = np.zeros(len(candidates), dtype=float)
    contribution_channels = np.zeros(len(candidates), dtype=float)
    channel_masks = {
        "card": ["card_prior_count", "card_recent_1h", "card_distinct_devices", "card_distinct_addresses", "new_card_device_link", "new_card_address_link"],
        "device": ["device_prior_count", "device_recent_1h", "device_distinct_cards", "new_card_device_link"],
        "address": ["address_prior_count", "address_recent_1h", "address_distinct_cards", "new_card_address_link"],
    }
    for columns in channel_masks.values():
        masked = candidate_rows.copy()
        masked.loc[:, columns] = 0
        masked_scores = graph_calibrator.predict(graph_model.predict_proba(masked)[:, 1])
        contributions = np.maximum(0, graph_scores[candidates] - masked_scores)
        contribution_total += contributions
        contribution_channels += contributions > 0.001
    demo_score = 3.0 * delta[candidates] + contribution_total + 0.02 * contribution_channels
    target_local = int(candidates[np.argmax(demo_score)])
    target_global = test_start + target_local
    target_meta = metadata[target_global]
    entity_values = {value for value in target_meta.values() if isinstance(value, str) and ":" in value}
    neighbours = []
    lower = max(test_start, target_global - 25_000)
    for global_index in range(lower, target_global + 1):
        candidate = metadata[global_index]
        raw = {candidate.get("card"), candidate.get("device"), candidate.get("address")}
        overlap = len(entity_values & raw)
        if overlap:
            neighbours.append((overlap, global_index))
    neighbours = [index for _, index in sorted(neighbours, key=lambda pair: (pair[1], pair[0]))[-8:]]
    if target_global not in neighbours:
        neighbours.append(target_global)
    neighbours = sorted(set(neighbours))

    node_map: dict[str, dict] = {}
    edges = []
    events = []
    start_time = int(frame.iloc[neighbours[0]]["TransactionDT"])
    for sequence, global_index in enumerate(neighbours, start=1):
        row = frame.iloc[global_index]
        item_meta = metadata[global_index]
        tx_id = f"TX-{int(row['TransactionID'])}"
        node_map[tx_id] = {
            "id": tx_id, "kind": "transaction", "label": f"₹{row['TransactionAmt'] * USD_TO_INR_SCENARIO:,.0f}",
            "truth": "fraud" if int(row["isFraud"]) else "legitimate",
            "isTarget": global_index == target_global,
        }
        evidence_ids = []
        for kind in ("card", "device", "address"):
            raw_value = item_meta.get(kind)
            if not raw_value:
                continue
            entity_id = public_id(raw_value)
            node_map.setdefault(entity_id, {"id": entity_id, "kind": kind, "label": entity_id})
            edges.append({"source": tx_id, "target": entity_id, "type": kind})
            evidence_ids.append(f"EV-{kind.upper()}-{entity_id[-4:]}")
        events.append({
            "sequence": sequence,
            "transactionId": tx_id,
            "amount": float(row["TransactionAmt"] * USD_TO_INR_SCENARIO),
            "sourceAmountUsd": float(row["TransactionAmt"]),
            "offsetMinutes": round((int(row["TransactionDT"]) - start_time) / 60, 1),
            "transactionRisk": float(base_scores[global_index - test_start]),
            "ringRisk": float(graph_scores[global_index - test_start]),
            "truth": "fraud" if int(row["isFraud"]) else "legitimate",
            "evidenceIds": evidence_ids,
        })

    target = events[[event["transactionId"] for event in events].index(f"TX-{int(frame.iloc[target_global]['TransactionID'])}")]
    target_feature = graph_features.iloc[target_global]
    target_vector = x_graph.iloc[[target_global]].copy()
    original_raw_score = float(graph_model.predict_proba(target_vector)[:, 1][0])
    masks = {
        "card connection": ["card_prior_count", "card_recent_1h", "card_distinct_devices", "card_distinct_addresses", "new_card_device_link", "new_card_address_link"],
        "device connection": ["device_prior_count", "device_recent_1h", "device_distinct_cards", "new_card_device_link"],
        "address connection": ["address_prior_count", "address_recent_1h", "address_distinct_cards", "new_card_address_link"],
    }
    counterfactuals = []
    masked_inputs = {}
    for label, columns in masks.items():
        masked = target_vector.copy()
        masked.loc[:, columns] = 0
        if label == "card connection":
            masked.loc[:, "component_pressure"] = max(0, float(masked["component_pressure"].iloc[0]) - float(target_feature["card_recent_1h"]))
        elif label == "device connection":
            masked.loc[:, "component_pressure"] = max(0, float(masked["component_pressure"].iloc[0]) - float(target_feature["device_recent_1h"] + target_feature["device_distinct_cards"]))
        else:
            masked.loc[:, "component_pressure"] = max(0, float(masked["component_pressure"].iloc[0]) - float(target_feature["address_recent_1h"] + target_feature["address_distinct_cards"]))
        masked_raw_score = float(graph_model.predict_proba(masked)[:, 1][0])
        masked_score = float(graph_calibrator.predict([masked_raw_score])[0])
        masked_inputs[label] = {column: float(masked.iloc[0][column]) for column in x_graph.columns}
        counterfactuals.append({
            "removed": label,
            "riskAfterRemoval": masked_score,
            "riskDeltaPp": (target["ringRisk"] - masked_score) * 100,
            "rawScoreAfterRemoval": masked_raw_score,
            "rawScoreDeltaPp": (original_raw_score - masked_raw_score) * 100,
            "method": "The graph model is re-scored after masking only features derived from this entity type; this is local sensitivity, not a causal claim.",
        })
    counterfactuals.sort(key=lambda item: item["riskDeltaPp"], reverse=True)
    support_mask = np.abs(validation_scores - target["ringRisk"]) <= 0.025
    if support_mask.sum() < 500:
        support_mask = np.abs(validation_scores - target["ringRisk"]) <= 0.05
    support_count = int(support_mask.sum())
    observed_rate = float(validation_y[support_mask].mean()) if support_count else None
    calibration_gap = abs(observed_rate - target["ringRisk"]) if observed_rate is not None else None
    confidence_reasons = []
    if max_psi >= 0.25:
        confidence_reasons.append(f"current-vs-validation drift is outside the approved boundary (max PSI {max_psi:.3f})")
    if support_count < 500:
        confidence_reasons.append(f"only {support_count} validation events sit near this calibrated risk")
    if float(target_feature["entity_novelty"]) >= 0.67:
        confidence_reasons.append("most relational entities were unseen before this event")
    if calibration_gap is not None and calibration_gap > 0.10:
        confidence_reasons.append(f"nearby validation outcomes differ from the estimate by {calibration_gap:.1%}")
    confidence_level = "low" if max_psi >= 0.25 or support_count < 500 else "medium" if confidence_reasons else "high"
    actions = decide(
        target["ringRisk"],
        target["amount"],
        PolicyConfig(),
        distribution_stable=max_psi < 0.25,
    ).to_dict()
    bridge_ids = [event["transactionId"] for event in events[-3:-1]]
    hold_ids = [target["transactionId"]] if actions["action"] == "hold" else []
    review_ids = bridge_ids + ([target["transactionId"]] if actions["action"] == "review" else [])
    allow_ids = [event["transactionId"] for event in events[:-3]] + ([target["transactionId"]] if actions["action"] == "allow" else [])
    summary_facts = [
        {"id": "EV-RING-01", "text": f"The connected subgraph contains {len(events)} transactions and {len(node_map) - len(events)} linked entities."},
        {"id": "EV-RING-02", "text": f"The device had already appeared with {int(target_feature['device_distinct_cards'])} other card profiles before this payment."},
        {"id": "EV-RING-03", "text": f"Graph context changed calibrated risk from {target['transactionRisk']:.1%} to {target['ringRisk']:.1%}."},
        {"id": "EV-RING-04", "text": f"The prior one-hour component pressure score was {int(target_feature['component_pressure'])}."},
    ]
    return {
        "incidentId": "SG-INC-042",
        "title": "Emerging multi-card abuse ring",
        "targetTransaction": target["transactionId"],
        "nodes": list(node_map.values()),
        "edges": edges,
        "events": events,
        "facts": summary_facts,
        "graphCounterfactuals": counterfactuals,
        "operationalConfidence": {
            "level": confidence_level,
            "riskProbability": target["ringRisk"],
            "calibrationSupport": support_count,
            "nearbyValidationFraudRate": observed_rate,
            "reasons": confidence_reasons or ["calibration support, entity history, and drift checks are inside the configured evaluation boundary"],
            "boundary": "Confidence is an operational gate derived from calibration support, entity novelty, and drift; it is not another fraud probability.",
        },
        "temporalGuard": {
            "targetDatasetSecond": int(frame.iloc[target_global]["TransactionDT"]),
            "graphCutoff": "strictly before the target event is inserted",
            "labelAvailabilityDelayHours": FEEDBACK_DELAY_SECONDS // 3600,
            "futureLabelsAccessible": False,
            "guard": "score current event → emit decision evidence → insert event for future transactions",
        },
        "scoringMode": "The event sequence is a deterministic replay derived from locked-test rows. Model inference, calibration, counterfactual re-scoring, policy, guardrails, HMAC validation, and audit chaining execute on each request.",
        "agentSummary": " ".join(fact["text"] for fact in summary_facts),
        "proposedAction": actions,
        "blastRadius": {
            "hold": hold_ids,
            "review": review_ids,
            "allow": allow_ids,
            "rule": "No shared entity is globally blocked; only transaction-level actions are permitted without analyst approval.",
        },
        "toolTrace": [
            {"tool": "verify_event", "result": "Signature valid · replay key unseen", "status": "passed"},
            {"tool": "score_transaction", "result": f"Calibrated standalone risk {target['transactionRisk']:.1%}", "status": "passed"},
            {"tool": "expand_temporal_graph", "result": f"{len(node_map)} nodes · past-only context", "status": "passed"},
            {"tool": "estimate_ring_risk", "result": f"Contextual risk {target['ringRisk']:.1%}", "status": "passed"},
            {"tool": "minimize_blast_radius", "result": f"{len(hold_ids)} holds · {len(review_ids)} reviews · no entity block", "status": "gated"},
        ],
        "dataNote": "Labels, source USD amounts, times, and links are derived from IEEE-CIS/Vesta. Displayed/policy amounts use the explicit fixed scenario conversion USD 1 = INR 83; this is not an observed FX rate. Entity labels are hashed.",
        "_privateModelInputs": {
            "transaction": {column: float(target_vector.iloc[0][column]) for column in BASE_FEATURES},
            "graph": {column: float(target_vector.iloc[0][column]) for column in x_graph.columns},
            "masked": masked_inputs,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    frame = load_frame(args.data_dir)
    graph_started = time.perf_counter()
    graph, metadata = add_temporal_graph_features(frame)
    graph_build_seconds = time.perf_counter() - graph_started

    first = int(len(frame) * 0.70)
    second = int(len(frame) * 0.85)
    x_base = frame[BASE_FEATURES].replace([np.inf, -np.inf], np.nan).fillna(-999)
    x_graph = pd.concat([x_base.reset_index(drop=True), graph], axis=1)
    y = frame["isFraud"].to_numpy(dtype=int)
    source_amounts_usd = frame["TransactionAmt"].to_numpy(dtype=float)
    amounts = source_amounts_usd * USD_TO_INR_SCENARIO
    weight = min(20.0, (first - y[:first].sum()) / max(y[:first].sum(), 1))
    sample_weight = np.where(y[:first] == 1, weight, 1.0)

    def fit(features: pd.DataFrame):
        model = HistGradientBoostingClassifier(
            learning_rate=0.08, max_iter=130, max_leaf_nodes=31,
            min_samples_leaf=35, l2_regularization=1.0, random_state=SEED,
        )
        model.fit(features.iloc[:first], y[:first], sample_weight=sample_weight)
        validation_raw = model.predict_proba(features.iloc[first:second])[:, 1]
        calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.00001, y_max=0.99999)
        calibrator.fit(validation_raw, y[first:second])
        test_scores = calibrator.predict(model.predict_proba(features.iloc[second:])[:, 1])
        validation_scores = calibrator.predict(validation_raw)
        return model, calibrator, validation_scores, test_scores

    base_model, base_calibrator, base_validation_scores, base_scores = fit(x_base)
    graph_model, graph_calibrator, graph_validation_scores, graph_scores = fit(x_graph)
    test_y = y[second:]
    test_amounts = amounts[second:]
    expected_loss_priority = graph_scores * (test_amounts + PolicyConfig().chargeback_fee)
    amount_rule_scores = test_amounts.copy()
    amount_rule = at_budget(test_y, amount_rule_scores, test_amounts)
    base_budget = at_budget(test_y, base_scores, test_amounts)
    graph_budget = at_budget(test_y, graph_scores, test_amounts)
    expected_loss_budget = at_budget(test_y, graph_scores, test_amounts, ranking_scores=expected_loss_priority)
    drift_features = []
    for feature in DRIFT_MONITOR_FEATURES:
        psi = population_stability_index(x_graph.iloc[first:second][feature], x_graph.iloc[second:][feature])
        drift_features.append({
            "feature": feature, "psi": psi, "status": drift_status(psi),
            "kind": "rolling/rate graph feature",
        })
    score_psi = population_stability_index(graph_validation_scores, graph_scores)
    drift_features.append({
        "feature": "calibrated_graph_risk", "psi": score_psi,
        "status": drift_status(score_psi), "kind": "model output",
    })
    drift_features.sort(key=lambda item: item["psi"], reverse=True)
    max_psi = drift_features[0]["psi"] if drift_features else 0.0
    graph_bundle = {"model": graph_model, "calibrator": graph_calibrator}

    metrics = {
        "project": "SentinelGraph",
        "dataset": {
            "name": "IEEE-CIS Fraud Detection (Vesta)", "rows": len(frame),
            "fraud_rows": int(y.sum()), "test_rows": len(test_y),
            "test_fraud_rows": int(test_y.sum()),
            "test_fraud_prevalence": float(test_y.mean()),
            "max_case_recall_at_1pct": float(min(1.0, max(1, int(len(test_y) * 0.01)) / max(test_y.sum(), 1))),
            "identity_rows": int(frame["id_02"].notna().sum()),
            "split": "chronological 70/15/15",
            "feedback_delay_hours": FEEDBACK_DELAY_SECONDS // 3600,
            "source": "https://www.kaggle.com/competitions/ieee-fraud-detection/data",
            "mirror": "https://www.kaggle.com/datasets/lnasiri007/ieeecis-fraud-detection",
        },
        "financial_units": {
            "source_amount_field": "TransactionAmt",
            "source_currency": "USD",
            "policy_currency": "INR",
            "usd_to_inr_rate": USD_TO_INR_SCENARIO,
            "assumption_locked_on": "2026-08-23",
            "method": "Every source amount is multiplied by the fixed scenario rate before queue economics, policy costs, or UI display. The raw USD field remains the model input.",
            "warning": "Scenario normalization only; not an observed per-transaction or historical market FX rate."
        },
        "ablations": [
            {"name": "Transaction model", "average_precision": float(average_precision_score(test_y, base_scores)), "roc_auc": float(roc_auc_score(test_y, base_scores)), **at_budget(test_y, base_scores, test_amounts)},
            {"name": "Transaction + temporal graph", "average_precision": float(average_precision_score(test_y, graph_scores)), "roc_auc": float(roc_auc_score(test_y, graph_scores)), **at_budget(test_y, graph_scores, test_amounts)},
        ],
        "system_comparison": [
            {"name": "Amount-only merchant rule", "kind": "deterministic rule", **amount_rule},
            {"name": "Transaction model", "kind": "calibrated ML", **base_budget},
            {"name": "Transaction + temporal graph", "kind": "calibrated ML", **graph_budget},
            {"name": "Expected-loss priority queue", "kind": "graph risk × rupee exposure", **expected_loss_budget},
        ],
        "review_frontier": [
            {
                "budget_pct": budget * 100,
                "transaction": at_budget(test_y, base_scores, test_amounts, budget),
                "graph": at_budget(test_y, graph_scores, test_amounts, budget),
            }
            for budget in (0.0025, 0.005, 0.01, 0.02, 0.05)
        ],
        "operational_queues": {
            "risk_ranked": graph_budget,
            "expected_loss_ranked": expected_loss_budget,
            "priority_formula": "calibrated P(fraud) × (transaction amount + ₹1,500 chargeback fee)",
        },
        "merchant_policy_lab": build_merchant_policy_lab(test_y, base_scores, graph_scores, test_amounts),
        "cold_start_evaluation": cold_start_evaluation(
            test_y,
            graph_scores,
            test_amounts,
            graph.iloc[second:].reset_index(drop=True),
            metadata[second:],
        ),
        "graph_rescue_evidence": graph_rescue_examples(
            frame,
            test_y,
            base_scores,
            graph_scores,
            test_amounts,
            graph.iloc[second:].reset_index(drop=True),
            second,
        ),
        "ap_delta_ci_95": bootstrap_delta(test_y, base_scores, graph_scores),
        "graph_queue_ci_95": bootstrap_operating_point(test_y, graph_scores, test_amounts),
        "uncertainty_method": {
            "name": "paired moving-block bootstrap",
            "block_size_events": 1024,
            "ap_delta_draws": 500,
            "operating_point_draws": 300,
            "why": "Contiguous event blocks preserve local temporal clustering that an IID row bootstrap would erase.",
            "limit": "This quantifies sampling uncertainty within the IEEE-CIS test period, not transfer to Indian payment traffic.",
        },
        "fixed_fpr": {
            "transaction": fixed_fpr_metrics(
                {"validation": y[first:second], "test": test_y}, base_validation_scores, base_scores,
            ),
            "graph": fixed_fpr_metrics(
                {"validation": y[first:second], "test": test_y}, graph_validation_scores, graph_scores,
            ),
        },
        "drift": {
            "status": drift_status(max_psi), "max_psi": max_psi,
            "features": drift_features[:8],
            "boundary": "PSI <0.10 stable; 0.10–0.25 watch; ≥0.25 pause automated action",
            "reference": "middle 15% chronological validation", "current": "final 15% chronological test",
            "exclusions": "Monotonic lifetime counters and distinct-entity totals are excluded because graph aging creates mechanical drift.",
        },
        "latency": {
            "inference": inference_latency(graph_bundle, x_graph.iloc[second:]),
            "graph_feature_build_seconds": graph_build_seconds,
            "graph_rows_per_second": float(len(frame) / max(graph_build_seconds, 1e-9)),
        },
        "fraud_value_missed_at_1pct": {
            "risk_ranked": float(graph_budget["fraud_value_total"] - graph_budget["fraud_value_captured"]),
            "expected_loss_ranked": float(expected_loss_budget["fraud_value_total"] - expected_loss_budget["fraud_value_captured"]),
        },
        "policy": realized_policy(test_y, graph_scores, test_amounts),
        "limitations": [
            "IEEE-CIS is real anonymized Vesta e-commerce data, but it is not specifically Indian payment traffic.",
            "TransactionAmt is source USD. INR exposure is a fixed-rate scenario normalization, not observed loss, prevented loss, recovered revenue, or historical FX.",
            "Card, address, and identity fields are anonymized; the product does not invent their hidden semantics.",
            "Confirmed-neighbour features assume a conservative 24-hour label-availability delay.",
            "The public mirror reproduces competition files but lists no license; production use requires confirming data rights.",
        ],
    }
    replay = build_replay(
        frame, metadata, graph, second, base_scores, graph_scores,
        x_graph, graph_model, graph_calibrator,
        graph_validation_scores, y[first:second], max_psi,
    )
    private_inputs = replay.pop("_privateModelInputs")
    (ARTIFACTS / "sentinel_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (ARTIFACTS / "incident.json").write_text(json.dumps(replay, indent=2), encoding="utf-8")
    (ARTIFACTS / "incident_inputs.json").write_text(json.dumps(private_inputs), encoding="utf-8")
    joblib.dump({"model": base_model, "calibrator": base_calibrator, "features": BASE_FEATURES}, ARTIFACTS / "transaction_model.joblib")
    joblib.dump({"model": graph_model, "calibrator": graph_calibrator, "features": BASE_FEATURES + GRAPH_FEATURES}, ARTIFACTS / "graph_model.joblib")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
