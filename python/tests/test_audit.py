import unittest

from server import hash_record, verify_audit_chain


class AuditChainTests(unittest.TestCase):
    def make_record(self, action, previous_hash):
        record = {
            "incidentId": "SG-INC-TEST",
            "target": "TX-TEST",
            "action": action,
            "failureMode": "healthy",
            "guardrail": "passed",
            "policyId": "POL-01",
            "timestamp": "2026-08-23T00:00:00+00:00",
            "previousHash": previous_hash,
        }
        record["recordHash"] = hash_record(record)
        return record

    def test_records_are_linked_and_verifiable(self):
        first = self.make_record("review", "GENESIS")
        second = self.make_record("hold", first["recordHash"])
        self.assertTrue(verify_audit_chain([first, second]))

    def test_mutation_breaks_chain(self):
        first = self.make_record("review", "GENESIS")
        second = self.make_record("hold", first["recordHash"])
        first["action"] = "allow"
        self.assertFalse(verify_audit_chain([first, second]))


if __name__ == "__main__":
    unittest.main()
