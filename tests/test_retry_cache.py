import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from src.audio import send_audio
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

    def test_cache_recording_for_retry_replaces_previous_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            retry_dir = Path(temp_dir) / "retry_audio"
            metadata_path = retry_dir / "latest.json"
            old_audio = b"old audio"
            audio = np.array([[0.0], [0.25], [-0.25], [0.5]], dtype=np.float32)

            with (
                mock.patch.object(retry_cache, "RETRY_AUDIO_DIR", retry_dir),
                mock.patch.object(retry_cache, "RETRY_METADATA_PATH", metadata_path),
            ):
                retry_cache.write_retry_cache(old_audio, "audio/mpeg", {"source_task_id": "c1"})
                self.assertEqual(retry_cache.get_latest_audio_path(), retry_dir / "latest.mp3")

                send_audio._cache_recording_for_retry(
                    audio_concat=audio,
                    task_id="c2",
                    duration=1.0,
                    time_start=10.0,
                    record_stop=11.0,
                )

                latest_path = retry_cache.get_latest_audio_path()
                self.assertEqual(latest_path, retry_dir / "latest.wav")
                self.assertIsNotNone(latest_path)
                self.assertNotEqual(latest_path.read_bytes(), old_audio)
                self.assertFalse((retry_dir / "latest.mp3").exists())
                self.assertIn('"source_task_id": "c2"', metadata_path.read_text(encoding="utf-8"))


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
