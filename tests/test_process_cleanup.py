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
        original_family = process_cleanup.current_process_family
        try:
            process_cleanup.subprocess.run = fake_run
            process_cleanup.time.sleep = lambda _seconds: None
            process_cleanup.current_process_family = lambda _processes=None: {10}
            terminated = process_cleanup.terminate_process_matches(processes, exclude_pid=10)
        finally:
            process_cleanup.subprocess.run = original_run
            process_cleanup.time.sleep = original_sleep
            process_cleanup.current_process_family = original_family

        self.assertEqual(terminated, [11])
        self.assertEqual(calls, [["taskkill", "/PID", "11", "/T", "/F"]])

    def test_terminate_process_matches_protects_current_process_family(self) -> None:
        processes = [
            {"pid": 100, "parent_pid": 1, "name": "python.exe", "command_line": "old gui"},
            {"pid": 200, "parent_pid": 1, "name": "pythonw.exe", "command_line": "new launcher"},
            {"pid": 201, "parent_pid": 200, "name": "pythonw.exe", "command_line": "new worker"},
            {"pid": 300, "parent_pid": 1, "name": "pythonw.exe", "command_line": "old worker"},
            {"pid": 301, "parent_pid": 300, "name": "pythonw.exe", "command_line": "old child"},
        ]
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)

        original_run = process_cleanup.subprocess.run
        original_sleep = process_cleanup.time.sleep
        original_family = process_cleanup.current_process_family
        try:
            process_cleanup.subprocess.run = fake_run
            process_cleanup.time.sleep = lambda _seconds: None
            process_cleanup.current_process_family = lambda _processes=None: {200, 201}
            terminated = process_cleanup.terminate_process_matches(processes)
        finally:
            process_cleanup.subprocess.run = original_run
            process_cleanup.time.sleep = original_sleep
            process_cleanup.current_process_family = original_family

        self.assertEqual(terminated, [301, 100, 300])
        self.assertEqual(
            calls,
            [
                ["taskkill", "/PID", "301", "/T", "/F"],
                ["taskkill", "/PID", "100", "/T", "/F"],
                ["taskkill", "/PID", "300", "/T", "/F"],
            ],
        )

    def test_current_process_family_walks_ancestors(self) -> None:
        original_getpid = process_cleanup.os.getpid
        try:
            process_cleanup.os.getpid = lambda: 201
            family = process_cleanup.current_process_family(
                [
                    {"pid": 1, "parent_pid": 0, "name": None, "command_line": None},
                    {"pid": 200, "parent_pid": 1, "name": None, "command_line": None},
                    {"pid": 201, "parent_pid": 200, "name": None, "command_line": None},
                    {"pid": 300, "parent_pid": 1, "name": None, "command_line": None},
                ]
            )
        finally:
            process_cleanup.os.getpid = original_getpid

        self.assertEqual(family, {1, 200, 201})

    def test_matches_gui_script_by_repo_root_and_basename(self) -> None:
        script_path = Path(r"D:\GitHub\CapsWriter-Offline\start_client_gui.py")
        root_text = process_cleanup._norm_text(str(script_path.resolve().parent))
        basename = script_path.name.lower()

        same_repo_command = process_cleanup._norm_text(
            r'"D:\GitHub\CapsWriter-Offline\.venv\Scripts\python.exe" start_client_gui.py'
        )
        other_repo_command = process_cleanup._norm_text(
            r'"D:\Other\CapsWriter-Offline\.venv\Scripts\python.exe" start_client_gui.py'
        )

        self.assertIn(root_text, same_repo_command)
        self.assertIn(basename, same_repo_command)
        self.assertNotIn(root_text, other_repo_command)
        self.assertIn(basename, other_repo_command)


if __name__ == "__main__":
    unittest.main()
