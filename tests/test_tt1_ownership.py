import json
import os
import tempfile
import unittest

from verification.isolation.runner import owned_database_problem


class OwnedDatabasePreflightTests(unittest.TestCase):
    def test_missing_receipt_refuses_without_database_access(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = os.path.join(directory, "receipts.jsonl")
            problem = owned_database_problem("test_intevia_case", receipt_path, "nonce")

        self.assertIn("no current-attempt creation receipt", problem)

    def test_current_attempt_creation_receipt_accepts_database(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = os.path.join(directory, "receipts.jsonl")
            with open(receipt_path, "w", encoding="utf-8") as receipt_file:
                receipt_file.write(json.dumps({"event": "created", "name": "test_intevia_case",
                                               "oid": 123, "run_nonce": "nonce"}) + "\n")

            problem = owned_database_problem("test_intevia_case", receipt_path, "nonce")

        self.assertIsNone(problem)

    def test_stale_receipt_refuses_database(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = os.path.join(directory, "receipts.jsonl")
            with open(receipt_path, "w", encoding="utf-8") as receipt_file:
                receipt_file.write(json.dumps({"event": "created", "name": "test_intevia_case",
                                               "oid": 123, "run_nonce": "old-nonce"}) + "\n")

            problem = owned_database_problem("test_intevia_case", receipt_path, "nonce")

        self.assertIn("not trustworthy", problem)

    def test_protected_database_refuses_even_with_creation_receipt(self):
        problem = owned_database_problem("intevia", "unused", "nonce", ("intevia", "intevia_dev"))

        self.assertIn("protected database", problem)


if __name__ == "__main__":
    unittest.main()