import json
import unittest
from pathlib import Path

from riskpilot.containment import canonical_identity, compile_containment


ROOT = Path(__file__).resolve().parents[1]


class ContainmentFirewallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.incident = json.loads((ROOT / "artifacts" / "incident.json").read_text(encoding="utf-8"))

    def test_shared_card_block_is_transformed_to_transaction_plan(self):
        result = compile_containment(self.incident, "block_card")
        self.assertEqual(result["verdict"], "transform")
        self.assertEqual(result["reasonCode"], "ENTITY_SCOPE_EXCEEDS_AUTHORITY")
        self.assertEqual(result["requested"]["paymentsTouched"], 5)
        self.assertEqual(result["requested"]["volumeFrozenInr"], 149395.85)
        self.assertEqual(result["resolvedReplayEvaluation"]["knownLegitimateTouched"], 4)
        self.assertEqual(result["resolvedReplayEvaluation"]["knownLegitimateVolumeSparedInr"], 95445.85)
        self.assertEqual(result["safePlan"]["reviewCount"], 3)
        self.assertEqual(result["safePlan"]["holdCount"], 0)
        self.assertEqual(result["safePlan"]["untouchedCount"], 5)

    def test_transform_has_distinct_stable_action_identities(self):
        first = compile_containment(self.incident, "block_address")
        second = compile_containment(self.incident, "block_address")
        self.assertTrue(first["identityChanged"])
        self.assertEqual(first["inputIdentity"], second["inputIdentity"])
        self.assertEqual(first["enforcedIdentity"], second["enforcedIdentity"])
        self.assertTrue(first["inputIdentity"].startswith("sha256:"))

    def test_resolved_labels_are_not_part_of_policy_identity(self):
        before = compile_containment(self.incident, "block_card")
        changed = json.loads(json.dumps(self.incident))
        for event in changed["events"]:
            event["truth"] = "fraud"
        after = compile_containment(changed, "block_card")
        self.assertEqual(before["inputIdentity"], after["inputIdentity"])
        self.assertEqual(before["enforcedIdentity"], after["enforcedIdentity"])
        self.assertNotEqual(
            before["resolvedReplayEvaluation"]["knownLegitimateTouched"],
            after["resolvedReplayEvaluation"]["knownLegitimateTouched"],
        )

    def test_unknown_proposal_fails_closed(self):
        with self.assertRaises(ValueError):
            compile_containment(self.incident, "ban_everything")

    def test_canonical_identity_is_order_independent(self):
        self.assertEqual(canonical_identity({"a": 1, "b": 2}), canonical_identity({"b": 2, "a": 1}))


if __name__ == "__main__":
    unittest.main()
