import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.audio import retry_cache
from src.audio import control_requests


class RetryCacheTest(unittest.TestCase):
    def test_claim_retry_request_only_allows_one_claim_per_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            claim_dir = Path(temp_dir) / "claims"
            with mock.patch.object(retry_cache, "RETRY_CLAIM_DIR", claim_dir):
                self.assertTrue(retry_cache.claim_retry_request(123))
                self.assertFalse(retry_cache.claim_retry_request(123))
                self.assertTrue(retry_cache.claim_retry_request(456))

    def test_claim_retry_request_rejects_empty_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            claim_dir = Path(temp_dir) / "claims"
            with mock.patch.object(retry_cache, "RETRY_CLAIM_DIR", claim_dir):
                self.assertFalse(retry_cache.claim_retry_request(None))
                self.assertFalse(claim_dir.exists())


class ControlRequestsTest(unittest.TestCase):
    def test_claim_clear_history_request_only_allows_one_claim_per_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            claim_dir = Path(temp_dir) / "claims"
            with mock.patch.object(control_requests, "CLEAR_HISTORY_CLAIM_DIR", claim_dir):
                self.assertTrue(control_requests.claim_clear_history_request(123))
                self.assertFalse(control_requests.claim_clear_history_request(123))
                self.assertTrue(control_requests.claim_clear_history_request(456))

    def test_claim_clear_history_request_rejects_empty_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            claim_dir = Path(temp_dir) / "claims"
            with mock.patch.object(control_requests, "CLEAR_HISTORY_CLAIM_DIR", claim_dir):
                self.assertFalse(control_requests.claim_clear_history_request(None))
                self.assertFalse(claim_dir.exists())


if __name__ == "__main__":
    unittest.main()
