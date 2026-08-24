import numpy as np
import pandas as pd

from build_sentinelgraph import (
    MERCHANT_POLICIES,
    cold_start_evaluation,
    merchant_value_at_capacity,
)


def test_merchant_value_queue_may_leave_negative_value_capacity_unused():
    y = np.array([0, 0, 1, 0])
    scores = np.array([0.01, 0.02, 0.99, 0.03])
    amounts = np.array([100.0, 100.0, 10_000.0, 100.0])
    result = merchant_value_at_capacity(y, scores, amounts, 0.75, MERCHANT_POLICIES[0])
    assert result["reviewed"] == 1
    assert result["fraud_count_caught"] == 1
    assert result["unused_slots"] == 2
    assert result["merchant_value"] > 0


def test_cold_start_buckets_are_mutually_exclusive_and_exhaustive():
    y = np.array([0, 1, 1, 0, 1])
    scores = np.array([0.1, 0.9, 0.8, 0.2, 0.7])
    amounts = np.full(5, 100.0)
    features = pd.DataFrame({
        "card_prior_count": [1, 1, 0, 0, 1],
        "device_prior_count": [1, 0, 1, 0, 0],
    })
    metadata = [
        {"device": "d1"}, {"device": "d2"}, {"device": "d3"},
        {"device": "d4"}, {"device": None},
    ]
    result = cold_start_evaluation(y, scores, amounts, features, metadata)
    assert sum(bucket["rows"] for bucket in result["buckets"]) == len(y)
    assert {bucket["id"] for bucket in result["buckets"]} == {
        "known_card_known_device", "known_card_new_device",
        "new_card_known_device", "new_card_new_device", "missing_device",
    }
