import tempfile
import unittest
from pathlib import Path

from src.system.single_instance import acquire_single_instance, release_single_instance


class SingleInstanceTest(unittest.TestCase):
    def test_single_instance_lock_blocks_second_acquire(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertTrue(acquire_single_instance(root, "client_gui"))
            try:
                self.assertFalse(acquire_single_instance(root, "client_gui"))
            finally:
                release_single_instance(root, "client_gui")

            self.assertTrue(acquire_single_instance(root, "client_gui"))
            release_single_instance(root, "client_gui")


if __name__ == "__main__":
    unittest.main()
