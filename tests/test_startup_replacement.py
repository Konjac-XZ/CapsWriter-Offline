import tempfile
import unittest
from pathlib import Path

from src.system.startup_replacement import (
    acquire_startup_slot,
    prepare_replacement_startup,
    release_startup_slot,
)


class StartupReplacementTest(unittest.TestCase):
    def test_startup_slot_is_released_for_next_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertTrue(acquire_startup_slot(root, "client_gui"))
            try:
                self.assertFalse(acquire_startup_slot(root, "client_gui"))
            finally:
                release_startup_slot(root, "client_gui")

            self.assertTrue(acquire_startup_slot(root, "client_gui"))
            release_startup_slot(root, "client_gui")

    def test_prepare_replacement_startup_runs_cleanup_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cleanup_calls = 0

            self.assertTrue(acquire_startup_slot(root, "core_client"))

            def replace_existing() -> None:
                nonlocal cleanup_calls
                cleanup_calls += 1
                release_startup_slot(root, "core_client")

            try:
                self.assertTrue(
                    prepare_replacement_startup(
                        root, "core_client", replace_existing, attempts=1, delay_s=0
                    )
                )
                self.assertEqual(cleanup_calls, 1)
            finally:
                release_startup_slot(root, "core_client")


if __name__ == "__main__":
    unittest.main()
