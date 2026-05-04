import unittest
from pathlib import Path

from src.system import process_cleanup


class ProcessCleanupTest(unittest.TestCase):
    def test_matches_script_requires_exact_repository_path(self) -> None:
        script_path = Path(r"D:\GitHub\CapsWriter-Offline\core_client.py")

        self.assertTrue(
            process_cleanup._matches_script(
                r'"D:\GitHub\CapsWriter-Offline\.venv\Scripts\pythonw.exe" '
                r'D:\GitHub\CapsWriter-Offline\core_client.py',
                script_path,
            )
        )
        self.assertFalse(
            process_cleanup._matches_script(
                r'"D:\Other\CapsWriter-Offline\.venv\Scripts\pythonw.exe" '
                r'D:\Other\CapsWriter-Offline\core_client.py',
                script_path,
            )
        )

    def test_descendant_order_returns_children_before_parents(self) -> None:
        processes = [
            {"pid": 10, "parent_pid": 1, "name": "pythonw.exe", "command_line": None},
            {"pid": 11, "parent_pid": 10, "name": "pythonw.exe", "command_line": None},
            {"pid": 12, "parent_pid": 11, "name": "pythonw.exe", "command_line": None},
        ]

        self.assertEqual(process_cleanup._descendant_order(processes), [12, 11, 10])

    def test_terminate_process_matches_excludes_current_pid(self) -> None:
        processes = [
            {"pid": 10, "parent_pid": 1, "name": "pythonw.exe", "command_line": None},
            {"pid": 11, "parent_pid": 10, "name": "pythonw.exe", "command_line": None},
        ]
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)

        original_run = process_cleanup.subprocess.run
        original_sleep = process_cleanup.time.sleep
        try:
            process_cleanup.subprocess.run = fake_run
            process_cleanup.time.sleep = lambda _seconds: None
            terminated = process_cleanup.terminate_process_matches(processes, exclude_pid=10)
        finally:
            process_cleanup.subprocess.run = original_run
            process_cleanup.time.sleep = original_sleep

        self.assertEqual(terminated, [11])
        self.assertEqual(calls, [["taskkill", "/PID", "11", "/T", "/F"]])


if __name__ == "__main__":
    unittest.main()
