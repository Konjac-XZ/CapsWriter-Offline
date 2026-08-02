import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

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
                mock.patch.object(retry_cache.shutil, "which", return_value=None),
            ):
                retry_cache.write_retry_cache(
                    old_audio, "audio/mpeg", {"source_task_id": "c1"}
                )
                self.assertEqual(
                    retry_cache.get_latest_audio_path(), retry_dir / "latest.mp3"
                )

                send_audio._cache_recording_for_retry(
                    audio_concat=audio,
                    task_id="c2",
                    duration=1.0,
                    time_start=10.0,
                    record_stop=11.0,
                )

                latest_path = retry_cache.get_latest_audio_path()
                self.assertEqual(latest_path, retry_dir / "latest.wav")
                assert latest_path is not None
                self.assertNotEqual(latest_path.read_bytes(), old_audio)
                self.assertFalse((retry_dir / "latest.mp3").exists())
                self.assertIn(
                    '"source_task_id": "c2"', metadata_path.read_text(encoding="utf-8")
                )

    def test_wav_retry_cache_keeps_wav_and_creates_64k_mp3_when_ffmpeg_exists(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            retry_dir = Path(temp_dir) / "retry_audio"
            metadata_path = retry_dir / "latest.json"
            audio = b"wav audio"

            def fake_run(args, **kwargs):
                self.assertIn("-b:a", args)
                self.assertEqual(args[args.index("-b:a") + 1], "64k")
                Path(args[-1]).write_bytes(b"mp3 audio")
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(retry_cache, "RETRY_AUDIO_DIR", retry_dir),
                mock.patch.object(retry_cache, "RETRY_METADATA_PATH", metadata_path),
                mock.patch.object(retry_cache.shutil, "which", return_value="ffmpeg"),
                mock.patch.object(retry_cache.sp, "run", side_effect=fake_run),
            ):
                target = retry_cache.write_retry_cache(
                    audio, "audio/wav", {"source_task_id": "c3"}
                )

                self.assertEqual(target, retry_dir / "latest.mp3")
                self.assertEqual(
                    retry_cache.get_latest_audio_path(), retry_dir / "latest.mp3"
                )
                self.assertEqual((retry_dir / "latest.wav").read_bytes(), audio)
                self.assertEqual((retry_dir / "latest.mp3").read_bytes(), b"mp3 audio")
                metadata_text = metadata_path.read_text(encoding="utf-8")
                self.assertIn('"mime": "audio/mpeg"', metadata_text)
                self.assertIn('"source_wav_path"', metadata_text)
                self.assertIn('"mp3_bitrate": "64k"', metadata_text)


class ControlRequestsTest(unittest.TestCase):
    def test_claim_clear_history_request_only_allows_one_claim_per_request(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            claim_dir = Path(temp_dir) / "claims"
            with mock.patch.object(
                control_requests, "CLEAR_HISTORY_CLAIM_DIR", claim_dir
            ):
                self.assertTrue(control_requests.claim_clear_history_request(123))
                self.assertFalse(control_requests.claim_clear_history_request(123))
                self.assertTrue(control_requests.claim_clear_history_request(456))

    def test_claim_clear_history_request_rejects_empty_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            claim_dir = Path(temp_dir) / "claims"
            with mock.patch.object(
                control_requests, "CLEAR_HISTORY_CLAIM_DIR", claim_dir
            ):
                self.assertFalse(control_requests.claim_clear_history_request(None))
                self.assertFalse(claim_dir.exists())


if __name__ == "__main__":
    unittest.main()
