import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.audio import retry_cache


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


if __name__ == "__main__":
    unittest.main()
