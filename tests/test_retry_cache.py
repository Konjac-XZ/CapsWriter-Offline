import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
import asyncio

import numpy as np

from src.audio import send_audio
from src.audio import retry_cache
from src.audio import control_requests
from src.transcribe.openai import openai_transcribe_audio


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

    def test_wav_retry_cache_does_not_transcode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            retry_dir = Path(temp_dir) / "retry_audio"
            metadata_path = retry_dir / "latest.json"
            audio = b"wav audio"

            with (
                mock.patch.object(retry_cache, "RETRY_AUDIO_DIR", retry_dir),
                mock.patch.object(retry_cache, "RETRY_METADATA_PATH", metadata_path),
            ):
                target = retry_cache.write_retry_cache(
                    audio, "audio/wav", {"source_task_id": "c3"}
                )

                self.assertEqual(target, retry_dir / "latest.wav")
                self.assertEqual(
                    retry_cache.get_latest_audio_path(), retry_dir / "latest.wav"
                )
                self.assertEqual((retry_dir / "latest.wav").read_bytes(), audio)
                self.assertFalse((retry_dir / "latest.mp3").exists())
                metadata_text = metadata_path.read_text(encoding="utf-8")
                self.assertIn('"mime": "audio/wav"', metadata_text)
                self.assertIn('"source_wav_path"', metadata_text)

    def test_upload_payload_becomes_retry_audio_and_keeps_playback_wav(self) -> None:
        async def run_case() -> None:
            audio = np.array([[0.0], [0.25], [-0.25]], dtype=np.float32)
            wav_task = asyncio.create_task(
                send_audio._cache_recording_in_worker(
                    audio_concat=audio,
                    task_id="c4",
                    duration=0.1,
                    time_start=10.0,
                    record_stop=10.1,
                )
            )
            await send_audio._cache_upload_payload_after_wav(
                wav_cache_task=wav_task,
                payload_bytes=b"already encoded mp3",
                payload_mime="audio/mpeg",
                task_id="c4",
                duration=0.1,
                time_start=10.0,
                record_stop=10.1,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            retry_dir = Path(temp_dir) / "retry_audio"
            metadata_path = retry_dir / "latest.json"
            with (
                mock.patch.object(retry_cache, "RETRY_AUDIO_DIR", retry_dir),
                mock.patch.object(retry_cache, "RETRY_METADATA_PATH", metadata_path),
                mock.patch.object(
                    send_audio, "write_retry_cache", retry_cache.write_retry_cache
                ),
            ):
                asyncio.run(run_case())
                self.assertEqual(
                    (retry_dir / "latest.mp3").read_bytes(), b"already encoded mp3"
                )
                self.assertGreater((retry_dir / "latest.wav").stat().st_size, 44)
                self.assertEqual(
                    retry_cache.get_latest_audio_path(), retry_dir / "latest.mp3"
                )
                metadata_text = metadata_path.read_text(encoding="utf-8")
                self.assertIn('"cache_stage": "upload_encoded"', metadata_text)
                self.assertIn('"source_wav_path"', metadata_text)

    def test_recording_cache_worker_does_not_block_event_loop(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_cache(**_kwargs) -> None:
            started.set()
            release.wait(timeout=1.0)

        async def run_case() -> None:
            task = asyncio.create_task(
                send_audio._cache_recording_in_worker(
                    audio_concat=np.zeros((1, 1), dtype=np.float32),
                    task_id="worker",
                    duration=0.0,
                    time_start=0.0,
                    record_stop=0.0,
                )
            )
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.001)
            self.assertTrue(started.is_set())
            self.assertFalse(task.done())
            # Reaching this line while the worker is waiting proves that the
            # event-loop thread remains free to process other work.
            release.set()
            await task

        with mock.patch.object(
            send_audio, "_cache_recording_for_retry", side_effect=slow_cache
        ):
            asyncio.run(run_case())

    def test_ffmpeg_input_encoding_does_not_mutate_recording(self) -> None:
        audio = np.array([[np.nan], [np.inf], [-np.inf], [2.0]], dtype=np.float32)
        original = audio.copy()

        encoded = openai_transcribe_audio._sanitized_f32_bytes(audio)

        np.testing.assert_equal(audio, original)
        np.testing.assert_equal(
            np.frombuffer(encoded, dtype=np.float32).reshape(-1, 1),
            np.array([[0.0], [1.0], [-1.0], [1.0]], dtype=np.float32),
        )


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
