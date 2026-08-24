import unittest

import pandas as pd

from build_sentinelgraph import add_temporal_graph_features


def event(transaction_id, timestamp, label, card1, identity):
    return {
        "TransactionID": transaction_id,
        "TransactionDT": timestamp,
        "isFraud": label,
        "card1": card1,
        "card2": 10,
        "card3": 20,
        "card5": 30,
        "card6": "credit",
        "addr1": 100,
        "addr2": 50,
        "id_02": identity,
        "DeviceInfo": "device",
    }


class TemporalGraphTests(unittest.TestCase):
    def test_current_and_recent_labels_are_not_visible(self):
        frame = pd.DataFrame([
            event(1, 0, 1, 111, 900),
            event(2, 100, 0, 111, 900),
        ])
        features, _ = add_temporal_graph_features(frame)
        self.assertEqual(features.loc[0, "confirmed_fraud_neighbours"], 0)
        self.assertEqual(features.loc[1, "confirmed_fraud_neighbours"], 0)
        self.assertEqual(features.loc[1, "card_prior_count"], 1)

    def test_label_appears_only_after_feedback_delay(self):
        frame = pd.DataFrame([
            event(1, 0, 1, 111, 900),
            event(2, 90_000, 0, 111, 900),
        ])
        features, _ = add_temporal_graph_features(frame)
        self.assertGreaterEqual(features.loc[1, "confirmed_fraud_neighbours"], 1)

    def test_new_relation_is_measured_before_insert(self):
        frame = pd.DataFrame([
            event(1, 0, 0, 111, 900),
            event(2, 100, 0, 111, 901),
            event(3, 200, 0, 111, 901),
        ])
        features, _ = add_temporal_graph_features(frame)
        self.assertEqual(features.loc[0, "new_card_device_link"], 1)
        self.assertEqual(features.loc[1, "new_card_device_link"], 1)
        self.assertEqual(features.loc[2, "new_card_device_link"], 0)


if __name__ == "__main__":
    unittest.main()
