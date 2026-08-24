import unittest
from pathlib import Path

from riskpilot.policy import PolicyConfig, decide, expected_costs
from riskpilot.retrieval import PolicyRetriever


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = PolicyConfig()

    def test_costs_are_non_negative(self):
        costs = expected_costs(0.5, 10_000, self.config)
        self.assertTrue(all(value >= 0 for value in costs.values()))

    def test_low_risk_is_allowed(self):
        self.assertEqual(decide(0.0001, 1_000, self.config).action, "allow")

    def test_high_risk_is_held(self):
        self.assertEqual(decide(0.99, 10_000, self.config).action, "hold")

    def test_missing_model_fails_to_review(self):
        result = decide(None, 10_000, self.config, model_available=False)
        self.assertEqual(result.action, "review")
        self.assertTrue(result.degraded)

    def test_probability_is_clamped(self):
        self.assertEqual(expected_costs(2.0, 100, self.config), expected_costs(1.0, 100, self.config))

    def test_drift_pauses_automation(self):
        result = decide(0.99, 10_000, self.config, distribution_stable=False)
        self.assertEqual(result.action, "review")
        self.assertTrue(result.degraded)

    def test_policy_retrieval_is_grounded(self):
        root = Path(__file__).resolve().parents[1]
        retriever = PolicyRetriever(root / "knowledge" / "policies.json")
        result = retriever.retrieve("model timeout evidence unavailable route to review")
        self.assertEqual(result["id"], "POL-02")


if __name__ == "__main__":
    unittest.main()
